#!/usr/bin/env python3
"""成稿质量闭环 — 选题→成稿→机械检查→独立审核→返工，直到过线或到轮次上限。

为什么要这个脚本：8:30 那个 scheduled task 自称「打分循环至90分」，但打分的是写稿的
那个模型自己。独立审核回来的真实分是 72–86，11 篇只有 1 篇过闸门。自评 87 / 独立审核 67
的那次差距就是 D7 决策的由来。所以这里把评分权从写稿的模型手里拿走：

  ① 每轮返工都是**新的 claude -p 进程** —— 同一会话里自己改自己评，改完必然自评合格
  ② 机械检查用 draft_check.py（代码硬核对），模型自报无效
  ③ 打分用 independent_audit.py（只喂成稿文本，不喂写作过程）
  ④ 过线阈值 85 而不是 90 —— 90 是自评口径的数字，独立审核口径下 21 篇历史稿最高 86，
     定 90 等于永远不过，loop 会空转到上限

用法：
  python3 refine_loop.py                      # 自动选一个已验证关键词跑完整 loop
  python3 refine_loop.py --topic "关键词"      # 指定选题
  python3 refine_loop.py --dry-run            # 只看选中哪个词、喂什么料，不调 claude
  python3 refine_loop.py --rounds 2           # 改轮次上限（默认 3）
"""
import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gemini_cli  # noqa: E402
from claude_limits import WEEKLY, classify_limit, is_limit, limit_banner  # noqa: E402
from headless_cli import HEADLESS_FLAGS, ensure_cwd  # noqa: E402

BARE_CWD = ensure_cwd()

REPO = Path(__file__).resolve().parents[2]
SUCAI = REPO / "xhs" / "素材库"
CIKU = SUCAI / "词库.csv"
AUDIT_LOG = SUCAI / "审核记录.csv"
PENDING = SUCAI / "待激活素材库.csv"
PROBE_DIR = SUCAI / "探测原始"
CARDS_SCRIPT = SUCAI / "图文模板" / "make_cards.py"
TITLE_SKILL = REPO / "skills" / "eric-xhs-title" / "SKILL.md"
FRAME = SUCAI / "知识框架.md"
HEALTH = REPO / "scripts" / "xhs-health"
CLAUDE = Path.home() / ".local/bin/claude"

# ── 料包预算（2026-08-08 加）────────────────────────────────────────────────
# 改之前实测：单次写稿 prompt 95,656 字，其中
#   评论区原话.csv 整库 56,053（58.6%）+ 案例库.csv 整库 21,832（22.8%）＝ 81.4%。
# 一天 4 次 × 最多 12 次调用 × 近 10 万字 —— loop日志 里已有 8 份
# "You've hit your session limit" 的留档，不是理论上会撞，是一直在撞。
#
# 改成按选题筛，而不是整库塞。⛔ 筛了就必须同步改 prompt 里
# 「上面两个库是整库喂给你的」那句话 —— prompt 谎报自己的输入，
# 比 prompt 大得多更糟：模型会以为「没有更合适的原话了」而将就用手上的。
QUOTE_BUDGET = 60    # 评论区原话保留行数（整库 268 行）
CASE_BUDGET = 30     # 案例库保留行数（整库 101 行）
# 跨来源组合是 2026-08-05 Eric 定的口径，需要一定广度，所以不是只给命中那几条：
# 命中的全留，剩下的名额用同场景域的补，还有空位再用其它场景域的补。

# ⛔ 必须与 independent_audit.PASS_SCORE 是同一个数（那边有降档依据的完整说明）。
# 两处不同步会重演 2026-08-05 那个 bug：达标稿被判「未过线」继续返工，越改越低。
# 2026-08-14 由 85 降到 80（Eric 定）。
PASS_SCORE = 80
MAX_ROUNDS = 3
WRITE_TIMEOUT = 900
RETRIES = 2          # claude -p 普通失败（格式跑偏/超时）的重试次数
RETRY_WAIT = 45      # 普通失败重试前等待秒数
LIMIT_WAIT = 30 * 60         # 撞额度后每次等 30 分钟
LIMIT_MAX_WAIT = 5 * 3600    # 额度窗口 5 小时，熬满还没回来就真停手
LOG_DIR = REPO / "xhs" / "素材库" / "loop日志"

SEP_SLUG, SEP_MD, SEP_JSON, SEP_END = "===SLUG===", "===MARKDOWN===", "===CARDS_JSON===", "===END==="

# 短名字符上限。prompt 要 6-12 个汉字，这里按字符算要给英文缩写留余量（见 parse_output）。
SLUG_MAX_CHARS = 20

# ── 把机械阈值翻成写作动作（2026-08-11 Eric 定）────────────────────────────
# 起因：08-11 手动跑两篇全部卡在「具体名词密度」和「CTA 编号选项」，而且来回震荡
# ——修好 CTA 掉密度，修好密度丢 CTA。其中一篇原本独立审核 85 分（正好到线），
# 返工三轮后审核分反降到 83，最终回滚归档。
#
# 阈值本身是没法直接执行的：模型数不准「每百字 2.0 个」，以前它靠 shell
# （cat > /tmp/body.txt）自己数，工具禁掉之后就只能凭感觉。所以把阈值翻译成
# 落笔时就能照做的动作，而不是让它事后去数。
#
# ⛔ 但绝不能写成「至少放 8 个数字」—— 那是直接诱导编造。
# 具体名词是数字/时间/金额/职位，编出来就是虚假信息：读者拿「被压薪 2000」
# 去谈薪是要吃亏的。而且「编造原话」是审核**红线**（2026-08-11 起：原维度 6
# 「原话可信度」15 分已撤销，不编造不冒充降为是/否的合规底线），
# 造了直接打回不看总分，白跑一轮。
#
# 正确的因果是反的：**密度不够不是文笔问题，是素材不够**。
# 上面给的评论区原话和案例库里全是真实的数字、职位、金额、时长，
# 写手该做的是去取，取不到就说明这个选题的料不够，不该硬凑。
#
# 另需记住这个阈值的分量（xhs/工作台.md D9）：它由 **4 篇**有独立审核分的稿
# 标定，参照系是内部审核分而非真实发布数据，且文档明写「与分数不单调相关
# （67 分稿密度 3.25 高于 83 分稿的 2.52）」「定位是异常检测不是质量评分」。
# 所以它只配当异常值拦截线，不该被当成质量目标去堆。
WRITE_ACTIONS = """

【怎么落笔才不会卡在机械项 —— 这些是动作，不是让你事后去数】
1. 具体名词（数字/时间/金额/职位/时长）：**全部从上面给的原话和案例里取**，
   每一段至少落一个。取不到就换个角度写，或者说明这篇素材不足 ——
   ⛔ 严禁自己编数字、编金额、编职位。编出来的会被审核按「编造原话」判红线，
   这一轮就白跑了；更要紧的是读者真会照着做决定。
2. CTA：结尾必须给 2–4 个**带字母编号**的选项，让读者回一个字母就能参与。
   照这个骨架写，别改成没有编号的开放式提问：
       你是哪一种？回个字母就行：
       A. <一种具体处境>
       B. <另一种具体处境>
       C. <第三种>
3. 这两项在一篇稿里同时成立才算过。别为了修其中一项把另一项改没了 ——
   实测有稿在这两项之间来回震荡三轮，最后比第一轮还差。"""

# ⛔ 别在 prompt 里让模型「自己数一遍」而不说清怎么数（2026-08-11 实测教训）。
# 原文是「改完自己数一遍再输出」，模型把它理解成「去把机械检查跑一遍」，于是
# 999 次 Bash 命令里出现 85 次 cd scripts/xhs-health、31 次 ls 同一个目录、
# 7 次 head -60 draft_check.py（读检查脚本源码）、6 次 cat > /tmp/body.txt 数字数。
# 机械检查其实早就由脚本在会话外跑（见下方 mechanical_check），结果通过 feedback
# 回灌，模型完全不必碰它。现在工具已由 HEADLESS_FLAGS 全禁，这句话也改成
# 「在心里核一遍」，两头堵住。
NO_TOOLS_NOTE = """\n\n⛔ 本次调用没有任何工具可用，也不需要：写稿要的素材、规格、
反馈已全部在上面给全了，成稿文件由脚本负责落盘。不要尝试读文件、列目录、
跑脚本或写临时文件 —— 直接按下面的格式把结果输出出来即可。"""

# 返工时不提醒的话，模型会为了修一条意见把已经达标的机械项改坏（实测：第 3 轮为了改
# 审核意见，具体名词密度从 2.0+ 掉到 1.51，连机械检查都没过，比上一轮还差）。
KEEP_PASSED = """⚠️ 改的是被指出的问题，别把已经达标的地方改坏。尤其守住这几条：
正文字数守住本篇口径的规格 · 平均句长≤30 · 引语密度≥0.8/百字 ·
具体名词（数字/时间/金额/职位）≥2.0/百字 · 排比≤1 处 · 「不是X是Y」≤2 处 ·
泛指群体词（很多人/有些人/大多数人）≤2 处 · 全篇只对读者说话不换视角 ·
结尾保留具体问句。改完在心里核一遍这几项再输出。""" + WRITE_ACTIONS


