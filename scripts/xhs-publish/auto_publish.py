#!/usr/bin/env python3
"""半自动发布 — 闸门通过的成稿自动预填 + 打开定时开关，最后一步留给人。

为什么是半自动：小红书发布页的日历组件（d-datepicker）只认 isTrusted 的原生手势，
JS 合成 click、完整鼠标事件序列、CDP Input.dispatchMouseEvent 全部试过，
元素能点中但面板不展开。选时段这一下没法程序化，索性交给人——每篇约 10 秒。
（还没穷尽：现有 /clickAt 只发了 mousePressed+mouseReleased，没有前置 mouseMoved、
没带 buttons、两个事件之间没有延迟。click_probe.mjs 就是来逐条试这些的。）

接力模式（2026-08-06 起默认开）：预填一篇 → 轮询盯着这个 tab 等它离开发布页 →
自动预填下一篇。所以 plist 只需要 22:00 一个触发点就能走完 DAILY_QUOTA 篇。

复用 scripts/case-entry/case_entry.py 的 prefill_xhs / do_publish_click，不重写发布流程。

⛔ 四道闸门，全部满足才发（任一不满足 → 跳过并记日志，绝不发）：
  1. 审核记录里该稿有「独立审核」行，且最新处置 = 发布
     （或有「人工放行」行 —— 见 --approve）
  2. draft_check.py 机械及格线通过
  3. 该稿尚未发布过（发布日志里没有成功记录）
  4. 成品图目录有已渲染的卡片图

为什么要闸门：2026-08-02 之前自评有处置权，一篇独立审核 67 分该返工的稿挂着「发布」状态。
若那时已有自动发布，它会被发出去。闸门就是防这个。

用法：
  python3 auto_publish.py                      # 定时任务入口；每天最多发 DAILY_QUOTA 篇，时段轮换
  python3 auto_publish.py --dry-run            # 走到预填为止，不点发布
  python3 auto_publish.py --approve 成稿_x.md  # 人工放行一篇（写入审核记录，下次运行即可发）
  python3 auto_publish.py --list               # 只看闸门评估结果，不做任何操作
  python3 auto_publish.py --confirm 成稿_x.md --at "2026-08-03 09:00"
                                               # 你在页面上点完「定时发布」后跑这个，回填词库
"""
import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median

# ⛔ 小红书发布页的日历是**北京时间**（页面上就写着「北京时间」），而本机在北美（EDT，
# 比北京慢 12 小时）。时段轮换池那 7 个点是给中国读者选的，本来就该按北京时间算。
#
# 2026-08-06 实测这个 bug 的后果：next_slot 用 datetime.now()（本机 22:30）算出「09:00」，
# 填进日历变成**北京 08-07 09:00** —— 而当时北京已经 10:30，时间在过去，
# 小红书不报错，直接把稿立刻发了出去（后台显示 10:36 发布，不是定时）。
# 也就是说：时区搞错不会失败，只会**安静地变成立即发布**，时段实验的数据全废。
BEIJING = timezone(timedelta(hours=8))


def bj_now():
    """当前的北京墙上时间（不带时区信息，直接可与日历上的数字比较）。"""
    return datetime.now(BEIJING).replace(tzinfo=None)

REPO = Path(__file__).resolve().parents[2]
SUCAI = REPO / "xhs" / "素材库"
AUDIT_LOG = SUCAI / "审核记录.csv"
CIKU = SUCAI / "词库.csv"
PUB_LOG = SUCAI / "发布日志.csv"
PROXY_BASE = "http://localhost:3456"
HEALTH_DIR = REPO / "scripts" / "xhs-health"

# 闸门线的唯一来源 —— 别在本文件里再写一次数字（2026-08-15 查出全仓有四处
# 各写各的 85，而这里早已是 80，详见 independent_audit.PASS_SCORE 上方注释）。
sys.path.insert(0, str(HEALTH_DIR))
try:
    from independent_audit import PASS_SCORE
except Exception:
    PASS_SCORE = 80
# 闸门发现缺成品图时现场补渲染用。与 refine_loop.CARDS_SCRIPT 是同一个脚本。
CARDS_SCRIPT = SUCAI / "图文模板" / "make_cards.py"

sys.path.insert(0, str(REPO / "scripts" / "case-entry"))

PUB_LOG_COLS = ["日期", "成稿文件", "标题", "闸门", "预填", "定时", "发布", "笔记链接", "备注"]

# 发布时段轮换池 — 每次发布取下一个，用来测出哪个时段搜索进入占比最高。
# 一轮 7 个点跑完，配合词库的「搜索来源占比」回填即可横向比较。
SLOT_HOURS = [9, 11, 12, 17, 18, 20, 22]
DAILY_QUOTA = 3          # 每天发 3 篇，不是一天占满 7 个时段
SCHED_LEAD = timedelta(minutes=30)   # 小红书定时发布需要提前量，太近会被拒
# 谁的「处置」算数。gate 取的是这些审核方里**最后一条**，所以少列一个，
# 那一方的结论就等于没写过。
# ⛔ 2026-08-14 补两个，都是踩到才发现的：
#   · 自动分诊（08-13 加）—— 不列进来的话，分诊判了「归档」闸门也看不见，
#     稿子仍按更早那条独立审核的「发布」放出去。反了。
#   · 阈值重判（08-14 阈值 85→80 时批量补的行）—— 不列进来重判就白写。
TRUSTED_AUDITORS = {"独立审核", "人工放行", "自动分诊", "阈值重判"}

# 接力模式：预填一篇 → 轮询等人点完「定时发布」→ 自动预填下一篇。
# 有了它，一天跑一次（22:00）就能走完 DAILY_QUOTA 篇；此前只能靠一天跑三次凑。
RELAY_POLL = 6                  # 每 6 秒看一眼页面
RELAY_TIMEOUT = 45 * 60         # 单篇最多等 45 分钟，超了就停手（不猜你发没发）
RELAY_LOST_STRIKES = 3          # 连续 3 次读不到页面才判定 tab 没了，避免抖动误判


