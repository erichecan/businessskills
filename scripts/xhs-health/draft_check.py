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
7. 标题跨篇相似度：与已发布/已产出的其它标题相似度 ≥30% 即违规（Eric 2026-09-05 定）。

用法：python3 draft_check.py [--days 2]（被 health_check.py 每日调用）
违规 → 打印明细，退出码 1。
"""
import argparse
import csv
import re
import sys
from datetime import date, timedelta
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

from style_metrics import measure

SUCAI = Path(__file__).resolve().parents[2] / "xhs" / "素材库"
PUB_LOG = SUCAI / "发布日志.csv"
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


# ---------- 清单第 7 条：标题跨篇相似度（Eric 2026-09-05 定） ----------
#
# 起因：Eric 肉眼扫发布日志，标题重复度已到 85% 以上。抽样实测印证了这个感觉——
# 发布日志里「社保对不上，HR不在面试时查你」vs「社保和简历对不上，HR不在面试那天查」
# 相似度 0.77，「试用期过了不给转正，他在等你先开口」vs「试用期过了没谈转正，
# 他不在等你表现」相似度 0.69，同一批稿子只是换了几个字重复投。
# 同一样本里真正不相关的两条标题相似度只有 0.13～0.16，卡在 0.30 中间没有误伤空间。
#
# 判定用「字符 Jaccard 与 difflib 序列比取较大值」，跟 scripts/xhs-loop/
# cross_section_report.py 里验证过的 title_similarity() 是同一套算法（那边是给
# 「文件↔发布记录」做强匹配，阈值 0.95 只收精确复述；这里是防真重复，用途不同，
# 阈值也不同，各自独立配置，不共享判据）。
TITLE_DUP_THRESHOLD = 0.30


def _title_norm(s: str) -> str:
    return re.sub(r"[^\w一-鿿]", "", s or "").lower()


def _char_jaccard(a: str, b: str) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def title_similarity(a: str, b: str) -> float:
    na, nb = _title_norm(a), _title_norm(b)
    if not na or not nb:
        return 0.0
    return max(_char_jaccard(na, nb), SequenceMatcher(None, na, nb).ratio())


def _slug_of_draft_filename(fname: str) -> str:
    """成稿_2026-08-19_试用期没拿到结果.md → 试用期没拿到结果。

    同一 slug 在不同日期出现是同一篇稿子的定向返工（rework_one 明确「只改点到的
    问题，其余保持原样」），标题理应不变——不能拿它跟自己比对，那不是真重复。
    """
    m = DATE_RE.search(fname)
    if not m:
        return fname
    prefix = f"成稿_{m.group(1)}_"
    return fname[len(prefix):-3] if fname.startswith(prefix) and fname.endswith(".md") else fname


@lru_cache(maxsize=1)
def _published_titles() -> tuple:
    """(slug, 标题) —— slug 取自「成稿文件」列，跟 _other_draft_titles 用同一套键，
    这样同一篇稿子发布后再被 health_check 扫到，不会拿自己的发布记录跟自己比对。"""
    if not PUB_LOG.exists():
        return ()
    out = []
    with PUB_LOG.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            title = (r.get("标题") or "").strip()
            if not title:
                continue
            out.append((_slug_of_draft_filename((r.get("成稿文件") or "").strip()), title))
    return tuple(out)


@lru_cache(maxsize=1)
def _other_draft_titles() -> tuple:
    """(slug, 标题) —— 素材库根目录 + 归档稿 全量，供跨稿比对。"""
    out = []
    for f in list(SUCAI.glob("成稿_*.md")) + list((SUCAI / "归档稿").glob("成稿_*.md")):
        title = extract(f.read_text(encoding="utf-8"))
        if title:
            out.append((_slug_of_draft_filename(f.name), title))
    return tuple(out)


def title_dup_issue(fname: str, title: str) -> str:
    """标题跟已发布 / 其它选题的稿子撞了没有。返回违规文案，没撞返回空串。"""
    if not title:
        return ""
    cur_slug = _slug_of_draft_filename(fname)
    pool = [t for slug, t in _published_titles() if slug != cur_slug]
    pool += [t for slug, t in _other_draft_titles() if slug != cur_slug]
    best_score, best_title = 0.0, ""
    for t in pool:
        s = title_similarity(title, t)
        if s > best_score:
            best_score, best_title = s, t
    if best_score >= TITLE_DUP_THRESHOLD:
        return (f"标题与已产出的「{best_title}」相似度 {best_score*100:.0f}%"
                f"（≥{int(TITLE_DUP_THRESHOLD*100)}% 上限）—— 换关键词角度或换表达，"
                f"不能只改几个字")
    return ""


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
BODY_RANGE = {"搜索流": (100, 560), "推荐流": (100, 560),
              # 祝福流是短内容：一句祝福 + 一个锦囊，撑到 500 字就注水了。
              # 上限同样带容差（规格 400 / 实判 440）—— 卡在 434 字返工一轮不值得，
              # 这和搜索流 500→560 是同一个理由。
              "祝福流": (80, 440)}

# 给模型看的规格（不含容差）。⛔ 别再把它写死在报错文案里：
# 旧文案硬编码「规格 100-500」，于是祝福流稿被报成「祝福流规格 100-500，实判 80-400」，
# 两个数互相打架，看的人不知道该信哪个。
SPEC_WORDS = {"搜索流": "100-500", "推荐流": "100-500", "祝福流": "80-400"}

# 口径标记：成稿头部的 `> 口径：搜索流`。draft_check 与 independent_audit 都靠它切规格 ——
# health_check 批量跑时没法逐篇传参，只能让稿子自己说明。
LANE_RE = re.compile(r"口径[:：]\s*\**\s*(搜索流|推荐流|祝福流)")


def lane_of(text, override=None):
    if override:
        return override
    m = LANE_RE.search(text)
    return m.group(1) if m else "搜索流"


# ── 七种力规则（2026-08-18 加，见 20260818-选题拓宽与评论自动化-tasks.md G2）──
#
# ⛔ 有日期门，且这是本条最重要的设计。
# 机械项每收紧一次就会静默废掉一批存量稿 —— 08-15 那次「积压 55 篇达标稿发不出去」
# 就是这么来的（换 CTA 口径没做存量迁移）。现在库存 108 篇、闸门刚放行 16 篇，
# 拿一条今天才定的规则去追溯判它们，等于把库存清零。
# 存量稿写的时候没有这条要求，判它们不公平也无意义。
CONCEPT_RULES_FROM = date(2026, 8, 19)

# 近义词/意译：把概念名的前缀接上「能力/本事/技巧…」就是最典型的跑偏写法。
# 「示弱力」→「示弱的能力」读起来没差，但读者记不住一个每次换个说法的东西，
# 而记住概念正是这套术语库存在的全部理由。
_NEAR_MISS = re.compile(r"(结构|说服|示弱|边界|反馈|化解|破冰)\s*(?:的)?\s*(能力|本事|技巧|水平|力量)")
# 「结尾」的口径 = **正文后半部分**，不是物理末尾的 N 字。
# ⛔ 别改回「末 150 字」：那块地方被 CTA 占着。必须命中清单第 3 条要求 CTA 是
# 「回一个字母/编号」型（依据是评论率 0.30% —— 让读者写一段话的成本太高），
# 于是正文最后一定是「你是哪一种？回个字母：A…B…C…」。
# 两条规则争同一块空间时，让有数据支撑的那条赢。实测：新稿正文里确实点了「结构力」，
# 只是被 CTA 挤到了 150 字窗口之外 —— 判它不合格是规则自己的问题。
_TAIL_RATIO = 0.5          # 概念名必须出现在正文后 50% 内
_MAX_CONCEPTS = 2


def _concept_names():
    import json
    d = json.loads((SUCAI / "概念术语库.json").read_text(encoding="utf-8"))
    return [c["name"] for c in d["concepts"]]


def concept_issues(text, d):
    """七种力三条硬规则。只对 CONCEPT_RULES_FROM 起的新稿生效。"""
    if d < CONCEPT_RULES_FROM:
        return []
    names = _concept_names()
    body = _body_section(text) or text
    issues = []

    for m in set(_NEAR_MISS.findall(body)):
        issues.append(f"概念措辞跑偏：「{m[0]}{m[1]}」应写作「{m[0]}力」"
                      f"（术语库措辞不许意译改写）")

    hit = [n for n in names if n in body]
    if len(hit) > _MAX_CONCEPTS:
        issues.append(f"涉及 {len(hit)} 个概念（>{_MAX_CONCEPTS}）：{'、'.join(hit)}"
                      f" —— 记忆点分散，聚焦到 1-2 个")

    flat = re.sub(r"\s", "", body)
    tail = flat[int(len(flat) * (1 - _TAIL_RATIO)):]
    if not any(n in tail for n in names):
        where = "全篇都没提" if not hit else f"只在前半段提了{'、'.join(hit)}"
        issues.append(f"正文后半段没有显性点名概念（{where}）"
                      f" —— 句式参考「这背后其实是____的问题」")
    return issues


# ── 下面两条只统计不拦（2026-08-18）──────────────────────────────────────
#
# 四段式和「结果是否编造」都判不准：前者要认出「诊断」「解法」是不是真的分开了，
# 后者要区分「转述真实反馈」和「编一个对方反应」。拿正则去判，误报率下不来。
# 台账 G2 的验收写着「误报率 0 才能上闸门」，所以先跑报告、看触发分布，
# 确认判据站得住再挪进 concept_issues。
# ⛔ 别因为「先上着看看」就把它们直接加进 issues —— 一条误报就是一篇达标稿发不出去。
_QUOTE_MARK = re.compile(r"(评论区有人|有人在评论区|读者(说|留言)|私信(里|说))")
_RESULT_REAL = re.compile(r"(后来|结果)(他|她|对方|领导|hr|HR)")
_RESULT_INFER = re.compile(r"(大概率|多半会|通常会|一般会).{0,40}(因为|原因是)")


def concept_report(text):
    body = _body_section(text) or text
    quoted = bool(_QUOTE_MARK.search(body))
    return {
        "引用了读者原话": quoted,
        "写了对方后续反应": bool(_RESULT_REAL.search(body)),
        "标了推理型结果": bool(_RESULT_INFER.search(body)),
    }


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
    dup = title_dup_issue(f.name, title)
    if dup:
        issues.append(dup)

    lane = lane_of(text, lane_override)
    lo, hi = BODY_RANGE[lane]
    body_sec = _body_section(text)
    if body_sec:
        blen = len(re.sub(r"\s|（正文总字数[^）]*）", "", body_sec))
        if not lo <= blen <= hi:
            issues.append(f"正文节 {blen} 字（{lane}规格 {SPEC_WORDS[lane]}，实判 {lo}-{hi}）")
    else:
        # ⛔ 2026-08-18 改成硬报错。旧写法是「找不到正文节时退回 300-2000 兜底」，
        # 于是「结构缺一整节」这件事在字数正常时**静默通过**，而且各 lane 的字数规格
        # 一起失效 —— 祝福流那篇 400 字的稿被按 300-2000 判，规格 80-400 完全没生效。
        # 搜索流稿一直都有正文节，所以这个洞一直没暴露，是新 lane 把它顶出来的。
        # 影响面实测：108 篇历史稿只有 1 篇（成稿_2026-07-02_面试肢体语言.md）会新触发。
        clen = len(re.sub(r"\s", "", body))
        issues.append(f"找不到「## 正文」节（全文 {clen} 字）—— 结构不完整，"
                      f"{lane}的字数规格 {lo}-{hi} 无从核对")
    if "五问启动检查" in text:
        issues.append("成稿文件包含「五问启动检查」章节（应只打印不落盘）")
    if "正文总字数" in text:
        issues.append("成稿文件包含「正文总字数」标注行（应只打印不落盘）")

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
    #
    # ⛔ 2026-08-15：旧的 CTA_HINTS 检查（「未检出 CTA/互动段」，要正文里有
    # 「评论区」字样或问号结尾）原本单独挂在这条前面，两条判据打架 ——
    # 08-08 换成编号口径之后，一个完全合格的编号 CTA（「A xxx／B xxx／C xxx」）
    # 只要没写「评论区」三个字，就会被旧检查判「未检出 CTA」。
    # 实测修存量稿时三篇全中：新 CTA 过了新口径、栽在旧口径上。
    # 旧口径是被取代的那一个，降级为 fallback：编号选项够了就不必再问它。
    # 保留它是因为两条并不完全冗余 —— 正文里列「A方案 B方案」也会命中 CTA_OPTION，
    # 这时旧口径能兜住「这段到底是不是在跟读者要互动」。
    cta_src = body_sec or body
    tail = cta_src[-CTA_TAIL_CHARS:]
    if len(CTA_OPTION.findall(tail)) < 2:
        issues.append(
            f"CTA 没给编号选项（正文末 {CTA_TAIL_CHARS} 字内 A/B/C 或 ①②③ 少于 2 个）。"
            "清单第 3 条：结尾要 2–4 个选项，让读者回一个字母就能参与")
        if not any(h in text for h in CTA_HINTS):
            issues.append("且未检出任何 CTA/互动段（无问句结尾、无评论区引导）")
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

    # ⛔ 2026-08-15 删除「正文复述卡片」与「关键词次数」两项（Eric 定）。
    #
    # 它们只活了一天。加的时候理由看着很硬 —— 08-11 之后 63 条独立审核里 56% 扣在
    # 「正文复述卡片」，是最大的单一扣分项。但 Eric 问了一句「为什么非要卡着不放」，
    # 回头去查依据，发现整条规则建立在一个从没验证过的前提上：
    # 「搜索流分工＝7 张图承载内容 + 正文承载关键词」出自 07-31 的拍板，不是数据。
    #
    # 拿账号自己 23 篇已发布笔记算相关系数（对观看量）：
    #     复述卡片处数   r = -0.040   几乎无关
    #     关键词出现次数 r = -0.148   几乎无关
    #     正文字数       r = +0.097   几乎无关
    # 观看最高那篇（1050，全账号第一）复述 6 处、关键词 **0 次**。
    # 再对外部基准：32 篇搜索位高赞笔记正文中位 **60 字**，
    # 日均赞前三名的正文是 **0 / 0 / 69 字** —— 爆款根本不写正文。
    #
    # 所以这两条既没有数据支持，代价还很实：2026-08-15 一轮返工三次尝试全被
    # 关键词那条拦下（1 次→未过、第 2 轮 77 分、第 3 轮 2 次→仍未过），
    # 三次 claude -p 全废、稿子回滚归档。删掉。
    # 省下的注意力放到标题和选词上 —— 那两项是有数据的（547 条 / 360 倍差异）。

    nb = len(NOT_BUT.findall(prose))
    if nb > 2:
        issues.append(f"「不是X是Y」句式 {nb} 处（>2，AI 味硬指标）")

    gen = GENERIC_READER.findall(prose)
    if len(gen) > MAX_GENERIC:
        issues.append(f"泛指群体词 {len(gen)} 处（>{MAX_GENERIC}）：{'、'.join(gen[:4])}"
                      f" —— 把读者归进第三方群体，视角该对着读者说")

    issues.extend(style_issues(text))

    issues.extend(concept_issues(text, d))

    prev5 = [pf.read_text(encoding="utf-8") for pd, pf in all_drafts if pd < d][-5:]
    for sig in SIGNATURES:
        if sig in text and any(sig in p for p in prev5):
            issues.append(f"签名句「{sig}」近 5 篇内重复使用（模板自我复制）")
    return issues


def regress() -> int:
    """全量回归：审核判过「发布」、但按**当前**机械规则不过的稿。

    ⛔ 这个命令的存在理由（2026-08-15）：机械项每收紧一次，就会静默废掉一批存量稿。
    CTA 判据 08-08 换口径（从「有没有 CTA」改成「有没有编号选项」）之后，
    08-02~08-08 写的 17 篇**当时合格、独立审核判发布**的稿集体变成不合格，
    而且掉进了没人管的夹缝：rework_queue 只取处置=返工，它们是「发布」；
    闸门又因机械项拦下。就这么烂了一周，攒到 55 篇曾达标却没发出去的稿。

    所以规则一改就要跑一次这个，把存量债显性化 —— 要么补修，要么明确归档，
    不能让它们无声地躺着。
    """
    import csv as _csv
    audit_log = SUCAI / "审核记录.csv"
    pub_log = SUCAI / "发布日志.csv"
    if not audit_log.exists():
        print("没有审核记录，跳过")
        return 0
    published = set()
    if pub_log.exists():
        with pub_log.open(encoding="utf-8-sig") as f:
            published = {(r.get("成稿文件") or "").strip() for r in _csv.DictReader(f)
                         if (r.get("发布") or "").startswith("✅")}
    latest = {}
    with audit_log.open(encoding="utf-8-sig") as f:
        for r in _csv.DictReader(f):
            if (r.get("审核方") or "").strip() == "独立审核":
                latest[(r.get("成稿文件") or "").strip()] = r

    all_drafts = drafts_sorted()
    index = {f.name: (d, f) for d, f in all_drafts}
    for f in (SUCAI / "归档稿").glob("成稿_*.md"):
        m = DATE_RE.search(f.name)
        if m and f.name not in index:
            index[f.name] = (date.fromisoformat(m.group(1)), f)

    stuck = []
    for name, row in latest.items():
        if name in published or (row.get("处置") or "").strip() != "发布":
            continue
        got = index.get(name)
        if not got:
            stuck.append((name, ["成稿文件已不在 素材库/ 或 归档稿/"]))
            continue
        d, path = got
        issues = check_one(d, path, all_drafts)
        if issues:
            stuck.append((name, issues))

    if not stuck:
        print("✅ 全量回归通过：没有「审核判发布但机械项不过」的存量稿")
        return 0
    print(f"⛔ {len(stuck)} 篇审核判「发布」的稿卡在当前机械规则上 —— "
          f"这是规则变更留下的存量债，要么补修要么明确归档：\n")
    for name, issues in stuck:
        print(f"  {name}")
        for i in issues:
            print(f"     - {i}")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--regress", action="store_true",
                    help="全量回归：列出「审核判发布但按当前机械规则不过」的存量稿")
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--file", metavar="FILENAME",
                    help="只检查指定成稿（refine_loop 单篇复检用）；退出码 2 = 文件不存在")
    ap.add_argument("--lane", choices=["搜索流", "推荐流", "祝福流"],
                    help="覆盖稿内口径标记（默认读成稿头部的「口径：X」，读不到按搜索流）")
    args = ap.parse_args()

    if args.regress:
        return regress()

    all_drafts = drafts_sorted()
    if args.file:
        recent = [(d, f) for d, f in all_drafts if f.name == args.file]
        # 归档稿里的也要能查 —— 重分类和存量回归都要对已 park 的稿判机械项，
        # 只扫根目录的话它们永远「找不到」，于是被当成无法判定而漏掉。
        if not recent:
            p = SUCAI / "归档稿" / args.file
            m = DATE_RE.search(args.file)
            if p.exists() and m:
                recent = [(date.fromisoformat(m.group(1)), p)]
        if not recent:
            print(f"找不到 {args.file}（素材库/ 与 归档稿/ 下均无，且文件名需含 YYYY-MM-DD）",
                  file=sys.stderr)
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
