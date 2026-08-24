"""Project CRUD transport adapters; Project Manager owns persistence."""

from __future__ import annotations

from typing import Mapping

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def _pm(context: ApiContext):
    return context.get("project_manager")


def list_projects(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    # Project Manager is an application-owned object, not a callable service
    # factory.  Use the object accessor so the list route shares the same
    # composition boundary as the other project/creative routes.
    return ApiResponse(200, _pm(context).list_projects())


def get_project(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    value = _pm(context).get_project(params["project_id"])
    return ApiResponse(200 if isinstance(value, dict) and value.get("accepted", True) else 404, value)


def create_project(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    value = _pm(context).create_project(request.json())
    return ApiResponse(200, value)


def import_project(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, _pm(context).import_project(request.json()))


def project_export(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    try:
        value = _pm(context).export_project(params["project_id"])
    except KeyError:
        return ApiResponse(404, {"status": "error", "error": "project_not_found"})
    return ApiResponse(200, value)


def project_manifest(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    value = _pm(context).export_manifest(params["project_id"])
    return ApiResponse(200 if value.get("accepted") else 404, value)


def project_missing(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    value = _pm(context).missing_artifact_state(params["project_id"])
    return ApiResponse(200 if value.get("accepted") else 404, value)


def project_compare(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    value = _pm(context).get_compare(params["project_id"])
    return ApiResponse(200 if value is not None else 404, value or {"status": "error", "error": "project_compare_not_found"})


def project_archive(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, _pm(context).archive_project(params["project_id"], archived=True))


def project_restore(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, _pm(context).archive_project(params["project_id"], archived=False))


def project_assets_add(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, _pm(context).add_project_asset(params["project_id"], request.json()))


def project_compare_update(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, _pm(context).update_compare(params["project_id"], request.json()))


def project_update(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, _pm(context).update_project(params["project_id"], request.json()))


def project_delete(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    # The Project Manager deliberately has no destructive delete primitive;
    # the legacy DELETE alias is a safe archive operation.
    return ApiResponse(200, _pm(context).archive_project(params["project_id"], archived=True))


def register(router: Router) -> None:
    owner = "src/services/api/routes/projects.py"
    router.register(route_id="projects.list", method="GET", path="/api/projects", domain="projects", owner=owner, handler=list_projects)
    router.register(route_id="projects.detail", method="GET", path="/api/projects/{project_id}", domain="projects", owner=owner, handler=get_project)
    router.register(route_id="projects.create", method="POST", path="/api/projects", domain="projects", owner=owner, handler=create_project)
    router.register(route_id="projects.import", method="POST", path="/api/projects/import", domain="projects", owner=owner, handler=import_project)
    router.register(route_id="projects.export", method="GET", path="/api/projects/{project_id}/export", domain="projects", owner=owner, handler=project_export)
    router.register(route_id="projects.manifest", method="GET", path="/api/projects/{project_id}/manifest", domain="projects", owner=owner, handler=project_manifest)
    router.register(route_id="projects.missing", method="GET", path="/api/projects/{project_id}/missing-artifacts", domain="projects", owner=owner, handler=project_missing)
    router.register(route_id="projects.compare", method="GET", path="/api/projects/{project_id}/compare", domain="projects", owner=owner, handler=project_compare)
    router.register(route_id="projects.archive", method="POST", path="/api/projects/{project_id}/archive", domain="projects", owner=owner, handler=project_archive)
    router.register(route_id="projects.restore", method="POST", path="/api/projects/{project_id}/restore", domain="projects", owner=owner, handler=project_restore)
    router.register(route_id="projects.assets_add", method="POST", path="/api/projects/{project_id}/assets", domain="projects", owner=owner, handler=project_assets_add)
    router.register(route_id="projects.compare_update", method="POST", path="/api/projects/{project_id}/compare", domain="projects", owner=owner, handler=project_compare_update)
    router.register(route_id="projects.update", method="PUT", path="/api/projects/{project_id}", domain="projects", owner=owner, handler=project_update)
    router.register(route_id="projects.delete", method="DELETE", path="/api/projects/{project_id}", domain="projects", owner=owner, handler=project_delete)
