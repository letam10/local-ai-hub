"""Backup/restore transport adapters; BackupManager owns ZIP semantics."""

from __future__ import annotations

from typing import Mapping

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def list_backups(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, {"status": "completed", "backups": context.call("backup_list")})


def create(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    result = context.call("backup_create")
    return ApiResponse(201 if result.get("accepted") else 500, result)


def inspect(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json()
    if set(body) - {"backup_id"} or not isinstance(body.get("backup_id"), str):
        return ApiResponse(400, {"status": "invalid", "error": "backup_id_required", "execution": "not_run", "dry_run": True})
    backup_id = body["backup_id"]
    result = context.call("backup_inspect", backup_id)
    return ApiResponse(200 if result.get("valid") else 400, result)


def plan(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json()
    if set(body) - {"backup_id"} or not isinstance(body.get("backup_id"), str):
        return ApiResponse(400, {"status": "invalid", "error": "backup_id_required", "execution": "not_run", "dry_run": True})
    backup_id = body["backup_id"]
    result = context.call("backup_plan", backup_id)
    return ApiResponse(200 if result.get("accepted") else 400, result)


def apply(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json()
    if set(body) - {"plan_id", "plan", "confirmed"} or ("plan_id" not in body and not isinstance(body.get("plan"), dict)):
        return ApiResponse(400, {"status": "invalid", "error": "backup_plan_id_required", "execution": "not_run", "dry_run": True})
    plan = body.get("plan") if isinstance(body.get("plan"), dict) else {}
    plan_id = body.get("plan_id") or plan.get("plan_id", "")
    result = context.call("backup_apply", plan_id, confirmed=bool(body.get("confirmed", False)))
    status = 200 if result.get("accepted") else (409 if result.get("status") == "conflict" or result.get("code") == 409 else 400)
    return ApiResponse(status, result)


def register(router: Router) -> None:
    owner = "src/services/api/routes/backups.py"
    router.register(route_id="backups.list", method="GET", path="/api/backup", domain="backups", owner=owner, handler=list_backups)
    router.register(route_id="backups.list_compat", method="GET", path="/api/backup/list", domain="backups", owner=owner, handler=list_backups)
    router.register(route_id="backups.create", method="POST", path="/api/backup/create", domain="backups", owner=owner, handler=create)
    router.register(route_id="backups.inspect", method="POST", path="/api/backup/inspect", domain="backups", owner=owner, handler=inspect)
    router.register(route_id="backups.plan", method="POST", path="/api/backup/plan", domain="backups", owner=owner, handler=plan)
    router.register(route_id="backups.apply", method="POST", path="/api/backup/apply", domain="backups", owner=owner, handler=apply)
