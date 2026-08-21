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


def recipe_export_pack(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    ids = request.query.get("id", [])
    return ApiResponse(200, context.get("project_manager").export_recipe_pack(ids or None))


def recipe_detail(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    value = context.get("project_manager").get_recipe(params["recipe_id"])
    return ApiResponse(200 if value is not None else 404, value or {"status": "error", "error": "recipe_not_found"})


def recipe_create(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, context.get("project_manager").create_recipe(request.json()))


def recipe_import_pack(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, context.get("project_manager").import_recipe_pack(request.json()))


def recipe_apply(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, context.get("project_manager").apply_recipe(params["recipe_id"], request.json()))


def recipe_update(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, context.get("project_manager").update_recipe(params["recipe_id"], request.json()))


def asset_update(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, context.get("project_manager").update_asset(params["asset_id"], request.json()))


def collection_create(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, context.get("project_manager").create_collection(request.json()))


def collection_update(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, context.get("project_manager").update_collection(params["collection_id"], request.json()))


def search_assets(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    query = request.query
    favorite_raw = query.get("favorite", [None])[0]
    favorite = None if favorite_raw is None else favorite_raw.lower() in {"1", "true", "yes"}
    return ApiResponse(200, context.get("project_manager").search_assets(
        query=str(query.get("query", [""])[0]), tags=query.get("tag") or None,
        favorite=favorite, media_type_prefix=str(query.get("media_type", [""])[0]),
        project_id=str(query.get("project_id", [""])[0]), sort_by=str(query.get("sort_by", ["created_at"])[0]),
        sort_desc=str(query.get("sort_desc", ["true"])[0]).lower() in {"1", "true", "yes"},
    ))


def register(router: Router) -> None:
    owner = "src/services/api/routes/creative.py"
    router.register(route_id="creative.assets", method="GET", path="/api/assets", domain="creative", owner=owner, handler=list_assets)
    router.register(route_id="creative.collections", method="GET", path="/api/collections", domain="creative", owner=owner, handler=collections)
    router.register(route_id="creative.recipes", method="GET", path="/api/recipes", domain="creative", owner=owner, handler=recipes)
    router.register(route_id="creative.overview", method="GET", path="/api/creative/overview", domain="creative", owner=owner, handler=overview)
    router.register(route_id="creative.gallery", method="GET", path="/api/workflow-gallery", domain="creative", owner=owner, handler=gallery)
    router.register(route_id="creative.recipe_export_pack", method="GET", path="/api/recipes/export-pack", domain="creative", owner=owner, handler=recipe_export_pack)
    router.register(route_id="creative.recipe_detail", method="GET", path="/api/recipes/{recipe_id}", domain="creative", owner=owner, handler=recipe_detail)
    router.register(route_id="creative.recipe_create", method="POST", path="/api/recipes", domain="creative", owner=owner, handler=recipe_create)
    router.register(route_id="creative.recipe_import", method="POST", path="/api/recipes/import-pack", domain="creative", owner=owner, handler=recipe_import_pack)
    router.register(route_id="creative.recipe_apply", method="POST", path="/api/recipes/{recipe_id}/apply", domain="creative", owner=owner, handler=recipe_apply)
    router.register(route_id="creative.recipe_update", method="PUT", path="/api/recipes/{recipe_id}", domain="creative", owner=owner, handler=recipe_update)
    router.register(route_id="creative.asset_update", method="PUT", path="/api/assets/{asset_id}", domain="creative", owner=owner, handler=asset_update)
    router.register(route_id="creative.collection_create", method="POST", path="/api/collections", domain="creative", owner=owner, handler=collection_create)
    router.register(route_id="creative.collection_update", method="PUT", path="/api/collections/{collection_id}", domain="creative", owner=owner, handler=collection_update)
    router.register(route_id="creative.assets_search", method="GET", path="/api/assets/search", domain="creative", owner=owner, handler=search_assets)
