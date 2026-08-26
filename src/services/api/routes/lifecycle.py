"""Desktop lifecycle route ownership; shutdown remains server-owned."""

from __future__ import annotations

from typing import Mapping

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def prepare_close(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    result = context.call("prepare_owned_shutdown")
    # The application service historically returned ``(status, payload)``
    # while injected/test contexts may already provide a payload dictionary.
    # Never serialize the tuple itself: the desktop close gate requires a
    # JSON object with ``status`` and ``active_jobs`` to verify shutdown.
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], int) and isinstance(result[1], dict):
        return ApiResponse(result[0], result[1])
    if isinstance(result, dict):
        status = result.get("http_status", 200)
        return ApiResponse(int(status) if isinstance(status, int) and not isinstance(status, bool) else 200, result)
    return ApiResponse(500, {"status": "error", "error": "lifecycle_prepare_close_invalid"})


def close(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, context.call("close_owned_idle"))


def cancel_and_wait(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json()
    timeout = body.get("timeout_seconds", 30)
    result = context.call("cancel_owned_jobs_and_wait", timeout)
    return ApiResponse(200 if result.get("status") == "completed" else 409, result)


def register(router: Router) -> None:
    owner = "src/services/api/routes/lifecycle.py"
    router.register(route_id="lifecycle.prepare_close", method="POST", path="/api/lifecycle/prepare-close", domain="lifecycle", owner=owner, handler=prepare_close)
    router.register(route_id="lifecycle.close", method="POST", path="/api/lifecycle/close", domain="lifecycle", owner=owner, handler=close)
    router.register(route_id="lifecycle.cancel_jobs", method="POST", path="/api/lifecycle/jobs/cancel-and-wait", domain="lifecycle", owner=owner, handler=cancel_and_wait)
