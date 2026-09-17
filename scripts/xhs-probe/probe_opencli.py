#!/usr/bin/env python3
"""probe 的 opencli 采集后端 —— 走 XHS 专用 Chromium 的登录态。

## 为什么另起一个后端而不是修 probe.py

2026-08-12 实测：probe.py 走 CDP Proxy 连的是采集专用 profile（9333,
`~/.xhs-chrome-profile`），那个 profile 的 `www.xiaohongshu.com` 只有游客 cookie
（a1/webId/gid，**没有 web_session**）。表现是 explore 首页能看到 34 张卡片、
搜索页却直接是「登录后查看搜索结果」。

于是当天两次采集分别报了「连续 2 个词返回空结果，判定被限流」和
「触发安全验证」—— 两条都是误报，真因就是没登录。CAPTCHA_RE 命中的是
登录墙文案里的「验证码」三个字（手机号登录框那个），不是真的风控验证。

opencli 走 Browser Bridge 附着 XHS 专用 Chromium（`com.eric.xhschrome`，
`~/.xhs-chromium-profile`，2026-08-21 起与日常 Chrome 分离），该 profile
已登录主站+创作者中心，不存在这个问题。CLAUDE.md「联网抓取规则」已把
opencli 定为第 0 优先级。

## 判断权边界（与 probe.py 一致，不得放宽）

density 仍由 probe.judge_density() 这套确定性规则算，本文件只负责取数。
取数方式换了，判据不能跟着换 —— 否则新旧两批 probe 结果不可比。

用法：
  python3 probe_opencli.py --resume            # 续跑 .probe_state.json 里剩下的词
  python3 probe_opencli.py "关键词1" "关键词2"
  python3 probe_opencli.py --limit 3 --from-cikuku
"""
import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from knobs import K  # noqa: E402


sys.path.insert(0, str(Path(__file__).resolve().parent))
import probe  # noqa: E402  复用 parse_likes / judge_density / slug / write_result

OUT_DIR = probe.OUT_DIR
STATE_FILE = probe.STATE_FILE

TOP_N = K("PROBE_TOP_N")          # 搜索位取样条数，与 probe.py 的 22 条量级对齐
DEEP_N = K("PROBE_DEEP_N")          # 深挖正文+评论的条数（与 probe.py 的 note_bodies/engage_samples 一致）
COMMENT_LIMIT = K("PROBE_COMMENT_LIMIT")  # 单篇取几条评论
OC_TIMEOUT = 180
GAP = K("PROBE_GAP")           # 每次 opencli 调用之间的间隔，别把日常 Chrome 打成风控目标


@lru_cache(maxsize=1)
def opencli_bin() -> str:
    """解析 opencli 的可执行路径，不依赖调用者的 PATH 干不干净。

    ⛔ 2026-08-13：这里原先写死裸名 "opencli"，靠 PATH 解析。launchd 给的 PATH 只有
    /usr/bin:/bin:/usr/sbin:/sbin，于是**手动跑必通、定时跑必挂** ——
    当天三轮探测全部 0 条，每个 probe_*.json 里都是
    「[Errno 2] No such file or directory: 'opencli'」，而 daily_probe.sh 把失败
    吞成一句 echo、brief 照报「✅ 采集探测 退出 0」，挂了一整天没人发现。

    launchd_runner.py 已经在 runner 那层把 PATH 补齐（覆盖所有定时任务），
    这里是第二道：直接调这个脚本、或者从任何 PATH 不全的地方调用时也不会再踩。
    找不到就抛 —— 静默返回 None 会被上层当成「这条词没数据」，
    和「工具压根没装」混为一谈，正是这次查了半天的原因。
    """
    p = shutil.which("opencli")
    if p:
        return p
    for c in ("/opt/homebrew/bin/opencli", "/usr/local/bin/opencli",
              str(Path.home() / ".local/bin/opencli"),
              str(Path.home() / ".bun/bin/opencli")):
        if os.access(c, os.X_OK):
            return c
    raise FileNotFoundError(
        "找不到 opencli。装了的话是 PATH 问题（launchd 的 PATH 不含 /opt/homebrew/bin）；"
        "没装就跑 npm i -g @jackwener/opencli")


