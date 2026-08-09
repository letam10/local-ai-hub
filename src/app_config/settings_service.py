"""Settings service boundary; persistence remains machine-local."""

from __future__ import annotations

from typing import Any

from src.app_config.schema import validate_settings


def normalize(settings: dict[str, Any]) -> dict[str, Any]:
    return validate_settings(dict(settings))
