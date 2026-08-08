from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from Hub.config import BASE_DIR, component


def configured_path(
    component_id: str,
    field: str,
    environment_variable: str | None = None,
) -> Path | None:
    """Resolve a local path without hard-coding a particular workstation."""
    value = os.environ.get(environment_variable, "") if environment_variable else ""
    if not value:
        value = str((component(component_id) or {}).get(field) or "")
    if not value:
        return None
    return Path(os.path.expandvars(value)).expanduser()


def local_root() -> Path:
    """Return the configured repository root, defaulting to this checkout."""
    value = os.environ.get("LOCALAIHUB_ROOT") or os.environ.get("LOCAL_AI_HOME")
    return Path(os.path.expandvars(value)).expanduser() if value else BASE_DIR


def local_cache_root() -> Path:
    value = os.environ.get("LOCAL_AI_CACHE")
    return Path(os.path.expandvars(value)).expanduser() if value else local_root() / "Cache"


def describe(component_id: str) -> dict[str, Any]:
    item = component(component_id) or {"id": component_id}
    executable = item.get("executable")
    return {
        "component": component_id,
        "name": item.get("name", component_id),
        "status": item.get("status", "unknown"),
        "path": item.get("path"),
        "executable": executable,
        "executable_exists": bool(executable and Path(executable).exists()),
        "source": item.get("source"),
    }


def unavailable(component_id: str, reason: str) -> dict[str, Any]:
    return {**describe(component_id), "status": "unavailable", "reason": reason}
