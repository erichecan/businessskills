#!/usr/bin/env python3
"""外部评论（去别人笔记下评论）—— 生成、风控、台账。

## 风险声明（不要删，删了下一个人会以为这条线从来没有风险）

小红书 2026-03-10《关于打击AI托管运营账号的治理公告》禁止利用技术手段模拟真人
进行虚假互动，对 AI 托管注册/发布/互动的账号予以**封禁**；2026-06-10《关于规范
搜索及问答生态相关行为的公告》禁止「评论区配合、账号矩阵联动」制造口碑。
本账号已有前科：`4d78742`「主站今天被限，请求特征太像机器」。

**Eric 在看过以上全部内容后，于 2026-08-18 决定改为全自动。**
风险由本文件的风控层承担，**压不到零**，最坏情况是账号级封禁。

## 风控层不是可选项

| 项 | 参数 | 为什么是这个数 |
|---|---|---|
| 每日上限 | 12 条 | 真人一天在陌生笔记下留 12 条评论已经算高频 |
| 单条间隔 | 随机 3–20 分钟 | 固定间隔是最容易被识别的机器特征 |
| 时段 | 避开 0–7 点 | 这个账号的人设不会凌晨三点在评论区答疑 |
| 去同质 | difflib 相似度 > 0.65 拒发 | 同质文本批量投放正是公告点名的「评论区配合」 |
| 熔断 | 出验证码/操作频繁，或当日连续失败 2 次 → 停 24h | 触发后不要「再试一次看看」 |

⚠️ **存活校验（发出 1 小时后回查还在不在）已按 Eric 2026-08-19 的决定取消。**
它原本是熔断的主要触发条件——被折叠/删除是最早的风控信号，比封号早。
取消的代价必须认下来：**评论被静默折叠现在发现不了**，只有等到发送本身开始失败
（撞验证码、连续失败）才会停。想恢复的话，缺的是一个 `fetch_alive(link, text)`：
打开笔记页、在评论区里找自己那条还在不在，读不到要单列一档（不能算「没了」，
网络抖动会让存活率虚低进而误熔断）。

## 分两段：生成不需要登录态，发送需要

`draft`  从评论区原话.csv 选目标 → 生成评论 → 过风控 → 写台账（状态=草稿）
`send`   把草稿按频次调度发出去（**需要 www 主站登录态**，见 draft_comments.py 的 C0）

⛔ 生成和发送**故意分开**：生成随时能跑、能人工抽查，发送受频次和熔断约束。
合在一起的话，「今天生成了 12 条」会不知不觉变成「今天发了 12 条」。
"""
import argparse
import csv
import difflib
import json
import random
import re
import time
import urllib.parse as up
import urllib.request as rq
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SUCAI = REPO / "xhs" / "素材库"
QUOTES = SUCAI / "评论区原话.csv"
LEDGER = SUCAI / "评论台账.csv"
STATE = SUCAI / "评论风控状态.json"
PROXY = "http://localhost:3456"

sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(Path(__file__).parent))

LEDGER_COLS = ["时间", "战场", "目标链接", "对方原话", "发出内容",
               "场景", "概念", "正文术语", "状态", "存活校验", "备注"]

# ── 风控参数 ────────────────────────────────────────────────────────────────
DAILY_CAP = 12
GAP_MIN, GAP_MAX = 3 * 60, 20 * 60          # 秒
QUIET_HOURS = range(0, 7)
# 0.65 是实测选的，不是拍的。四组样本在三种度量下的分离度：
#                        2gram  3gram  difflib
#   近义改写①              0.70   0.58     0.91
#   近义改写②              0.37   0.26     0.74
#   同套路换场景            0.18   0.09     0.43
#   完全不同                0.00   0.00     0.06
# 3-gram Jaccard 把「那句原话 / 的那句话」这种改写的差异放大了，0.58 拦不住 ——
# 而那两句在人眼里就是同一句。difflib 基于最长匹配块，对改写敏感得多。
# ⚠️ 0.43 的「同套路换场景」是放行的。单条看没问题，但**同一个句式模板刷十条**
# 仍然是公告点名的那种模式 —— 这一层现在拦不住，靠每日 12 条的上限兜着。
SIM_THRESHOLD = 0.65
COOLDOWN_HOURS = 24


