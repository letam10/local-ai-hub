"""Small compatibility aliases kept on top of canonical service routes."""

from __future__ import annotations

from typing import Mapping

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def models(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    refresh = request.query.get("refresh", [""])[0].lower() in {"1", "true", "yes"}
    return ApiResponse(200, {"status": "completed", "models": context.call("model_summary", force=refresh)})


def storage(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, context.call("storage_summary"))


def storage_scan(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, context.call("storage_summary", force=True))


def applications(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, {"status": "completed", "applications": context.call("applications")})


def launch(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    status, payload = context.call("launch_application", params["application_id"])
    return ApiResponse(status, payload)


def register(router: Router) -> None:
    owner = "src/services/api/routes/compatibility.py"
    router.register(route_id="compat.models", method="GET", path="/models", domain="models", owner=owner, handler=models)
    router.register(route_id="compat.storage", method="GET", path="/api/storage", domain="storage", owner=owner, handler=storage)
    router.register(route_id="compat.storage_scan", method="POST", path="/api/storage/scan", domain="storage", owner=owner, handler=storage_scan)
    router.register(route_id="applications.list", method="GET", path="/api/applications", domain="applications", owner=owner, handler=applications)
    router.register(route_id="applications.launch", method="POST", path="/api/applications/{application_id}/launch", domain="applications", owner=owner, handler=launch)
