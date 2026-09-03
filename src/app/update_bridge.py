"""Native bridge for the two-phase, side-by-side application restart.

The HTTP update route only stages a verified candidate.  This bridge owns the
phase-2 transaction: it rechecks the close/ownership gate, commits the pointer,
publishes a session-bound watchdog contract, authorizes exactly one native
close, and then lets the watchdog relaunch the stable payload.  A failure after
pointer activation rolls the pointer back before the old desktop is allowed to
remain in an ambiguous state.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path
import secrets
import subprocess
from typing import Any

from src.app.stable_shell import StableShellError, resolve_launch_plan, resolve_verified_running_plan
from src.services.app_update import AppUpdateError, app_update_service, reason_code_for, update_error_projection
from src.services.process_manager.managed import terminate_owned_process


def _creationflags() -> int:
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))


def _restart_after_update(self: Any) -> dict[str, object]:
    install_value = os.environ.get("LOCALAIHUB_INSTALL_ROOT")
    running_value = os.environ.get("LOCALAIHUB_APP_ROOT")
    if not install_value or not running_value:
        return update_error_projection("INSTALLED_PRODUCT_REQUIRED", status="unavailable")
    install_root = Path(install_value).expanduser().absolute()
    try:
        running_plan = resolve_verified_running_plan(install_root, Path(running_value).expanduser().absolute())
    except (OSError, ValueError, StableShellError):
        return update_error_projection("RUNNING_PAYLOAD_INVALID")

    desktop_main = importlib.import_module("src.app.main")
    prepare = getattr(desktop_main, "_prepare_owned_api_close", None)
    if not callable(prepare):
        return update_error_projection("CLOSE_PREFLIGHT_UNAVAILABLE")
    try:
        detail = prepare()
    except Exception:
        detail = None
    if (
        not isinstance(detail, dict)
        or detail.get("verification") != "verified"
        or not isinstance(detail.get("active_jobs"), int)
        or isinstance(detail.get("active_jobs"), bool)
        or detail.get("active_jobs") != 0
    ):
        owner = detail.get("owner") if isinstance(detail, dict) else "unknown"
        verification = detail.get("verification") if isinstance(detail, dict) else "unknown"
        active_jobs = detail.get("active_jobs") if isinstance(detail, dict) else None
        if owner == "external" and verification != "verified":
            failure_code = "EXTERNAL_OWNER_UNVERIFIED"
        elif owner == "external" and isinstance(active_jobs, int) and not isinstance(active_jobs, bool) and active_jobs > 0:
            failure_code = "EXTERNAL_OWNER_ACTIVE"
        else:
            failure_code = "ACTIVE_OR_UNKNOWN_JOBS"
        blocked = update_error_projection(failure_code, active_jobs=active_jobs if isinstance(active_jobs, int) else None)
        blocked.update({
            "owner": owner,
            "message": detail.get("message") if isinstance(detail, dict) else "Không thể xác minh trạng thái tác vụ.",
        })
        return blocked

    service = app_update_service()
    try:
        staged = service.staged_update()
    except (AppUpdateError, StableShellError, OSError, ValueError) as exc:
        return update_error_projection(getattr(exc, "code", "STAGED_UPDATE_INVALID"))
    if staged is None:
        return update_error_projection("NO_STAGED_PAYLOAD", status="not_required")
    previous = staged.get("previous") if isinstance(staged, dict) else None
    if not isinstance(previous, dict) or previous.get("version") != running_plan.version:
        return update_error_projection(
            "STAGED_RUNNING_PAYLOAD_MISMATCH",
            current_payload_id=running_plan.version,
            candidate_payload_id=staged.get("payload_id") if isinstance(staged, dict) else None,
        )
    payload_id = str(staged.get("payload_id") or "")
    source_commit = str(staged.get("source_commit") or "")
    authorize = getattr(self, "_authorize_update_restart", None)
    abort = getattr(self, "_abort_update_restart", None)
    destroy = getattr(self, "_destroy_window", None)
    if not callable(authorize) or not callable(destroy):
        return update_error_projection(
            "DESKTOP_RESTART_TRANSACTION_UNAVAILABLE",
            transaction_id=staged.get("transaction_id") if isinstance(staged, dict) else None,
            current_payload_id=running_plan.version,
            candidate_payload_id=payload_id,
            rollback_payload_id=previous.get("version"),
        )

    committed = False
    rollback_failed = False
    watchdog: subprocess.Popen[object] | None = None
    nonce = secrets.token_hex(16)
    try:
        # The service revalidates the staged bytes and current pointer under a
        # cross-process lock.  Nothing changes in current.json before this
        # point, so a close veto leaves the running payload untouched.
        commit = service.commit_staged_restart()
        committed = commit.get("status") == "activated"
        if not committed:
            raise AppUpdateError("UPDATE_COMMIT_FAILED")
        service.create_restart_session(payload_id=payload_id, source_commit=source_commit, nonce=nonce, parent_pid=os.getpid())

        # The running desktop owns this bridge, but its payload may predate
        # the restart-runtime fix. Resolve the newly activated candidate
        # before spawning the watchdog so the first update after the fix does
        # not fall back to the old payload's PyInstaller-stub watchdog.
        try:
            candidate_plan = resolve_launch_plan(install_root)
        except (OSError, ValueError, StableShellError) as exc:
            raise AppUpdateError("CANDIDATE_LAUNCH_PLAN_INVALID") from exc
        if (
            candidate_plan.version != payload_id
            or candidate_plan.environment.get("LOCALAIHUB_BUILD_SHA") != source_commit
            or candidate_plan.environment.get("LOCALAIHUB_BUILD_PAYLOAD") != payload_id
        ):
            raise AppUpdateError("CANDIDATE_LAUNCH_IDENTITY_MISMATCH")

        launcher = install_root / "LocalAIHub.exe"
        watchdog_script = candidate_plan.app_payload / "src" / "app" / "update_watchdog.py"
        if not launcher.is_file() or launcher.is_symlink():
            raise AppUpdateError("STABLE_LAUNCHER_UNAVAILABLE")
        if not watchdog_script.is_file() or watchdog_script.is_symlink():
            raise AppUpdateError("UPDATE_WATCHDOG_UNAVAILABLE")
        if not authorize():
            raise AppUpdateError("DESKTOP_CLOSE_AUTHORIZATION_FAILED")
        session_path = install_root / "update-state" / "restart-session.json"
        environment = dict(candidate_plan.environment)
        environment.update({
            "LOCALAIHUB_WATCHDOG_INSTALL_ROOT": str(install_root),
            "LOCALAIHUB_WATCHDOG_APP_ROOT": str(candidate_plan.app_payload),
            "LOCALAIHUB_WATCHDOG_WAIT_PID": str(os.getpid()),
            "LOCALAIHUB_WATCHDOG_TIMEOUT": "30",
            "LOCALAIHUB_WATCHDOG_SESSION_PATH": str(session_path),
            "LOCALAIHUB_WATCHDOG_SESSION_NONCE": nonce,
            "LOCALAIHUB_INSTALL_ROOT": str(install_root),
            "LOCALAIHUB_APP_ROOT": str(candidate_plan.app_payload),
            "LOCALAIHUB_DATA_ROOT": str(candidate_plan.data_root),
            "PYTHONPATH": str(candidate_plan.app_payload),
            "PYTHONNOUSERSITE": "1",
            "PYTHONUTF8": "1",
        })
        watchdog = subprocess.Popen(
            [str(candidate_plan.runtime_pythonw), "-m", "src.app.update_watchdog"],
            cwd=str(candidate_plan.app_payload), env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, creationflags=_creationflags(),
        )
        if not destroy():
            raise AppUpdateError("DESKTOP_DESTROY_FAILED")
        return {"status": "completed", "payload_id": payload_id, "source_commit": source_commit, "restart": "scheduled", "close_transaction": "update_restart_committed"}
    except (AppUpdateError, OSError, ValueError) as exc:
        if watchdog is not None:
            try:
                terminate_owned_process(watchdog)
            except Exception:
                pass
        if committed:
            try:
                service.rollback_pending_restart(reason=getattr(exc, "code", "RESTART_TRANSACTION_FAILED"))
            except Exception:
                # Keep the original failure visible; the watchdog still has
                # the pending marker if rollback itself needs manual review.
                rollback_failed = True
        if callable(abort):
            try:
                abort()
            except Exception:
                pass
        code = "WATCHDOG_ROLLBACK_FAILED" if rollback_failed else getattr(exc, "code", "RESTART_TRANSACTION_FAILED")
        value = update_error_projection(
            code,
            status="error",
            transaction_id=staged.get("transaction_id") if isinstance(staged, dict) else None,
            current_payload_id=previous.get("version") if isinstance(previous, dict) else running_plan.version,
            candidate_payload_id=payload_id,
            rollback_payload_id=previous.get("version") if isinstance(previous, dict) else None,
        )
        value["reason_code"] = reason_code_for(code)
        return value


def install_update_bridge(bridge_class: type[Any]) -> None:
    """Expose one bounded restart operation without changing close APIs."""

    if getattr(bridge_class, "restart_after_update", None) is None:
        setattr(bridge_class, "restart_after_update", _restart_after_update)


__all__ = ["_restart_after_update", "install_update_bridge"]
