#!/usr/bin/env python3
"""成稿机械及格线检查 — 外部的尺（代码硬核对，不靠模型自报）。

对最近 N 篇 成稿_*.md 逐篇检查：
1. 标题 ≤20 字（逐字数，emoji 不计）
2. 正文 100-500 字（2026-08-11 Eric 把下限从 300 改到 100，依据见 BODY_RANGE 注释）
3. CTA/互动段存在（问句结尾或含"评论区/你呢/你遇到过"）
4. AI 味硬指标：「不是X是Y」句式 ≤2 处
5. 跨篇查重：签名句（我面过300人/上周一个候选人 等）近 5 篇内重复即违规
6. 文风守门线：平均句长 ≤30 / 排比 ≤1。指标实现见 style_metrics.py。
   ⛔ 引语密度与具体名词密度两条已于 2026-08-11 删除（Eric 定，理由见下方注释）。

用法：python3 draft_check.py [--days 2]（被 health_check.py 每日调用）
违规 → 打印明细，退出码 1。
"""
import argparse
import re
import sys
from datetime import date, timedelta
from pathlib import Path

from style_metrics import measure

SUCAI = Path(__file__).resolve().parents[2] / "xhs" / "素材库"
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
SIGNATURES = ["我面过300", "上周一个候选人", "笔都没动", "在表上打了叉", "45分钟"]
CTA_HINTS = ["评论区", "你呢", "你遇到过", "你会怎么", "留言", "？\n", "?\n"]

# CTA 选项标记：A/B/C、①②③、1./2./3.。要求正文尾部至少出现 2 个。
# ⛔ 2026-08-08 换口径。原来只查「有没有 CTA」——18 篇稿全都通过，
# 而真实评论率是 0.30%（1992 观看 / 6 条评论）。检查项通过 ≠ 目的达成：
# 那些 CTA 全是「把他当时那句原话发评论区」这种最贵的形态，查得出「有」，
# 查不出「贵」。现在改查**评论成本**：有没有给编号选项。
CTA_OPTION = re.compile(r"(?:^|[\s，,、（(])(?:[ABCD][\s．.、:：]|[①②③④]|[1-4][\s．.、][^\d])")
CTA_TAIL_CHARS = 260          # 只在正文尾部找，中段的「A 还是 B」是埋钩子不是收口
# 「说说你的情况」型的特征词。命中即判贵 —— 这类要读者写一段、还要公开贴原话。
CTA_EXPENSIVE = re.compile(r"(原话|那句话)[^。！？\n]{0,12}(发|贴|留|扔)[^。！？\n]{0,8}(评论|上来)"
                           r"|说说你的|讲讲你的|聊聊你的")
NOT_BUT = re.compile(r"不是[^，。；\n]{1,15}[，,]?[是而]")
# 把读者归进第三方群体的表达。视角跳动整体上机械判不了（要判「他」指的是对方
# 还是读者同类，那是语义），但这类泛指词是其中能机械抓的一半。
# 阈值 3 有数据支撑：8 月以来的稿都 ≤1 个，只有 7 月老稿出现过 3-4 个。
# 定位是异常检测，不是质量评分 —— 完整规则见 必须命中清单.md 第 16 条，由审核员判。
GENERIC_READER = re.compile(r"很多人|有些人|有的人|大部分人|大多数人|不少人|多数人|一些人|大家都")
MAX_GENERIC = 2
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️]")


def extract(text):
    m = re.search(r"^#+\s*(?:首选)?标题[:：]?\s*(.+)$", text, re.M)
    title = m.group(1).strip() if m else ""
    if not title:
        for line in text.splitlines():
            line = line.strip().lstrip("#").strip()
            if line:
                title = line
                break
    return title


