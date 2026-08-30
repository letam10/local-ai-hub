"""Extensibility V2 read-only transport."""

from __future__ import annotations

from collections.abc import Mapping

from src.services import platform_extensibility_v2

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def snapshot(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del request, context, params
    return ApiResponse(200, platform_extensibility_v2.snapshot())


def detail(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del request, context
    value = platform_extensibility_v2.detail(params.get("area_id", ""))
    return ApiResponse(200, value) if isinstance(value, Mapping) else ApiResponse(404, {"status": "unavailable", "error": "platform_extensibility_area_not_found", "execution": "not_run", "dry_run": True})


def register(router: Router) -> None:
    owner = "src/services/api/routes/platform_extensibility_v2.py"
    root = "/api/extensibility/v2"
    router.register(route_id="extensibility.v2_snapshot", method="GET", path=root, domain="extensibility", owner=owner, handler=snapshot)
    router.register(route_id="extensibility.v2_detail", method="GET", path=f"{root}/{{area_id}}", domain="extensibility", owner=owner, handler=detail)


__all__ = ["register"]
