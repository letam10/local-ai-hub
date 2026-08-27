"""Read-only Capability Graph V2 transport adapters.

Every response is created by the application-owned graph service.  Browser
input is only an opaque capability ID; route adapters never create a manager,
read a model path, start a provider, or submit a lifecycle operation.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def _not_found() -> ApiResponse:
    return ApiResponse(404, {
        "status": "unavailable",
        "error": "capability_not_found",
        "execution": "not_run",
        "dry_run": True,
    })


def snapshot(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del request, params
    return ApiResponse(200, context.call("capability_graph_snapshot"))


def _detail(context: ApiContext, name: str, capability_id: str) -> ApiResponse:
    value: Any = context.call(name, capability_id)
    return ApiResponse(200, value) if isinstance(value, Mapping) else _not_found()


def capability(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del request
    return _detail(context, "capability_graph_capability", params.get("capability_id", ""))


def dependency_tree(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del request
    return _detail(context, "capability_graph_dependency_tree", params.get("capability_id", ""))


def blockers(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del request
    return _detail(context, "capability_graph_blockers", params.get("capability_id", ""))


def safe_actions(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del request
    return _detail(context, "capability_graph_safe_actions", params.get("capability_id", ""))


def verification_evidence(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del request
    return _detail(context, "capability_graph_verification_evidence", params.get("capability_id", ""))


def register(router: Router) -> None:
    owner = "src/services/api/routes/capability_graph.py"
    root = "/api/capabilities/v2"
    router.register(route_id="capabilities.v2_snapshot", method="GET", path=root, domain="capabilities", owner=owner, handler=snapshot)
    router.register(route_id="capabilities.v2_dependency_tree", method="GET", path=f"{root}/{{capability_id}}/dependency-tree", domain="capabilities", owner=owner, handler=dependency_tree)
    router.register(route_id="capabilities.v2_blockers", method="GET", path=f"{root}/{{capability_id}}/blockers", domain="capabilities", owner=owner, handler=blockers)
    router.register(route_id="capabilities.v2_safe_actions", method="GET", path=f"{root}/{{capability_id}}/safe-actions", domain="capabilities", owner=owner, handler=safe_actions)
    router.register(route_id="capabilities.v2_verification_evidence", method="GET", path=f"{root}/{{capability_id}}/verification-evidence", domain="capabilities", owner=owner, handler=verification_evidence)
    router.register(route_id="capabilities.v2_detail", method="GET", path=f"{root}/{{capability_id}}", domain="capabilities", owner=owner, handler=capability)


__all__ = ["register"]
