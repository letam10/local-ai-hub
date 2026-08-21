"""Settings transport adapters; persistence remains in app_config."""

from __future__ import annotations

from typing import Mapping

from ..context import ApiContext
from ..errors import ApiError
from ..response import ApiResponse
from ..router import ApiRequest, Router


def get_settings(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, context.call("settings_payload"))


def get_schema(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, context.call("settings_schema"))


def save_settings(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    payload = request.json(strict=True)
    expected = payload.pop("expected_revision", None)
    result = context.call("settings_save", payload, expected_revision=expected)
    status = 200 if result.get("accepted") else (409 if result.get("status") == "conflict" else 400)
    return ApiResponse(status, result)


def reset_settings(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    payload = request.json()
    section = payload.get("section")
    result = context.call("settings_reset", str(section)) if section else context.call("settings_reset_all")
    return ApiResponse(200 if result.get("accepted") else 400, result)


def register(router: Router) -> None:
    owner = "src/services/api/routes/settings.py"
    router.register(route_id="settings.get", method="GET", path="/api/settings", domain="settings", owner=owner, handler=get_settings)
    router.register(route_id="settings.schema", method="GET", path="/api/settings/schema", domain="settings", owner=owner, handler=get_schema)
    router.register(route_id="settings.patch", method="PATCH", path="/api/settings", domain="settings", owner=owner, handler=save_settings)
    router.register(route_id="settings.post", method="POST", path="/api/settings", domain="settings", owner=owner, handler=save_settings)
    router.register(route_id="settings.reset", method="POST", path="/api/settings/reset", domain="settings", owner=owner, handler=reset_settings)
