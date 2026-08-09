"""Small, dependency-free validation for local Hub settings."""

from __future__ import annotations

from typing import Any


def validate_settings(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("settings must be an object")
    host = value.get("bind_host", "127.0.0.1")
    if host != "127.0.0.1":
        raise ValueError("Hub API must remain loopback-only")
    return value
