"""Creative workspace list/read adapters over Project Manager."""

from __future__ import annotations

from typing import Mapping

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def list_assets(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    query = request.query
    value = context.get("project_manager").list_assets(
        query=str(query.get("query", [""])[0]), tag=str(query.get("tag", [""])[0]),
        favorite=str(query.get("favorite", [""])[0]).lower() in {"1", "true", "yes"},
        collection_id=str(query.get("collection", [""])[0]), project_id=str(query.get("project", [""])[0]),
    )
    return ApiResponse(200, value)


def collections(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, context.get("project_manager").list_collections())


def recipes(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, context.get("project_manager").list_recipes())


def overview(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, context.get("project_manager").overview())


def gallery(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, context.get("project_manager").workflow_gallery())


def register(router: Router) -> None:
    owner = "src/services/api/routes/creative.py"
    router.register(route_id="creative.assets", method="GET", path="/api/assets", domain="creative", owner=owner, handler=list_assets)
    router.register(route_id="creative.collections", method="GET", path="/api/collections", domain="creative", owner=owner, handler=collections)
    router.register(route_id="creative.recipes", method="GET", path="/api/recipes", domain="creative", owner=owner, handler=recipes)
    router.register(route_id="creative.overview", method="GET", path="/api/creative/overview", domain="creative", owner=owner, handler=overview)
    router.register(route_id="creative.gallery", method="GET", path="/api/workflow-gallery", domain="creative", owner=owner, handler=gallery)