# ── oc() 的失败原因（2026-09-17 加）────────────────────────────────────────────
#
# ⛔ 起因：2026-09-12 起采集连续 15 轮（5 天 × 3 轮）全部 0 条，日志每轮都写
# 「0 条（搜索无结果或登录态失效）」、收尾写「多半是登录态失效」。
# 真实原因是 **Browser Bridge 扩展没连上**（opencli doctor: Extension not connected），
# 跟登录态毫无关系 —— 重启专用 Chromium 就好了。查错的人被日志指向了错误的方向。
#
# 根因就在下面这个函数：它把**所有**失败都压成 None ——
#   扩展断连 / 登录失效 / 超时 / opencli 崩了 / 真的 0 条结果，调用方一律看到 None。
# 信息在这里被丢掉，后面任何一层都补不回来。
#
# ⚠️ 还有一层坑：opencli 的**错误输出即使加了 `-f json` 也是 YAML**
# （`ok: false` / `error:` / `  code: BROWSER_CONNECT`），所以 json.loads 直接抛
# JSONDecodeError，连 `d.get("ok") is False` 那个分支都走不到 —— 那行代码
# 从来没有生效过。现在从文本里把 code 抠出来。
LAST_ERROR: dict = {}

# 基础设施故障：重试没有意义，整轮应该立刻停手（继续跑只是白烧 8-20 分钟）
INFRA_CODES = {"BROWSER_CONNECT", "DAEMON", "DAEMON_NOT_RUNNING", "BROWSER_NOT_FOUND"}
# 登录态问题：需要人扫码，不是脚本能自愈的
AUTH_CODES = {"AUTH_REQUIRED", "LOGIN_REQUIRED", "AUTH"}

_ERR_CODE_RE = re.compile(r"^\s*code:\s*([A-Z_]+)\s*$", re.M)


def _record_error(kind: str, code: str = "", msg: str = ""):
    LAST_ERROR.clear()
    LAST_ERROR.update({"kind": kind, "code": code, "message": msg})