def next_slot(now=None, skip=0):
    """按发布日志里上一次用过的时段，取池中下一个；该点今天已过就顺延到明天。
    严格保持轮换顺序——不因今天来不及就跳号，否则时段对比会有偏。

    ⛔ 全程用**北京时间**：返回值是要填进小红书日历的数字，那个日历是北京时间。
    用本机时间算会安静地变成「立即发布」（见文件顶部 BEIJING 那段注释）。"""
    now = now or bj_now()
    used = []
    for r in read_csv(PUB_LOG):
        # 只有真发出去的才算占用时段：dry-run 与失败记录不能推进轮换，
        # 否则几次调试就把 7 个时段空耗完，时段对比的样本量会失衡。
        if not (r.get("发布") or "").startswith("✅"):
            continue
        s = (r.get("定时") or "").strip()
        if " " in s:
            try:
                used.append(int(s.split()[1].split(":")[0]))
            except ValueError:
                pass
    if used and used[-1] in SLOT_HOURS:
        hour = SLOT_HOURS[(SLOT_HOURS.index(used[-1]) + 1 + skip) % len(SLOT_HOURS)]
    else:
        # 还没发过：挑今天来得及的最早时段，别让第一篇白等到明天。
        # 之后就严格按池子顺序轮换，保证每个时段样本量一致。
        todo = [h for h in SLOT_HOURS
                if now.replace(hour=h, minute=0, second=0, microsecond=0) > now + SCHED_LEAD]
        hour = todo[skip % len(todo)] if todo else SLOT_HOURS[skip % len(SLOT_HOURS)]
    # 分钟固定整点：定时发布界面上真人只会选 9:00 这种整点，挑 9:47 反而不像人操作
    cand = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if cand <= now + SCHED_LEAD:
        cand += timedelta(days=1)
    return cand.strftime("%Y-%m-%d %H:%M"), hour


def read_csv(path):
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def published_today():
    today = date.today().isoformat()
    return sum(1 for r in read_csv(PUB_LOG)
               if r.get("发布", "").startswith("✅") and (r.get("日期") or "").startswith(today))


def published_already():
    return {r["成稿文件"].strip() for r in read_csv(PUB_LOG) if r.get("发布", "").startswith("✅")}


# 同一个关键词最多发几篇。2026-08-15 Eric 提出「话题重复度太高，
# 已经有差不多 6 篇汇报被打断了」，查证属实且比这更糟 ——
# 后台 33 篇已发布里「被打断」类占 5 篇（汇报 3 + 答辩 2）、「绩效面谈被打低分」3 篇。
#
# 数据说明重复发不划算：**量不累积，反而集体停在低位**。
#     晋升答辩      1050 → 301           （第 2 篇跌 3.5 倍）
#     绩效被打低分   157 → 107 → 107
#     汇报被打断      62 →  61 →  60      （三篇几乎一样，都很低）
# 对照单主题的稿：156 / 153 / 149。同词的后续篇是在跟自己抢同一个搜索位。
#
# 阈值定 2 而不是 1：第 2 篇仍能拿到第 1 篇的 30-70%（301、107），有价值；
# 第 3 篇起明显是内耗。
#
# ⛔ 为什么闸门必须管这件事：选题层的 used_keywords() 只在**选新题**时排重，
# 而**返工不走选题** —— 一篇稿返工 N 次就产生 N 个成稿文件，标题各不相同，
# 闸门原来只查「标题是否重复」，于是同一个词的多个版本全都发了出去。
# 这正是那 3 篇「汇报被领导打断」的由来（refine_loop.py:218 的注释早就记着
# 「这个词前排 0.14，还在这个词上写了 3 篇」，但没人把它接到闸门上）。
#
# ⛔ 常量 MAX_PER_KEYWORD 已于 2026-08-15 拆成下面两个，别再加回来 ——
# 留一个没人用的旧阈值在这，早晚有人照着它改出第三套口径。

# ── 2026-08-15 受控实验：把配额的键从「关键词」换成「关键词 × 角度」（Eric 定）──
#
# 原来的 MAX_PER_KEYWORD=2 拦的是关键词。它背后的数据是可靠的：
#     晋升答辩      1050 → 301
#     绩效被打低分   157 → 107 → 107
#     汇报被打断      62 →  61 →  60      对照单主题稿 156/153/149
# 但要看清这批数据证明的到底是什么 —— 那几篇同词稿**角度也相同**
# （都是「求职者视角·该怎么做」），是在跟自己抢同一个搜索位。
# 所以它证明的是「同词**同角度**」内耗，这个结论保留。
#
# 「同词**不同角度**」会不会内耗，**账号历史上零样本** —— 至今所有稿都是一词一角度，
# 角度的唯一来源是 probe 探出的那一个答案空缺。不能拿上面的数据外推。
#
# 因此开一个受控实验，而不是直接放开：
#   · 同词同角度 ≤2 篇       —— 保留已验证的结论，不动
#   · 同词总量   ≤4 篇       —— 不同角度另开配额，但给总量封顶，避免刷屏
#   · 角度必须在成稿头部显式声明，声明不了的按「未声明」归一 —— 不声明就退化成旧规则
# 发布后拿真实数据对比「同词不同角度」的第 2、3 篇有没有掉量，再决定放开还是收回。
MAX_PER_KEYWORD_ANGLE = 2      # 同词同角度
MAX_PER_KEYWORD_TOTAL = 4      # 同词所有角度合计

ANGLE_RE = re.compile(r"^>?\s*角度[:：]\s*\**\s*([^\n*]+)", re.M)
ANGLE_UNSET = "未声明"