# 两条流**共用一套流程和一个正文规格**，只在标题/首图/开头三处切口径。
# Eric 2026-08-03 两条决定：①推荐流不做视频、按同一条成稿路线做图文，账号丰富度才起得来
# ②推荐流正文也写 100-500（原 800-1200，08-03 统一为 300-500，08-11 下限改 100）—— 字数再分叉，流程就变成两套了。
# 口径差异的依据是 eric-xhs-audit SKILL.md 文末「附录 · 推荐流口径」。
LANE_SPEC = {
    "搜索流": {
        "words": "100-500",
        "brief": "搜索流图文笔记（7 张图承载内容 + 100-500 字正文承载关键词）",
        # 标题规则不在这里写死，运行时从 eric-xhs-title/SKILL.md 注入（见 title_rules）。
        "rules": """{TITLE_RULES}
- 首图：搜索原句大字 + 结论前置（信息型），让搜索的人一眼确认「答的就是我搜的」
- 开头：让搜索者确认答的就是他搜的，不要铺垫
- ⛔ 红线：首图不是搜索原句 / 出现「表达力」三字 / 编造原话
- 【审核侧按读者动作打分，权重如下 —— 写的时候就照这个用力】
  · 标题 25 + 首图 20 = **45 分全押在「第 3 秒手指停不停」**，这是最重的一块
  · 搜索意图 20：这个词的搜索位得有人在看
  · 开头 15：读完开头 1/3，他心里要留着一个未完成的问题
  · 正文 10：他会不会存（有没有能直接套用的话术）、24 小时后记得哪一句
  · CTA 10：他想不想说话，回一条要几个字
  ⚠️ 「原话可信度」**已于 2026-08-11 撤销**（原 15 分，曾是最重项）。
  不编造、不冒充降为**红线**（是/否，合规底线），不再计分 ——
  写的时候别再为了堆原话颗粒度而牺牲标题和首图，那 15 分已经不存在了。""",
    },
    "推荐流": {
        "words": "100-500",
        "brief": "推荐流图文笔记（7 张图承载内容 + 100-500 字正文）",
        "rules": """- 标题：**留悬念不把答案说完**，张力 6 项（对比/数字/悬念/冲突/时间承诺/结果承诺）命中 ≥2，≤20 字
- 首图：0.3 秒制造认知冲突/好奇/焦虑（气质型），**不要**搜索原句大字
- 开头：15 分档，前 3 秒抓手 + 留悬念
- 主指标是 CES（赞×1+藏×1+评×4+转×4）和互动率 ≥5%，所以评论钩子权重更高
- ⛔ 红线：标题把答案说完 = 必死（这条与搜索流正好相反）
- ⚠️ 推荐流暂无高分范例可参照（历史推荐流稿最高 69 分），下面给的范例是搜索流的，
  只学它的结构组织和原话使用方式，标题/首图/开头一律按上面推荐流口径重做""",
    },
}


def _read_or(path: Path, fallback: str) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else fallback


def title_rules() -> str:
    """搜索流标题规则的唯一真相：eric-xhs-title/SKILL.md 的实证规律那一节。

    这里曾内嵌过一份副本，靠人手跟 SKILL.md 同步。2026-08-05 出事：SKILL.md 已经把
    「含疑问词的搜索原句不得原样搬入」定成硬约束，副本还停在「不必是搜索原句」的软措辞，
    模型照副本写出「职场被孤立怎么办，动手的未必是同事」——前半段恰好是实测里最强的
    两个负信号（疑问句 −35、写困境 −32）。规则有两份副本，迟早只更新其中一份。
    """
    m = re.search(r"^## ⭐ 最高优先级.*?(?=^## )", _read_or(TITLE_SKILL, ""), re.M | re.S)
    if not m:
        # 退化成「没有标题规则」比直接报错危险得多：稿子照样写得出来、照样过审，
        # 只是标题悄悄回到踩负信号的形态，没人会发现。所以这里必须停。
        raise SystemExit(f"⛔ 取不到标题规则：{TITLE_SKILL} 缺失，"
                         f"或「## ⭐ 最高优先级」这一节的标题被改过")
    return m.group(0).strip()


def used_keywords() -> set:
    """已经成过稿的关键词。

    不另建索引表——成稿 md 的头部会写「关键词来源：词库.csv「XXX」」，
    直接全文匹配即可，少一张要维护的表就少一处会对不上的地方。
    """
    files = list(SUCAI.glob("成稿_*.md")) + list((SUCAI / "归档稿").glob("成稿_*.md"))
    blob = "\n".join(f.read_text(encoding="utf-8") for f in files)
    return {kw for kw in _all_keywords() if kw and kw in blob}


def _all_keywords() -> list:
    if not CIKU.exists():
        return []
    return [(r.get("关键词") or "").strip() for r in csv.DictReader(CIKU.open(encoding="utf-8-sig"))]


# 搜索位前排日均赞中位的下限。低于它 = 这个位上排第一也没人看。
# 依据 docs/20260811-五项爆款规律.md §二：不同词的前排强度差 360 倍
# （职场万用术 36.18 vs 说完方案领导没反应怎么办 0.10），而本账号已发 12 篇里
# **8 篇的词前排日均赞中位 <2**，「汇报被领导打断怎么接」= 0.14，还在这个词上写了 3 篇。
# 「词的前排强度」vs「我们的日均观看」相关 +0.335（n=12，样本小，方向性参考）。
MIN_SLOT_STRENGTH = 1.0


def slot_strength(keyword: str) -> float | None:
    """该词搜索位的前排日均赞中位。None = 没有 probe 数据，算不出。

    ⛔ 口径是**日均**赞（点赞 ÷ 发布至采集的天数），不是 probe 里存的
    density.median_likes（那是绝对赞数，量级 8-1069）。两者差着「笔记活了多少天」。
    实现复用 draft_check.search_slot_evidence，别在这里写第二份。
    """
    sys.path.insert(0, str(HEALTH))
    from draft_check import search_slot_evidence
    return search_slot_evidence(f"关键词来源：`词库.csv`「{keyword}」")["日均赞中位"]


def pick_topic() -> dict | None:
    """选一个搜索位已验证、没写过、且**前排确实有人互动**的词。

    状态=已验证 是硬条件——它代表人工核过这个词的搜索位确实有空缺，没验过就写容易撞饱和赛道。
    密度「待探测」不是硬条件：必须命中清单第 8 条允许「两者皆无时在备注说明为什么值得赌」，
    只排后面。初版把它也当硬条件，结果 9 个已验证词里 7 个已写过、剩 2 个全是待探测，
    loop 直接无题可做 —— 比清单本身还严，把自己饿死了。

    ⚡ 2026-08-12 加前排强度过滤（清单第 8 条的第二个判据）。在此之前这个函数
    **只看「已验证」和「竞争密度」**，而 probe 早就把 top_notes 的 likes+published_at
    采下来了，从没用于选词 —— 于是一直在优化「如何在一个没人看的搜索位上排第一」。
    这是整条链路上最贵的一个问题：稿子写得再好，那个位上本来就没人。

    过滤是**软的**：达标词优先，全都不达标时仍返回最强的那个并告警，不让 loop 饿死
    （同上，把自己饿死过一次了）。算不出强度的（无 probe 数据）排在达标词之后、
    已知弱词之前 —— 未知不等于差，但也不该优先于已证明有人看的。
    """
    if not CIKU.exists():
        return None
    used = used_keywords()
    pool = [r for r in csv.DictReader(CIKU.open(encoding="utf-8-sig"))
            if (r.get("关键词") or "").strip()
            and (r.get("关键词") or "").strip() not in used
            and (r.get("状态") or "").strip() == "已验证"]
    if not pool:
        return None

    for r in pool:
        r["_strength"] = slot_strength((r["关键词"]).strip())

    def rank(r):
        # ⛔ 不是「强度越高越好」。判据分四档（五项爆款规律 §二）：
        #   5-20 甜区（有人看，前排又没强到碰不动）> 1-5（能写，天花板低）
        #   > 未知（无 probe） > >20 红海（要明确角度差异才进） > <1（没人互动）
        # 初版写成 -strength 排序，选出来的第一个词是 28.8 —— 正好是要谨慎进的红海。
        s = r["_strength"]
        if s is None:
            tier = 2
        elif 5 <= s <= 20:
            tier = 0
        elif MIN_SLOT_STRENGTH <= s < 5:
            tier = 1
        elif s > 20:
            tier = 3
        else:
            tier = 4
        # 同档内：密度已探测的优先；甜区内再按越接近 20 越靠前（有量的一侧）
        return (tier, (r.get("竞争密度") or "").strip() in ("", "待探测"), -(s or 0))

    pool.sort(key=rank)
    best = pool[0]
    s = best["_strength"]
    if s is None:
        print(f"   ⚠️ 选题「{best['关键词']}」无 probe 数据，搜索位强度未知（清单第 8 条第②判据无法核）")
    elif s < MIN_SLOT_STRENGTH:
        # ⛔ 2026-08-14 改：以前这里只打印一句「真正该做的是补选词，不是写这篇」，
        # 然后**照写不误**。代价在审核端结算 —— 08-11 之后 63 条独立审核里
        # **46% 的扣分理由是「该词前排日均赞中位 0.14/0.53，几乎无人互动」**，
        # 稿子本身没毛病，是这个位上排第一也没人看。
        # 让写稿去背选词的锅，跟当初「人群错配」判成稿归档是同一类错误。
        #
        # 现在真的停手：宁可这一轮不出新稿，也不烧一次 claude -p 去写注定被扣分的稿。
        # 额度转去返工（队列里 18 篇 80+ 分，离过线只差一处定点修改），产出效率高得多。
        # 这也把压力放回该承受的地方：要写新稿，先让采集/probe 补出有人互动的词。
        print(f"   ⛔ {len(pool)} 个候选词**没有一个**搜索位上有人互动"
              f"（最强的「{best['关键词']}」也只有 {s} 日均赞 < {MIN_SLOT_STRENGTH}）。")
        print("      本轮不写新稿 —— 写出来也会因为「搜索位没人互动」被扣分，"
              "这是选词的问题不是写稿的问题。")
        print("      去补词：python3 scripts/xhs-collect/daily_collect.py"
              " && python3 scripts/xhs-probe/probe_opencli.py --from-cikuku --limit 5")
        return None
    elif s > 20:
        print(f"   ⚠️ 选题「{best['关键词']}」= {s} 日均赞，>20 属红海（无甜区词可选）。"
              f"前排已被高赞占满，没有明确的角度差异就别硬进")
    else:
        tag = "甜区" if s >= 5 else "可写但天花板低"
        print(f"   选题「{best['关键词']}」搜索位日均赞中位 {s}（{tag}）")
    return best


