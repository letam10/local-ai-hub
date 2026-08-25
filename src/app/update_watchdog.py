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
from src.shared.runtime_identity import API_PROTOCOL_VERSION, api_identity
from src.shared.version import PRODUCT_VERSION


WATCHDOG_TIMEOUT_SECONDS = 30.0
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_PAYLOAD_RE = re.compile(r"^main-[0-9a-f]{12}$")


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


def _health_matches(app_root: Path, *, version: str, source_commit: str | None, expected: dict[str, str]) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{_port()}/health", timeout=0.5) as response:
            value = json.loads(response.read(128 * 1024 + 1).decode("utf-8"))
        return (
            isinstance(value, dict)
            and value.get("status") == "healthy"
            and all(value.get(key) == item for key, item in expected.items())
            and value.get("api_protocol_version") == API_PROTOCOL_VERSION
            and (not source_commit or (value.get("build_source_commit") == source_commit and value.get("build_payload_id") == version))
        )
    except (OSError, UnicodeError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, urllib.error.URLError, urllib.error.HTTPError):
        return False


def _wait_for_target_health(app_root: Path, *, version: str, source_commit: str | None, expected: dict[str, str], deadline: float, process: subprocess.Popen[object] | None = None) -> bool:
    marker = app_root / "update-state" / "pending-health.json"
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None and marker.exists():
            return False
        if _health_matches(app_root, version=version, source_commit=source_commit, expected=expected) and not marker.exists():
            return True
        time.sleep(0.2)
    return False


def _rollback_previous(app_root: Path, *, reason: str) -> bool:
    marker = app_root / "update-state" / "pending-health.json"
    history = app_root / "update-state" / "previous-current.json"
    try:
        previous = _json(history)
        if set(previous) != {"schema_version", "version", "payload_relative", "manifest_sha256"} or previous.get("schema_version") != POINTER_SCHEMA:
            return False
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
        return True
    except (OSError, UnicodeError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError, StableShellError):
        return False


def _launch_stable(app_root: Path) -> subprocess.Popen[object]:
    launcher = app_root / "LocalAIHub.exe"
    if not launcher.is_file() or launcher.is_symlink():
        raise OSError("stable_launcher_unavailable")
    environment = dict(os.environ)
    environment.pop("LOCALAIHUB_WATCHDOG_WAIT_PID", None)
    # Old stable launcher binaries may not yet know the build-health fields.
    # Inject the already-validated current payload identity into the inherited
    # child environment so the API handshake remains exact without rebuilding
    # the launcher executable first.
    try:
        pointer = load_current_pointer(app_root)
        build_path = app_root / str(pointer["payload_relative"]) / "build.json"
        build = _json(build_path) if build_path.is_file() else {}
        commit = str(build.get("source_commit") or "")
        version = str(pointer.get("version") or "")
        if _SHA_RE.fullmatch(commit) and version == f"main-{commit[:12]}":
            environment.update({"LOCALAIHUB_BUILD_SHA": commit, "LOCALAIHUB_BUILD_PAYLOAD": version})
        else:
            environment.pop("LOCALAIHUB_BUILD_SHA", None)
            environment.pop("LOCALAIHUB_BUILD_PAYLOAD", None)
    except (OSError, UnicodeError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, StableShellError):
        environment.pop("LOCALAIHUB_BUILD_SHA", None)
        environment.pop("LOCALAIHUB_BUILD_PAYLOAD", None)
    return subprocess.Popen(
        [str(launcher)],
        cwd=str(app_root),
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
        return {"status": "failed", "code": "WATCHDOG_PARENT_TIMEOUT"}
    try:
        target_version, target_commit, target_identity = _target_identity(app_root, require_build=True)
        child = _launch_stable(app_root)
    except (OSError, StableShellError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return {"status": "failed", "code": "WATCHDOG_LAUNCH_FAILED", "detail": type(exc).__name__}
    if _wait_for_target_health(app_root, version=target_version, source_commit=target_commit, expected=target_identity, deadline=deadline, process=child):
        return {"status": "healthy", "payload_id": target_version, "source_commit": target_commit}
    try:
        terminate_owned_process(child)
    except Exception:
        pass
    if not _rollback_previous(app_root, reason="WATCHDOG_POST_RESTART_HEALTH_FAILED"):
        return {"status": "failed", "code": "WATCHDOG_ROLLBACK_FAILED"}
    try:
        fallback = _launch_stable(app_root)
        version, commit, identity = _target_identity(app_root, require_build=False)
        fallback_deadline = time.monotonic() + max(5.0, min(60.0, float(timeout_seconds)))
        if _wait_for_target_health(app_root, version=version, source_commit=commit, expected=identity, deadline=fallback_deadline, process=fallback):
            return {"status": "rolled_back", "payload_id": version, "source_commit": commit}
        terminate_owned_process(fallback)
    except (OSError, StableShellError, ValueError, TypeError, json.JSONDecodeError):
        pass
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
