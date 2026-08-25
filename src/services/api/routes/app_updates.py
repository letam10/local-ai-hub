"""Installed-product update routes backed by successful ``main`` CI artifacts."""

from __future__ import annotations

from typing import Mapping

from src.services.app_update import AppUpdateError, app_update_service

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def _error(exc: AppUpdateError) -> ApiResponse:
    code = exc.code
    status = 409 if code.startswith(("UPDATE_", "ROLLBACK_")) else 503
    return ApiResponse(status, {"status": "blocked", "code": code, "error": str(exc), "execution": "not_run"})


def status(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    refresh = request.query.get("refresh") == "1"
    return ApiResponse(200, app_update_service().status(refresh=refresh))


def changes(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    try:
        return ApiResponse(200, app_update_service().changes())
    except AppUpdateError as exc:
        return _error(exc)


def prepare(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json(strict=True)
    if set(body) != {"confirmed"} or body.get("confirmed") is not True:
        return ApiResponse(400, {"status": "invalid", "code": "UPDATE_CONFIRMATION_REQUIRED", "execution": "not_run"})
    try:
        health = context.call("health")
        active = health.get("active_jobs") if isinstance(health, dict) else None
        if isinstance(active, bool) or not isinstance(active, int) or active != 0:
            return ApiResponse(409, {"status": "blocked", "code": "ACTIVE_JOBS_BLOCK_UPDATE", "active_jobs": active, "execution": "not_run"})
        return ApiResponse(200, app_update_service().prepare())
    except AppUpdateError as exc:
        return _error(exc)


def rollback(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json(strict=True)
    if set(body) != {"confirmed"} or body.get("confirmed") is not True:
        return ApiResponse(400, {"status": "invalid", "code": "ROLLBACK_CONFIRMATION_REQUIRED", "execution": "not_run"})
    try:
        health = context.call("health")
        active = health.get("active_jobs") if isinstance(health, dict) else None
        if isinstance(active, bool) or not isinstance(active, int) or active != 0:
            return ApiResponse(409, {"status": "blocked", "code": "ACTIVE_JOBS_BLOCK_UPDATE", "active_jobs": active, "execution": "not_run"})
        return ApiResponse(200, app_update_service().rollback())
    except AppUpdateError as exc:
        return _error(exc)


def register(router: Router) -> None:
    owner = "src/services/api/routes/app_updates.py"
    router.register(route_id="app_updates.status", method="GET", path="/api/app-update/status", domain="app_updates", owner=owner, handler=status)
    router.register(route_id="app_updates.changes", method="GET", path="/api/app-update/changes", domain="app_updates", owner=owner, handler=changes)
    router.register(route_id="app_updates.prepare", method="POST", path="/api/app-update/prepare", domain="app_updates", owner=owner, handler=prepare)
    router.register(route_id="app_updates.rollback", method="POST", path="/api/app-update/rollback", domain="app_updates", owner=owner, handler=rollback)


__all__ = ["register"]