# 文风守门线 —— 2026-08-11 Eric 砍掉两条密度类拦截：
#
#   ⛔ 已删除：引语密度 ≥0.8/百字、具体名词密度 ≥2.0/百字
#
# Eric 的原话：「名词密度、评论区原话、用户原话、处境、案例库、对方原话、我的原话、
# 可迁移的那一句等等，这都是些什么狗屁规则，没有一个是和读者有关的，
# 也没有一个是有爆款特征的。」
#
# 这个判断有实测支撑（T13 基准测试）：
#   · 我们按标准自产的对标稿   具体名词密度 1.92 ⛔未过 · 可追溯率 21.3%
#   · 外部爆款（日均 500 赞）  具体名词密度 2.39 ✅通过 · 可追溯率 **0.0%**
#   爆款全篇零引语，密度是靠「10 道题」「覆盖 **90% 以上**」这种**无来源自我承诺**撑起来的。
#   也就是说这两个指标**分不清「真实细节」和「编出来的数字」，而后者更好堆** ——
#   它们惩罚了我们最想要的（可追溯真实原话），奖励了明令禁止的（编造）。
#
# 保留下来的两条与读者体验直接相关，不是文本内部属性：
#   · 平均句长 ≤30 —— 可读性研究：>25 词理解率显著下降（中文约 30-40 字）
#   · 排比 ≤1     —— 决策 5「排比句=活人感杀手第一名」
#
# ⚠️ 「不编造、不冒充」没有被删，但它**不再是密度指标，而是红线**（见 audit skill）：
# 那是合规底线（广告法第二十四条 / 小红书社区规范 4.1.4「无真实体验经历」属违规），
# 不是质量分。素材库（评论区原话.csv / 案例库.csv）也没删 —— 它是**素材供给**，
# 不是规则；写稿仍然要用真实素材，否则就是在编。
MAX_AVG_SENT_LEN = 30
MAX_PARALLEL = 1


def style_issues(text):
    m = measure(text)
    out = []
    if m["平均句长"] > MAX_AVG_SENT_LEN:
        out.append(f"平均句长 {m['平均句长']} 字（>{MAX_AVG_SENT_LEN}，句子过长读不动）")
    if m["排比处数"] > MAX_PARALLEL:
        out.append(f"排比 {m['排比处数']} 处（>{MAX_PARALLEL}，活人感杀手）：{m['_排比实例'][0][:50]}")
    return out


def drafts_sorted():
    files = []
    for f in SUCAI.glob("成稿_*.md"):
        m = DATE_RE.search(f.name)
        if m:
            files.append((date.fromisoformat(m.group(1)), f))
    return sorted(files)


# 正文字数规格：**100-500**（2026-08-11 Eric 定，下限由 300 改为 100）。
#
# 改的依据是 T13 基准测试：32 篇**采自搜索结果页**的高赞笔记，去掉话题标签后
# 正文字数中位只有 **60 字**，27/32 篇低于 300，日均赞前三名（3750/1527/692）
# 的正文分别是 0 / 0 / 69 字。它们确实拿到了搜索位 ——
# 所以「正文要 300 字以上才承载得住关键词、才被搜到」在数据上不成立。
# 旧下限 300 来自 20260731 决策 3（拍板），不是数据。
#
# 上限 560 保留容差（规格 500，卡在 501 字返工不值得）；
# 下限不再加容差 —— 100 本身已经足够低，再放宽就失去意义了。
BODY_RANGE = {"搜索流": (100, 560), "推荐流": (100, 560)}

# 口径标记：成稿头部的 `> 口径：搜索流`。draft_check 与 independent_audit 都靠它切规格 ——
# health_check 批量跑时没法逐篇传参，只能让稿子自己说明。
LANE_RE = re.compile(r"口径[:：]\s*\**\s*(搜索流|推荐流)")


def lane_of(text, override=None):
    if override:
        return override
    m = LANE_RE.search(text)
    return m.group(1) if m else "搜索流"


