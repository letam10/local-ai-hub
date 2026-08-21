"""Node Studio registry and validation adapters."""

from __future__ import annotations

import re
from typing import Mapping

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router
from ...node_studio.state import public_draft


_SAFE_RESULT_STATUSES = frozenset({"completed", "error", "manual_review", "not_found"})
_SAFE_DRAFT_ID = re.compile(r"^draft_[a-z][a-z0-9_-]{0,31}\.json$")
_SAFE_RESULT_REASONS = frozenset({
    "scope_invalid",
    "graph_invalid",
    "draft_storage_unavailable",
    "draft_storage_conflict",
    "draft_manual_review",
    "draft_read_unavailable",
    "draft_write_failed",
})


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
    value = public_draft(context.call("node_draft_load", params["scope"]))
    return ApiResponse(200 if value is not None else 404, {"status": "completed", "draft": value} if value is not None else {"status": "error", "error": "node_draft_not_found", "draft": None})


def _public_mutation_result(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or not isinstance(value.get("accepted"), bool):
        return {"accepted": False, "status": "error", "reason": "draft_storage_unavailable", "execution": "not_run", "dry_run": True}
    accepted = bool(value["accepted"])
    raw_status = value.get("status")
    status = raw_status if isinstance(raw_status, str) and raw_status in _SAFE_RESULT_STATUSES else ("completed" if accepted else "error")
    result: dict[str, object] = {"accepted": accepted, "status": status}
    draft_id = value.get("draft_id")
    if isinstance(draft_id, str) and _SAFE_DRAFT_ID.fullmatch(draft_id):
        result["draft_id"] = draft_id
    if accepted:
        if value.get("deleted") is True:
            result["deleted"] = True
        return result
    raw_reason = value.get("reason")
    reason = raw_reason if isinstance(raw_reason, str) and raw_reason in _SAFE_RESULT_REASONS else "draft_storage_unavailable"
    result.update({"reason": reason, "execution": "not_run", "dry_run": True})
    return result


def draft_save(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json()
    graph = body.get("graph", {}) if isinstance(body, Mapping) else {}
    result = _public_mutation_result(context.call("node_draft_persist", params["scope"], graph))
    return ApiResponse(200 if result.get("accepted") else 400, result)


def draft_delete(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    result = _public_mutation_result(context.call("node_draft_clear", params["scope"]))
    if result.get("accepted") and result.get("status") == "not_found":
        return ApiResponse(404, {"status": "error", "error": "node_draft_not_found", "draft": None})
    return ApiResponse(200 if result.get("accepted") else 409, result)


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
