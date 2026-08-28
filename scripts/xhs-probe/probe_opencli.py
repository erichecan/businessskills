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
import re
import shutil
import subprocess
import sys
import time
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import probe  # noqa: E402  复用 parse_likes / judge_density / slug / write_result

OUT_DIR = probe.OUT_DIR
STATE_FILE = probe.STATE_FILE

TOP_N = 20          # 搜索位取样条数，与 probe.py 的 22 条量级对齐
DEEP_N = 5          # 深挖正文+评论的条数（与 probe.py 的 note_bodies/engage_samples 一致）
COMMENT_LIMIT = 12  # 单篇取几条评论
OC_TIMEOUT = 180
GAP = 2.5           # 每次 opencli 调用之间的间隔，别把日常 Chrome 打成风控目标


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


def oc(args, timeout=OC_TIMEOUT):
    """调 opencli 并解析 JSON。失败返回 None（由调用方决定降级还是抛）。"""
    r = subprocess.run([opencli_bin(), "xiaohongshu", *args, "-f", "json"],
                       capture_output=True, text=True, timeout=timeout)
    out = (r.stdout or "").strip()
    if not out:
        return None
    try:
        d = json.loads(out)
    except json.JSONDecodeError:
        return None
    # 错误形态：{"ok": false, "error": {...}}
    if isinstance(d, dict) and d.get("ok") is False:
        return None
    return d


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