def _rows(path):
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def read_ledger():
    return _rows(LEDGER)


def write_ledger(rows):
    tmp = LEDGER.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LEDGER_COLS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in LEDGER_COLS})
    tmp.replace(LEDGER)


def append_ledger(row):
    rows = read_ledger()
    rows.append(row)
    write_ledger(rows)


# ── 去同质 ──────────────────────────────────────────────────────────────────

def _flat(s):
    return re.sub(r"\s|[，。！？、,.!?：:；;]", "", s)


def similarity(a, b):
    """difflib 的最长匹配块比例。标准库，不引依赖；评论只有几十字，O(n²) 无所谓。"""
    fa, fb = _flat(a), _flat(b)
    if not fa or not fb:
        return 0.0
    return difflib.SequenceMatcher(None, fa, fb).ratio()


# 「X力」里属于日常汉语、不该被当成造词的那些。
# 这张表宁可长一点 —— 误报一条就是一条本来能发的评论被打回。
_COMMON_LI = {"能力", "努力", "压力", "精力", "体力", "实力", "活力", "动力", "魅力",
              "张力", "主力", "全力", "有力", "无力", "权力", "暴力", "武力", "视力",
              "听力", "记忆力", "注意力", "执行力", "影响力", "竞争力", "抗压力",
              "说服力", "判断力", "行动力", "免疫力", "生命力", "想象力", "创造力"}
def coined_terms(text, terms):
    """找出正文里造出来的「X力」—— 不在术语库、也不是日常词的。

    ⛔ 这条检查的必要性是实测出来的：第一批 3 条里就出现「把对抗力换成反馈力」。
    「对抗力」听着很顺，但术语库里没有这个词。让模型顺手造力，整套概念体系
    会在评论区被稀释成「什么都是一种力」—— 那正好毁掉建立术语库的理由。

    ⛔ 别用 `[\u4e00-\u9fff]{1,3}力` 这种正则：它贪婪匹配，
    「把对抗力换成反馈力」会被切成「把对抗力」和「成反馈力」，连正确用词也报成造词。
    正确做法是从「力」字往前按**已知词**匹配（已知词都是 3 字），匹配不上才算造词。
    """
    known = set(terms) | _COMMON_LI
    out = set()
    for i, ch in enumerate(text):
        if ch != "力":
            continue
        if any(i - n + 1 >= 0 and text[i - n + 1:i + 1] in known for n in (3, 2)):
            continue
        out.add(text[max(0, i - 2):i + 1])
    return sorted(out)


def too_similar(text, sent_texts, threshold=SIM_THRESHOLD):
    """返回 (是否过近, 最像的那条, 相似度)。"""
    best, score = "", 0.0
    for s in sent_texts:
        v = similarity(text, s)
        if v > score:
            best, score = s, v
    return score > threshold, best, score


# ── 熔断与频次 ──────────────────────────────────────────────────────────────

def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"熔断至": "", "原因": ""}


def save_state(st):
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def trip_breaker(reason, now=None):
    now = now or datetime.now()
    st = load_state()
    st["熔断至"] = (now + timedelta(hours=COOLDOWN_HOURS)).isoformat(timespec="seconds")
    st["原因"] = reason
    save_state(st)
    return st


def breaker_on(now=None):
    """(是否熔断中, 原因)。"""
    now = now or datetime.now()
    st = load_state()
    until = st.get("熔断至") or ""
    if until and now < datetime.fromisoformat(until):
        return True, f"{st.get('原因','')}（至 {until}）"
    return False, ""


def can_send_now(ledger, now=None):
    """现在能不能发下一条。返回 (能不能, 原因)。纯函数，好测。"""
    now = now or datetime.now()
    on, why = breaker_on(now)
    if on:
        return False, f"熔断中：{why}"
    if now.hour in QUIET_HOURS:
        return False, f"{now.hour} 点在静默时段（避开 {QUIET_HOURS.start}-{QUIET_HOURS.stop} 点）"
    today = now.date().isoformat()
    sent = [r for r in ledger if r.get("状态") == "已发送"
            and (r.get("时间") or "").startswith(today)]
    if len(sent) >= DAILY_CAP:
        return False, f"今天已发 {len(sent)}/{DAILY_CAP} 条，到上限"
    if sent:
        last = max(datetime.fromisoformat(r["时间"]) for r in sent)
        wait = (now - last).total_seconds()
        if wait < GAP_MIN:
            return False, f"距上一条才 {int(wait)}s，最小间隔 {GAP_MIN}s"
    return True, ""


