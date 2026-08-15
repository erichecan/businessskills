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

# ---------- 清单第 15 条：引语可追溯性（⛔ 证据，不是闸门） ----------
#
# 检索本身是纯字符串操作，不该每次花一次模型调用去做。但**结果不能当红线用**：
#
# 2026-08-12 全量回归（15 篇历史稿）暴露：按「每句引语都必须逐字查得到」判，
# 8 篇被判违规，而它们多数是过了审的。看被判的内容就知道口径错在哪 ——
#   「我确认一下，这个加班是阶段性冲刺，还是长期常态？」
#   「这版取自 BI 日报，口径是当月新客、剔除退款，我发您核对。」
# 这些不是转述别人的话，是**教读者照着说的话术模板**，本账号的核心产出。
# 它们本来就不该在素材库里。
#
# 也就是说正文里的引语有两类：
#   ① 转述型（他说／领导说／有人在评论区说）→ 必须逐字可追溯（合规底线）
#   ② 话术模板（你可以这样回／换成这句）→ 作者原创，无从追溯也不该追溯
# 审核员一直在自动区分这两类，所以规则文本与实际执行的偏差从没暴露过；
# 机械化把它逼了出来。**2026-08-12 Eric 裁定采用这个两类口径**
# （「只要正文里表达的意思是来自案例库和评论区原话就行，我要的是话术模板」），
# 清单第 15 条、audit 红线判定方法、写稿 prompt 的真实性红线均已按此改写。
#
# 结论：代码只负责「查得到查不到」这个事实，**由审核员判它属于哪一类**。
# 这是分工，不是妥协 —— 检索交给代码，类型判断交给模型。
# ⚠️ 想把它升级成硬拦截，得先让规则区分这两类引语，并拿全量历史稿回归到零误判。
BODY_SECTION_RE = re.compile(r"^#{1,3}\s*\*{0,2}正文[^\n]*\n(.*?)(?=\n#{1,3}\s|\Z)", re.M | re.S)


def _body_section(text):
    """只取「## 正文」那一节。引语检索必须限定在这里 —— 头部引言区里本来就会
    出现素材原话（「关键词来源」「素材」那几行），拿它们当正文引语查是自证。"""
    m = BODY_SECTION_RE.search(text)
    return m.group(1) if m else ""


# ── 正文复述卡片（2026-08-14 加）──────────────────────────────────────────
# 这是**最大的单一扣分项**，而且一直只能靠审核员肉眼发现 —— 等发现时一整轮
# claude -p 已经烧掉了。08-11 之后 63 条独立审核里 56% 命中这条；
# 80-84 分那一档 11 篇更集中，9 篇写着同一句话：
#   「正文几乎逐字复述七张卡片 → 完整关键词只出现 1-2 次」
# 两件事其实是一件：正文被卡片内容占满，就没地方承载关键词了。
# 而搜索流的分工写死在成稿模板里 —— **7 张图承载内容，正文承载关键词**。
#
# 判据用「连续 N 字命中」而不是整句相等：复述时人会改标点、调语序、换连接词，
# 整句比对一条都抓不到。N=12 是权衡后的值：太短会误伤（「面试官」「怎么办」这类
# 短语本来就会同时出现在图和正文里），太长又抓不到改写型复述。
# 参数由回测标定，不是拍脑袋：拿 29 篇有独立审核结论的稿做样本，
# 以审核员判没判「复述」为标准答案，扫 N×MAX 的组合（结果见下）：
#     N=8  MAX=2 → 准确 62% 召回 83%（误报 9 篇）
#     N=12 MAX=2 → 准确 59% 召回 72%（误报 9 篇）
#     N=12 MAX=8 → 准确 100% 召回 44%（**误报 0**）   ← 采用
#     N=20 MAX=3 → 准确 100% 召回 33%
# 选零误报那一档：这是**硬拦截**，误报会让一篇没毛病的稿被打回重写、白烧一轮
# claude -p，比漏报贵得多。漏掉的那一半是「改写型复述」（改了措辞语序），
# 机械比对本来就抓不到，继续交给审核员 —— 机械项只负责抓确凿的。
CARD_ECHO_N = 12          # 连续 12 字与某张卡片相同 → 记一处
CARD_ECHO_MAX = 8         # 8 处以上＝通篇搬运。少数几处呼应是正常的


