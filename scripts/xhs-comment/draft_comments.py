#!/usr/bin/env python3
"""评论区自动化：只生成草稿，永远不替你发。

⛔ 为什么「只生成不发」不是保守，是硬约束（2026-08-08 查证）：
小红书 2026-03-10《关于打击AI托管运营账号的治理公告》——「严格禁止任何利用
技术手段模拟真人、进行非真实内容创作或虚假互动的行为」，对「通过AI托管工具
注册、发布、互动的账号」予以**封禁**。
2026-06-10《关于规范搜索及问答生态相关行为的公告》——禁止「刷量、购买虚假
点赞/收藏/评论（含自行操作）」「通过**评论区配合、账号矩阵联动**等方式人为
制造口碑」，处置为降权、下架、封禁。
所以机器人评论、小号自评、自动回复真人，全部在红线内侧。这个脚本做的是
**不模拟真人的那部分**：读、比对、写草稿。最后一下由 Eric 本人点发送。
和发布流水线「最后一下留给人」是同一个形状。

两个子命令：

  first   为成稿生成「首评草稿」——发布后由本人发在自己笔记下的第一条评论。
          本人以博主身份说话，不伪装他人，不在上述红线内。
          空置顶等于白扔一块高曝光位。

  watch   监测自己笔记下的新评论，为每条生成 2–3 个回复候选写进台账。
          ⚠️ 需要 www.xiaohongshu.com 的登录态（创作后台的登录态不通用，
          创作后台只给评论**数量**、不给评论正文）。见 --probe。

用法：
  python3 draft_comments.py first --draft 成稿_2026-08-07_空降后下属不服管.md
  python3 draft_comments.py first --all-pending      # 所有已排期未发布的
  python3 draft_comments.py watch --probe            # 检查登录态、导出页面结构
  python3 draft_comments.py watch                    # 抓新评论 + 生成回复草稿
"""
import argparse
import csv
import random
import json
import re
import subprocess
import sys
import time
import urllib.parse as up
import urllib.request as rq
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SUCAI = REPO / "xhs" / "素材库"
LEDGER = SUCAI / "评论台账.csv"
CLAUDE = Path.home() / ".local" / "bin" / "claude"
PROXY = "http://localhost:3456"

sys.path.insert(0, str(REPO / "scripts" / "case-entry"))

LEDGER_COLS = ["首次发现", "笔记标题", "笔记ID", "评论ID", "评论者", "评论原话",
               "回复草稿", "状态"]


# ── 基础设施 ────────────────────────────────────────────────────────────────

def api(path, data=None, timeout=40):
    req = rq.Request(PROXY + path, data=data.encode() if data else None,
                     method="POST" if data else "GET")
    return json.loads(rq.urlopen(req, timeout=timeout).read())


def ev(tid, js):
    return api(f"/eval?target={tid}", js).get("value")


def run_claude(prompt, timeout=300):
    """调 headless claude。这里不做 refine_loop 那套额度熬夜重试 ——
    首评/回复草稿都是小请求，撞额度就直接说，等下一次跑就行，没必要占着进程五小时。

    2026-08-18 改两处（T8）：
    ① 走 headless_cli.build_argv —— 此前这里自己拼 `[claude, "-p", prompt]`，
       吃不到 safe-mode / 禁工具那批 flag，等于每次白付 23.4k 新写缓存。
    ② 降档 Sonnet 5。评论链路是照 6 条硬要求写 80 字，判断难度与审核不在一个量级，
       而 C 组要把评论量放大一个数量级（每天 12 条外部 + 首评 + 读者回复）。
       ⚠️ Sonnet 弱在格式遵循：首评是纯文本、回复是严格三行，调用方的格式校验一条不能减。
    """
    if not CLAUDE.exists():
        return None, f"找不到 claude CLI：{CLAUDE}"
    sys.path.insert(0, str(REPO / "scripts"))
    from headless_cli import SONNET, build_argv, ensure_cwd
    try:
        r = subprocess.run(build_argv(CLAUDE, prompt, model=SONNET),
                           cwd=str(ensure_cwd()),
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "claude 超时"
    out = (r.stdout or "").strip()
    if re.search(r"hit your (session|usage) limit|额度", out):
        return None, "撞额度，稍后再跑"
    if r.returncode != 0 or not out:
        return None, f"claude 失败：{(r.stderr or '')[:200]}"
    return out, ""


# 中文里夹半角标点，读着就像机器写的。实测 Sonnet 比 Opus 明显更爱用 `:` `,` `?`
# （2026-08-18 三篇复测，3/3 出现）。这种事不靠模型自觉 —— 落盘前机械归一化，
# 判据是「前一个字符是中文」，避免误伤 A/B、3:00 这类。
_HALF2FULL = {",": "，", ":": "：", ";": "；", "?": "？", "!": "！"}
_CJK = re.compile(r"[\u4e00-\u9fff]")


def normalize_punct(text: str) -> str:
    """半角标点转全角 —— 只在**紧邻中文**时转，且两侧都是数字时一律不动。

    只看前一个字符不够：「还是C:讲得清」的冒号前是 ASCII 字母、后面才是中文，
    照样是中文句子里的半角标点。两侧都是数字的（3:00、1,000）必须放过。
    """
    out = []
    for i, ch in enumerate(text):
        prev, nxt = text[i - 1] if i else "", text[i + 1] if i + 1 < len(text) else ""
        if (ch in _HALF2FULL and (_CJK.match(prev) or _CJK.match(nxt))
                and not (prev.isdigit() and nxt.isdigit())):
            out.append(_HALF2FULL[ch])
        else:
            out.append(ch)
    return "".join(out)


def read_ledger():
    if not LEDGER.exists():
        return []
    with LEDGER.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_ledger(rows):
    # 原子替换：这个脚本可能和 nightly_brief 并发跑，直接 open("w") 会留下
    # 一段「文件被截断、只写了一半」的窗口，读者这时读到的是残缺 CSV。
    tmp = LEDGER.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LEDGER_COLS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in LEDGER_COLS})
    tmp.replace(LEDGER)