def next_gap():
    """下一条等多久。随机化是风控的一部分，固定间隔是最容易被抓的机器特征。"""
    return random.randint(GAP_MIN, GAP_MAX)


# ── 生成 ────────────────────────────────────────────────────────────────────

PROMPT = """给小红书的一条真实评论写一条回应，由博主本人发在**别人的笔记**评论区。

## 这条评论要做到什么

不是刷存在感，是**在这个人的具体问题下给一句真正有用的建议**。
读到它的人应该觉得「这条比楼里其他回复都实在」，而不是「又一个来引流的」。

## 硬要求

1. 必须**针对对方说的那件事**：引用他提到的一个具体细节（他的处境、他的原话里的词），
   让人一眼看出这是写给他的，不是通用模板。
2. 给一句**可直接照抄的话**或一个当场能用的判据。⛔ 不许只讲道理（「要有边界感」这种）。
3. **全长 ≤ 60 字（硬上限，超了整条作废）**。写完自己数一遍。评论区没人读长篇。
4. 口语，像人随手回的。禁止「宝子」「家人们」「建议收藏」「关注我」。
5. **默认不用概念术语**。大多数评论不需要出现术语，直接给建议就够了。
   只有当某个术语能让这条建议**更清楚**时才用，且最多 1 次。
   可用术语（措辞必须逐字照抄）：{terms}
   ⛔ 不许造术语库里没有的「X力」（实测出现过「对抗力」——术语库里没有这个词）。
   ⛔ 不许在句尾贴标签（「……这就是化解力」这种）：读起来是在推销概念，不是在帮人。
   用不上就不用，硬塞比不塞更像广告。
6. ⛔ 不引流：不提主页、不提账号、不说「我这有」「私我」。
7. 不承诺结果（保过、一定能、肯定），不出现身份头衔（前腾讯/资深/专家/总监）。

## 输出格式

只输出评论正文本身。不要解释、不要引号、不要 markdown。

---

对方的原话：{quote}

他暴露的处境：{situation}
"""


def gen_one(quote, situation, model=None):
    from draft_comments import run_claude, normalize_punct
    import scene_map
    prompt = PROMPT.format(terms="、".join(scene_map.load_terms()),
                           quote=quote[:400], situation=situation[:120])
    out, err = run_claude(prompt)
    if not out:
        return None, err
    txt = re.sub(r"^```[a-z]*\n|\n```$", "", out.strip()).strip()
    # ⛔ 只剥**成对**的首尾引号。无脑 `.strip('"“”')` 会把
    # 「"反问一句这里面有啥情况"确实比防御话术安全」剥成
    # 「反问一句这里面有啥情况"确实比防御话术安全」—— 左引号没了、右引号留在句中。
    if len(txt) > 1 and txt[0] in '"“' and txt[-1] in '"”':
        txt = txt[1:-1].strip()
    return normalize_punct(txt), ""


def pick_targets(ledger, limit):
    """挑还没评论过的笔记。每个链接只评一次 —— 同一条笔记下反复出现才是刷屏。"""
    done = {r.get("目标链接") for r in ledger if r.get("战场") == "外部"}
    by_link = {}
    for r in _rows(QUOTES):
        link = (r.get("来源链接") or "").strip()
        quote = (r.get("用户原话") or "").strip()
        sit = (r.get("暴露的处境") or "").strip()
        # ⛔ 有一批行的「暴露的处境」写的是「探测词：xxx」—— 那是采集来源，不是处境。
        # 拿它当 prompt 输入，模型只能对着一个搜索词瞎猜对方的困境，
        # 实测生成的第 3 条就是这么跑偏的（对方在说 CTO 面试，处境写着「第三面hr面试问什么」）。
        if not link or not quote or link in done or sit.startswith("探测词"):
            continue
        # 每个链接取最长的那条原话：信息最多，最容易给出具体建议
        cur = by_link.get(link)
        if not cur or len(quote) > len(cur.get("用户原话", "")):
            by_link[link] = r
    out = sorted(by_link.values(), key=lambda r: -len(r.get("用户原话", "")))
    return out[:limit]


