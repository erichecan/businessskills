#!/usr/bin/env python3
"""搜索位空缺探测器 · 第 1 层：客观提取，不做判断。

给一个候选长尾词，通过 web-access CDP Proxy 采集小红书搜索结果页数据，
按确定性规则算出竞争密度，落盘 JSON 供第 2 层（skill）分析。

设计依据：docs/20260802-eric-xhs-probe-搜索位空缺探测器实施方案.md
实测修正（2026-08-02）：
  - 搜索结果页不提供笔记总数 → note_count 字段作废，改用前排点赞分布
  - 下拉补全被防护，合成事件触发不了 → autocomplete 降级，改用标题精确匹配率
  - 详情页必须在搜索 tab 内 click a.cover 打开（需 xsec_token），直连返回空壳

用法：
  python3 probe.py --keyword "空降新老板前30天怎么站稳"
  python3 probe.py --from-cikuku --limit 5
  python3 probe.py --resume
"""
import argparse
import csv
import json
import random
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SUCAI = REPO / "xhs" / "素材库"
CIKU = SUCAI / "词库.csv"
OUT_DIR = SUCAI / "探测原始"
STATE_FILE = OUT_DIR / ".probe_state.json"
REJECT_DIR = OUT_DIR / ".rejected"   # 被拒绝覆盖的降级结果，留档但不进流水线

PROXY = "http://localhost:3456"
SEARCH_URL = "https://www.xiaohongshu.com/search_result?keyword={kw}&source=web_search_result_notes"

MAX_KEYWORDS_PER_RUN = 5
DELAY_BETWEEN_KEYWORDS = (45, 90)
DELAY_IN_PAGE = (2, 5)
# 开跑前的随机错峰上限（秒），与 daily_collect.JITTER_MAX 同一套理由：
# 词间延迟已经随机，但 launchd 掐整点触发这件事抖动不掉，得在入口补。
JITTER_MAX = 15 * 60
PAGE_LOAD_WAIT = 4
COMMENT_NOTES = 3      # 每个词点开几篇**高赞**笔记（取评论 + 正文）
LOW_LIKE_NOTES = 2     # 2026-08-12 加：再点开几篇**低赞**笔记做对照组。
                       # 每篇多约 10-15s（click + 加载 + back），5 篇/词约 90s。

RULE_VERSION = "v3"

# ⛔ 这里原本有 CAPTCHA_RE / EMPTY_RE 两个 Python 常量，但**没有任何代码用它们** ——
# 真正的判定一直在 JS_PAGE_STATE 里。两处各写一份正则、只有一处生效，
# 是 08-12 那次误判能活这么久的原因之一。已删，判定口径只留 JS 那一处。


# ---------- CDP Proxy ----------

def proxy_get(path, timeout=30):
    with urllib.request.urlopen(f"{PROXY}{path}", timeout=timeout) as r:
        return json.loads(r.read().decode())


