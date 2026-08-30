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


def _expected_revision(body: Mapping[str, object]) -> int | None:
    """Accept an optional optimistic-CAS revision without coercion."""

    value = body.get("expected_revision")
    if value is None:
        return None
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def set_favorite(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    """Persist only an explicit favorite marker for a stored workflow."""

    body = request.json(strict=True)
    if not isinstance(body, Mapping) or set(body) - {"favorite", "expected_revision"} or type(body.get("favorite")) is not bool:
        return ApiResponse(400, {"status": "invalid", "errors": [{"code": "workflow_library_favorite_payload_invalid", "message": "Favorite phải là boolean trong payload đã allowlist.", "action": "Gửi favorite=true hoặc favorite=false rồi thử lại."}]})
    if "expected_revision" in body and _expected_revision(body) is None:
        return ApiResponse(400, {"status": "invalid", "errors": [{"code": "workflow_library_revision_invalid", "message": "expected_revision phải là số nguyên không âm.", "action": "Tải lại Workflow Library rồi gửi revision hiện tại."}]})
    result = context.call("workflow_library_store").set_favorite(
        params["workflow_id"],
        body["favorite"],
        expected_revision=_expected_revision(body),
    )
    return ApiResponse(_status(result), result)


def mark_opened(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    """Record one explicit Library-open event; a GET remains read-only."""

    body = request.json(strict=True)
    if not isinstance(body, Mapping) or set(body) - {"expected_revision"}:
        return ApiResponse(400, {"status": "invalid", "errors": [{"code": "workflow_library_open_payload_invalid", "message": "Payload mở workflow chỉ nhận expected_revision tùy chọn.", "action": "Tải lại Workflow Library rồi thử lại."}]})
    if "expected_revision" in body and _expected_revision(body) is None:
        return ApiResponse(400, {"status": "invalid", "errors": [{"code": "workflow_library_revision_invalid", "message": "expected_revision phải là số nguyên không âm.", "action": "Tải lại Workflow Library rồi gửi revision hiện tại."}]})
    result = context.call("workflow_library_store").mark_opened(
        params["workflow_id"],
        expected_revision=_expected_revision(body),
    )
    return ApiResponse(_status(result), result)


def register(router: Router) -> None:
    owner = "src/services/api/routes/workflows.py"
    router.register(route_id="workflows.list", method="GET", path="/api/workflow-library", domain="workflows", owner=owner, handler=list_workflows)
    router.register(route_id="workflows.detail", method="GET", path="/api/workflow-library/{workflow_id}", domain="workflows", owner=owner, handler=get_workflow)
    router.register(route_id="workflows.save", method="POST", path="/api/workflow-library", domain="workflows", owner=owner, handler=mutate)
    router.register(route_id="workflows.import", method="POST", path="/api/workflow-library/import", domain="workflows", owner=owner, handler=lambda req, ctx, p: mutate(req, ctx, {"action": "import"}))
    router.register(route_id="workflows.migration_plan", method="POST", path="/api/workflow-library/migration/plan", domain="workflows", owner=owner, handler=lambda req, ctx, p: mutate(req, ctx, {"action": "migration_plan"}))
    router.register(route_id="workflows.migration_confirm", method="POST", path="/api/workflow-library/migration/confirm", domain="workflows", owner=owner, handler=lambda req, ctx, p: mutate(req, ctx, {"action": "migration_confirm"}))
    router.register(route_id="workflows.favorite", method="POST", path="/api/workflow-library/{workflow_id}/favorite", domain="workflows", owner=owner, handler=set_favorite)
    router.register(route_id="workflows.opened", method="POST", path="/api/workflow-library/{workflow_id}/opened", domain="workflows", owner=owner, handler=mark_opened)
    router.register(route_id="workflows.delete", method="DELETE", path="/api/workflow-library/{workflow_id}", domain="workflows", owner=owner, handler=delete_workflow)
