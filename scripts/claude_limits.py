#!/usr/bin/env python3
"""headless claude 的额度错误分类 —— 三个调用点（写稿/审核/probe）共用一套判定。

为什么要分类而不是一个正则打天下（2026-08-11 实测）：

原先 refine_loop.py 和 independent_audit.py 用同一个 LIMIT_RE 匹配所有额度错误，
撞上就「每 30 分钟试一次，最多熬 5 小时」。这对 **session limit** 是对的——
它的窗口就是 5 小时，08-05 那次熬 50 分钟额度就回来了。

但对 **weekly limit** 是灾难：周额度要等到重置日（`resets 12am`）才回来，
动辄几天，熬 5 小时纯属空转。08-10 的实测账单：

    全天 112 次调用 · 0 token · 0 产出 · 每次都熬满 5 小时才放弃

日志里 112 份留档全是同一句 "You've hit your weekly limit · resets 12am"。
这一天写稿 40 次、审核 32 次、probe 40 次，全部空跑，而且看日志像是「跑过了」。

所以按窗口长度分三类，每类的退避策略不同：

    WEEKLY   周额度耗尽 → ⛔ 立刻停手。等不起，也不该等。
    SESSION  5 小时窗口 → 等 30 分钟再试，最多熬 5 小时（原策略，保留）
    RATE     瞬时限流/429 → 短等即可，不是配额问题

auto_analyze.py 原本**完全没有**额度处理：撞额度时 parse_json 拿不到 JSON，
被当作「解析失败」重试 1 次后丢弃，那条 probe 任务就此消失且不留痕迹。
现在它也走这套判定。
"""
import re

WEEKLY = "weekly"
SESSION = "session"
RATE = "rate"

# ⛔ 判定顺序即优先级，不能调换：weekly 和 session 的提示都带 "resets N am"，
# 泛匹配放前面会把 weekly 误判成 session，于是继续熬 5 小时——正是要修的那个 bug。
_WEEKLY_RE = re.compile(
    r"weekly\s+limit|weekly\s+usage|limit.{0,20}\bthis\s+week\b", re.I)
_SESSION_RE = re.compile(
    r"session\s+limit|5[-\s]?hour\s+limit", re.I)
_RATE_RE = re.compile(
    r"\brate.?limit|\b429\b|too\s+many\s+requests|overloaded", re.I)
# 兜底：只说了「额度用完，X 点重置」但没说是哪种窗口。
# 归到 SESSION（继续等）而不是 WEEKLY（停手）——保守选择：
# 误等的代价是浪费半小时，误停的代价是当天不再产出。
_GENERIC_RE = re.compile(
    r"usage\s+limit|hit\s+your\s+limit|resets?\s+\d+\s*(am|pm)", re.I)


def classify_limit(*texts):
    """判断这次调用撞的是哪种额度限制。

    传入 stdout / stderr（claude CLI 把额度提示当正常输出打印到 stdout，
    returncode 仍是 0，所以两个流都要看）。

    返回 WEEKLY / SESSION / RATE 之一，或 None（不是额度问题）。
    """
    blob = "\n".join(t for t in texts if t)
    if not blob:
        return None
    if _WEEKLY_RE.search(blob):
        return WEEKLY
    if _SESSION_RE.search(blob):
        return SESSION
    if _RATE_RE.search(blob):
        return RATE
    if _GENERIC_RE.search(blob):
        return SESSION
    return None


def is_limit(*texts):
    """只关心「是不是额度问题」时用这个，等价于旧的 LIMIT_RE.search()。"""
    return classify_limit(*texts) is not None


def limit_banner(kind, tag=""):
    """撞额度时打印给人看的一行。weekly 必须显眼——它意味着今天别指望有产出了。"""
    who = f"{tag} " if tag else ""
    if kind == WEEKLY:
        return (f"   ⛔ {who}撞【周额度】上限，重置前再试都是空转，立刻停手。\n"
                f"      本次任务未完成，额度恢复后需重新触发。")
    if kind == SESSION:
        return f"   ⏳ {who}撞 5 小时窗口额度"
    if kind == RATE:
        return f"   ⏳ {who}瞬时限流（非配额）"
    return f"   ⏳ {who}额度受限"
