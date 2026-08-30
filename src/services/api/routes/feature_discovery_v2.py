"""Read-only API feature-discovery transport adapters."""

from __future__ import annotations

from typing import Any, Mapping

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def snapshot(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del request, params
    return ApiResponse(200, context.call("feature_discovery_v2_snapshot"))


def detail(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del request
    value: Any = context.call("feature_discovery_v2_detail", params.get("feature_id", ""))
    if not isinstance(value, Mapping):
        return ApiResponse(404, {
            "status": "unavailable",
            "error": "feature_discovery_v2_not_found",
            "execution": "not_run",
            "dry_run": True,
        })
    return ApiResponse(200, value)


def register(router: Router) -> None:
    owner = "src/services/api/routes/feature_discovery_v2.py"
    root = "/api/features/v2"
    router.register(route_id="feature_discovery.v2_snapshot", method="GET", path=root, domain="capabilities", owner=owner, handler=snapshot)
    router.register(route_id="feature_discovery.v2_detail", method="GET", path=f"{root}/{{feature_id}}", domain="capabilities", owner=owner, handler=detail)


__all__ = ["register"]
