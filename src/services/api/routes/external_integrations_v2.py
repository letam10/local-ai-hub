"""External Integration V2 transport with no direct desktop control."""

from __future__ import annotations

from collections.abc import Mapping

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def _missing() -> ApiResponse:
    return ApiResponse(404, {"status": "unavailable", "error": "external_integration_v2_not_found", "execution": "not_run", "dry_run": True})


def snapshot(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del request, params
    return ApiResponse(200, context.call("external_integrations_v2_snapshot"))


def detail(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del request
    value = context.call("external_integrations_v2_detail", params.get("integration_id", ""))
    return ApiResponse(200, value) if isinstance(value, Mapping) else _missing()


def launch_plan(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del request
    value = context.call("external_integrations_v2_launch_plan", params.get("integration_id", ""))
    if not isinstance(value, Mapping):
        return _missing()
    return ApiResponse(200 if value.get("status") == "completed" else 409, value)


def register(router: Router) -> None:
    owner = "src/services/api/routes/external_integrations_v2.py"
    root = "/api/external-integrations/v2"
    router.register(route_id="external_integrations.v2_snapshot", method="GET", path=root, domain="integrations", owner=owner, handler=snapshot)
    router.register(route_id="external_integrations.v2_launch_plan", method="POST", path=f"{root}/{{integration_id}}/launch-plan", domain="integrations", owner=owner, handler=launch_plan)
    router.register(route_id="external_integrations.v2_detail", method="GET", path=f"{root}/{{integration_id}}", domain="integrations", owner=owner, handler=detail)


__all__ = ["register"]
