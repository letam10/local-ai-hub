"""Component Manager transport adapters (Phase 2 compatibility contract)."""

from __future__ import annotations

from typing import Mapping

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def snapshot(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, context.call("component_snapshot"))


def detail(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    try:
        return ApiResponse(200, context.call("component_detail", params["component_id"]))
    except Exception:
        return ApiResponse(404, {"status": "error", "error": "unknown_component"})


def plan_lookup(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    value = context.call("component_plan_lookup", params["plan_id"])
    return ApiResponse(200 if value else 404, value or {"status": "error", "error": "unknown_component_plan"})


def job_lookup(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    value = context.call("component_job_lookup", params["job_id"])
    return ApiResponse(200 if value else 404, value or {"status": "error", "error": "unknown_component_job"})


def install_plan(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    value = request.json(strict=True)
    allowed = {"component_id", "component_type", "variant"}
    if set(value) - allowed or not isinstance(value.get("component_id"), str) or not isinstance(value.get("component_type"), str) or (value.get("variant") is not None and not isinstance(value.get("variant"), str)):
        return ApiResponse(400, {"status": "invalid", "error": "component_plan_payload_invalid", "execution": "not_run", "dry_run": True})
    return ApiResponse(200, context.call("component_plan_install", value["component_id"], component_type=value["component_type"], variant=value.get("variant")))


def plan_verify(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    value = request.json(strict=True)
    if set(value) - {"component_id", "component_type"} or not isinstance(value.get("component_id"), str) or not isinstance(value.get("component_type"), str):
        return ApiResponse(400, {"status": "invalid", "error": "component_verify_payload_invalid", "execution": "not_run", "dry_run": True})
    return ApiResponse(200, context.call("component_plan_verify", value["component_id"], component_type=value["component_type"]))


def confirm_install(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json(strict=True)
    if set(body) - {"confirmed"} or type(body.get("confirmed")) is not bool:
        return ApiResponse(400, {"status": "invalid", "error": "component_confirmation_invalid", "execution": "not_run", "dry_run": True})
    result = context.call("component_confirm_install", params["plan_id"], confirmed=body["confirmed"])
    return ApiResponse(200 if result.get("status") not in {"invalid", "error", "conflict"} else 409, result)


def confirm_install_body(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json(strict=True)
    if set(body) - {"plan_id", "confirmed"} or not isinstance(body.get("plan_id"), str) or type(body.get("confirmed")) is not bool:
        return ApiResponse(400, {"status": "invalid", "error": "component_confirmation_invalid", "execution": "not_run", "dry_run": True})
    result = context.call("component_confirm_install", body["plan_id"], confirmed=body["confirmed"])
    return ApiResponse(200 if result.get("status") not in {"invalid", "error", "conflict"} else 409, result)


def maintenance_plan(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json(strict=True)
    action = params.get("action") or body.get("action")
    component_id = body.get("component_id")
    if set(body) - {"component_id", "action"} or not isinstance(component_id, str) or not isinstance(action, str):
        return ApiResponse(400, {"status": "invalid", "error": "component_maintenance_payload_invalid", "execution": "not_run", "dry_run": True})
    return ApiResponse(200, context.call("component_plan_maintenance", component_id, action=action))


def confirm_maintenance(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json(strict=True)
    if set(body) - {"confirmed"} or type(body.get("confirmed")) is not bool:
        return ApiResponse(400, {"status": "invalid", "error": "component_confirmation_invalid", "execution": "not_run", "dry_run": True})
    result = context.call("component_confirm_maintenance", params["plan_id"], confirmed=body["confirmed"])
    return ApiResponse(200 if result.get("status") not in {"invalid", "error", "conflict"} else 409, result)


def cancel_job(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    if request.json(strict=True):
        return ApiResponse(400, {"status": "invalid", "error": "component_cancel_payload_invalid", "execution": "not_run", "dry_run": True})
    result = context.call("component_cancel_job", params["job_id"])
    return ApiResponse(202 if result.get("status") == "cancelling" else 409, result)


def import_plan(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json(strict=True)
    if set(body) - {"selection_id", "mode"} or not isinstance(body.get("selection_id"), str) or not isinstance(body.get("mode"), str):
        return ApiResponse(400, {"status": "invalid", "error": "component_import_payload_invalid", "execution": "not_run", "dry_run": True})
    return ApiResponse(200, context.call("component_plan_import", body["selection_id"], mode=body["mode"]))


def import_confirm(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json(strict=True)
    if set(body) - {"plan_id", "confirmed"} or not isinstance(body.get("plan_id"), str) or type(body.get("confirmed")) is not bool:
        return ApiResponse(400, {"status": "invalid", "error": "component_confirmation_invalid", "execution": "not_run", "dry_run": True})
    result = context.call("component_confirm_import", body["plan_id"], confirmed=body["confirmed"])
    return ApiResponse(200 if result.get("status") not in {"invalid", "error", "conflict"} else 409, result)


def action_plan(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json(strict=True)
    action = params.get("action")
    if set(body) - {"component_id"} or not isinstance(body.get("component_id"), str) or action not in {"repair", "update", "uninstall"}:
        return ApiResponse(400, {"status": "invalid", "error": "component_maintenance_payload_invalid", "execution": "not_run", "dry_run": True})
    return ApiResponse(200, context.call("component_plan_maintenance", body["component_id"], action=action))


def register(router: Router) -> None:
    owner = "src/services/api/routes/components.py"
    router.register(route_id="components.snapshot", method="GET", path="/api/components", domain="components", owner=owner, handler=snapshot)
    router.register(route_id="components.detail", method="GET", path="/api/components/{component_id}", domain="components", owner=owner, handler=detail)
    router.register(route_id="components.plan", method="GET", path="/api/components/plans/{plan_id}", domain="components", owner=owner, handler=plan_lookup)
    router.register(route_id="components.job", method="GET", path="/api/components/jobs/{job_id}", domain="components", owner=owner, handler=job_lookup)
    router.register(route_id="components.install_plan", method="POST", path="/api/components/install/plan", domain="components", owner=owner, handler=install_plan)
    router.register(route_id="components.install_confirm", method="POST", path="/api/components/install/confirm", domain="components", owner=owner, handler=confirm_install_body)
    router.register(route_id="components.install_apply", method="POST", path="/api/components/install/apply", domain="components", owner=owner, handler=confirm_install_body)
    router.register(route_id="components.plan_confirm", method="POST", path="/api/components/plans/{plan_id}/confirm", domain="components", owner=owner, handler=confirm_install)
    router.register(route_id="components.verify_plan", method="POST", path="/api/components/verify/plan", domain="components", owner=owner, handler=plan_verify)
    router.register(route_id="components.import_plan", method="POST", path="/api/components/import/plan", domain="components", owner=owner, handler=import_plan)
    router.register(route_id="components.import_confirm", method="POST", path="/api/components/import/confirm", domain="components", owner=owner, handler=import_confirm)
    router.register(route_id="components.maintenance_plan", method="POST", path="/api/components/maintenance/plan", domain="components", owner=owner, handler=maintenance_plan)
    for action in ("repair", "update", "uninstall"):
        router.register(route_id=f"components.{action}_plan", method="POST", path=f"/api/components/{action}/plan", domain="components", owner=owner, handler=action_plan)
    router.register(route_id="components.maintenance_confirm", method="POST", path="/api/components/maintenance/{plan_id}/confirm", domain="components", owner=owner, handler=confirm_maintenance)
    router.register(route_id="components.cancel", method="POST", path="/api/components/jobs/{job_id}/cancel", domain="components", owner=owner, handler=cancel_job)
