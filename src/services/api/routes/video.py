"""Video workflow submission aliases."""

from __future__ import annotations

from typing import Mapping

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def _submit(tool: str, request: ApiRequest, context: ApiContext) -> ApiResponse:
    status, payload = context.call("submit_tool", tool, request.json(strict=True))
    return ApiResponse(status, payload)


def subtitle(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return _submit("create_subtitled_video", request, context)


def anime(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return _submit("upscale_anime_video", request, context)


def register(router: Router) -> None:
    owner = "src/services/api/routes/video.py"
    router.register(route_id="video.subtitle", method="POST", path="/video/subtitle", domain="video", owner=owner, handler=subtitle)
    router.register(route_id="video.anime", method="POST", path="/video/upscale/anime", domain="video", owner=owner, handler=anime)