def probe_evidence(keyword: str) -> str:
    """该词的探测原始数据。审核维度 1 要核competition密度是否有 probe 文件支撑。"""
    hits = []
    for f in sorted(PROBE_DIR.glob("probe_*.json")) if PROBE_DIR.exists() else []:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if keyword in json.dumps(data, ensure_ascii=False):
            hits.append(f"【{f.name}】\n{json.dumps(data, ensure_ascii=False, indent=1)[:3000]}")
    return "\n\n".join(hits) or "（该词无 probe_*.json，需在备注说明为什么值得赌）"


def pose_library() -> str:
    """姿势库清单，读目录不写死 —— make_cards.py 的注释就因为写死而停在「共 18 个」，
    实际已经 30 个了。文件名自带语义（pose19_谈薪拉扯），模型据此选图。"""
    d = SUCAI / "图文模板"
    names = sorted((p.stem for p in d.glob("pose*.png")),
                   key=lambda s: int(re.match(r"pose(\d+)", s).group(1)))
    return "、".join(names)


def recent_drafts(n=5) -> str:
    """近 n 篇的**开头和结尾**——给模型看「别再用这些开头和签名句」。

    跨篇查重是机械检查项（清单第 5 条），事后拦不如事前给。

    ⛔ 原来每篇喂 1500 字全文，5 篇 7,664 字。但这一块的用途只有两个：
    别撞开头钩子、别撞签名句/CTA。中间那段讲什么跟查重无关，白花。
    改成每篇只给前 320 字（开头钩子）+ 后 220 字（签名句与 CTA）。
    """
    out = []
    for f in sorted(SUCAI.glob("成稿_*.md"))[-n:]:
        t = f.read_text(encoding="utf-8")
        body = t.split("## 话题标签")[0]
        out.append(f"【{f.name}】\n开头：{body[:320]}\n…\n结尾：{body[-220:]}")
    return "\n\n".join(out)


def knowledge_frame() -> str:
    """知识框架.md —— 内容生产的大脑，写稿时必须应用。

    ⛔ 2026-08-08 之前**没有任何脚本读它**（只有 eric-xhs-audit/SKILL.md 引用）。
    也就是说那段时间改知识框架对产出一个字的影响都没有 —— 08-08 改 CTA 口径时
    才发现这件事。写手真正读的一直是 必须命中清单.md。现在两边都读。

    砍掉三节再喂，理由各不相同：
      §七 高热选题母题库  —— 选题在进这个函数之前就定死了，写稿时用不上
      §十二 选题4维框架   —— 同上，而且它明写「来自 eric-xhs-topic」
      §十三 标题12类触发器 —— **它是 eric-xhs-title/SKILL.md 的副本**，
        而 title_rules() 已经把 SKILL.md 的实证规律那节单独喂进 prompt 了。
        同一份规则喂两遍，迟早只更新其中一份 —— title_rules() 的 docstring
        记着 08-05 就是这么出的事（副本停在旧措辞，模型照副本写，标题踩负信号）。
    """
    t = _read_or(FRAME, "")
    if not t:
        raise SystemExit(f"⛔ 取不到知识框架：{FRAME} 缺失")
    for head in ("## 七、", "## 十二、", "## 十三、"):
        t = re.sub(rf"^{re.escape(head)}.*?(?=^## )", "", t, flags=re.M | re.S)
    return t.strip()


def _csv_rows(path):
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _kw_domain_map():
    """关键词 → 场景域。评论区原话只记了「候选词」，场景域要靠词库查。"""
    return {r["关键词"]: (r.get("场景域") or "").strip()
            for r in _csv_rows(CIKU) if r.get("关键词")}


def _pick(rows, hit, same, budget):
    """命中的全留 → 同场景域补 → 其它补满预算。返回 (选中行, 命中数)。"""
    out = [r for r in rows if hit(r)]
    n_hit = len(out)
    for pred in (same, lambda r: True):
        if len(out) >= budget:
            break
        for r in rows:
            if len(out) >= budget:
                break
            if r not in out and pred(r):
                out.append(r)
    return out[:budget], n_hit


def _as_block(rows, cols):
    if not rows:
        return "（无）"
    head = ",".join(cols)
    body = "\n".join(",".join((r.get(c) or "").replace("\n", " ") for c in cols) for r in rows)
    return f"{head}\n{body}"


# 关键词切 2 字片段做字面匹配。中文没空格，整词匹配「谈薪预算就这么多」命不中，
# 切片能把「谈薪」捞出来，够用且不引分词依赖。
# STOP 里是搜索词里的通用连接片段 —— 不排掉的话「怎么」会命中大半个库，
# 相关性筛选就退化成随机取。
_FRAG_STOP = {"怎么", "么办", "怎办", "什么", "的时", "一个", "可以", "不是", "还是",
              "应该", "要不", "是不", "能不", "如何", "为什", "么样", "我的", "自己"}


def _frags(kw):
    return {kw[i:i + 2] for i in range(len(kw) - 1)} - _FRAG_STOP


def relevant_quotes(kw, domain):
    """评论区原话：与本篇相关的那些，不是整库 268 行。

    ⛔ 别只靠「候选词」列筛。2026-08-08 实测：268 行里 **216 行的候选词是空的**，
    只有 46 行能查到场景域。只按候选词筛，等于 83% 的行看不出相关性、
    按文件顺序凑数 —— 库是砍小了，留下的却不是对的行，而且从输出上看不出来。
    所以主力信号是**正文文本匹配**（用户原话 + 暴露的处境），候选词只作最强命中。
    """
    rows = _csv_rows(SUCAI / "评论区原话.csv")
    dm = _kw_domain_map()
    fr = _frags(kw)

    def textual(r):
        blob = (r.get("用户原话") or "") + (r.get("暴露的处境") or "")
        return any(f in blob for f in fr)

    sel, n_hit = _pick(
        rows,
        hit=lambda r: (r.get("候选词") or "").strip() == kw,
        same=lambda r: textual(r) or (domain and dm.get((r.get("候选词") or "").strip()) == domain),
        budget=QUOTE_BUDGET)
    n_rel = sum(1 for r in sel if textual(r)
                or (r.get("候选词") or "").strip() == kw
                or (domain and dm.get((r.get("候选词") or "").strip()) == domain))
    cols = ["用户原话", "暴露的处境", "候选词"]
    return _as_block(sel, cols), len(sel), len(rows), n_rel


def relevant_cases(kw, row):
    """案例库：本词关联的 + 场景/原话字面相关的 + 补满预算。"""
    rows = _csv_rows(SUCAI / "案例库.csv")
    linked = {x.strip() for x in re.split(r"[,，\s]+", row.get("关联案例ID") or "") if x.strip()}
    fr = _frags(kw)

    def textual(r):
        blob = (r.get("场景") or "") + (r.get("对方原话") or "") + (r.get("可迁移的那一句") or "")
        return any(f in blob for f in fr)

    sel, n_hit = _pick(
        rows,
        hit=lambda r: (r.get("案例ID") or "").strip() in linked,
        same=textual,
        budget=CASE_BUDGET)
    n_rel = sum(1 for r in sel if textual(r) or (r.get("案例ID") or "").strip() in linked)
    cols = ["案例ID", "场景", "对方原话", "我的原话", "结果", "可迁移的那一句", "来源"]
    return _as_block(sel, cols), len(sel), len(rows), n_rel


