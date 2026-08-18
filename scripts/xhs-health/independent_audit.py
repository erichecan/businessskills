#!/usr/bin/env python3
"""独立审核（headless claude）— 裁判与运动员分离。

找出最近 3 天内没有审核记录的 成稿_*.md，用 headless `claude -p` 按
eric-xhs-audit 标准做第三方审核（只给成稿文本，不给写作过程），
把 CSV 行追加进 审核记录.csv，完整报告存 素材库/审核报告/。

由 com.eric.xhsaudit LaunchAgent 每天 09:05 触发；也可手动运行。
"""
import argparse
import csv
import io
import json
import re
import subprocess
import sys
import time
import unicodedata
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from claude_limits import WEEKLY, classify_limit, limit_banner  # noqa: E402
from headless_cli import build_argv, ensure_cwd  # noqa: E402

BARE_CWD = ensure_cwd()

REPO = Path(__file__).resolve().parents[2]
SUCAI = REPO / "xhs" / "素材库"
AUDIT_LOG = SUCAI / "审核记录.csv"
REPORT_DIR = SUCAI / "审核报告"
CLAUDE = Path.home() / ".local/bin/claude"
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
# 给模型的列（不含审核方——那一列由代码填，模型无权自称是谁）
# 维度列不带分值——口径切换时满分会变
# （搜索流 20/20/15/10/10/15/10，推荐流 20/20/15/15/13/7/10；2026-08-08 两条流都把
#  CTA 提到 10 分，见 eric-xhs-audit/SKILL.md 与 知识框架.md 第十六节）
MODEL_HEADER = ("日期,成稿文件,总分,评级,口径,选题,标题,首图,开头,正文,"
                "可信度,CTA,红线,处置,备注")
AUDITOR_COL_INDEX = 13  # 红线之后、处置之前
HEADER = MODEL_HEADER.replace("红线,处置", "红线,审核方,处置")
DISPOSITION_COL_INDEX = 14  # 插入审核方之后，处置就落在这一列


# ⛔ 过线阈值 —— 必须与 refine_loop.PASS_SCORE 保持同一个数。
# 两处不同步会重演 2026-08-05 那个 bug：评级表说 ≥85 可发，处置列却另有一套，
# 达标的稿被判「未过线」继续返工，越改越低，最后当失败归档。
#
# 2026-08-14 由 85 降到 80（Eric 定）。降之前先查了为什么总卡在 80-84：
# 08-11 之后 63 条独立审核里，**六个维度有五个从没拿过满分**
# （选题最高 19/20、标题 22/25、首图 18/20、开头 13/15、正文 9/10），
# 各维度历史最高加起来正好 91 —— 也就是实际达到过的总分上限。
# ≥85 的只有 10/63＝16%，85 这条线要求六维同时贴近各自天花板，通道太窄。
# 80 分档的 11 篇全部无红线，扣分集中在「正文复述卡片」这一处可定点修复的毛病，
# 稿子本身能看。与其让它们全堵在返工队列（已 47 篇，每天只消化 2 篇），
# 不如放出去拿真实数据 —— 反正 L3 主指标是搜索来源占比，那要发布了才测得到。
PASS_SCORE = 80


