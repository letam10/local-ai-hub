"""Read-only Artifact Library V2 transport adapters."""

from __future__ import annotations

from collections.abc import Mapping

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def _limit(request: ApiRequest) -> int:
    values = request.query.get("limit", [])
    if not values:
        return 120
    if len(values) != 1:
        return 120
    try:
        value = int(values[0])
    except (TypeError, ValueError):
        return 120
    return max(1, min(240, value))


def snapshot(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del params
    return ApiResponse(200, context.call("artifact_library_v2_snapshot", limit=_limit(request)))


def detail(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del request
    value = context.call("artifact_library_v2_detail", params.get("artifact_id", ""))
    if value is None:
        return ApiResponse(404, {"status": "not_found", "error": "artifact_library_v2_not_found", "execution": "not_run", "dry_run": True})
    return ApiResponse(200, value)


def register(router: Router) -> None:
    owner = "src/services/api/routes/artifact_library_v2.py"
    root = "/api/artifact-library/v2"
    router.register(route_id="artifact_library.v2_snapshot", method="GET", path=root, domain="artifacts", owner=owner, handler=snapshot)
    router.register(route_id="artifact_library.v2_detail", method="GET", path=f"{root}/{{artifact_id}}", domain="artifacts", owner=owner, handler=detail)


__all__ = ["register"]
