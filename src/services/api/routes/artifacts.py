"""Artifact metadata/open adapters; byte streaming remains transport-owned."""

from __future__ import annotations

from typing import Mapping

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def status(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    value = context.call("artifact_status", params["artifact_id"])
    return ApiResponse(200 if value.get("found") else 404, value)


def open_artifact(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    ok, message = context.call("open_artifact", params["artifact_id"])
    return ApiResponse(202 if ok else 404, {"status": "completed" if ok else "error", "message": message})


def register(router: Router) -> None:
    owner = "src/services/api/routes/artifacts.py"
    router.register(route_id="artifacts.status", method="GET", path="/api/artifacts/{artifact_id}/status", domain="artifacts", owner=owner, handler=status)
    router.register(route_id="artifacts.open", method="POST", path="/api/artifacts/{artifact_id}/open", domain="artifacts", owner=owner, handler=open_artifact)