def decide_disposition(score, redline, mech_ok=True):
    """处置由代码算，不让模型自己填 —— 和「审核方」「口径」两列同一个道理（D7）。

    ⛔ 这是 2026-08-05 查出的真 bug。原先 prompt 只写「处置用 发布/待人工/归档」，
    没给任何分档规则，模型就自己发挥：`成稿_2026-08-05_干最多的常被刷.md` 第 1 轮
    拿到 **85 分 绿 红线无**（refine_loop 的过线线正好是 85），审核员却写
    「85分擦线故转人工」判了「待人工」。loop 的过线条件是 分数≥85 且 无红线 且
    **处置=发布**，于是这篇明明达标的稿被判未过线、继续返工，第 2 轮掉到 84 分，
    最后当作失败归档。评级表（SKILL.md）写得清清楚楚 ≥85 = 🟢 可发，
    但那张表管的是「评级」列，没人把它接到「处置」列上。

    接上之后 loop 才真的可能过线 —— 在此之前，只要审核员觉得「擦线」，
    这套 loop 在结构上就永远出不了一篇可发的稿。

    ── 分档为什么是这四档（阈值由 45 篇独立审核实测标定，不是拍脑袋）──

    历史分布：中位 81、最高 89、**从没有一篇到过 90**。
      85-89 → 10 篇（全部无红线）    80-84 → 14 篇（全部无红线）
      70-79 → 17 篇（13 篇无红线）   <70  →  4 篇

    每一档回答的是同一个问题：**下一步谁来动手。**
    ⛔ 分档全部由 PASS_SCORE 推导，下面写的是**公式不是字面数字** ——
    2026-08-15 查出这段 docstring 和 prompt 都还停在 85（PASS_SCORE 08-14 已降到 80），
    模型照着 prompt 里的「≥85 发布 / 75-84 返工」校准打分，把稿系统性压在 84 以下，
    这正是「总卡在 80-84」的成因。现在 prompt 不再告诉模型闸门线在哪。
      发布   ≥PASS_SCORE 且无红线且机械项通过 —— auto_publish 直接取。
      机修   ≥PASS_SCORE 且无红线，但机械项不过 —— 只差局部改动，
             走 refine_loop --mech-fix 定点修，**不重写全文、不重新审核**。
             （2026-08-16 从「返工」里拆出来，理由见下方 decide_disposition 内注释）
      返工   PASS_SCORE-10 ~ PASS_SCORE-1 —— 且**全部无红线**，
             扣分多是「首图不是搜索原句」「开头没关键词」这种改一句的事。
             这一档给 loop 自己改，不惊动人 —— 原先它落进「待人工」，
             人不看它就永远停在那，等于把最容易救的一批稿全废了。
      待人工 55 ~ PASS_SCORE-11 —— 短板是结构性的（选题偏、通篇没有原话），
             改一句救不回来，值不值得再投入得由人判断。这一档才配叫「待人工」。
      归档   <55，或红线 + 分数 <70 —— 停手不再消耗。

    红线不再一律归档：红线里像「AI 味强信号≥3 处」是机械可修的，
    分数还在 70 以上就给 loop 一次返工机会。安全性不受影响 ——
    发布仍然要求「≥85 且红线为无」，返工过程中修不掉红线就发不出去。
    """
    clean = (redline or "").strip() in ("", "无", "无。", "None")
    try:
        s = int(str(score).strip())
    except (TypeError, ValueError):
        return "待人工"          # 分数都没解析出来，别替人做决定
    if not clean:
        return "返工" if s >= 70 else "归档"
    # ⛔ 2026-08-14 补：机械项不过就不能判「发布」，哪怕分数够。
    # 不补的话会出现一种没人管的死状态 —— 处置写着「发布」，闸门却因为机械项
    # 把它拦下：它既不在返工队列（rework_queue 只取处置=返工），又永远发不出去。
    #
    # ⛔ 2026-08-16 再修：上面那次判了「返工」，方向对了一半，代价却很大。
    # 「返工」意味着交给 refine_loop，而 refine_loop 干的是 **write_draft 重写全文
    # + 重新跑 independent_audit**。于是一篇「分数够、只差一处机械项」的稿：
    #   ① 内容被整篇重写 —— 明明只需要补 3 行 CTA
    #   ② 重写完重新审 —— 而重复审同一份稿的标准差是 4.16 分，等于重抽一次签
    #   ③ CTA 编号选项这类要求 AI 经常修不掉，于是回到 ①，无限循环
    # 实测代价：成稿_2026-08-07_汇报被打断 被审 12 次
    # （84,81,82,81,81,81,81,81,86,72,81,83 —— 第 9 次 86 分都没能出去），
    # 08-13_汇报被打断 11 次。8 月 134 次返工里最大的一块黑洞就在这。
    #
    # 机械项是**代码能判、且只需局部改**的东西，不该动用「重写+重审」这么重的通道。
    # 单独给它一档「机修」：不进 AI 返工队列，走 refine_loop --mech-fix 定点修，
    # 修完不重审、沿用原分数（内容主体没变，分数不该重抽）。
    if s >= PASS_SCORE:
        return "发布" if mech_ok else "机修"
    if s >= PASS_SCORE - 10:
        return "返工"
    return "待人工" if s >= 55 else "归档"


