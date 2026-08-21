"""Media tool submission routes.

These adapters deliberately contain no media implementation.  They normalize
the public alias to the single Job Manager submission service so legacy names
cannot grow a second execution path.
"""

from __future__ import annotations

from typing import Mapping

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def _submit(tool: str, request: ApiRequest, context: ApiContext) -> ApiResponse:
    status, payload = context.call("submit_tool", tool, request.json(strict=True))
    return ApiResponse(status, payload)


def probe(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return _submit("probe_media", request, context)


def run(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return _submit("run_media_operation", request, context)


def register(router: Router) -> None:
    owner = "src/services/api/routes/media.py"
    router.register(route_id="media.probe", method="POST", path="/media/probe", domain="media", owner=owner, handler=probe)
    router.register(route_id="media.probe_alias", method="POST", path="/probe_media", domain="media", owner=owner, handler=probe)
    router.register(route_id="media.run", method="POST", path="/api/media/run", domain="media", owner=owner, handler=run)