def build_prompt(row: dict, feedback: str, round_no: int, lane: str = "搜索流",
                 fixed_title: str = "") -> str:
    kw = row["关键词"]
    spec = LANE_SPEC[lane]
    title_block = ""
    if fixed_title:
        # 标题已由人挑定时，正文必须兑现它推翻的那个预设 —— 否则标题许诺了反转、
        # 正文却在讲别的，点进来的人会觉得被骗，评论和收藏都拿不到。
        title_block = f"""
【本篇标题已定，直接用作首选标题，不要另拟】
{fixed_title}

⛔ 正文必须兑现这个标题推翻的预设：标题否定了读者的哪个判断，正文就要在前三句
把「为什么你以为的那样其实不成立」讲清楚，并给出真正成立的那一面。
标题许诺了反转，正文却讲别的 = 骗点击，评论和收藏都会掉。
备选标题仍出 2 个，但首选必须原样用上面这句。
"""
    benchmark = _read_or(SUCAI / "成稿_2026-08-02_空降前30天.md", "（无范例）")
    domain = (row.get("场景域") or "").strip()
    quote_block, n_quote, tot_quote, hit_q = relevant_quotes(kw, domain)
    case_block, n_case, tot_case, hit_c = relevant_cases(kw, row)
    # 打印相关行数而不只是总行数 —— 相关的只有个位数时，说明这个词的素材是真不够，
    # 那时写出来的稿八成要靠硬凑，早点看见比事后在审核里看见强。
    print(f"   料包：评论区原话 {n_quote}/{tot_quote}（其中相关 {hit_q}）· "
          f"案例库 {n_case}/{tot_case}（其中相关 {hit_c}）")
    if hit_q < 8:
        print(f"   ⚠️ 只有 {hit_q} 条相关原话（阈值 8），这个词的素材偏薄，成稿备注里要说明")
    rework = ""
    if feedback:
        rework = f"""
⛔ 这是第 {round_no} 轮返工，不是新写。上一轮的稿被打回，原因如下。
逐条修掉，不要重写无关的部分，也不要为了绕开某条检查而牺牲可读性：

{feedback}
"""
    # ⛔ 块的顺序 = 缓存命中率，不是排版偏好（2026-08-12 实测后重排）。
    # Anthropic 的 prompt cache 按**前缀**匹配：从第一个不同的字符起，后面全部失效。
    # 原顺序把【选题】关键词和【今天是 X】放在知识框架（14.5k 字）和清单（10.7k 字）
    # **之前** —— 于是每篇稿只要关键词一换，那 25k 字规则的缓存就整块作废。
    # 实测两篇不同选题的 prompt 共同前缀只有 2,625 字 = **5.5%**，
    # 意味着那 25k 字规则从来没享受过 0.1× 的读缓存，每篇都按 2× 的写缓存价重算。
    # （审核侧 independent_audit.py 的顺序本来就是对的，实测 70.3%。）
    #
    # 现在的规矩：**静态在前，动态在后**。往这个 prompt 里加东西时先问
    # 「它每篇都一样吗」——一样就放上半区，不一样就放下半区，别插进上半区。
    # 唯一例外是文末的输出格式：它虽然静态，但必须紧贴生成点，否则模型漏分隔符。
    return f"""你是 Eric 的小红书成稿写手。写一篇{spec['brief']}。

【本篇口径：{lane}】必须在成稿头部的引言区写一行 `> 口径：{lane}` ——
draft_check.py 和 independent_audit.py 都靠这一行判断用哪套规格，漏了会按搜索流误判。
{spec['rules'].replace('{TITLE_RULES}', title_rules() if '{TITLE_RULES}' in spec['rules'] else '')}

【知识框架（内容生产的大脑，本篇必须落在它的定位、红线、骨架和质量标准之内）】
{knowledge_frame()}

【必须命中清单（逐条命中，机械项由代码核对，自报无效）】
{_read_or(SUCAI / '必须命中清单.md', '（清单缺失）')}
{WRITE_ACTIONS}

【格式范例（这篇独立审核 86 分，是目前唯一过闸门的稿。学它的结构，别抄它的内容）】
⚠️ 它自己被扣分的两处别学：①「上面七张图讲的是…」这类元叙述——正文不要提"图里讲了什么"，
图文各说各的；②末段密集堆评论原话，把落点冲淡了。
{benchmark}

⛔ 视角红线：全篇像作者只对着读者一个人说话，从头到尾不换视角。
- 「你」=读者，且读者始终是**当事人**：讲的是"你"遇到的事，不是"某个人"遇到的事
- 「他/她」只能指**对方**：领导、面试官、HR、评委、同事
- 不许用「他」「很多人」「有些人」「大多数人」指代**处境和读者相同的人** ——
  写「他说：没有，都挺清楚的」，读者读到的是"你在讲别人"；
  写「很多人以为第三轮就是走流程」，读者被归进了第三方群体。两种都是把读者推开。
- 不许开头用「我」讲某人的故事、中段突然切成「你」——读者会迷惑那个人是不是自己
- 引用评论区原话时出现第三方是合法的（「有人在评论区说」），引完立刻回到「你」
- 写完自检：给每句话前面默念「我在跟你说：」，读着别扭的那句就是视角跑了

⛔ 真实性红线 —— **先分清正文里的引号包着的是哪一种**（2026-08-12 Eric 定）：

① **转述型**：写成「他说」「领导说」「面试官问」「有人在评论区说」的话。
   这类必须**逐字**来自上面的案例库或评论区原话库，一个语气词都不许改、不许删。
   不许编造对话、不许编造数字。案例库「来源」列是「采集」的，不能写成"我"的亲身经历，
   要写成"有人在评论区说""看到有人分享"；只有「来源=自有」的才能用第一人称。
   （合规底线：广告法第二十四条 + 小红书社区规范 4.1.4）

② **话术模板**：教读者照着说的那种话（「你可以这样回：…」「换成这句：…」）。
   ✅ **这一类是本篇要产出的核心东西，由你原创，不必也无从在库里找到。**
   对它的要求不是"能不能逐字追溯"，而是：
   - 它针对的**处境**要来自上面的素材（读者真的会遇到、评论区真的有人在问）
   - 它回应的**对方那句话**如果引出来了，那句仍属①类，要逐字可追溯
   - 给的是能直接抄走就用的句子，不是"要真诚沟通"这类道理

⛔ 别因为"库里没有这句"就不敢写话术 —— 那是把本账号最该给的东西砍掉了。
也别把原创话术伪装成「有人说」来蹭真实性，那反而踩了①的红线。

✅ 但**跨来源组合是允许的，而且是本篇希望你做的**（2026-08-05 Eric 定）：
上面两个库给的**不是只有本关键词那几条** —— 直接命中的全给了，此外还补了同场景域
和其它场景域的行，就是为了让你能跨来源组合（数量见各块标题里的 N/总数）。
一个搜索位的答案本来就该由多个真实片段拼出来 —— 把 C012 的对方原话、C037 的结果、
评论区三条不同用户的原话组织进同一篇，每条各自可追溯即合规，不算编造，不会被扣分。
只用单一案例反而写不透。
⚠️ 但这两块是**筛过的子集**，不是全库。手上这些拼不出可信的一篇时，说出来
（在成稿备注里写「素材不足：缺 XXX 类原话」），不要为了凑数编一句。
⛔ 组合的四条边界（越过就是编造。⚠️ 这四条只管上面①类的转述型引语，
不管你原创的话术模板 —— 话术模板不存在"照抄谁"的问题）：
① 转述型引语逐字照抄，一个语气词都不许改；
② 不许把两个人的话合并成一个人说的；
③ 不许给组合出来的案例补一个素材里没有的结果或数字；
④ 不许把多段拼接叙述成「同一个人的一次连续经历」——分别交代"有人""另一个人"。

【卡片文案长度上限 —— 超了人物就被挤没】
一张卡的版面是守恒的：文字占得越多，底部人物图能占的高度越少。
实测有张卡 body 写了 9 行，人物被压到 60px 高，等于白放。所以每张卡：
一张卡的版面预算是 **9 个「等效行」**，title / quote / body 三个字段**共享**这 9 行。
换算方式（渲染器实算，不是估计）：

    title：每 10 字占 1 行，再 ×1.5（字号大）  → 18 字 = 3.0 行
    quote：每 14 字占 1 行，再 ×1.15          → 14 字 = 1.15 行
    body ：每 19 字占 1 行                    → 4 段×19 字 = 4.0 行

**按下面这个配比写，合计 8.15 行，留得住人物图：**
- title ≤ 18 字
- quote ≤ 14 字（一行说完）；这张卡不需要金句就留空 ""，别硬凑
- body ≤ 4 段、每段 ≤19 字（合计 ≤76 字），段间用 `<br>`

⛔ 两个最容易踩的坑（都是实测出来的，不是提醒而已）：

1. **quote 是最贵的字段**。它每行只容 14 字还带 1.15 倍权重，写 35 字就吃掉 3.45 行 ——
   接近预算的四成。实测某篇稿 7 张卡里超标的 2 张，全栽在 quote 写了 45 字和 37 字上，
   而没超标的几张 quote 都是空的。**宁可留空，不要写长。**
2. **别写长句指望它自动断行**。body 超过 19 字会自动折行，
   「body 90 字 + 只用 2 个 `<br>`」看起来守了字数也守了换行数，
   实际每段 30 字、每段折成 2 行 = 6 行，等于偷偷翻倍。**自己按 19 字一段断好。**

装不下就拆成两张卡，别硬塞进一张。

【pose 字段 —— 每张卡必须给，按这张卡讲的内容选人物姿势】
可选姿势（文件名自带语义，照抄名字，不要加 .png）：
{pose_library()}

选图规则：
- **第 1 张 cover 卡不要 pose**（留空字符串）：封面右下角已经有圆形 IP 头像，
  再挂一张大姿势图就是一张卡上两个人。只有第 2-7 张需要 pose。
- 按**这张卡的内容**选，不是按卡型选。讲谈薪拉扯就用 pose19_谈薪拉扯，
  讲被追问答不上来就用 pose8_被追问冒汗，讲复盘记录就用 pose30_记三行笔记。
- ⛔ 7 张卡不许出现重复姿势 —— 每篇笔记的图现在长得都一样，就是因为以前按卡型
  固定映射（scene 永远是面试对坐、contrast 永远是摊手），30 个姿势只用到 9 个。
- 实在没有贴切的，选情绪对得上的（叹气/沉思/如释重负），别硬凑动作。

────────────────────────────────────────────────────
以上是每篇都一样的规则（缓存前缀到此为止）。以下是本篇专属的材料。
────────────────────────────────────────────────────

【今天是 {date.today().isoformat()}】成稿将命名为 成稿_{date.today().isoformat()}_<你给的短名>.md，
文中提到日期或引用卡片文件名时按这个日期写。

【选题】关键词：{kw}
场景域 {row.get('场景域','')} · 意图强度 {row.get('意图强度','')} · 竞争密度 {row.get('竞争密度','')} · 关联案例 {row.get('关联案例ID','')}
{title_block}{rework}
【案例库（{n_case}/{tot_case} 行，按本篇选题筛过；正文引用的原话必须能追溯到这里的某一行；注意「来源」列决定人称）】
{case_block}

【评论区原话（{n_quote}/{tot_quote} 行，按本篇选题筛过；另一个合法原话来源，逐字引用不改写）】
{quote_block}

【该词的探测数据】
{probe_evidence(kw)}

【近 5 篇成稿（开头钩子和签名句不得与这些重复）】
{recent_drafts()}

输出格式（严格遵守，不要输出任何其他内容，不要用代码块包裹）：
{SEP_SLUG}
<文件名短名，6-12 个汉字，不含标点，**必须是读得通的中文短语**：
 宁可少说一层意思，也不要为了压字数砍掉「是/的/被/不」这类虚词。
 ✅ 空降前30天 / 汇报被打断不是没耐心 / 第三轮不再看能力
 ⛔ 孤立多半领导默许（砍虚词砍成了不通的中文，应作「孤立是领导默许」）>
{SEP_MD}
<成稿 markdown 全文，章节顺序照范例：标题 / 发布标题 / 正文 / 图文卡片 / 话题标签 / 处置>
{SEP_JSON}
<7 个卡片对象的 JSON 数组，字段 type/tag/title/quote/body/pose，
 type 依次为 cover,scene,contrast,quote,why,formula,boundary，body 内换行用 <br>>
{SEP_END}{NO_TOOLS_NOTE}"""


