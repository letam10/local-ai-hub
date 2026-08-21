"""Diagnostics and recovery-draft transport adapters."""

from __future__ import annotations

from typing import Mapping

from ..response import ApiResponse
from ..router import ApiRequest, Router
from ..context import ApiContext
from ...diagnostics.center import DIAGNOSTIC_SUBSYSTEMS, public_export_projection, public_snapshot_projection


def _snapshot(context: ApiContext) -> dict[str, dict[str, object]]:
    """Read and revalidate the center-owned snapshot before public use."""
    try:
        value = context.call("diagnostics_snapshot")
    except Exception:
        value = None
    return public_snapshot_projection(value)


def snapshot(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, {"status": "completed", "snapshot": _snapshot(context)})


def export(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    try:
        value = context.call("diagnostics_export")
    except Exception:
        value = None
    return ApiResponse(200, public_export_projection(value))


def subsystem(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    name = params.get("subsystem", "")
    if name not in DIAGNOSTIC_SUBSYSTEMS:
        return ApiResponse(404, {"status": "error", "error": "unknown_diagnostic_subsystem"})
    return ApiResponse(200, {"status": "completed", "subsystem": name, "data": _snapshot(context)[name]})


def recovery_drafts(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    recovery = _snapshot(context)["recovery_forensic"]
    category_flags = recovery.get("category_flags", {"draft": False, "temp": False, "unknown": False})
    count = recovery.get("recovery_count", 0)
    return ApiResponse(200, {
        "status": "completed",
        "drafts": {
            "count": count,
            "category_flags": category_flags,
        },
        "recovery": recovery,
    })


def repair_verify(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, {"status": "completed", "result": _snapshot(context)["config_registry"]})


def repair_inspect(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, {"status": "completed", "result": _snapshot(context)["recovery_forensic"]})


def clear_recovery_drafts(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json()
    if not bool(body.get("confirmed", False)):
        return ApiResponse(400, {"status": "unconfirmed", "error": "recovery_draft_confirmation_required"})
    scopes = body.get("scopes")
    if not isinstance(scopes, list) or not scopes or any(not isinstance(item, str) or not item for item in scopes):
        return ApiResponse(400, {"status": "invalid", "error": "recovery_draft_scopes_invalid"})
    try:
        result = context.call("clear_recovery_drafts", scopes)
    except Exception:
        return ApiResponse(503, {
            "status": "unavailable",
            "error": "recovery_clear_unavailable",
            "execution": "not_run",
            "dry_run": True,
        })
    if not isinstance(result, dict):
        return ApiResponse(503, {"status": "unavailable", "error": "recovery_clear_unavailable", "execution": "not_run", "dry_run": True})
    status = result.get("status") if result.get("status") in {"completed", "manual_review", "not_found", "unavailable"} else "manual_review"
    return ApiResponse(200 if status != "unavailable" else 503, {
        "status": status,
        "cleared": status == "completed",
        "cleared_count": result.get("cleared_count", 0),
        "manual_review_count": result.get("manual_review_count", 0),
        "results": result.get("results", []),
        "message": "Recovery draft clear request completed." if status == "completed" else "Recovery draft clear requires manual review; uncertain data was preserved.",
        "execution": "not_run" if status != "completed" else "completed",
        "dry_run": status != "completed",
    })


def register(router: Router) -> None:
    owner = "src/services/api/routes/diagnostics.py"
    router.register(route_id="diagnostics.snapshot", method="GET", path="/api/diagnostics/snapshot", domain="diagnostics", owner=owner, handler=snapshot)
    router.register(route_id="diagnostics.export", method="GET", path="/api/diagnostics/export", domain="diagnostics", owner=owner, handler=export)
    router.register(route_id="diagnostics.subsystem", method="GET", path="/api/diagnostics/subsystem/{subsystem}", domain="diagnostics", owner=owner, handler=subsystem)
    router.register(route_id="diagnostics.recovery_drafts", method="GET", path="/api/diagnostics/repair/recovery-drafts", domain="diagnostics", owner=owner, handler=recovery_drafts)
    router.register(route_id="diagnostics.verify_config", method="POST", path="/api/diagnostics/repair/verify-config", domain="diagnostics", owner=owner, handler=repair_verify)
    router.register(route_id="diagnostics.inspect_recovery", method="POST", path="/api/diagnostics/repair/inspect-recovery", domain="diagnostics", owner=owner, handler=repair_inspect)
    router.register(route_id="diagnostics.clear_recovery", method="POST", path="/api/diagnostics/repair/clear-recovery-drafts", domain="diagnostics", owner=owner, handler=clear_recovery_drafts)
