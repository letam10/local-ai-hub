"""Diagnostics and recovery-draft transport adapters."""

from __future__ import annotations

from typing import Mapping

from ..response import ApiResponse
from ..router import ApiRequest, Router
from ..context import ApiContext
from src.services.diagnostics.center import PUBLIC_SUBSYSTEM_KEYS, is_public_snapshot


def _fallback_row() -> dict[str, object]:
    return {
        "status": "UNKNOWN",
        "reason": "Diagnostic snapshot unavailable or ambiguous.",
        "next_action": "Review the diagnostic source manually.",
        "execution": "not_run",
        "dry_run": True,
        "code": "diagnostic_projection_unavailable",
    }


def _sanitized_snapshot(context: ApiContext) -> dict[str, object]:
    """Select only rows from the center-owned bounded public projection."""

    try:
        value = context.call("diagnostics_snapshot")
    except Exception:
        value = None
    if not is_public_snapshot(value):
        return {key: _fallback_row() for key in PUBLIC_SUBSYSTEM_KEYS}
    return {
        key: value[key]
        for key in PUBLIC_SUBSYSTEM_KEYS
    }


def snapshot(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, {"status": "completed", "snapshot": _sanitized_snapshot(context)})


def export(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, context.call("diagnostics_export"))


def subsystem(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    subsystem_name = params["subsystem"]
    if subsystem_name not in PUBLIC_SUBSYSTEM_KEYS:
        return ApiResponse(404, {"status": "error", "error": "unknown_diagnostic_subsystem"})
    value = _sanitized_snapshot(context)[subsystem_name]
    return ApiResponse(200, {"status": "completed", "subsystem": subsystem_name, "data": value})


def recovery_drafts(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    snapshot_value = _sanitized_snapshot(context)
    return ApiResponse(200, {"status": "completed", "drafts": [], "recovery": snapshot_value["recovery_forensic"]})


def repair_verify(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, {"status": "completed", "result": _sanitized_snapshot(context)["config_registry"]})


def repair_inspect(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, {"status": "completed", "result": _sanitized_snapshot(context)["recovery_forensic"]})


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
