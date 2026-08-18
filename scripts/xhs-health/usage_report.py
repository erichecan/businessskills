#!/usr/bin/env python3
"""额度消耗看板 —— 扫 ~/.claude/projects 的会话 jsonl，算出钱花在哪。

## 为什么要有这个脚本

2026-08-11 和 08-18 各人肉盘过一次，每次都要写一遍扫描逻辑，而且
**08-11 那次算错了一倍** —— 按 jsonl 行统计 usage，把同一个 API 响应的
thinking / text 两个 content block 分行落盘的记录各计了一次。
那次报「写稿 $2.06 / 审核 $1.13」，实际是 $0.98 / $0.82。

⛔ 所以第一条铁律：**按 `message.id` 去重**。流式响应会把一个响应拆成多条
jsonl 记录，usage 字段在每条上都是全量重复的，不是增量。

## 口径

「$」是**标准 API 单价折算的额度等价成本**，不是现金账单 —— 这些消耗由
Claude Code 订阅覆盖，月费已付。它的用途是衡量距离周额度上限还有多远。

    input×P + cache_write_1h×P×2 + cache_write_5m×P×1.25 + cache_read×P×0.1 + output×Po

## 定时 vs 手动

同一批脚本，launchd 定时触发的和 Eric 手动跑的，成本性质完全不同
（前者是生产成本、后者是开发成本），必须分开看。判据是本地时间落不落在
10:00–19:00 的 usage 任务窗口内（见 20260811-token优化-tasks.md T7）。
"""
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path.home() / ".claude" / "projects"

# 标准 API 单价（$/M）：(input, output)。未知模型按 Opus 计，宁可高报不低报。
PRICE = {
    "claude-opus-5": (5, 25), "claude-opus-4-8": (5, 25), "claude-opus-4-7": (5, 25),
    "claude-sonnet-5": (3, 15), "claude-sonnet-4-6": (3, 15),
    "claude-fable-5": (10, 50), "claude-haiku-4-5": (1, 5),
}
DEFAULT_PRICE = (5, 25)

# 本机时区偏移（EDT = UTC−4）。jsonl 里的 timestamp 是 UTC。
LOCAL_OFFSET = timedelta(hours=-4)
WINDOW = (10, 19)          # usage 定时任务窗口，见 T7

WEEK_BUDGET = 1400         # 周额度上限实测 $1,350–1,500，取中位
WARN_AT = 1100             # 超过就在 brief 里打醒目提示

# ⛔ 必须按**额度周期**统计，不能按「近 N 天」—— 周额度每周日重置，
# 跨过重置点的窗口会把上个周期的消耗算进来，报出「已超上限」的假警报
# （2026-08-18 第一版就是这么错的：8 天窗口横跨两个周期，报 $1,817 / 上限 $1,400）。
WEEK_RESETS_ON = 6         # 周日 = 6（datetime.weekday()）

TASKS = [
    ("独立第三方审核员", "审核"),
    ("小红书成稿写手", "写稿"),
    ("搜索位空缺分析员", "probe分析"),
    ("修一篇已经通过内容审核", "定稿修补"),
    ("复盘一次小红书笔记数据预测", "预测复盘"),
    ("首评", "首评"),
    ("喜马拉雅", "喜马拉雅"),
    ("口播稿", "喜马拉雅"),
    ("线索筛选", "建站线索"),
    ("售前诊断", "建站线索"),
]
AUTO_TASKS = {"写稿", "审核", "probe分析", "定稿修补", "预测复盘"}


def classify(first_prompt: str) -> str:
    for mark, name in TASKS:
        if mark in first_prompt:
            return name
    return "交互式开发"