# ── 子命令 first：首评草稿 ──────────────────────────────────────────────────

FIRST_PROMPT = """你在给一条已发布的小红书笔记写「首评」——由博主本人发在自己笔记下的第一条评论。

## 这条首评要解决的问题

这个账号 18 篇笔记合计 1992 次观看、**只有 6 条评论**（评论率 0.30%）。
最好的一篇 1050 观看、30 赞、39 收藏、**0 评论**。
收藏 > 点赞 > 评论 = 读者觉得「有用，存起来」然后就走了。

根因不是没有 CTA，是**CTA 的成本太高**。现在正文结尾长这样：
- 「你被打断过最难接的一次是哪句话？评论区说说。」
- 「把他当时那句原话发评论区，我帮你看他在试探哪一层。」
要读者回忆具体事件、组织一段话、还要把领导的原话公开贴出来。
话题又是被孤立、不想转正、下属不服管这种——公开发这些对读者有真实风险。

所以首评的任务是：**把评论成本降到打一个字**。

## 硬要求

1. 给出 2–4 个**编号选项**，让读者只需回一个字母或数字。
   选项必须来自这篇笔记正文里真实出现过的分类，不许现编。
2. 选项之间要真的互斥、且都像自己——读者能一眼认出「我是 B」。
   ⛔ 选项描述的是**读者的处境**，不是你打算给他的东西。回报只在第 3 条那句里说一次，
   不许拆进选项里。（2026-08-18 实测：Sonnet 两次都在这里偏，两次都把选项写成了回报，
   结果读者根本认不出自己是哪个字母。）
   ✅「A 抢着表忠心、B 他压你价、C 他全程在说你插不进话」
   ⛔「A 他追问期望薪资你怎么接、B 压价那刻怎么把范围推回去」
3. 承诺一个**具体回报**：回了这个字母能得到什么（对应那一种的第一句话怎么说 / 判据是什么）。
   不许承诺结果（「保过」「一定能」），不许引流付费。
4. 全长 ≤ 80 字。这是评论不是正文。
5. 口语，像人随手补一句，不像运营话术。禁止「宝子」「家人们」「绝绝子」。
6. 不出现任何身份头衔（前腾讯 / 资深 / 专家 / 总监）。

## 输出格式

只输出首评正文本身，不要解释、不要引号、不要 markdown。

---

笔记标题：{title}

笔记正文：
{body}
"""


