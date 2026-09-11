"""Small stable Windows launcher for the installed Local AI Hub product."""

from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request

try:
    from .stable_shell import APP_USER_MODEL_ID, StableShellError, atomic_activate_pointer, resolve_launch_plan
    from .payload_bootstrap import api_server_command
    from src.services.process_manager.managed import terminate_owned_process
except ImportError:  # PyInstaller executes this file as a top-level script.
    from src.app.stable_shell import APP_USER_MODEL_ID, StableShellError, atomic_activate_pointer, resolve_launch_plan
    from src.app.payload_bootstrap import api_server_command
    from src.services.process_manager.managed import terminate_owned_process


POST_RESTART_TIMEOUT_SECONDS = 30.0
ONEDIR_SMOKE_TIMEOUT_SECONDS = 35.0


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


def _smoke_read(url: str, *, limit: int = 256 * 1024) -> bytes:
    with urllib.request.urlopen(url, timeout=0.5) as response:
        return response.read(limit + 1)


def _smoke_result_path(plan: object, name: str) -> Path | None:
    value = os.environ.get(name)
    if not value:
        return None
    path = Path(value).expanduser().absolute()
    root = Path(plan.app_root).absolute()  # type: ignore[attr-defined]
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path


def _write_smoke_result(path: Path | None, value: dict[str, object]) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
    except (OSError, TypeError, ValueError):
        return


def _run_onedir_cold_start_smoke(plan: object) -> int:
    """Exercise the actual onedir EXE through a bounded API/UI fixture.

    This branch is opt-in for CI/isolated candidate verification only. Normal
    product launch never sets ``LOCALAIHUB_ONEDIR_SMOKE`` and continues through
    the native desktop payload path below. The child remains owned by this EXE
    until the test writes the stop marker, making normal-close and PID lineage
    observable without launching production or a GPU workload.
    """

    result_path = _smoke_result_path(plan, "LOCALAIHUB_ONEDIR_SMOKE_RESULT")
    stop_path = _smoke_result_path(plan, "LOCALAIHUB_ONEDIR_SMOKE_STOP")
    if result_path is None or stop_path is None:
        return 78
    try:
        port = int(os.environ.get("LOCALAIHUB_PORT") or "0")
    except ValueError:
        return 78
    if not 1024 <= port <= 65535:
        return 78
    environment = dict(plan.environment)  # type: ignore[attr-defined]
    environment.update({
        "LOCALAIHUB_PORT": str(port),
        "LOCALAIHUB_BIND_HOST": "127.0.0.1",
        "LOCALAIHUB_PREFLIGHT": "1",
        "LOCALAIHUB_ONEDIR_SMOKE": "",
    })
    child: subprocess.Popen[object] | None = None
    deadline = time.monotonic() + ONEDIR_SMOKE_TIMEOUT_SECONDS
    base = f"http://127.0.0.1:{port}"
    commit = str(environment.get("LOCALAIHUB_BUILD_SHA") or "")
    payload_id = str(environment.get("LOCALAIHUB_BUILD_PAYLOAD") or "")
    result: dict[str, object] = {
        "status": "failed",
        "source_commit": commit,
        "payload_id": payload_id,
        "actual_executable": True,
        "launcher_mode": "onedir",
        "mei_dependency": False,
        "api_health": False,
        "frontend_route": False,
        "dashboard_route": False,
        "frontend_ready": "NOT_AVAILABLE_ON_TEST_HOST",
        "normal_close": False,
    }
    try:
        child = subprocess.Popen(
            [str(plan.runtime_pythonw), *api_server_command(Path(plan.app_payload))],  # type: ignore[attr-defined]
            cwd=str(plan.app_payload),  # type: ignore[attr-defined]
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        while time.monotonic() < deadline:
            if child.poll() is not None:
                break
            try:
                health = json.loads(_smoke_read(f"{base}/health").decode("utf-8"))
                if not isinstance(health, dict) or health.get("status") != "healthy" or health.get("build_source_commit") != commit or health.get("build_payload_id") != payload_id:
                    time.sleep(0.2)
                    continue
                result["api_health"] = True
                ui = _smoke_read(f"{base}/ui/").decode("utf-8")
                result["frontend_route"] = "Local AI Hub" in ui and "/ui/app.js" in ui
                dashboard = json.loads(_smoke_read(f"{base}/api/dashboard").decode("utf-8"))
                result["dashboard_route"] = isinstance(dashboard, dict)
                if result["frontend_route"] and result["dashboard_route"]:
                    result["status"] = "passed"
                break
            except (OSError, UnicodeError, UnicodeDecodeError, json.JSONDecodeError, ValueError, urllib.error.URLError, urllib.error.HTTPError):
                time.sleep(0.2)
        _write_smoke_result(result_path, result)
        if result["status"] != "passed":
            return 1
        while child.poll() is None and not stop_path.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        if child.poll() is None and stop_path.exists():
            terminate_owned_process(child)
            try:
                # ``terminate_owned_process`` waits for the OS tree, but a
                # Popen wrapper can briefly retain a stale poll result.  Make
                # the normal-close evidence deterministic without extending
                # the bounded smoke deadline indefinitely.
                child.wait(timeout=5.0)
            except (OSError, subprocess.SubprocessError):
                pass
        result["normal_close"] = child.poll() is not None
        _write_smoke_result(result_path, result)
        return 0 if result["normal_close"] else 1
    except (OSError, ValueError):
        _write_smoke_result(result_path, result)
        return 1
    finally:
        if child is not None and child.poll() is None:
            try:
                terminate_owned_process(child)
            except Exception:
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
    if os.environ.get("LOCALAIHUB_ONEDIR_SMOKE") == "1":
        return _run_onedir_cold_start_smoke(plan)
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
