"""Narrow native restart bridge used after an atomic payload update.

The stable executable is launched again only after the installed current pointer
already targets a different payload.  The new payload waits for the old desktop
PID before entering the single-instance section, so the shortcut/launcher never
needs to be rewritten during updates.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path
import subprocess
from typing import Any

from src.app.stable_shell import StableShellError, resolve_launch_plan, resolve_verified_running_plan


def _creationflags() -> int:
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))


def _restart_after_update(self: Any) -> dict[str, object]:
    install_value = os.environ.get("LOCALAIHUB_INSTALL_ROOT")
    running_value = os.environ.get("LOCALAIHUB_APP_ROOT")
    if not install_value or not running_value:
        return {"status": "unavailable", "code": "INSTALLED_PRODUCT_REQUIRED"}
    install_root = Path(install_value).expanduser().absolute()
    try:
        plan = resolve_launch_plan(install_root)
        running = Path(running_value).expanduser().absolute()
        running_plan = resolve_verified_running_plan(install_root, running)
    except (OSError, ValueError, StableShellError):
        return {"status": "blocked", "code": "UPDATE_POINTER_INVALID"}
    try:
        same_payload = plan.app_payload.resolve() == running.resolve()
    except OSError:
        same_payload = plan.app_payload.absolute() == running.absolute()
    if same_payload:
        return {"status": "not_required", "code": "NO_PENDING_PAYLOAD"}

    desktop_main = importlib.import_module("src.app.main")
    prepare = getattr(desktop_main, "_prepare_owned_api_close", None)
    if not callable(prepare):
        return {"status": "blocked", "code": "CLOSE_PREFLIGHT_UNAVAILABLE"}
    detail = prepare()
    if not isinstance(detail, dict) or detail.get("verification") != "verified" or detail.get("active_jobs") != 0:
        return {
            "status": "blocked", "code": "ACTIVE_OR_UNKNOWN_JOBS",
            "active_jobs": detail.get("active_jobs") if isinstance(detail, dict) else None,
        }

    launcher = install_root / "LocalAIHub.exe"
    if not launcher.is_file() or launcher.is_symlink():
        return {"status": "blocked", "code": "STABLE_LAUNCHER_UNAVAILABLE"}
    watchdog = running_plan.app_payload / "src" / "app" / "update_watchdog.py"
    if not watchdog.is_file() or watchdog.is_symlink():
        return {"status": "blocked", "code": "UPDATE_WATCHDOG_UNAVAILABLE"}
    environment = dict(os.environ)
    environment.update({
        "LOCALAIHUB_WATCHDOG_INSTALL_ROOT": str(install_root),
        "LOCALAIHUB_WATCHDOG_APP_ROOT": str(running_plan.app_payload),
        "LOCALAIHUB_WATCHDOG_WAIT_PID": str(os.getpid()),
        "LOCALAIHUB_WATCHDOG_TIMEOUT": "30",
        "LOCALAIHUB_INSTALL_ROOT": str(install_root),
        "LOCALAIHUB_APP_ROOT": str(running_plan.app_payload),
        "LOCALAIHUB_DATA_ROOT": str(running_plan.data_root),
        "PYTHONPATH": str(running_plan.app_payload),
        "PYTHONNOUSERSITE": "1",
        "PYTHONUTF8": "1",
    })
    try:
        subprocess.Popen(
            [str(running_plan.runtime_pythonw), "-m", "src.app.update_watchdog"],
            cwd=str(running_plan.app_payload), env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, creationflags=_creationflags(),
        )
    except OSError:
        return {"status": "error", "code": "RESTART_LAUNCH_FAILED"}
    destroy = getattr(self, "_destroy_window", None)
    if not callable(destroy):
        return {"status": "error", "code": "DESKTOP_DESTROY_UNAVAILABLE"}
    destroy()
    return {"status": "completed", "payload_id": plan.version, "restart": "scheduled"}


def install_update_bridge(bridge_class: type[Any]) -> None:
    """Expose one bounded restart operation without changing existing close APIs."""

    if getattr(bridge_class, "restart_after_update", None) is None:
        setattr(bridge_class, "restart_after_update", _restart_after_update)


__all__ = ["install_update_bridge"]
