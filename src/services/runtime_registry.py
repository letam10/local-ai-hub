"""Safe, local registry for managed desktop applications.

The registry is intentionally machine-local.  It is used for applications that
Local AI Hub can launch through an explicit allowlist, including portable apps
under ``runtime`` and installer-managed external applications such as AIRI.
No request can supply a command, executable, arguments, or working directory.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from src.shared.paths.registry import CONFIG_ROOT, ROOT


LOCAL_REGISTRY = CONFIG_ROOT / "application_registry.local.json"
EXAMPLE_REGISTRY = CONFIG_ROOT / "application_registry.example.json"
APPLICATION_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _read_registry() -> dict[str, Any]:
    for path in (LOCAL_REGISTRY, EXAMPLE_REGISTRY):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            return value
    return {"applications": []}


def _as_path(value: object) -> Path | None:
    if not isinstance(value, str) or not value.strip() or value.startswith("${"):
        return None
    try:
        return Path(os.path.expandvars(value)).expanduser()
    except (OSError, ValueError):
        return None


def _allowed_arguments(value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return []
    return [item for item in value if item]


def _running_executables() -> set[str]:
    """Return executable names observed by Windows without accepting user input."""

    if os.name != "nt":
        return set()
    try:
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    names: set[str] = set()
    for line in result.stdout.splitlines():
        if not line.startswith('"'):
            continue
        name = line.split('",', 1)[0].strip('"').casefold()
        if name:
            names.add(name)
    return names


def _is_managed_here(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        path.resolve().relative_to(ROOT)
        return True
    except (OSError, ValueError):
        return False


def _public_entry(entry: dict[str, Any], running: set[str]) -> dict[str, Any]:
    executable = _as_path(entry.get("executable"))
    configured_status = str(entry.get("status") or "unknown")
    observed = "missing"
    if executable and executable.is_file():
        observed = "running" if executable.name.casefold() in running else "installed"
    elif configured_status in {"not_installed", "planned"}:
        observed = "not_installed"

    path = _as_path(entry.get("path"))
    return {
        "id": entry.get("id"),
        "display_name": entry.get("display_name") or entry.get("id"),
        "category": entry.get("category") or "application",
        "classification": entry.get("classification") or "UNKNOWN",
        "management_status": configured_status,
        "component_status": observed,
        "launchable": bool(entry.get("launch", False) and executable and executable.is_file()),
        "managed_location": "LocalAIHub" if _is_managed_here(path or executable) else "External managed",
        "notes": entry.get("notes") or "",
    }


def applications() -> list[dict[str, Any]]:
    running = _running_executables()
    result: list[dict[str, Any]] = []
    for entry in _read_registry().get("applications", []):
        if not isinstance(entry, dict) or not APPLICATION_ID.fullmatch(str(entry.get("id", ""))):
            continue
        result.append(_public_entry(entry, running))
    return result


def _entry_by_id(application_id: str) -> dict[str, Any] | None:
    if not APPLICATION_ID.fullmatch(application_id):
        return None
    for entry in _read_registry().get("applications", []):
        if isinstance(entry, dict) and entry.get("id") == application_id:
            return entry
    return None


def launch(application_id: str) -> tuple[int, dict[str, Any]]:
    """Launch an executable from the local allowlist, never from request data."""

    entry = _entry_by_id(application_id)
    if entry is None:
        return 404, {"status": "error", "error": "Unknown managed application."}
    executable = _as_path(entry.get("executable"))
    if not entry.get("launch", False) or executable is None or not executable.is_file():
        return 503, {
            "status": "unavailable",
            "application": application_id,
            "reason": "This application is not installed or is not enabled for managed launch.",
        }
    working_directory = _as_path(entry.get("working_directory"))
    if working_directory is not None and not working_directory.is_dir():
        working_directory = executable.parent
    if working_directory is None:
        working_directory = executable.parent
    try:
        process = subprocess.Popen(
            [str(executable), *_allowed_arguments(entry.get("arguments"))],
            cwd=working_directory,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    except OSError as exc:
        return 500, {"status": "error", "application": application_id, "error": str(exc)}
    return 202, {
        "status": "launching",
        "application": application_id,
        "pid": process.pid,
        "message": "The allowlisted application was started without loading any Hub model directly.",
    }
