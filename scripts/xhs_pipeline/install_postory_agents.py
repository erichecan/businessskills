#!/usr/bin/env python3
"""Install only PoStory's collection, generation, and approval-gated publish jobs."""
from __future__ import annotations

import argparse
import plistlib
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
RUNNER = REPO / "scripts" / "launchd_runner.py"
PYTHON = "/opt/homebrew/bin/python3"
CONFIG = Path.home() / ".config" / "xhs-pipelines" / "pipelines.json"
AGENTS = Path.home() / "Library" / "LaunchAgents"
LOGS = Path.home() / "Library" / "Logs" / "postory"
JOBS = {
    "com.eric.postorypubliccollect": (2, "collect", "--pipeline", "postory-xhs"),
    "com.eric.postory.xhs-generate": (3, "generate-daily"),
    "com.eric.postory.xhs-publish": (22, "publish-approved"),
}


def build(label: str, hour: int, command: tuple[str, ...]) -> dict:
    log = str(LOGS / f"launchd-{label}.log")
    return {
        "Label": label,
        "ProgramArguments": [
            PYTHON, str(RUNNER), "xhs_pipeline/cli.py",
            "--config", str(CONFIG), *command,
        ],
        "WorkingDirectory": str(REPO),
        "StartCalendarInterval": {"Hour": hour, "Minute": 0},
        "StandardOutPath": log,
        "StandardErrorPath": log,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="打印配置，不写入或重载任务")
    args = parser.parse_args()
    if not Path(PYTHON).is_file():
        print(f"⛔ 找不到 Homebrew Python：{PYTHON}", file=sys.stderr)
        return 2
    if not CONFIG.is_file():
        print(f"⛔ 找不到 PoStory 流水线配置：{CONFIG}", file=sys.stderr)
        return 2
    if not args.dry_run:
        AGENTS.mkdir(parents=True, exist_ok=True)
        LOGS.mkdir(parents=True, exist_ok=True)
    for label, (hour, *command) in JOBS.items():
        data = build(label, hour, tuple(command))
        path = AGENTS / f"{label}.plist"
        print(f"{label}  @ {hour:02d}:00  {' '.join(command)}")
        if args.dry_run:
            continue
        path.write_bytes(plistlib.dumps(data))
        subprocess.run(["launchctl", "unload", str(path)], capture_output=True, check=False)
        result = subprocess.run(["launchctl", "load", str(path)], capture_output=True, text=True)
        if result.returncode:
            print(f"⛔ 无法加载 {label}: {(result.stderr or result.stdout).strip()}", file=sys.stderr)
            return result.returncode
    print("\n[dry-run] 未写入" if args.dry_run else f"\n✅ 已安装并加载 {len(JOBS)} 个 PoStory 定时任务")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