LIMIT_WAIT = 30 * 60         # 与 refine_loop 同一策略：撞额度每 30 分钟试一次
LIMIT_MAX_WAIT = 5 * 3600    # 5 小时窗口的额度才值得熬这么久


def run_claude_waiting_out_limits(prompt):
    """撞额度就等，不当失败（2026-08-05 Eric 定）。

    审核和写稿在同一个额度池里，写稿那边熬过了额度、轮到审核又立刻撞上并放弃，
    等于前面白等。所以两边必须用同一套退避策略，否则 loop 仍然会在审核这一步断掉。

    ⛔ 「等」只适用于 session limit（2026-08-11 修）。周额度耗尽要等到重置日，
    熬 5 小时是空转 —— 08-10 审核这一路空转了 32 次。判定逻辑与 refine_loop
    共用 claude_limits，避免两边再次跑偏。
    """
    waited = 0
    while True:
        r = subprocess.run(build_argv(CLAUDE, prompt),
                           cwd=str(BARE_CWD), capture_output=True, text=True, timeout=600)
        out = (r.stdout or "").strip()
        kind = classify_limit(out, r.stderr or "")
        if not kind:
            return out
        if kind == WEEKLY:
            print(limit_banner(kind, "审核"), flush=True)
            return ""
        if waited + LIMIT_WAIT > LIMIT_MAX_WAIT:
            print(f"   ⛔ 审核撞额度累计等待 {waited//60} 分钟，超过 {LIMIT_MAX_WAIT//3600} 小时上限，停手")
            return ""
        waited += LIMIT_WAIT
        print(f"   ⏳ 审核撞额度（{kind}），等 {LIMIT_WAIT//60} 分钟后重试"
              f"（已累计 {waited//60}/{LIMIT_MAX_WAIT//60} 分钟）", flush=True)
        time.sleep(LIMIT_WAIT)


def unaudited_drafts():
    """只有「独立审核」行才算审过。

    这里曾是最大的漏洞：任务自评也往 审核记录.csv 写行，一写进去这篇稿就被
    当成已审核而跳过——自评等于抢占了裁判的位置。2026-08-01 那篇自评 87 分判
    「发布」的稿，补做独立审核只有 68 分判「返工」。
    """
    audited = set()
    if AUDIT_LOG.exists():
        for row in csv.DictReader(AUDIT_LOG.open(encoding="utf-8-sig")):
            if (row.get("审核方") or "").strip() != "独立审核":
                continue
            audited.add((row.get("成稿文件") or "").strip())
    cutoff = date.today() - timedelta(days=3)
    out = []
    for f in sorted(SUCAI.glob("成稿_*.md")):
        m = DATE_RE.search(f.name)
        if m and date.fromisoformat(m.group(1)) >= cutoff and f.name not in audited:
            out.append(f)
    return out


def mechanical_result(fname):
    r = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "draft_check.py"), "--days", "3"],
        capture_output=True, text=True,
    )
    lines, keep = [], False
    for line in r.stdout.splitlines():
        if line.startswith("⛔"):
            keep = fname in line
        if keep:
            lines.append(line)
    return "\n".join(lines) or "机械检查通过"


