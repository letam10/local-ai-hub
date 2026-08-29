"""Product Experience V2 transport: finite navigation, onboarding and search."""

from __future__ import annotations

from collections.abc import Mapping

from src.services import product_experience_v2

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def snapshot(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del request, context, params
    return ApiResponse(200, product_experience_v2.snapshot())


def onboarding(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del request, context, params
    return ApiResponse(200, product_experience_v2.onboarding())


def search(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del context, params
    values = request.query.get("q", [])
    query = values[0] if isinstance(values, list) and values else ""
    return ApiResponse(200, product_experience_v2.search(query))


def register(router: Router) -> None:
    owner = "src/services/api/routes/product_experience_v2.py"
    root = "/api/product-experience/v2"
    router.register(route_id="product_experience.v2_snapshot", method="GET", path=root, domain="product_experience", owner=owner, handler=snapshot)
    router.register(route_id="product_experience.v2_onboarding", method="GET", path=f"{root}/onboarding", domain="product_experience", owner=owner, handler=onboarding)
    router.register(route_id="product_experience.v2_search", method="GET", path=f"{root}/search", domain="product_experience", owner=owner, handler=search)


__all__ = ["register"]
