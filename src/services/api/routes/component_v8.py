"""V8 component-operation and trusted-source control-plane routes.

These adapters expose only opaque operation IDs and path-free catalog
acceptance metadata. They do not create managers, start provider discovery or
component work on GET, and confirmation still requires an explicit boolean.
"""

from __future__ import annotations

from typing import Mapping

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def _bounded_limit(request: ApiRequest) -> int | None:
    values = request.query.get("limit")
    if values is None:
        return 100
    if len(values) != 1:
        return None
    try:
        value = int(values[0])
    except (TypeError, ValueError):
        return None
    return value if 1 <= value <= 500 else None


def operations(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del params
    limit = _bounded_limit(request)
    if limit is None:
        return ApiResponse(400, {"status": "invalid", "error": "component_operation_limit_invalid", "execution": "not_run", "dry_run": True})
    return ApiResponse(200, context.call("component_operations", limit=limit))


def operation_lookup(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del request
    value = context.call("component_operation", params["operation_id"])
    return ApiResponse(200 if value else 404, value or {"status": "error", "error": "unknown_component_operation"})


def operation_confirm(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json(strict=True)
    if set(body) != {"confirmed"} or type(body.get("confirmed")) is not bool:
        return ApiResponse(400, {"status": "invalid", "error": "component_confirmation_invalid", "execution": "not_run", "dry_run": True})
    result = context.call("component_confirm_operation", params["operation_id"], confirmed=body["confirmed"])
    status = 200 if result.get("status") not in {"invalid", "error", "conflict", "unavailable"} else 409
    return ApiResponse(status, result)


def operation_cancel(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    if request.json(strict=True):
        return ApiResponse(400, {"status": "invalid", "error": "component_cancel_payload_invalid", "execution": "not_run", "dry_run": True})
    result = context.call("component_cancel_operation", params["operation_id"])
    return ApiResponse(200 if result.get("status") == "cancelled" else 409, result)


def source_acceptance_snapshot(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del request, params
    return ApiResponse(200, context.call("component_source_acceptance_snapshot"))


def source_acceptance_detail(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del request
    try:
        return ApiResponse(200, context.call("component_source_acceptance", params["component_id"]))
    except ValueError as exc:
        code = str(exc)
        return ApiResponse(404 if code == "unknown_component" else 400, {"status": "invalid", "error": code, "execution": "not_run", "dry_run": True})


def register(router: Router) -> None:
    owner = "src/services/api/routes/component_v8.py"
    router.register(route_id="components.v8_operations", method="GET", path="/api/components/operations", domain="components", owner=owner, handler=operations)
    router.register(route_id="components.v8_operation_lookup", method="GET", path="/api/components/operations/{operation_id}", domain="components", owner=owner, handler=operation_lookup)
    router.register(route_id="components.v8_operation_confirm", method="POST", path="/api/components/operations/{operation_id}/confirm", domain="components", owner=owner, handler=operation_confirm)
    router.register(route_id="components.v8_operation_cancel", method="POST", path="/api/components/operations/{operation_id}/cancel", domain="components", owner=owner, handler=operation_cancel)
    router.register(route_id="components.v8_source_acceptance", method="GET", path="/api/components/source-acceptance", domain="components", owner=owner, handler=source_acceptance_snapshot)
    router.register(route_id="components.v8_source_acceptance_detail", method="GET", path="/api/components/{component_id}/source-acceptance", domain="components", owner=owner, handler=source_acceptance_detail)


__all__ = ["register"]
