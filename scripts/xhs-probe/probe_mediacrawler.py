#!/usr/bin/env python3
"""probe 的 MediaCrawler 采集后端 —— 替掉被小红书认出来的 opencli search。

## 为什么换这个后端

2026-09-22 现场排查（三次独立 opencli search 实测，含强制开新标签页）钉死：opencli
的 xiaohongshu search adapter 把请求写死用 `source=web_search_result_notes`，这个
参数值全世界装这个工具的人搜任何词都一样，已经被小红书的风控当成自动化签名——
不管账号、不管 tab、不管等多久，一碰这个 URL 就跳验证码墙。opencli 官方自己的
`opencli-autofix` 排障手册也把"验证码/限流"列为硬停止线："not an adapter issue"，
不该打补丁、不该指望它出新版本修。

MediaCrawler（github.com/NanmiCoder/MediaCrawler）走的是完全不同的路子：直接调用
小红书网页自己用的搜索接口，不构造那种会暴露身份的 URL。2026-09-22 同一账号、同一个
xhschrome（连验证码都还没解掉的那个会话）用它搜「offer」一次成功，20 条真实结果，
全程无验证码。

## 判断权边界（与 probe.py / probe_opencli.py 一致，不得放宽）

density 仍由 probe.judge_density() 这套确定性规则算，本文件只负责取数。

## 请求量克制（两步走，不是图省事）

第一步广搜 TOP_N 条、不带评论，只为够 judge_density() 的采样量（需要 ≥8 个有效点赞
样本）；第二步只对点赞最高的 DEEP_N 篇用 `--type detail --specified_id` 精确取正文
+评论，不重新广搜一次全量。比"广搜时顺手把 TOP_N 条评论全抓了"省了 (TOP_N-DEEP_N)
倍的请求量——License 和常识都要求"合理控制请求频率，不搞大规模爬取"，这不是可选项。

## 部署位置

MediaCrawler 装在仓库外面（`~/.mediacrawler`），当成跟 opencli 一样的外部工具，
不vendor进这个仓库——它是独立的 uv/Playwright 项目，装在项目里不合适，也没必要
把它的版本升级绑定到这个仓库的 git 历史上。

用法：
  python3 probe_mediacrawler.py --resume            # 续跑 .probe_state.json 里剩下的词
  python3 probe_mediacrawler.py "关键词1" "关键词2"
  python3 probe_mediacrawler.py --limit 3 --from-cikuku
"""
import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from knobs import K  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import probe  # noqa: E402  复用 parse_likes / judge_density / clean_comment / slug / write_result

OUT_DIR = probe.OUT_DIR
STATE_FILE = probe.STATE_FILE

TOP_N = K("PROBE_TOP_N")            # 广搜取样条数，与 opencli 版本对齐
DEEP_N = K("PROBE_DEEP_N")          # 深挖正文+评论的条数
COMMENT_LIMIT = K("PROBE_COMMENT_LIMIT")  # 单篇取几条评论
MC_TIMEOUT = 180

MEDIACRAWLER_DIR = Path.home() / ".mediacrawler"

LAST_ERROR: dict = {}


def _record_error(kind: str, msg: str = ""):
    LAST_ERROR.clear()
    LAST_ERROR.update({"kind": kind, "code": "", "message": msg})


def _uv_bin() -> str:
    """解析 uv 的可执行路径，不依赖调用者的 PATH 干不干净。

    ⛔ 这条教训是从 opencli_bin() 抄来的：2026-08-13 那次 launchd 的 PATH 只有
    /usr/bin:/bin:/usr/sbin:/sbin，裸名解析在定时任务里必挂、手动跑必通，
    查了半天才发现。uv 装在 ~/.local/bin，同样不在 launchd 的默认 PATH 里。
    """
    p = shutil.which("uv")
    if p:
        return p
    for c in (str(Path.home() / ".local/bin/uv"), "/opt/homebrew/bin/uv", "/usr/local/bin/uv"):
        if os.access(c, os.X_OK):
            return c
    raise FileNotFoundError("找不到 uv。装了的话是 PATH 问题；没装就跑 curl -LsSf "
                            "https://astral.sh/uv/install.sh | sh")