def check_one(d, f, all_drafts, lane_override=None):
    """单篇机械检查，返回违规条目列表（空 = 通过）。

    all_drafts 是全量成稿（按日期排序），只为跨篇查重用——签名句要跟「这篇之前的 5 篇」比，
    那 5 篇可能落在本次检查窗口之外，所以不能只传窗口内的。
    """
    text = f.read_text(encoding="utf-8")
    body = re.sub(r"```.*?```", "", text, flags=re.S)
    issues = []

    title = extract(text)
    tlen = len(_EMOJI.sub("", title))
    if tlen > 20:
        issues.append(f"标题 {tlen} 字（>20）：「{title[:30]}」")

    lane = lane_of(text, lane_override)
    lo, hi = BODY_RANGE[lane]
    bm = re.search(r"^#{1,3}\s*\*{0,2}正文[^\n]*\n(.*?)(?=\n#{1,3}\s|\Z)", text, re.M | re.S)
    if bm:
        blen = len(re.sub(r"\s|（正文总字数[^）]*）", "", bm.group(1)))
        if not lo <= blen <= hi:
            issues.append(f"正文节 {blen} 字（{lane}规格 100-500，实判 {lo}-{hi}）")
    else:
        clen = len(re.sub(r"\s", "", body))
        if not 300 <= clen <= 2000:
            issues.append(f"全文 {clen} 字且未找到正文节")
    if "五问启动检查" in text:
        issues.append("成稿文件包含「五问启动检查」章节（应只打印不落盘）")
    if "正文总字数" in text:
        issues.append("成稿文件包含「正文总字数」标注行（应只打印不落盘）")

    if not any(h in text for h in CTA_HINTS):
        issues.append("未检出 CTA/互动段（无问句结尾、无评论区引导）")

    # 必须命中清单 第 3 条：CTA 得是「回一个字母/编号」型
    tail = body[-CTA_TAIL_CHARS:]
    if len(CTA_OPTION.findall(tail)) < 2:
        issues.append(
            f"CTA 没给编号选项（正文末 {CTA_TAIL_CHARS} 字内 A/B/C 或 ①②③ 少于 2 个）。"
            "清单第 3 条：结尾要 2–4 个选项，让读者回一个字母就能参与")
    m = CTA_EXPENSIVE.search(body)
    if m:
        issues.append(f"CTA 是「说说你的情况」型（命中「{m.group()}」）—— 清单第 3 条禁止："
                      "要读者写一段、公开贴领导原话，是最贵的一种评论")

    nb = len(NOT_BUT.findall(body))
    if nb > 2:
        issues.append(f"「不是X是Y」句式 {nb} 处（>2，AI 味硬指标）")

    gen = GENERIC_READER.findall(body)
    if len(gen) > MAX_GENERIC:
        issues.append(f"泛指群体词 {len(gen)} 处（>{MAX_GENERIC}）：{'、'.join(gen[:4])}"
                      f" —— 把读者归进第三方群体，视角该对着读者说")

    issues.extend(style_issues(text))

    prev5 = [pf.read_text(encoding="utf-8") for pd, pf in all_drafts if pd < d][-5:]
    for sig in SIGNATURES:
        if sig in text and any(sig in p for p in prev5):
            issues.append(f"签名句「{sig}」近 5 篇内重复使用（模板自我复制）")
    return issues


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--file", metavar="FILENAME",
                    help="只检查指定成稿（refine_loop 单篇复检用）；退出码 2 = 文件不存在")
    ap.add_argument("--lane", choices=["搜索流", "推荐流"],
                    help="覆盖稿内口径标记（默认读成稿头部的「口径：X」，读不到按搜索流）")
    args = ap.parse_args()

    all_drafts = drafts_sorted()
    if args.file:
        recent = [(d, f) for d, f in all_drafts if f.name == args.file]
        if not recent:
            print(f"找不到 {args.file}（需在 素材库/ 下且文件名含 YYYY-MM-DD）", file=sys.stderr)
            return 2
    else:
        cutoff = date.today() - timedelta(days=args.days)
        recent = [(d, f) for d, f in all_drafts if d >= cutoff]
        if not recent:
            print("近期无成稿，跳过机械检查")
            return 0

    problems = []
    for d, f in recent:
        issues = check_one(d, f, all_drafts, args.lane)
        if issues:
            problems.append((f.name, issues))

    if not problems:
        print(f"机械及格线检查通过（{len(recent)} 篇）")
        return 0
    for name, issues in problems:
        print(f"⛔ {name}")
        for i in issues:
            print(f"   - {i}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
