"""Workflow Library revision-safe transport adapters."""

from __future__ import annotations

from typing import Mapping

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def _status(value: object) -> int:
    status = value.get("status") if isinstance(value, dict) else None
    return {"conflict": 409, "invalid": 400, "not_found": 404, "recovery_required": 503, "error": 500}.get(status, 200)


def list_workflows(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, context.call("workflow_library_store").list_workflows())


def get_workflow(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    value = context.call("workflow_library_store").get_workflow(params["workflow_id"])
    return ApiResponse(_status(value), value)


def mutate(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    store = context.call("workflow_library_store")
    body = request.json(strict=True)
    if params.get("action") == "import":
        result = store.import_json(body.get("content", ""), expected_revision=body.get("expected_revision"))
    elif params.get("action") == "migration_plan":
        result = store.plan_migration(body.get("entries", []))
    elif params.get("action") == "migration_confirm":
        result = store.confirm_migration(body.get("entries", []), expected_revision=body.get("expected_revision"))
    else:
        result = store.save_workflow(body.get("workflow"), expected_revision=body.get("expected_revision"))
    return ApiResponse(_status(result), result)


def delete_workflow(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json(strict=True)
    result = context.call("workflow_library_store").delete_workflow(params["workflow_id"], expected_revision=body.get("expected_revision"))
    return ApiResponse(_status(result), result)


def register(router: Router) -> None:
    owner = "src/services/api/routes/workflows.py"
    router.register(route_id="workflows.list", method="GET", path="/api/workflow-library", domain="workflows", owner=owner, handler=list_workflows)
    router.register(route_id="workflows.detail", method="GET", path="/api/workflow-library/{workflow_id}", domain="workflows", owner=owner, handler=get_workflow)
    router.register(route_id="workflows.save", method="POST", path="/api/workflow-library", domain="workflows", owner=owner, handler=mutate)
    router.register(route_id="workflows.import", method="POST", path="/api/workflow-library/import", domain="workflows", owner=owner, handler=lambda req, ctx, p: mutate(req, ctx, {"action": "import"}))
    router.register(route_id="workflows.migration_plan", method="POST", path="/api/workflow-library/migration/plan", domain="workflows", owner=owner, handler=lambda req, ctx, p: mutate(req, ctx, {"action": "migration_plan"}))
    router.register(route_id="workflows.migration_confirm", method="POST", path="/api/workflow-library/migration/confirm", domain="workflows", owner=owner, handler=lambda req, ctx, p: mutate(req, ctx, {"action": "migration_confirm"}))
    router.register(route_id="workflows.delete", method="DELETE", path="/api/workflow-library/{workflow_id}", domain="workflows", owner=owner, handler=delete_workflow)