def cmd_first(args):
    from case_entry import parse_draft

    names = []
    if args.draft:
        names = [args.draft]
    elif args.all_pending:
        names = [p.name for p in sorted(SUCAI.glob("成稿_*.md"))][-args.limit:]
    if not names:
        print("没指定成稿。用 --draft <文件名> 或 --all-pending。")
        return 1

    for name in names:
        p = SUCAI / name
        if not p.exists():
            cand = list(SUCAI.rglob(name))
            if not cand:
                print(f"⛔ 找不到 {name}")
                continue
            p = cand[0]
        d = parse_draft(p.read_text(encoding="utf-8"))
        title, body = (d.get("title") or "").strip(), (d.get("body") or "").strip()
        if not body:
            print(f"⛔ {p.name}：解析不出正文")
            continue
        print(f"\n▶ {title}")
        out, err = run_claude(FIRST_PROMPT.format(title=title, body=body[:2500]))
        if not out:
            print(f"  ⛔ {err}")
            continue
        # ⛔ 不能 split("\n")[0]。首评天然是多行 —— 「他在等的那件旧账，多半是这三种：」
        # 后面跟着 A/B/C 三个选项，只取第一行等于把选项全扔了，剩下一句没头没尾的话。
        # （2026-08-08 第一版就是这么错的。）只剥掉代码围栏和整体引号。
        first = re.sub(r"^```[a-z]*\n|\n```$", "", out.strip()).strip().strip('"“”')
        first = normalize_punct(first)
        n = len(first.replace("\n", ""))
        print(f"  首评草稿（{n} 字）：")
        print("".join(f"  │ {l}\n" for l in first.split("\n")), end="")
        out_path = SUCAI / "首评草稿" / f"{p.stem}.txt"
        out_path.parent.mkdir(exist_ok=True)
        out_path.write_text(first + "\n", encoding="utf-8")
        print(f"  已存 {out_path.relative_to(REPO)}")
    return 0


# ── 子命令 watch：评论监测 + 回复草稿 ───────────────────────────────────────

REPLY_PROMPT = """给小红书评论写 3 个回复候选，供博主本人挑一个发出去。

## 回复的目标不是「回应完」，是「让对话继续」

模板化回复（谢谢支持 / 欢迎交流）等于把话说死。好的回复要**留一个新问题**，
让这位读者还想再回一句，也让别的读者看见有话可说。

## 硬要求

1. 每条 ≤ 50 字，口语，像人随手回的。
2. 必须先**具体回应对方说的那件事**（引一个他提到的细节），再抛新问题。
   不许通用到换个评论也能用。
3. 抛的新问题要**低成本**：能用一个词或一个选项回答，不要求对方再写一段。
3b. ⛔ **不要顺着对方的做法往下聊**。如果他说的是明显不妥的路子（诅咒/报复同事、
   简历造假、背后使绊子），既不评价也不追问细节 —— 追问细节等于表态认同。
   把话拉回他真正的处境（他为什么走到这一步）和一句能用的说法。
   实测踩过：读者说「偷偷做仪式扎他，他现在经常请病假」，模型回的是
   「这招你还真敢用，他请假频率是每周还是隔阵子一次」—— 账号人设是职场表达，
   不是陪聊诅咒同事。
4. 不承诺结果（保过 / 一定能 / 肯定），不引流付费，不出现身份头衔。
5. 三条要真的不同——不同的切入角度，不是同一句话换说法。

## 输出格式

严格三行，每行一条，行首不要编号、不要引号、不要 markdown。

---

笔记标题：{title}

读者评论：{comment}
"""

LETTER_RE = re.compile(r"^\s*([A-Za-z]|[1-9]|[①②③④])\s*[.。、]?\s*$")

LETTER_PROMPT = """有位读者在你的小红书笔记下回了一个字母「{letter}」。

## 这不是一条普通评论，是**在兑现你的承诺**

你的笔记结尾放的是选项型 CTA：「回 A 我发你……回 B 我发你……」。
他回了字母，说明他认了其中一种处境，而且**在等你给出你答应过的那个东西**。
回一句「谢谢支持」等于当场失信，比不回更糟。

## 下面是你在那篇笔记的首评里给过的选项

{options}

## 硬要求

1. **先兑现**：直接给出「{letter}」那一类对应的那句话术或判据 —— 可以照抄着用的，
   不是「你可以试试更自信一点」这种。这是回复的主体。
2. 如果上面的选项里认不出「{letter}」对应哪一类（比如没找到对应的首评），
   就只回一句话：用一个具体问题请他补一句他的处境，好对上号。
   ⛔ 这种情况下**不要瞎编**一个 A 类答案。
3. ≤60 字，口语，像人随手回的。
4. 不承诺结果，不出现身份头衔，不引流。

## 输出格式

严格三行，每行一个候选，行首不要编号、不要引号。
"""


def first_comment_options():
    """把已生成的首评草稿里的选项汇总成一段，喂给 LETTER_PROMPT。

    ⚠️ 通知页拿不到笔记链接（条目里的 a 全指向评论者主页），所以**没法确知**
    这个字母是哪一篇笔记下的。折中：把所有首评草稿的选项都给模型，让它按字母对号；
    对不上就按第 2 条要求追问，而不是瞎编一个答案。
    """
    d = SUCAI / "首评草稿"
    if not d.exists():
        return "（没有已生成的首评草稿）"
    out = []
    for f in sorted(d.glob("*.txt"))[-8:]:
        out.append(f"— {f.stem.removeprefix('成稿_')}\n{f.read_text(encoding='utf-8').strip()}")
    return "\n\n".join(out) if out else "（没有已生成的首评草稿）"


