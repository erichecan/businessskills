"""Runtime context for explicitly selected, isolated XHS pipelines.

Legacy entrypoints keep their existing paths when XHS_PIPELINE_ID is absent.
Setting XHS_PIPELINE_ID opts into a validated, enabled pipeline; there is no
fallback to legacy paths if that named configuration is missing or invalid.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import PipelineConfigError, get_pipeline, load_registry


@dataclass(frozen=True)
class PipelineContext:
    pipeline_id: str
    pipeline: dict[str, Any]
    data_root: Path
    browser_profile: Path
    cdp_port: int
    proxy_port: int

    @property
    def account_id(self) -> str:
        return self.pipeline["publishing"]["account_id"]

    @property
    def publishing(self) -> dict[str, Any]:
        return self.pipeline["publishing"]

    @property
    def collection(self) -> dict[str, Any]:
        return self.pipeline["collection"]

    @property
    def generation(self) -> dict[str, Any]:
        return self.pipeline["generation"]

    def model_route(self, route_name: str) -> dict[str, Any]:
        routes = self.pipeline["model_routes"]
        route = routes.get(route_name)
        if route is None:
            raise PipelineConfigError(f"model route {route_name!r} is not configured")
        if route.get("enabled") is False:
            raise PipelineConfigError(f"model route {route_name!r} is disabled")
        return route["primary"]


def active_context(*, required: bool = False) -> PipelineContext | None:
    """Load the selected pipeline from XHS_PIPELINE_ID, if one was requested."""
    pipeline_id = (os.environ.get("XHS_PIPELINE_ID") or "").strip()
    if not pipeline_id:
        if required:
            raise PipelineConfigError("set XHS_PIPELINE_ID to select a pipeline explicitly")
        return None

    pipeline = get_pipeline(load_registry(), pipeline_id)
    workspace = pipeline["workspace"]
    browser = pipeline["publishing"]["browser"]
    return PipelineContext(
        pipeline_id=pipeline_id,
        pipeline=pipeline,
        data_root=Path(os.path.expandvars(workspace["data_root"])).expanduser().resolve(),
        browser_profile=Path(os.path.expandvars(browser["user_data_dir"])).expanduser().resolve(),
        cdp_port=browser["cdp_port"],
        proxy_port=browser["proxy_port"],
    )


def data_root(legacy_root: Path) -> Path:
    """Return the selected pipeline's private data root or the legacy root."""
    context = active_context()
    return context.data_root if context else legacy_root


def proxy_base(legacy_port: int = 3456) -> str:
    """Return this pipeline's loopback browser bridge URL."""
    context = active_context()
    port = context.proxy_port if context else legacy_port
    return f"http://127.0.0.1:{port}"


def browser_settings() -> dict[str, Any] | None:
    """Return browser settings only for an explicitly selected pipeline."""
    context = active_context()
    if not context:
        return None
    return context.publishing["browser"]
