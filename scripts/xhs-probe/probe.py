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

PROXY = "http://localhost:3456"
SEARCH_URL = "https://www.xiaohongshu.com/search_result?keyword={kw}&source=web_search_result_notes"

MAX_KEYWORDS_PER_RUN = 5
DELAY_BETWEEN_KEYWORDS = (45, 90)
DELAY_IN_PAGE = (2, 5)
PAGE_LOAD_WAIT = 4
COMMENT_NOTES = 3  # 每个词点开几篇笔记取评论

RULE_VERSION = "v3"

CAPTCHA_RE = re.compile(r"安全验证|验证码|滑动验证|captcha", re.I)
EMPTY_RE = re.compile(r"没找到相关内容|暂无相关|换个关键词")


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

JS_PAGE_STATE = """(()=>{const t=document.body.innerText||"";return JSON.stringify({
 n:document.querySelectorAll("section.note-item").length,
 captcha:/安全验证|验证码|滑动验证/.test(t),
 empty:/没找到相关内容|暂无相关|换个关键词/.test(t),
 len:t.length})})()"""

JS_CARDS = """(()=>JSON.stringify([...document.querySelectorAll("section.note-item")].map((s,i)=>{
 const a=s.querySelector("a.cover")||s.querySelector("a[href*='/explore/']");
 const href=a?a.getAttribute("href"):null;
 const m=href?href.match(/\\/(?:explore|search_result|discovery\\/item)\\/([0-9a-zA-Z]+)/):null;
 const lines=(s.innerText||"").split("\\n").map(x=>x.trim()).filter(Boolean);
 const dl=lines.find(x=>/^\\d{2}-\\d{2}$/.test(x)||/^\\d{4}-\\d{2}-\\d{2}$/.test(x));
 return {rank:i+1,note_id:m?m[1]:null,href:href,
  title:s.querySelector(".title")?.innerText||null,
  author:s.querySelector(".author .name")?.innerText||null,
  likes_raw:s.querySelector(".count")?.innerText||null,
  date_raw:dl||null}})))()"""

JS_COMMENTS = """(()=>{const out=[];
 document.querySelectorAll("[class*=comment-item]").forEach(e=>{
  if(e.className.includes("comment-item-sub"))return;
  const txt=(e.innerText||"").replace(/\\s+/g," ").trim();
  const c=e.querySelector(".note-text")||e.querySelector("[class*=content] .note-text")||e.querySelector("[class*=content]");
  out.push({raw:txt,content:c?(c.innerText||"").trim():null,
   is_author:/\\s作者\\s/.test(" "+txt+" ")});});
 return JSON.stringify({url:location.href,comments:out.slice(0,30)})})()"""

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
        "comments": [],
        "density": {"verdict": "待探测", "rule_version": RULE_VERSION},
    }

    kw_enc = urllib.parse.quote(keyword)
    target = proxy_get(f"/new?url={urllib.parse.quote(SEARCH_URL.format(kw=kw_enc), safe=':/?=&%')}")["targetId"]

    try:
        time.sleep(PAGE_LOAD_WAIT)
        state = proxy_eval(target, JS_PAGE_STATE)

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
        picked = sorted(
            [c for c in cards if c.get("note_id")],
            key=lambda c: (c.get("likes") is None, -(c.get("likes") or 0)),
        )[:COMMENT_NOTES]
        for c in picked:
            pause(DELAY_IN_PAGE)
            try:
                proxy_click(target, f'section.note-item a.cover[href*="{c["note_id"]}"]')
                time.sleep(PAGE_LOAD_WAIT)
                data = proxy_eval(target, JS_COMMENTS)
                if "/explore/" not in (data.get("url") or ""):
                    continue
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


def load_pending(limit):
    """从词库取待探测词：竞争密度=待探测，优先意图强度高。

    状态用 startswith("候选") 而非精确匹配：历史数据里有 26 行写成
    「候选-源自记忆库(热度10000)」，精确匹配挑不中，这些词就永远排不进探测队列。
    「待验证」保留只为兼容老数据——2026-08-03 起不再产生该状态（Eric 定：不要这个中间态）。
    """
    rows = list(csv.DictReader(CIKU.open(encoding="utf-8-sig")))
    pending = [r for r in rows
               if r.get("竞争密度", "").strip() == "待探测"
               and (r.get("状态", "").strip().startswith("候选")
                    or r.get("状态", "").strip() in ("待验证", "排队"))]
    pending.sort(key=lambda r: {"高": 0, "中": 1, "低": 2}.get(r.get("意图强度", "").strip(), 3))
    return [r["关键词"].strip() for r in pending[:limit]]


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
    ap.add_argument("--limit", type=int, default=MAX_KEYWORDS_PER_RUN)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--recompute", metavar="YYYYMMDD",
                    help="按当前阈值离线重算该日全部 JSON 的 density，不联网")
    args = ap.parse_args()

    if args.recompute:
        return recompute(args.recompute)

    if not proxy_alive():
        print("CDP Proxy 未就绪。先跑：node ~/.claude/skills/web-access/scripts/check-deps.mjs", file=sys.stderr)
        return 1

    if args.resume:
        if not STATE_FILE.exists():
            print("无中断状态可续跑", file=sys.stderr)
            return 1
        keywords = json.loads(STATE_FILE.read_text(encoding="utf-8"))["remaining"]
    elif args.from_cikuku:
        keywords = load_pending(args.limit)
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
    done, aborted = [], False

    for i, kw in enumerate(keywords):
        print(f"[{i+1}/{len(keywords)}] {kw}", flush=True)
        try:
            r = probe_keyword(kw)
        except Exception as e:
            r = {"keyword": kw, "completeness": "failed", "_error": str(e),
                 "probed_at": datetime.now().isoformat(timespec="seconds")}

        path = OUT_DIR / f"probe_{today}_{slug(kw)}.json"
        path.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
        d = r.get("density", {})
        print(f"    → {r['completeness']} | 密度={d.get('verdict','—')} | "
              f"笔记={len(r.get('top_notes',[]))} 评论={len(r.get('comments',[]))} "
              f"| {path.name}", flush=True)
        if d.get("reason"):
            print(f"      {d['reason']}", flush=True)
        done.append(kw)

        if r.get("_error") == "captcha_triggered":
            print("!! 触发安全验证，本轮立即终止（不重试、不换词硬撑）", file=sys.stderr)
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