def parse_output(out: str):
    def between(a, b):
        m = re.search(re.escape(a) + r"\s*(.*?)\s*" + re.escape(b), out, re.S)
        return m.group(1).strip() if m else ""
    slug = between(SEP_SLUG, SEP_MD)
    md = between(SEP_MD, SEP_JSON)
    raw = between(SEP_JSON, SEP_END) or out.split(SEP_JSON)[-1].strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    try:
        cards = json.loads(raw)
    except json.JSONDecodeError:
        cards = None
    slug = re.sub(r"[^\w一-鿿]", "", slug)
    # ⛔ 2026-08-12 修：原来是 [:12] 静默硬截断。prompt 要的是「6-12 个**汉字**」，
    # 而这里按**字符**数截 —— 关键词带英文缩写（HR / AI / KPI / OKR）时必然踩中：
    # 「HR面试薪酬谈判别再套模板」汉字只有 11 个是合规的，但 HR 占 2 字符、共 13 字符，
    # 被砍成「HR面试薪酬谈判别再套模」，短名当场不通顺。
    # 之前没暴露是因为那几篇的短名恰好是纯中文；这与用哪个模型无关，Claude 一样会中。
    # 现在：上限按字符放宽到 20（中文文件名再长也无妨），且截断必须打出来 ——
    # 静默截断出来的坏短名会一路带进文件名、cards.json、发布日志，事后很难追。
    if len(slug) > SLUG_MAX_CHARS:
        print(f"   ⚠️ 短名 {len(slug)} 字符超上限 {SLUG_MAX_CHARS}，截断："
              f"「{slug}」→「{slug[:SLUG_MAX_CHARS]}」", flush=True)
        slug = slug[:SLUG_MAX_CHARS]
    return slug, md, cards


def run_claude(prompt: str, tag: str) -> str:
    """调 headless claude，失败重试并把原始输出留档。

    一轮 loop 最多要调 6 次 claude -p，每次 60k+ token，连续调用撞限流是常态。
    首跑实测：第 2 轮审核和第 3 轮成稿接连返回异常输出，第一次失败就放弃，
    等于把前面两轮的工都扔了。失败时把 stdout/stderr 存进 loop日志/ ——
    不然只看到「解析失败」四个字，没法判断是限流、超时还是格式跑偏。

    ⛔ 撞额度和「调用失败」要分开处理（2026-08-05 Eric 定）。
    普通失败（格式跑偏、超时）重试 2 次、间隔 45s 就够，再多是浪费。
    但撞额度不是失败，是**还没到时候** —— 08-05 凌晨那次 02:48–03:10 连撞六次
    "You've hit your session limit · resets 4am"，三篇稿两篇没跑满轮次直接归档，
    而只要再等 50 分钟额度就回来了。所以额度错误单独一条路径：
    每 30 分钟试一次，最多熬 5 小时（额度窗口就是 5 小时），期间不消耗普通重试次数。

    ⛔ 但「熬 5 小时」只对 **session limit** 成立（2026-08-11 修）。
    周额度耗尽时要等到重置日才回来，熬 5 小时是纯空转：08-10 全天 112 次调用、
    0 token、0 产出，日志里 112 份留档全是同一句 "hit your weekly limit"。
    所以现在按 claude_limits.classify_limit() 分类，weekly 直接停手。
    """
    limit_waited = 0
    attempt = 0
    while True:
        attempt += 1
        try:
            r = subprocess.run([str(CLAUDE), *HEADLESS_FLAGS, "-p", prompt],
                               cwd=str(BARE_CWD), capture_output=True, text=True,
                               timeout=WRITE_TIMEOUT)
            out = (r.stdout or "").strip()
            if r.returncode == 0 and out and not is_limit(out):
                return out
            err = (r.stderr or "")[:2000]
        except subprocess.TimeoutExpired:
            out, err = "", f"超时 {WRITE_TIMEOUT}s"
        LOG_DIR.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%H%M%S")
        (LOG_DIR / f"{date.today().isoformat()}_{tag}_try{attempt}_{stamp}.txt").write_text(
            f"=== stderr ===\n{err}\n\n=== stdout ===\n{out[:5000]}", encoding="utf-8")

        # 额度限制：出现在 stdout（claude CLI 把它当正常输出打印，returncode 仍是 0），
        # 所以上面必须连 returncode==0 的分支一起查，不然会把这句提示当成稿子存下来。
        kind = classify_limit(out, err)
        if kind == WEEKLY:
            print(limit_banner(kind, tag), flush=True)
            return ""
        if kind:
            if limit_waited + LIMIT_WAIT > LIMIT_MAX_WAIT:
                print(f"   ⛔ {tag} 撞额度已累计等待 {limit_waited//60} 分钟，"
                      f"超过上限 {LIMIT_MAX_WAIT//3600} 小时，停手")
                return ""
            limit_waited += LIMIT_WAIT
            print(f"   ⏳ {tag} 撞额度限制（{kind}），等 {LIMIT_WAIT//60} 分钟后重试"
                  f"（已累计 {limit_waited//60}/{LIMIT_MAX_WAIT//60} 分钟）", flush=True)
            time.sleep(LIMIT_WAIT)
            attempt -= 1        # 等额度不算失败，不消耗普通重试次数
            continue

        print(f"   ⚠️ {tag} 第 {attempt} 次调用失败（原始输出已存 loop日志/）")
        if attempt > RETRIES:
            return ""
        time.sleep(RETRY_WAIT * attempt)


def run_model(prompt: str, tag: str, first_pass: bool) -> str:
    """首轮初稿走 Gemini（免费），返工走 Claude。

    分工是 Eric 定的（2026-08-12）：「写稿、探词、搜索评估用 Gemini 做初步，
    返工和评审再用 Claude」。这里的判据是 **feedback 是否为空**：
      · feedback 空  = 从零写第一稿，没有审核意见要吃透 → Gemini
      · feedback 非空 = 带着审核报告做定向修改，要准确理解扣分点并只改那几处 → Claude

    这不只是省额度的取舍，也是能力匹配：Gemini 免费层只有 Flash（Pro 全系额度为 0，
    实测连一句 hi 都 429），而定向返工恰恰是最吃理解力的一环 —— 改错地方比不改更糟。

    每轮 claude -p 有 38.7k 固定开销 + 33.8k 内容 ≈ 72.5k；首轮换掉即省这一整块。
    撞额度静默回退 Claude，不让稿子因为 Gemini 不可用就写不出来。
    """
    if (first_pass and os.environ.get("XHS_ENGINE", "gemini").lower() != "claude"
            and gemini_cli.available()):
        try:
            # 温度比探词分析高：那边要的是稳定判据，这边要的是可读的稿子。
            out = gemini_cli.run(prompt, temperature=0.7)
            if out:
                print(f"   · {tag} 由 Gemini 出稿（未消耗 Claude 额度）", flush=True)
                return out
        except gemini_cli.QuotaExhausted as e:
            print(f"   ⏬ Gemini 免费额度已满，回退 Claude：{e}", flush=True)
        except Exception as e:                        # noqa: BLE001
            print(f"   ⏬ Gemini 出稿失败，回退 Claude：{e}", flush=True)
    return run_claude(prompt, tag)


