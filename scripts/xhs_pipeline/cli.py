"""Command-line entrypoint for setting up and operating an isolated pipeline."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
REPO = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from xhs_pipeline.browser import start as start_browser  # noqa: E402
from xhs_pipeline.config import DEFAULT_CONFIG, PipelineConfigError, get_pipeline, load_registry  # noqa: E402


def initialize(config_path: Path) -> None:
    template = REPO / "config" / "xhs-pipelines.example.json"
    if config_path.exists():
        raise PipelineConfigError(f"配置已存在，不覆盖：{config_path}")
    config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copyfile(template, config_path)
    try:
        config_path.chmod(0o600)
    except OSError:
        pass
    print(f"已创建配置模板：{config_path}")
    print("下一步启动本地配置页，填写品牌素材目录、采集源和浏览器参数，再启用流水线。")


def main() -> int:
    parser = argparse.ArgumentParser(prog="xhs-pipeline")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                        help="外部流水线配置文件")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="在仓库外创建 postory-xhs 配置模板")
    sub.add_parser("dashboard", help="启动本地可视化配置页")
    validate = sub.add_parser("validate", help="校验配置并显示隔离路径")
    validate.add_argument("--pipeline", default="postory-xhs")
    login = sub.add_parser("browser-login", help="启动指定流水线的专属 Chromium 登录窗口")
    login.add_argument("--pipeline", default="postory-xhs")
    login.add_argument("--no-open", action="store_true", help="启动服务但不打开创作中心")
    generate = sub.add_parser("generate", help="根据素材生成草稿；PoStory 稿件会自动审稿")
    generate.add_argument("--pipeline", default="postory-xhs")
    generate.add_argument("--brief", action="append", required=True,
                          help="本篇内容任务；可重复传入以批量生成")
    collect = sub.add_parser("collect", help="从配置的公开来源采集并更新素材池")
    collect.add_argument("--pipeline", default="postory-xhs")
    collect.add_argument("--source", action="append", dest="source_ids", help="只运行指定来源，可重复")
    collect.add_argument("--dry-run", action="store_true", help="采集统计但不写入素材池")
    publish = sub.add_parser("publish", help="将已审核图文包上传并按原时段规则定时发布")
    publish.add_argument("--pipeline", default="postory-xhs")
    publish.add_argument("--package", type=Path, required=True, help="包含 note.json、caption.txt 与 cards/ 的图文包目录")
    publish.add_argument("--account-label", required=True, help="本次已在创作页核对的账号显示名")
    publish.add_argument("--at", default="auto", help="北京时间 YYYY-MM-DD HH:MM；默认按原轮换规则选下一个时段")
    publish.add_argument("--submit-scheduled", action="store_true", help="执行平台最终「定时发布」提交")
    sub.add_parser("generate-daily", help="PoStory 每日生成最多五篇待人工审核稿件")
    sub.add_parser("publish-approved", help="PoStory 每晚提交最多五篇自动审稿通过的稿件")
    approve = sub.add_parser("approve", help="记录 PoStory 稿件的人工审批")
    approve.add_argument("--package", type=Path, required=True, help="稿件包目录")
    approve.add_argument("--approved-by", required=True, help="审批人")
    args = parser.parse_args()

    try:
        if args.command == "init":
            initialize(args.config)
            return 0
        if args.command == "dashboard":
            os.environ["XHS_PIPELINES_CONFIG"] = str(args.config.expanduser().resolve())
            from xhs_pipeline.dashboard import main as dashboard_main
            return dashboard_main()
        if args.command == "generate":
            from xhs_pipeline.generate import generate as generate_draft
            os.environ["XHS_PIPELINES_CONFIG"] = str(args.config.expanduser().resolve())
            os.environ["XHS_PIPELINE_ID"] = args.pipeline
            from xhs_pipeline.automation import auto_review_package
            from xhs_pipeline.runtime import active_context
            failures = 0
            for brief in args.brief:
                try:
                    draft = generate_draft(brief)
                except Exception as exc:
                    failures += 1
                    print(f"⛔ 草稿未生成（{brief}）：{exc}", file=sys.stderr)
                    continue
                if args.pipeline == "postory-xhs":
                    try:
                        review = auto_review_package(active_context(required=True), draft)
                    except Exception as exc:
                        failures += 1
                        print(f"⏸️ 稿件已生成但自动审批未完成（{draft}）：{exc}", file=sys.stderr)
                        continue
                    print(f"✅ PoStory 稿件：{draft}；自动审核状态：{review['status']}")
                else:
                    print(f"✅ 待人工审核图文笔记包：{draft}")
            return 2 if failures else 0
        if args.command == "collect":
            os.environ["XHS_PIPELINES_CONFIG"] = str(args.config.expanduser().resolve())
            os.environ["XHS_PIPELINE_ID"] = args.pipeline
            from xhs_pipeline.collect import collect as collect_public_sources
            result = collect_public_sources(
                dry_run=args.dry_run,
                source_ids=set(args.source_ids) if args.source_ids else None,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1 if result["errors"] and not result["saved"] else 0
        if args.command == "publish":
            os.environ["XHS_PIPELINES_CONFIG"] = str(args.config.expanduser().resolve())
            os.environ["XHS_PIPELINE_ID"] = args.pipeline
            from xhs_pipeline.runtime import active_context
            from xhs_pipeline.publish import publish_package
            result = publish_package(
                active_context(required=True),
                args.package,
                account_label=args.account_label,
                submit_scheduled=args.submit_scheduled,
                schedule_at=args.at,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command in {"generate-daily", "publish-approved", "approve"}:
            os.environ["XHS_PIPELINES_CONFIG"] = str(args.config.expanduser().resolve())
            os.environ["XHS_PIPELINE_ID"] = "postory-xhs"
            from xhs_pipeline.automation import run_cli
            return run_cli(
                args.command,
                package=str(args.package) if args.command == "approve" else None,
                approved_by=args.approved_by if args.command == "approve" else "",
            )
        registry = load_registry(args.config)
        pipeline_id = args.pipeline
        pipeline = get_pipeline(registry, pipeline_id)
        if args.command == "validate":
            workspace = Path(pipeline["workspace"]["data_root"]).expanduser()
            browser = pipeline["publishing"]["browser"]
            print(json.dumps({
                "pipeline_id": pipeline_id,
                "account_id": pipeline["publishing"]["account_id"],
                "data_root": str(workspace),
                "browser_profile": browser["user_data_dir"],
                "cdp_port": browser["cdp_port"],
                "proxy_port": browser["proxy_port"],
                "collection_provider": "public_rss_and_web",
                "enabled_sources": [
                    {"id": source["id"], "name": source.get("name", source["id"])}
                    for source in pipeline["collection"]["sources"] if source.get("enabled")
                ],
                "editorial_topics": [
                    topic.get("id") for topic in pipeline["collection"].get("editorial_matrix", {}).get("topics", [])
                ],
                "final_submit_requires_approval": pipeline["publishing"]["final_submit_requires_approval"],
            }, ensure_ascii=False, indent=2))
            return 0
        os.environ["XHS_PIPELINES_CONFIG"] = str(args.config.expanduser().resolve())
        os.environ["XHS_PIPELINE_ID"] = pipeline_id
        context = start_browser(open_creator=not args.no_open)
        print(f"✅ {context.pipeline_id} 浏览器与专属 CDP bridge 已就绪。")
        print(f"账号标识：{context.account_id}；profile：{context.browser_profile}")
        print("已打开此流水线的创作中心；如页面提示未登录，请在该窗口扫码。不要复制或导出 cookie。")
        return 0
    except (PipelineConfigError, OSError, RuntimeError) as exc:
        print(f"⛔ {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
