"""Platform Hardening V2 read-only transport."""

from __future__ import annotations

from collections.abc import Mapping

from src.services import platform_hardening_v2

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def snapshot(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del request, context, params
    return ApiResponse(200, platform_hardening_v2.snapshot())


def detail(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del request, context
    value = platform_hardening_v2.detail(params.get("area_id", ""))
    return ApiResponse(200, value) if isinstance(value, Mapping) else ApiResponse(404, {"status": "unavailable", "error": "platform_hardening_area_not_found", "execution": "not_run", "dry_run": True})


def register(router: Router) -> None:
    owner = "src/services/api/routes/platform_hardening_v2.py"
    root = "/api/platform-hardening/v2"
    router.register(route_id="platform_hardening.v2_snapshot", method="GET", path=root, domain="platform_hardening", owner=owner, handler=snapshot)
    router.register(route_id="platform_hardening.v2_detail", method="GET", path=f"{root}/{{area_id}}", domain="platform_hardening", owner=owner, handler=detail)


__all__ = ["register"]
