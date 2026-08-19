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
4. 不承诺结果（保过 / 一定能 / 肯定），不引流付费，不出现身份头衔。
5. 三条要真的不同——不同的切入角度，不是同一句话换说法。

## 输出格式

严格三行，每行一条，行首不要编号、不要引号、不要 markdown。

---

笔记标题：{title}

读者评论：{comment}
"""

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
    return api("/new?url=" + up.quote(url, safe=""))["targetId"]


def logged_in(tid):
    return not bool(ev(tid, '(()=>/\\/login/.test(location.href))()'))


def cmd_watch(args):
    tid = open_tab("https://www.xiaohongshu.com/notification/comments")
    time.sleep(9)
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

    print("⛔ 抓取选择器还没在真页面上定过。先跑 --probe，"
          "把结构贴给我，我据此填 COMMENT_SELECTORS，再跑这条。")
    print("   （不先定就写选择器 = 抓到 0 条和「没有新评论」长得一样，"
          "正是这条链路反复出问题的方式。）")
    return 1


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

    a = ap.parse_args()
    return cmd_first(a) if a.cmd == "first" else cmd_watch(a)


if __name__ == "__main__":
    sys.exit(main())