def code_evidence(draft_text: str) -> str:
    """代码先算好、模型不必再算的两块事实（2026-08-12）。

    目的是把「审核员自己去数、去查」换成「代码给结果，审核员只做判断」：
    检索和取数是确定性的，交给代码；类型判断和打分是语义的，留给模型。
    ⚠️ 两块都是**证据不是判决** —— 见 draft_check 里各自的注释，
    尤其第 15 条：查不到 ≠ 编造，正文里的话术模板本来就无从追溯。
    """
    from draft_check import search_slot_evidence, untraceable_quotes

    ev = search_slot_evidence(draft_text)
    if ev["日均赞中位"] is None:
        slot = (f"关键词「{ev['关键词']}」· 词库竞争密度 {ev['竞争密度'] or '未收录'}"
                f" · ⚠️ 该词无 probe 数据，前排强度算不出 —— "
                f"维度 1 的第⑤项（这个搜索位有没有人互动）本篇**无法核验**，"
                f"如实写「无数据」，不要因此扣分也不要假设它没问题")
    else:
        slot = (f"关键词「{ev['关键词']}」· 词库竞争密度 {ev['竞争密度'] or '未收录'}\n"
                f"前排 {ev['样本']} 条笔记的**日均赞中位 = {ev['日均赞中位']}** → {ev['判定']}\n"
                f"（口径：日均赞＝点赞÷发布至采集的天数。⛔ 别和 probe 里 density.median_likes "
                f"那个绝对赞数中位混用，两者差着「笔记活了多少天」）")

    bad = untraceable_quotes(draft_text)
    if not bad:
        quotes = "正文里 ≥8 字的引语**全部**在案例库/评论区原话/probe 中逐字查到。"
    else:
        rows = "\n".join(f"  · {why}：「{q[:50]}」" for q, why in bad)
        quotes = (f"以下 {len(bad)} 句引语在三个库里查不到：\n{rows}\n"
                  f"⛔ **查不到 ≠ 编造，先判它属于哪一类**：\n"
                  f"  ① 转述型（他说／领导说／有人在评论区说）→ 查不到就是红线「编造或冒充」；\n"
                  f"  ② 话术模板（教读者照着说的那种）→ 作者原创，**无从追溯，不构成编造，不要扣分**。\n"
                  f"  这两类正文里都有，代码分不了，所以留给你判。")
    return f"【搜索位强度（维度 1 第⑤项的判据）】\n{slot}\n\n【引语可追溯性检索结果】\n{quotes}"


def _read_or(path: Path, fallback: str) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else fallback


# ── 料包筛选（2026-08-11 加）──────────────────────────────────────────────
# 改之前实测：单次审核 prompt 148,159 字，其中
#   评论区原话.csv 整库 70,805（48%）+ 词库.csv 整库 33,892（23%）
#   + 案例库.csv 整库 26,612（18%）＝ 89%。
# 而审核这一路的成本 84% 就是写缓存（＝prompt 本身）：它是单轮、无工具、
# 纯文本进出的调用，读/写缓存比只有 0.02。08-07~08 两天光审核就烧了 $215。
#
# ⛔ 审核和写稿要的东西不一样，别照搬 refine_loop 的筛法。
# 写稿是**选材**，要广度（跨来源组合，所以它按场景域补满预算）；
# 审核是**核对** —— 只需回答「正文里这句原话，库里有没有、是不是照抄」。
# 所以这里从成稿正文**反查**：库里哪些行被这篇稿引用了，就给哪些行。
#
# 判据是「最长公共子串长度」，用 n-gram 集合求交等价实现（朴素 DP 在
# 451 行 × 2000 字上要跑几十秒，n-gram 是线性的）：
#   ≥6 字连续相同 → 强命中，**照抄**，必须全留，超预算也要留
#   4-5 字        → 弱命中，疑似改写，留着让审核员自己判
# 归一化时去掉标点空白，只留字母数字汉字 —— 库里的「？」和稿里的「?」
# 不该导致匹配失败。
STRONG_N = 6
WEAK_N = 4
QUOTE_BUDGET = 40    # 评论区原话保留行数（整库 330 行）
CASE_BUDGET = 25     # 案例库保留行数（整库 119 行）


def _norm(s: str) -> str:
    """只留字母/数字/汉字。标点、空白、换行都不参与匹配。"""
    return "".join(ch for ch in (s or "") if unicodedata.category(ch)[0] in "LN")


def _ngrams(s: str, n: int) -> set:
    return {s[i:i + n] for i in range(len(s) - n + 1)} if len(s) >= n else set()


def _csv_rows(path: Path):
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _as_block(rows, cols):
    if not rows:
        return "（无相关行）"
    head = ",".join(cols)
    body = "\n".join(",".join((r.get(c) or "").replace("\n", " ") for c in cols) for r in rows)
    return f"{head}\n{body}"