def oc(args, timeout=OC_TIMEOUT):
    """调 opencli 并解析 JSON。失败返回 None，**失败原因记进 LAST_ERROR**。

    LAST_ERROR["kind"] 取值：infra / auth / empty / parse / crash / timeout。
    调用方该怎么用：infra 和 auth 立刻停整轮（见 preflight），其余才是「这条词没数据」。
    """
    try:
        r = subprocess.run([opencli_bin(), "xiaohongshu", *args, "-f", "json"],
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        _record_error("timeout", msg=f"opencli {' '.join(args[:2])} 超过 {timeout}s")
        return None
    out = (r.stdout or "").strip()
    if not out:
        _record_error("crash", msg=(r.stderr or "").strip()[:200] or "opencli 无输出")
        return None
    try:
        d = json.loads(out)
    except json.JSONDecodeError:
        # 错误分支走的就是这里 —— opencli 的错误是 YAML，不是 JSON
        m = _ERR_CODE_RE.search(out)
        code = m.group(1) if m else ""
        kind = ("infra" if code in INFRA_CODES else
                "auth" if code in AUTH_CODES else "parse")
        _record_error(kind, code, out[:200])
        return None
    if isinstance(d, dict) and d.get("ok") is False:
        err = d.get("error") or {}
        code = str(err.get("code") or "")
        kind = ("infra" if code in INFRA_CODES else
                "auth" if code in AUTH_CODES else "parse")
        _record_error(kind, code, str(err.get("message") or "")[:200])
        return None
    _record_error("empty" if not d else "", "")
    return d


# preflight 的探针词。⛔ 不要写死一个 —— 每天固定用同一个词去探，本身就是可识别的
# 特征，而这个账号的整条链路对主站风控相当敏感（2026-08-16 因此降过频）。
# 选的都是搜索量大、跟本账号赛道无关紧要的通用词，探不到就是真有问题。
_PREFLIGHT_WORDS = ["面试", "职场", "offer", "简历", "跳槽", "加薪"]


def preflight() -> tuple[bool, str]:
    """开跑前确认这条链是通的。通 → (True, "")；不通 → (False, 该怎么修)。

    ⛔ **不用 `opencli auth status`**：CLAUDE.md 明写它只做 quick check（看 cookie 在不在），
    实测出现过它报 logged_in: true 而真实请求直接 AUTH_REQUIRED。判据必须是真实命令。

    放在每轮开头跑一次，成本是一次 search（几秒），换掉的是「8 个词各跑一遍、
    20 分钟之后才发现整条链断了」，而且报的原因还是错的。
    """
    word = random.choice(_PREFLIGHT_WORDS)
    hits = oc(["search", word, "--limit", "1"])
    if hits:
        return True, ""
    kind = LAST_ERROR.get("kind", "")
    code = LAST_ERROR.get("code", "")
    if kind == "infra":
        return False, (
            f"⛔ 浏览器桥断了（{code or 'BROWSER_CONNECT'}）—— 不是登录态问题，别去扫码。\n"
            "   查：opencli doctor\n"
            "   修：launchctl kickstart -k gui/$(id -u)/com.eric.xhschrome"
            "（重启 XHS 专用 Chromium，cookie 在 profile 里不会掉）\n"
            "   还不行再：opencli daemon restart")
    if kind == "auth":
        return False, (
            f"⛔ 登录态失效（{code}）—— 需要人扫码，脚本自愈不了。\n"
            "   跑：opencli xiaohongshu login（会开登录页等扫码，先告知 Eric）\n"
            "   注意主站与创作者中心 cookie 相互独立，按需分别验。")
    if kind == "timeout":
        return False, f"⛔ opencli 超时：{LAST_ERROR.get('message', '')}"
    if kind in ("crash", "parse"):
        return False, (f"⛔ opencli 异常（{kind}）：{LAST_ERROR.get('message', '')[:160]}\n"
                       "   先跑 opencli doctor 看是哪一环。")
    # 链路通、真的搜不到东西 —— 这才是风控该考虑的那种情况。
    # ⚠️ 注意这跟「浏览器桥断了」是两回事：桥是好的，是站点不给数据。
    # 调用方报警时别把这两种混成一句话（health_check 第一版就混了）。
    return False, (f"⚠️ 风控：浏览器桥正常、创作者中心多半也正常，但主站搜「{word}」"
                   f"这种通用词返回 0 条。\n"
                   "   参考 2026-08-16 那次：降频错峰，别继续投，硬投一整天都会空。\n"
                   "   （连续密集请求最容易触发 —— 刚手动验证过就跑采集尤其容易撞上。）")


def note_id_of(url: str) -> str:
    m = re.search(r"/(?:explore|search_result|discovery/item)/([0-9a-f]{24})", url or "")
    return m.group(1) if m else ""


def fields_to_dict(rows):
    """opencli note 返回 [{"field": "...", "value": "..."}]，压成 dict。"""
    if isinstance(rows, dict):
        return rows
    out = {}
    for r in rows or []:
        if isinstance(r, dict) and "field" in r:
            out[r["field"]] = r.get("value")
    return out


def collect(keyword: str) -> dict:
    """采一个词。返回与 probe.py 同结构的 dict。"""
    res = {
        "keyword": keyword,
        "probed_at": datetime.now().isoformat(timespec="seconds"),
        "source": "opencli",
        "completeness": "partial",
        "_error": None,
        "note_count": None,
        "autocomplete": [],
        "top_notes": [],
        "note_bodies": [],
        "engage_samples": [],
        "comments": [],
    }

    hits = oc(["search", keyword, "--limit", str(TOP_N)])
    if not hits:
        res["completeness"] = "failed"
        res["_error"] = "search_empty"
        res["density"] = probe.judge_density([], keyword)
        return res

    for h in hits:
        url = h.get("url") or ""
        res["top_notes"].append({
            "rank": h.get("rank"),
            "note_id": note_id_of(url),
            "href": url,
            "title": h.get("title") or "",
            "author": h.get("author") or "",
            "cover": h.get("cover") or "",
            "likes": probe.parse_likes(h.get("likes")),
            "published_at": h.get("published_at") or "",
            "url": url,
        })

    res["density"] = probe.judge_density(res["top_notes"], keyword)

    # 深挖：按点赞从高到低取前 DEEP_N，拿正文与评论。
    # 用 search 原样返回的 signed URL —— note/comments 要求带 xsec_token，
    # 自己按 note_id 拼的裸 URL 会被 ARGUMENT 拒掉。
    ranked = sorted(res["top_notes"],
                    key=lambda n: (n["likes"] is None, -(n["likes"] or 0)))[:DEEP_N]
    for n in ranked:
        time.sleep(GAP)
        d = fields_to_dict(oc(["note", n["url"]]))
        if d:
            res["note_bodies"].append({
                "note_id": n["note_id"],
                "note_url": n["url"],
                "title": d.get("title") or n["title"],
                "likes": probe.parse_likes(d.get("likes")) or n["likes"],
                "body": d.get("content") or "",
                "tags": re.findall(r"#([^\s#]+)", d.get("content") or ""),
            })
            res["engage_samples"].append({
                "note_id": n["note_id"],
                "rank": n["rank"],
                "title": n["title"],
                "likes_from_card": n["likes"],
                "like_raw": d.get("likes"),
                "collect_raw": d.get("collects"),
                "comment_raw": d.get("comments"),
                "comment_total_raw": d.get("comments"),
                "body_len": len(d.get("content") or ""),
                "engage_bar_raw": "opencli:note",
            })

        time.sleep(GAP)
        cms = oc(["comments", n["url"], "--limit", str(COMMENT_LIMIT)])
        got = 0
        for c in (cms or []):
            txt = (c.get("text") or "").strip()
            if txt:
                res["comments"].append({
                    "note_id": n["note_id"],
                    "note_url": n["url"],
                    "text": txt,
                })
                got += 1
        # CDP 版的 dom_comments 是页面上数出来的可见评论数；这里等价物是本次实际取到的条数。
        if res["engage_samples"] and res["engage_samples"][-1]["note_id"] == n["note_id"]:
            res["engage_samples"][-1]["dom_comments"] = got

    if res["top_notes"] and res["note_bodies"] and res["comments"]:
        res["completeness"] = "full"
    return res


def load_keywords(args):
    if args.resume:
        if not STATE_FILE.exists():
            print("无中断状态可续跑", file=sys.stderr)
            return []
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))["remaining"]
    if args.from_cikuku:
        return probe.load_pending(args.limit)
    return args.keyword


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("keyword", nargs="*")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--from-cikuku", action="store_true")
    ap.add_argument("--limit", type=int, default=5)
    args = ap.parse_args()

    keywords = load_keywords(args)
    if not keywords:
        print("没有待探测关键词", file=sys.stderr)
        return 1

    # ── 开跑前先确认链路（2026-09-17 加，与 daily_collect 同一道闸）─────────────
    # ⛔ 起因：09-16 那轮 5 个词全 failed，每条都写「笔记=0 正文=0 评论=0」
    # 「有效点赞样本仅 0 条」，而链路日志收尾还写着「小红书=正常」。
    # 真因跟采集是同一个：Browser Bridge 扩展没连上。
    # 不先验链路的话，每个 failed 的词都会**落一份空的 probe_*.json**，
    # 那些空文件后面会被当成「这个词探过了、没数据」，污染选词判据。
    ok, why = preflight()
    if not ok:
        print(why, file=sys.stderr)
        print("本轮不探测 —— 链路不通时每个词都会落一份空 probe_*.json，"
              "而空结果会被下游当成「探过了，没数据」，污染选词。", file=sys.stderr)
        return 1

    today = date.today().strftime("%Y%m%d")
    done, failed = [], []
    for i, kw in enumerate(keywords):
        print(f"[{i+1}/{len(keywords)}] {kw}", flush=True)
        try:
            r = collect(kw)
        except Exception as e:
            r = {"keyword": kw, "completeness": "failed", "_error": str(e),
                 "probed_at": datetime.now().isoformat(timespec="seconds"),
                 "source": "opencli"}

        path = OUT_DIR / f"probe_{today}_{probe.slug(kw)}.json"
        path, kept = probe.write_result(path, r)
        d = r.get("density", {})
        if kept:
            print(f"    ⚠️ 已有 full 结果，本次{r['completeness']}不覆盖 → 另存 {path.name}",
                  file=sys.stderr, flush=True)
        print(f"    → {r['completeness']} | 密度={d.get('verdict','—')} | "
              f"笔记={len(r.get('top_notes',[]))} 正文={len(r.get('note_bodies',[]))} "
              f"评论={len(r.get('comments',[]))} | {path.name}", flush=True)
        if d.get("reason"):
            print(f"      {d['reason']}", flush=True)

        (done if r["completeness"] != "failed" else failed).append(kw)
        remaining = keywords[i + 1:]
        if remaining:
            STATE_FILE.write_text(json.dumps({"remaining": remaining}, ensure_ascii=False),
                                  encoding="utf-8")
        elif STATE_FILE.exists():
            STATE_FILE.unlink()

    print(f"\n完成 {len(done)}/{len(keywords)}" + (f"，失败 {len(failed)}" if failed else ""))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
