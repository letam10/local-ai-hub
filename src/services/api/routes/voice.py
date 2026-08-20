"""Speech and voice tool submission aliases."""

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
    owner = "src/services/api/routes/voice.py"
    for route_id, path, tool in (
        ("speech.transcribe", "/speech/transcribe", "transcribe_media"),
        ("voice.tts", "/voice/tts", "text_to_speech"),
        ("voice.design", "/voice/design", "design_voice"),
        ("voice.clone", "/voice/clone", "clone_voice"),
        ("voice.convert", "/voice/convert", "convert_voice"),
    ):
        router.register(route_id=route_id, method="POST", path=path, domain="voice", owner=owner, handler=_handler(tool))