def cmd_draft(args):
    import scene_map
    ledger = read_ledger()
    sent_texts = [r.get("发出内容", "") for r in ledger if r.get("发出内容")]
    targets = pick_targets(ledger, args.limit)
    if not targets:
        print("没有可评论的新目标（评论区原话.csv 里的链接都评过了）")
        return 0
    print(f"待生成 {len(targets)} 条（已评过 {len(set(r.get('目标链接') for r in ledger))} 个链接）\n")

    terms = scene_map.load_terms()
    scenes = scene_map.load_scenes()
    ok = 0
    for i, t in enumerate(targets, 1):
        quote = (t.get("用户原话") or "").strip()
        sit = (t.get("暴露的处境") or "").strip()
        print(f"[{i}/{len(targets)}] {sit[:30]}")
        tagged = scene_map.tag(f"{sit} {quote}"[:200], scenes)
        txt, err = gen_one(quote, sit)
        if not txt:
            print(f"   ⛔ {err}")
            continue
        n = len(re.sub(r"\s", "", txt))
        dup, like, score = too_similar(txt, sent_texts)
        used = [c for c in terms if c in txt]
        flags = []
        if n > 60:
            flags.append(f"{n} 字 >60")
        if len(used) > 1:
            flags.append(f"用了 {len(used)} 个术语（单条最多 1 个）")
        coined = coined_terms(txt, terms)
        if coined:
            flags.append(f"造词：{'、'.join(coined)}（术语库里没有）")
        if dup:
            flags.append(f"与已发的相似度 {score:.2f}>{SIM_THRESHOLD}")
        status = "草稿" if not flags else "打回"
        print(f"   {txt}")
        print(f"   → {n} 字 · 术语 {used or '无'} · {status}"
              + (f" · {'; '.join(flags)}" if flags else ""))
        append_ledger({"时间": datetime.now().isoformat(timespec="seconds"),
                       "战场": "外部", "目标链接": t.get("来源链接", ""),
                       "对方原话": quote[:120], "发出内容": txt,
                       # 概念记的是**这条评论打在哪个概念的场景上**，不是正文里出现了哪个词。
                       # 只统计「正文提到的术语」的话，一批不提术语的好评论会全部记成 0 覆盖 ——
                       # 而强化概念靠的是「在对的场景给对的建议」，不是每条都贴标签。
                       "概念": (tagged or {}).get("概念", ""),
                       "场景": (tagged or {}).get("场景", ""),
                       "正文术语": "/".join(used), "状态": status,
                       "存活校验": "", "备注": "; ".join(flags)})
        sent_texts.append(txt)
        ok += status == "草稿"
    print(f"\n生成 {ok} 条草稿 → {LEDGER.relative_to(REPO)}")
    print("发送是另一个子命令：`send`（受每日上限、间隔、静默时段、熔断约束）")
    return 0


# ── 发送 ────────────────────────────────────────────────────────────────────
#
# 选择器全部是 2026-08-19 在真页面上 probe 出来的，不是凭空写的。规矩在
# draft_comments.py 里：不先 probe 就写选择器 = 抓到 0 条和「本来就没有新评论」
# 长得一模一样，这正是这条链路反复出问题的方式。页面改版后要重新 probe，
# 不要在这里猜着改。


# 笔记页的评论框和通知页**不是同一套**（2026-08-19 在真页面上定的）：
#   通知页  textarea.comment-input  → React 受控，用 prototype setter + input 事件
#   笔记页  p.content-input（contenteditable）→ 自定义编辑器，只有 execCommand 有效
# ⛔ 别把两套混用。给 contenteditable 赋 .value 什么也不会发生，
#    而且失败得很安静：框里没字、点发送没反应，日志里看不出区别。
NOTE_SELECTORS = {
    "activate": "div.not-active, div.inner-when-not-active",
    "input": "p.content-input",
    "submit": "button.btn.submit",
    "cancel": "button.btn.cancel",
}

ACTIVATE_JS = """(()=>{const el=[...document.querySelectorAll("div")]
 .find(e=>/not-active|inner-when-not-active/.test(String(e.className))&&(e.offsetWidth||e.offsetHeight));
 if(!el) return "notfound"; el.click(); return "ok";})()"""

