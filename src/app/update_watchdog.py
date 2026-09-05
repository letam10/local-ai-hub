"""Updater-owned restart watchdog independent of the stable launcher binary.

The desktop payload starts this helper with its *old* bundled runtime before it
closes.  The helper waits for that exact desktop PID, starts the stable EXE,
proves the newly selected payload through the loopback health/build identity,
and restores the verified previous pointer exactly once on failure.  It never
enumerates or terminates an unowned process or listener.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.error
import urllib.request

from src.app.stable_shell import (
    PRODUCT_ID,
    POINTER_SCHEMA,
    StableShellError,
    VERSION_MANIFEST_SCHEMA,
    _is_reparse,
    atomic_activate_pointer,
    load_current_pointer,
    resolve_launch_plan,
)
from src.app.launcher_migration import (
    LauncherMigrationError,
    activate_launcher_bundle,
    launcher_tree_manifest,
    reconcile_launcher_transaction,
    restore_launcher_bundle,
)
from src.services.process_manager.managed import terminate_owned_process
from src.services.app_update import _try_write_update_state, _update_serialization_lock, reason_code_for
from src.shared.runtime_identity import API_PROTOCOL_VERSION, api_identity
from src.shared.version import PRODUCT_VERSION


WATCHDOG_TIMEOUT_SECONDS = 30.0
RESTART_SESSION_SCHEMA = "local-ai-hub-restart-session.v1"
RESTART_TRANSACTION_SCHEMA = "local-ai-hub-restart-transaction.v1"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_PAYLOAD_RE = re.compile(r"^main-[0-9a-f]{12}$")
_TRANSACTION_RE = re.compile(r"^txn-[0-9a-f]{32}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_MAX_WORKFLOW_RUN_ID = (1 << 63) - 1


def _hard_crash_checkpoint(name: str) -> None:
    """Test-only hard termination hook for pointer/recovery fault injection."""

    if os.environ.get("LOCALAIHUB_TEST_HARD_CRASH_BOUNDARY") == name:
        os._exit(198)


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


def _restart_transaction_path(app_root: Path) -> Path:
    return app_root / "update-state" / "restart-transaction.json"


def _read_restart_transaction(app_root: Path) -> dict[str, object] | None:
    path = _restart_transaction_path(app_root)
    if not path.is_file() or path.is_symlink():
        return None
    value = _json(path, limit=128 * 1024)
    return _validate_restart_transaction(value)


def _remove_owned_pending_health(app_root: Path, *, transaction_id: str, payload_id: str) -> None:
    """Remove only the pending marker created by this deferred transaction."""

    path = _pending_health_path(app_root)
    if not path.is_file() or path.is_symlink():
        return
    try:
        value = _json(path, limit=64 * 1024)
    except (OSError, UnicodeError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return
    if value.get("transaction_id") != transaction_id or value.get("payload_id") != payload_id:
        return
    try:
        path.unlink()
    except OSError:
        pass


def _read_pending_health(app_root: Path) -> dict[str, object] | None:
    path = _pending_health_path(app_root)
    if not path.is_file() or path.is_symlink():
        return None
    try:
        value = _json(path, limit=64 * 1024)
    except (OSError, UnicodeError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise StableShellError("RESTART_PENDING_HEALTH_INVALID") from None
    if (
        value.get("schema_version") != "local-ai-hub-pending-health.v1"
        or not isinstance(value.get("transaction_id"), str)
        or not _TRANSACTION_RE.fullmatch(str(value["transaction_id"]))
        or not isinstance(value.get("payload_id"), str)
        or not _PAYLOAD_RE.fullmatch(str(value["payload_id"]))
        or not isinstance(value.get("source_commit"), str)
        or not _SHA_RE.fullmatch(str(value["source_commit"]))
        or not isinstance(value.get("previous"), dict)
    ):
        raise StableShellError("RESTART_PENDING_HEALTH_INVALID")
    return value


def reconcile_restart_transaction(app_root: Path) -> dict[str, object]:
    """Recover pointer/pending ordering from a fresh watchdog process."""

    transaction = _read_restart_transaction(app_root)
    if transaction is None:
        return {"status": "not_pending"}
    previous = transaction.get("previous")
    if not isinstance(previous, dict):
        raise StableShellError("RESTART_TRANSACTION_INVALID")
    current = load_current_pointer(app_root)
    pending = _read_pending_health(app_root)
    transaction_id = str(transaction["transaction_id"])
    payload_id = str(transaction["payload_id"])
    if transaction.get("status") in {"health_admitted", "completed"} and pending is None and current.get("version") == payload_id:
        try:
            _restart_transaction_path(app_root).unlink()
        except FileNotFoundError:
            pass
        return {"status": "completed", "transaction_id": transaction_id}
    if pending is not None and (
        pending.get("transaction_id") != transaction_id
        or pending.get("payload_id") != payload_id
        or pending.get("source_commit") != transaction.get("source_commit")
        or pending.get("previous") != previous
    ):
        raise StableShellError("RESTART_PENDING_HEALTH_MISMATCH")
    candidate_visible = current.get("version") == payload_id
    previous_visible = current == previous
    if candidate_visible and pending is None:
        # The exact manager-reproduced crash window: current.json selected an
        # unproven candidate while the recovery intent was absent.  Refuse it
        # and restore the exact old pointer/shell before any relaunch.
        if not _rollback_deferred_product(app_root, transaction, reason="RESTART_CANDIDATE_WITHOUT_HEALTH_INTENT"):
            raise LauncherMigrationError("LAUNCHER_ROLLBACK_FAILED")
        return {"status": "rolled_back", "transaction_id": transaction_id}
    if candidate_visible and pending is not None:
        return {"status": "candidate_pending_health", "transaction_id": transaction_id}
    if previous_visible:
        return {"status": "prepared", "transaction_id": transaction_id, "pending_health": pending is not None}
    raise StableShellError("RESTART_CURRENT_POINTER_CHANGED")


def _rollback_deferred_activation(app_root: Path, transaction: dict[str, object], previous: dict[str, object]) -> None:
    """Restore pointer first, then the shell, preserving pair consistency."""

    transaction_id = transaction.get("transaction_id")
    payload_id = transaction.get("payload_id")
    if not isinstance(transaction_id, str) or not _TRANSACTION_RE.fullmatch(transaction_id) or not isinstance(payload_id, str):
        raise LauncherMigrationError("LAUNCHER_ROLLBACK_FAILED")
    current = load_current_pointer(app_root)
    if current != previous:
        _hard_crash_checkpoint("rollback_before_pointer_restore")
        atomic_activate_pointer(
            app_root,
            version=str(previous["version"]),
            manifest_sha256=str(previous["manifest_sha256"]),
        )
        _hard_crash_checkpoint("rollback_after_pointer_restore")
    rollback_root = app_root / "update-state" / "launcher-rollback" / transaction_id
    if rollback_root.is_dir() and not rollback_root.is_symlink():
        _hard_crash_checkpoint("rollback_before_shell_restore")
        restore_launcher_bundle(app_root, transaction_id=transaction_id, _allow_state_status=True)
        _hard_crash_checkpoint("rollback_after_shell_restore")
    _remove_owned_pending_health(app_root, transaction_id=transaction_id, payload_id=payload_id)


def _validate_restart_transaction(value: object) -> dict[str, object]:
    """Validate every deferred activation field before any filesystem move."""

    required = {
        "schema_version", "transaction_id", "payload_id", "source_commit", "update_kind",
        "previous", "manifest_sha256", "workflow_run_id", "launcher_format",
        "launcher_executable_sha256", "launcher_tree_manifest_sha256", "launcher_file_count",
        "launcher_total_bytes", "status", "created_at",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise StableShellError("RESTART_TRANSACTION_INVALID")
    if value.get("schema_version") != RESTART_TRANSACTION_SCHEMA or value.get("update_kind") != "APP_AND_LAUNCHER":
        raise StableShellError("RESTART_TRANSACTION_INVALID")
    payload_id = value.get("payload_id")
    source_commit = value.get("source_commit")
    transaction_id = value.get("transaction_id")
    if (
        not isinstance(payload_id, str)
        or not _PAYLOAD_RE.fullmatch(payload_id)
        or not isinstance(source_commit, str)
        or not _SHA_RE.fullmatch(source_commit)
        or payload_id != f"main-{source_commit[:12]}"
        or not isinstance(transaction_id, str)
        or not _TRANSACTION_RE.fullmatch(transaction_id)
    ):
        raise StableShellError("RESTART_TRANSACTION_INVALID")
    previous = value.get("previous")
    if (
        not isinstance(previous, dict)
        or set(previous) != {"schema_version", "version", "payload_relative", "manifest_sha256"}
        or previous.get("schema_version") != POINTER_SCHEMA
        or not isinstance(previous.get("version"), str)
        or not _VERSION_RE.fullmatch(previous["version"])
        or previous.get("payload_relative") != f"versions/{previous['version']}"
        or not isinstance(previous.get("manifest_sha256"), str)
        or not _SHA256_RE.fullmatch(previous["manifest_sha256"])
    ):
        raise StableShellError("RESTART_TRANSACTION_INVALID")
    run_id = value.get("workflow_run_id")
    if isinstance(run_id, bool) or not isinstance(run_id, int) or not 0 < run_id <= _MAX_WORKFLOW_RUN_ID:
        raise StableShellError("RESTART_TRANSACTION_INVALID")
    if (
        value.get("launcher_format") != "onedir"
        or not isinstance(value.get("launcher_executable_sha256"), str)
        or not _SHA256_RE.fullmatch(value["launcher_executable_sha256"])
        or not isinstance(value.get("launcher_tree_manifest_sha256"), str)
        or not _SHA256_RE.fullmatch(value["launcher_tree_manifest_sha256"])
        or isinstance(value.get("launcher_file_count"), bool)
        or not isinstance(value.get("launcher_file_count"), int)
        or not 2 <= value["launcher_file_count"] <= 10_000
        or isinstance(value.get("launcher_total_bytes"), bool)
        or not isinstance(value.get("launcher_total_bytes"), int)
        or not 1 <= value["launcher_total_bytes"] <= 500 * 1024 * 1024
        or not isinstance(value.get("manifest_sha256"), str)
        or not _SHA256_RE.fullmatch(value["manifest_sha256"])
        or not isinstance(value.get("created_at"), str)
        or not 1 <= len(value["created_at"]) <= 80
        or value.get("status") not in {
            "awaiting_old_exit", "activation_copy_complete", "old_exe_moved", "old_internal_moved",
            "old_manifest_moved", "candidate_exe_moved", "candidate_internal_moved",
            "shell_manifest_written", "shell_switched", "pointer_activated", "health_intent_written",
            "health_admitted", "completed", "rollback_in_progress", "rolled_back",
        }
    ):
        raise StableShellError("RESTART_TRANSACTION_INVALID")
    return dict(value)


def _validate_deferred_payload_identity(app_root: Path, transaction: dict[str, object]) -> None:
    """Re-read the payload identity before any root launcher move."""

    payload_id = str(transaction["payload_id"])
    source_commit = str(transaction["source_commit"])
    payload_root = app_root / "versions" / payload_id
    manifest_path = payload_root / "manifest.json"
    try:
        if _is_reparse(payload_root) or not payload_root.is_dir() or _is_reparse(manifest_path) or _is_reparse(payload_root / "build.json"):
            raise OSError("payload_reparse")
        manifest_raw = manifest_path.read_bytes()
        if len(manifest_raw) > 128 * 1024:
            raise ValueError("manifest_too_large")
        manifest = json.loads(manifest_raw.decode("utf-8"))
        build = _json(payload_root / "build.json", limit=32 * 1024)
    except (OSError, UnicodeError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise StableShellError("RESTART_PAYLOAD_IDENTITY_INVALID") from None
    if hashlib.sha256(manifest_raw).hexdigest() != str(transaction["manifest_sha256"]):
        raise StableShellError("RESTART_PAYLOAD_MANIFEST_CHANGED")
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"schema_version", "product_id", "version", "app_relative", "runtime_relative", "entrypoint"}
        or manifest.get("schema_version") != VERSION_MANIFEST_SCHEMA
        or manifest.get("product_id") != PRODUCT_ID
        or manifest.get("version") != payload_id
        or manifest.get("app_relative") != "app"
        or manifest.get("runtime_relative") != "runtime/Python312/pythonw.exe"
        or manifest.get("entrypoint") != "src.app.launcher"
        or build.get("schema_version") != "local-ai-hub-build-info.v1"
        or build.get("source_commit") != source_commit
        or build.get("workflow_run_id") != transaction.get("workflow_run_id")
    ):
        raise StableShellError("RESTART_PAYLOAD_IDENTITY_MISMATCH")


def _atomic_restart_transaction_state(app_root: Path, value: dict[str, object]) -> None:
    _atomic_json(_restart_transaction_path(app_root), value)


def _activate_deferred_product(app_root: Path, transaction: dict[str, object]) -> bool:
    """Switch the verified shell and pointer only after the old desktop exits."""

    transaction = _validate_restart_transaction(transaction)
    live_transaction = _read_restart_transaction(app_root)
    if live_transaction is None or live_transaction != transaction:
        raise StableShellError("RESTART_TRANSACTION_CHANGED")
    transaction = live_transaction
    payload_id = str(transaction["payload_id"])
    source_commit = str(transaction["source_commit"])
    transaction_id = str(transaction["transaction_id"])
    previous = transaction.get("previous")
    if not isinstance(previous, dict):
        raise StableShellError("RESTART_TRANSACTION_INVALID")
    current = load_current_pointer(app_root)
    pending = _read_pending_health(app_root)
    if pending is not None and (
        pending.get("transaction_id") != transaction_id
        or pending.get("payload_id") != payload_id
        or pending.get("source_commit") != source_commit
        or pending.get("previous") != previous
    ):
        raise StableShellError("RESTART_PENDING_HEALTH_MISMATCH")
    if current.get("version") == payload_id:
        if pending is None:
            raise StableShellError("RESTART_CANDIDATE_WITHOUT_HEALTH_INTENT")
        return True
    if current != previous:
        raise StableShellError("RESTART_CURRENT_POINTER_CHANGED")
    _validate_deferred_payload_identity(app_root, transaction)
    payload_root = app_root / "versions" / payload_id
    candidate_bundle = payload_root / "launcher" / "LocalAIHub"
    try:
        # This is the transaction-bound read immediately before activation.
        # ``activate_launcher_bundle`` repeats the comparison after copying,
        # closing the remaining prepare/copy TOCTOU window.
        candidate_manifest = launcher_tree_manifest(candidate_bundle)
    except LauncherMigrationError as exc:
        raise StableShellError("RESTART_LAUNCHER_MANIFEST_INVALID") from exc
    expected_manifest = {
        "schema_version": "local-ai-hub-launcher-bundle.v1",
        "format": transaction["launcher_format"],
        "executable": "LocalAIHub.exe",
        "executable_sha256": transaction["launcher_executable_sha256"],
        "tree_manifest_sha256": transaction["launcher_tree_manifest_sha256"],
        "file_count": transaction["launcher_file_count"],
        "total_bytes": transaction["launcher_total_bytes"],
        "files": candidate_manifest.get("files"),
    }
    if any(candidate_manifest.get(key) != expected_manifest.get(key) for key in expected_manifest):
        raise StableShellError("RESTART_LAUNCHER_MANIFEST_MISMATCH")
    try:
        # The recovery intent is durable before current.json can select the
        # candidate.  A process death here leaves the old pointer and shell.
        if pending is None:
            _atomic_json(_pending_health_path(app_root), {
                "schema_version": "local-ai-hub-pending-health.v1",
                "payload_id": payload_id,
                "source_commit": source_commit,
                "previous": previous,
                "transaction_id": transaction_id,
                "launcher_format": "onedir",
                "launcher_executable_sha256": transaction["launcher_executable_sha256"],
                "launcher_tree_manifest_sha256": transaction["launcher_tree_manifest_sha256"],
            })
            _hard_crash_checkpoint("during_pending_health_write")
        _atomic_restart_transaction_state(app_root, {**transaction, "status": "health_intent_written"})
        _hard_crash_checkpoint("after_health_intent_before_shell")
        shell_recovery = reconcile_launcher_transaction(app_root, transaction_id=transaction_id)
        if shell_recovery.get("status") in {"shell_switched", "already_reconciled", "shell_retained"}:
            activation = {
                "format": "onedir",
                "executable_sha256": transaction["launcher_executable_sha256"],
                "tree_manifest_sha256": transaction["launcher_tree_manifest_sha256"],
                "file_count": transaction["launcher_file_count"],
                "total_bytes": transaction["launcher_total_bytes"],
                "shell_retained": shell_recovery.get("status") == "shell_retained",
            }
        elif shell_recovery.get("status") == "restored":
            raise StableShellError("RESTART_SHELL_ROLLED_BACK")
        else:
            activation = activate_launcher_bundle(
                app_root,
                candidate_bundle,
                transaction_id=transaction_id,
                payload_id=payload_id,
                source_commit=source_commit,
                workflow_run_id=int(transaction["workflow_run_id"]),
                expected_manifest=candidate_manifest,
            )
        for key in ("format", "executable_sha256", "tree_manifest_sha256", "file_count", "total_bytes"):
            transaction_key = "launcher_" + ("tree_manifest_sha256" if key == "tree_manifest_sha256" else key)
            if activation.get(key) != transaction.get(transaction_key):
                raise StableShellError("RESTART_LAUNCHER_MANIFEST_MISMATCH")
        pending_after_shell = _read_pending_health(app_root)
        if pending_after_shell is not None:
            _atomic_json(_pending_health_path(app_root), {
                **pending_after_shell,
                "launcher_retained": activation.get("shell_retained") is True,
            })
        _atomic_restart_transaction_state(app_root, {**transaction, "status": "shell_switched"})
        _hard_crash_checkpoint("before_pointer_switch")
        pointer = atomic_activate_pointer(app_root, version=payload_id, manifest_sha256=str(transaction["manifest_sha256"]))
        _hard_crash_checkpoint("after_pointer_switch")
        _atomic_restart_transaction_state(app_root, {**transaction, "status": "pointer_activated"})
        _hard_crash_checkpoint("after_pointer_before_health_cleanup")
        return pointer.get("version") == payload_id
    except Exception:
        try:
            _rollback_deferred_activation(app_root, transaction, previous)
        except Exception as rollback_error:
            raise LauncherMigrationError("LAUNCHER_ROLLBACK_FAILED") from rollback_error
        raise


def _rollback_deferred_product(app_root: Path, transaction: dict[str, object] | None, *, reason: str) -> bool:
    if not isinstance(transaction, dict):
        return False
    previous = transaction.get("previous")
    if not isinstance(previous, dict):
        return False
    try:
        current = load_current_pointer(app_root)
        if current != previous:
            atomic_activate_pointer(app_root, version=str(previous["version"]), manifest_sha256=str(previous["manifest_sha256"]))
        transaction_id = transaction.get("transaction_id")
        if isinstance(transaction_id, str) and (app_root / "update-state" / "launcher-rollback" / transaction_id).is_dir():
            restore_launcher_bundle(app_root, transaction_id=transaction_id, _allow_state_status=True)
        _atomic_json(app_root / "update-state" / "last-rollback.json", {
            "schema_version": "local-ai-hub-pending-health.v1", "status": "rollback", "reason": reason, "payload_id": previous.get("version"),
        })
        for path in (_pending_health_path(app_root), _restart_transaction_path(app_root)):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        return True
    except (OSError, StableShellError, ValueError, TypeError, json.JSONDecodeError):
        return False


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
    if not launcher.is_file() or launcher.is_symlink() or _is_reparse(launcher):
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
    deferred: dict[str, object] | None = None
    try:
        reconcile_restart_transaction(app_root)
        deferred = _read_restart_transaction(app_root)
        if deferred is not None:
            _activate_deferred_product(app_root, deferred)
        target_version, target_commit, target_identity = _target_identity(app_root, require_build=True)
        child = _launch_stable(app_root)
    except (OSError, StableShellError, ValueError, TypeError, json.JSONDecodeError) as exc:
        rolled_back = _rollback_deferred_product(app_root, deferred, reason="WATCHDOG_LAUNCH_FAILED_ROLLBACK") if deferred is not None else _rollback_previous(app_root, reason="WATCHDOG_LAUNCH_FAILED_ROLLBACK")
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
        if deferred is not None:
            try:
                _restart_transaction_path(app_root).unlink()
            except FileNotFoundError:
                pass
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
    rolled_back = _rollback_deferred_product(app_root, deferred, reason="WATCHDOG_POST_RESTART_HEALTH_FAILED") if deferred is not None else _rollback_previous(
        app_root,
        reason="WATCHDOG_POST_RESTART_HEALTH_FAILED",
        reason_code=candidate_failure_reason,
    )
    if not rolled_back:
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


__all__ = ["WATCHDOG_TIMEOUT_SECONDS", "_health_matches", "_rollback_previous", "_wait_for_pid_exit", "reconcile_restart_transaction", "run"]
