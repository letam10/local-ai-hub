"""Component Lifecycle Engine V2 loopback transport adapters.

The route surface accepts only an opaque graph capability ID and one finite
action code.  It exposes planning/read-only information; it never accepts a
path, source URL, provider command, model bytes, or browser-supplied graph.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def _not_found() -> ApiResponse:
    return ApiResponse(404, {
        "status": "unavailable",
        "error": "lifecycle_capability_not_found",
        "execution": "not_run",
        "dry_run": True,
    })


def snapshot(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del request, params
    return ApiResponse(200, context.call("component_lifecycle_v2_snapshot"))


def detail(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del request
    value: Any = context.call("component_lifecycle_v2_detail", params.get("capability_id", ""))
    return ApiResponse(200, value) if isinstance(value, Mapping) else _not_found()


def plan(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json(strict=True)
    if not isinstance(body, dict) or set(body) != {"action"} or not isinstance(body.get("action"), str):
        return ApiResponse(400, {
            "status": "invalid",
            "error": "lifecycle_plan_payload_invalid",
            "execution": "not_run",
            "dry_run": True,
        })
    value: Any = context.call("component_lifecycle_v2_plan", params.get("capability_id", ""), body["action"])
    if not isinstance(value, Mapping):
        return _not_found()
    status = value.get("status")
    code = value.get("code")
    if status == "planned" or status == "completed":
        return ApiResponse(200, value)
    if code == "invalid_lifecycle_action":
        return ApiResponse(400, value)
    return ApiResponse(409, value)


def register(router: Router) -> None:
    owner = "src/services/api/routes/component_lifecycle_v2.py"
    root = "/api/component-lifecycle/v2"
    router.register(route_id="component_lifecycle.v2_snapshot", method="GET", path=root, domain="components", owner=owner, handler=snapshot)
    router.register(route_id="component_lifecycle.v2_detail", method="GET", path=f"{root}/{{capability_id}}", domain="components", owner=owner, handler=detail)
    router.register(route_id="component_lifecycle.v2_plan", method="POST", path=f"{root}/{{capability_id}}/plans", domain="components", owner=owner, handler=plan)


__all__ = ["register"]
