"""Health, bootstrap and capability route adapters."""

from __future__ import annotations

from typing import Mapping

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def _call(request: ApiRequest, context: ApiContext, name: str, *args: object, **kwargs: object) -> ApiResponse:
    return ApiResponse(200, context.call(name, *args, **kwargs))


def health(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return _call(request, context, "health", probe_gpu=False)


def bootstrap(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return _call(request, context, "bootstrap_payload")


def dashboard(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return _call(request, context, "bootstrap_payload")


def capabilities(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return _call(request, context, "capability_control_plane")


def lifecycle(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return _call(request, context, "lifecycle_payload")


def tools(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return _call(request, context, "tools_payload")


def components_compat(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, {"status": "completed", "components": context.call("component_statuses")})


def modules(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    control = context.call("capability_control_plane")
    value = control.get("module_manager") if isinstance(control, dict) else None
    return ApiResponse(200, value if isinstance(value, dict) else {"status": "unavailable", "execution": "not_run", "dry_run": True})


def module_detail(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    value = modules(request, context, params).payload
    records = value.get("modules") if isinstance(value, dict) else []
    selected = next((item for item in records if isinstance(item, dict) and item.get("id") == params.get("module_id")), None)
    if selected is None:
        return ApiResponse(404, {"status": "error", "error": "unknown_module"})
    return ApiResponse(200, {"status": "completed", "execution": "not_run", "dry_run": True, "module": selected})


def register(router: Router) -> None:
    owner = "src/services/api/routes/health.py"
    router.register(route_id="health.root", method="GET", path="/", domain="health", owner=owner, handler=health)
    router.register(route_id="health.live", method="GET", path="/health", domain="health", owner=owner, handler=health)
    router.register(route_id="dashboard.snapshot", method="GET", path="/api/dashboard", domain="dashboard", owner=owner, handler=dashboard)
    router.register(route_id="capabilities.snapshot", method="GET", path="/api/capabilities", domain="health", owner=owner, handler=capabilities)
    router.register(route_id="lifecycle.snapshot", method="GET", path="/api/lifecycle", domain="lifecycle", owner=owner, handler=lifecycle)
    router.register(route_id="tools.catalog", method="GET", path="/tools", domain="health", owner=owner, handler=tools)
    router.register(route_id="components.compat", method="GET", path="/components", domain="components", owner=owner, handler=components_compat)
    router.register(route_id="modules.list", method="GET", path="/api/modules", domain="modules", owner=owner, handler=modules)
    router.register(route_id="modules.detail", method="GET", path="/api/modules/{module_id}", domain="modules", owner=owner, handler=module_detail)