LOGIN_HINT = """
⛔ www.xiaohongshu.com 没有登录态，读不到评论正文。

  创作后台（creator.xiaohongshu.com）的登录态**不通用**，而且它只给评论
  「数量」，不给评论内容 —— 已验证：笔记数据详情页有「评论数 177」，
  但页面上没有任何一条评论原文。

  需要你在 cdp-proxy 控制的那个 Chrome 里手动登录一次 www.xiaohongshu.com
  （扫码，一次就行，之后 cookie 会留着）。我不替你登录。

  登录后重跑：python3 draft_comments.py watch --probe
"""


def open_tab(url):
    return api("/new", url)["targetId"]  # v2.5.3 起 /new 改 POST body 传 URL


def logged_in(tid):
    return not bool(ev(tid, '(()=>/\\/login/.test(location.href))()'))


# 2026-08-18 在真页面上定的（登录后 probe 出来的，不是凭空写的）。
#
# ⛔ 旧代码写的是 /notification/comments —— **那个路径已经不存在了**，
# 实测直接跳 404（errorCode=-510000，把 comments 当成 noteId 去解析）。
# 现在的入口是 /notification，评论在「评论和@」这个 tab 下。
#
# ⚠️ 通知页**拿不到笔记链接**，条目里的 a 全指向评论者主页。
# 所以回复只能走条目自带的「回复」按钮就地回，不能靠拼 URL 跳到笔记页。
NOTIF_URL = "https://www.xiaohongshu.com/notification"
COMMENT_SELECTORS = {
    "tab": "评论和@",
    "item": "div.container",
    "user": 'a[href^="/user/profile"]',
    "time": "span.interaction-time",
    "text": "div.interaction-content",
    "reply_btn": "div.action-text",
}

CLICK_TAB_JS = ('(()=>{const el=[...document.querySelectorAll("span,div,li")]'
                '.find(e=>e.textContent.trim()==="%(tab)s"&&(e.offsetWidth||e.offsetHeight));'
                'if(el){(el.closest("li,div[class*=tab],a")||el).click();return "ok"}'
                'return "notfound"})()') % COMMENT_SELECTORS

SCRAPE_JS = ('(()=>{const out=[];'
             'document.querySelectorAll("%(item)s").forEach((c,i)=>{'
             'const t=c.querySelector("%(text)s"); if(!t) return;'
             'const u=[...c.querySelectorAll(\'%(user)s\')].find(a=>a.textContent.trim()), tm=c.querySelector("%(time)s");'
             'const hint=[...c.querySelectorAll("span")].map(e=>e.textContent.trim())'
             '.find(x=>/评论了你的笔记|回复了你的评论/.test(x))||"";'
             'out.push({idx:i,user:(u&&u.textContent.trim())||"",'
             'profile:(u&&u.getAttribute("href"))||"",'
             'time:(tm&&tm.textContent.trim())||"",kind:hint,text:t.textContent.trim()});});'
             'return JSON.stringify(out);})()') % COMMENT_SELECTORS


def scrape_comments(tid):
    """抓「评论和@」tab 下的评论。返回 list。"""
    ev(tid, CLICK_TAB_JS)
    for _ in range(15):
        time.sleep(2)
        if not ev(tid, 'document.querySelectorAll(".skeleton-item").length'):
            break
    raw = ev(tid, SCRAPE_JS)
    try:
        return json.loads(raw) if raw else []
    except ValueError:
        return []


REPLY_SELECTORS = {
    "input": "textarea.comment-input",
    "submit": "button.submit",
}

# React 受控组件：直接改 .value 不会触发 onChange，输入框看着有字、内部 state 还是空，
# 点发送等于发了个空评论。必须用原型上的 setter 再手动派发 input 事件。
FILL_JS = """(()=>{
  const el=document.querySelector("%(input)s");
  if(!el) return "no-input";
  const setter=Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,"value").set;
  setter.call(el, %(text)s);
  el.dispatchEvent(new Event("input",{bubbles:true}));
  el.dispatchEvent(new Event("change",{bubbles:true}));
  return JSON.stringify({placeholder:el.placeholder||"", value:el.value});
})()"""