def proxy_eval(target, js, timeout=30):
    """eval 走 POST。proxy 按表达式求值，多语句必须自己包 IIFE。"""
    req = urllib.request.Request(
        f"{PROXY}/eval?target={target}",
        data=js.encode(),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = json.loads(r.read().decode())
    if "error" in raw:
        raise RuntimeError(f"eval failed: {raw['error']}")
    value = raw.get("value")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def proxy_click(target, selector, timeout=30):
    req = urllib.request.Request(
        f"{PROXY}/click?target={target}",
        data=selector.encode(),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def proxy_alive():
    try:
        proxy_get("/targets", timeout=5)
        return True
    except Exception:
        return False


def pause(rng):
    time.sleep(random.uniform(*rng))


# ---------- 解析 ----------

def parse_likes(raw):
    """'1361' → 1361；'1.3万' → 13000；'赞'/None → None。"""
    if not raw:
        return None
    s = str(raw).strip()
    m = re.match(r"^([\d.]+)\s*万$", s)
    if m:
        return int(float(m.group(1)) * 10000)
    m = re.match(r"^(\d+)$", s)
    if m:
        return int(m.group(1))
    return None


def parse_date(raw, today=None):
    """'03-11' → 本年；'2025-01-31' → 原样；其他 → None。"""
    if not raw:
        return None
    s = str(raw).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    m = re.match(r"^(\d{2})-(\d{2})$", s)
    if m:
        year = (today or date.today()).year
        return f"{year}-{m.group(1)}-{m.group(2)}"
    return None


def core_tokens(keyword):
    """长尾词切成核心词，用于算标题精确匹配率。丢掉疑问词和虚词。"""
    stop = {"怎么办", "怎么", "如何", "什么", "该", "的", "了", "吗", "呢",
            "会", "要", "能", "可以", "是", "有", "被", "还", "不"}
    tokens = re.findall(r"[一-龥]{2,}|[A-Za-z]+|\d+", keyword)
    out = []
    for t in tokens:
        for s in stop:
            t = t.replace(s, "")
        if len(t) >= 2:
            out.append(t)
    return out


def match_ratio(keyword, titles):
    """前排标题里，命中 ≥半数核心词的比例。低 = 没人精确回答这个词。"""
    tokens = core_tokens(keyword)
    if not tokens or not titles:
        return None
    need = max(1, len(tokens) // 2)
    hit = sum(1 for t in titles if t and sum(1 for tok in tokens if tok in t) >= need)
    return round(hit / len(titles), 3)


META_TAIL_RE = re.compile(
    r"\s*(?:\d{4}-)?\d{2}-\d{2}\s*[一-龥]{2,4}(?:\s*(?:赞|回复|展开|\d+))*\s*$")
PURE_META_RE = re.compile(r"^[\d\s赞回复展开条]*$")


def clean_comment(text):
    """剥掉「日期+地区 赞 回复」尾巴。只做格式清洗，有没有信息量交给第 2 层判断。"""
    if not text:
        return ""
    s = re.sub(r"\s+", " ", text).strip()
    for _ in range(3):
        s2 = META_TAIL_RE.sub("", s).strip()
        if s2 == s:
            break
        s = s2
    return "" if PURE_META_RE.match(s) else s


def note_url(href, note_id):
    """卡片 href 是 /search_result/<id>?xsec_token=...，笔记真实地址在 /explore/ 下。
    xsec_token 是访问详情的必需参数，必须原样带上。"""
    if not href or not note_id:
        return None
    query = href.split("?", 1)[1] if "?" in href else ""
    base = f"https://www.xiaohongshu.com/explore/{note_id}"
    return f"{base}?{query}" if query else base


def median(nums):
    xs = sorted(n for n in nums if n is not None)
    if not xs:
        return None
    mid = len(xs) // 2
    return xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2


# ---------- 竞争密度（确定性规则，不经模型） ----------

def judge_density(notes, keyword):
    """决策1「好词三条件」的实测替代口径。

    原口径的「笔记数不为零但不多」在网页端取不到数，改用前排点赞分布：
    前排混着大量低赞笔记 = 这个搜索位没被占满。
    """
    likes = [n["likes"] for n in notes]
    titles = [n["title"] for n in notes]
    known = [x for x in likes if x is not None]

    d = {
        "verdict": "待探测",
        "rule_version": RULE_VERSION,
        "sample_size": len(notes),
        "likes_known": len(known),
        "median_likes": None,
        "low_like_ratio": None,
        "title_match_ratio": match_ratio(keyword, titles),
        "reason": "",
    }

    if len(known) < 8:
        d["reason"] = f"有效点赞样本仅 {len(known)} 条，不足以判定"
        return d

    d["median_likes"] = median(known)
    d["low_like_ratio"] = round(sum(1 for x in known if x < 100) / len(known), 3)

    med, low = d["median_likes"], d["low_like_ratio"]
    # v3 阈值：由 2026-08-02 的 15 个实测样本自然分组导出，双指标同时约束。
    # v2 的「中位数 > 3000」是死条款（15 个样本无一触发），判定实际全压在低赞占比上，
    # 单指标太脆，故把中位数拉回真正起作用的量级。
    #   实测分布：低 8-125赞/40-85%低赞 · 中 221-438/15-30% · 高 468-1069/10-12%
    if low >= 0.40 and med <= 200:
        d["verdict"] = "低"
        d["reason"] = f"前排点赞中位数 {med}，低赞(<100)占比 {low:.0%} — 搜索位有空缺"
    elif low <= 0.13 or med >= 600:
        d["verdict"] = "高"
        d["reason"] = f"前排点赞中位数 {med}，低赞占比 {low:.0%} — 已被高赞笔记占满"
    else:
        d["verdict"] = "中"
        d["reason"] = f"前排点赞中位数 {med}，低赞占比 {low:.0%}"

    if d["title_match_ratio"] is not None and d["title_match_ratio"] < 0.20:
        d["reason"] += f"；标题精确匹配率仅 {d['title_match_ratio']:.0%}，无笔记正面回答此长句"
    return d


# ---------- JS ----------

# 页面状态判定的唯一真相。三种失败长得像但处置完全不同，必须分开：
#   login   —— 没登录。搜索页整页是登录墙，重试多少次都一样，要人去登录。
#   captcha —— 真被风控拦了。要停手换时间，继续跑只会加重。
#   empty   —— 这个词真没内容。属正常结果，不是故障。
#
# ⛔ 2026-08-12：captcha 的正则原本含「验证码」，而登录墙里手机号登录框写着
# 「输入验证码 / 获取验证码」—— 于是**没登录被报成触发安全验证**，日志把排查
# 方向带偏了一整天（真因是那个 Chrome profile 的 www 域没有 web_session cookie）。
# 现在：先判 login，captcha 显式排除登录场景，且不再拿「验证码」当风控特征。
JS_PAGE_STATE = """(()=>{const t=document.body.innerText||"";
 const login=/登录后查看|扫码登录|手机号登录|新用户可直接登录/.test(t);
 return JSON.stringify({
 n:document.querySelectorAll("section.note-item").length,
 login:login,
 captcha:!login&&/安全验证|滑动验证|请完成验证|captcha/i.test(t),
 empty:/没找到相关内容|暂无相关|换个关键词/.test(t),
 len:t.length})})()"""

# 2026-08-12 加 cover：封面图 URL。
# 起因：审核评分卡里「首图」占 20 分，而采集库**一个图片字段都没有** ——
# 那 20 分的判据（「搜索原句大字 + 结论前置」）来自 07-31 决策 3 的拍板，至今零验证。
# 取封面 URL 是零成本的（同一次 eval，不多点一次页面），拿到后才谈得上分析首图规律。
# 多候选 + 兜底：小红书的懒加载会让 src 暂时为空，data-src / srcset 里仍有值。
JS_CARDS = """(()=>JSON.stringify([...document.querySelectorAll("section.note-item")].map((s,i)=>{
 const a=s.querySelector("a.cover")||s.querySelector("a[href*='/explore/']");
 const href=a?a.getAttribute("href"):null;
 const m=href?href.match(/\\/(?:explore|search_result|discovery\\/item)\\/([0-9a-zA-Z]+)/):null;
 const lines=(s.innerText||"").split("\\n").map(x=>x.trim()).filter(Boolean);
 const dl=lines.find(x=>/^\\d{2}-\\d{2}$/.test(x)||/^\\d{4}-\\d{2}-\\d{2}$/.test(x));
 const im=s.querySelector("a.cover img")||s.querySelector("img");
 let cov=im?(im.getAttribute("src")||im.getAttribute("data-src")||null):null;
 if(!cov&&im&&im.getAttribute("srcset")) cov=im.getAttribute("srcset").split(" ")[0];
 if(!cov&&a){const bg=getComputedStyle(a).backgroundImage;
   if(bg&&bg!=="none") cov=(bg.match(/url\\(["']?(.*?)["']?\\)/)||[])[1]||null;}
 return {rank:i+1,note_id:m?m[1]:null,href:href,
  title:s.querySelector(".title")?.innerText||null,
  author:s.querySelector(".author .name")?.innerText||null,
  likes_raw:s.querySelector(".count")?.innerText||null,
  cover:cov,
  date_raw:dl||null}})))()"""

# 正文和评论一起取：详情页已经打开了，多跑一个 querySelector 是零成本，
# 而单独为正文再点开一次笔记等于把反封控的点击预算翻倍。
# 正文选择器按特异性排序 —— #detail-desc 是笔记正文容器，
# 但要排除评论区里同样带 .note-text 的节点，所以先限定在 desc 容器内找。
JS_COMMENTS = """(()=>{const out=[];
 document.querySelectorAll("[class*=comment-item]").forEach(e=>{
  if(e.className.includes("comment-item-sub"))return;
  const txt=(e.innerText||"").replace(/\\s+/g," ").trim();
  const c=e.querySelector(".note-text")||e.querySelector("[class*=content] .note-text")||e.querySelector("[class*=content]");
  out.push({raw:txt,content:c?(c.innerText||"").trim():null,
   is_author:/\\s作者\\s/.test(" "+txt+" ")});});
 const dc=document.querySelector("#detail-desc")||document.querySelector(".note-content")
   ||document.querySelector("[class*=note-scroller] [class*=desc]");
 const de=dc?(dc.querySelector(".note-text")||dc):null;
 const tt=document.querySelector("#detail-title")||document.querySelector(".note-content .title");
 const tags=[...document.querySelectorAll("#detail-desc a[href*='/search_result'], .note-content a.tag")]
   .map(a=>(a.innerText||"").trim()).filter(Boolean);
 // ── 互动数据（2026-08-12 加）─────────────────────────────────────────
 // 起因：我们一直在优化评论率（自己账号 0.30%），却**从没采过别人的评论数** ——
 // 等于在没有任何参照系的情况下调 CTA。搜索卡片只给点赞，评论/收藏只有详情页有。
 // 详情页已经打开了，多跑几个 querySelector 是零成本。
 // ⛔ 选择器一律多候选 + 保留 raw：小红书 class 名常带 hash 且会变，
 // 写死一个必然某天静默失效（返回 null 而不报错，最难发现）。
 // raw 存下整条互动栏的文本，即使选择器全错，事后也能从 raw 里正则解析出来。
 const eb=document.querySelector(".engage-bar")||document.querySelector("[class*=engage-bar]")
   ||document.querySelector("[class*=interact-container]")||document.querySelector("[class*=engage]");
 const pick=(...sels)=>{for(const q of sels){const e=eb?eb.querySelector(q):null;
   const v=e?(e.innerText||"").trim():"";if(v)return v;}return null;};
 const bodyTxt=document.body.innerText||"";
 const cmTotal=(bodyTxt.match(/共\\s*([\\d.]+\\s*[万千]?)\\s*条评论/)||[])[1]
   ||(bodyTxt.match(/评论\\s*[（(]\\s*([\\d.]+\\s*[万千]?)\\s*[）)]/)||[])[1]||null;
 return JSON.stringify({url:location.href,comments:out.slice(0,30),
  note_title:tt?(tt.innerText||"").trim():null,
  note_body:de?(de.innerText||"").trim():null,
  note_tags:tags.slice(0,20),
  engage:{
   like_raw:pick("[class*=like] .count","[class*=like-wrapper]","[class*=like]"),
   collect_raw:pick("[class*=collect] .count","[class*=collect-wrapper]","[class*=collect]"),
   comment_raw:pick("[class*=chat] .count","[class*=chat-wrapper]","[class*=comment] .count"),
   comment_total_raw:cmTotal,
   dom_comments:out.length,
   raw:eb?(eb.innerText||"").replace(/\\s+/g," ").trim():null}})})()"""

JS_BACK = """(()=>{history.back();return "back"})()"""


# ---------- 主流程 ----------

def probe_keyword(keyword):
    result = {
        "keyword": keyword,
        "probed_at": datetime.now().isoformat(timespec="seconds"),
        "source": "xhs_cdp",
        "completeness": "failed",
        "_error": None,
        "note_count": None,          # 网页端不提供，恒为 null（保留字段便于将来接口化）
        "autocomplete": [],          # v1 不采集，见文件头实测修正
        "top_notes": [],
        "note_bodies": [],           # 2026-08-05 加：点开的那几篇的正文全文
        # 2026-08-12 加：所有点开过的笔记的**互动数据**（赞/藏/评），含正文为空的。
        # note_bodies 要求 body 非空（下游按正文用），但正文为空的笔记互动数据同样有价值 ——
        # 实测 32 篇里 10 篇正文是纯签名档，那 10 篇的赞藏评正是「正文没内容也能爆」的证据。
        "engage_samples": [],
        "comments": [],
        "density": {"verdict": "待探测", "rule_version": RULE_VERSION},
    }

    kw_enc = urllib.parse.quote(keyword)
    target = proxy_get(f"/new?url={urllib.parse.quote(SEARCH_URL.format(kw=kw_enc), safe=':/?=&%')}")["targetId"]

    try:
        time.sleep(PAGE_LOAD_WAIT)
        state = proxy_eval(target, JS_PAGE_STATE)

        # 顺序不能反：登录墙也会渲染出「验证码」字样，先判 login 才不会误报成风控。
        if state.get("login"):
            result["_error"] = "login_required"
            result["completeness"] = "failed"
            return result
        if state.get("captcha"):
            result["_error"] = "captcha_triggered"
            result["completeness"] = "failed"
            return result
        if state.get("n", 0) == 0:
            result["_error"] = "no_results_or_blocked"
            result["completeness"] = "failed" if state.get("empty") else "partial"
            return result

        cards = proxy_eval(target, JS_CARDS)
        for c in cards:
            c["likes"] = parse_likes(c.pop("likes_raw", None))
            c["published_at"] = parse_date(c.pop("date_raw", None))
            c["url"] = note_url(c.get("href"), c.get("note_id"))
        result["top_notes"] = cards
        result["completeness"] = "partial"

        # 评论：必须在搜索 tab 内 click a.cover 打开（详情页直连返回空壳）
        # 按点赞降序挑，不按 rank——低赞笔记通常没有评论，原话库会颗粒无收
        by_likes = sorted(
            [c for c in cards if c.get("note_id")],
            key=lambda c: (c.get("likes") is None, -(c.get("likes") or 0)),
        )
        picked = by_likes[:COMMENT_NOTES]
        # ⛔ 2026-08-12 加低赞对照组。
        # 原来只取高赞前 N，后果是采了 3 个月、32 篇有正文的笔记**全部是高赞**，
        # 想做「高赞 vs 低赞在正文/开头/CTA 上差在哪」的对照时才发现没有对照组 ——
        # 只能得出「爆款长什么样」，得不出「爆款和非爆款差在哪」，而后者才是规律。
        # 低赞笔记评论少，对原话库贡献小，但它是**分析的分母**，不能省。
        low = [c for c in by_likes if c.get("likes") is not None][-LOW_LIKE_NOTES:]
        picked += [c for c in low if c["note_id"] not in {p["note_id"] for p in picked}]
        for c in picked:
            pause(DELAY_IN_PAGE)
            try:
                proxy_click(target, f'section.note-item a.cover[href*="{c["note_id"]}"]')
                time.sleep(PAGE_LOAD_WAIT)
                data = proxy_eval(target, JS_COMMENTS)
                if "/explore/" not in (data.get("url") or ""):
                    continue
                # 正文：2026-08-05 加。此前只存标题，而标题只能告诉你「别人写了这个角度」，
                # 告诉不了你「他是怎么答的」——判答案空缺、找可迁移的说法都得看正文。
                eng = data.get("engage") or {}
                result["engage_samples"].append({
                    "note_id": c["note_id"],
                    "rank": c.get("rank"),
                    "title": (data.get("note_title") or c.get("title") or "").strip(),
                    "likes_from_card": c.get("likes"),
                    "like_raw": eng.get("like_raw"),
                    "collect_raw": eng.get("collect_raw"),
                    "comment_raw": eng.get("comment_raw"),
                    "comment_total_raw": eng.get("comment_total_raw"),
                    "dom_comments": eng.get("dom_comments"),
                    "body_len": len((data.get("note_body") or "").strip()),
                    "engage_bar_raw": eng.get("raw"),
                })
                body = (data.get("note_body") or "").strip()
                if body:
                    result["note_bodies"].append({
                        "note_id": c["note_id"],
                        "note_url": data["url"],
                        "title": (data.get("note_title") or c.get("title") or "").strip(),
                        "likes": c.get("likes"),
                        "body": body[:4000],
                        "tags": data.get("note_tags") or [],
                    })
                for cm in data.get("comments", []):
                    if cm.get("is_author"):
                        continue
                    text = clean_comment(cm.get("content") or cm.get("raw"))
                    if len(text) < 8:
                        continue
                    result["comments"].append({
                        "note_id": c["note_id"],
                        "note_url": data["url"],
                        "text": text,
                    })
                proxy_eval(target, JS_BACK)
                time.sleep(PAGE_LOAD_WAIT)
            except Exception as e:
                result["_error"] = f"comment_fetch_partial: {e}"
                break

        result["density"] = judge_density(cards, keyword)
        result["completeness"] = "full" if result["comments"] else "partial"
        return result

    finally:
        try:
            proxy_get(f"/close?target={target}")
        except Exception:
            pass


def slug(keyword):
    return re.sub(r"[^\w一-龥]+", "", keyword)[:20]


def probed_slugs():
    """已落过盘的词（按 slug）。用文件名比对，不读 JSON 内容。"""
    out = set()
    for p in OUT_DIR.glob("probe_*.json"):
        if p.name.endswith(".result.json"):
            continue
        m = re.match(r"probe_\d{8}_(.+)\.json$", p.name)
        if m:
            out.add(m.group(1))
    return out


COMPLETENESS_RANK = {"failed": 0, "partial": 1, "full": 2}


def write_result(path, result):
    """落盘，但**不允许用更差的结果覆盖更好的结果**。返回 (实际路径, 是否保护了旧文件)。

    2026-08-12 的事故：同一天连采三轮同一批词，第三轮被风控返回
    `no_results_or_blocked`（partial，笔记=0），同名文件直接覆盖了第一轮的 full 数据，
    14 条互动样本无声消失（分析脚本的样本数 29 → 15 才发现）。
    采集失败是常态，失败结果**吞掉已采好的数据**不是。
    """
    rank = COMPLETENESS_RANK.get(result.get("completeness"), 0)
    if path.exists():
        try:
            old_rank = COMPLETENESS_RANK.get(
                json.loads(path.read_text(encoding="utf-8")).get("completeness"), 0)
        except (json.JSONDecodeError, OSError):
            old_rank = -1
        if rank < old_rank:
            # 落到隐藏子目录：留档可排查，但躲开 auto_analyze / backfill 的
            # `probe_*.json` glob —— 否则这份空数据会被当成独立结果，
            # auto_analyze 还会为它花钱调一次模型。
            stamp = datetime.now().strftime("%H%M%S")
            REJECT_DIR.mkdir(parents=True, exist_ok=True)
            alt = REJECT_DIR / f"{path.stem}.{result.get('completeness', 'x')}-{stamp}.json"
            alt.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            return alt, True
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return path, False


# ── 账号定位过滤（2026-08-13 Eric 定）─────────────────────────────────────
# 「我整个账号都是面向职场的」。非职场词进探测队列有两笔代价：
#   ① 白烧探测配额 —— 2026-08-13 23:34 那轮 5 个词里有 3 个是这类
#      （「如何让任何人都爱上和你聊天？人缘爆棚！」「新闻复述法练习表达能力」
#       「如何通过表达建立主体性」），前排赞中位 1320/1498/3091，全是进不去的红海。
#   ② 制造假错配 —— 搜索位上挤满非职场人群，审核就会拿「人群不对」判成稿归档，
#      而稿子本身没问题（《答辩被打断》那篇的 63 分红就是这么来的）。
#      根在选词，不在成稿；所以这道闸设在选词层，不设在审核层。
#
# 规则写死、不问模型：判据要稳定可复现，而且这层每天跑几十次，不值得花额度。
_OFF_TOPIC = re.compile("|".join([
    # 校园 / 学生 —— 账号不服务这批人
    "毕业答辩", "毕业设计", "毕设", "开题", "论文", "导师", "辅导员", "校园",
    "大学生", "研究生", "考研", "保研", "社团", "宿舍", "学长", "学姐",
    # 泛社交 / 生活
    "爱上和你聊天", "人缘", "恋爱", "相亲", "闺蜜", "婆婆", "亲戚",
    "育儿", "减肥", "穿搭", "化妆", "旅游", "美食",
    # 泛表达训练 —— 不落在任何职场场景上
    "新闻复述", "建立主体性", "锻炼输出表达", "口才训练",
]))
# 命中上面、但同时含这些的仍然放行：说明场景还是职场。
# 「应届/校招/实习」故意留在白名单 —— 求职是职场的入口，
# 「应届生怎么谈薪」「校招面试问题」这类是本账号的正经选题（池里 17 个学生向词全是这类）。
_ON_TOPIC = re.compile("|".join([
    "职场", "面试", "hr", "HR", "领导", "老板", "同事", "下属", "上司",
    "汇报", "晋升", "述职", "绩效", "薪", "offer", "入职", "转正", "离职",
    "跳槽", "加班", "部门", "公司", "单位", "体制内", "应届", "校招", "实习", "答辩",
]))


def is_off_topic(kw: str) -> bool:
    """这个词是否偏离账号定位（职场）。命中黑名单且不含任何职场信号才算。"""
    return bool(_OFF_TOPIC.search(kw)) and not _ON_TOPIC.search(kw)


def load_pending(limit, skip_probed=False):
    """从词库取待探测词：竞争密度=待探测，优先意图强度高。

    状态用 startswith("候选") 而非精确匹配：历史数据里有 26 行写成
    「候选-源自记忆库(热度10000)」，精确匹配挑不中，这些词就永远排不进探测队列。
    「待验证」保留只为兼容老数据——2026-08-03 起不再产生该状态（Eric 定：不要这个中间态）。

    skip_probed（2026-08-12 加）：排除已有 probe JSON 的词。
    存在的理由：词库的「竞争密度」由**第 3 层** backfill.py 回写，而 backfill 要等
    第 2 层的 .result.json。只跑本脚本时词库状态永远停在「待探测」，
    于是每轮 --from-cikuku 都取到同一批前 N 个词 —— 08-11 与 08-12 连采三轮
    全是同 5 个词，同名文件还互相覆盖，样本量原地踏步（队列里另有 345 个词从没采过）。
    ⚠️ 默认关：定时任务 daily_probe.sh 依赖原有行为，且复采同一词做时间对照是正当用法。
    要扩样本覆盖面时显式加这个开关。
    """
    rows = list(csv.DictReader(CIKU.open(encoding="utf-8-sig")))
    pending = [r for r in rows
               if r.get("竞争密度", "").strip() == "待探测"
               and (r.get("状态", "").strip().startswith("候选")
                    or r.get("状态", "").strip() in ("待验证", "排队"))]
    pending.sort(key=lambda r: {"高": 0, "中": 1, "低": 2}.get(r.get("意图强度", "").strip(), 3))
    words = [r["关键词"].strip() for r in pending]

    # 账号定位过滤（见 is_off_topic）。被挡掉的要打出来 —— 静默丢词的话，
    # 哪天规则误伤了正经选题，没人看得出来「这个词怎么从来没被探过」。
    off = [w for w in words if is_off_topic(w)]
    if off:
        words = [w for w in words if not is_off_topic(w)]
        print(f"   · 账号定位过滤挡下 {len(off)} 个非职场词："
              f"{'、'.join(w[:18] for w in off[:5])}{' 等' if len(off) > 5 else ''}")
    if skip_probed:
        done = probed_slugs()
        words = [w for w in words if slug(w) not in done]
    return words[:limit]


def recompute(day):
    """阈值改了之后，从已抓到的 top_notes 重算 density。抓取成本不必重付。"""
    files = [p for p in sorted(OUT_DIR.glob(f"probe_{day}_*.json"))
             if not p.name.endswith(".result.json")]
    if not files:
        print(f"{day} 没有探测结果", file=sys.stderr)
        return 1
    for p in files:
        d = json.loads(p.read_text(encoding="utf-8"))
        notes = d.get("top_notes") or []
        if not notes:
            continue
        old = d.get("density", {}).get("verdict")
        d["density"] = judge_density(notes, d["keyword"])
        d["density"]["recomputed_at"] = datetime.now().isoformat(timespec="seconds")
        p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        new = d["density"]["verdict"]
        mark = "" if old == new else f"  ← 由「{old}」变更"
        print(f"{d['keyword']}: {new}{mark}")
        print(f"    {d['density']['reason']}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keyword", action="append", default=[])
    ap.add_argument("--from-cikuku", action="store_true")
    ap.add_argument("--skip-probed", action="store_true",
                    help="跳过已有 probe JSON 的词（词库状态未回写时防止每轮取到同一批）")
    ap.add_argument("--limit", type=int, default=MAX_KEYWORDS_PER_RUN)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--recompute", metavar="YYYYMMDD",
                    help="按当前阈值离线重算该日全部 JSON 的 density，不联网")
    ap.add_argument("--no-jitter", action="store_true",
                    help="跳过开跑前的随机延迟（手工调试用；定时任务别加）")
    args = ap.parse_args()

    if args.recompute:
        return recompute(args.recompute)

    # ⛔ 开跑前随机错峰（2026-08-16 加，与 daily_collect 同一套理由）。
    # 词间延迟这里本来就有 (45,90) 随机，缺的是**开跑时刻**的随机 ——
    # launchd 掐整点触发，「每天 12:30 准时开始搜索」这个规律抖动不掉。
    # --recompute 是离线重算，不联网，所以放在它后面判断。
    if not args.no_jitter:
        wait = random.uniform(0, JITTER_MAX)
        print(f"（错峰等待 {wait / 60:.1f} 分钟再开跑，避开整点规律）", flush=True)
        time.sleep(wait)

    if not proxy_alive():
        print("CDP Proxy 未就绪。先跑：node ~/.claude/skills/web-access/scripts/check-deps.mjs", file=sys.stderr)
        return 1

    if args.resume:
        if not STATE_FILE.exists():
            print("无中断状态可续跑", file=sys.stderr)
            return 1
        keywords = json.loads(STATE_FILE.read_text(encoding="utf-8"))["remaining"]
    elif args.from_cikuku:
        keywords = load_pending(args.limit, skip_probed=args.skip_probed)
    else:
        keywords = args.keyword

    if not keywords:
        print("没有待探测关键词", file=sys.stderr)
        return 1

    if len(keywords) > MAX_KEYWORDS_PER_RUN:
        print(f"单轮上限 {MAX_KEYWORDS_PER_RUN} 词（反封控），截断", file=sys.stderr)
        keywords = keywords[:MAX_KEYWORDS_PER_RUN]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat().replace("-", "")
    done, aborted, blank = [], False, 0

    for i, kw in enumerate(keywords):
        print(f"[{i+1}/{len(keywords)}] {kw}", flush=True)
        try:
            r = probe_keyword(kw)
        except Exception as e:
            r = {"keyword": kw, "completeness": "failed", "_error": str(e),
                 "probed_at": datetime.now().isoformat(timespec="seconds")}

        path = OUT_DIR / f"probe_{today}_{slug(kw)}.json"
        path, kept = write_result(path, r)
        d = r.get("density", {})
        if kept:
            print(f"    ⚠️ 已有 full 结果，本次{r['completeness']}不覆盖 → 另存 {path.name}",
                  file=sys.stderr, flush=True)
        print(f"    → {r['completeness']} | 密度={d.get('verdict','—')} | "
              f"笔记={len(r.get('top_notes',[]))} 正文={len(r.get('note_bodies',[]))} "
              f"评论={len(r.get('comments',[]))} "
              f"| {path.name}", flush=True)
        if d.get("reason"):
            print(f"      {d['reason']}", flush=True)
        done.append(kw)

        if r.get("_error") == "login_required":
            print("!! 未登录：搜索页是登录墙，不是被风控。本轮终止（重试多少次都一样）\n"
                  "   CDP 连的是采集专用 profile（9333, ~/.xhs-chrome-profile），"
                  "它的 www.xiaohongshu.com 可能没有 web_session cookie；\n"
                  "   注意 creator 域和 www 域的登录态是各自独立的，能进创作后台不代表能搜。\n"
                  "   → 首选改跑 opencli 版（附着日常 Chrome）：python3 probe_opencli.py --resume",
                  file=sys.stderr)
            aborted = True
            break

        if r.get("_error") == "captcha_triggered":
            print("!! 触发安全验证，本轮立即终止（不重试、不换词硬撑）", file=sys.stderr)
            aborted = True
            break

        # 单次空结果可能是这个词真没内容；连续两次基本是被限流了。
        # 08-12 实测：被限流后仍硬跑完剩余 3 个词，全部返回空 —— 白采还加重风控。
        blank = blank + 1 if r.get("_error") == "no_results_or_blocked" else 0
        if blank >= 2:
            print("!! 连续 2 个词返回空结果，判定被限流，本轮终止（换个时间再跑）",
                  file=sys.stderr)
            aborted = True
            break

        if i < len(keywords) - 1:
            pause(DELAY_BETWEEN_KEYWORDS)

    remaining = [k for k in keywords if k not in done]
    if aborted and remaining:
        STATE_FILE.write_text(json.dumps({"remaining": remaining, "aborted_at": datetime.now().isoformat()},
                                         ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"剩余 {len(remaining)} 词已存档，稍后 --resume", file=sys.stderr)
        return 1
    if STATE_FILE.exists() and not remaining:
        STATE_FILE.unlink()
    return 0


if __name__ == "__main__":
    sys.exit(main())
