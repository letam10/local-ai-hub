from __future__ import annotations

from src.services.runtime_registry import application


def capability() -> dict:
    """Return the same path-free AIRI truth used by the applications route."""

    record = application("airi") or {
        "id": "airi",
        "display_name": "AIRI",
        "discovery_state": "unavailable",
        "launch_state": "unavailable",
        "launchable": False,
        "running": False,
        "running_state": "not_running",
        "reason_code": "airi_not_discovered",
        "component_status": "unavailable",
    }
    status = record.get("component_status") if record.get("component_status") in {"installed", "running"} else "unavailable"
    return {
        "component": "airi",
        "display_name": "AIRI",
        "status": status,
        "execution": "not_run",
        "discovery_state": record.get("discovery_state", "unavailable"),
        "launch_state": record.get("launch_state", "unavailable"),
        "launchable": record.get("launchable") is True,
        "running": record.get("running") is True,
        "running_state": record.get("running_state") if record.get("running_state") in {"running", "not_running", "unknown"} else "unknown",
        "reason_code": str(record.get("reason_code") or "airi_not_discovered"),
    }
