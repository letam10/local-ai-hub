"""Project Workspace V2 projection and opaque-reference transport adapters."""

from __future__ import annotations

from collections.abc import Mapping

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def snapshot(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del request, params
    return ApiResponse(200, context.call("project_workspace_v2_snapshot"))


def detail(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del request
    value = context.call("project_workspace_v2_detail", params.get("project_id", ""))
    if value is None:
        return ApiResponse(404, {"status": "not_found", "error": "project_workspace_v2_not_found", "execution": "not_run", "dry_run": True})
    return ApiResponse(200, value)


def export_manifest(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del request
    value = context.call("project_workspace_v2_export_manifest", params.get("project_id", ""))
    if value is None:
        return ApiResponse(404, {"status": "not_found", "error": "project_workspace_v2_not_found", "execution": "not_run", "dry_run": True})
    return ApiResponse(200, value)


def attach_workflow(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json(strict=True)
    workflow_id = body.get("workflow_id") if isinstance(body, Mapping) and set(body) == {"workflow_id"} else None
    value = context.call("project_workspace_v2_attach_workflow", params.get("project_id", ""), workflow_id)
    status = 400 if isinstance(value, Mapping) and value.get("status") == "invalid" else 503 if isinstance(value, Mapping) and value.get("status") == "unavailable" else 200
    return ApiResponse(status, value)


def attach_job(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json(strict=True)
    job_id = body.get("job_id") if isinstance(body, Mapping) and set(body) == {"job_id"} else None
    value = context.call("project_workspace_v2_attach_job", params.get("project_id", ""), job_id)
    status = 400 if isinstance(value, Mapping) and value.get("status") == "invalid" else 503 if isinstance(value, Mapping) and value.get("status") == "unavailable" else 200
    return ApiResponse(status, value)


def register(router: Router) -> None:
    owner = "src/services/api/routes/project_workspace_v2.py"
    root = "/api/project-workspace/v2"
    router.register(route_id="project_workspace.v2_snapshot", method="GET", path=root, domain="projects", owner=owner, handler=snapshot)
    router.register(route_id="project_workspace.v2_detail", method="GET", path=f"{root}/{{project_id}}", domain="projects", owner=owner, handler=detail)
    router.register(route_id="project_workspace.v2_export_manifest", method="GET", path=f"{root}/{{project_id}}/manifest", domain="projects", owner=owner, handler=export_manifest)
    router.register(route_id="project_workspace.v2_attach_workflow", method="POST", path=f"{root}/{{project_id}}/workflows", domain="projects", owner=owner, handler=attach_workflow)
    router.register(route_id="project_workspace.v2_attach_job", method="POST", path=f"{root}/{{project_id}}/jobs", domain="projects", owner=owner, handler=attach_job)


__all__ = ["register"]
