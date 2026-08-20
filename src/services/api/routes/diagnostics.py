"""Diagnostics and recovery-draft transport adapters."""

from __future__ import annotations

from typing import Mapping

from ..response import ApiResponse
from ..router import ApiRequest, Router
from ..context import ApiContext


def snapshot(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, {"status": "completed", "snapshot": context.call("diagnostics_snapshot")})


def export(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, context.call("diagnostics_export"))


def subsystem(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    value = context.call("diagnostics_subsystem", params["subsystem"])
    return ApiResponse(200 if value is not None else 404, value or {"status": "error", "error": "unknown_diagnostic_subsystem"})


def recovery_drafts(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, context.call("diagnostics_recovery_drafts"))


def repair_verify(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, {"status": "completed", "result": context.call("diagnostics_config_registry")})


def repair_inspect(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, {"status": "completed", "result": context.call("diagnostics_recovery_state")})


def clear_recovery_drafts(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json()
    if not bool(body.get("confirmed", False)):
        return ApiResponse(400, {"status": "unconfirmed", "error": "recovery_draft_confirmation_required"})
    scopes = body.get("scopes")
    if not isinstance(scopes, list) or not scopes or any(not isinstance(item, str) or not item for item in scopes):
        return ApiResponse(400, {"status": "invalid", "error": "recovery_draft_scopes_invalid"})
    return ApiResponse(200, context.call("clear_recovery_drafts", scopes))


def register(router: Router) -> None:
    owner = "src/services/api/routes/diagnostics.py"
    router.register(route_id="diagnostics.snapshot", method="GET", path="/api/diagnostics/snapshot", domain="diagnostics", owner=owner, handler=snapshot)
    router.register(route_id="diagnostics.export", method="GET", path="/api/diagnostics/export", domain="diagnostics", owner=owner, handler=export)
    router.register(route_id="diagnostics.subsystem", method="GET", path="/api/diagnostics/subsystem/{subsystem}", domain="diagnostics", owner=owner, handler=subsystem)
    router.register(route_id="diagnostics.recovery_drafts", method="GET", path="/api/diagnostics/repair/recovery-drafts", domain="diagnostics", owner=owner, handler=recovery_drafts)
    router.register(route_id="diagnostics.verify_config", method="POST", path="/api/diagnostics/repair/verify-config", domain="diagnostics", owner=owner, handler=repair_verify)
    router.register(route_id="diagnostics.inspect_recovery", method="POST", path="/api/diagnostics/repair/inspect-recovery", domain="diagnostics", owner=owner, handler=repair_inspect)
    router.register(route_id="diagnostics.clear_recovery", method="POST", path="/api/diagnostics/repair/clear-recovery-drafts", domain="diagnostics", owner=owner, handler=clear_recovery_drafts)
