"""Vision and OCR Job Manager submission aliases."""

from __future__ import annotations

from typing import Mapping

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def _submit(tool: str, request: ApiRequest, context: ApiContext) -> ApiResponse:
    status, payload = context.call("submit_tool", tool, request.json(strict=True))
    return ApiResponse(status, payload)


def _handler(tool: str):
    return lambda request, context, params: _submit(tool, request, context)


def register(router: Router) -> None:
    owner = "src/services/api/routes/vision.py"
    for route_id, path, tool in (
        ("vision.parse", "/vision/ui/parse", "parse_screen"),
        ("vision.detect", "/vision/detect", "detect_objects"),
        ("vision.ground", "/vision/ground", "ground_objects"),
        ("vision.segment", "/vision/segment", "segment_image"),
        ("vision.segment_box", "/vision/segment-box", "segment_from_box"),
        ("vision.segment_points", "/vision/segment-points", "segment_from_points"),
        ("vision.track", "/vision/track", "track_video_object"),
        ("vision.ocr", "/ocr/parse", "ocr_document"),
    ):
        router.register(route_id=route_id, method="POST", path=path, domain="vision", owner=owner, handler=_handler(tool))
