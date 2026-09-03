"""Updater-owned restart watchdog independent of the stable launcher binary.

The desktop payload starts this helper with its *old* bundled runtime before it
closes.  The helper waits for that exact desktop PID, starts the stable EXE,
proves the newly selected payload through the loopback health/build identity,
and restores the verified previous pointer exactly once on failure.  It never
enumerates or terminates an unowned process or listener.
"""

from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.error
import urllib.request

from src.app.stable_shell import (
    POINTER_SCHEMA,
    StableShellError,
    atomic_activate_pointer,
    load_current_pointer,
    resolve_launch_plan,
)
from src.services.process_manager.managed import terminate_owned_process
from src.services.app_update import _try_write_update_state, _update_serialization_lock, reason_code_for
from src.shared.runtime_identity import API_PROTOCOL_VERSION, api_identity
from src.shared.version import PRODUCT_VERSION


WATCHDOG_TIMEOUT_SECONDS = 30.0
RESTART_SESSION_SCHEMA = "local-ai-hub-restart-session.v1"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_PAYLOAD_RE = re.compile(r"^main-[0-9a-f]{12}$")
_TRANSACTION_RE = re.compile(r"^txn-[0-9a-f]{32}$")


def _json(path: Path, *, limit: int = 64 * 1024) -> dict[str, object]:
    raw = path.read_bytes()
    if len(raw) > limit:
        raise ValueError("json_too_large")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("json_object_required")
    return value