INSERT_JS = """(()=>{const e=document.querySelector("p.content-input");
 if(!e) return "no-input"; e.focus();
 document.execCommand("selectAll",false,null);
 document.execCommand("delete",false,null);
 const ok=document.execCommand("insertText",false,%s);
 return JSON.stringify({ok:ok, text:e.textContent});})()"""

CLEAR_JS = """(()=>{const e=document.querySelector("p.content-input");
 if(!e) return "gone"; e.focus();
 document.execCommand("selectAll",false,null);
 document.execCommand("delete",false,null);
 return e.textContent;})()"""

SUBMIT_JS = """(()=>{const b=document.querySelector("button.btn.submit");
 if(!b) return "no-submit";
 if(b.disabled) return "disabled";
 b.click(); return "ok";})()"""

# 撞上这些就熔断：验证码、频繁操作提示、登录失效。
# ⚠️ 存活校验（发出 1 小时后回查还在不在）**已按 Eric 2026-08-19 的决定取消**。
# 它原本是熔断的主要触发条件，取消后熔断只剩下发送侧这些**当场可见**的信号。
# 代价要认：评论被静默折叠这种情况现在发现不了，只有等到发送本身开始失败才会停。
RISK_PATTERNS = ("验证码", "操作频繁", "频繁操作", "请稍后再试", "登录", "异常")


def _cdp(path, data=None, timeout=60):
    req = rq.Request(PROXY + path, data=data.encode() if data else None,
                     method="POST" if data else "GET")
    return json.loads(rq.urlopen(req, timeout=timeout).read())


def _ev(tid, js):
    return _cdp(f"/eval?target={tid}", js).get("value")


def _count_comments(tid):
    """读「共 N 条评论」。读不到返回 None（不能当成 0，那会把失败判成成功）。"""
    raw = _ev(tid, r"""(()=>{const e=[...document.querySelectorAll("div,span")]
     .find(x=>/^共\s*[\d.,万]+\s*条评论$/.test((x.textContent||"").trim()));
     return e?e.textContent.trim():"";})()""")
    if not raw:
        return None
    m = re.search(r"([\d.]+)\s*(万?)", raw)
    if not m:
        return None
    n = float(m.group(1))
    return int(n * 10000) if m.group(2) else int(n)


def post_comment(link: str, text: str):
    """在 link 这条笔记下发一条评论。返回 (成功, 说明)。"""
    if not link:
        return False, "没有目标链接"
    tid = _cdp("/new?url=" + up.quote(link, safe=":/?&=%"))["targetId"]
    try:
        time.sleep(9)
        url = _ev(tid, "location.href") or ""
        if "/explore/" not in url and "/discovery/" not in url:
            return False, f"没打开笔记页（当前 {url[:60]}）"
        page = (_ev(tid, '(document.body.innerText||"").slice(0,3000)') or "")
        hit = [p for p in RISK_PATTERNS if p in page[:600]]
        if "验证码" in page[:600] or "操作频繁" in page[:600] or "频繁操作" in page[:600]:
            return False, f"RISK:页面出现风控提示 {hit}"
        # ⛔ 登录墙和风控提示必须分开：登录态掉了该去重新扫码，**不该熔断 24 小时**。
        # 实测 2026-08-19：登录态部分失效时 `/explore` 首页照常打开、笔记页却盖一层
        # 扫码框，而 URL 仍然是 `/explore/<id>` —— 只看 URL 判断不出来，
        # 后果是报成「评论框没出现」这种查不到根因的错。
        if "扫码" in page[:400] and "登录" in page[:400]:
            return False, ("LOGIN:笔记页盖了登录墙 —— cdp profile"
                           "（~/.xhs-chrome-profile 那个 Chrome）的登录态掉了，"
                           "在那个窗口里重新扫码即可，不要当成风控")

        # ⛔ 发送前先记下评论总数 —— 这是唯一可靠的成功判据。
        # 第一版用「发送后输入框清空」判，结果实测第一条**明明发出去了**
        # （评论数 977→978）却被判成失败：这条笔记的编辑器发完不清空。
        # 判据说失败、实际成功是最危险的一类 bug —— 台账记错、重试还会重复评论，
        # 而重复评论正是「评论区配合」最像机器的特征。
        before = _count_comments(tid)

        if _ev(tid, ACTIVATE_JS) != "ok":
            return False, "找不到评论激活区"
        time.sleep(2)
        raw = _ev(tid, INSERT_JS % json.dumps(text, ensure_ascii=False))
        if raw == "no-input":
            return False, "评论框没出现"
        try:
            d = json.loads(raw)
        except (ValueError, TypeError):
            return False, f"填入回读失败：{raw}"
        # ⛔ 回读必须逐字比对。contenteditable 会吞掉部分字符（换行、表情），
        # 只看「非空」的话，发出去的可能是半句话。
        if d.get("text", "").strip() != text.strip():
            _ev(tid, CLEAR_JS)
            return False, f"回读对不上（框里是 {d.get('text','')[:30]!r}），已清空不发"

        r = _ev(tid, SUBMIT_JS)
        if r != "ok":
            _ev(tid, CLEAR_JS)
            return False, f"发送按钮不可用（{r}）"
        time.sleep(5)
        after_txt = (_ev(tid, '(document.body.innerText||"").slice(0,600)') or "")
        if any(p in after_txt for p in ("验证码", "操作频繁", "频繁操作")):
            return False, "RISK:发送后出现风控提示"
        after = _count_comments(tid)
        _ev(tid, CLEAR_JS)          # 不管成没成，都别把文本留在框里
        if before is not None and after is not None:
            if after > before:
                return True, f"评论数 {before}→{after}"
            return False, f"评论数没涨（{before}→{after}），判定未发出"
        # 读不到计数时退回文本检索：清空输入框之后再找，避免把框里的字当成已发出
        hit = _ev(tid, '(document.body.innerText||"").includes(%s)'
                  % json.dumps(text[:14], ensure_ascii=False))
        return (bool(hit), "按正文检索判定" if hit else "读不到评论数且正文检索不到")
    finally:
        try:
            _cdp("/close?target=" + tid)
        except Exception:                                   # noqa: BLE001
            pass