def write_draft(row, feedback, round_no, dry_run=False, lane="搜索流", fixed_title=""):
    prompt = build_prompt(row, feedback, round_no, lane, fixed_title)
    if dry_run:
        print(f"--- [dry-run] 第 {round_no} 轮 prompt 共 {len(prompt)} 字，前 600 字：\n{prompt[:600]}\n---")
        return None, None, None
    return parse_output(run_model(prompt, f"write_r{round_no}", first_pass=not feedback))


def save(slug, md, cards):
    today = date.today().isoformat()
    stem = f"{today}_{slug}"
    # headless claude 不知道今天几号，md 正文里那句「卡片见 图文_YYYY-MM-DD_xxx_cards.json」
    # 常常写成别的日期（跨午夜跑必错），指向一个不存在的文件。按实际落盘名校正。
    md = re.sub(r"图文_\d{4}-\d{2}-\d{2}_[^\s`）)]*?_cards\.json", f"图文_{stem}_cards.json", md)
    draft = SUCAI / f"成稿_{stem}.md"
    draft.write_text(md, encoding="utf-8")
    if cards:
        (SUCAI / f"图文_{stem}_cards.json").write_text(
            json.dumps(cards, ensure_ascii=False, indent=1), encoding="utf-8")
    return draft


def render_cards(slug: str) -> bool:
    """渲染 7 张卡片图（headless Chrome，零 AI 生图成本）。

    发布闸门第 4 条要求成品图目录有已渲染的图，不渲染的话稿子再好也卡在闸门外——
    实测那篇 89 分绿稿就是因为这个被判 ⛔，链路差这一步没通。
    """
    today = date.today().isoformat()
    src = SUCAI / f"图文_{today}_{slug}_cards.json"
    out = SUCAI / "成品图" / f"{today}_{slug}"
    if not src.exists():
        print("   ⚠️ 无卡片 JSON，跳过渲染（发布闸门会拦下）")
        return False
    r = subprocess.run([sys.executable, str(CARDS_SCRIPT), str(src), f"{out}/"],
                       cwd=CARDS_SCRIPT.parent, capture_output=True, text=True, timeout=300)
    n = len(list(out.glob("*.png"))) if out.exists() else 0
    if r.returncode != 0 or n < 7:
        print(f"   ⚠️ 卡片渲染异常（{n}/7 张）：{(r.stderr or r.stdout)[:200]}")
        return False
    print(f"   卡片图 → 成品图/{today}_{slug}/（{n} 张）")
    return True


def mech_check(fname: str, lane="搜索流"):
    r = subprocess.run([sys.executable, str(HEALTH / "draft_check.py"), "--file", fname,
                        "--lane", lane], capture_output=True, text=True)
    return r.returncode == 0, (r.stdout or "").strip()


def _audit_rows(fname: str) -> list:
    if not AUDIT_LOG.exists():
        return []
    return [r for r in csv.DictReader(AUDIT_LOG.open(encoding="utf-8-sig"))
            if (r.get("成稿文件") or "").strip() == fname
            and (r.get("审核方") or "").strip() == "独立审核"]


def audit(fname: str, lane="搜索流"):
    """跑独立审核并取本轮新增的那一行。分数为 None = 审核失败（不是低分）。

    ⛔ 这里曾经只取「该文件最后一行独立审核记录」，不管这轮审核有没有真的写出新行。
    首跑就踩了：第 2 轮审核调用失败没写行，函数原样返回上一轮的 83 分，loop 以为
    返工没效果 —— 实际那一稿手动复审是 89 分，早该过线收工。更危险的方向是反过来：
    上一轮若是 90 分，这次审核失败会被读成「过线」直接放行一篇没审过的稿。
    所以改成用行数增量判断，审出来才算数。
    """
    before = len(_audit_rows(fname))
    for attempt in (1, 2):
        r = subprocess.run([sys.executable, str(HEALTH / "independent_audit.py"), "--force", fname,
                            "--lane", lane], capture_output=True, text=True,
                           # ⛔ 不能用 WRITE_TIMEOUT(900s)：independent_audit 撞额度时会自己
                           # 等到 5 小时，900s 就把它掐死了，等于额度退避在审核这一步失效。
                           timeout=LIMIT_MAX_WAIT + WRITE_TIMEOUT)
        rows = _audit_rows(fname)
        if len(rows) > before:
            break
        LOG_DIR.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%H%M%S")
        (LOG_DIR / f"{date.today().isoformat()}_audit_try{attempt}_{stamp}.txt").write_text(
            f"=== stdout ===\n{r.stdout[:5000]}\n\n=== stderr ===\n{r.stderr[:2000]}", encoding="utf-8")
        print(f"   ⚠️ 独立审核第 {attempt} 次未写出记录（原始输出已存 loop日志/）")
        if attempt == 1:
            time.sleep(RETRY_WAIT)
    else:
        return None, "审核未产生新记录", "", ""

    last = rows[-1]
    try:
        score = int((last.get("总分") or "0").strip())
    except ValueError:
        score = 0
    report = _read_or(SUCAI / "审核报告" / f"{Path(fname).stem}_独立审核.md", last.get("备注", ""))
    return score, (last.get("红线") or "").strip(), report, (last.get("处置") or "").strip()


def rework_queue() -> list:
    """处置=返工 的稿，按分数从高到低排 —— 离 85 最近的先改，最省额度。

    这一档是 2026-08-05 加的（原先 75-84 全落进「待人工」这个死档，人不看就永远停在那）。
    实测这批稿的扣分极其集中，审核报告里常常直接写着「改一句即可上 85」：
    84 分那篇是「首图大字非搜索原句 + 开头首句未含关键词」，两处都是一句话的事。
    所以返工不是从头重写，是拿着审核报告做定向修改 —— prompt 里喂的 feedback
    就是上一次的完整审核报告。

    稿可能在 素材库/ 也可能已经被 park 进 归档稿/，两处都要找。
    """
    latest, released = {}, set()
    for r in csv.DictReader(AUDIT_LOG.open(encoding="utf-8-sig")) if AUDIT_LOG.exists() else []:
        who, fname = (r.get("审核方") or "").strip(), (r.get("成稿文件") or "").strip()
        if who == "独立审核":
            latest[fname] = r
        elif who == "人工放行":
            released.add(fname)
    # 已发出去的稿不该再返工：改了也不会重发，纯烧额度。
    # 两个来源都要查——发布日志记流程走过的，人工放行记人拍板放行的。
    sys.path.insert(0, str(REPO / "scripts" / "xhs-publish"))
    kw_full = set()
    try:
        from auto_publish import (MAX_PER_KEYWORD, keyword_of,
                                  published_already, published_keyword_counts)
        released |= published_already()
        # ⛔ 2026-08-15：同一关键词已发满的，返工也别再排。
        # 闸门那边已经拦住不让发（见 auto_publish.MAX_PER_KEYWORD），
        # 这里不拦的话，loop 会一直挑这些「离过线最近」的高分稿去改 ——
        # 改好了照样发不出去，纯烧额度。实测返工队列里「汇报被领导打断怎么接」
        # 和「绩效面谈被打低分怎么开口」各压着好几篇，两个词都已经发满 3 篇。
        counts = published_keyword_counts()
        kw_full = {k for k, v in counts.items() if v >= MAX_PER_KEYWORD}
    except Exception:
        keyword_of = None
    out = []
    skipped_full = []
    for fname, r in latest.items():
        if (r.get("处置") or "").strip() != "返工" or fname in released:
            continue
        if keyword_of and kw_full:
            kw = keyword_of(fname)
            if kw in kw_full:
                skipped_full.append((fname, kw))
                continue
        path = next((p for p in (SUCAI / fname, SUCAI / "归档稿" / fname) if p.exists()), None)
        if not path:
            continue
        try:
            score = int((r.get("总分") or "0").strip())
        except ValueError:
            score = 0
        out.append({"file": fname, "path": path, "score": score, "row": r,
                    "stale": is_stale_score(r)})
    # 静默跳过会让人以为队列本来就这么短，得说出来 —— 这几篇不是没排上，是发不出去
    if skipped_full:
        kws = sorted({k for _, k in skipped_full})
        print(f"   · 跳过 {len(skipped_full)} 篇：关键词已发满 {MAX_PER_KEYWORD} 篇"
              f"（{'、'.join(kws)}），改好了也过不了闸门")
    return sorted(out, key=lambda x: -x["score"])