# ── 开跑前用 opencli 探一次真实登录态（2026-09-25 加）──────────────────────────
#
# ⛔ 起因：daily_collect/daily_probe 是 launchd 定时任务，没人盯着。MediaCrawler
# 自己的 pong() 登录检测这次是准的（主站+创作者中心确实都 AUTH_REQUIRED），但它
# 判定没登录之后直接调 PIL 的 Image.show()（~/.mediacrawler/tools/crawler_util.py
# show_qrcode()）把二维码解码成临时 PNG 丢给系统看图工具弹出来，然后进 retry 循环
# 等最多 120-600 秒的扫码——launchd 任务里没人会去扫。一天 3 轮 collect + 2 轮
# probe，每轮开头 preflight() 都会独立起一次 MediaCrawler 子进程，登录没恢复之前
# 等于每天好几次「无声无息弹窗、等到 180s 子进程超时被杀、报 MediaCrawler 超时」——
# 这正是 2026-09-25 15:00/20:30 两轮日志里 timeout 的真实来源，不是频率限制。
#
# opencli 的 AUTH_REQUIRED 判断在 2026-09-17 那次事故后就是验证过的真实信号（不是
# `opencli auth status` 那种会说谎的 quick check），借用它在触发 MediaCrawler 前
# 先拦一道：真掉线就直接报 auth、不再启动 MediaCrawler，把「要不要弹窗等人扫码」
# 这个决定交还给人，而不是让它对着一个没人看的 cron 会话自作主张弹一次。
def _opencli_auth_required() -> bool:
    """用一次轻量 opencli 探测判断是不是真的没登录。探测本身失败不当作没登录——
    那种情况交给下面 MediaCrawler 自己的 pong() 判断，避免这道闸门本身变成新的误判源。
    """
    try:
        import probe_opencli
        r = subprocess.run([probe_opencli.opencli_bin(), "xiaohongshu", "search",
                           "测试", "--limit", "1", "-f", "json"],
                          capture_output=True, text=True, timeout=30)
    except Exception:                                         # noqa: BLE001
        return False
    combined = (r.stdout or "") + (r.stderr or "")
    return "AUTH_REQUIRED" in combined or "LOGIN_REQUIRED" in combined


