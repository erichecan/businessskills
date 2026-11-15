"""Migrate the saved postory-xhs config and install its public collector agent."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
REPO = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from xhs_pipeline.config import DEFAULT_CONFIG, get_pipeline, validate_registry  # noqa: E402
from xhs_pipeline.install_collector_agent import install as install_agent  # noqa: E402


def migrate(config_path: Path, *, dry_run: bool = False, install_agent_task: bool = True) -> int:
    config_path = config_path.expanduser().resolve()
    if not config_path.is_file():
        raise FileNotFoundError(f"流水线配置不存在：{config_path}")
    document = json.loads(config_path.read_text(encoding="utf-8"))
    template = json.loads((REPO / "config" / "xhs-pipelines.example.json").read_text(encoding="utf-8"))
    current = get_pipeline(document, "postory-xhs", allow_disabled=True)
    template_pipeline = get_pipeline(template, "postory-xhs", allow_disabled=True)
    was_enabled = current.get("enabled", False)

    current["collection"] = template_pipeline["collection"]
    current.setdefault("generation", {})["material_sources"] = []
    current["generation"].setdefault("rewrite", {})["style_profile"] = "postory_smb_marketing_authority"
    current["enabled"] = was_enabled
    validate_registry(document)

    print(f"配置：{config_path}")
    print(f"旧站内采集/manual_import 将替换为 {len(current['collection']['sources'])} 个公开来源。")
    print(f"pipeline enabled：{was_enabled}")
    if dry_run:
        print("[dry-run] 未写配置，也未安装 LaunchAgent。")
        return 0

    config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = config_path.with_suffix(config_path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    temporary.replace(config_path)
    try:
        config_path.chmod(0o600)
    except OSError:
        pass
    print("✅ 正式配置已迁移并通过校验。")

    if install_agent_task:
        install_agent(config_path=config_path)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="迁移 postory-xhs 并安装公开来源采集定时任务")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="仓库外流水线配置")
    parser.add_argument("--dry-run", action="store_true", help="只显示计划，不修改配置")
    parser.add_argument("--skip-agent", action="store_true", help="只迁移配置，不安装定时任务")
    args = parser.parse_args()
    try:
        return migrate(args.config, dry_run=args.dry_run, install_agent_task=not args.skip_agent)
    except Exception as exc:
        print(f"⛔ 设置失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
