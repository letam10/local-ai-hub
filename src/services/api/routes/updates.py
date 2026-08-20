"""Manual component update-check transport.

All values are opaque component IDs and booleans.  Source checks are explicit
and bounded; no route performs automatic install or update activation.
"""

from __future__ import annotations

from typing import Mapping

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def settings(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, context.call("update_settings_get"))


def settings_save(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json(strict=True)
    if set(body) != {"policy"} or not isinstance(body.get("policy"), str):
        return ApiResponse(400, {"status": "invalid", "error": "update_settings_invalid", "execution": "not_run", "dry_run": True})
    result = context.call("update_settings_set", body["policy"])
    return ApiResponse(200 if result.get("status") == "saved" else 400, result)


def check(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json(strict=True)
    if set(body) - {"component_id", "refresh_source"} or not isinstance(body.get("component_id"), str) or ("refresh_source" in body and type(body["refresh_source"]) is not bool):
        return ApiResponse(400, {"status": "invalid", "error": "update_check_invalid", "execution": "not_run", "dry_run": True})
    return ApiResponse(200, context.call("update_check", body["component_id"], force_source_check=bool(body.get("refresh_source", False))))


def check_all(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json(strict=True)
    if set(body) - {"refresh_source"} or ("refresh_source" in body and type(body["refresh_source"]) is not bool):
        return ApiResponse(400, {"status": "invalid", "error": "update_check_all_invalid", "execution": "not_run", "dry_run": True})
    return ApiResponse(200, context.call("update_check_all", force_source_check=bool(body.get("refresh_source", False))))


def plan(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json(strict=True)
    if set(body) != {"component_id"} or not isinstance(body.get("component_id"), str):
        return ApiResponse(400, {"status": "invalid", "error": "update_plan_invalid", "execution": "not_run", "dry_run": True})
    return ApiResponse(200, context.call("update_plan", body["component_id"]))


def plan_lookup(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    value = context.call("update_plan_lookup", params["plan_id"])
    return ApiResponse(200 if value else 404, value or {"status": "error", "error": "unknown_update_plan"})


def confirm(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json(strict=True)
    if set(body) != {"confirmed"} or type(body.get("confirmed")) is not bool:
        return ApiResponse(400, {"status": "invalid", "error": "update_confirmation_invalid", "execution": "not_run", "dry_run": True})
    result = context.call("update_apply", params["plan_id"], confirmed=body["confirmed"])
    return ApiResponse(200 if result.get("status") not in {"error", "conflict"} else 409, result)


def rollback(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json(strict=True)
    if set(body) != {"component_id"} or not isinstance(body.get("component_id"), str):
        return ApiResponse(400, {"status": "invalid", "error": "rollback_invalid", "execution": "not_run", "dry_run": True})
    return ApiResponse(200, context.call("update_rollback", body["component_id"]))


def register(router: Router) -> None:
    owner = "src/services/api/routes/updates.py"
    router.register(route_id="updates.settings", method="GET", path="/api/updates/settings", domain="updates", owner=owner, handler=settings)
    router.register(route_id="updates.settings_save", method="POST", path="/api/updates/settings", domain="updates", owner=owner, handler=settings_save)
    router.register(route_id="updates.check", method="POST", path="/api/updates/check", domain="updates", owner=owner, handler=check)
    router.register(route_id="updates.check_all", method="POST", path="/api/updates/check-all", domain="updates", owner=owner, handler=check_all)
    router.register(route_id="updates.plan", method="POST", path="/api/updates/plan", domain="updates", owner=owner, handler=plan)
    router.register(route_id="updates.plan_lookup", method="GET", path="/api/updates/plans/{plan_id}", domain="updates", owner=owner, handler=plan_lookup)
    router.register(route_id="updates.confirm", method="POST", path="/api/updates/plans/{plan_id}/confirm", domain="updates", owner=owner, handler=confirm)
    router.register(route_id="updates.rollback", method="POST", path="/api/updates/rollback", domain="updates", owner=owner, handler=rollback)


__all__ = ["register"]
