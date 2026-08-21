"""Model and runtime metadata adapters."""

from __future__ import annotations

from typing import Mapping

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def models(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    refresh = request.query.get("refresh", [""])[0].casefold() in {"1", "true"}
    return ApiResponse(200, {"status": "completed", "models": context.call("model_summary", force=refresh)})


def model_detail(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    try:
        return ApiResponse(200, {"status": "completed", "model": context.call("model_manager_inspect", params["model_id"])})
    except Exception:
        return ApiResponse(404, {"status": "error", "error": "unknown_model"})


def runtimes(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, context.call("runtime_manager_snapshot"))


def runtime_detail(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    try:
        return ApiResponse(200, {"status": "completed", "runtime": context.call("runtime_manager_verify", params["runtime_id"])})
    except Exception:
        return ApiResponse(404, {"status": "error", "error": "unknown_runtime"})


def register(router: Router) -> None:
    owner = "src/services/api/routes/models.py"
    router.register(route_id="models.list", method="GET", path="/api/models", domain="models", owner=owner, handler=models)
    router.register(route_id="models.detail", method="GET", path="/api/models/{model_id}", domain="models", owner=owner, handler=model_detail)
