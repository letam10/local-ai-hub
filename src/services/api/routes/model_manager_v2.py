"""Path-free Model Manager V2 API adapters.

This route family receives a finite action and optional opaque native-selection
ID only. It never accepts model paths, URLs, file data, checksums supplied by
the browser, or client-controlled catalog/registry records.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def _not_found() -> ApiResponse:
    return ApiResponse(404, {
        "status": "unavailable",
        "error": "model_v2_not_found",
        "execution": "not_run",
        "dry_run": True,
    })


def snapshot(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del request, params
    return ApiResponse(200, context.call("model_manager_v2_snapshot"))


def detail(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del request
    value: Any = context.call("model_manager_v2_detail", params.get("model_id", ""))
    return ApiResponse(200, value) if isinstance(value, Mapping) else _not_found()


def preflight(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del request
    value: Any = context.call("model_manager_v2_preflight", params.get("model_id", ""))
    return ApiResponse(200, value) if isinstance(value, Mapping) else _not_found()


def plan(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json(strict=True)
    if not isinstance(body, dict) or set(body) - {"action", "selection_id"} or not isinstance(body.get("action"), str):
        return ApiResponse(400, {
            "status": "invalid",
            "error": "model_v2_plan_payload_invalid",
            "execution": "not_run",
            "dry_run": True,
        })
    selection_id = body.get("selection_id")
    if selection_id is not None and not isinstance(selection_id, str):
        return ApiResponse(400, {
            "status": "invalid",
            "error": "model_v2_plan_payload_invalid",
            "execution": "not_run",
            "dry_run": True,
        })
    value: Any = context.call("model_manager_v2_plan", params.get("model_id", ""), body["action"], selection_id=selection_id)
    if not isinstance(value, Mapping):
        return _not_found()
    status = value.get("status")
    if status == "completed" or status == "planned":
        return ApiResponse(200, value)
    if status == "invalid":
        return ApiResponse(400, value)
    return ApiResponse(409, value)


def register(router: Router) -> None:
    owner = "src/services/api/routes/model_manager_v2.py"
    root = "/api/model-manager/v2"
    router.register(route_id="model_manager.v2_snapshot", method="GET", path=root, domain="models", owner=owner, handler=snapshot)
    router.register(route_id="model_manager.v2_detail", method="GET", path=f"{root}/{{model_id}}", domain="models", owner=owner, handler=detail)
    router.register(route_id="model_manager.v2_preflight", method="GET", path=f"{root}/{{model_id}}/preflight", domain="models", owner=owner, handler=preflight)
    router.register(route_id="model_manager.v2_plan", method="POST", path=f"{root}/{{model_id}}/plans", domain="models", owner=owner, handler=plan)


__all__ = ["register"]
