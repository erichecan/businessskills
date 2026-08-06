#!/usr/bin/env python3
"""重写全部 launchd plist，让它们统一走 launchd_runner.py（Homebrew python）。

为什么要统一：见 launchd_runner.py 的文件头。一句话——TCC 授权只需要点一次，
而且要点的那个目标是 .app 包（面板里永远可选），不是会被置灰的符号链接。

用法：
  python3 install_agents.py --dry-run   # 只打印会生成什么
  python3 install_agents.py             # 写入 ~/Library/LaunchAgents 并重新加载
  python3 install_agents.py --verify    # 只检查当前 7 个任务的退出码
"""
import argparse
import plistlib
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "scripts" / "launchd_runner.py"
PYTHON = "/opt/homebrew/bin/python3"
AGENTS = Path.home() / "Library" / "LaunchAgents"

# label → (相对 scripts/ 的脚本, 参数, 触发时点)
# 时点全部保持原样，这次只换解释器和入口，不动调度 —— 一次只改一件事，
# 出问题才分得清是权限没生效还是时点改错了。
JOBS = {
    "com.eric.xhsprobe":   ("xhs-probe/daily_probe.sh", [],
                            [(0, 15), (6, 15), (12, 15), (18, 15)]),
    "com.eric.xhsaudit":   ("xhs-health/independent_audit.py", [], [(9, 5)]),
    "com.eric.xhshealth":  ("xhs-health/health_check.py", [], [(9, 30), (19, 30)]),
    "com.eric.xhsdata":    ("xhs-publish/daily_data.sh", [], [(8, 30)]),
    "com.eric.xhswrite":   ("xhs-loop/refine_loop.py", ["--rework", "3", "--count", "3"],
                            [(4, 30), (14, 30)]),
    "com.eric.xhspublish": ("xhs-publish/auto_publish.py", [], [(9, 0), (14, 0), (21, 0)]),
    "com.eric.xhsbrief":   ("xhs-health/nightly_brief.py", [], [(21, 0)]),
}


def build(label, rel, extra, times):
    return {
        "Label": label,
        "ProgramArguments": [PYTHON, str(RUNNER), rel, *extra],
        "StartCalendarInterval": [{"Hour": h, "Minute": m} for h, m in times],
        "StandardOutPath": f"/tmp/{label.split('.')[-1]}.log",
        "StandardErrorPath": f"/tmp/{label.split('.')[-1]}.log",
    }


def existing_times(label):
    """沿用磁盘上已有的时点，别把用户手调过的时间冲掉。"""
    p = AGENTS / f"{label}.plist"
    if not p.exists():
        return None
    try:
        d = plistlib.loads(p.read_bytes())
    except Exception:
        return None
    sci = d.get("StartCalendarInterval")
    if isinstance(sci, dict):
        sci = [sci]
    if not isinstance(sci, list):
        return None
    return [(e.get("Hour", 0), e.get("Minute", 0)) for e in sci] or None


def verify():
    bad = 0
    for label in JOBS:
        out = subprocess.run(["launchctl", "list", label],
                             capture_output=True, text=True).stdout
        m = re.search(r'"LastExitStatus"\s*=\s*(\d+)', out)
        if not out.strip():
            print(f"  ❌ {label} 未加载")
            bad += 1
            continue
        raw = int(m.group(1)) if m else -1
        rc = raw >> 8 if raw > 255 else raw
        hint = "  ← 外置卷权限未生效（TCC）" if rc in (2, 126) else ""
        print(f"  {'✅' if rc == 0 else '❌'} {label:<22} 退出码 {rc}{hint}")
        bad += rc != 0
    print(f"\n{len(JOBS)-bad}/{len(JOBS)} 正常")
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()

    if args.verify:
        return verify()

    if not Path(PYTHON).exists():
        print(f"⛔ 找不到 {PYTHON}（brew install python@3.14）", file=sys.stderr)
        return 1

    AGENTS.mkdir(parents=True, exist_ok=True)
    for label, (rel, extra, times) in JOBS.items():
        times = existing_times(label) or times
        data = build(label, rel, extra, times)
        path = AGENTS / f"{label}.plist"
        when = " ".join(f"{h:02d}:{m:02d}" for h, m in times)
        print(f"{label:<22} {rel} {' '.join(extra)}  @ {when}")
        if args.dry_run:
            continue
        path.write_bytes(plistlib.dumps(data))
        subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
        subprocess.run(["launchctl", "load", str(path)], capture_output=True)
    if args.dry_run:
        print("\n[dry-run] 未写入")
        return 0
    print(f"\n✅ 已重装 {len(JOBS)} 个任务，Program 统一为 {PYTHON}")
    print("   接下来只需在「完全磁盘访问权限」里加这一个（.app 包，面板里可选）：")
    print("   /opt/homebrew/opt/python@3.14/Frameworks/Python.framework/Versions/3.14/"
          "Resources/Python.app")
    print("   加完验证：python3 scripts/install_agents.py --verify")
    return 0


if __name__ == "__main__":
    sys.exit(main())
