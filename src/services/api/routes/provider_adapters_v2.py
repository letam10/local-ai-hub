"""Provider Adapter V2 transport: inspection and typed preflight only."""

from __future__ import annotations

from collections.abc import Mapping

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def _missing() -> ApiResponse:
    return ApiResponse(404, {"status": "unavailable", "error": "provider_adapter_v2_not_found", "execution": "not_run", "dry_run": True})


def snapshot(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del request, params
    return ApiResponse(200, context.call("provider_adapters_v2_snapshot"))


def detail(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del request
    value = context.call("provider_adapters_v2_detail", params.get("adapter_id", ""))
    return ApiResponse(200, value) if isinstance(value, Mapping) else _missing()


def preflight(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    value = context.call("provider_adapters_v2_preflight", params.get("adapter_id", ""), request.json(strict=True))
    if not isinstance(value, Mapping):
        return _missing()
    return ApiResponse(400 if value.get("status") == "invalid" else 200, value)


def register(router: Router) -> None:
    owner = "src/services/api/routes/provider_adapters_v2.py"
    root = "/api/provider-adapters/v2"
    router.register(route_id="provider_adapters.v2_snapshot", method="GET", path=root, domain="providers", owner=owner, handler=snapshot)
    router.register(route_id="provider_adapters.v2_preflight", method="POST", path=f"{root}/{{adapter_id}}/preflight", domain="providers", owner=owner, handler=preflight)
    router.register(route_id="provider_adapters.v2_detail", method="GET", path=f"{root}/{{adapter_id}}", domain="providers", owner=owner, handler=detail)


__all__ = ["register"]