# 「这一栏等于没有红线」的判据。见 gate() 里红线那段的注释：
# 「无」「无。」「无；首图…已逼近红线」「无（待核：…）」都算无；
# 「无法确认…」不算（「无」后面跟的是字，不是标点或结束）。
NO_REDLINE_RE = re.compile(r"^\s*(无|None|-)\s*($|[；;，,。、（(：:])")


def angle_of(name):
    """成稿头部的 `> 角度：面试官视角·他在筛什么`。取不到返回「未声明」。

    ⛔ 取不到时**不能**当成「一个独特的新角度」放行 —— 那会让不写声明成为绕开
    配额的后门。所有未声明的稿共享同一个桶，等于退回旧的「同词 ≤2」规则。
    """
    f = SUCAI / name
    if not f.exists():
        f = SUCAI / "归档稿" / name
    if not f.exists():
        return ANGLE_UNSET
    m = ANGLE_RE.search(f.read_text(encoding="utf-8")[:2000])
    return m.group(1).strip() if m else ANGLE_UNSET


def published_keyword_counts():
    """已发布笔记按「成稿声明的关键词」计数。归档稿也要读 —— 发过的稿多半已归档。"""
    counts = {}
    for r in read_csv(PUB_LOG):
        if not (r.get("发布") or "").startswith("✅"):
            continue
        name = (r.get("成稿文件") or "").strip()
        kw = keyword_of(name)
        if kw:
            counts[kw] = counts.get(kw, 0) + 1
    return counts


def published_angle_counts():
    """已发布笔记按 (关键词, 角度) 计数。返回 {(kw, angle): n}。"""
    counts = {}
    for r in read_csv(PUB_LOG):
        if not (r.get("发布") or "").startswith("✅"):
            continue
        name = (r.get("成稿文件") or "").strip()
        kw = keyword_of(name)
        if kw:
            key = (kw, angle_of(name))
            counts[key] = counts.get(key, 0) + 1
    return counts


def backend_titles():
    """创作后台抓回来的真实已发标题。

    ⛔ 发布日志**不足以**判重：它只记本脚本走过的流程，人工在页面上直接发的、
    以及脚本发了但没来得及回填的，都不在里面。2026-08-05 的 brief 就点名过三篇
    「闸门放行但后台已有同名笔记」—— 半自动时代那只是个提醒，人看一眼就绕开了；
    全自动之后没有人看，闸门放行就等于直接发出去，那三篇会变成三条重复笔记。
    所以判重必须以后台的真实列表为准。
    """
    return {(r.get("标题") or "").strip()
            for r in read_csv(SUCAI / "发布数据.csv") if (r.get("标题") or "").strip()}


def draft_title_of(name):
    """成稿的 H1 是关键词，真正发出去的标题在「## 发布标题」段，必须走 parse_draft。"""
    from case_entry import parse_draft
    for p in (SUCAI / name, SUCAI / "归档稿" / name):
        if p.exists():
            return (parse_draft(p.read_text(encoding="utf-8")).get("title") or "").strip()
    return ""


def gate(name):
    """返回 (是否放行, 理由)。理由无论放行与否都要能解释清楚。"""
    rows = [r for r in read_csv(AUDIT_LOG) if r.get("成稿文件", "").strip() == name]
    trusted = [r for r in rows if (r.get("审核方") or "").strip() in TRUSTED_AUDITORS]
    if not trusted:
        self_only = [r for r in rows if (r.get("审核方") or "").strip() == "自评"]
        return False, "只有自评、无独立审核" if self_only else "无任何审核记录"

    # ⛔ 2026-08-15 换口径：中位分，不是最后一次。（Eric 定）
    #
    # 起因是实测出来的一个硬事实：**对同一份内容完全没变的稿反复跑独立审核，
    # 分数极差中位 11 分、标准差 4.16**。举例（同一个文件，8 次审核）：
    #     成稿_2026-08-08_汇报被打断  [86, 83, 90, 78, 82, 89, 78, 88]
    #     成稿_2026-08-07_汇报被打断  [84,81,82,81,81,81,81,81,86,72,81,83]
    # 22 篇有 ≥3 次审核的稿里，**14 篇的分数范围跨越了 80 分闸门线**。
    #
    # 取 trusted[-1] 等于让「最后一次抽到几分」决定发不发 —— 这是抽签，不是判定。
    # 它还制造了一个假象：「返工三轮反降到 83」看着像改坏了，其实多半只是重抽了一次签，
    # 于是 loop 继续返工、继续抽，8 月 134 次返工里相当一部分耗在这个循环里。
    #
    # 中位数对这种噪声稳健得多。实测切换的净效果：新放行 1 篇、收紧 5 篇 ——
    # 它不是用来增产的（产量瓶颈是机械项存量债，已由 --regress 那条线解决），
    # 是用来让判定不再取决于运气。被收紧的 5 篇中位分本来就不够。
    manual = [r for r in trusted if (r.get("审核方") or "").strip() == "人工放行"]
    if manual:
        return _gate_rest(name, f"人工放行（{manual[-1].get('备注', '')[:30]}）")

    scores = []
    for r in trusted:
        try:
            scores.append(int((r.get("总分") or "").strip()))
        except ValueError:
            pass
    if not scores:
        return False, "审核记录里没有可解析的总分"

    # 红线从严：历史上任何一次判过红线，就得人看一眼，不看中位数。
    #
    # ⛔ 判「无」不能用等值比较。审核员经常写成「无；首图非搜索原句已逼近红线」
    # 「无（待核：两条原话未见于 csv）」—— 这些都是**没有红线**，只是附了一句说明。
    # 按等值比较会把它们全判成有红线，实测误伤 2 篇 82-84 分的稿。
    # 判据：以「无」开头、且紧跟结束或标点。这样「无法确认…」不会被误当成无红线。
    reds = [r for r in trusted if not NO_REDLINE_RE.match((r.get("红线") or "").strip() or "无")]
    if reds:
        return False, (f"历史 {len(trusted)} 次审核里有 {len(reds)} 次判过红线"
                       f"（最近一次「{(reds[-1].get('红线') or '').strip()[:40]}」）"
                       f"—— 红线不看中位数，需人工确认后 --approve")

    med = median(scores)
    if med < PASS_SCORE:
        return False, (f"审核中位分 {med:g} < {PASS_SCORE}"
                       f"（{len(scores)} 次审核 {scores}；已改用中位数，不再看最后一次）")
    return _gate_rest(name, f"审核中位分 {med:g}/{len(scores)} 次 {scores}")


