"""Finite API transport errors without internal path/command leakage."""

from __future__ import annotations

import re
from typing import Any

from .response import ApiResponse


_CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str = "", action: str = "") -> None:
        self.status = int(status)
        self.code = code if _CODE.fullmatch(code) else "api_error"
        self.message = str(message)[:240]
        self.action = str(action)[:240]
        super().__init__(self.code)


def safe_error_response(exc: Exception) -> ApiResponse:
    if isinstance(exc, ApiError):
        payload: dict[str, Any] = {"status": "error", "error": exc.code}
        if exc.message:
            payload["message"] = exc.message
        if exc.action:
            payload["next_action"] = exc.action
        return ApiResponse(exc.status, payload)
    if isinstance(exc, ValueError):
        code = str(exc)
        code = code if _CODE.fullmatch(code) else "invalid_request"
        return ApiResponse(400, {"status": "invalid", "error": code, "execution": "not_run", "dry_run": True})
    return ApiResponse(500, {"status": "error", "error": "api_internal_error"})


__all__ = ["ApiError", "safe_error_response"]
