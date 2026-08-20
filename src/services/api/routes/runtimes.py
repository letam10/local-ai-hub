"""Runtime Manager transport adapters."""

from __future__ import annotations

from typing import Mapping

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def list_runtimes(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, context.call("runtime_manager_snapshot"))


def detail(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    try:
        return ApiResponse(200, {"status": "completed", "runtime": context.call("runtime_manager_verify", params["runtime_id"])})
    except Exception:
        return ApiResponse(404, {"status": "error", "error": "unknown_runtime"})


def register(router: Router) -> None:
    owner = "src/services/api/routes/runtimes.py"
    router.register(route_id="runtimes.list", method="GET", path="/api/runtimes", domain="runtimes", owner=owner, handler=list_runtimes)
    router.register(route_id="runtimes.detail", method="GET", path="/api/runtimes/{runtime_id}", domain="runtimes", owner=owner, handler=detail)
