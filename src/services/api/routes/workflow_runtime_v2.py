"""Workflow Runtime V2 contract and plan-only preflight transport."""

from __future__ import annotations

from collections.abc import Mapping

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def contract(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del request, params
    return ApiResponse(200, context.call("workflow_runtime_v2_contract"))


def preflight(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del params
    value = context.call("workflow_runtime_v2_preflight", request.json(strict=True))
    status = 400 if isinstance(value, Mapping) and value.get("status") == "invalid" else 200
    return ApiResponse(status, value)


def dispatch(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del params
    value = context.call("workflow_runtime_v2_dispatch", request.json(strict=True))
    status_name = value.get("status") if isinstance(value, Mapping) else "unavailable"
    status = 202 if status_name == "accepted" else 400 if status_name == "invalid" else 503 if status_name == "unavailable" else 409
    return ApiResponse(status, value)


def register(router: Router) -> None:
    owner = "src/services/api/routes/workflow_runtime_v2.py"
    root = "/api/workflow-runtime/v2"
    router.register(route_id="workflow_runtime.v2_contract", method="GET", path=root, domain="workflows", owner=owner, handler=contract)
    router.register(route_id="workflow_runtime.v2_preflight", method="POST", path=f"{root}/preflight", domain="workflows", owner=owner, handler=preflight)
    router.register(route_id="workflow_runtime.v2_dispatch", method="POST", path=f"{root}/dispatch", domain="workflows", owner=owner, handler=dispatch)


__all__ = ["register"]
