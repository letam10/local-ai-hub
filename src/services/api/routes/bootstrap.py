"""Model-free Core bootstrap status route."""

from __future__ import annotations

from typing import Mapping

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def status(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, context.call("bootstrap_payload"))


def register(router: Router) -> None:
    router.register(route_id="bootstrap.status", method="GET", path="/api/bootstrap", domain="bootstrap", owner="src/services/api/routes/bootstrap.py", handler=status)