def open_reply_box(tid, want_user):
    """点开第 want_user 那条评论的回复框。返回 (成功, 说明)。

    ⛔ 用 placeholder 回读校验是必须的：回复框的 placeholder 是「回复 <对方昵称>」，
    对不上就说明点开的是**别人那条** —— 把话回到陌生人的评论下，比不回严重得多。
    """
    r = ev(tid, '''(()=>{const cs=[...document.querySelectorAll("div.container")]
        .filter(e=>e.querySelector("div.interaction-content"));
        const c=cs.find(e=>{const a=[...e.querySelectorAll('a[href^="/user/profile"]')]
            .find(x=>x.textContent.trim()); return a&&a.textContent.trim()===%s;});
        if(!c) return "no-item";
        const b=[...c.querySelectorAll("div.action-text")].find(e=>e.textContent.trim()==="回复");
        if(!b) return "no-button";
        b.click(); return "ok";})()''' % json.dumps(want_user, ensure_ascii=False))
    if r != "ok":
        return False, f"找不到 {want_user} 那条评论的回复入口（{r}）"
    time.sleep(2)
    return True, ""


def fill_reply(tid, text):
    raw = ev(tid, FILL_JS % {"input": REPLY_SELECTORS["input"],
                             "text": json.dumps(text, ensure_ascii=False)})
    if raw == "no-input":
        return False, "回复框没出现"
    try:
        d = json.loads(raw)
    except (ValueError, TypeError):
        return False, f"回读失败：{raw}"
    return True, d


def submit_reply(tid):
    r = ev(tid, '''(()=>{const b=document.querySelector("%s");
        if(!b) return "no-submit";
        if(b.disabled) return "disabled";
        b.click(); return "ok";})()''' % REPLY_SELECTORS["submit"])
    time.sleep(3)
    if r != "ok":
        return False, r
    # 发出去之后输入框会被清空 —— 这是唯一能在页面上验证「真的发了」的信号
    left = ev(tid, '(()=>{const e=document.querySelector("%s");return e?e.value:"gone"})()'
              % REPLY_SELECTORS["input"])
    return (left in ("", "gone")), f"发送后输入框残留：{left!r}"


def cmd_watch(args):
    tid = open_tab(NOTIF_URL)
    time.sleep(6)
    if not logged_in(tid):
        print(LOGIN_HINT)
        return 2

    if args.probe:
        # ⛔ 选择器要在真页面上定出来，不能凭空写。凭空写出来的抓取脚本
        # 抓到 0 条时和「本来就没有新评论」长得一模一样 —— 又一个静默失败。
        print("=== 页面结构（用来定选择器）===")
        print(ev(tid, r'''(()=>{
          const t=(document.body.innerText||"").replace(/\s+/g," ");
          const cls={};
          document.querySelectorAll("*").forEach(e=>{
            const c=(e.className||"").toString();
            if(/comment|notice|notif|item|msg/i.test(c)&&e.offsetHeight>30)
              cls[c.slice(0,50)]=(cls[c.slice(0,50)]||0)+1;});
          return JSON.stringify({正文前400:t.slice(0,400),候选容器:cls},null,1);})()'''))
        print(f"\ntid={tid}（选择器定好后填进 COMMENT_SELECTORS 再跑不带 --probe 的）")
        return 0

    items = scrape_comments(tid)
    # ⛔ 抓到 0 条要当**故障**报，不能当「今天没有新评论」。这两种在日志里长得一模一样，
    # 而页面改版正是这么静默失效的 —— /notification/comments 那次就是（路径没了、跳 404）。
    if not items:
        print("⛔ 抓到 0 条。页面结构可能又变了 —— 跑 `watch --probe` 重新定选择器。"
              "不要当成「没有新评论」放过去。")
        return 1

    sys.path.insert(0, str(Path(__file__).parent))
    from outreach import read_ledger, append_ledger
    import scene_map

    ledger = read_ledger()
    seen = {(r.get("备注") or "") for r in ledger if r.get("战场") == "回复"}

    def key(x):
        return f"{x['user']}|{x['time']}|{x['text'][:30]}"

    fresh = [x for x in items if key(x) not in seen
             and x["text"] and x["text"] != "该评论已删除"]
    print(f"抓到 {len(items)} 条，新的 {len(fresh)} 条")
    if not fresh:
        return 0

    scenes = scene_map.load_scenes()
    for x in fresh[:args.limit]:
        print(f"\n▶ {x['user']}（{x['time']} · {x['kind']}）：{x['text'][:60]}")
        m_letter = LETTER_RE.match(x["text"])
        if m_letter:
            # 读者回了字母 = 在等你兑现首评里的承诺。走另一套 prompt，
            # 普通回复 prompt 遇到「A」只会说「没有具体信息，没法引用细节」。
            print("   （字母型回复 —— 读者在等首评承诺的那个东西）")
            prompt = LETTER_PROMPT.format(letter=m_letter.group(1),
                                          options=first_comment_options())
        else:
            prompt = REPLY_PROMPT.format(title=x["kind"], comment=x["text"][:400])
        out, err = run_claude(prompt)
        if not out:
            print(f"   ⛔ {err}")
            continue
        cands = [normalize_punct(l.strip().lstrip("123.、 "))
                 for l in out.strip().splitlines() if l.strip()][:3]
        for i, c in enumerate(cands, 1):
            print(f"   {i}. {c}")
        t = scene_map.tag(x["text"][:150], scenes)
        append_ledger({"时间": datetime.now().isoformat(timespec="seconds"),
                       "战场": "回复", "目标链接": x.get("profile", ""),
                       "对方原话": x["text"][:120],
                       "发出内容": cands[0] if cands else "",
                       "场景": (t or {}).get("场景", ""),
                       "概念": (t or {}).get("概念", ""),
                       "正文术语": "", "状态": "草稿", "存活校验": "",
                       "备注": key(x)})
    print("\n已写进评论台账（战场=回复）。发送走 C3 的「回复」按钮链路。")
    return 0


