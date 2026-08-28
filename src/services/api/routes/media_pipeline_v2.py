"""Media Pipeline V2 contract and preflight-only transport."""

from __future__ import annotations

from collections.abc import Mapping

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def contract(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del request, params
    return ApiResponse(200, context.call("media_pipeline_v2_contract"))


def preflight(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del params
    value = context.call("media_pipeline_v2_preflight", request.json(strict=True))
    status = 400 if isinstance(value, Mapping) and value.get("status") == "invalid" else 200
    return ApiResponse(status, value)


def register(router: Router) -> None:
    owner = "src/services/api/routes/media_pipeline_v2.py"
    root = "/api/media-pipeline/v2"
    router.register(route_id="media_pipeline.v2_contract", method="GET", path=root, domain="media", owner=owner, handler=contract)
    router.register(route_id="media_pipeline.v2_preflight", method="POST", path=f"{root}/preflight", domain="media", owner=owner, handler=preflight)


__all__ = ["register"]
