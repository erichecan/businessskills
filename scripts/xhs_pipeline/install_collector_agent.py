"""Install only the PoStory public-source collection LaunchAgent on macOS."""
from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
REPO = SCRIPTS.parent
DEFAULT_CONFIG = Path.home() / ".config" / "xhs-pipelines" / "pipelines.json"
LABEL = "com.eric.postorypubliccollect"
RETIRED_LABELS = ("com.eric.xhscollect", "com.eric.xhsprobe")
sys.path.insert(0, str(SCRIPTS))
from xhs_pipeline.config import get_pipeline, load_registry  # noqa: E402


def install(*, dry_run: bool = False, config_path: Path | None = None) -> int:
    config = Path(config_path or os.environ.get("XHS_PIPELINES_CONFIG", DEFAULT_CONFIG)).expanduser().resolve()
    if not config.is_file():
        raise FileNotFoundError(f"流水线配置不存在：{config}；请先运行 cli.py init 并保存启用配置")
    pipeline = get_pipeline(load_registry(config), "postory-xhs")
    if not any(source.get("enabled") for source in pipeline["collection"]["sources"]):
        raise RuntimeError("postory-xhs 没有启用的公开采集来源；请先保存并启用来源配置")
    interpreter = Path("/opt/homebrew/bin/python3")
    if not interpreter.exists():
        interpreter = Path(sys.executable)
    launch_agents = Path.home() / "Library" / "LaunchAgents"
    logs = Path.home() / "Library" / "Logs" / "xhs"
    plist_path = launch_agents / f"{LABEL}.plist"
    log_path = logs / "postory-public-collection.log"
    document = {
        "Label": LABEL,
        "ProgramArguments": [str(interpreter), str(SCRIPTS / "launchd_runner.py"),
                             "xhs_pipeline/cli.py", "--config", str(config),
                             "collect", "--pipeline", "postory-xhs"],
        "StartCalendarInterval": {"Hour": 9, "Minute": 0},
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
        "WorkingDirectory": str(REPO),
    }
    print(f"将停用旧站内采集任务：{', '.join(RETIRED_LABELS)}")
    print(f"将安装公开来源采集：{plist_path}（每日 09:00）")
    print(f"配置：{config}")
    if dry_run:
        return 0
    if shutil.which("launchctl") is None:
        raise RuntimeError("launchctl 不可用；此安装脚本只能在 macOS 上运行")

    launch_agents.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    for old_label in RETIRED_LABELS:
        old_path = launch_agents / f"{old_label}.plist"
        subprocess.run(["launchctl", "unload", str(old_path)], capture_output=True)
        try:
            old_path.unlink()
        except FileNotFoundError:
            pass
    plist_path.write_bytes(plistlib.dumps(document, sort_keys=True))
    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
    result = subprocess.run(["launchctl", "load", str(plist_path)], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"launchctl load 失败：{result.stderr.strip() or result.stdout.strip()}")
    print("✅ 旧小红书站内采集任务已卸载；PoStory 公开来源采集已安装。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="仅安装 PoStory 公开来源采集定时任务")
    parser.add_argument("--dry-run", action="store_true", help="显示变更计划，不写入 LaunchAgents")
    parser.add_argument("--config", type=Path, help="仓库外的流水线配置文件")
    args = parser.parse_args()
    try:
        return install(dry_run=args.dry_run, config_path=args.config)
    except Exception as exc:
        print(f"⛔ {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