def cmd_reply(args):
    """把台账里状态=草稿的「回复」发出去。

    ⛔ 默认 --dry-run：点开回复框、填进去、**不点发送**，回读 placeholder 与 value。
    第一次真发之前必须先看这一步对不对 —— 回复框认错人的代价是把话回到陌生人评论下。
    """
    sys.path.insert(0, str(Path(__file__).parent))
    from outreach import read_ledger, write_ledger, breaker_on, next_gap

    on, why = breaker_on()
    if on:
        print(f"⛔ 熔断中：{why}")
        return 2

    ledger = read_ledger()
    todo = [(i, r) for i, r in enumerate(ledger)
            if r.get("战场") == "回复" and r.get("状态") == "草稿" and r.get("发出内容")]
    if not todo:
        print("没有待发的回复草稿。先跑 `watch`。")
        return 0

    tid = open_tab(NOTIF_URL)
    time.sleep(6)
    if not logged_in(tid):
        print(LOGIN_HINT)
        return 2

    def reload_notif():
        """每条回复前重新加载通知页。

        ⛔ 不能省。上一条的回复框不会自己关，第二条点「回复」时框还是上一个人的 ——
        实测第一次 dry-run 就撞上了：要回给 👼 的话，placeholder 还写着「回复 不妄」。
        靠 placeholder 回读能拦住，但拦住只是不发，链路仍然是断的。
        重新加载慢 10 秒，而回复量本来就是每天几条，值。
        """
        api(f"/navigate?target={tid}", NOTIF_URL)  # v2.5.3 起 /navigate 改 POST body 传 URL
        time.sleep(4)
        ev(tid, CLICK_TAB_JS)
        for _ in range(15):
            time.sleep(2)
            if not ev(tid, 'document.querySelectorAll(".skeleton-item").length'):
                return True
        return False

    sent = 0
    for i, r in todo[:args.limit]:
        reload_notif()
        user = (r.get("备注") or "").split("|")[0]
        text = r["发出内容"]
        print(f"\n▶ 回复 {user}：{text[:50]}")
        ok, err = open_reply_box(tid, user)
        if not ok:
            print(f"   ⛔ {err}")
            continue
        ok, d = fill_reply(tid, text)
        if not ok:
            print(f"   ⛔ {d}")
            continue
        ph = d.get("placeholder", "")
        # 回读校验：placeholder 必须是「回复 <这个人>」
        if user and user not in ph:
            print(f"   ⛔ 回复对象对不上：placeholder={ph!r}，期望包含「{user}」—— 不发，跳过")
            continue
        print(f"   ✓ 已填入（placeholder={ph!r}，回读 {len(d.get('value',''))} 字）")
        if args.dry_run:
            print("   [dry-run] 不点发送")
            continue
        ok, note = submit_reply(tid)
        r["状态"] = "已发送" if ok else "发送失败"
        r["时间"] = datetime.now().isoformat(timespec="seconds")
        r["备注"] = (r.get("备注") or "") + ("" if ok else f" · {note}")
        write_ledger(ledger)
        print(f"   {'✅ 已发送' if ok else '⛔ ' + note}")
        sent += ok
        if sent < len(todo[:args.limit]):
            g = min(next_gap(), 300)      # 回复是被动响应，间隔比外部评论短，但仍不规律
            print(f"   等 {g}s")
            time.sleep(g)
    print(f"\n本轮发出 {sent} 条")
    return 0


SELF_UID = "64cc5138000000002b009107"
SELF_PROFILE = f"https://www.xiaohongshu.com/user/profile/{SELF_UID}"