def _card_texts(fname):
    """取同名 cards.json 里每张卡的可读文本。取不到就返回空 —— 没有卡片
    不该判违规（推荐流的稿本来就没有七卡）。"""
    import json as _json
    stem = fname.removeprefix("成稿_").removesuffix(".md")
    p = SUCAI / f"图文_{stem}_cards.json"
    if not p.exists():
        return []
    try:
        cards = _json.loads(p.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError):
        return []
    if isinstance(cards, dict):
        cards = cards.get("cards") or []
    out = []
    for c in cards:
        if not isinstance(c, dict):
            continue
        # body 是卡片正文，最容易被搬进成稿正文；quote/title 也一并看
        txt = " ".join(str(c.get(k) or "") for k in ("title", "quote", "body"))
        out.append(_norm(re.sub(r"<[^>]+>", "", txt)))     # 卡片 body 里有 <br>
    return [t for t in out if t]


def _card_echo(body, cards, keyword=""):
    """正文里有多少处连续 CARD_ECHO_N 字与卡片重合。返回 (处数, 示例)。

    ⛔ 关键词必须先剔掉再比。首图卡要求**逐字等于用户搜索原句**，正文又要求
    承载同一个关键词 —— 两边都出现是设计要求，不是复述。首版没剔，
    结果 4 篇回测里 2 篇报的「复述」示例全是关键词本身（「hr面试薪酬谈判有哪些技」），
    等于拿制度要求的事去判违规。
    """
    nb = _norm(body)
    if keyword:
        nb = nb.replace(_norm(keyword), "")
        cards = [c.replace(_norm(keyword), "") for c in cards]
    if not nb or not cards:
        return 0, ""
    hits, seen, sample = 0, set(), ""
    for i in range(len(nb) - CARD_ECHO_N + 1):
        frag = nb[i:i + CARD_ECHO_N]
        if frag in seen:
            continue
        for c in cards:
            if frag in c:
                hits += 1
                seen.update(nb[j:j + CARD_ECHO_N]
                            for j in range(i, min(i + CARD_ECHO_N, len(nb) - CARD_ECHO_N + 1)))
                if not sample:
                    sample = frag
                break
    return hits, sample


def _keyword_hits(text, body):
    """完整关键词在正文里出现几次。规格是 3-5 次（SKILL.md 维度 1 判据④）。

    与上面那条同源：正文被卡片内容占满 → 关键词挤不进去。18 篇两病并发。
    这一项以前也只有审核员在数，纯机械的事没必要花模型额度。
    """
    m = re.search(r"关键词来源[^「]*「([^」]+)」", text[:2000])
    if not m:
        return None, ""
    kw = m.group(1).strip()
    return _norm(body).count(_norm(kw)), kw


QUOTE_RE = re.compile(r"[「“\"']([^「」“”\"'\n]{6,60})[」”\"']")
QUOTE_MIN = 8          # 短于这个字数的引语不查：6-8 字的口语短句在任何库里都容易撞上
FUZZY_N = 8            # 整句查不到时，用 8 字连续相同判「疑似改写」——比 STRONG_N(6) 严
LIBS = ("案例库.csv", "评论区原话.csv")


def _norm(s):
    """比对前抹掉标点空白：引语落到正文里常被改标点，那不算改内容。"""
    return re.sub(r"[\s，。！？、；：,.!?;:…—－\-~～\"'「」“”‘’（）()【】]", "", s)


