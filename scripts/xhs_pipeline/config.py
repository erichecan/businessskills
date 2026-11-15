#!/usr/bin/env python3
"""Read and validate isolated XHS pipeline configurations."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = Path.home() / ".config" / "xhs-pipelines" / "pipelines.json"
PIPELINE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,47}$")


class PipelineConfigError(ValueError):
    """Raised when a pipeline registry is missing or invalid."""


def _external_path(value: Any, field: str, *, required: bool = True) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        if required:
            raise PipelineConfigError(f"{field} must be a non-empty path")
        return None
    path = Path(os.path.expandvars(value)).expanduser().resolve()
    try:
        path.relative_to(REPO)
    except ValueError:
        return path
    raise PipelineConfigError(f"{field} must be outside the repository (contains private client data)")


def _require_dict(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PipelineConfigError(f"{field} must be an object")
    return value


def _validate_model_route(route: Any, field: str) -> None:
    route = _require_dict(route, field)
    if route.get("enabled") is False and "primary" not in route:
        if set(route) != {"enabled"}:
            raise PipelineConfigError(
                f"{field} is disabled and cannot contain unvalidated model/provider settings"
            )
        return
    if "fallback" in route:
        raise PipelineConfigError(f"{field}.fallback is disabled; model failures must stop the stage")
    config = _require_dict(route.get("primary"), f"{field}.primary")
    provider = config.get("provider")
    transport = config.get("transport")
    if transport != "codex_cli" or provider != "chatgpt":
        raise PipelineConfigError(f"{field}.primary must use provider=chatgpt and transport=codex_cli")
    if not isinstance(config.get("executable"), str) or not config["executable"].strip():
        raise PipelineConfigError(f"{field}.primary.executable is required for codex_cli")
    if config.get("auth_source") != "chatgpt_login":
        raise PipelineConfigError(f"{field}.primary.auth_source must be chatgpt_login")
    if config.get("billing_expectation") != "chatgpt_plan":
        raise PipelineConfigError(f"{field}.primary.billing_expectation must be chatgpt_plan")
    has_model = isinstance(config.get("model"), str) and bool(config["model"].strip())
    uses_default = config.get("model_source") == "codex_config_default"
    if has_model == uses_default:
        raise PipelineConfigError(
            f"{field}.primary must set either model or model_source=codex_config_default"
        )
    execution = _require_dict(config.get("execution"), f"{field}.primary.execution")
    if (execution.get("sandbox") != "read-only"
            or execution.get("ephemeral") is not True
            or execution.get("cwd_source") != "pipeline_workspace"
            or execution.get("skip_git_repo_check") is not True):
        raise PipelineConfigError(
            f"{field}.primary.execution must use read-only, ephemeral, pipeline_workspace, "
            "and skip_git_repo_check=true"
        )


def validate_registry(document: Any) -> dict[str, Any]:
    root = _require_dict(document, "registry")
    if root.get("schema_version") != 1:
        raise PipelineConfigError("schema_version must be 1")
    pipelines = root.get("pipelines")
    if not isinstance(pipelines, list):
        raise PipelineConfigError("pipelines must be an array")

    seen: set[str] = set()
    seen_accounts: set[str] = set()
    seen_ports: set[int] = set()
    seen_proxy_ports: set[int] = set()
    seen_browser_profiles: set[Path] = set()
    seen_data_roots: list[Path] = []
    seen_material_roots: list[Path] = []
    for index, raw in enumerate(pipelines):
        prefix = f"pipelines[{index}]"
        pipeline = _require_dict(raw, prefix)
        current_material_roots: list[Path] = []
        pipeline_id = pipeline.get("id")
        if not isinstance(pipeline_id, str) or not PIPELINE_ID_RE.fullmatch(pipeline_id):
            raise PipelineConfigError(f"{prefix}.id must be a lowercase slug (2-48 characters)")
        if pipeline_id in seen:
            raise PipelineConfigError(f"duplicate pipeline id: {pipeline_id}")
        seen.add(pipeline_id)
        if not isinstance(pipeline.get("enabled"), bool):
            raise PipelineConfigError(f"{prefix}.enabled must be a boolean")

        workspace = _require_dict(pipeline.get("workspace"), f"{prefix}.workspace")
        data_root = _external_path(workspace.get("data_root"), f"{prefix}.workspace.data_root")
        for previous_root in seen_material_roots:
            overlaps = (data_root == previous_root
                        or data_root.is_relative_to(previous_root)
                        or previous_root.is_relative_to(data_root))
            if overlaps:
                raise PipelineConfigError(f"{prefix}.workspace.data_root overlaps another pipeline's material source")
        for previous_root in seen_data_roots:
            overlaps = (data_root == previous_root
                        or data_root.is_relative_to(previous_root)
                        or previous_root.is_relative_to(data_root))
            if overlaps:
                raise PipelineConfigError(f"{prefix}.workspace.data_root overlaps another pipeline's data root")
        seen_data_roots.append(data_root)

        collection = _require_dict(pipeline.get("collection"), f"{prefix}.collection")
        sources = collection.get("sources")
        if not isinstance(sources, list) or not sources:
            raise PipelineConfigError(f"{prefix}.collection.sources must be a non-empty array")
        source_ids: set[str] = set()
        for source_index, source in enumerate(sources):
            source = _require_dict(source, f"{prefix}.collection.sources[{source_index}]")
            for key in ("id", "provider"):
                if not isinstance(source.get(key), str) or not source[key].strip():
                    raise PipelineConfigError(f"{prefix}.collection.sources[{source_index}].{key} is required")
            if source["id"] in source_ids:
                raise PipelineConfigError(f"duplicate collection source id: {source['id']}")
            source_ids.add(source["id"])
            if not isinstance(source.get("enabled"), bool):
                raise PipelineConfigError(f"{prefix}.collection.sources[{source_index}].enabled must be a boolean")
            if source.get("provider") != "google_news_rss":
                raise PipelineConfigError(
                    f"{prefix}.collection.sources[{source_index}].provider must be google_news_rss; "
                    "the Postory collector uses public RSS discovery and does not collect Xiaohongshu content"
                )
            if source.get("enabled"):
                settings = _require_dict(source.get("settings", {}),
                                         f"{prefix}.collection.sources[{source_index}].settings")
                if not isinstance(settings.get("query"), str) or not settings["query"].strip():
                    raise PipelineConfigError(
                        f"{prefix}.collection.sources[{source_index}].settings.query is required"
                    )
                max_items = settings.get("max_items", 10)
                if not isinstance(max_items, int) or isinstance(max_items, bool) or not 1 <= max_items <= 100:
                    raise PipelineConfigError(
                        f"{prefix}.collection.sources[{source_index}].settings.max_items must be 1-100"
                    )
                freshness_days = settings.get("published_within_days")
                if freshness_days is not None and (
                        not isinstance(freshness_days, int) or isinstance(freshness_days, bool)
                        or not 1 <= freshness_days <= 3650):
                    raise PipelineConfigError(
                        f"{prefix}.collection.sources[{source_index}].settings.published_within_days must be 1-3650"
                    )
        delay = collection.get("request_delay_seconds", 1.5)
        if not isinstance(delay, (int, float)) or isinstance(delay, bool) or not 0.2 <= delay <= 60:
            raise PipelineConfigError(f"{prefix}.collection.request_delay_seconds must be 0.2-60")
        matrix = _require_dict(collection.get("editorial_matrix", {}), f"{prefix}.collection.editorial_matrix")
        topics = matrix.get("topics", [])
        if not isinstance(topics, list):
            raise PipelineConfigError(f"{prefix}.collection.editorial_matrix.topics must be an array")
        topic_ids: set[str] = set()
        for topic_index, topic in enumerate(topics):
            topic = _require_dict(topic, f"{prefix}.collection.editorial_matrix.topics[{topic_index}]")
            topic_id = topic.get("id")
            terms = topic.get("terms")
            if not isinstance(topic_id, str) or not topic_id.strip() or topic_id in topic_ids:
                raise PipelineConfigError(f"{prefix}.collection.editorial_matrix has a missing or duplicate topic id")
            if not isinstance(terms, list) or any(not isinstance(term, str) or not term.strip() for term in terms):
                raise PipelineConfigError(f"{prefix}.collection.editorial_matrix topic {topic_id} needs non-empty terms")
            topic_ids.add(topic_id)

        generation = _require_dict(pipeline.get("generation"), f"{prefix}.generation")
        material_sources = generation.get("material_sources")
        if not isinstance(material_sources, list):
            raise PipelineConfigError(f"{prefix}.generation.material_sources must be an array")
        for source_index, source in enumerate(material_sources):
            source = _require_dict(source, f"{prefix}.generation.material_sources[{source_index}]")
            material_root = _external_path(source.get("path"),
                                            f"{prefix}.generation.material_sources[{source_index}].path")
            if material_root == data_root or data_root.is_relative_to(material_root):
                raise PipelineConfigError(
                    f"{prefix}.generation.material_sources[{source_index}].path must not contain the pipeline data root"
                )
            for other_data_root in seen_data_roots[:-1]:
                overlaps = (material_root == other_data_root
                            or material_root.is_relative_to(other_data_root)
                            or other_data_root.is_relative_to(material_root))
                if overlaps:
                    raise PipelineConfigError(
                        f"{prefix}.generation.material_sources[{source_index}].path overlaps another pipeline's data root"
                    )
            for previous_root in seen_material_roots:
                overlaps = (material_root == previous_root
                            or material_root.is_relative_to(previous_root)
                            or previous_root.is_relative_to(material_root))
                if overlaps:
                    raise PipelineConfigError(
                        f"{prefix}.generation.material_sources[{source_index}].path overlaps "
                        "another pipeline's private material source"
                    )
            current_material_roots.append(material_root)
        rewrite = _require_dict(generation.get("rewrite"), f"{prefix}.generation.rewrite")
        _external_path(rewrite.get("prompt_file"), f"{prefix}.generation.rewrite.prompt_file")

        model_routes = _require_dict(pipeline.get("model_routes"), f"{prefix}.model_routes")
        for route_name in ("collection_relevance", "first_draft", "rewrite", "review"):
            _validate_model_route(model_routes.get(route_name), f"{prefix}.model_routes.{route_name}")
        if "speech_to_text" in model_routes:
            _validate_model_route(model_routes["speech_to_text"], f"{prefix}.model_routes.speech_to_text")

        publishing = _require_dict(pipeline.get("publishing"), f"{prefix}.publishing")
        if publishing.get("platform") != "xiaohongshu":
            raise PipelineConfigError(f"{prefix}.publishing.platform must be xiaohongshu")
        account_id = publishing.get("account_id")
        if not isinstance(account_id, str) or not account_id.strip():
            raise PipelineConfigError(f"{prefix}.publishing.account_id is required")
        if not isinstance(publishing.get("identity_verified", False), bool):
            raise PipelineConfigError(f"{prefix}.publishing.identity_verified must be a boolean")
        identity_label = publishing.get("verified_account_label", "")
        if not isinstance(identity_label, str):
            raise PipelineConfigError(f"{prefix}.publishing.verified_account_label must be a string")
        if publishing.get("identity_verified") and not identity_label.strip():
            raise PipelineConfigError(
                f"{prefix}.publishing.verified_account_label is required after identity verification"
            )
        if account_id in seen_accounts:
            raise PipelineConfigError(f"publishing.account_id is already assigned to another pipeline: {account_id}")
        seen_accounts.add(account_id)
        browser = _require_dict(publishing.get("browser"), f"{prefix}.publishing.browser")
        if browser.get("engine") != "chromium":
            raise PipelineConfigError(f"{prefix}.publishing.browser.engine must be chromium")
        executable = browser.get("executable")
        if not isinstance(executable, str) or not executable.strip():
            raise PipelineConfigError(f"{prefix}.publishing.browser.executable is required")
        profile = _external_path(browser.get("user_data_dir"),
                                 f"{prefix}.publishing.browser.user_data_dir")
        port = browser.get("cdp_port")
        if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
            raise PipelineConfigError(f"{prefix}.publishing.browser.cdp_port must be 1024-65535")
        if port in seen_ports or port in seen_proxy_ports:
            raise PipelineConfigError(f"CDP port {port} is assigned to more than one pipeline")
        seen_ports.add(port)
        proxy_port = browser.get("proxy_port")
        if (not isinstance(proxy_port, int) or isinstance(proxy_port, bool)
                or not 1024 <= proxy_port <= 65535):
            raise PipelineConfigError(f"{prefix}.publishing.browser.proxy_port must be 1024-65535")
        if proxy_port == port or proxy_port in seen_proxy_ports or proxy_port in seen_ports:
            raise PipelineConfigError(f"{prefix}.publishing.browser.proxy_port conflicts with another port")
        seen_proxy_ports.add(proxy_port)
        if port in seen_proxy_ports:
            raise PipelineConfigError(f"{prefix}.publishing.browser.cdp_port conflicts with a proxy port")
        for previous_profile in seen_browser_profiles:
            overlaps = (profile == previous_profile
                        or profile.is_relative_to(previous_profile)
                        or previous_profile.is_relative_to(profile))
            if overlaps:
                raise PipelineConfigError(f"{prefix}.publishing.browser.user_data_dir overlaps another browser profile")
        seen_browser_profiles.add(profile)
        for material_root in [*seen_material_roots, *current_material_roots]:
            overlaps = (profile == material_root
                        or profile.is_relative_to(material_root)
                        or material_root.is_relative_to(profile))
            if overlaps:
                raise PipelineConfigError(f"{prefix}: browser profile overlaps a private material directory")
        overlaps = (profile == data_root
                    or profile.is_relative_to(data_root)
                    or data_root.is_relative_to(profile))
        if overlaps:
            raise PipelineConfigError(f"{prefix}: browser profile and content data root must be separate")
        if publishing.get("final_submit_requires_approval") is not True:
            raise PipelineConfigError(f"{prefix}.publishing.final_submit_requires_approval must remain true")
        seen_material_roots.extend(current_material_roots)

    return root


def load_registry(path: str | Path | None = None) -> dict[str, Any]:
    """Load registry from an explicit path or XHS_PIPELINES_CONFIG.

    No default live file is shipped, so callers must opt in by creating one.
    """
    config_path = Path(path or os.environ.get("XHS_PIPELINES_CONFIG", DEFAULT_CONFIG)).expanduser()
    if not config_path.is_file():
        raise PipelineConfigError(
            f"pipeline registry not found: {config_path}; copy config/xhs-pipelines.example.json "
            "to an external config file and customize it first"
        )
    try:
        document = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineConfigError(f"cannot read pipeline registry {config_path}: {exc}") from exc
    return validate_registry(document)


def get_pipeline(registry: dict[str, Any], pipeline_id: str, *, allow_disabled: bool = False) -> dict[str, Any]:
    """Return one named pipeline; disabled configs are blocked by default."""
    for pipeline in registry["pipelines"]:
        if pipeline["id"] == pipeline_id:
            if not pipeline["enabled"] and not allow_disabled:
                raise PipelineConfigError(f"pipeline {pipeline_id!r} is disabled")
            return pipeline
    raise PipelineConfigError(f"pipeline not found: {pipeline_id}")
