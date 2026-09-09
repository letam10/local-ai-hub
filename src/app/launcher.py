"""
/*
  FILE NOTE
  - Mục đích: Desktop launcher entrypoint cho Local AI Hub — điều phối khởi động không console, kiểm tra môi trường, và chuyển tiếp vào main desktop shell
  - Liên kết trực tiếp: src/app/main.py, src/app/bootstrap.py, src/services/process_manager/windows.py, scripts/update_managed_shortcuts.ps1
  - Vùng ảnh hưởng khi sửa: Toàn bộ quá trình khởi động ứng dụng desktop từ shortcut Windows hoặc dòng lệnh
*/
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.app.bootstrap import bootstrap
from src.app.launcher_migration import reconcile_launcher_transaction
from src.app.main import DesktopBridge, main
from src.app.stable_shell import LaunchPlan, resolve_launch_plan, resolve_verified_running_plan
from src.app.update_watchdog import reconcile_restart_transaction
from src.app.update_bridge import install_update_bridge


_RECOVERY_HANDOFF_ENV_KEYS = (
    "LOCALAIHUB_RESTART_SESSION_PATH",
    "LOCALAIHUB_RESTART_SESSION_NONCE",
    "LOCALAIHUB_WATCHDOG_INSTALL_ROOT",
    "LOCALAIHUB_WATCHDOG_APP_ROOT",
    "LOCALAIHUB_WATCHDOG_WAIT_PID",
    "LOCALAIHUB_WATCHDOG_TIMEOUT",
    "LOCALAIHUB_WATCHDOG_SESSION_PATH",
    "LOCALAIHUB_WATCHDOG_SESSION_NONCE",
    "LOCALAIHUB_RESTART_WAIT_PID",
)


def _wait_for_restart_parent() -> None:
    """Let a newly launched payload wait until the previous desktop exits."""

    raw = os.environ.pop("LOCALAIHUB_RESTART_WAIT_PID", "")
    try:
        pid = int(raw)
    except (TypeError, ValueError):
        return
    if pid <= 0 or pid == os.getpid():
        return
    if os.name == "nt":
        try:
            import ctypes

            synchronize = 0x00100000
            wait_object_0 = 0
            timeout = 45_000
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(synchronize, False, pid)
            if handle:
                try:
                    result = kernel32.WaitForSingleObject(handle, timeout)
                    if result == wait_object_0:
                        return
                finally:
                    kernel32.CloseHandle(handle)
        except (AttributeError, OSError, ValueError):
            pass
        return
    deadline = time.monotonic() + 45.0
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.1)


def _same_payload_identity(running: LaunchPlan, selected: LaunchPlan) -> bool:
    """Require the running entrypoint and selected pointer to be the same payload."""

    def same_path(left: Path, right: Path) -> bool:
        return os.path.normcase(str(left.absolute())) == os.path.normcase(str(right.absolute()))

    if (
        running.version != selected.version
        or not same_path(running.payload_root, selected.payload_root)
        or not same_path(running.app_payload, selected.app_payload)
        or not same_path(running.runtime_pythonw, selected.runtime_pythonw)
    ):
        return False
    for key in ("LOCALAIHUB_BUILD_PAYLOAD", "LOCALAIHUB_BUILD_SHA"):
        expected = selected.environment.get(key)
        if expected is not None and running.environment.get(key) != expected:
            return False
    return True


def _relaunch_selected_payload(plan: LaunchPlan) -> None:
    """Start one exact selected payload, then let this stale process exit."""

    environment = dict(plan.environment)
    build_sha = environment.get("LOCALAIHUB_BUILD_SHA")
    build_payload = environment.get("LOCALAIHUB_BUILD_PAYLOAD")
    if (
        not isinstance(build_sha, str)
        or len(build_sha) != 40
        or any(char not in "0123456789abcdef" for char in build_sha)
        or build_payload != plan.version
        or plan.version != f"main-{build_sha[:12]}"
    ):
        # A legacy payload may not publish build.json.  Never let the stale
        # candidate's inherited optional identity describe that payload.
        environment.pop("LOCALAIHUB_BUILD_SHA", None)
        environment.pop("LOCALAIHUB_BUILD_PAYLOAD", None)
    for key in _RECOVERY_HANDOFF_ENV_KEYS:
        environment.pop(key, None)
    # The previous payload must wait for this candidate process to exit before
    # it enters bootstrap/main.  This keeps the recovery handoff single-owner
    # without re-entering the stable launcher or spawning a watchdog.
    environment["LOCALAIHUB_RESTART_WAIT_PID"] = str(os.getpid())
    subprocess.Popen(
        list(plan.command),
        cwd=str(plan.app_payload),
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0,
    )


def _handoff_after_recovery(install_root: Path, recovery: dict[str, object]) -> bool:
    """Do not let a payload continue after recovery selected a different one."""

    running_value = os.environ.get("LOCALAIHUB_APP_ROOT")
    running = (
        resolve_verified_running_plan(install_root, Path(running_value).expanduser().absolute())
        if running_value
        else None
    )
    restart = recovery.get("restart") if isinstance(recovery, dict) else None
    rollback_handoff = isinstance(restart, dict) and restart.get("status") == "rolled_back"
    if running is None and not rollback_handoff:
        return False
    selected = resolve_launch_plan(install_root)
    if rollback_handoff or running is None or not _same_payload_identity(running, selected):
        _relaunch_selected_payload(selected)
        return True
    return False


def _reconcile_startup_transactions() -> dict[str, object]:
    """Let the real payload entrypoint own one idempotent recovery pass.

    The restart watchdog normally completes the deferred transaction before it
    launches this payload.  A process termination can still leave a durable
    journal between two filesystem phases, so the payload performs the same
    two read/reconcile operations once at startup.  Both operations are
    transaction-bound and idempotent; no second watchdog is spawned here.
    Development/check-out launches without an installed root have no managed
    transaction to reconcile.
    """

    install_value = os.environ.get("LOCALAIHUB_INSTALL_ROOT")
    if not install_value:
        return {"status": "not_configured"}
    install_root = Path(install_value).expanduser().absolute()
    restart = reconcile_restart_transaction(install_root)
    shell = reconcile_launcher_transaction(install_root)
    return {"status": "reconciled", "restart": restart, "shell": shell}


def launch() -> int:
    """Bootstrap managed directories and run the native desktop shell."""
    _wait_for_restart_parent()
    try:
        recovery = _reconcile_startup_transactions()
        install_value = os.environ.get("LOCALAIHUB_INSTALL_ROOT")
        if install_value and _handoff_after_recovery(Path(install_value).expanduser().absolute(), recovery):
            return 0
    except (OSError, TypeError, ValueError, UnicodeError) as exc:
        # A malformed or ambiguous journal must not be hidden by launching a
        # payload that could observe an unpaired pointer/shell state.
        print(f"Startup update recovery failed: {exc}", file=sys.stderr)
        return 81
    install_update_bridge(DesktopBridge)
    try:
        bootstrap()
    except Exception as exc:
        print(f"Bootstrap initialization failed: {exc}", file=sys.stderr)
    return main()


if __name__ == "__main__":
    raise SystemExit(launch())
