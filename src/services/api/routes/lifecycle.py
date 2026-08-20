"""Desktop lifecycle route ownership; shutdown remains server-owned."""

from __future__ import annotations

from typing import Mapping

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def prepare_close(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, context.call("prepare_owned_shutdown"))


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