def _pick_by_draft(rows, cols, draft_text, budget, extra_hit=None):
    """按成稿反查：强命中全留（超预算也留）→ 弱命中补 → 补满预算。

    返回 (选中行, 强命中数)。
    """
    nd = _norm(draft_text)
    g_strong, g_weak = _ngrams(nd, STRONG_N), _ngrams(nd, WEAK_N)

    def blob(r):
        return _norm("".join(r.get(c) or "" for c in cols))

    strong, weak, rest = [], [], []
    for r in rows:
        b = blob(r)
        if (extra_hit and extra_hit(r)) or (_ngrams(b, STRONG_N) & g_strong):
            strong.append(r)
        elif _ngrams(b, WEAK_N) & g_weak:
            weak.append(r)
        else:
            rest.append(r)

    # ⛔ 强命中不受预算约束。漏掉一行「稿里照抄了、料包里没有」的原话，
    # 审核员就会按「编造原话」判红线 —— 那是比多花几千 token 严重得多的错误。
    out = list(strong)
    for pool in (weak, rest):
        for r in pool:
            if len(out) >= max(budget, len(strong)):
                break
            out.append(r)
    return out, len(strong)


def relevant_quotes(draft_text: str, kw: str = ""):
    """评论区原话：本稿引用/改写到的那些，不是整库 330 行。"""
    rows = _csv_rows(SUCAI / "评论区原话.csv")
    cols = ["用户原话", "暴露的处境", "候选词"]
    sel, n_strong = _pick_by_draft(
        rows, ["用户原话", "暴露的处境"], draft_text, QUOTE_BUDGET,
        extra_hit=(lambda r: kw and (r.get("候选词") or "").strip() == kw))
    return _as_block(sel, cols), len(sel), len(rows), n_strong


def relevant_cases(draft_text: str, kw: str = ""):
    """案例库：本稿引用到的案例 + 疑似改写的，不是整库 119 行。"""
    rows = _csv_rows(SUCAI / "案例库.csv")
    cols = ["案例ID", "场景", "对方原话", "我的原话", "结果", "可迁移的那一句", "来源"]
    sel, n_strong = _pick_by_draft(
        rows, ["场景", "对方原话", "我的原话", "可迁移的那一句"], draft_text, CASE_BUDGET)
    return _as_block(sel, cols), len(sel), len(rows), n_strong


def relevant_probe_quotes(draft_text: str):
    """本稿引用的 probe 探测结果里的 quotes 块。

    ⛔ 这一块以前根本没喂给审核员，是个真实的误判来源：成稿头部写着
    「素材：`.result.json` 的 quotes 块 4 条」，正文里的原话其实来自探测结果，
    **不在评论区原话.csv 里**。审核员在给定的库里查不到，只能按维度 6
    「原话无法追溯」降级甚至判红线「编造原话」。
    实测 成稿_2026-08-09_试用期没结果.md 对 评论区原话.csv 的强命中数是 0 ——
    它的原话全部来自 probe。单个 result.json 只有约 4KB，喂进来几乎不花钱。
    """
    stems = set(re.findall(r"(probe_\d{8}_[^\s`）)]+?)\.(?:result\.)?json", draft_text))
    blocks = []
    for stem in sorted(stems):
        p = SUCAI / "探测原始" / f"{stem}.result.json"
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        quotes = data.get("quotes") or []
        if not quotes:
            continue
        lines = [f"# {stem}（keyword={data.get('keyword','')}）"]
        for q in quotes:
            lines.append(f"- 用户原话：{(q.get('用户原话') or '').replace(chr(10), ' ')}"
                         f" ｜ 处境：{(q.get('暴露的处境') or '').replace(chr(10), ' ')}")
        blocks.append("\n".join(lines))
    return ("\n\n".join(blocks) if blocks else "（本稿头部未引用任何 probe 探测结果）"), len(stems)