def _library_text():
    """三个合法原话来源拼成一锅字符串。probe 的正文与评论也算 —— 清单第 15 条
    明写「不要求来自同一行、同一次采集」，逐字检索的范围就是这三处。"""
    import csv as _csv
    import json as _json
    buf = []
    for name in LIBS:
        p = SUCAI / name
        if not p.exists():
            continue
        with p.open(encoding="utf-8-sig") as f:
            for row in _csv.DictReader(f):
                buf.extend(str(v) for v in row.values() if v)
    probe_dir = SUCAI / "探测原始"
    if probe_dir.is_dir():
        for p in probe_dir.glob("probe_*.json"):
            try:
                d = _json.loads(p.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            buf.extend(c.get("text", "") for c in (d.get("comments") or []))
            buf.extend(b.get("body", "") for b in (d.get("note_bodies") or []))
    return _norm("\n".join(buf))


def _shared_ngram(a, lib_norm, n):
    """a 里有没有任一 n 字连续片段出现在库中。命中即返回那片段，否则 None。

    ⛔ 别改回「找最长公共子串」：那要 O(len² × len(lib)) 次子串搜索，
    库有几十万字，一篇稿几条查不到的引语就能跑掉几十秒。
    判「疑似改写」只需要知道**有没有** n 字连续相同，不需要知道最长是多少。
    """
    for i in range(len(a) - n + 1):
        frag = a[i:i + n]
        if frag in lib_norm:
            return frag
    return None


def untraceable_quotes(text):
    """返回在三个库里查不到的引语列表：[(引语, 判定)]。空 = 全部可追溯。"""
    body = _body_section(text)
    if not body:
        return []
    lib = _library_text()
    if not lib:
        return []                       # 库读不到时不报违规，免得把好稿冤枉了
    out = []
    seen = set()
    for q in QUOTE_RE.findall(body):
        if len(q) < QUOTE_MIN or q in seen:
            continue
        seen.add(q)
        nq = _norm(q)
        if not nq or nq in lib:
            continue                    # 整句逐字查得到 = 通过
        frag = _shared_ngram(nq, lib, FUZZY_N)
        out.append((q, f"疑似改写（库里只对上「{frag}」）" if frag else "库里完全查不到"))
    return out


# ---------- 清单第 8 条：搜索位有据可依 + 前排得有人看（证据，非闸门） ----------
#
# ⛔ 这条**不进 issues、不打回**。关键词在选题阶段就定死了，写手改不动 ——
# 稿子写完才说「这个词没人看」，打回去也只能原地重写一遍同一个词。
# 它的正确位置是选题闸门（pick_topic 的前置过滤），这里只负责把数算出来，
# 交给审核员当维度 1 的判据，并让这个缺口显性化。
#
# 判据口径来自 docs/20260811-五项爆款规律.md：前 20 条笔记的**日均赞中位**
# <1 不写 ／ 1-5 天花板低 ／ 5-20 甜区 ／ >20 红海。
# ⚠️ 注意不是 probe 里存的 density.median_likes —— 那是**绝对赞数**中位（量级 8-1069），
# 两个数差着一个「笔记活了多少天」，别拿来互相印证。
KW_RE = re.compile(r"词库\.csv[`\s]*[「\"']([^「」\"']+)[」\"']")
DAILY_LIKE_TIERS = ((1, "⛔ <1：这个搜索位几乎没人互动，排第一也没用"),
                    (5, "⚠️ 1-5：可写，但天花板不高，别指望量"),
                    (20, "✅ 5-20：甜区"),
                    (float("inf"), "⚠️ >20：红海，需要明确的角度差异"))


def _slug(kw):
    return re.sub(r"[^\w一-龥]+", "", kw)[:20]


def search_slot_evidence(text):
    """算本篇关键词的搜索位强度。返回 dict，取不到数时如实说取不到。"""
    import csv as _csv
    import json as _json
    from datetime import datetime
    from statistics import median

    m = KW_RE.search(text)
    kw = m.group(1).strip() if m else ""
    ev = {"关键词": kw or "（成稿头部未标注关键词来源）", "竞争密度": None,
          "日均赞中位": None, "判定": None, "样本": 0}
    if not kw:
        return ev

    ciku = SUCAI / "词库.csv"
    if ciku.exists():
        with ciku.open(encoding="utf-8-sig") as f:
            for row in _csv.DictReader(f):
                if (row.get("关键词") or "").strip() == kw:
                    ev["竞争密度"] = (row.get("竞争密度") or "").strip() or "（空）"
                    break

    dailies = []
    for p in sorted((SUCAI / "探测原始").glob(f"probe_*_{_slug(kw)}.json")):
        try:
            d = _json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        probed = (d.get("probed_at") or "")[:10]
        try:
            probed_d = datetime.strptime(probed, "%Y-%m-%d").date()
        except ValueError:
            continue
        for n in d.get("top_notes") or []:
            likes, pub = n.get("likes"), n.get("published_at")
            if likes is None or not pub:
                continue
            try:
                days = (probed_d - datetime.strptime(pub, "%Y-%m-%d").date()).days
            except ValueError:
                continue
            dailies.append(likes / max(days, 1))
    if dailies:
        med = median(dailies)
        ev["日均赞中位"] = round(med, 2)
        ev["样本"] = len(dailies)
        ev["判定"] = next(label for ceiling, label in DAILY_LIKE_TIERS if med < ceiling)
    return ev


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
    body_sec = _body_section(text)
    if body_sec:
        blen = len(re.sub(r"\s|（正文总字数[^）]*）", "", body_sec))
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
    #
    # ⛔ 2026-08-12 修：这里原本取 body[-260:]，而 body 是**去掉代码块的全文**
    # （正文节是 body_sec，见上面几行）—— 变量名骗了人，报错文案也一直写着
    # 「正文末 260 字」，实际量的是文件末 260 字。而成稿正文后面还有
    # 「## 图文卡片」「## 话题标签」「## 处置」三节元信息，稳定占掉 200+ 字，
    # 于是真正的 CTA 每次都被挤出窗口。
    # 实测这篇：正文节里有 10 个 A/B/C 标记、正文节尾窗口命中 8 个，
    # 按全文尾窗口只命中 1 个 → 判违规。今天三篇稿（2 篇 Claude + 1 篇 Gemini）全中此坑。
    # 判据没变（仍是「结尾要 2–4 个选项」），只是把窗口挪到该量的地方。
    cta_src = body_sec or body
    tail = cta_src[-CTA_TAIL_CHARS:]
    if len(CTA_OPTION.findall(tail)) < 2:
        issues.append(
            f"CTA 没给编号选项（正文末 {CTA_TAIL_CHARS} 字内 A/B/C 或 ①②③ 少于 2 个）。"
            "清单第 3 条：结尾要 2–4 个选项，让读者回一个字母就能参与")
    m = CTA_EXPENSIVE.search(cta_src)
    if m:
        issues.append(f"CTA 是「说说你的情况」型（命中「{m.group()}」）—— 清单第 3 条禁止："
                      "要读者写一段、公开贴领导原话，是最贵的一种评论")

    # ⛔ 2026-08-13 修：与上面 CTA 窗口同型的坑，2026-08-12 只修了 CTA 一条，这两条漏网。
    # 这两个指标量的是「读者读到的文字里有多少 AI 味」，但 body 是**去掉代码块的全文**，
    # 于是三样根本不会被读者看到的东西也被算了进去：
    #   ① 备选标题（「评委扣分的不是内容是这个」—— 备选压根不会发出去）
    #   ② 「## 处置」里的打分说明、strongest_moment 引用（引的还是正文原句，重复计数）
    #   ③ 「机械自检」那一行本身 —— 它字面写着「「不是X是Y」句式 1 处」，自己命中自己
    # 实测《答辩被打断，评委问的根本不是项目本身》：正文节 1 处（合格），
    # 按全文算 3 处 → 被判违规、卡在发布闸门外。判据没变，只把窗口挪到该量的地方。
    prose = body_sec or body

    # 正文复述卡片 + 关键词密度（2026-08-14 加，见文件上方 CARD_ECHO_N 处的注释）
    if body_sec:
        hits, kw = _keyword_hits(text, body_sec)
        echo, sample = _card_echo(body_sec, _card_texts(f.name), kw)
        if echo > CARD_ECHO_MAX:
            issues.append(
                f"正文复述卡片 {echo} 处（>{CARD_ECHO_MAX}）：如「{sample}」——"
                f"搜索流的分工是 7 张图承载内容、正文承载关键词，"
                f"把卡片抄进正文等于两头落空")
        # ⛔ 硬拦只到「少于 2 次」，不照搬 SKILL.md 的 3-5 次规格。
        # 首版直接拿 3-5 当机械门槛，当场把返工链路卡死：2026-08-15 实测一轮返工
        # 三次尝试全被这条拦下（1 次 → 未过；第 2 轮 77 分；第 3 轮做到 2 次 → 仍未过），
        # 三次 claude -p 全废，稿子回滚归档。
        #
        # 错在混淆了两种判据的性质：3-5 次是**审核维度里的扣分项**（差一点扣一点分），
        # 而机械项是**硬拦截**（不过就发不出去）。把软性规格升级成硬门槛，
        # 等于给链路加了一道谁都过不去的闸。何况正文只有 100-500 字，
        # 一个 10 字的词塞 3 次占掉 30 字，读起来就是关键词堆砌 —— 又会撞上 AI 味那几条。
        #
        # 机械项只管确凿的那一端：0-1 次＝正文根本没承载关键词，搜索流的正文职能落空。
        # 「2 次还是 3 次」这种程度问题留给审核员按维度扣分。
        if hits is not None and hits < 2:
            issues.append(f"完整关键词「{kw}」在正文只出现 {hits} 次 —— "
                          f"搜索流的正文职能就是承载关键词（审核规格 3-5 次，"
                          f"这里只硬拦 <2 次）")

    nb = len(NOT_BUT.findall(prose))
    if nb > 2:
        issues.append(f"「不是X是Y」句式 {nb} 处（>2，AI 味硬指标）")

    gen = GENERIC_READER.findall(prose)
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