def is_stale_score(audit_row) -> bool:
    """这条审核记录是不是**旧评分卡**打的分 —— 旧分不能和新分比大小。

    ⛔ 2026-08-13 查出的真 bug，代价是 11 篇返工被全部误判成失败。

    2026-08-11 评分卡撤销了「原话可信度」15 分（降为红线是/否）。于是审核记录里
    出现两种分：带可信度分的（旧卡）和不带的（新卡）。而 rework_queue 取的是
    同名文件的**最新一条**记录 —— 对 08-11 之前审过、之后没再审的稿，那条就是旧卡分。

    实测「成稿_2026-08-02_领导不回消息.md」：
        旧卡 08-04 → 84 分（可信度 8）
        新卡 08-13 → **47 分**（同一份内容，指纹 5ab81cc5）
        返工产出 08-13 → **81 分**（新卡）
    真相是返工把 47 分提到了 81 分、涨了 34 分；系统却拿 81 去和旧卡的 84 比，
    判定「越改越差」，回滚到那份实际只值 47 分的原稿。

    差距远大于被撤销的 15 分（标题 20→6、正文 11→4），说明 08-11 那次不只是删了一项，
    是整体收紧了标准。所以跨卡的分数**一分都不能比**，不是减 15 分就能对齐。
    ⛔ 2026-08-15 又换了一次卡（Eric 定，按判据有没有数据支撑重排权重）：
        搜索意图 20→32 · 标题 25→35 · 首图 20→10 · 开头 15→8 · CTA 10→5
    幅度比 08-11 那次还大（首图砍一半、搜索意图涨 60%），同一份稿在两套卡下
    差十几分是常态。所以现在有两条边界，任一条命中就算旧卡。
    """
    if (audit_row.get("可信度") or "").strip() not in ("", "-"):
        return True                      # 08-11 之前：带已撤销的可信度分
    return (audit_row.get("日期") or "").strip() < CARD_V3_DATE


# 08-15 权重重排的生效日 —— 这一天之前的分数与之后的不可比。
CARD_V3_DATE = "2026-08-15"


BODY_SECTION_RE = re.compile(r"^#{1,3}\s*\*{0,2}正文[^\n]*\n(.*?)(?=\n#{1,3}\s|\Z)", re.M | re.S)


def body_budget_hint(draft: Path, lane: str) -> str:
    """告诉模型「你离正文字数上限还有多远」。

    ⛔ 2026-08-12 加。返工最常见的失败不是改错方向，是**改着改着把字数改超了**：
    那篇 HR 谈薪稿 3 轮返工，第 1 轮 76→82 分红线清零，第 2、3 轮却全栽在
    正文 571 / 568 字（上限 560），白跑两轮还得回滚到第 1 轮的版本。

    根因是模型看不见自己离上限有多近。而审核意见常常是「关键词只出现 2 次，要求 3-5 次」
    这类**要求补内容**的条目 —— 当时正文 539 字、余量仅 21 字，照着补必然超。
    正确解法是等量替换（把「这件事」换成完整关键词），不是净增。这一点必须明说。

    口径与 draft_check.BODY_RANGE 保持一致：那里是判据，这里只是把判据翻译给模型。
    """
    m = BODY_SECTION_RE.search(_read_or(draft, ""))
    if not m:
        return ""
    n = len(re.sub(r"\s|（正文总字数[^）]*）", "", m.group(1)))
    # 上限从 draft_check 直接取，不在这里抄第二份 —— 判据只能有一处。
    # （LANE_SPEC 里的 words="100-500" 是给模型看的口径，实判上限是 560，两者不是一回事。）
    sys.path.insert(0, str(HEALTH))
    from draft_check import BODY_RANGE  # noqa: E402  延迟导入，避免脚本启动就拉起检查器依赖
    hi = BODY_RANGE.get(lane, (100, 560))[1]
    left = hi - n
    # ⛔ 2026-08-13 重写这段。08-12 首版按余量分档：>80 字只报数字，≤80 才给约束。
    # 结果 8 篇返工全军覆没，实测返工时正文**净增 90-159 字**（占原文 19-34%）：
    #   汇报被打断 464→623(+159) · 未必怪你 469→602(+133) · 分三种 475→565(+90)
    # 这几篇返工前余量都是 85-96 字，**刚好越过 80 的阈值**，于是一条约束都没给。
    #
    # 但真正的教训不是阈值调多少 —— 是**模型在返工时根本没做定向修改，而是重写**。
    # prompt 早就写着「只改点到的问题，其余保持原样，不要重写」，它照样重写；
    # 重写又把原稿里本来得分的东西一起换掉，于是分数不升反降（84→71、84→78、83→80）。
    # 所以字数守恒必须变成**每次都给的硬约束**，而不是余量紧张时才提醒。
    cap = min(hi, n + 30)
    base = (f"\n【⛔ 字数守恒（这是返工，不是重写）】当前正文 {n} 字，硬上限 {hi} 字。\n"
            f"改完之后正文应当在 **{max(100, n - 60)}–{cap} 字**之间 —— "
            f"即相对现在**最多只准多 30 字**。\n"
            f"实测教训：8 篇返工稿平均净增 120 字，全部撞上限作废，而且分数不升反降 —— "
            f"多写的那些字换掉了原稿里本来得分的部分。\n"
            f"审核意见里凡是要求「补」的（补关键词、补案例、补细节），一律**先删等量的字再加**。"
            f"优先用等量替换：把「这件事」「上面说的」这类指代直接换成完整关键词，"
            f"字数几乎不变，关键词次数就上去了。")
    if left <= 80:
        base += f"\n⚠️ 现在离上限只剩 {left} 字，几乎没有腾挪空间，务必先删后加。"
    return base


def rework_one(item, args, lane="搜索流"):
    """对一篇已有的稿做定向返工：喂上一次的审核报告，改完重审，过线就渲染图交闸门。

    与 run_one 的区别是不重新选题、不从零写 —— 关键词、卡片、结构都沿用原稿，
    只按审核意见改。所以 feedback 从第 1 轮就带着，不像 run_one 第 1 轮是空的。
    """
    fname, draft = item["file"], item["path"]
    kw = keyword_of_draft(draft)
    row = next((r for r in csv.DictReader(CIKU.open(encoding="utf-8-sig"))
                if (r.get("关键词") or "").strip() == kw), {"关键词": kw}) if kw else {"关键词": ""}
    report = _read_or(SUCAI / "审核报告" / f"{draft.stem}_独立审核.md", item["row"].get("备注", ""))
    print(f"\n{'='*54}\n返工 {fname}（上次 {item['score']} 分，差 {85-item['score']} 分到线）")

    # 归档稿里的先挪回素材库根目录：draft_check / independent_audit 都只扫根目录，
    # 留在归档稿里改完了也没人审。过不了线时 park() 会把它再放回去。
    if draft.parent.name == "归档稿":
        draft = draft.replace(SUCAI / fname)

    slug = fname.removeprefix("成稿_").removesuffix(".md").split("_", 1)[-1]
    feedback = (f"【上一次独立审核 {item['score']} 分，未过线（需 ≥{args.threshold}）。"
                f"这是定向返工：只改下面点到的问题，其余保持原样，不要重写。】\n"
                f"{report[:4000]}\n{body_budget_hint(draft, lane)}\n{KEEP_PASSED}")
    # 旧评分卡的分数不能当门槛（见 is_stale_score）：拿它比会把真正的改进判成倒退，
    # 然后回滚到那份按现行标准根本不合格的原稿。基线置 -1 = 本轮任何过了机械检查的
    # 新版本都优于它，之后的轮次再正常择优（都是新卡分，可比）。
    if item.get("stale"):
        print(f"   ⚠️ 上次 {item['score']} 分是**旧评分卡**（08-11 前，含已撤销的可信度项）打的，"
              f"与本轮新卡分数不可比 —— 不拿它当门槛，本轮以新卡结果为准")
    base_score = -1 if item.get("stale") else item["score"]
    best = {"score": base_score, "md": draft.read_text(encoding="utf-8"), "cards": None}
    for rnd in range(1, args.rounds + 1):
        print(f"\n--- 返工第 {rnd}/{args.rounds} 轮 ---")
        new_slug, md, cards = write_draft(row, feedback, rnd, lane=lane)
        if not md:
            print("⛔ 返工输出解析失败（原始输出见 loop日志/）")
            break
        draft = save(slug, md, cards)
        ok, mech = mech_check(draft.name, lane)
        if not ok:
            print(f"机械检查未过：\n{mech}")
            feedback = f"【机械检查（代码硬核对，必须全部修掉）】\n{mech}\n{KEEP_PASSED}"
            continue
        score, redline, report, disposition = audit(draft.name, lane)
        if score is None:
            print("⛔ 独立审核未写出记录，停手")
            break
        print(f"独立审核：{score} 分 · 红线：{redline or '无'} · 处置：{disposition or '?'}")
        if score > best["score"]:
            best = {"score": score, "md": md, "cards": cards}
        if score >= args.threshold and redline in ("", "无") and disposition == "发布":
            print(f"\n✅ 返工过线（{item['score']} → {score}）")
            render_cards(slug)
            predict_one(draft.name, lane)
            return "过线"
        feedback = (f"【独立审核 {score} 分 · 处置「{disposition}」，仍未过线】\n"
                    f"{report[:4000]}\n{KEEP_PASSED}")
    if best["md"] and best["md"] != draft.read_text(encoding="utf-8"):
        draft = save(slug, best["md"], best["cards"])
        print(f"回滚到最佳版本（{best['score']} 分）")
    park(draft, best["score"], feedback if "feedback" in dir() else "返工未过线")
    return "归档"