def scan(since: str):
    """返回 [(本地datetime, 任务名, 成本, 项目名), ...]，只含 since 之后的会话。"""
    sessions = []
    for proj in sorted(ROOT.iterdir()) if ROOT.is_dir() else []:
        if not proj.is_dir():
            continue
        for f in sorted(proj.glob("*.jsonl")):
            first, ts_first = "", ""
            seen, by_model = set(), defaultdict(lambda: [0, 0, 0, 0, 0])
            for line in f.open(errors="ignore"):
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                t = d.get("type")
                if not first:
                    if t == "queue-operation" and d.get("content"):
                        first = d["content"][:300]
                    elif t == "user":
                        c = d.get("message", {}).get("content")
                        if isinstance(c, str):
                            first = c[:300]
                        elif isinstance(c, list):
                            for b in c:
                                if isinstance(b, dict) and b.get("type") == "text":
                                    first = b.get("text", "")[:300]
                                    break
                if not ts_first and d.get("timestamp"):
                    ts_first = d["timestamp"]
                if t != "assistant":
                    continue
                m = d.get("message", {})
                u, mid = m.get("usage") or {}, m.get("id")
                if not u or not mid or mid in seen:     # ⛔ 按 message.id 去重
                    continue
                seen.add(mid)
                cc = u.get("cache_creation") or {}
                r = by_model[m.get("model", "?")]
                r[0] += u.get("input_tokens", 0)
                r[1] += cc.get("ephemeral_1h_input_tokens", 0)
                r[2] += cc.get("ephemeral_5m_input_tokens", 0) or (
                    0 if cc else u.get("cache_creation_input_tokens", 0))
                r[3] += u.get("cache_read_input_tokens", 0)
                r[4] += u.get("output_tokens", 0)
            if not by_model or not ts_first or ts_first[:10] < since:
                continue
            cost = 0.0
            for model, (inp, w1, w5, cr, outp) in by_model.items():
                pi, po = PRICE.get(model, DEFAULT_PRICE)
                cost += (inp * pi + w1 * pi * 2 + w5 * pi * 1.25 + cr * pi * 0.1 + outp * po) / 1e6
            local = datetime.fromisoformat(ts_first.replace("Z", "+00:00")).astimezone(
                timezone.utc) + LOCAL_OFFSET
            sessions.append((local.replace(tzinfo=None), classify(first), cost, proj.name))
    return sessions


def week_start(today: datetime) -> datetime:
    """最近一个周日（含今天若今天是周日）—— 额度周期的起点。"""
    back = (today.weekday() - WEEK_RESETS_ON) % 7
    return (today - timedelta(days=back)).replace(hour=0, minute=0, second=0, microsecond=0)


def report(days: int = 0) -> str:
    """days=0（默认）按本额度周期统计；days>0 强制按近 N 天（跨周期，仅供回溯分析）。"""
    today = (datetime.now(timezone.utc) + LOCAL_OFFSET).replace(tzinfo=None)
    if days:
        start, scope = today - timedelta(days=days), f"近 {days} 天（跨额度周期，仅供回溯）"
    else:
        start = week_start(today)
        scope = f"本额度周期第 {(today - start).days + 1} 天"
    since = start.date().isoformat()
    rows = scan(since)
    if not rows:
        return f"额度看板：{since} 起没有会话记录。"

    buckets = defaultdict(float)
    for dt, task, cost, _ in rows:
        if task in AUTO_TASKS:
            in_window = WINDOW[0] <= dt.hour < WINDOW[1]
            buckets["定时自动化" if in_window else "手动调试"] += cost
        else:
            buckets[task] += cost
    total = sum(buckets.values())

    today_str = today.date().isoformat()
    today_cost = sum(c for dt, _, c, _ in rows if dt.date().isoformat() == today_str)

    lines = [f"## 额度消耗（{scope}，{since} 起）", ""]
    for k, v in sorted(buckets.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {k:<12} ${v:>8.2f}  {v / total * 100:>5.1f}%")
    lines += ["", f"  {'合计':<12} ${total:>8.2f}   今日 ${today_cost:.2f}"]
    left = WEEK_BUDGET - total
    lines.append(f"  周额度上限约 ${WEEK_BUDGET}，剩余 ${left:.0f}（{left / WEEK_BUDGET * 100:.0f}%）")
    if total >= WARN_AT:
        lines += ["", f"  ⚠️⚠️ 已达 ${total:.0f}，逼近周上限 ${WEEK_BUDGET} —— "
                      "先停手动调试和长会话，别让定时任务撞顶停摆"]
    return "\n".join(lines)


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    print(report(n))
