"""Final V7 catalog and one-click lifecycle routes."""

from __future__ import annotations

from typing import Mapping

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def catalog(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    query = request.query.get("q", [""])[0]
    category = request.query.get("category", [""])[0]
    raw_installed = request.query.get("installed", [""])[0].casefold()
    installed = True if raw_installed == "true" else False if raw_installed == "false" else None
    return ApiResponse(200, context.call("productization_snapshot", query=query, category=category, installed=installed))


def catalog_detail(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    value = context.call("productization_detail", params["component_id"])
    return ApiResponse(200 if value else 404, value or {"status": "error", "error": "unknown_component_id"})


def plan(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json(strict=True)
    if set(body) != {"component_id"} or not isinstance(body.get("component_id"), str):
        return ApiResponse(400, {"status": "invalid", "error": "productization_plan_payload_invalid", "execution": "not_run", "dry_run": True})
    return ApiResponse(200, context.call("productization_plan", body["component_id"]))


def plan_lookup(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    value = context.call("productization_plan_lookup", params["plan_id"])
    return ApiResponse(200 if value else 404, value or {"status": "error", "error": "unknown_plan"})


def confirm(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json(strict=True)
    if set(body) != {"confirmed"} or type(body.get("confirmed")) is not bool:
        return ApiResponse(400, {"status": "invalid", "error": "productization_confirmation_invalid", "execution": "not_run", "dry_run": True})
    value = context.call("productization_confirm", params["plan_id"], confirmed=body["confirmed"])
    return ApiResponse(200 if value.get("status") not in {"error", "conflict"} else 409, value)


def maintenance(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json(strict=True)
    if set(body) != {"component_id", "action"} or not isinstance(body.get("component_id"), str) or body.get("action") not in {"repair", "update", "uninstall"}:
        return ApiResponse(400, {"status": "invalid", "error": "productization_maintenance_invalid", "execution": "not_run", "dry_run": True})
    return ApiResponse(200, context.call("productization_maintenance", body["component_id"], body["action"]))


def register(router: Router) -> None:
    owner = "src/services/api/routes/productization.py"
    router.register(route_id="productization.catalog", method="GET", path="/api/productization/catalog", domain="productization", owner=owner, handler=catalog)
    router.register(route_id="productization.detail", method="GET", path="/api/productization/catalog/{component_id}", domain="productization", owner=owner, handler=catalog_detail)
    router.register(route_id="productization.plan", method="POST", path="/api/productization/plans", domain="productization", owner=owner, handler=plan)
    router.register(route_id="productization.lookup", method="GET", path="/api/productization/plans/{plan_id}", domain="productization", owner=owner, handler=plan_lookup)
    router.register(route_id="productization.confirm", method="POST", path="/api/productization/plans/{plan_id}/confirm", domain="productization", owner=owner, handler=confirm)
    router.register(route_id="productization.maintenance", method="POST", path="/api/productization/maintenance", domain="productization", owner=owner, handler=maintenance)


__all__ = ["register"]