def cmd_send(args):
    ledger = read_ledger()
    drafts = [i for i, r in enumerate(ledger)
              if r.get("状态") == "草稿" and r.get("战场") == "外部"]
    if not drafts:
        print("没有待发的草稿。先跑 `draft`。")
        return 0
    sent = 0
    for i in drafts:
        can, why = can_send_now(ledger)
        if not can:
            print(f"⏸ 停在第 {sent} 条：{why}")
            break
        r = ledger[i]
        try:
            ok, note = post_comment(r["目标链接"], r["发出内容"])
        except NotImplementedError as e:
            print(f"⛔ {e}")
            return 2
        r["状态"] = "已发送" if ok else "发送失败"
        r["时间"] = datetime.now().isoformat(timespec="seconds")
        r["备注"] = note
        write_ledger(ledger)
        sent += ok
        # 存活校验取消后，熔断只剩发送侧这两条信号：
        #   ① 页面出现风控提示（验证码/操作频繁）→ 立刻停，这是最硬的
        #   ② 连续失败 2 次 → 多半是登录态掉了或页面又改了，继续试只会更像机器
        if not ok and note.startswith("LOGIN:"):
            print(f"   ⛔ {note}")
            print("   停在这里但**不熔断** —— 登录态问题重新扫码就能继续，"
                  "熔断 24 小时是给风控信号留的")
            break
        if not ok and note.startswith("RISK:"):
            trip_breaker(note)
            print(f"   ⛔⛔ 撞到风控提示，已熔断 {COOLDOWN_HOURS} 小时：{note}")
            break
        fails = [x for x in ledger if x.get("状态") == "发送失败"
                 and (x.get("时间") or "").startswith(datetime.now().date().isoformat())]
        if len(fails) >= 2:
            trip_breaker(f"今日连续发送失败 {len(fails)} 次")
            print(f"   ⛔⛔ 今日已失败 {len(fails)} 次，熔断 {COOLDOWN_HOURS} 小时 —— "
                  f"先查登录态和页面结构，别再试")
            break
        if sent >= args.limit:
            break
        gap = next_gap()
        print(f"   已发 {sent} 条，等 {gap // 60} 分 {gap % 60} 秒")
        time.sleep(gap)
    print(f"本轮发出 {sent} 条")
    return 0