def keyword_of_draft(path: Path) -> str:
    """成稿头部写着「关键词来源：`词库.csv`「XXX」」，直接取，不靠标题猜。"""
    m = re.search(r"关键词来源[^「]*「([^」]+)」", path.read_text(encoding="utf-8")[:2000])
    return m.group(1).strip() if m else ""


def park(draft: Path, score, note):
    """3 轮仍不过 → 移进 归档稿/ 并登记待激活库，停手不再消耗。

    这个止损机制不是新发明：待激活素材库.csv 里那条 86 分的稿就是「3 轮定向修改后仍卡在
    上限」被存起来的，已经验证过有效。按既有约定要真的把文件移进 归档稿/——
    留在素材库根目录的话，health_check 每天都会拿它报一次机械检查违规，
    而它已经是「决定不发」的稿，那个告警纯属噪音。
    """
    archive = SUCAI / "归档稿"
    archive.mkdir(exist_ok=True)
    target = archive / draft.name
    draft.replace(target)
    new = not PENDING.exists()
    with PENDING.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["日期", "选题名", "最终得分", "主要扣分项", "成稿文件路径", "状态"])
        w.writerow([date.today().isoformat(), draft.stem, score, note[:300],
                    str(target.relative_to(SUCAI)), "待激活"])
    print(f"   已移入 归档稿/{draft.name}")


def predict_one(draft_name: str, lane: str):
    """过线后押个数，7 天后由 review_prediction.py 对账。

    不押数就没法复盘：「这篇效果不好」是感觉，「预测观看 400 实际 120」才是能改进的结论。
    """
    r = subprocess.run([sys.executable, str(Path(__file__).parent / "predict.py"),
                        draft_name, "--lane", lane], capture_output=True, text=True, timeout=120)
    print((r.stdout or "").strip() or f"   ⚠️ 预测失败：{(r.stderr or '')[:200]}")


def run_one(row, args, lane, fixed_title="") -> str:
    """跑完一篇的完整闭环。返回 过线 / 归档 / 失败。"""
    kw = row["关键词"]
    print(f"\n{'='*54}\n选题：{kw}（{lane} · 密度 {row.get('竞争密度','?')} · 意图 {row.get('意图强度','?')}）")

    # 越改越差是真会发生的：复跑实测第 2 轮 84 分，第 3 轮为了修审核意见把具体名词密度
    # 从 2.0+ 改到 1.51，连机械检查都没过。只留最后一版等于把最好的那版扔了，所以择优。
    best = {"score": -1, "md": None, "cards": None}
    feedback, draft, score, slug, md = "", None, 0, None, None
    for rnd in range(1, args.rounds + 1):
        print(f"\n--- 第 {rnd}/{args.rounds} 轮 ---")
        new_slug, md, cards = write_draft(row, feedback, rnd, lane=lane, fixed_title=fixed_title)
        if not new_slug or not md:
            print("⛔ 成稿输出解析失败（原始输出见 loop日志/）")
            break
        # 短名固定用第一轮的：模型每轮可能起不同短名，不固定就会散落好几个文件，
        # 而检查和审核只认最后一个，前面几份成了没人管的孤儿稿。
        slug = slug or new_slug
        draft = save(slug, md, cards)
        print(f"成稿 → {draft.name}" + ("" if cards else "（⚠️ 卡片 JSON 解析失败，首图无法核验）"))

        ok, mech = mech_check(draft.name, lane)
        if not ok:
            print(f"机械检查未过：\n{mech}")
            feedback = f"【机械检查（代码硬核对，必须全部修掉）】\n{mech}\n{KEEP_PASSED}"
            continue
        print("机械检查通过 → 送独立审核")

        score, redline, report, disposition = audit(draft.name, lane)
        if score is None:
            print("⛔ 独立审核连续两次未写出记录，本轮无法判定，停手（不拿上一轮旧分充数）")
            break
        print(f"独立审核：{score} 分 · 红线：{redline or '无'} · 处置：{disposition or '?'}")
        if score > best["score"]:
            best = {"score": score, "md": md, "cards": cards}
        # 必须同时满足处置=发布。D7 定的是「处置以独立审核为准」，只看分数会和闸门打架：
        # 实测一篇 86 分绿、红线无、但审核员处置写「待人工」的稿，loop 判过线收工，
        # auto_publish 却拦下 —— loop 以为成了，稿子其实卡在闸门外，而且不会再返工。
        if score >= args.threshold and redline in ("", "无") and disposition == "发布":
            print(f"\n✅ 过线（≥{args.threshold} 且无红线）")
            render_cards(slug)
            predict_one(draft.name, lane)
            # 时间以 ~/Library/LaunchAgents/com.eric.xhspublish.plist 为准，改 plist 记得同步这里。
            # 这里曾写「10:00」而 plist 是 22:00，害人白天等发布、等不到也不知道该去查哪。
            print("   → 交给 auto_publish 闸门（每晚 22:00 定时任务取，半自动：预填 + 打开定时开关，最后一步仍需人点）")
            return "过线"
        feedback = (f"【独立审核 {score} 分 · 处置「{disposition}」，未过线"
                    f"（需 ≥{args.threshold}、红线为无、且处置=发布）】\n"
                    f"{report[:4000]}\n{KEEP_PASSED}")

    if not draft:
        print("⛔ 一轮成稿都没产出，无产物可归档")
        return "失败"
    # 比内容不比分数：机械检查未过的轮次根本没拿到分，score 还留着上一轮的值，
    # 拿它比较会得出「最后一版就是最佳」的错误结论，回滚静默失效。
    if best["md"] and best["md"] != md:
        draft = save(slug, best["md"], best["cards"])
        print(f"回滚到最佳版本（{best['score']} 分，最后一版更差）")
    print("⏸ 未过线／中途停手，存入待激活素材库")
    park(draft, max(best["score"], 0), feedback or "loop 中断，未取得有效审核分")
    return "归档"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", help="指定关键词（默认从词库自动选；--count>1 时只作用于第一篇）")
    ap.add_argument("--rounds", type=int, default=MAX_ROUNDS)
    ap.add_argument("--threshold", type=int, default=PASS_SCORE)
    ap.add_argument("--count", type=int, default=1, help="连跑几篇（消化候选积压用）")
    ap.add_argument("--rework", type=int, nargs="?", const=3, metavar="N",
                    help="先返工 N 篇处置=返工 的旧稿（默认 3），再写新稿。"
                         "返工优先：这批离 85 只差一两分，改一句就能过，比从零写新稿省得多")
    ap.add_argument("--rework-only", action="store_true", help="只返工，不写新稿")
    ap.add_argument("--rework-file", action="append", metavar="FILENAME",
                    help="指定返工哪几篇（可重复），不按分数自动排队")
    ap.add_argument("--lane", default="搜索流", choices=["搜索流", "推荐流"])
    ap.add_argument("--title", default="", help="指定首选标题（人已挑定时用），正文须兑现它推翻的预设")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    tally = {"过线": 0, "归档": 0, "失败": 0}

    # 返工排在写新稿前面：这批稿离 85 只差一两分、扣分点已由审核报告点名，
    # 一次定向修改的成本远低于从零写一篇，额度花在这里回报最高。
    n_rework = args.rework if args.rework is not None else (3 if (args.rework_only or args.rework_file) else 0)
    if n_rework:
        queue = rework_queue()
        if args.rework_file:
            want = set(args.rework_file)
            missing = want - {q["file"] for q in queue}
            for m in missing:
                print(f"⛔ {m} 不在返工队列里（处置不是「返工」，或已发布）")
            queue = [q for q in queue if q["file"] in want]
        else:
            queue = queue[:n_rework]
        if not queue:
            print("返工队列为空（没有处置=返工 的稿）")
        for item in queue:
            if args.dry_run:
                print(f"[dry-run] 返工 {item['file']}（{item['score']} 分）")
                continue
            tally[rework_one(item, args, args.lane)] += 1
    if args.rework_only:
        print(f"\n{'='*54}\n返工 {n_rework} 篇：过线 {tally['过线']} · 归档 {tally['归档']}")
        return 0

    for i in range(args.count):
        if args.topic and i == 0:
            rows = [r for r in csv.DictReader(CIKU.open(encoding="utf-8-sig"))
                    if (r.get("关键词") or "").strip() == args.topic]
            row = rows[0] if rows else {"关键词": args.topic}
        else:
            row = pick_topic()
        if not row:
            # 燃料耗尽不是「今天没事干」，是上游断供了：采集 → probe → auto_analyze。
            # 这条链停在哪一环，loop 就从哪一天起空转，说清楚该去补哪一环。
            print("⛔ 无题可做：词库里没有「状态=已验证 且 未写过」的词。")
            print("   燃料在上游：python3 scripts/xhs-probe/probe.py --from-cikuku --limit 5")
            print("             → auto_analyze.py → backfill.py（做→已验证，全自动）")
            return 1 if i == 0 else 0

        if args.dry_run:
            print(f"选题：{row['关键词']}（{args.lane}）")
            write_draft(row, "", 1, dry_run=True, lane=args.lane, fixed_title=args.title)
            return 0
        tally[run_one(row, args, args.lane, args.title)] += 1

    if args.count > 1:
        print(f"\n{'='*54}\n本轮 {args.count} 篇："
              f"过线 {tally['过线']} · 归档 {tally['归档']} · 失败 {tally['失败']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
