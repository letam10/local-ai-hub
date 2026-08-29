"""Product Experience V2 transport: finite navigation, onboarding and search."""

from __future__ import annotations

from collections.abc import Mapping

from src.services import product_experience_v2

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def snapshot(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del request, context, params
    return ApiResponse(200, product_experience_v2.snapshot())


def onboarding(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del request, context, params
    return ApiResponse(200, product_experience_v2.onboarding())


def search(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del params
    values = request.query.get("q", [])
    query = values[0] if isinstance(values, list) and values else ""
    sources: dict[str, object] = {}
    try:
        catalog = context.call("productization_snapshot")
        if isinstance(catalog, Mapping):
            sources["models"] = catalog.get("models", [])
    except Exception:
        pass
    try:
        tools = context.call("tools_payload")
        if isinstance(tools, Mapping):
            sources["tools"] = tools.get("tools", [])
    except Exception:
        pass
    try:
        manager = context.get("project_manager")
        projects = manager.list_projects() if manager is not None else None
        if isinstance(projects, Mapping):
            sources["projects"] = projects.get("projects", [])
    except Exception:
        pass
    try:
        workflows = context.call("workflow_library_payload")
        if isinstance(workflows, Mapping):
            sources["workflows"] = workflows.get("workflows", [])
    except Exception:
        pass
    try:
        jobs = context.call("list_jobs", limit=120)
        sources["jobs"] = jobs if isinstance(jobs, list) else []
    except Exception:
        pass
    try:
        artifacts = context.call("artifact_library_v2_snapshot", limit=120)
        if isinstance(artifacts, Mapping):
            sources["artifacts"] = artifacts.get("artifacts", [])
    except Exception:
        pass
    return ApiResponse(200, product_experience_v2.search(query, sources=sources))


def register(router: Router) -> None:
    owner = "src/services/api/routes/product_experience_v2.py"
    root = "/api/product-experience/v2"
    router.register(route_id="product_experience.v2_snapshot", method="GET", path=root, domain="product_experience", owner=owner, handler=snapshot)
    router.register(route_id="product_experience.v2_onboarding", method="GET", path=f"{root}/onboarding", domain="product_experience", owner=owner, handler=onboarding)
    router.register(route_id="product_experience.v2_search", method="GET", path=f"{root}/search", domain="product_experience", owner=owner, handler=search)


__all__ = ["register"]