def relevant_ciku(draft_text: str):
    """词库：只给本篇那一行。整库 590 行 33.9k 字，审核只用得到「本词的密度/意图」。

    关键词从成稿头部的「关键词来源：`词库.csv`「XXX」」取；取不到就退回
    用正文反查（长关键词才可能匹配上，短的宁可给空也不要给整库）。
    """
    rows = _csv_rows(SUCAI / "词库.csv")
    cols = ["关键词", "场景域", "场景类型", "意图强度", "竞争密度", "关联案例ID", "状态"]
    m = re.search(r"词库\.csv[`\s]*[「\"']([^「」\"']+)[」\"']", draft_text)
    kw = m.group(1).strip() if m else ""
    hit = [r for r in rows if kw and (r.get("关键词") or "").strip() == kw]
    if not hit:
        nd = _norm(draft_text)
        hit = [r for r in rows
               if (r.get("关键词") or "").strip() and _norm(r["关键词"]) in nd][:3]
    return _as_block(hit, cols), (hit[0].get("关键词") if hit else kw), len(rows)


LANE_HINT = {
    "搜索流": "按 skill 正文的搜索流口径（默认）审核。",
    "推荐流": ("⚠️ 本次按 skill 文末「附录 · 推荐流口径」审核，报告开头须注明「本次按推荐流口径审核」。"
               "差异：标题要留悬念不说答案且张力 6 项命中≥2；首图追求 0.3 秒认知冲突（气质型）而非搜索原句；"
               "开头 15 分（前 3 秒抓手+留悬念）；可信度降为 7 分；主指标看 CES≥8/互动率≥5%；"
               "红线换成「标题把答案说完」，搜索流那三条红线（首图非搜索原句等）本次不适用。"),
}


