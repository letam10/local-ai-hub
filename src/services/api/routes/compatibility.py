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
    # Keep startup responsive: this route only starts the bounded FAST scan.
    scanner = context.get("start_storage_scan")
    return ApiResponse(200, scanner(mode="fast") if callable(scanner) else context.call("storage_summary"))


def storage_scan(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    # Explicit Quét lại is the only route that requests the full DEEP_EXACT
    # walk.  It remains asynchronous and returns the current projection.
    scanner = context.get("start_storage_scan")
    return ApiResponse(200, scanner(force=True, mode="deep_exact") if callable(scanner) else context.call("storage_summary", force=True))


def storage_scan_cancel(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    canceller = context.get("cancel_storage_scan")
    if callable(canceller):
        scan_id = request.query.get("scan_id", [None])[0]
        return ApiResponse(202, canceller(scan_id))
    return ApiResponse(409, {"status": "unavailable", "reason": "Storage scan cancellation is unavailable."})


def storage_scan_status(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    snapshot = context.get("storage_scan_snapshot")
    return ApiResponse(200, snapshot() if callable(snapshot) else context.call("storage_summary"))


def applications(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, {"status": "completed", "applications": context.call("applications")})


def launch(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    status, payload = context.call("launch_application", params["application_id"])
    return ApiResponse(status, payload)


def close(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    status, payload = context.call("close_application", params["application_id"], params["instance_id"])
    return ApiResponse(status, payload)


def register(router: Router) -> None:
    owner = "src/services/api/routes/compatibility.py"
    router.register(route_id="compat.models", method="GET", path="/models", domain="models", owner=owner, handler=models)
    router.register(route_id="compat.storage", method="GET", path="/api/storage", domain="storage", owner=owner, handler=storage)
    router.register(route_id="compat.storage_scan", method="POST", path="/api/storage/scan", domain="storage", owner=owner, handler=storage_scan)
    router.register(route_id="compat.storage_scan_cancel", method="POST", path="/api/storage/scan/cancel", domain="storage", owner=owner, handler=storage_scan_cancel)
    router.register(route_id="compat.storage_scan_status", method="GET", path="/api/storage/scan", domain="storage", owner=owner, handler=storage_scan_status)
    router.register(route_id="applications.list", method="GET", path="/api/applications", domain="applications", owner=owner, handler=applications)
    router.register(
        route_id="applications.launch",
        method="POST",
        path="/api/applications/{application_id}/launch",
        domain="applications",
        owner=owner,
        handler=launch,
        status_codes=(202, 404, 409, 500, 503),
    )
    router.register(
        route_id="applications.close",
        method="POST",
        path="/api/applications/{application_id}/instances/{instance_id}/close",
        domain="applications",
        owner=owner,
        handler=close,
        status_codes=(200, 400, 404, 409, 500, 503),
    )