def _gate_rest(name, verdict):
    """中位分/人工放行已判可发之后，剩下的那几道闸（重复、同词、图、机械项）。"""

    if name in published_already():
        return False, "发布日志显示已发布过"

    # 第 3 条闸门的另一半：拿后台真实已发列表再核一遍标题
    t = draft_title_of(name)
    if t and t in backend_titles():
        return False, f"创作后台已有同名笔记「{t}」（发布日志漏记，再发即重复）"

    # 同词限发，两层配额（见 MAX_PER_KEYWORD_ANGLE 上方的完整依据）。
    # 标题查重拦不住这种情况：同一个词的几个版本标题各不相同，逐条看都「不重复」，
    # 合起来就是刷屏 —— 所以按声明的关键词和角度计数，不按标题。
    kw = keyword_of(name)
    if kw:
        angle = angle_of(name)
        same_angle = published_angle_counts().get((kw, angle), 0)
        total = published_keyword_counts().get(kw, 0)
        if same_angle >= MAX_PER_KEYWORD_ANGLE:
            return False, (f"关键词「{kw}」× 角度「{angle}」已发布 {same_angle} 篇"
                           f"（同角度上限 {MAX_PER_KEYWORD_ANGLE}）—— 同词同角度再发是"
                           f"跟自己抢同一个搜索位，实测第 3 篇起量不累积"
                           + ("；本篇未声明角度，未声明的稿共用一个桶，"
                              "要开新角度得在成稿头部写 `> 角度：xxx`"
                              if angle == ANGLE_UNSET else ""))
        if total >= MAX_PER_KEYWORD_TOTAL:
            return False, (f"关键词「{kw}」各角度合计已发布 {total} 篇"
                           f"（同词总量上限 {MAX_PER_KEYWORD_TOTAL}）—— 角度再多也该换词了")

    stem = name.removeprefix("成稿_").removesuffix(".md")
    imgs = sorted((SUCAI / "成品图" / stem).glob("*.png")) if (SUCAI / "成品图" / stem).is_dir() else []

    # ⛔ 2026-08-16：不能只判「有没有图」，要判**张数够不够**。
    # 实测 成稿_2026-08-06_面试不说名字不扣分 的 cards JSON 有 7 张卡片，
    # 成品图目录却只有一张 01_cover.png（case_entry 预览时缓存的封面），
    # 而闸门 `if not imgs` 认为「有图」就放行 —— 发出去会是一篇只有封面、
    # 缺 6 张内页的笔记。这是发布事故，不是瑕疵。
    want = 0
    cards_json = SUCAI / f"图文_{stem}_cards.json"
    if cards_json.exists():
        try:
            data = json.loads(cards_json.read_text(encoding="utf-8"))
            want = len(data) if isinstance(data, list) else len(data.get("cards") or [])
        except (ValueError, OSError):
            want = 0
    if want and len(imgs) < want:
        print(f"   · {name} 成品图只有 {len(imgs)}/{want} 张，重新渲染")
        imgs = []                    # 交给下面那段统一现场渲染

    if not imgs:
        # ⛔ 2026-08-14：缺图不该直接拦下一篇其他条件都合格的稿。
        # 渲染是**纯机械步骤**（headless Chrome 跑 make_cards.py，不花任何 AI 额度），
        # 缺了现场补就是，没有理由让稿子停在这。
        #
        # 为什么会缺：refine_loop 只在**过线时**才渲染（rework_one 结尾「过线就渲染图交闸门」）。
        # 于是没过线的稿从来没有图 —— 2026-08-14 把阈值从 85 降到 80 之后，
        # 返工队列里 6 篇够格的稿**全部**卡在「图 0 张」，一篇都放不出来。
        # 阈值一改，这批稿的图就集体缺席，这个耦合以前没人注意到。
        cards = SUCAI / f"图文_{stem}_cards.json"
        if cards.exists():
            out = SUCAI / "成品图" / stem
            r = subprocess.run([sys.executable, str(CARDS_SCRIPT), str(cards), f"{out}/"],
                               cwd=str(CARDS_SCRIPT.parent), capture_output=True,
                               text=True, timeout=300)
            imgs = sorted(out.glob("*.png")) if out.is_dir() else []
            if imgs and (not want or len(imgs) >= want):
                print(f"   · {name} 缺成品图，已现场渲染 {len(imgs)} 张")
            elif imgs:
                return False, (f"成品图只渲出 {len(imgs)}/{want} 张，缺内页不能发"
                               f"（{(r.stderr or r.stdout)[:60]}）")
            else:
                return False, f"成品图缺失且现场渲染失败：{(r.stderr or r.stdout)[:80]}"
        else:
            return False, "成品图目录没有已渲染的卡片图，且没有卡片 JSON 可渲染"

    # ⛔ 2026-08-13 修：原先取的是整份 stdout 里第一条「- 」开头的行，而 --days 30
    # 一次要检查 80+ 篇，那一行几乎总是**别的稿**的问题。实测本篇真实拦点是
    # 「不是X是Y」3 处，闸门却报「正文节 899 字」（另一篇的），照着这条改稿永远改不对。
    # 改成只跑本篇（--file），拦点就一定是本篇的。
    r = subprocess.run([sys.executable, str(HEALTH_DIR / "draft_check.py"),
                        "--file", name, "--lane", "搜索流"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        first = next((l.strip() for l in r.stdout.splitlines() if l.strip().startswith("-")), "见 draft_check 输出")
        return False, f"机械及格线未过：{first}"

    return True, f"{verdict} · 机械项通过 · {len(imgs)} 张图"


def candidates():
    out = []
    for f in sorted(SUCAI.glob("成稿_*.md")):
        ok, why = gate(f.name)
        out.append((f.name, ok, why))
    return out


def log_run(row):
    exists = PUB_LOG.exists()
    with PUB_LOG.open("a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=PUB_LOG_COLS)
        if not exists:
            w.writeheader()
        w.writerow({c: row.get(c, "") for c in PUB_LOG_COLS})


def keyword_of(name):
    """从成稿正文头部取它写的是哪个词。成稿模板里「关键词来源：`词库.csv`「X」」是确定信息，
    比拿标题去猜可靠。2026-08-05 之前只按标题匹配，而标题策略早已改成「不复读搜索原句」
    （见 commit ee904ce），于是每篇都匹配不上、发布日和笔记链接一路空着没人发现。"""
    f = SUCAI / name
    if not f.exists():
        # 已发布的稿多半已经被挪进 归档稿/。不找这里的话
        # published_keyword_counts() 会永远数出 0，同词限发形同虚设。
        f = SUCAI / "归档稿" / name
    if not f.exists():
        return ""
    head = f.read_text(encoding="utf-8")[:2000]
    m = re.search(r"关键词来源[^「]*「([^」]+)」", head)
    return m.group(1).strip() if m else ""


def backfill_ciku(title, link, name=""):
    """把发布日/笔记链接写回词库。先按成稿声明的关键词精确定位，取不到再退回标题包含匹配。"""
    rows = read_csv(CIKU)
    if not rows:
        return "词库为空"
    cols = list(rows[0].keys())
    hit = None
    declared = keyword_of(name) if name else ""
    if declared:
        hit = next((r for r in rows if (r.get("关键词") or "").strip() == declared), None)
    if hit is None:
        for r in rows:
            kw = (r.get("关键词") or "").strip()
            if kw and (kw in title or title in kw):
                hit = r
                break
    if hit is None:
        hint = f"；成稿声明的词「{declared}」不在词库里" if declared else ""
        return f"词库无匹配关键词（标题「{title}」{hint}），发布日未回填"
    hit["状态"] = "已发布"
    # ⛔ 2026-08-12 修复：这里曾经无条件 hit["发布日"]=today，导致 fetch_stats.py 事后
    # 补链接（backfill_note_links，常常晚于实际发布好几天——16/23 命中率，剩下靠人工）
    # 每次都把发布日重写成「今天」，把发布满 7 天的笔记又打回 0 天。预测复盘对账要求
    # 发布天数>=7，这个 bug 导致词库.csv 里几乎没有笔记能真正攒到 7 天——
    # 这是 review_prediction.py 一直没数据可对账的根因之一（另一根因见 fetch_aged_stats）。
    # 改为只在首次转已发布（发布日为空）时才盖时间戳，之后任何回填都不再覆盖。
    if not (hit.get("发布日") or "").strip():
        hit["发布日"] = date.today().isoformat()
    if link:
        hit["笔记链接"] = link
    # 原子替换：refine_loop 可能正并发读词库选题，直接 open("w") 会有一段
    # 文件被截断、只写了一半的窗口，读者这时读到的是残缺 CSV。
    tmp = CIKU.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    os.replace(tmp, CIKU)
    return f"已回填词库「{hit['关键词']}」" + ("" if link else "（笔记链接待人工补）")


def approve(name):
    rows = read_csv(AUDIT_LOG)
    if not rows:
        print("审核记录为空，无法放行", file=sys.stderr)
        return 1
    cols = list(rows[0].keys())
    prev = [r for r in rows if r.get("成稿文件", "").strip() == name
            and (r.get("审核方") or "").strip() == "独立审核"]
    if not prev:
        print(f"⛔ {name} 没有独立审核记录，不允许人工放行——先跑 independent_audit.py", file=sys.stderr)
        return 1
    base = prev[-1]
    row = {c: "" for c in cols}
    row.update(base)
    row.update({"日期": date.today().isoformat(), "审核方": "人工放行", "处置": "发布",
                "备注": f"人工放行：在独立审核 {base.get('总分')}分（{base.get('评级')}）基础上由人决定发布"})
    with AUDIT_LOG.open("a", encoding="utf-8", newline="") as f:
        csv.DictWriter(f, fieldnames=cols).writerow(row)
    print(f"✅ 已放行 {name}（基于独立审核 {base.get('总分')}分）。下次运行 auto_publish.py 即会发布。")
    return 0


SCHED_SWITCH_JS = (
    '(()=>{const w=document.querySelector(".post-time-wrapper");'
    'if(!w)return "无定时区";'
    'const on=()=>/(^|\\s)checked(\\s|$)/.test(w.querySelector(".d-switch-simulator")?.className||"");'
    'if(on())return "已开";'
    'const s=w.querySelector(".d-switch.d-clickable"),r=s.getBoundingClientRect();'
    's.dispatchEvent(new MouseEvent("click",{bubbles:true,cancelable:true,'
    'clientX:r.left+r.width/2,clientY:r.top+r.height/2,view:window}));'
    'return on()?"已开":"点击未生效";})()')


def open_sched_switch(tid):
    """只打开定时开关，不碰日历。滚动与点击必须分两次调用，否则 rect 还是滚动前的。"""
    import time as _t
    import urllib.request as rq

    def ev(js):
        req = rq.Request(f"{PROXY_BASE}/eval?target={tid}", data=js.encode(), method="POST")
        return json.loads(rq.urlopen(req, timeout=30).read()).get("value")

    ev('(()=>{const s=document.querySelector(".post-time-wrapper .d-switch.d-clickable");'
       'if(s)s.scrollIntoView({block:"center",behavior:"instant"});return 1})()')
    _t.sleep(2)
    ev(SCHED_SWITCH_JS)
    _t.sleep(1.5)
    return ev('(()=>{const w=document.querySelector(".post-time-wrapper");'
              'if(!w)return "无定时区";'
              'const on=/(^|\\s)checked(\\s|$)/.test(w.querySelector(".d-switch-simulator")?.className||"");'
              'return on?("已打开 · "+(w.innerText||"").replace(/\\n/g," ").slice(0,40)):"未打开";})()')


def set_schedule(tid, at, do_publish=False):
    """调 set_schedule.mjs 自动选日期和时分（2026-08-06 起可用）。返回 (成功, 日志)。

    此前这一步是留给人的 10 秒 —— 因为老注释断言 d-datepicker 点不开。实测推翻了：
    最朴素的 CDP mousePressed+mouseReleased 就能打开面板。当初判定失败，多半是在
    .post-time-wrapper 内部找面板，而面板其实挂在 body 下的 .d-popover 里，永远找不到。
    """
    # ⛔ 不能只写 "node"。launchd 给的 PATH 是 /usr/bin:/bin:/usr/sbin:/sbin，
    # 不含 /opt/homebrew/bin —— 2026-08-06 22:00 首次全自动运行就死在这：
    # FileNotFoundError: 'node'，预填完、定时开关也开了，然后整个进程崩掉，
    # 一篇没发、连一行日志都没留下（崩在 log_run 之前）。
    # 和 75cad98 修 /usr/bin/python3 是同一类坑：**launchd 里一切外部命令都要绝对路径**。
    node = shutil.which("node") or next(
        (p for p in ("/opt/homebrew/bin/node", "/usr/local/bin/node") if Path(p).exists()), "")
    if not node:
        return False, "找不到 node 可执行文件（PATH 里没有，常见安装位置也没有）"
    js = Path(__file__).parent / "set_schedule.mjs"
    cmd = [node, str(js), "--at", at, "--target", tid]
    if do_publish:
        cmd.append("--publish")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    return r.returncode == 0, (r.stdout or "") + (r.stderr or "")


PAGE_STATE_JS = (
    'JSON.stringify({'
    'url:location.href,'
    'sched:(document.querySelector(".post-time-wrapper")||{}).innerText||""'
    '})')

# 页面上定时区域的文案形如「定时发布 2026-08-07 09:00」，也见过省掉年份的短格式。
SCHED_FULL_RE = re.compile(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?\s*(\d{1,2}):(\d{2})")
SCHED_SHORT_RE = re.compile(r"(?<!\d)(\d{1,2})[-/月](\d{1,2})日?\s*(\d{1,2}):(\d{2})")


def parse_sched_text(s):
    """从定时区域文案里抠出真正选中的时间。

    ⛔ 必须读页面上的真值，不能拿脚本建议的 next_slot 顶替：发布日志的「定时」列
    喂给 next_slot() 做时段轮换，记错了 7 个时段的对比样本就有偏。读不到就留空，
    宁可空着让人补，也不写一个看起来对的假时间。"""
    m = SCHED_FULL_RE.search(s or "")
    if m:
        y, mo, d, h, mi = (int(x) for x in m.groups())
        return f"{y:04d}-{mo:02d}-{d:02d} {h:02d}:{mi:02d}"
    m = SCHED_SHORT_RE.search(s or "")
    if m:
        mo, d, h, mi = (int(x) for x in m.groups())
        return f"{date.today().year:04d}-{mo:02d}-{d:02d} {h:02d}:{mi:02d}"
    return ""


def watch_until_published(tid, name, timeout=RELAY_TIMEOUT, poll=RELAY_POLL):
    """轮询发布页，等人点完「定时发布」。返回 {state, sched, why}。

    判定只认一个信号：**URL 离开了 /publish/publish**。
    发成功后小红书会跳走（success 页或笔记管理），这是强信号。
    刻意不用「标题输入框变空」当信号 —— 人手动刷新一下页面表单也会变空，
    那会把一篇根本没发的稿记成已发布，比漏判严重得多。

    读不到页面（人把 tab 关了）不算已发布，按 lost 停手。同理：宁可漏，不可错。
    """
    import urllib.request as rq

    deadline = time.time() + timeout
    last_sched, strikes = "", 0
    print(f"\n⏳ 接力模式：等你点完「定时发布」（最多等 {timeout // 60} 分钟，"
          f"每 {poll} 秒看一眼）。别关这个 tab。")
    while time.time() < deadline:
        time.sleep(poll)
        try:
            req = rq.Request(f"{PROXY_BASE}/eval?target={tid}",
                             data=PAGE_STATE_JS.encode(), method="POST")
            st = json.loads(json.loads(rq.urlopen(req, timeout=20).read()).get("value") or "{}")
            strikes = 0
        except Exception as e:
            strikes += 1
            if strikes >= RELAY_LOST_STRIKES:
                return {"state": "lost", "sched": last_sched,
                        "why": f"连续 {strikes} 次读不到发布页（tab 关了或代理挂了）：{e}"}
            continue
        url = st.get("url") or ""
        got = parse_sched_text(st.get("sched"))
        if got:
            last_sched = got
        if "/publish/publish" not in url:
            return {"state": "published", "sched": last_sched,
                    "why": f"页面已离开发布页 → {url[:80]}"}
    return {"state": "timeout", "sched": last_sched,
            "why": f"等了 {timeout // 60} 分钟仍停在发布页，没检测到发布动作"}


def record_published(name, at, how="接力检测"):
    """接力检测到发出去之后记账 + 回填词库。与 --confirm 走同一套账，避免两条路口径不一。"""
    from case_entry import parse_draft
    title = parse_draft((SUCAI / name).read_text(encoding="utf-8"))["title"]
    note = backfill_ciku(title, "", name)
    shown = at or "时间未读到"
    log_run({"日期": datetime.now().strftime("%Y-%m-%d %H:%M"), "成稿文件": name,
             "标题": title, "闸门": "✅ 通过", "预填": "✅", "定时": at,
             "发布": f"✅ {how}已定时 {shown}",
             "备注": note + ("" if at else "；⚠️ 页面没读到定时时间，请手工补「定时」列，"
                             "否则时段轮换会错位")})
    return note


def publish_one(name, dry_run, immediate=False, full_auto=False):
    """batch_offset：同一次运行里第 N 篇往后顺延 N 个时段，避免两篇撞同一个点。

    full_auto=True 时连最后那一下「定时发布」也自己点，全程无人。

    返回 (退出码, tid)。tid 给接力模式用来盯这个 tab；失败或 dry-run 时可能是 None。"""
    from case_entry import prefill_xhs, do_publish_click, parse_draft

    text = (SUCAI / name).read_text(encoding="utf-8")
    title = parse_draft(text)["title"]
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    row = {"日期": stamp, "成稿文件": name, "标题": title, "闸门": "✅ 通过"}

    pre = prefill_xhs(name, archived=False)
    row["预填"] = "✅" if pre.get("ok") else "❌"
    if not pre.get("ok"):
        row["发布"] = "— 未执行"
        row["备注"] = pre.get("log", "")[-160:].replace("\n", "；")
        log_run(row)
        print(f"⛔ 预填失败：\n{pre.get('log')}", file=sys.stderr)
        return 1, None

    print(pre["log"])
    sched_time, hour = next_slot(skip=publish_one.batch_offset)
    publish_one.batch_offset += 0 if immediate else 1
    row["定时"] = "" if immediate else sched_time

    if dry_run:
        row["发布"] = "— dry-run 未点发布"
        row["备注"] = f"预填完成；本次将用的时段是 {sched_time}（轮换池 {SLOT_HOURS}）"
        log_run(row)
        print(f"\n[dry-run] 已预填，未点发布。若继续将定时到 {sched_time}。")
        return 0, pre["tid"]

    if immediate:
        res = do_publish_click(pre["tid"], "now", "")
        row["发布"] = "✅ 已点击立即发布" if res.get("ok") else "❌ 点击失败"
        print(res.get("log", ""))
        row["备注"] = (backfill_ciku(title, "", name) if res.get("ok") else "") or ""
        log_run(row)
        return (0 if res.get("ok") else 1), pre["tid"]

    sw = open_sched_switch(pre["tid"])
    print(f"\n定时开关：{sw}")

    sched_ok, sched_log = set_schedule(pre["tid"], sched_time, do_publish=full_auto)
    print("\n".join("  " + l for l in sched_log.strip().split("\n")))

    # 切到前台，否则最后那一下等于没人能点：cdp-proxy 用 background:true 建 tab，
    # 预填完的页面一直躲在后台，日志写着「已打开创作平台」而人根本看不见
    # （2026-08-05 实测，Eric 找不到页面，最后一步没人点，稿就卡在那）。
    # full_auto 且已点完发布时就不用切了 —— 没人需要看它。
    if not (full_auto and sched_ok):
        focus = subprocess.run([sys.executable, str(Path(__file__).parent / "focus_tab.py"),
                                "--target", pre["tid"]], capture_output=True, text=True)
        print((focus.stdout or focus.stderr or "").strip())

    if full_auto and sched_ok:
        row["发布"] = f"✅ 全自动已定时 {sched_time}"
        row["备注"] = backfill_ciku(title, "", name)
        log_run(row)
        print(f"\n✅ 全自动完成：{name} → {sched_time}")
        return 0, pre["tid"]

    row["发布"] = "⏸ 待人工定时"
    # 失败时把 node 的最后几行塞进备注 —— 全自动模式下没人盯着终端，
    # 日志里只写「失败」等于什么都没说，第二天没人知道是选时间挂了还是点发布挂了。
    tail = " / ".join(l.strip() for l in sched_log.strip().split("\n")[-3:] if l.strip())
    row["备注"] = (f"定时开关：{sw}；自动选时间："
                   + (f"✅ {sched_time}" if sched_ok else f"❌ {tail[:160]}"))
    log_run(row)
    print("─" * 58)
    if sched_ok:
        print(f"  时间已自动选好：{sched_time}"
              f"（轮换池 {SLOT_HOURS}，本次第 {SLOT_HOURS.index(hour)+1} 个）")
        print(f"  你只需在 Chrome 里点一下底部红色「定时发布」按钮。")
    else:
        print(f"  ⚠️ 自动选时间失败，请手工完成：点时间那块 → 选日期 → 选 {hour} 时 / 00 分 → 点定时发布")
        print(f"  建议时段：{sched_time}")
    print("─" * 58)
    return 0, pre["tid"]


def confirm(name, at):
    """人工在页面上点完「定时发布」后调用：记账 + 回填词库。"""
    from case_entry import parse_draft
    f = SUCAI / name
    if not f.exists():
        print(f"找不到 {f}", file=sys.stderr)
        return 1
    title = parse_draft(f.read_text(encoding="utf-8"))["title"]
    note = backfill_ciku(title, "", name)
    log_run({"日期": datetime.now().strftime("%Y-%m-%d %H:%M"), "成稿文件": name,
             "标题": title, "闸门": "✅ 通过", "预填": "✅", "定时": at,
             "发布": f"✅ 人工确认已定时 {at}", "备注": note})
    print(f"✅ 已记账：{name} → {at}")
    print(f"   {note}")
    print("   笔记链接请到创作后台复制后填进 词库.csv 的「笔记链接」列（health_check 会提醒）")
    return 0


publish_one.batch_offset = 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--approve", metavar="FILENAME")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--now", action="store_true", help="立即发布，不走时段轮换")
    ap.add_argument("--slots", action="store_true", help="只看下一个发布时段")
    ap.add_argument("--only", action="append", metavar="FILENAME",
                    help="只处理指定成稿（可重复）。手动发布用：绕过每日配额，但**不绕闸门**")
    ap.add_argument("--confirm", metavar="FILENAME", help="人工点完定时发布后回填")
    ap.add_argument("--at", metavar="TIME", default="", help="配合 --confirm，实际定时时间")
    ap.add_argument("--no-relay", dest="relay", action="store_false",
                    help="退回旧行为：预填一篇就停，不等你点完、也不接力预填下一篇")
    ap.add_argument("--full-auto", action="store_true",
                    help="连最后那下「定时发布」也自己点，全程无人。默认不开 —— "
                         "默认只把时间选好，红色按钮留给你点，人还在回路里")
    args = ap.parse_args()

    if args.approve:
        return approve(args.approve)

    if args.confirm:
        return confirm(args.confirm, args.at or datetime.now().strftime("%Y-%m-%d %H:%M"))

    if args.slots:
        s, h = next_slot()
        print(f"轮换池：{SLOT_HOURS}")
        print(f"下一个时段：{s}（{h} 点）")
        return 0

    cands = candidates()
    passed = [(n, w) for n, ok, w in cands if ok]

    if args.list:
        print(f"{'成稿':<44}{'闸门':<6}理由")
        for n, ok, w in cands:
            print(f"{n[:42]:<44}{'✅' if ok else '⛔':<6}{w}")
        print(f"\n放行 {len(passed)} / {len(cands)} 篇")
        return 0

    if not passed:
        blocked = [f"{n}：{w}" for n, ok, w in cands if not ok][-3:]
        print(f"[{datetime.now():%Y-%m-%d %H:%M}] 无稿通过闸门，不发布。最近 3 篇原因：")
        for b in blocked:
            print("  -", b)
        return 0

    done_today = published_today()
    if args.only:
        # 手动指定：绕过每日配额（是人在决定发几篇），但闸门照走 ——
        # passed 已经是过闸门的列表，指定了没过闸门的篇目会在这里被剔掉并明确报出来。
        want = list(dict.fromkeys(args.only))
        by_name = {n: w for n, w in passed}
        batch = [(n, by_name[n]) for n in want if n in by_name]
        for n in want:
            if n not in by_name:
                why = next((w for nm, ok, w in cands if nm == n), "不在成稿列表里")
                print(f"⛔ 跳过 {n}：{why}")
        if not batch:
            print("指定的稿都没过闸门，不发布。")
            return 1
        print(f"[{datetime.now():%Y-%m-%d %H:%M}] 手动指定 {len(batch)} 篇"
              f"（今日已发 {done_today}，本次不受配额 {DAILY_QUOTA} 限制）\n")
    else:
        quota = DAILY_QUOTA - done_today
        if quota <= 0 and not args.dry_run:
            print(f"[{datetime.now():%Y-%m-%d %H:%M}] 今日已发 {done_today} 篇，达到每日配额 {DAILY_QUOTA}，不再发布。")
            return 0
        batch = passed[:max(quota, 1) if not args.dry_run else 1]
        print(f"[{datetime.now():%Y-%m-%d %H:%M}] 闸门通过 {len(passed)} 篇，"
              f"今日已发 {done_today}/{DAILY_QUOTA}，本次处理 {len(batch)} 篇\n")
    # ⛔ 一次只能有一篇稿躺在发布页上。创作平台的发布页一次只承载一篇，连续预填第二篇
    # 会把第一篇直接覆盖掉 —— 而最后一步（选时段、点发布）要人来点，人还没点稿就没了。
    # 2026-08-03 实测：连预填 3 篇，页面上只剩最后一篇，前两篇白填。
    #
    # 所以多篇不能并行填，只能**接力**：填一篇 → 盯着这个 tab 等它离开发布页 → 再填下一篇。
    # 这就是 --relay（默认开）。有了它，一天跑一次就能走完 DAILY_QUOTA 篇；
    # 2026-08-06 之前只能靠 plist 排三个触发点凑出「一天 3 篇」。
    total = len(batch)
    for i, (name, why) in enumerate(batch, 1):
        print(f"--- [{i}/{total}] {name}\n    {why}")
        rc, tid = publish_one(name, args.dry_run, args.now, args.full_auto)
        if rc != 0:
            return rc
        if i == total:
            break
        if args.now or args.full_auto:
            # 这两种模式下发布已经点完了，没有人工步骤要等，直接下一篇
            time.sleep(10)
            continue
        if args.dry_run or not args.relay or not tid:
            print(f"\n还有 {total - i} 篇在队列里，**发完这篇再跑一次**才会预填下一篇"
                  f"（创作平台一次只放得下一篇）：")
            for n, _ in batch[i:]:
                print(f"  · {n}")
            print(f"  发完后先回填：python3 auto_publish.py --confirm {name} --at \"YYYY-MM-DD HH:MM\"")
            break

        st = watch_until_published(tid, name)
        if st["state"] != "published":
            # 没确认发出去就绝不记 ✅，也绝不预填下一篇（下一篇会盖掉这一篇）
            log_run({"日期": datetime.now().strftime("%Y-%m-%d %H:%M"), "成稿文件": name,
                     "标题": "", "闸门": "✅ 通过", "预填": "✅",
                     "发布": f"⏸ 接力未确认（{st['state']}）", "备注": st["why"]})
            print(f"\n⏸ {st['why']}")
            print(f"   队列里还剩 {total - i} 篇未预填。你若已经发出去了，跑："
                  f"\n   python3 auto_publish.py --confirm \"{name}\" --at \"YYYY-MM-DD HH:MM\"")
            return 0
        note = record_published(name, st["sched"])
        print(f"\n✅ 已发布（{st['sched'] or '时间未读到'}）· {note}")
        print(f"   接力预填下一篇…\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