# 自己主页的 __INITIAL_STATE__ 里，每篇笔记是这个形状（2026-08-19 在真页面上确认）：
#   外层    {id, noteCard, index, exposed, ssrRendered, xsecToken}
#   noteCard {displayTitle, interactInfo, cover, noteId, time, xsecToken, type, user}
# 两层都带 xsecToken，取哪层都行。
MY_NOTES_JS = r"""(()=>{const st=window.__INITIAL_STATE__; if(!st) return "no-state";
  const seen=new Set(); const out=[]; const ids=new Set();
  const walk=(o,d)=>{ if(!o||typeof o!=="object"||d>10||seen.has(o)) return;
    seen.add(o);
    const nc = o.noteCard || (o.displayTitle ? o : null);
    const id = o.id || o.noteId || (nc && nc.noteId);
    const tok = o.xsecToken || (nc && nc.xsecToken);
    const title = nc && nc.displayTitle;
    if(id && tok && title && !ids.has(id)){ ids.add(id);
      out.push({id:String(id), title:String(title), token:String(tok)}); }
    for(const k of Object.keys(o)){ try{ walk(o[k],d+1);}catch(e){} } };
  walk(st,0);
  return JSON.stringify(out);})()"""


def my_notes(limit=80):
    """自己已发布的笔记：[{id, title, url}]，**url 带 xsec_token**。

    ⛔ 走 cdp 浏览器的主页 `__INITIAL_STATE__`，**不要改回 opencli**。
    不是因为 opencli 不好用（它能拿到同样的数据），是因为**同一账号的网页会话互斥**：
    opencli 吃日常 Chrome 的登录态，发评论吃 cdp profile 的，2026-08-19 实测在
    cdp 里扫码登录后日常 Chrome 当场被踢下线（主站和创作者中心一起掉）。
    「拿链接」和「发评论」分在两个浏览器里，就永远凑不齐同时在线的时刻。
    走这条路，取链接和发评论共用一个登录态，这个问题根本不存在。

    ⚠️ 台账里「`__INITIAL_STATE__` 有循环引用、拿不到 token」那条旧结论是**错的**，
    它是在**未登录**状态下扫出来的（那时全树只有 1 处 xsec 字段）。登录之后同一
    棵树上有 308 处，id 和 token 与 opencli 返回的逐字一致。循环引用不是问题 ——
    用 Set 记访问过的对象即可，别用 JSON.stringify 整棵树。

    ⛔ 拿不到就抛，**绝不返回空列表**：空列表和「今天没有新笔记」在日志里长得
    一模一样，这正是这条链路反复静默失效的方式。
    """
    tid = open_tab(SELF_PROFILE)
    try:
        time.sleep(12)
        url = ev(tid, "location.href") or ""
        if "/login" in url:
            raise RuntimeError("主页跳了登录页 —— cdp profile 的登录态掉了，"
                               "在那个 Chrome 窗口重新扫码（scripts/xhs-comment/show_login.py）")
        raw = ev(tid, MY_NOTES_JS)
        if raw == "no-state":
            raise RuntimeError("主页没有 __INITIAL_STATE__ —— 页面结构变了，重新 probe")
        try:
            notes = json.loads(raw or "[]")
        except ValueError:
            raise RuntimeError(f"__INITIAL_STATE__ 提取结果不是 JSON：{str(raw)[:200]}")
    finally:
        try:
            api("/close?target=" + tid)
        except Exception:                                   # noqa: BLE001
            pass
    if not notes:
        raise RuntimeError("主页一篇笔记都没抓到 —— 按故障处理，不当成「没有新笔记」")
    for n in notes:
        n["url"] = (f"https://www.xiaohongshu.com/explore/{n['id']}"
                    f"?xsec_token={up.quote(n['token'])}&xsec_source=pc_user")
    return notes[:limit]


def _note_id(url: str) -> str:
    """从任意一种 URL 形式里抠出 noteId（24 位十六进制）。

    台账里历史记录有两种形式：早期的 `/explore/<id>` 和现在 opencli 给的
    `/user/profile/<uid>/<id>?xsec_token=`。⛔ 去重必须按 id，按整条 URL 去重
    会把同一篇认成两篇 → **重复发首评**，而重复评论正是「评论区配合」最像机器的特征。
    """
    ids = re.findall(r"[0-9a-f]{24}", url or "")
    return ids[-1] if ids else ""


def drafts_by_title():
    """成稿 H1 → 成稿路径。首评要发给哪篇笔记，靠标题对上号。"""
    out = {}
    for f in list(SUCAI.glob("成稿_*.md")) + list((SUCAI / "归档稿").glob("成稿_*.md")):
        first = f.read_text(encoding="utf-8").splitlines()[0] if f.stat().st_size else ""
        h1 = first.lstrip("# ").strip()
        if h1:
            out.setdefault(h1, f)
    return out