def cmd_gaps(args):
    """C6：评论区语料反哺选题。

    两件事：
    ① **未打标的处境** —— 场景地图接不住的口语。实测这是常态：匹配词是按**搜索关键词**
       设计的，而评论区说的是人话（「薪资区间」「follow up」「套近乎」都曾漏网）。
       这批词是现成的、有真实语料背书的匹配词缺口清单，和 S2 用未打标词反推场景是同一招。
    ② **打上标但零产出的场景** —— 读者正在这个处境里说话，而我们一篇都没写过。
       这比 pick_topic 自己算出来的缺口更硬：那是「配额说该做」，这是「有人正在问」。
    """
    import scene_map
    scenes = scene_map.load_scenes()
    rows = [r for r in _rows(QUOTES)
            if not (r.get("暴露的处境") or "").strip().startswith("探测词")]
    from collections import Counter
    untagged, by_scene = [], Counter()
    for r in rows:
        sit = (r.get("暴露的处境") or "").strip()
        if not sit:
            continue
        t = scene_map.tag(f"{sit} {(r.get('用户原话') or '')[:80]}", scenes)
        if t:
            by_scene[t["场景"]] += 1
        else:
            untagged.append(sit)

    print(f"## 评论区语料 {len(rows)} 条（去掉「探测词」行）\n")
    print(f"### 打不上标的 {len(untagged)} 条（{len(untagged)/max(len(rows),1):.0%}）—— 匹配词缺口")
    for sit in untagged[:args.limit]:
        print(f"   · {sit[:56]}")

    # 零产出场景：读者在问，我们没写过
    sys.path.insert(0, str(REPO / "scripts" / "xhs-loop"))
    from refine_loop import scene_output
    produced = scene_output(90)          # 用 90 天窗口，判「有没有写过」而不是「近期够不够」
    hot_zero = [(sc, n) for sc, n in by_scene.most_common()
                if not produced.get(sc)]
    print(f"\n### 读者在问、我们 90 天内一篇没写的场景 {len(hot_zero)} 个")
    for sc, n in hot_zero[:12]:
        row = scene_map.scene_of(sc, scenes)
        print(f"   · {sc:<16} 评论区 {n:>3} 条 · {row['默认概念'] if row else ''}")
    if args.write and hot_zero:
        sys.path.insert(0, str(REPO / "scripts" / "xhs-loop"))
        from refine_loop import log_gap
        for sc, n in hot_zero[:args.limit]:
            row = scene_map.scene_of(sc, scenes)
            if row:
                log_gap(row, f"评论区有 {n} 条真实语料落在这个场景，90 天内零产出")
        print(f"\n→ 已写 {min(len(hot_zero), args.limit)} 条进 缺词信号.csv（下一轮采集会定向投）")
    return 0


def cmd_state(args):
    ledger = read_ledger()
    now = datetime.now()
    can, why = can_send_now(ledger, now)
    on, bwhy = breaker_on(now)
    today = now.date().isoformat()
    sent_today = [r for r in ledger if r.get("状态") == "已发送"
                  and (r.get("时间") or "").startswith(today)]
    print(f"台账 {len(ledger)} 条 · 今日已发 {len(sent_today)}/{DAILY_CAP}")
    print(f"熔断：{'⛔ ' + bwhy if on else '正常'}")
    print(f"现在能发：{'✅' if can else '❌ ' + why}")
    print("存活校验：已取消（Eric 2026-08-19）—— 评论被静默折叠现在发现不了")
    from collections import Counter
    print("状态分布：", dict(Counter(r.get("状态", "") for r in ledger)))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("draft", help="生成外部评论草稿（不发送，不需要登录态）")
    d.add_argument("--limit", type=int, default=DAILY_CAP)
    sub.add_parser("state", help="看风控状态：今日额度、间隔、熔断")
    g = sub.add_parser("gaps", help="评论区语料反哺选题：匹配词缺口 + 读者在问却没写过的场景")
    g.add_argument("--limit", type=int, default=15)
    g.add_argument("--write", action="store_true", help="把零产出场景写进 缺词信号.csv")
    sd = sub.add_parser("send", help="把草稿按频次调度发出去（需 C0：www 主站登录态）")
    sd.add_argument("--limit", type=int, default=DAILY_CAP)
    a = ap.parse_args()
    return {"draft": cmd_draft, "state": cmd_state, "gaps": cmd_gaps,
            "send": cmd_send}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
