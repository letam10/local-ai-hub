"""Small stable Windows launcher for the installed Local AI Hub product."""

from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time

try:
    from .stable_shell import APP_USER_MODEL_ID, StableShellError, atomic_activate_pointer, resolve_launch_plan
    from src.services.process_manager.managed import terminate_owned_process
except ImportError:  # PyInstaller executes this file as a top-level script.
    from src.app.stable_shell import APP_USER_MODEL_ID, StableShellError, atomic_activate_pointer, resolve_launch_plan
    from src.services.process_manager.managed import terminate_owned_process


POST_RESTART_TIMEOUT_SECONDS = 30.0


def _pending_health_path(app_root: Path) -> Path:
    return app_root / "update-state" / "pending-health.json"


def _wait_for_pending_health(app_root: Path, process: subprocess.Popen[object], *, timeout_seconds: float = POST_RESTART_TIMEOUT_SECONDS) -> bool:
    """Wait for the new payload to prove health; never infer success from launch alone."""

    marker = _pending_health_path(app_root)
    deadline = time.monotonic() + max(1.0, min(120.0, float(timeout_seconds)))
    while marker.is_file() and time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        time.sleep(0.2)
    return not marker.exists()


def _rollback_pending(app_root: Path) -> object | None:
    marker = _pending_health_path(app_root)
    try:
        pending = json.loads(marker.read_text(encoding="utf-8"))
        previous = pending.get("previous") if isinstance(pending, dict) else None
        if not isinstance(previous, dict):
            return None
        pointer = atomic_activate_pointer(app_root, version=str(previous["version"]), manifest_sha256=str(previous["manifest_sha256"]))
        marker.unlink(missing_ok=True)
        history = app_root / "update-state" / "last-rollback.json"
        temporary = history.with_name(f".{history.name}.{os.getpid()}.tmp")
        history.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps({"schema_version": "local-ai-hub-pending-health.v1", "status": "rollback", "reason": "POST_RESTART_TIMEOUT", "payload_id": pointer["version"]}, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        os.replace(temporary, history)
        return pointer
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError, StableShellError):
        return None


def _set_app_user_model_id() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except (AttributeError, OSError):
        pass


def launch() -> int:
    """Launch the active installed payload; never fall back to dev Python."""

    _set_app_user_model_id()
    # Preserve the logical launch path for task-owned mapped candidate roots.
    # ``resolve()`` collapses a Windows SUBST path back to its physical
    # ``D:\...\Temp`` target before the stable-shell boundary can validate the
    # candidate.  ``resolve_launch_plan`` still performs the complete
    # no-reparse/manifest/path validation, so keeping the launch spelling here
    # does not weaken the installed-product safety checks.
    executable = Path(sys.executable).absolute()
    try:
        plan = resolve_launch_plan(executable.parent)
    except StableShellError:
        return 78
    try:
        child = subprocess.Popen(plan.command, cwd=plan.app_payload, env=plan.environment, close_fds=True)
    except (OSError, ValueError):
        return 79
    if _pending_health_path(plan.app_root).is_file() and not _wait_for_pending_health(plan.app_root, child):
        try:
            terminate_owned_process(child)
        except Exception:
            pass
        rollback = _rollback_pending(plan.app_root)
        if rollback is not None:
            try:
                previous = resolve_launch_plan(plan.app_root)
                subprocess.Popen(previous.command, cwd=previous.app_payload, env=previous.environment, close_fds=True)
            except (OSError, ValueError, StableShellError):
                return 80
        return 80
    return 0


if __name__ == "__main__":
    raise SystemExit(launch())