def cmd_first_send(args):
    """给已发布、还没发过首评的笔记自动发首评。

    ⛔ 时机上不能挂在 auto_publish 后面：那条链路是**定时发布**（排到未来 17:00/20:00），
    发布动作完成时笔记还不存在，评论无从发起。所以这里改成扫自己主页，
    看哪篇已经真的挂出去了、且台账里没记过首评。
    """
    sys.path.insert(0, str(Path(__file__).parent))
    from outreach import (read_ledger, append_ledger, post_comment,
                          breaker_on, trip_breaker, COOLDOWN_HOURS)

    on, why = breaker_on()
    if on:
        print(f"⛔ 熔断中：{why}")
        return 2

    try:
        notes = my_notes()
    except RuntimeError as e:
        print(f"⛔ 拿不到笔记列表：{e}")
        return 2

    ledger = read_ledger()
    done = {_note_id(r.get("目标链接", "")) for r in ledger if r.get("战场") == "首评"}
    done.discard("")
    by_title = drafts_by_title()

    todo = []
    for n in notes:
        if n["id"] in done:
            continue
        title = (n.get("title") or "").strip()
        d = by_title.get(title)
        if d:
            todo.append((n["url"], title, d))
    print(f"主页 {len(notes)} 篇 · 能对上成稿且没发过首评的 {len(todo)} 篇")
    if not todo:
        return 0

    sent = 0
    for link, title, draft in todo[:args.limit]:
        print(f"\n▶ {title[:34]}")
        txt_path = SUCAI / "首评草稿" / f"{draft.stem}.txt"
        if not txt_path.exists():
            print("   · 没有首评草稿，现生成")
            from case_entry import parse_draft
            d = parse_draft(draft.read_text(encoding="utf-8"))
            out, err = run_claude(FIRST_PROMPT.format(title=d.get("title", ""),
                                                      body=(d.get("body") or "")[:2500]))
            if not out:
                print(f"   ⛔ {err}")
                continue
            first = normalize_punct(re.sub(r"^```[a-z]*\n|\n```$", "", out.strip()).strip())
            txt_path.parent.mkdir(exist_ok=True)
            txt_path.write_text(first + "\n", encoding="utf-8")
        text = txt_path.read_text(encoding="utf-8").strip()
        print(f"   首评（{len(text.replace(chr(10),''))} 字）：{text[:60]}")
        if args.dry_run:
            print("   [dry-run] 不发送")
            continue
        # 发布后隔一会儿再评论 —— 「发布即首评」是很显眼的机器特征
        wait = random.randint(args.delay_min, args.delay_max)
        print(f"   等 {wait}s 再发（避开发布即评论的特征）")
        time.sleep(wait)
        ok, note = post_comment(link, text)
        append_ledger({"时间": datetime.now().isoformat(timespec="seconds"),
                       "战场": "首评", "目标链接": link, "对方原话": "",
                       "发出内容": text, "场景": "", "概念": "", "正文术语": "",
                       "状态": "已发送" if ok else "发送失败",
                       "存活校验": "", "备注": note})
        print(f"   {'✅ ' if ok else '⛔ '}{note}")
        sent += ok
        if not ok and note.startswith("LOGIN:"):
            print("   ⛔ 停在这里但**不熔断** —— 登录态问题，重新扫码就能继续")
            break
        if not ok and note.startswith("RISK:"):
            trip_breaker(note)
            print(f"   ⛔⛔ 撞风控，熔断 {COOLDOWN_HOURS} 小时")
            break
    print(f"\n发出 {sent} 条首评")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("first", help="生成首评草稿")
    f.add_argument("--draft", help="成稿文件名")
    f.add_argument("--all-pending", action="store_true", help="最近若干篇成稿")
    f.add_argument("--limit", type=int, default=5)

    w = sub.add_parser("watch", help="监测新评论并生成回复草稿")
    w.add_argument("--probe", action="store_true", help="只检查登录态并导出页面结构")
    w.add_argument("--limit", type=int, default=5, help="最多为几条评论生成回复候选")

    fs = sub.add_parser("first-send", help="给已发布、还没发过首评的笔记自动发首评")
    fs.add_argument("--limit", type=int, default=2)
    fs.add_argument("--dry-run", action="store_true", default=True)
    fs.add_argument("--send", dest="dry_run", action="store_false")
    fs.add_argument("--delay-min", type=int, default=180)
    fs.add_argument("--delay-max", type=int, default=900)

    rp = sub.add_parser("reply", help="把台账里的回复草稿发出去（默认 dry-run）")
    rp.add_argument("--limit", type=int, default=3)
    rp.add_argument("--dry-run", action="store_true", default=True)
    rp.add_argument("--send", dest="dry_run", action="store_false",
                    help="真的点发送（默认只填不发）")

    a = ap.parse_args()
    return {"first": cmd_first, "watch": cmd_watch, "reply": cmd_reply,
            "first-send": cmd_first_send}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