def _run(extra_args: list, timeout: int = MC_TIMEOUT):
    """跑一次 MediaCrawler。成功返回 (save_dir, True)，save_dir 用完调用方必须自己清理；
    失败返回 (None, False)，原因记在 LAST_ERROR。
    """
    if _opencli_auth_required():
        _record_error("auth", "opencli 探测到 AUTH_REQUIRED —— 登录态真的掉了，"
                              "不启动 MediaCrawler（避免它自己弹二维码等一个没人看的窗口）")
        return None, False
    save_dir = tempfile.mkdtemp(prefix="mc_probe_")
    cmd = [_uv_bin(), "run", "main.py",
           "--platform", "xhs", "--lt", "qrcode",
           "--save_data_option", "jsonl", "--save_data_path", save_dir,
           *extra_args]
    try:
        r = subprocess.run(cmd, cwd=str(MEDIACRAWLER_DIR), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        _record_error("timeout", f"mediacrawler {' '.join(extra_args[:4])} 超过 {timeout}s")
        shutil.rmtree(save_dir, ignore_errors=True)
        return None, False
    combined = (r.stdout or "") + (r.stderr or "")
    if "website-login/captcha" in combined or "安全验证" in combined:
        _record_error("captcha", "命中小红书验证码墙")
        shutil.rmtree(save_dir, ignore_errors=True)
        return None, False
    if "CDPBrowserManager" in combined and "Successfully connected" not in combined:
        _record_error("infra", "连不上 xhschrome（CDP 9333）——确认 xhschrome 在跑，"
                               "或 launchctl kickstart -k gui/$(id -u)/com.eric.xhschrome")
        shutil.rmtree(save_dir, ignore_errors=True)
        return None, False
    if r.returncode != 0:
        _record_error("crash", combined[-300:])
        shutil.rmtree(save_dir, ignore_errors=True)
        return None, False
    return save_dir, True


def _read_jsonl(save_dir: str, crawler_type: str, item_type: str) -> list:
    today = date.today().strftime("%Y-%m-%d")
    p = Path(save_dir) / "xhs" / "jsonl" / f"{crawler_type}_{item_type}_{today}.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def _map_note(item: dict, rank: int) -> dict:
    likes = probe.parse_likes(item.get("liked_count"))
    published_at = ""
    ts = item.get("time")
    if ts:
        try:
            published_at = datetime.fromtimestamp(int(ts) / 1000).strftime("%Y-%m-%d")
        except (ValueError, OSError, OverflowError):
            published_at = ""
    cover = (item.get("image_list") or "").split(",")[0].strip()
    url = item.get("note_url") or ""
    return {
        "rank": rank,
        "note_id": item.get("note_id") or "",
        "href": url,
        "title": (item.get("title") or "").strip(),
        "author": (item.get("nickname") or "").strip(),
        "cover": cover,
        "likes": likes,
        "published_at": published_at,
        "url": url,
    }


# preflight 的探针词。⛔ 跟 probe_opencli.py 用同一份，别写死一个——
# 这个账号的整条链路对主站风控相当敏感（2026-08-16 因此降过频）。
_PREFLIGHT_WORDS = ["面试", "职场", "offer", "简历", "跳槽", "加薪"]


def preflight() -> tuple:
    """开跑前确认这条链是通的。通 → (True, "")；不通 → (False, 该怎么修)。"""
    word = random.choice(_PREFLIGHT_WORDS)
    save_dir, ok = _run(["--type", "search", "--keywords", word,
                         "--crawler_max_notes_count", "1", "--get_comment", "false"])
    if ok:
        try:
            items = _read_jsonl(save_dir, "search", "contents")
        finally:
            shutil.rmtree(save_dir, ignore_errors=True)
        if items:
            return True, ""
        return False, (f"⚠️ 风控：链路通，但主站搜「{word}」这种通用词返回 0 条。\n"
                       "   参考 2026-08-16 那次：降频错峰，别继续投，硬投一整天都会空。")

    kind = LAST_ERROR.get("kind", "")
    if kind == "auth":
        return False, ("⛔ 登录态失效（opencli 探测到 AUTH_REQUIRED）—— 需要人扫码，"
                       "脚本自愈不了，本轮不会再弹 MediaCrawler 自己的二维码窗口。\n"
                       "   跑：opencli xiaohongshu login（会开登录页等扫码，先告知 Eric）\n"
                       "   注意主站与创作者中心 cookie 相互独立，按需分别验。")
    if kind == "captcha":
        return False, ("⛔ 命中小红书验证码墙——这不是 MediaCrawler 能自己解的，需要人工去 "
                       "xhschrome 窗口过一次验证（大概率是扫码）。\n"
                       "   跟 opencli 那堵是不是同一堵：opencli 走的 URL 特征已知会被拦，"
                       "这里如果也被拦，说明是账号级别的信号，不只是 URL 特征问题了，"
                       "两条独立技术路线都被拦意味着风险等级要重新评估，不要自动重试。")
    if kind == "infra":
        return False, f"⛔ {LAST_ERROR.get('message', '')}"
    if kind == "timeout":
        return False, f"⛔ MediaCrawler 超时：{LAST_ERROR.get('message', '')}"
    return False, (f"⛔ MediaCrawler 异常（{kind}）：{LAST_ERROR.get('message', '')[:160]}\n"
                   "   先手动跑一次 uv run main.py --platform xhs --lt qrcode --type search "
                   "--keywords 测试 看是哪一环。")


def collect(keyword: str) -> dict:
    """采一个词。返回与 probe.py / probe_opencli.py 同结构的 dict。"""
    res = {
        "keyword": keyword,
        "probed_at": datetime.now().isoformat(timespec="seconds"),
        "source": "mediacrawler",
        "completeness": "partial",
        "_error": None,
        "note_count": None,
        "autocomplete": [],
        "top_notes": [],
        "note_bodies": [],
        "engage_samples": [],
        "comments": [],
    }

    # 第一步：广搜，只为密度判断取样，不带评论。
    save_dir, ok = _run(["--type", "search", "--keywords", keyword,
                         "--crawler_max_notes_count", str(TOP_N), "--get_comment", "false"])
    if not ok:
        res["completeness"] = "failed"
        res["_error"] = LAST_ERROR.get("kind", "search_failed")
        res["density"] = probe.judge_density([], keyword)
        return res

    try:
        items = _read_jsonl(save_dir, "search", "contents")
    finally:
        shutil.rmtree(save_dir, ignore_errors=True)

    if not items:
        res["completeness"] = "failed"
        res["_error"] = "search_empty"
        res["density"] = probe.judge_density([], keyword)
        return res

    for i, item in enumerate(items):
        res["top_notes"].append(_map_note(item, i + 1))

    res["density"] = probe.judge_density(res["top_notes"], keyword)

    # 第二步：只对点赞最高的 DEEP_N 篇精确取正文+评论，不重新广搜一次全量。
    ranked = sorted(res["top_notes"],
                    key=lambda n: (n["likes"] is None, -(n["likes"] or 0)))[:DEEP_N]
    ids = [n["href"] for n in ranked if n["href"]]
    by_id = {n["note_id"]: n for n in ranked}

    if ids:
        save_dir2, ok2 = _run(["--type", "detail", "--specified_id", ",".join(ids),
                               "--get_comment", "true",
                               "--max_comments_count_singlenotes", str(COMMENT_LIMIT)])
        if ok2:
            try:
                detail_items = _read_jsonl(save_dir2, "detail", "contents")
                comment_items = _read_jsonl(save_dir2, "detail", "comments")
            finally:
                shutil.rmtree(save_dir2, ignore_errors=True)

            for d in detail_items:
                nid = d.get("note_id") or ""
                n = by_id.get(nid)
                res["note_bodies"].append({
                    "note_id": nid,
                    "note_url": d.get("note_url") or (n["url"] if n else ""),
                    "title": d.get("title") or (n["title"] if n else ""),
                    "likes": probe.parse_likes(d.get("liked_count")) or (n["likes"] if n else None),
                    "body": (d.get("desc") or "").replace("﻿", ""),
                    "tags": [t for t in (d.get("tag_list") or "").split(",") if t],
                })
                res["engage_samples"].append({
                    "note_id": nid,
                    "rank": n["rank"] if n else None,
                    "title": n["title"] if n else (d.get("title") or ""),
                    "likes_from_card": n["likes"] if n else None,
                    "like_raw": d.get("liked_count"),
                    "collect_raw": d.get("collected_count"),
                    "comment_raw": d.get("comment_count"),
                    "comment_total_raw": d.get("comment_count"),
                    "body_len": len(d.get("desc") or ""),
                    "engage_bar_raw": "mediacrawler:detail",
                    "dom_comments": sum(1 for c in comment_items if c.get("note_id") == nid),
                })

            for c in comment_items:
                nid = c.get("note_id") or ""
                text = probe.clean_comment(c.get("content") or "")
                if text:
                    res["comments"].append({
                        "note_id": nid,
                        "note_url": (by_id.get(nid) or {}).get("url", ""),
                        "text": text,
                    })
        else:
            res["_error"] = res.get("_error") or f"detail_{LAST_ERROR.get('kind', 'failed')}"

    if res["top_notes"] and res["note_bodies"] and res["comments"]:
        res["completeness"] = "full"
    return res


def oc(args: list, timeout: int = MC_TIMEOUT):
    """兼容 daily_collect.py 里 `probe_opencli.oc(["search", kw, "--limit", N])` 的调用形状。

    ⛔ 只支持 search——daily_collect.py 目前只这么调，没有覆盖 note/comments 等其他
    opencli 子命令的必要（那些没坏，继续走 opencli 本身，不需要这个 shim 兜底）。
    """
    if not args or args[0] != "search":
        _record_error("parse", f"probe_mediacrawler.oc 只支持 search，收到 {args[:1]}")
        return None
    keyword = args[1] if len(args) > 1 else ""
    limit = 20
    if "--limit" in args:
        try:
            limit = int(args[args.index("--limit") + 1])
        except (ValueError, IndexError):
            pass

    save_dir, ok = _run(["--type", "search", "--keywords", keyword,
                         "--crawler_max_notes_count", str(limit), "--get_comment", "false"],
                       timeout=timeout)
    if not ok:
        return None
    try:
        items = _read_jsonl(save_dir, "search", "contents")
    finally:
        shutil.rmtree(save_dir, ignore_errors=True)

    return [
        {"rank": i + 1,
         "title": (it.get("title") or "").strip(),
         "author": (it.get("nickname") or "").strip(),
         "likes": str(probe.parse_likes(it.get("liked_count")) or ""),
         "url": it.get("note_url") or "",
         "published_at": ""}
        for i, it in enumerate(items)
    ]


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
        except Exception as e:                                    # noqa: BLE001
            r = {"keyword": kw, "completeness": "failed", "_error": str(e),
                 "probed_at": datetime.now().isoformat(timespec="seconds"),
                 "source": "mediacrawler"}

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
