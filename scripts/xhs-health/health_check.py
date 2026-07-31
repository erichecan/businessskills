#!/usr/bin/env python3
"""素材库健康检查（进程外心跳）— P0 止血。

检查三件事：
1. 运行日志断流：最新 xlsx 产出日期 与 运行日志.csv 最后一行日期 相差 >1 天 → 告警
2. 日志 schema：最近数据行必须恰好 10 列，数值列必须是数字 → 违规即告警
3. 打分闸门：最近 2 天的 成稿_*.md 必须在 审核记录.csv 有对应行 → 缺失即告警

告警动作：macOS 通知 + 追加写 素材库/健康告警.md + 退出码 1。
由 com.eric.xhshealth LaunchAgent 每天 09:30 触发，也可手动运行。
"""
import csv
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SUCAI = REPO / "xhs" / "素材库"
RUN_LOG = SUCAI / "运行日志.csv"
AUDIT_LOG = SUCAI / "审核记录.csv"
ALERT_FILE = SUCAI / "健康告警.md"

LOG_COLUMNS = 10
NUMERIC_COLS = [2, 3, 4, 5, 7]  # 跑的关键词数/总抓取条数/本轮新增/记忆库累计/连续0新增轮数

DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def latest_xlsx_date():
    dates = []
    for f in SUCAI.glob("职场表达与面试技巧_*.xlsx"):
        m = DATE_RE.search(f.name)
        if m:
            dates.append(date.fromisoformat(m.group(1)))
    return max(dates) if dates else None


def check_log_freshness(alerts):
    xlsx_date = latest_xlsx_date()
    if xlsx_date is None:
        alerts.append("找不到任何 xlsx 产出——采集任务可能已整体停摆")
        return
    if not RUN_LOG.exists():
        alerts.append("运行日志.csv 不存在")
        return
    log_dates = []
    for row in csv.reader(RUN_LOG.open(encoding="utf-8")):
        if row and DATE_RE.fullmatch(row[0].strip()):
            log_dates.append(date.fromisoformat(row[0].strip()))
    if not log_dates:
        alerts.append("运行日志.csv 没有任何有效日期行")
        return
    gap = (xlsx_date - max(log_dates)).days
    if gap > 1:
        alerts.append(
            f"运行日志断流：任务仍在产出（最新 xlsx {xlsx_date}），"
            f"但日志停在 {max(log_dates)}，已断 {gap} 天"
        )


def check_log_schema(alerts):
    if not RUN_LOG.exists():
        return
    rows = [r for r in csv.reader(RUN_LOG.open(encoding="utf-8")) if r]
    bad = []
    for i, row in enumerate(rows[-10:], start=len(rows) - min(10, len(rows) - 1)):
        if not DATE_RE.fullmatch(row[0].strip()):
            continue  # 表头或注释行
        if len(row) != LOG_COLUMNS:
            bad.append(f"第{i+1}行 {row[0]}/{row[1] if len(row)>1 else '?'}：{len(row)} 列（应为 {LOG_COLUMNS}）")
            continue
        for c in NUMERIC_COLS:
            if row[c].strip() and not re.fullmatch(r"\d+", row[c].strip()):
                bad.append(f"第{i+1}行 {row[0]}：第{c+1}列应为数字，实为「{row[c][:20]}」")
                break
    if bad:
        alerts.append("运行日志 schema 违规：\n  - " + "\n  - ".join(bad))


def check_audit_gate(alerts):
    cutoff = date.today() - timedelta(days=2)
    recent = []
    for f in SUCAI.glob("成稿_*.md"):
        m = DATE_RE.search(f.name)
        if m and date.fromisoformat(m.group(1)) >= cutoff:
            recent.append(f.name)
    if not recent:
        return
    audited = set()
    if AUDIT_LOG.exists():
        for row in csv.DictReader(AUDIT_LOG.open(encoding="utf-8")):
            audited.add((row.get("成稿文件") or "").strip())
    missing = [f for f in recent if f not in audited]
    if missing:
        alerts.append(
            "成稿未过审核闸门（审核记录.csv 无对应行）：\n  - " + "\n  - ".join(missing)
        )


def notify(title, text):
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{text[:180]}" with title "{title}" sound name "Basso"'],
            check=False, timeout=10,
        )
    except Exception:
        pass


def main() -> int:
    alerts = []
    check_log_freshness(alerts)
    check_log_schema(alerts)
    check_audit_gate(alerts)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    if not alerts:
        print(f"[{now}] 健康检查通过：日志新鲜、schema 合规、成稿均有审核记录")
        return 0

    report = f"\n## {now} 健康检查告警\n\n" + "\n\n".join(f"- {a}" for a in alerts) + "\n"
    with ALERT_FILE.open("a", encoding="utf-8") as f:
        if ALERT_FILE.stat().st_size == 0:
            f.write("# 素材库健康告警记录\n\n> 由 scripts/xhs-health/health_check.py 自动写入。处理完一条就删一条。\n")
        f.write(report)
    print(f"[{now}] 发现 {len(alerts)} 项告警：")
    for a in alerts:
        print(f"  ⛔ {a}")
    notify("小红书采集任务告警", f"{len(alerts)} 项异常，详见 素材库/健康告警.md")
    return 1


if __name__ == "__main__":
    sys.exit(main())
