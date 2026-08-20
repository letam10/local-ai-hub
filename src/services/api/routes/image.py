"""Image generation submission aliases."""

from __future__ import annotations

from typing import Mapping

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def _submit(tool: str, request: ApiRequest, context: ApiContext) -> ApiResponse:
    status, payload = context.call("submit_tool", tool, request.json(strict=True))
    return ApiResponse(status, payload)


def flux(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return _submit("generate_flux", request, context)


def qwen(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return _submit("generate_qwen_image", request, context)


def register(router: Router) -> None:
    owner = "src/services/api/routes/image.py"
    router.register(route_id="image.flux", method="POST", path="/api/image/flux", domain="image", owner=owner, handler=flux)
    router.register(route_id="image.qwen", method="POST", path="/api/image/qwen", domain="image", owner=owner, handler=qwen)
