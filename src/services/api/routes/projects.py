"""Project CRUD transport adapters; Project Manager owns persistence."""

from __future__ import annotations

from typing import Mapping

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def _pm(context: ApiContext):
    return context.get("project_manager")


def list_projects(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, context.call("project_manager").list_projects())


def get_project(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    value = _pm(context).get_project(params["project_id"])
    return ApiResponse(200 if isinstance(value, dict) and value.get("accepted", True) else 404, value)


def create_project(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    value = _pm(context).create_project(request.json())
    return ApiResponse(200, value)


def import_project(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, _pm(context).import_project(request.json()))


def project_export(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, _pm(context).export_project(params["project_id"]))


def project_manifest(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    value = _pm(context).export_manifest(params["project_id"])
    return ApiResponse(200 if value.get("accepted") else 404, value)


def project_missing(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    value = _pm(context).missing_artifact_state(params["project_id"])
    return ApiResponse(200 if value.get("accepted") else 404, value)


def register(router: Router) -> None:
    owner = "src/services/api/routes/projects.py"
    router.register(route_id="projects.list", method="GET", path="/api/projects", domain="projects", owner=owner, handler=list_projects)
    router.register(route_id="projects.detail", method="GET", path="/api/projects/{project_id}", domain="projects", owner=owner, handler=get_project)
    router.register(route_id="projects.create", method="POST", path="/api/projects", domain="projects", owner=owner, handler=create_project)
    router.register(route_id="projects.import", method="POST", path="/api/projects/import", domain="projects", owner=owner, handler=import_project)
    router.register(route_id="projects.export", method="GET", path="/api/projects/{project_id}/export", domain="projects", owner=owner, handler=project_export)
    router.register(route_id="projects.manifest", method="GET", path="/api/projects/{project_id}/manifest", domain="projects", owner=owner, handler=project_manifest)
    router.register(route_id="projects.missing", method="GET", path="/api/projects/{project_id}/missing-artifacts", domain="projects", owner=owner, handler=project_missing)
