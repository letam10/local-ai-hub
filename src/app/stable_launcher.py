"""Small stable Windows launcher for the installed Local AI Hub product."""

from __future__ import annotations

import ctypes
from pathlib import Path
import subprocess
import sys

try:
    from .stable_shell import APP_USER_MODEL_ID, StableShellError, resolve_launch_plan
except ImportError:  # PyInstaller executes this file as a top-level script.
    from src.app.stable_shell import APP_USER_MODEL_ID, StableShellError, resolve_launch_plan


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
    executable = Path(sys.executable).resolve()
    try:
        plan = resolve_launch_plan(executable.parent)
    except StableShellError:
        return 78
    try:
        subprocess.Popen(plan.command, cwd=plan.app_payload, env=plan.environment, close_fds=True)
    except (OSError, ValueError):
        return 79
    return 0


if __name__ == "__main__":
    raise SystemExit(launch())