def build_audit_prompt(draft: Path, lane: str = None):
    """拼审核 prompt。抽出来是为了能在不调模型的前提下测字数（--dry-run）。

    返回 (prompt, lane, stats)。
    """
    from draft_check import lane_of                    # 同一套口径识别，不重复实现
    text_for_lane = draft.read_text(encoding="utf-8")
    lane = lane or lane_of(text_for_lane)
    skill = (REPO / "skills/eric-xhs-audit/SKILL.md").read_text(encoding="utf-8")
    checklist = (SUCAI / "必须命中清单.md").read_text(encoding="utf-8")
    # ⛔ 标杆样本库.md 不再喂入（2026-08-11）。
    # 它取自**推荐流热榜**，而 audit skill 明确规定「搜索流选题不得因『标杆库无同类先例』扣分」。
    # 一边告诉审核员"这份不能用来扣分"、一边把它整篇喂进去，是在赌模型忍得住不用它 ——
    # 而且它的内容只是一张「标题 + 热度」表（2.1KB），对搜索流成稿没有可比性。
    # 但**不能什么都不说**：08-02 踩过的坑是审核员一旦发现某份资产没给，
    # 就在报告里标注「未提供故无法核验」并整体降级。所以这里显式说明为什么不给。
    benchmark_note = (
        "（⛔ 本次**有意不提供**标杆样本库：它取自推荐流热榜，是「标题+热度」表，"
        "与搜索流成稿没有可比性。审核标准已规定不得因「无同类先例」扣分，"
        "故这里不提供也**不构成任何降级理由** —— 维度 1 请改用下方词库与 probe 数据核对。）"
    )
    # headless claude 只看得到 prompt 里的东西。skill 写「审核前先读 X」不够，必须喂进来，
    # 否则审核员只能标注「未提供故无法核验」并降级——2026-08-02 连续踩过两次。
    # 但「喂进来」≠「整库塞」：审核是核对不是选材，按成稿反查即可（见 _pick_by_draft）。
    ciku, ciku_kw, ciku_total = relevant_ciku(text_for_lane)
    cases, cases_kept, cases_total, cases_strong = relevant_cases(text_for_lane)
    quotes_lib, q_kept, q_total, q_strong = relevant_quotes(text_for_lane)
    probe_quotes, probe_n = relevant_probe_quotes(text_for_lane)
    # 首图/七卡内容在单独的 cards.json 里。不喂进来，审核员看不到首图，
    # 只能把维度 3 按未知降级给半分——2026-08-02 三篇稿都栽在这。
    # 2026-08-12 T7 后该维度问的是「第 3 秒手指停不停」，更依赖看到首图本身。
    stem = draft.name.removeprefix("成稿_").removesuffix(".md")
    cards = _read_or(SUCAI / f"图文_{stem}_cards.json", "（本稿无卡片 JSON，首图无法核验）")
    text = draft.read_text(encoding="utf-8")
    prompt = f"""你是独立第三方审核员。只依据下面给出的材料审核这篇小红书成稿，不做任何修改建议之外的事。

【本次审核口径：{lane}】
{LANE_HINT[lane]}

【审核标准 skills/eric-xhs-audit】
{skill}

【必须命中清单】
{checklist}

【关于标杆/先例参照】
{benchmark_note}

⛔ 关于下面三个库：给你的**不是整库，是按本篇正文反查出来的子集**。
筛法：把正文和库里每一行做最长公共子串比对，≥{STRONG_N} 字连续相同的（＝正文照抄了它）
全部保留，4-5 字的（＝疑似改写）也保留，再补若干行凑够额度。所以：
  · 正文里**照抄**的原话，一定在下面这些块里，查不到就是真的没有；
  · 但「下面没有」**不等于「编造」** —— 原话也可能来自探测结果，
    见后面【probe 探测结果 quotes】那一块，核对是否编造时两块都要看。
  · 别因为「只给了子集所以无法核验」而降级 —— 核验所需的行已经在里面了。

──────────────────────────────
以上是每篇都一样的规则（缓存前缀到此为止）。以下是本篇专属的材料。
────────────────────────────────────────────────────

【词库.csv（维度 1 的判据：本词的竞争密度/意图强度。整库 {ciku_total} 行，只给本篇这行）】
{ciku}

【案例库.csv（红线「不编造、不冒充」的核对依据：正文引用的原话能否追溯到某个案例 ID。
整库 {cases_total} 行 → 给 {cases_kept} 行，其中 {cases_strong} 行是正文照抄命中）】
{cases}

【评论区原话.csv（同一条红线的另一来源：原话是否照抄不改写。
整库 {q_total} 行 → 给 {q_kept} 行，其中 {q_strong} 行是正文照抄命中）】
{quotes_lib}

【probe 探测结果 quotes（同一条红线的第三来源，本稿头部引用了 {probe_n} 份探测结果。
⚠️ 正文原话很多来自这里而**不在**评论区原话.csv 里，判「编造」前必须先查这一块）】
{probe_quotes}

【机械检查结果（代码硬核对，以此为准）】
{mechanical_result(draft.name)}

{code_evidence(text)}

【图文卡片 JSON（维度 3「第 3 秒停不停」的核对依据；第 1 张即首图）】
{cards}

【待审成稿 {draft.name}】
{text}

输出要求（严格遵守）：
第 1 行输出且仅输出一行 CSV（不加代码块），列顺序为：
{MODEL_HEADER}
其中 日期={date.today().isoformat()}，成稿文件={draft.name}，口径填 {lane}，
评级用 绿/黄/橙/红，红线用 无 或简述，
处置列**固定填一个减号 `-`**——这一列由代码按分档规则统一改判，你填什么都不影响结果。
⛔ 不要去猜闸门线在哪，也不要为了「让它过」或「稳妥起见压一档」而调整总分。
你唯一的任务是把**总分和红线判准**，那两列才是真正决定处置的输入。
维度列按 skill 的评分卡顺序填分：搜索意图 32 / 标题 35 / 首图 10 / 开头 8 / 正文 10 / CTA 5，
**六项合计必须等于总分，且总分上限 100**。
⛔ CSV 里「可信度」那一列（在 正文 与 CTA 之间）**固定填一个减号 `-`** ——
该维度已于 2026-08-11 撤销（原 15 分），「不编造、不冒充」降为红线只判是/否。
列本身为兼容历史数据保留，**不要给它打分**：给它打分会让六维之和 + 它 > 100，
把发布闸门悄悄冲垮（08-11 前的记录就是七维口径，别照着它们填）。
备注以「独立审核」开头并给一句关键结论（备注内不得含逗号，用分号代替）。
第 2 行起输出完整审核报告（7 维逐项+最高优先级改一句）。"""

    stats = {"关键词": ciku_kw, "词库": f"1/{ciku_total}",
             "案例库": f"{cases_kept}/{cases_total}（强命中 {cases_strong}）",
             "评论区原话": f"{q_kept}/{q_total}（强命中 {q_strong}）",
             "probe": probe_n, "prompt字数": len(prompt)}
    return prompt, lane, stats