def _atomic_json(path: Path, value: dict[str, object]) -> None:
    raw = (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            kernel = ctypes.windll.kernel32
            handle = kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            code = ctypes.c_ulong()
            try:
                if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return False
                return code.value == 259  # STILL_ACTIVE
            finally:
                kernel.CloseHandle(handle)
        except (AttributeError, OSError):
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _wait_for_pid_exit(pid: int, deadline: float) -> bool:
    while _pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    return not _pid_alive(pid)


def _port() -> int:
    try:
        value = int(os.environ.get("LOCALAIHUB_PORT") or 8765)
    except (TypeError, ValueError):
        return 8765
    return value if 1024 <= value <= 65535 else 8765


def _session_path() -> Path | None:
    value = os.environ.get("LOCALAIHUB_WATCHDOG_SESSION_PATH")
    if not value:
        return None
    path = Path(value).expanduser().absolute()
    return path if path.name == "restart-session.json" else None


def _session_snapshot() -> dict[str, object] | None:
    path = _session_path()
    nonce = os.environ.get("LOCALAIHUB_WATCHDOG_SESSION_NONCE")
    if path is None or not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{32}", nonce):
        return None
    try:
        value = _json(path, limit=64 * 1024)
    except (OSError, UnicodeError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    if value.get("schema_version") != RESTART_SESSION_SCHEMA or value.get("nonce") != nonce:
        return None
    transaction_id = value.get("transaction_id")
    if transaction_id is not None and (not isinstance(transaction_id, str) or _TRANSACTION_RE.fullmatch(transaction_id) is None):
        return None
    port = value.get("api_port")
    api_pid = value.get("api_pid")
    if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
        return None
    if isinstance(api_pid, bool) or not isinstance(api_pid, int) or api_pid <= 0:
        return None
    return value


def _session_port() -> int | None:
    value = _session_snapshot()
    return int(value["api_port"]) if value is not None else None


def _pending_health_path(app_root: Path) -> Path:
    return app_root / "update-state" / "pending-health.json"


def _target_identity(app_root: Path, *, require_build: bool = False) -> tuple[str, str | None, dict[str, str]]:
    pointer = load_current_pointer(app_root)
    version = str(pointer["version"])
    if not version or len(version) > 32 or any(char in version for char in "\\/:\0"):
        raise StableShellError("WATCHDOG_TARGET_INVALID")
    plan = resolve_launch_plan(app_root)
    try:
        build = _json(plan.payload_root / "build.json") if (plan.payload_root / "build.json").is_file() else {}
    except (OSError, UnicodeError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        if require_build:
            raise StableShellError("WATCHDOG_BUILD_INVALID")
        build = {}
    source_commit = str(build.get("source_commit") or "")
    if not _SHA_RE.fullmatch(source_commit) or version != f"main-{source_commit[:12]}":
        if require_build:
            raise StableShellError("WATCHDOG_BUILD_INVALID")
        source_commit = ""
    expected = api_identity(
        product_version=PRODUCT_VERSION,
        installation_root=plan.app_root,
        app_root=plan.app_payload,
        data_root=plan.data_root,
    )
    return version, source_commit, expected


def _health_matches(
    app_root: Path,
    *,
    version: str,
    source_commit: str | None,
    expected: dict[str, str],
    port: int | None = None,
    require_frontend: bool = True,
) -> bool:
    try:
        selected_port = _session_port() if port is None else port
        if selected_port is None:
            selected_port = _port()
        with urllib.request.urlopen(f"http://127.0.0.1:{selected_port}/health", timeout=0.5) as response:
            value = json.loads(response.read(128 * 1024 + 1).decode("utf-8"))
        return (
            isinstance(value, dict)
            and value.get("status") == "healthy"
            and all(value.get(key) == item for key, item in expected.items())
            and value.get("api_protocol_version") == API_PROTOCOL_VERSION
            and (not source_commit or (value.get("build_source_commit") == source_commit and value.get("build_payload_id") == version))
            and (not require_frontend or not _pending_health_path(app_root).exists())
        )
    except (OSError, UnicodeError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, urllib.error.URLError, urllib.error.HTTPError):
        return False


def _wait_for_target_health(app_root: Path, *, version: str, source_commit: str | None, expected: dict[str, str], deadline: float, process: subprocess.Popen[object] | None = None) -> bool:
    marker = app_root / "update-state" / "pending-health.json"
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None and marker.exists():
            return False
        session = _session_snapshot()
        if _session_path() is not None and session is None:
            # A restart-session contract was requested; never fall back to
            # probing the old/default port while the candidate has not yet
            # authenticated and published its selected loopback port.
            time.sleep(0.2)
            continue
        if session is not None and session.get("payload_id") != version:
            time.sleep(0.2)
            continue
        if session is not None and source_commit and session.get("source_commit") != source_commit:
            time.sleep(0.2)
            continue
        if _health_matches(app_root, version=version, source_commit=source_commit, expected=expected, port=_session_port()) and not marker.exists():
            return True
        time.sleep(0.2)
    return False


def _rollback_previous(app_root: Path, *, reason: str, reason_code: str | None = None) -> bool:
    marker = app_root / "update-state" / "pending-health.json"
    history = app_root / "update-state" / "previous-current.json"
    try:
        with _update_serialization_lock(app_root):
            previous = _json(history)
            if set(previous) != {"schema_version", "version", "payload_relative", "manifest_sha256"} or previous.get("schema_version") != POINTER_SCHEMA:
                return False
            pending: dict[str, object] = {}
            try:
                pending = _json(marker) if marker.is_file() and not marker.is_symlink() else {}
            except (OSError, UnicodeError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
                pending = {}
            pointer = atomic_activate_pointer(app_root, version=str(previous["version"]), manifest_sha256=str(previous["manifest_sha256"]))
            _atomic_json(app_root / "update-state" / "last-rollback.json", {
                "schema_version": "local-ai-hub-pending-health.v1",
                "status": "rollback",
                "reason": reason,
                "payload_id": pointer["version"],
            })
            try:
                marker.unlink()
            except FileNotFoundError:
                pass
            session = _session_path()
            if session is not None:
                try:
                    session.unlink()
                except FileNotFoundError:
                    pass
            _try_write_update_state(
                app_root,
                phase="rolled_back",
                progress=100,
                transaction_id=pending.get("transaction_id") if isinstance(pending.get("transaction_id"), str) else None,
                current_payload_id=pointer.get("version"),
                candidate_payload_id=pending.get("payload_id") if isinstance(pending.get("payload_id"), str) else None,
                rollback_payload_id=pointer.get("version"),
                reason_code=reason_code_for(reason_code or reason),
                last_error_code=reason if re.fullmatch(r"[A-Z][A-Z0-9_]{2,95}", reason) else "WATCHDOG_ROLLBACK_FAILED",
            )
            return True
    except (OSError, UnicodeError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError, StableShellError):
        _try_write_update_state(
            app_root,
            phase="error",
            progress=0,
            reason_code="watchdog_rollback_failed",
            last_error_code="WATCHDOG_ROLLBACK_FAILED",
        )
        return False


def _launch_stable(app_root: Path) -> subprocess.Popen[object]:
    """Launch the selected payload without re-entering the PyInstaller stub.

    The stable ``LocalAIHub.exe`` remains the normal shortcut entrypoint.  A
    restart watchdog, however, already owns a validated installation root and
    payload pointer.  Starting that one-file stub during the narrow restart
    window can fail before Python starts when its ``_MEI`` extraction is
    transiently unavailable.  Launching the exact bundled ``pythonw.exe``
    selected by :func:`resolve_launch_plan` keeps the same manifest/path
    validation while removing that bootloader dependency from recovery.
    """

    launcher = app_root / "LocalAIHub.exe"
    if not launcher.is_file() or launcher.is_symlink():
        raise OSError("stable_launcher_unavailable")
    plan = resolve_launch_plan(app_root)
    environment = dict(plan.environment)
    # The payload waits for the exact old desktop PID, while the watchdog
    # itself remains alive to own rollback/health decisions.
    wait_pid = environment.get("LOCALAIHUB_WATCHDOG_WAIT_PID")
    if wait_pid:
        environment["LOCALAIHUB_RESTART_WAIT_PID"] = wait_pid
    environment.pop("LOCALAIHUB_WATCHDOG_WAIT_PID", None)
    # The watchdog owns the restart-session authentication values while the
    # new payload owns publishing its selected API port.  Bridge the private
    # watchdog names into the payload names explicitly; without this mapping
    # the new desktop never updates api_port/api_pid and the watchdog cannot
    # authenticate a fallback-port candidate.
    watchdog_session_path = environment.get("LOCALAIHUB_WATCHDOG_SESSION_PATH")
    watchdog_session_nonce = environment.get("LOCALAIHUB_WATCHDOG_SESSION_NONCE")
    if (
        isinstance(watchdog_session_path, str)
        and Path(watchdog_session_path).name == "restart-session.json"
        and isinstance(watchdog_session_nonce, str)
        and re.fullmatch(r"[0-9a-f]{32}", watchdog_session_nonce)
    ):
        environment["LOCALAIHUB_RESTART_SESSION_PATH"] = watchdog_session_path
        environment["LOCALAIHUB_RESTART_SESSION_NONCE"] = watchdog_session_nonce
    else:
        environment.pop("LOCALAIHUB_RESTART_SESSION_PATH", None)
        environment.pop("LOCALAIHUB_RESTART_SESSION_NONCE", None)
    # Old stable launcher binaries may not yet know the build-health fields.
    # Inject the already-validated current payload identity into the inherited
    # child environment so the API handshake remains exact without rebuilding
    # the launcher executable first.
    try:
        build_path = plan.payload_root / "build.json"
        build = _json(build_path) if build_path.is_file() else {}
        commit = str(build.get("source_commit") or "")
        version = str(plan.version)
        if _SHA_RE.fullmatch(commit) and version == f"main-{commit[:12]}":
            environment.update({"LOCALAIHUB_BUILD_SHA": commit, "LOCALAIHUB_BUILD_PAYLOAD": version})
        else:
            environment.pop("LOCALAIHUB_BUILD_SHA", None)
            environment.pop("LOCALAIHUB_BUILD_PAYLOAD", None)
    except (OSError, UnicodeError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, StableShellError):
        environment.pop("LOCALAIHUB_BUILD_SHA", None)
        environment.pop("LOCALAIHUB_BUILD_PAYLOAD", None)
    return subprocess.Popen(
        list(plan.command),
        cwd=str(plan.app_payload),
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0,
    )


def run(*, app_root: Path, wait_pid: int, timeout_seconds: float = WATCHDOG_TIMEOUT_SECONDS) -> dict[str, object]:
    deadline = time.monotonic() + max(5.0, min(120.0, float(timeout_seconds)))
    if wait_pid and not _wait_for_pid_exit(wait_pid, deadline):
        rolled_back = _rollback_previous(app_root, reason="WATCHDOG_PARENT_TIMEOUT_ROLLBACK")
        return {"status": "rolled_back" if rolled_back else "failed", "code": "WATCHDOG_PARENT_TIMEOUT_ROLLBACK" if rolled_back else "WATCHDOG_PARENT_TIMEOUT"}
    try:
        target_version, target_commit, target_identity = _target_identity(app_root, require_build=True)
        child = _launch_stable(app_root)
    except (OSError, StableShellError, ValueError, TypeError, json.JSONDecodeError) as exc:
        rolled_back = _rollback_previous(app_root, reason="WATCHDOG_LAUNCH_FAILED_ROLLBACK")
        return {"status": "rolled_back" if rolled_back else "failed", "code": "WATCHDOG_LAUNCH_FAILED_ROLLBACK" if rolled_back else "WATCHDOG_LAUNCH_FAILED", "detail": type(exc).__name__}
    if _wait_for_target_health(app_root, version=target_version, source_commit=target_commit, expected=target_identity, deadline=deadline, process=child):
        session = _session_path()
        session_value = _session_snapshot()
        if session is not None:
            try:
                session.unlink()
            except FileNotFoundError:
                pass
        _try_write_update_state(
            app_root,
            phase="succeeded",
            progress=100,
            transaction_id=session_value.get("transaction_id") if isinstance(session_value, dict) and isinstance(session_value.get("transaction_id"), str) else None,
            current_payload_id=target_version,
            candidate_payload_id=target_version,
        )
        return {"status": "healthy", "payload_id": target_version, "source_commit": target_commit}
    # Preserve the distinction between an API that never became healthy and a
    # healthy API whose frontend handshake remained pending.  The normal
    # health wait intentionally treats both as failure; one bounded API-only
    # probe before cleanup gives the recovery record an accurate reason.
    candidate_failure_reason = (
        "frontend_readiness_timeout"
        if _health_matches(
            app_root,
            version=target_version,
            source_commit=target_commit,
            expected=target_identity,
            port=_session_port(),
            require_frontend=False,
        )
        else "api_readiness_timeout"
    )
    try:
        terminate_owned_process(child)
    except Exception:
        pass
    if not _rollback_previous(
        app_root,
        reason="WATCHDOG_POST_RESTART_HEALTH_FAILED",
        reason_code=candidate_failure_reason,
    ):
        return {"status": "failed", "code": "WATCHDOG_ROLLBACK_FAILED"}
    try:
        # The rollback relaunch targets the previous payload, so the candidate
        # session nonce/port contract must not constrain that fallback probe.
        os.environ.pop("LOCALAIHUB_WATCHDOG_SESSION_PATH", None)
        os.environ.pop("LOCALAIHUB_WATCHDOG_SESSION_NONCE", None)
        fallback = _launch_stable(app_root)
        version, commit, identity = _target_identity(app_root, require_build=False)
        fallback_deadline = time.monotonic() + max(5.0, min(60.0, float(timeout_seconds)))
        if _wait_for_target_health(app_root, version=version, source_commit=commit, expected=identity, deadline=fallback_deadline, process=fallback):
            _try_write_update_state(
                app_root,
                phase="rolled_back",
                progress=100,
                current_payload_id=version,
                rollback_payload_id=version,
                reason_code=candidate_failure_reason,
                last_error_code="WATCHDOG_POST_RESTART_HEALTH_FAILED",
            )
            return {"status": "rolled_back", "payload_id": version, "source_commit": commit}
        terminate_owned_process(fallback)
    except (OSError, StableShellError, ValueError, TypeError, json.JSONDecodeError):
        pass
    _try_write_update_state(
        app_root,
        phase="error",
        progress=0,
        reason_code="previous_payload_relaunch_failed",
        last_error_code="WATCHDOG_RECOVERY_FAILED",
    )
    return {"status": "failed", "code": "WATCHDOG_RECOVERY_FAILED"}


def main() -> int:
    install_value = os.environ.get("LOCALAIHUB_WATCHDOG_INSTALL_ROOT") or os.environ.get("LOCALAIHUB_INSTALL_ROOT")
    app_value = os.environ.get("LOCALAIHUB_WATCHDOG_APP_ROOT")
    if not install_value or not app_value:
        return 2
    try:
        wait_pid = int(os.environ.get("LOCALAIHUB_WATCHDOG_WAIT_PID") or 0)
    except (TypeError, ValueError):
        wait_pid = 0
    result = run(app_root=Path(install_value).expanduser().absolute(), wait_pid=wait_pid, timeout_seconds=float(os.environ.get("LOCALAIHUB_WATCHDOG_TIMEOUT") or WATCHDOG_TIMEOUT_SECONDS))
    return 0 if result.get("status") in {"healthy", "rolled_back"} else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["WATCHDOG_TIMEOUT_SECONDS", "_health_matches", "_rollback_previous", "_wait_for_pid_exit", "run"]
