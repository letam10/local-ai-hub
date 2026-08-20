"""Node Studio registry and validation adapters."""

from __future__ import annotations

from typing import Mapping

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def registry(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    scope = request.query.get("scope", [""])[0]
    return ApiResponse(200, context.call("node_registry_payload", scope))


def availability(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    payload = context.call("node_registry_payload", request.query.get("scope", [""])[0])
    return ApiResponse(200, {"status": "completed", "contract_version": payload["contract_version"], "scope": payload["scope"], "availability": payload["availability"], "nodes": [{"type": item["type"], "title": item["title"], "status": item["status"], "availability": item["availability"]} for item in payload["nodes"]]})


def presets(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, {"status": "completed", "presets": context.call("node_preset_summaries")})


def preset(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    value = context.call("node_preset", params["preset_id"])
    if value is None:
        return ApiResponse(404, {"status": "error", "error": "unknown_node_preset"})
    validation = context.call("node_validate", value)
    return ApiResponse(200, {"status": "completed", "graph": validation["graph"], "validation": {"valid": validation["valid"], "errors": validation["errors"]}})


def validate(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json()
    return ApiResponse(200, {"status": "completed", "validation": context.call("node_validate", body.get("graph"), require_runnable=bool(body.get("require_runnable")))})


def dirty(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json()
    return ApiResponse(200, {"status": "completed", **context.call("node_downstream", body.get("graph"), body.get("changed_node_ids"))})


def run(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json()
    status, payload = context.call("submit_graph", body, draft=bool(body.get("draft")))
    return ApiResponse(status, payload)


def run_snapshot(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    value = context.call("node_run_snapshot", params["run_id"])
    return ApiResponse(200 if value is not None else 404, {"status": "completed", "run": value} if value is not None else {"status": "error", "error": "node_run_not_found"})


def draft_get(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    value = context.call("node_draft_load", params["scope"])
    return ApiResponse(200 if value is not None else 404, {"status": "completed", "draft": value} if value is not None else {"status": "error", "error": "node_draft_not_found", "draft": None})


def draft_save(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json()
    result = context.call("node_draft_persist", params["scope"], body.get("graph", {}))
    return ApiResponse(200 if result.get("accepted", True) else 400, result)


def draft_delete(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    context.call("node_draft_clear", params["scope"])
    return ApiResponse(200, {"status": "completed", "scope": params["scope"]})


def register(router: Router) -> None:
    owner = "src/services/api/routes/node_studio.py"
    router.register(route_id="node.registry", method="GET", path="/api/node-studio/registry", domain="node_studio", owner=owner, handler=registry)
    router.register(route_id="node.availability", method="GET", path="/api/node-studio/availability", domain="node_studio", owner=owner, handler=availability)
    router.register(route_id="node.presets", method="GET", path="/api/node-studio/presets", domain="node_studio", owner=owner, handler=presets)
    router.register(route_id="node.preset", method="GET", path="/api/node-studio/presets/{preset_id}", domain="node_studio", owner=owner, handler=preset)
    router.register(route_id="node.validate", method="POST", path="/api/node-studio/validate", domain="node_studio", owner=owner, handler=validate)
    router.register(route_id="node.dirty", method="POST", path="/api/node-studio/dirty", domain="node_studio", owner=owner, handler=dirty)
    router.register(route_id="node.run", method="POST", path="/api/node-studio/run", domain="node_studio", owner=owner, handler=run)
    router.register(route_id="node.run_snapshot", method="GET", path="/api/node-studio/runs/{run_id}", domain="node_studio", owner=owner, handler=run_snapshot)
    router.register(route_id="node.draft_get", method="GET", path="/api/node-studio/drafts/{scope}", domain="node_studio", owner=owner, handler=draft_get)
    router.register(route_id="node.draft_save", method="POST", path="/api/node-studio/drafts/{scope}", domain="node_studio", owner=owner, handler=draft_save)
    router.register(route_id="node.draft_delete", method="DELETE", path="/api/node-studio/drafts/{scope}", domain="node_studio", owner=owner, handler=draft_delete)
