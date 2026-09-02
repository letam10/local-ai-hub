"""Installed-product update routes backed by successful ``main`` CI artifacts."""

from __future__ import annotations

from typing import Mapping

from src.services.app_update import AppUpdateError, app_update_service, update_error_projection

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def _error(exc: AppUpdateError) -> ApiResponse:
    code = exc.code
    status_code = 409 if code.startswith(("UPDATE_", "ROLLBACK_")) else 503
    try:
        value = app_update_service().error_projection(code)
    except Exception:
        value = update_error_projection(code)
    value.update({"status": "blocked", "code": code, "error": str(exc), "execution": "not_run"})
    return ApiResponse(status_code, value)


def _active_jobs_block(active_jobs: object) -> ApiResponse:
    value = update_error_projection("ACTIVE_JOBS_BLOCK_UPDATE", status="blocked")
    value.update({
        "error": "Không thể cập nhật khi còn job hoạt động hoặc chưa xác minh được ownership.",
        "active_jobs": active_jobs,
        "execution": "not_run",
    })
    return ApiResponse(409, value)


def _invalid(code: str) -> ApiResponse:
    value = update_error_projection(code, status="invalid")
    value["execution"] = "not_run"
    return ApiResponse(400, value)


def status(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    refresh_values = request.query.get("refresh", [])
    refresh = bool(refresh_values and refresh_values[-1] == "1")
    return ApiResponse(200, app_update_service().status(refresh=refresh))


def auth_status(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, app_update_service().auth_status())


def auth_device_start(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json(strict=True)
    if body and set(body) != set():
        return _invalid("OAUTH_REQUEST_INVALID")
    return ApiResponse(200, app_update_service().begin_device_login())


def auth_logout(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json(strict=True)
    if body and set(body) != set():
        return _invalid("OAUTH_REQUEST_INVALID")
    return ApiResponse(200, app_update_service().logout_auth())


def auth_device_poll(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json(strict=True)
    session_id = body.get("session_id") if isinstance(body, dict) else None
    if set(body) != {"session_id"} or not isinstance(session_id, str) or not session_id or len(session_id) > 128:
        return _invalid("OAUTH_SESSION_INVALID")
    return ApiResponse(200, app_update_service().poll_device_login(session_id))


def auth_device_cancel(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json(strict=True)
    session_id = body.get("session_id") if isinstance(body, dict) else None
    if set(body) != {"session_id"} or not isinstance(session_id, str) or not session_id or len(session_id) > 128:
        return _invalid("OAUTH_SESSION_INVALID")
    return ApiResponse(200, app_update_service().cancel_device_login(session_id))


def changes(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    try:
        return ApiResponse(200, app_update_service().changes())
    except AppUpdateError as exc:
        return _error(exc)


def prepare(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json(strict=True)
    if set(body) != {"confirmed"} or body.get("confirmed") is not True:
        return _invalid("UPDATE_CONFIRMATION_REQUIRED")
    try:
        health = context.call("health")
        active = health.get("active_jobs") if isinstance(health, dict) else None
        if isinstance(active, bool) or not isinstance(active, int) or active != 0:
            return _active_jobs_block(active)
        return ApiResponse(200, app_update_service().prepare())
    except AppUpdateError as exc:
        return _error(exc)


def rollback(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json(strict=True)
    if set(body) != {"confirmed"} or body.get("confirmed") is not True:
        return _invalid("ROLLBACK_CONFIRMATION_REQUIRED")
    try:
        health = context.call("health")
        active = health.get("active_jobs") if isinstance(health, dict) else None
        if isinstance(active, bool) or not isinstance(active, int) or active != 0:
            return _active_jobs_block(active)
        return ApiResponse(200, app_update_service().rollback())
    except AppUpdateError as exc:
        return _error(exc)


def register(router: Router) -> None:
    owner = "src/services/api/routes/app_updates.py"
    router.register(route_id="app_updates.status", method="GET", path="/api/app-update/status", domain="app_updates", owner=owner, handler=status)
    router.register(route_id="app_updates.auth_status", method="GET", path="/api/app-update/auth/status", domain="app_updates", owner=owner, handler=auth_status)
    router.register(route_id="app_updates.auth_device_start", method="POST", path="/api/app-update/auth/device/start", domain="app_updates", owner=owner, handler=auth_device_start)
    router.register(route_id="app_updates.auth_device_poll", method="POST", path="/api/app-update/auth/device/poll", domain="app_updates", owner=owner, handler=auth_device_poll)
    router.register(route_id="app_updates.auth_device_cancel", method="POST", path="/api/app-update/auth/device/cancel", domain="app_updates", owner=owner, handler=auth_device_cancel)
    router.register(route_id="app_updates.auth_logout", method="POST", path="/api/app-update/auth/logout", domain="app_updates", owner=owner, handler=auth_logout)
    router.register(route_id="app_updates.changes", method="GET", path="/api/app-update/changes", domain="app_updates", owner=owner, handler=changes)
    router.register(route_id="app_updates.prepare", method="POST", path="/api/app-update/prepare", domain="app_updates", owner=owner, handler=prepare)
    router.register(route_id="app_updates.rollback", method="POST", path="/api/app-update/rollback", domain="app_updates", owner=owner, handler=rollback)


__all__ = ["register"]