def audit_one(draft: Path, lane: str = None) -> bool:
    prompt, lane, stats = build_audit_prompt(draft, lane)
    print(f"   料包：词库 {stats['词库']} · 案例库 {stats['案例库']} · "
          f"原话 {stats['评论区原话']} · probe {stats['probe']} 份 → prompt {stats['prompt字数']:,} 字")
    out = run_claude_waiting_out_limits(prompt)
    first = next((l for l in out.splitlines() if draft.name in l and l.count(",") >= 14), None)
    if not first:
        print(f"⛔ {draft.name}: 未能解析 CSV 行\n{out[:300]}")
        return False
    fields = next(csv.reader(io.StringIO(first.strip())))
    if len(fields) > 4:
        fields[4] = lane      # 口径由代码填，跟审核方一样不让模型自称
    fields.insert(AUDITOR_COL_INDEX, "独立审核")
    if len(fields) > DISPOSITION_COL_INDEX:
        model_said = fields[DISPOSITION_COL_INDEX].strip()
        # 机械项在 build_audit_prompt 里是内联喂给模型的，这里要拿它来定处置，
        # 所以单独取一次（纯代码检查，不花额度）。
        mech = mechanical_result(draft.name)
        decided = decide_disposition(fields[2] if len(fields) > 2 else "",
                                     fields[12] if len(fields) > 12 else "",
                                     mech_ok=mech.strip() == "机械检查通过")
        if decided == "返工" and mech.strip() != "机械检查通过":
            print(f"   机械项未过 → 即使分数达标也不判发布：{mech.splitlines()[-1][:60]}")
        fields[DISPOSITION_COL_INDEX] = decided
        if model_said and model_said != decided:
            print(f"   处置：模型写「{model_said}」→ 按分档规则改判「{decided}」")
    buf = io.StringIO()
    csv.writer(buf, lineterminator="").writerow(fields)
    with AUDIT_LOG.open("a", encoding="utf-8") as f:
        f.write(buf.getvalue() + "\n")
    REPORT_DIR.mkdir(exist_ok=True)
    (REPORT_DIR / f"{draft.stem}_独立审核.md").write_text(out, encoding="utf-8")
    print(f"✅ {draft.name} → {first.split(',')[2]} 分（{first.split(',')[3]}）")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", metavar="FILENAME",
                    help="强制重审指定成稿（返工后复审用；会追加一行新的独立审核记录）")
    ap.add_argument("--lane", choices=["搜索流", "推荐流"],
                    help="覆盖稿内口径标记（默认读成稿头部的「口径：X」，读不到按搜索流）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只拼 prompt 报字数与料包命中，不调模型、不写审核记录")
    args = ap.parse_args()

    if args.dry_run:
        targets = ([SUCAI / args.force] if args.force
                   else unaudited_drafts() or sorted(SUCAI.glob("成稿_*.md"), reverse=True)[:3])
        for t in targets:
            if not t.exists():
                print(f"找不到 {t}", file=sys.stderr)
                return 1
            _, lane, stats = build_audit_prompt(t, args.lane)
            print(f"\n{t.name}（{lane}）")
            for k, v in stats.items():
                print(f"  {k}: {v}")
        return 0

    if args.force:
        target = SUCAI / args.force
        if not target.exists():
            print(f"找不到 {target}", file=sys.stderr)
            return 1
        return 0 if audit_one(target, args.lane) else 1

    drafts = unaudited_drafts()
    if not drafts:
        print("近 3 天成稿均已有审核记录，无需独立审核")
        return 0
    ok = sum(audit_one(d, args.lane) for d in drafts)
    print(f"独立审核完成：{ok}/{len(drafts)}")
    return 0 if ok == len(drafts) else 1


if __name__ == "__main__":
    sys.exit(main())
