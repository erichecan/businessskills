#!/usr/bin/env python3
"""定时任务排期的回归测试。

跑法：python3 scripts/test_schedule.py

三件事：
  1. plist 能被 plistlib 严格解析
     ⛔ 这条是踩出来的：XML 注释里不允许出现连续两个减号，而写 plist 注释时
     很自然会引用命令行参数（形如「减号减号rework 2」）。`plutil -lint` **查不出来**，
     它会报 OK；但 plistlib 和 launchd 会解析失败，任务静默不按预期触发。
  2. 消耗 usage 的任务只在 10:00–19:00 触发（2026-08-11 Eric 定）
     夜里跑的问题不是费钱，是没人看得见 —— 08-10 全天 112 次调用撞周额度
     空转，直到第二天查账才发现。
  3. 写稿日产不超过发布配额的 1.5 倍
"""
import plistlib
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"

# 会调 claude -p 的任务（daily_probe.sh 内含 auto_analyze.py）
USAGE_JOBS = {"com.eric.xhswrite", "com.eric.xhsaudit", "com.eric.xhsprobe"}
WINDOW = (10, 19)

REPO_PLISTS = {
    "com.eric.xhswrite": REPO / "scripts/xhs-loop/com.eric.xhswrite.plist",
    "com.eric.xhsaudit": REPO / "scripts/xhs-health/com.eric.xhsaudit.plist",
    "com.eric.xhsprobe": REPO / "scripts/xhs-probe/com.eric.xhsprobe.plist",
}

failures = []


def check(label, got, want):
    if got == want:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label}\n     期望 {want!r}，实际 {got!r}")
        failures.append(label)


def hours_of(d):
    s = d.get("StartCalendarInterval")
    if isinstance(s, dict):
        s = [s]
    return [(x.get("Hour", 0), x.get("Minute", 0)) for x in (s or [])]


print("── 1. plist 能被严格解析（XML 注释里不得有连续两个减号）──")
parsed = {}
for name, path in REPO_PLISTS.items():
    raw = path.read_text(encoding="utf-8")
    # 直接查注释体，给出比 ExpatError 更有指向性的报错
    bad = [c for c in re.findall(r"<!--(.*?)-->", raw, re.S) if "--" in c]
    check(f"{name} 注释无连续减号", bad, [])
    try:
        parsed[name] = plistlib.loads(raw.encode())
        print(f"  ✅ {name} plistlib 解析通过")
    except Exception as e:  # noqa: BLE001
        print(f"  ❌ {name} plistlib 解析失败：{e}")
        failures.append(name)

print("\n── 2. 消耗 usage 的任务只在 10:00–19:00 触发 ──")
for name in sorted(USAGE_JOBS):
    d = parsed.get(name)
    if not d:
        continue
    hrs = hours_of(d)
    out = [f"{h:02d}:{m:02d}" for h, m in hrs if not (WINDOW[0] <= h < WINDOW[1])]
    check(f"{name} {[f'{h:02d}:{m:02d}' for h, m in hrs]} 全在窗口内", out, [])

print("\n── 3. 写稿日产不超过发布配额的 1.5 倍 ──")
quota = int(re.search(r"DAILY_QUOTA\s*=\s*(\d+)",
                      (REPO / "scripts/xhs-publish/auto_publish.py").read_text(encoding="utf-8")).group(1))
args = parsed["com.eric.xhswrite"]["ProgramArguments"]
per = int(args[args.index("--rework") + 1]) + int(args[args.index("--count") + 1])
daily = per * len(hours_of(parsed["com.eric.xhswrite"]))
print(f"     每次 {per} 篇 × {len(hours_of(parsed['com.eric.xhswrite']))} 次 = {daily} 篇/天，配额 {quota}")
check(f"日产 {daily} <= 配额 {quota} × 1.5", daily <= quota * 1.5, True)

print("\n── 4. 仓库版与已安装版一致（改了仓库忘了装是常见事故）──")
for name, path in REPO_PLISTS.items():
    installed = LAUNCH_AGENTS / f"{name}.plist"
    if not installed.exists():
        print(f"  ⚠️ {name} 未安装，跳过")
        continue
    same = plistlib.loads(installed.read_bytes()) == parsed.get(name)
    check(f"{name} 仓库版 == 已安装版", same, True)

print()
if failures:
    print(f"⛔ {len(failures)} 条未通过")
    sys.exit(1)
print("✅ 全部通过")
