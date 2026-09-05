"""Installed Local AI Hub updater for successful ``main`` build artifacts.

The updater never performs ``git pull`` and never executes a repository checkout.
It consumes a bounded GitHub Actions artifact through an already-authenticated
GitHub CLI, verifies the artifact manifest/hash, reuses the currently reviewed
bundled runtime, stages a side-by-side payload, and atomically switches the
stable shell pointer.  Models, Environments and DATA_ROOT are out of scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import stat
import subprocess
import threading
import time
from typing import Any, Callable
import urllib.error
import urllib.request
import zipfile

from src.app.stable_shell import (
    POINTER_SCHEMA,
    PRODUCT_ID,
    VERSION_MANIFEST_SCHEMA,
    StableShellError,
    atomic_activate_pointer,
    load_current_pointer,
    resolve_launch_plan,
)
from src.app.launcher_migration import (
    LauncherMigrationError,
    launcher_projection,
    launcher_tree_manifest,
)
from src.app.payload_bootstrap import api_server_command
from src.shared.runtime_identity import API_PROTOCOL_VERSION, api_identity
from src.shared.version import PRODUCT_VERSION
from src.services.process_manager.managed import terminate_owned_process
from src.services.update_transport import AuthState, TransportError, TransportSelector, build_transport

REPOSITORY = "letam10/local-ai-hub"
WORKFLOW_FILE = "ci.yml"
# ``local-ai-hub-main-update`` is the historical APP_ONLY artifact name.  The
# exact base updater at de616f4 knows only this identity and its legacy
# manifest/contract, so it must remain APP_ONLY forever.  The onedir shell
# product is deliberately a separate artifact identity; otherwise the old
# client would download a contract it cannot parse and the first bootstrap
# could never reach the new updater.
LEGACY_UPDATE_ARTIFACT_NAME = "local-ai-hub-main-update"
COMPOSITE_UPDATE_ARTIFACT_NAME = "local-ai-hub-composite-product-update"
# Compatibility alias for callers/tests that used the historical constant.
UPDATE_ARTIFACT_NAME = LEGACY_UPDATE_ARTIFACT_NAME
UPDATE_SCHEMA = "local-ai-hub-main-update.v1"
COMPOSITE_UPDATE_SCHEMA = "local-ai-hub-composite-update.v1"
UPDATE_CONTRACT_SCHEMA = "local-ai-hub-update-contract.v1"
UPDATE_CONTRACT_NAME = "update-contract.json"
BUILD_INFO_SCHEMA = "local-ai-hub-build-info.v1"
PENDING_HEALTH_SCHEMA = "local-ai-hub-pending-health.v1"
STAGED_UPDATE_SCHEMA = "local-ai-hub-staged-update.v1"
RESTART_SESSION_SCHEMA = "local-ai-hub-restart-session.v1"
RESTART_TRANSACTION_SCHEMA = "local-ai-hub-restart-transaction.v1"
BOOTSTRAP_PENDING_SCHEMA = "local-ai-hub-bootstrap-pending.v1"
BOOTSTRAP_PENDING_FILE = "bootstrap-pending.json"
RUNTIME_STRATEGY = "reuse-current"
RUNTIME_STRATEGY_BUNDLED = "bundled"
UPDATE_KIND_APP_ONLY = "APP_ONLY"
UPDATE_KIND_FULL = "FULL"
UPDATE_KIND_APP_AND_LAUNCHER = "APP_AND_LAUNCHER"
PRODUCT_UPDATE_CONTRACT_SCHEMA = "local-ai-hub-product-update-contract.v1"
RUNTIME_CONTRACT_BUNDLED = "bundled"
RUNTIME_CONTRACT_REUSE_CURRENT = "reuse-current"
MINIMUM_LAUNCHER_VERSION = "v8.0.1"
MAX_GH_JSON_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_EXTRACTED_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_FILES = 12_000
CACHE_SECONDS = 120.0
CANDIDATE_API_PREFLIGHT_TIMEOUT_SECONDS = 30.0
# Bootstrap composes a bounded server-owned snapshot and may legitimately
# take several seconds on a populated local DATA_ROOT.  Keep this timeout
# finite, but do not reject a healthy candidate merely because the first
# snapshot exceeds the health probe's short request window.
CANDIDATE_BOOTSTRAP_PREFLIGHT_TIMEOUT_SECONDS = 15.0
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PAYLOAD_RE = re.compile(r"^main-[0-9a-f]{12}$")
_RUNTIME_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_TRANSACTION_RE = re.compile(r"^txn-[0-9a-f]{32}$")
_OPAQUE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MAX_WORKFLOW_RUN_ID = (1 << 63) - 1

UPDATE_STATE_SCHEMA = "local-ai-hub-update-state.v1"
UPDATE_STATE_FILE = "updater-state.json"
UPDATE_PHASES = (
    "idle",
    "checking",
    "update_available",
    "preparing",
    "staged",
    "confirm_restart",
    "restarting",
    "succeeded",
    "rolled_back",
    "blocked",
    "error",
)
STABLE_UPDATE_REASON_CODES = frozenset({
    "artifact_download_failed",
    "artifact_identity_mismatch",
    "manifest_hash_mismatch",
    "candidate_preflight_failed",
    "active_job_blocks_restart",
    "external_owner_detected",
    "port_ownership_unknown",
    "pointer_commit_failed",
    "new_payload_process_failed",
    "api_readiness_timeout",
    "frontend_readiness_timeout",
    "watchdog_rollback_failed",
    "previous_payload_relaunch_failed",
    # Stable classifications needed by the public status surface for checks
    # that are not failures in the artifact/restart transaction itself.
    "authentication_required",
    "artifact_not_found",
    "channel_update_blocked",
    "channel_relation_unavailable",
    "update_transaction_busy",
    "staged_update_invalid",
    "rollback_unavailable",
    "state_unavailable",
    "update_failed",
    "launcher_migration_required",
})

_ERROR_REASON_CODES = {
    "UPDATE_DOWNLOAD_FAILED": "artifact_download_failed",
    "GITHUB_API_FAILED": "artifact_download_failed",
    "GITHUB_CLI_FAILED": "artifact_download_failed",
    "GITHUB_RUNS_INVALID": "artifact_download_failed",
    "UPDATE_ARTIFACT_NOT_FOUND": "artifact_not_found",
    "UPDATE_ARTIFACT_AMBIGUOUS": "artifact_identity_mismatch",
    "UPDATE_ARTIFACT_EXPIRED": "artifact_identity_mismatch",
    "UPDATE_COMPOSITE_ARTIFACT_REQUIRED": "artifact_identity_mismatch",
    "UPDATE_OLD_CLIENT_ARTIFACT_MISMATCH": "artifact_identity_mismatch",
    "UPDATE_IDENTITY_MISMATCH": "artifact_identity_mismatch",
    "UPDATE_MANIFEST_INVALID": "artifact_identity_mismatch",
    "UPDATE_MANIFEST_TOO_LARGE": "artifact_identity_mismatch",
    "UPDATE_PRODUCT_VERSION_MISMATCH": "artifact_identity_mismatch",
    "UPDATE_COMMIT_MISMATCH": "artifact_identity_mismatch",
    "UPDATE_PAYLOAD_ID_INVALID": "artifact_identity_mismatch",
    "UPDATE_ARCHIVE_IDENTITY_INVALID": "artifact_identity_mismatch",
    "UPDATE_CONTRACT_INVALID": "artifact_identity_mismatch",
    "UPDATE_KIND_UNSUPPORTED": "artifact_identity_mismatch",
    "UPDATE_RUN_MISMATCH": "artifact_identity_mismatch",
    "UPDATE_PRODUCT_CONTRACT_INVALID": "artifact_identity_mismatch",
    "UPDATE_LAUNCHER_REQUIRED": "artifact_identity_mismatch",
    "UPDATE_LAUNCHER_MANIFEST_MISMATCH": "manifest_hash_mismatch",
    "UPDATE_LAUNCHER_INTERNAL_MISSING": "artifact_identity_mismatch",
    "UPDATE_LAUNCHER_STAGING_FAILED": "pointer_commit_failed",
    "LAUNCHER_ROLLBACK_FAILED": "watchdog_rollback_failed",
    "LAUNCHER_CANDIDATE_INVALID": "artifact_identity_mismatch",
    "UPDATE_APP_PROTOCOL_INCOMPATIBLE": "artifact_identity_mismatch",
    "UPDATE_RUNTIME_CONTRACT_MISMATCH": "artifact_identity_mismatch",
    "UPDATE_RUNTIME_STRATEGY_UNSUPPORTED": "artifact_identity_mismatch",
    "UPDATE_ARCHIVE_HASH_MISMATCH": "manifest_hash_mismatch",
    "UPDATE_CONTRACT_HASH_MISMATCH": "manifest_hash_mismatch",
    "UPDATE_CHECKSUM_MANIFEST_MISSING": "manifest_hash_mismatch",
    "UPDATE_ARCHIVE_FILE_COUNT_MISMATCH": "artifact_identity_mismatch",
    "UPDATE_ARCHIVE_PATH_INVALID": "artifact_identity_mismatch",
    "UPDATE_ARCHIVE_TOO_LARGE": "artifact_identity_mismatch",
    "UPDATE_EXTRACTED_SIZE_LIMIT": "artifact_identity_mismatch",
    "UPDATE_ARCHIVE_EMPTY": "artifact_identity_mismatch",
    "STAGED_MANIFEST_HASH_MISMATCH": "manifest_hash_mismatch",
    "UPDATE_IMPORT_PREFLIGHT_FAILED": "candidate_preflight_failed",
    "CURRENT_RUNTIME_UNAVAILABLE": "candidate_preflight_failed",
    "FULL_RUNTIME_PAYLOAD_MISSING": "candidate_preflight_failed",
    "UPDATE_RUNTIME_HASH_MISMATCH": "artifact_identity_mismatch",
    "UPDATE_RUNTIME_REPARSE": "artifact_identity_mismatch",
    "UPDATE_RUNTIME_BOUNDS_EXCEEDED": "artifact_identity_mismatch",
    "UPDATE_TARGET_CONFLICT": "candidate_preflight_failed",
    "UPDATE_PREPARE_FAILED": "candidate_preflight_failed",
    "UPDATE_CANDIDATE_IDENTITY_INVALID": "candidate_preflight_failed",
    "UPDATE_CANDIDATE_RUNTIME_UNAVAILABLE": "candidate_preflight_failed",
    "UPDATE_CANDIDATE_PORT_UNAVAILABLE": "port_ownership_unknown",
    "UPDATE_CANDIDATE_API_TIMEOUT": "api_readiness_timeout",
    "UPDATE_CANDIDATE_API_START_FAILED": "new_payload_process_failed",
    "UPDATE_CANDIDATE_API_EXITED": "new_payload_process_failed",
    "UPDATE_CANDIDATE_FRONTEND_PREFLIGHT_FAILED": "frontend_readiness_timeout",
    "UPDATE_CANDIDATE_BOOTSTRAP_PREFLIGHT_FAILED": "frontend_readiness_timeout",
    "FRONTEND_BUILD_UNAVAILABLE": "frontend_readiness_timeout",
    "UPDATE_COMMIT_FAILED": "pointer_commit_failed",
    "UPDATE_STATE_WRITE_FAILED": "pointer_commit_failed",
    "UPDATE_STATE_CLEAR_FAILED": "pointer_commit_failed",
    "UPDATE_STAGED_UPDATE_PENDING": "update_transaction_busy",
    "UPDATE_STATE_INVALID": "state_unavailable",
    "UPDATE_PENDING_HEALTH_INVALID": "pointer_commit_failed",
    "STAGED_CURRENT_POINTER_CHANGED": "pointer_commit_failed",
    "DESKTOP_CLOSE_AUTHORIZATION_FAILED": "pointer_commit_failed",
    "DESKTOP_DESTROY_FAILED": "pointer_commit_failed",
    "DESKTOP_RESTART_TRANSACTION_UNAVAILABLE": "pointer_commit_failed",
    "STABLE_LAUNCHER_UNAVAILABLE": "new_payload_process_failed",
    "UPDATE_WATCHDOG_UNAVAILABLE": "new_payload_process_failed",
    "CANDIDATE_LAUNCH_PLAN_INVALID": "new_payload_process_failed",
    "CANDIDATE_LAUNCH_IDENTITY_MISMATCH": "new_payload_process_failed",
    "WATCHDOG_LAUNCH_FAILED": "new_payload_process_failed",
    "WATCHDOG_LAUNCH_FAILED_ROLLBACK": "new_payload_process_failed",
    "WATCHDOG_PARENT_TIMEOUT_ROLLBACK": "watchdog_rollback_failed",
    "WATCHDOG_POST_RESTART_HEALTH_FAILED": "api_readiness_timeout",
    "WATCHDOG_ROLLBACK_FAILED": "watchdog_rollback_failed",
    "WATCHDOG_RECOVERY_FAILED": "previous_payload_relaunch_failed",
    "ACTIVE_JOBS_BLOCK_UPDATE": "active_job_blocks_restart",
    "ACTIVE_OR_UNKNOWN_JOBS": "active_job_blocks_restart",
    "EXTERNAL_OWNER_ACTIVE": "external_owner_detected",
    "EXTERNAL_OWNER_UNVERIFIED": "external_owner_detected",
    "PORT_OWNERSHIP_UNKNOWN": "port_ownership_unknown",
    "GITHUB_AUTH_REQUIRED": "authentication_required",
    "OAUTH_AUTH_REQUIRED": "authentication_required",
    "OAUTH_LOGIN_REQUIRED": "authentication_required",
    "OAUTH_TOKEN_INVALID": "authentication_required",
    "GITHUB_CLI_REQUIRED": "authentication_required",
    "GITHUB_CLI_INVALID": "authentication_required",
    "CREDENTIAL_STORE_UNAVAILABLE": "authentication_required",
    "UPDATE_TRANSACTION_BUSY": "update_transaction_busy",
    "STAGED_UPDATE_INVALID": "staged_update_invalid",
    "STAGED_PAYLOAD_INVALID": "staged_update_invalid",
    "STAGED_PAYLOAD_UNAVAILABLE": "staged_update_invalid",
    "STAGED_RUNTIME_UNAVAILABLE": "staged_update_invalid",
    "STAGED_UPDATE_UNAVAILABLE": "staged_update_invalid",
    "STAGED_BUILD_IDENTITY_MISMATCH": "staged_update_invalid",
    "ROLLBACK_POINTER_UNAVAILABLE": "rollback_unavailable",
    "ROLLBACK_POINTER_INVALID": "rollback_unavailable",
    "INSTALLED_PRODUCT_REQUIRED": "state_unavailable",
    "CLOSE_PREFLIGHT_UNAVAILABLE": "active_job_blocks_restart",
    "RUNNING_PAYLOAD_INVALID": "candidate_preflight_failed",
    "STAGED_RUNNING_PAYLOAD_MISMATCH": "candidate_preflight_failed",
    "NO_STAGED_PAYLOAD": "rollback_unavailable",
}

_STATUS_REASON_CODES = {
    "blocked_current_ahead_of_main": "channel_update_blocked",
    "blocked_channel_diverged": "channel_update_blocked",
    "channel_relation_unavailable": "channel_relation_unavailable",
    "auth_required": "authentication_required",
    "oauth_configuration_required": "authentication_required",
    "unavailable": "state_unavailable",
    "no_artifact": "artifact_not_found",
    "launcher_migration_required": "launcher_migration_required",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _update_state_path(root: Path) -> Path:
    return root / "update-state" / UPDATE_STATE_FILE


def _new_transaction_id() -> str:
    """Return an opaque transaction identifier safe for public projection."""

    return f"txn-{secrets.token_hex(16)}"


def _safe_opaque_id(value: object) -> str | None:
    if not isinstance(value, str) or _OPAQUE_ID_RE.fullmatch(value) is None:
        return None
    return value


def reason_code_for(error_code: object) -> str:
    """Map internal error labels to the stable public updater vocabulary."""

    value = str(error_code or "")
    if value in STABLE_UPDATE_REASON_CODES:
        return value
    return _ERROR_REASON_CODES.get(value, "update_failed")


def _write_update_state(
    root: Path,
    *,
    phase: str,
    progress: int,
    transaction_id: str | None = None,
    can_prepare: bool = False,
    can_restart: bool = False,
    requires_restart: bool = False,
    current_payload_id: str | None = None,
    candidate_payload_id: str | None = None,
    rollback_payload_id: str | None = None,
    reason_code: str | None = None,
    last_error_code: str | None = None,
) -> dict[str, Any]:
    """Persist only bounded updater state; never persist paths or commands."""

    if phase not in UPDATE_PHASES or not isinstance(progress, int) or isinstance(progress, bool) or not 0 <= progress <= 100:
        raise AppUpdateError("UPDATE_STATE_INVALID")
    if transaction_id is not None and _TRANSACTION_RE.fullmatch(transaction_id) is None:
        raise AppUpdateError("UPDATE_STATE_INVALID")
    for value in (current_payload_id, candidate_payload_id, rollback_payload_id):
        if value is not None and _safe_opaque_id(value) is None:
            raise AppUpdateError("UPDATE_STATE_INVALID")
    if reason_code is not None and reason_code not in STABLE_UPDATE_REASON_CODES:
        raise AppUpdateError("UPDATE_STATE_INVALID")
    if last_error_code is not None and re.fullmatch(r"[A-Z][A-Z0-9_]{2,95}", last_error_code) is None:
        raise AppUpdateError("UPDATE_STATE_INVALID")
    value: dict[str, Any] = {
        "schema_version": UPDATE_STATE_SCHEMA,
        "transaction_id": transaction_id,
        "phase": phase,
        "progress": progress,
        "can_prepare": bool(can_prepare),
        "can_restart": bool(can_restart),
        "requires_restart": bool(requires_restart),
        "current_payload_id": current_payload_id,
        "candidate_payload_id": candidate_payload_id,
        "rollback_payload_id": rollback_payload_id,
        "reason_code": reason_code,
        "last_error_code": last_error_code,
        "updated_at": _utc_now(),
    }
    _write_json_atomic(_update_state_path(root), value)
    return value


def _try_write_update_state(root: Path, **kwargs: Any) -> dict[str, Any] | None:
    """Best-effort state publication; transaction safety remains authoritative."""

    try:
        return _write_update_state(root, **kwargs)
    except (AppUpdateError, OSError, UnicodeError, ValueError):
        return None


def _read_update_state(root: Path) -> dict[str, Any] | None:
    path = _update_state_path(root)
    if not path.is_file() or path.is_symlink():
        return None
    try:
        value = _safe_json_file(path, max_bytes=64 * 1024)
    except (OSError, UnicodeError, UnicodeDecodeError, json.JSONDecodeError, AppUpdateError) as exc:
        raise AppUpdateError("UPDATE_STATE_INVALID") from exc
    required = {
        "schema_version", "transaction_id", "phase", "progress", "can_prepare", "can_restart",
        "requires_restart", "current_payload_id", "candidate_payload_id", "rollback_payload_id",
        "reason_code", "last_error_code", "updated_at",
    }
    if set(value) != required or value.get("schema_version") != UPDATE_STATE_SCHEMA:
        raise AppUpdateError("UPDATE_STATE_INVALID")
    transaction_id = value.get("transaction_id")
    if transaction_id is not None and (not isinstance(transaction_id, str) or _TRANSACTION_RE.fullmatch(transaction_id) is None):
        raise AppUpdateError("UPDATE_STATE_INVALID")
    if value.get("phase") not in UPDATE_PHASES:
        raise AppUpdateError("UPDATE_STATE_INVALID")
    progress = value.get("progress")
    if isinstance(progress, bool) or not isinstance(progress, int) or not 0 <= progress <= 100:
        raise AppUpdateError("UPDATE_STATE_INVALID")
    for key in ("can_prepare", "can_restart", "requires_restart"):
        if not isinstance(value.get(key), bool):
            raise AppUpdateError("UPDATE_STATE_INVALID")
    for key in ("current_payload_id", "candidate_payload_id", "rollback_payload_id"):
        item = value.get(key)
        if item is not None and _safe_opaque_id(item) is None:
            raise AppUpdateError("UPDATE_STATE_INVALID")
    reason = value.get("reason_code")
    if reason is not None and reason not in STABLE_UPDATE_REASON_CODES:
        raise AppUpdateError("UPDATE_STATE_INVALID")
    error = value.get("last_error_code")
    if error is not None and (not isinstance(error, str) or re.fullmatch(r"[A-Z][A-Z0-9_]{2,95}", error) is None):
        raise AppUpdateError("UPDATE_STATE_INVALID")
    if not isinstance(value.get("updated_at"), str):
        raise AppUpdateError("UPDATE_STATE_INVALID")
    return dict(value)


def update_error_projection(
    error_code: object,
    *,
    status: str = "blocked",
    transaction_id: str | None = None,
    current_payload_id: str | None = None,
    candidate_payload_id: str | None = None,
    rollback_payload_id: str | None = None,
    active_jobs: int | None = None,
) -> dict[str, Any]:
    """Build a complete, path-free error response for API/bridge callers."""

    code = str(error_code or "UPDATE_FAILED")
    phase = "blocked" if status == "blocked" else "error"
    value: dict[str, Any] = {
        "status": status,
        "code": code,
        "phase": phase,
        "progress": 0,
        "can_prepare": False,
        "can_restart": False,
        "requires_restart": False,
        "transaction_id": transaction_id if _TRANSACTION_RE.fullmatch(transaction_id or "") else None,
        "current_payload_id": _safe_opaque_id(current_payload_id),
        "candidate_payload_id": _safe_opaque_id(candidate_payload_id),
        "rollback_payload_id": _safe_opaque_id(rollback_payload_id),
        "can_rollback": False,
        "rollback_mode": "recovery_only" if _safe_opaque_id(rollback_payload_id) else "unavailable",
        "reason_code": reason_code_for(code),
        "last_error_code": code if re.fullmatch(r"[A-Z][A-Z0-9_]{2,95}", code) else "UPDATE_FAILED",
        "execution": "not_run",
        "payload_status": "unavailable" if current_payload_id is None else "blocked",
        "launcher_status": "unknown",
        "launcher_format": "unknown",
        "launcher_migration_required": True,
        "restart_state": "idle",
    }
    if active_jobs is not None:
        value["active_jobs"] = active_jobs
    return value


def _staged_update_path(root: Path) -> Path:
    return root / "update-state" / "staged-update.json"


def _restart_session_path(root: Path) -> Path:
    return root / "update-state" / "restart-session.json"


def _restart_transaction_path(root: Path) -> Path:
    return root / "update-state" / "restart-transaction.json"


def _bootstrap_pending_path(root: Path) -> Path:
    return root / "update-state" / BOOTSTRAP_PENDING_FILE


def _write_bootstrap_pending(
    root: Path,
    *,
    source_commit: str,
    workflow_run_id: int,
    status: str = "pending",
    reason_code: str | None = None,
    transaction_id: str | None = None,
    payload_id: str | None = None,
    restart_authorized: bool = False,
) -> dict[str, Any]:
    """Persist the legacy-client hand-off without storing paths or commands."""

    if not _SHA_RE.fullmatch(source_commit):
        raise AppUpdateError("UPDATE_COMMIT_MISMATCH")
    if isinstance(workflow_run_id, bool) or not isinstance(workflow_run_id, int) or not 0 < workflow_run_id <= MAX_WORKFLOW_RUN_ID:
        raise AppUpdateError("UPDATE_RUN_MISMATCH")
    if status not in {"pending", "downloading", "verifying", "staged", "restarting", "blocked", "failed", "completed"}:
        raise AppUpdateError("UPDATE_STATE_INVALID")
    if reason_code is not None and reason_code not in STABLE_UPDATE_REASON_CODES:
        raise AppUpdateError("UPDATE_STATE_INVALID")
    if transaction_id is not None and (not isinstance(transaction_id, str) or _TRANSACTION_RE.fullmatch(transaction_id) is None):
        raise AppUpdateError("UPDATE_STATE_INVALID")
    if payload_id is not None and (not isinstance(payload_id, str) or _PAYLOAD_RE.fullmatch(payload_id) is None or payload_id != f"main-{source_commit[:12]}"):
        raise AppUpdateError("UPDATE_STATE_INVALID")
    if type(restart_authorized) is not bool:
        raise AppUpdateError("UPDATE_STATE_INVALID")
    value: dict[str, Any] = {
        "schema_version": BOOTSTRAP_PENDING_SCHEMA,
        "source_commit": source_commit,
        "workflow_run_id": workflow_run_id,
        "legacy_artifact_name": LEGACY_UPDATE_ARTIFACT_NAME,
        "composite_artifact_name": COMPOSITE_UPDATE_ARTIFACT_NAME,
        "status": status,
        "reason_code": reason_code,
        "transaction_id": transaction_id,
        "payload_id": payload_id,
        "restart_authorized": restart_authorized,
        "updated_at": _utc_now(),
    }
    _write_json_atomic(_bootstrap_pending_path(root), value)
    return value


def _read_bootstrap_pending(root: Path) -> dict[str, Any] | None:
    path = _bootstrap_pending_path(root)
    if not path.is_file() or path.is_symlink():
        return None
    try:
        value = _safe_json_file(path, max_bytes=16 * 1024)
    except (OSError, UnicodeError, UnicodeDecodeError, json.JSONDecodeError, AppUpdateError) as exc:
        raise AppUpdateError("UPDATE_STATE_INVALID") from exc
    legacy_expected = {
        "schema_version", "source_commit", "workflow_run_id", "legacy_artifact_name",
        "composite_artifact_name", "status", "reason_code", "updated_at",
    }
    expected = legacy_expected | {"transaction_id", "payload_id", "restart_authorized"}
    if set(value) == legacy_expected:
        value = {**value, "transaction_id": None, "payload_id": None, "restart_authorized": False}
    if set(value) != expected or value.get("schema_version") != BOOTSTRAP_PENDING_SCHEMA:
        raise AppUpdateError("UPDATE_STATE_INVALID")
    source_commit = value.get("source_commit")
    run_id = value.get("workflow_run_id")
    if (
        not isinstance(source_commit, str)
        or not _SHA_RE.fullmatch(source_commit)
        or isinstance(run_id, bool)
        or not isinstance(run_id, int)
        or not 0 < run_id <= MAX_WORKFLOW_RUN_ID
        or value.get("legacy_artifact_name") != LEGACY_UPDATE_ARTIFACT_NAME
        or value.get("composite_artifact_name") != COMPOSITE_UPDATE_ARTIFACT_NAME
        or value.get("status") not in {"pending", "downloading", "verifying", "staged", "restarting", "blocked", "failed", "completed"}
        or (value.get("reason_code") is not None and value.get("reason_code") not in STABLE_UPDATE_REASON_CODES)
        or (value.get("transaction_id") is not None and (not isinstance(value.get("transaction_id"), str) or _TRANSACTION_RE.fullmatch(str(value.get("transaction_id"))) is None))
        or (value.get("payload_id") is not None and (not isinstance(value.get("payload_id"), str) or _PAYLOAD_RE.fullmatch(str(value.get("payload_id"))) is None or value.get("payload_id") != f"main-{source_commit[:12]}"))
        or type(value.get("restart_authorized")) is not bool
        or not isinstance(value.get("updated_at"), str)
    ):
        raise AppUpdateError("UPDATE_STATE_INVALID")
    return dict(value)


def _complete_bootstrap_marker(root: Path, pending: dict[str, Any], *, current: dict[str, Any]) -> dict[str, Any]:
    """Commit a matching stale bootstrap marker to one terminal state."""

    if pending.get("status") in {"blocked", "failed"}:
        return {"status": "blocked", "code": pending.get("reason_code") or "UPDATE_BOOTSTRAP_BLOCKED", "bootstrap_pending": True}
    if (
        pending.get("source_commit") != current.get("commit")
        or pending.get("workflow_run_id") != current.get("workflow_run_id")
        or pending.get("payload_id") not in {None, current.get("payload_id")}
    ):
        return {"status": "blocked", "code": "UPDATE_BOOTSTRAP_IDENTITY_MISMATCH", "bootstrap_pending": True}
    completed = _write_bootstrap_pending(
        root,
        source_commit=str(pending["source_commit"]),
        workflow_run_id=int(pending["workflow_run_id"]),
        status="completed",
        transaction_id=pending.get("transaction_id") if isinstance(pending.get("transaction_id"), str) else None,
        payload_id=str(current.get("payload_id")) if _PAYLOAD_RE.fullmatch(str(current.get("payload_id") or "")) else None,
        restart_authorized=False,
    )
    _write_json_atomic(root / "update-state" / "bootstrap-completed.json", {
        "schema_version": "local-ai-hub-bootstrap-completed.v1",
        "source_commit": completed["source_commit"],
        "workflow_run_id": completed["workflow_run_id"],
        "transaction_id": completed.get("transaction_id"),
        "payload_id": completed.get("payload_id"),
        "status": "completed",
    })
    return {"status": "completed", "bootstrap_pending": False, "source_commit": completed["source_commit"], "workflow_run_id": completed["workflow_run_id"]}


def _unlink_state(path: Path) -> None:
    try:
        if path.is_file() and not path.is_symlink():
            path.unlink()
    except OSError:
        pass


@contextmanager
def _update_serialization_lock(root: Path):
    """Serialize stage/commit/rollback across the API and desktop processes.

    ``threading.RLock`` is insufficient because the prepare route and native
    restart bridge run in different processes.  An exclusive, tiny marker in
    the installer-owned update-state directory provides a fail-closed
    cross-process lock without a daemon or a service dependency.  Stale locks
    are never silently removed; the next operation reports a bounded busy
    state instead of risking a concurrent pointer transaction.
    """

    lock_path = root / "update-state" / "update-transaction.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd: int | None = None
    try:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise AppUpdateError("UPDATE_TRANSACTION_BUSY") from exc
        os.write(fd, f"pid={os.getpid()}\n".encode("ascii"))
        yield
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                lock_path.unlink()
            except OSError:
                pass


def _classify_channel_relation(compare_status: object) -> str:
    """Map GitHub compare semantics to the safe updater decision."""

    value = str(compare_status or "").casefold()
    return {
        "identical": "same",
        "ahead": "forward_update_available",
        "behind": "blocked_current_ahead_of_main",
        "diverged": "blocked_channel_diverged",
    }.get(value, "channel_relation_unavailable")


class AppUpdateError(RuntimeError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True)
class UpdateCandidate:
    run_id: int
    source_commit: str
    artifact_id: int
    artifact_name: str
    update_kind: str = UPDATE_KIND_APP_ONLY

    @property
    def is_composite(self) -> bool:
        return self.update_kind == UPDATE_KIND_APP_AND_LAUNCHER or self.artifact_name == COMPOSITE_UPDATE_ARTIFACT_NAME


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _runtime_inventory_hash(root: Path) -> str:
    """Hash a bounded runtime inventory without following reparse entries."""

    rows: list[dict[str, Any]] = []
    total = 0
    for current, directories, filenames in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        directories[:] = sorted(directories)
        for name in directories:
            if (current_path / name).is_symlink():
                raise AppUpdateError("UPDATE_RUNTIME_REPARSE")
        for name in sorted(filenames):
            path = current_path / name
            if path.is_symlink() or not path.is_file():
                raise AppUpdateError("UPDATE_RUNTIME_REPARSE")
            size = path.stat().st_size
            total += size
            if len(rows) >= 50_000 or total > 1_000_000_000:
                raise AppUpdateError("UPDATE_RUNTIME_BOUNDS_EXCEEDED")
            rows.append({"name": path.relative_to(root).as_posix(), "size": size, "sha256": _sha256(path)})
    if not rows:
        raise AppUpdateError("FULL_RUNTIME_PAYLOAD_MISSING")
    raw = (json.dumps(sorted(rows, key=lambda row: row["name"]), ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _canonical_json(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _safe_json_file(path: Path, *, max_bytes: int = 256 * 1024) -> dict[str, Any]:
    raw = path.read_bytes()
    if len(raw) > max_bytes:
        raise AppUpdateError("UPDATE_MANIFEST_TOO_LARGE")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise AppUpdateError("UPDATE_MANIFEST_INVALID")
    return value


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    raw = _canonical_json(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise AppUpdateError("UPDATE_STATE_WRITE_FAILED") from exc


def _pending_health_path(root: Path) -> Path:
    return root / "update-state" / "pending-health.json"


def _record_pending_health(
    root: Path,
    *,
    previous: dict[str, Any],
    payload_id: str,
    source_commit: str,
    transaction_id: str | None = None,
) -> None:
    value: dict[str, Any] = {
        "schema_version": PENDING_HEALTH_SCHEMA,
        "payload_id": payload_id,
        "source_commit": source_commit,
        "previous": previous,
    }
    if transaction_id is not None and _TRANSACTION_RE.fullmatch(transaction_id):
        value["transaction_id"] = transaction_id
    _write_json_atomic(_pending_health_path(root), value)


def _commit_restart_health_admission(root: Path, *, transaction_id: str | None, payload_id: str, source_commit: str) -> None:
    """Durably commit health admission before clearing restart journals."""

    restart_path = _restart_transaction_path(root)
    transaction: dict[str, Any] | None = None
    if transaction_id is not None and restart_path.is_file() and not restart_path.is_symlink():
        transaction = _safe_json_file(restart_path, max_bytes=128 * 1024)
        if (
            transaction.get("schema_version") != RESTART_TRANSACTION_SCHEMA
            or transaction.get("transaction_id") != transaction_id
            or transaction.get("payload_id") != payload_id
            or transaction.get("source_commit") != source_commit
        ):
            raise AppUpdateError("UPDATE_RESTART_TRANSACTION_MISMATCH")
        _write_json_atomic(restart_path, {**transaction, "status": "health_admitted"})
    _write_json_atomic(root / "update-state" / "restart-completed.json", {
        "schema_version": "local-ai-hub-restart-completed.v1",
        "transaction_id": transaction_id,
        "payload_id": payload_id,
        "source_commit": source_commit,
        "status": "completed",
    })
    if transaction is not None:
        _write_json_atomic(restart_path, {**transaction, "status": "completed"})


def _write_restart_session(root: Path, value: dict[str, Any]) -> None:
    """Publish bounded candidate-session identity for the watchdog.

    The state is internal installer metadata.  It intentionally carries no
    filesystem paths, tokens, or user data; only the nonce, exact payload
    identity, loopback port and process IDs needed to bind the watchdog probe.
    """

    _write_json_atomic(_restart_session_path(root), value)


def _read_staged_update(root: Path) -> dict[str, Any] | None:
    path = _staged_update_path(root)
    if not path.is_file() or path.is_symlink():
        return None
    value = _safe_json_file(path, max_bytes=64 * 1024)
    expected = {
        "schema_version", "payload_id", "source_commit", "payload_relative",
        "manifest_sha256", "previous", "staged_at", "update_kind",
    }
    allowed = expected | {
        "transaction_id", "workflow_run_id", "launcher_format", "launcher_executable_sha256",
        "launcher_tree_manifest_sha256", "launcher_file_count", "launcher_total_bytes",
    }
    if not expected.issubset(value) or not set(value).issubset(allowed) or value.get("schema_version") != STAGED_UPDATE_SCHEMA:
        raise AppUpdateError("STAGED_UPDATE_INVALID")
    payload_id = value.get("payload_id")
    source_commit = value.get("source_commit")
    relative = value.get("payload_relative")
    digest = value.get("manifest_sha256")
    if (
        not isinstance(payload_id, str) or _PAYLOAD_RE.fullmatch(payload_id) is None
        or not isinstance(source_commit, str) or _SHA_RE.fullmatch(source_commit) is None
        or payload_id != f"main-{source_commit[:12]}"
        or relative != f"versions/{payload_id}"
        or not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None
        or not isinstance(value.get("previous"), dict)
        or not isinstance(value.get("staged_at"), str)
        or value.get("update_kind") not in {UPDATE_KIND_APP_ONLY, UPDATE_KIND_FULL, UPDATE_KIND_APP_AND_LAUNCHER}
        or (
            value.get("transaction_id") is not None
            and (
                not isinstance(value.get("transaction_id"), str)
                or _TRANSACTION_RE.fullmatch(str(value.get("transaction_id"))) is None
            )
        )
        or (
            value.get("update_kind") == UPDATE_KIND_APP_AND_LAUNCHER
            and (
                isinstance(value.get("workflow_run_id"), bool)
                or not isinstance(value.get("workflow_run_id"), int)
                or value.get("workflow_run_id") <= 0
                or value.get("launcher_format") != "onedir"
                or not isinstance(value.get("launcher_executable_sha256"), str)
                or _SHA256_RE.fullmatch(value.get("launcher_executable_sha256")) is None
                or not isinstance(value.get("launcher_tree_manifest_sha256"), str)
                or _SHA256_RE.fullmatch(value.get("launcher_tree_manifest_sha256")) is None
                or isinstance(value.get("launcher_file_count"), bool)
                or not isinstance(value.get("launcher_file_count"), int)
                or value.get("launcher_file_count") < 2
                or isinstance(value.get("launcher_total_bytes"), bool)
                or not isinstance(value.get("launcher_total_bytes"), int)
                or value.get("launcher_total_bytes") < 1
            )
        )
    ):
        raise AppUpdateError("STAGED_UPDATE_INVALID")
    return dict(value)


def mark_startup_health(
    app_root: Path,
    *,
    health: dict[str, Any] | None = None,
    frontend_ready: bool = False,
) -> dict[str, Any]:
    """Commit or fail-closed a pending update after API *and* UI readiness.

    The native shell may inspect API health while loading the WebView, but it
    is not allowed to clear the pending marker until the frontend explicitly
    calls :func:`confirm_frontend_ready` through the desktop bridge.
    """

    root = app_root.absolute()
    pending_path = _pending_health_path(root)
    pending_exists = pending_path.is_file() and not pending_path.is_symlink()
    if frontend_ready:
        try:
            current = load_current_pointer(root)
            build_path = root / str(current["payload_relative"]) / "build.json"
            if not build_path.is_file() or build_path.is_symlink():
                if not pending_exists:
                    return {"status": "not_pending"}
                raise AppUpdateError("FRONTEND_BUILD_UNAVAILABLE")
            build = _safe_json_file(build_path, max_bytes=32 * 1024)
            source_commit = str(build.get("source_commit") or "")
            payload_id = str(current.get("version") or "")
            if _SHA_RE.fullmatch(source_commit) and payload_id == f"main-{source_commit[:12]}":
                if not isinstance(health, dict) or health.get("build_source_commit") != source_commit or health.get("build_payload_id") != payload_id:
                    return {"status": "frontend_rejected", "code": "FRONTEND_BUILD_MISMATCH"}
        except (OSError, UnicodeError, json.JSONDecodeError, StableShellError, AppUpdateError):
            if not pending_exists:
                return {"status": "not_pending"}
            return {"status": "frontend_rejected", "code": "FRONTEND_BUILD_UNAVAILABLE"}
    if not pending_exists:
        return {"status": "not_pending"}
    if not frontend_ready:
        return {"status": "frontend_pending", "code": "FRONTEND_READY_REQUIRED"}
    pending = _safe_json_file(pending_path, max_bytes=64 * 1024)
    if pending.get("schema_version") != PENDING_HEALTH_SCHEMA or not isinstance(pending.get("previous"), dict):
        raise AppUpdateError("UPDATE_PENDING_HEALTH_INVALID")
    transaction_id = pending.get("transaction_id") if isinstance(pending.get("transaction_id"), str) else None
    if pending.get("transaction_id") is not None and transaction_id is None:
        raise AppUpdateError("UPDATE_PENDING_HEALTH_INVALID")
    if transaction_id is not None and _TRANSACTION_RE.fullmatch(transaction_id) is None:
        raise AppUpdateError("UPDATE_PENDING_HEALTH_INVALID")
    current = load_current_pointer(root)
    build_path = root / str(current.get("payload_relative")) / "build.json"
    build = _safe_json_file(build_path, max_bytes=32 * 1024)
    healthy = (
        current.get("version") == pending.get("payload_id")
        and build.get("source_commit") == pending.get("source_commit")
        and isinstance(health, dict)
        and health.get("product_id") == PRODUCT_ID
        and health.get("product_version") == PRODUCT_VERSION
        and health.get("api_protocol_version") == API_PROTOCOL_VERSION
        and health.get("app_user_model_id") == "LocalAIHub.Desktop"
        and isinstance(health.get("installation_id"), str)
        and len(health.get("installation_id")) == 32
        and health.get("build_source_commit") == pending.get("source_commit")
        and health.get("build_payload_id") == pending.get("payload_id")
    )
    launcher_admission = True
    if pending.get("launcher_tree_manifest_sha256") is not None:
        launcher = launcher_projection(root)
        launcher_admission = launcher.get("status") == "verified"
        if pending.get("launcher_retained") is not True:
            launcher_admission = launcher_admission and (
                launcher.get("format") == pending.get("launcher_format", "onedir")
                and launcher.get("executable_sha256") == pending.get("launcher_executable_sha256")
                and launcher.get("tree_manifest_sha256") == pending.get("launcher_tree_manifest_sha256")
            )
    healthy = healthy and launcher_admission
    if healthy:
        try:
            _commit_restart_health_admission(
                root,
                transaction_id=transaction_id,
                payload_id=str(current["version"]),
                source_commit=str(build["source_commit"]),
            )
            pending_path.unlink()
        except OSError as exc:
            raise AppUpdateError("UPDATE_STATE_CLEAR_FAILED") from exc
        _unlink_state(_staged_update_path(root))
        _unlink_state(_restart_transaction_path(root))
        _unlink_state(_restart_session_path(root))
        _try_write_update_state(
            root,
            phase="succeeded",
            progress=100,
            transaction_id=transaction_id,
            current_payload_id=current.get("version"),
            rollback_payload_id=pending.get("previous", {}).get("version"),
        )
        return {"status": "healthy", "payload_id": current["version"], "source_commit": build["source_commit"]}
    previous = pending["previous"]
    pointer = atomic_activate_pointer(root, version=str(previous["version"]), manifest_sha256=str(previous["manifest_sha256"]))
    _write_json_atomic(root / "update-state" / "last-rollback.json", {
        "schema_version": PENDING_HEALTH_SCHEMA,
        "status": "rollback",
        "payload_id": pointer["version"],
        "reason": "POST_RESTART_HEALTH_FAILED",
    })
    try:
        pending_path.unlink()
    except OSError:
        pass
    _unlink_state(_staged_update_path(root))
    _unlink_state(_restart_session_path(root))
    _try_write_update_state(
        root,
        phase="rolled_back",
        progress=100,
        transaction_id=transaction_id,
        current_payload_id=pointer.get("version"),
        candidate_payload_id=pending.get("payload_id"),
        rollback_payload_id=pointer.get("version"),
        reason_code="api_readiness_timeout",
        last_error_code="POST_RESTART_HEALTH_FAILED",
    )
    return {"status": "rollback", "payload_id": pointer["version"], "code": "POST_RESTART_HEALTH_FAILED"}


def _safe_update_manifest(value: object, *, expected_commit: str | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AppUpdateError("UPDATE_MANIFEST_INVALID")
    required = {
        "schema_version", "product_id", "product_version", "channel", "source_commit",
        "payload_id", "runtime_strategy", "archive", "archive_sha256", "file_count",
    }
    schema = value.get("schema_version")
    composite_optional = {
        "workflow_run_id", "app_file_count", "app_total_bytes", "app_manifest_sha256",
        "launcher_format", "launcher_file_count", "launcher_total_bytes",
        "launcher_manifest_sha256", "launcher_executable_sha256",
    }
    if schema == COMPOSITE_UPDATE_SCHEMA:
        allowed = required | composite_optional
        if not required.issubset(value) or not set(value).issubset(allowed):
            raise AppUpdateError("UPDATE_MANIFEST_INVALID")
    elif set(value) != required:
        raise AppUpdateError("UPDATE_MANIFEST_INVALID")
    source_commit = str(value.get("source_commit") or "")
    payload_id = str(value.get("payload_id") or "")
    digest = str(value.get("archive_sha256") or "")
    archive = str(value.get("archive") or "")
    count = value.get("file_count")
    if schema not in {UPDATE_SCHEMA, COMPOSITE_UPDATE_SCHEMA} or value.get("product_id") != PRODUCT_ID:
        raise AppUpdateError("UPDATE_IDENTITY_MISMATCH")
    if value.get("product_version") != PRODUCT_VERSION or value.get("channel") != "main":
        raise AppUpdateError("UPDATE_PRODUCT_VERSION_MISMATCH")
    if not _SHA_RE.fullmatch(source_commit) or (expected_commit is not None and source_commit != expected_commit):
        raise AppUpdateError("UPDATE_COMMIT_MISMATCH")
    if payload_id != f"main-{source_commit[:12]}" or not _PAYLOAD_RE.fullmatch(payload_id):
        raise AppUpdateError("UPDATE_PAYLOAD_ID_INVALID")
    if value.get("runtime_strategy") not in {RUNTIME_STRATEGY, RUNTIME_STRATEGY_BUNDLED}:
        raise AppUpdateError("UPDATE_RUNTIME_STRATEGY_UNSUPPORTED")
    if archive != "LocalAIHub-main-update.zip" or not _SHA256_RE.fullmatch(digest):
        raise AppUpdateError("UPDATE_ARCHIVE_IDENTITY_INVALID")
    if isinstance(count, bool) or not isinstance(count, int) or count < 1 or count > MAX_ARCHIVE_FILES:
        raise AppUpdateError("UPDATE_FILE_COUNT_INVALID")
    if schema == COMPOSITE_UPDATE_SCHEMA:
        run_id = value.get("workflow_run_id")
        if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
            raise AppUpdateError("UPDATE_RUN_MISMATCH")
        for key in ("app_manifest_sha256", "launcher_manifest_sha256", "launcher_executable_sha256"):
            if not isinstance(value.get(key), str) or _SHA256_RE.fullmatch(value[key]) is None:
                raise AppUpdateError("UPDATE_PRODUCT_CONTRACT_INVALID")
        if value.get("launcher_format") != "onedir":
            raise AppUpdateError("UPDATE_LAUNCHER_REQUIRED")
        for key, minimum in (("app_file_count", 1), ("launcher_file_count", 2), ("app_total_bytes", 0), ("launcher_total_bytes", 1)):
            item = value.get(key)
            if isinstance(item, bool) or not isinstance(item, int) or item < minimum:
                raise AppUpdateError("UPDATE_PRODUCT_CONTRACT_INVALID")
        if expected_commit is not None and run_id <= 0:
            raise AppUpdateError("UPDATE_RUN_MISMATCH")
    return dict(value)


def _safe_update_contract(value: object, *, expected_commit: str | None = None) -> dict[str, Any]:
    """Validate the optional contract without weakening legacy manifests."""

    if not isinstance(value, dict):
        raise AppUpdateError("UPDATE_CONTRACT_INVALID")
    required = {
        "schema_version", "update_kind", "app_protocol", "runtime_contract",
        "runtime_version", "runtime_hash", "minimum_launcher_version", "source_commit",
    }
    schema = value.get("schema_version")
    if schema == PRODUCT_UPDATE_CONTRACT_SCHEMA:
        product_required = required | {
            "product_id", "product_version", "workflow_run_id", "app_payload_id",
            "app_manifest_sha256", "app_file_count", "app_total_bytes", "launcher_format",
            "launcher_executable_sha256", "launcher_tree_manifest_sha256", "launcher_file_count",
            "launcher_total_bytes",
        }
        if set(value) != product_required:
            raise AppUpdateError("UPDATE_PRODUCT_CONTRACT_INVALID")
        if value.get("product_id") != PRODUCT_ID or value.get("product_version") != PRODUCT_VERSION:
            raise AppUpdateError("UPDATE_PRODUCT_CONTRACT_INVALID")
    elif set(value) != required or schema != UPDATE_CONTRACT_SCHEMA:
        raise AppUpdateError("UPDATE_CONTRACT_INVALID")
    source_commit = value.get("source_commit")
    if not isinstance(source_commit, str) or not _SHA_RE.fullmatch(source_commit) or (expected_commit is not None and source_commit != expected_commit):
        raise AppUpdateError("UPDATE_COMMIT_MISMATCH")
    if value.get("app_protocol") != API_PROTOCOL_VERSION or value.get("minimum_launcher_version") != MINIMUM_LAUNCHER_VERSION:
        raise AppUpdateError("UPDATE_APP_PROTOCOL_INCOMPATIBLE")
    kind = value.get("update_kind")
    runtime_contract = value.get("runtime_contract")
    runtime_version = value.get("runtime_version")
    runtime_hash = value.get("runtime_hash")
    if kind == UPDATE_KIND_APP_ONLY:
        if runtime_contract != RUNTIME_CONTRACT_REUSE_CURRENT or runtime_version is not None or runtime_hash is not None:
            raise AppUpdateError("UPDATE_CONTRACT_INVALID")
    elif kind == UPDATE_KIND_FULL:
        if runtime_contract != RUNTIME_CONTRACT_BUNDLED or not isinstance(runtime_version, str) or not runtime_version or not isinstance(runtime_hash, str) or not _RUNTIME_HASH_RE.fullmatch(runtime_hash):
            raise AppUpdateError("UPDATE_CONTRACT_INVALID")
    elif kind == UPDATE_KIND_APP_AND_LAUNCHER:
        if schema != PRODUCT_UPDATE_CONTRACT_SCHEMA or runtime_contract != RUNTIME_CONTRACT_REUSE_CURRENT or runtime_version is not None or runtime_hash is not None:
            raise AppUpdateError("UPDATE_PRODUCT_CONTRACT_INVALID")
        run_id = value.get("workflow_run_id")
        if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
            raise AppUpdateError("UPDATE_RUN_MISMATCH")
        if value.get("app_payload_id") != f"main-{source_commit[:12]}":
            raise AppUpdateError("UPDATE_PRODUCT_CONTRACT_INVALID")
        for key in ("app_manifest_sha256", "launcher_executable_sha256", "launcher_tree_manifest_sha256"):
            if not isinstance(value.get(key), str) or _SHA256_RE.fullmatch(value[key]) is None:
                raise AppUpdateError("UPDATE_PRODUCT_CONTRACT_INVALID")
        if value.get("launcher_format") != "onedir":
            raise AppUpdateError("UPDATE_LAUNCHER_REQUIRED")
        for key, minimum in (("app_file_count", 1), ("launcher_file_count", 2), ("app_total_bytes", 0), ("launcher_total_bytes", 1)):
            item = value.get(key)
            if isinstance(item, bool) or not isinstance(item, int) or item < minimum:
                raise AppUpdateError("UPDATE_PRODUCT_CONTRACT_INVALID")
    else:
        raise AppUpdateError("UPDATE_KIND_UNSUPPORTED")
    return dict(value)


def _zip_member_is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_ISLNK(mode)


def _safe_extract_app_archive(archive: Path, destination: Path, *, expected_files: int, update_kind: str = UPDATE_KIND_APP_ONLY) -> int:
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise AppUpdateError("UPDATE_ARCHIVE_TOO_LARGE")
    extracted = 0
    total_bytes = 0
    with zipfile.ZipFile(archive, "r") as bundle:
        members = bundle.infolist()
        if len(members) != expected_files or len(members) > MAX_ARCHIVE_FILES:
            raise AppUpdateError("UPDATE_ARCHIVE_FILE_COUNT_MISMATCH")
        for info in members:
            name = info.filename
            allowed_prefix = (
                name.startswith("app/")
                or (update_kind == UPDATE_KIND_FULL and name.startswith("runtime/"))
                or (update_kind == UPDATE_KIND_APP_AND_LAUNCHER and name.startswith("launcher/"))
            )
            if (
                not allowed_prefix
                or name.startswith(("/", "\\"))
                or "\\" in name
                or any(part in {"", ".", ".."} for part in name.split("/"))
                or _zip_member_is_symlink(info)
            ):
                raise AppUpdateError("UPDATE_ARCHIVE_PATH_INVALID")
            if info.is_dir():
                continue
            total_bytes += max(0, int(info.file_size))
            if total_bytes > MAX_EXTRACTED_BYTES:
                raise AppUpdateError("UPDATE_EXTRACTED_SIZE_LIMIT")
            target = destination.joinpath(*name.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info, "r") as source, target.open("xb") as sink:
                shutil.copyfileobj(source, sink, length=1024 * 1024)
            extracted += 1
    if extracted < 1:
        raise AppUpdateError("UPDATE_ARCHIVE_EMPTY")
    return extracted


class AppUpdateService:
    """Singleton-safe installed updater with a bounded GitHub CLI transport."""

    def __init__(self, *, runner: Runner | None = None, gh_path: str | None = None, transport: Any | None = None, allow_test_root: bool = False) -> None:
        self._runner = runner or subprocess.run
        self._gh_path = gh_path
        self._transport_override = transport
        self._allow_test_root = bool(allow_test_root)
        self._transport_selector: TransportSelector | None = None
        self._lock = threading.RLock()
        self._cached: tuple[float, dict[str, Any]] | None = None
        self._retry_attempt: int | None = None
        self._transaction_id: str | None = None

    def _on_transport_retry(self, attempt: int, total: int) -> None:
        self._retry_attempt = max(1, min(int(total), int(attempt)))

    def _selected_transport(self) -> tuple[Any, AuthState]:
        if self._transport_override is not None:
            state = self._transport_override.auth_state()
            return self._transport_override, state
        if self._transport_selector is None:
            self._transport_selector = build_transport(runner=self._runner, gh_path=self._gh_path, retry_callback=self._on_transport_retry)
        return self._transport_selector.select()

    def _transport(self) -> Any:
        transport, _state = self._selected_transport()
        return transport

    def _install_root(self) -> Path:
        raw = os.environ.get("LOCALAIHUB_INSTALL_ROOT")
        if not raw:
            raise AppUpdateError("INSTALLED_PRODUCT_REQUIRED")
        root = Path(raw).expanduser().absolute()
        # Stable-shell validation also rejects Temp/.git/reparse product roots.
        resolve_launch_plan(root, allow_test_root=self._allow_test_root)
        return root

    def _gh(self) -> str:
        candidate = self._gh_path or os.environ.get("LOCALAIHUB_GH_CLI") or shutil.which("gh")
        if not candidate:
            raise AppUpdateError("GITHUB_CLI_REQUIRED")
        path = Path(candidate).expanduser()
        if not path.is_file() or path.is_symlink():
            raise AppUpdateError("GITHUB_CLI_INVALID")
        return str(path)

    def _creationflags(self) -> int:
        return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0

    def _run(self, args: list[str], *, timeout: float = 25.0, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        command = [self._gh(), *args]
        try:
            return self._runner(
                command,
                cwd=str(cwd) if cwd else None,
                env=dict(os.environ),
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
                creationflags=self._creationflags(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise AppUpdateError("GITHUB_CLI_FAILED", type(exc).__name__) from exc

    def _authenticated(self) -> bool:
        _transport, state = self._selected_transport()
        return state.status == "ready"

    def _api_json(self, endpoint: str, fields: dict[str, str] | None = None) -> dict[str, Any]:
        try:
            return self._transport().api_json(endpoint, fields)
        except TransportError as exc:
            raise AppUpdateError(exc.code) from exc

    @staticmethod
    def _artifact_candidates(artifacts: object, *, name: str) -> list[dict[str, Any]]:
        """Return live, uniquely selectable artifact metadata for one identity."""

        if not isinstance(artifacts, list):
            return []
        rows = [row for row in artifacts if isinstance(row, dict) and row.get("name") == name]
        if len(rows) > 1:
            raise AppUpdateError("UPDATE_ARTIFACT_AMBIGUOUS")
        live = [row for row in rows if row.get("expired") is not True]
        return live

    def _latest_candidate(self, *, require_composite: bool = False) -> UpdateCandidate | None:
        """Select one exact main-run artifact, preferring the composite identity.

        The legacy identity is intentionally still visible to a new client as
        a bootstrap fallback.  It is never selected when a live composite is
        present in the same successful run, and a composite identity that is
        present but expired/ambiguous fails closed instead of silently
        downgrading to APP_ONLY.
        """

        runs = self._api_json(
            f"repos/{REPOSITORY}/actions/workflows/{WORKFLOW_FILE}/runs",
            {"branch": "main", "event": "push", "status": "success", "per_page": "20"},
        ).get("workflow_runs")
        if not isinstance(runs, list):
            raise AppUpdateError("GITHUB_RUNS_INVALID")
        for row in runs:
            if not isinstance(row, dict) or row.get("conclusion") != "success" or row.get("head_branch") != "main":
                continue
            sha = str(row.get("head_sha") or "").lower()
            run_id = row.get("id")
            if not _SHA_RE.fullmatch(sha) or isinstance(run_id, bool) or not isinstance(run_id, int) or not 0 < run_id <= MAX_WORKFLOW_RUN_ID:
                continue
            artifacts = self._api_json(f"repos/{REPOSITORY}/actions/runs/{run_id}/artifacts", {"per_page": "100"}).get("artifacts")
            if not isinstance(artifacts, list):
                continue
            composite_rows = self._artifact_candidates(artifacts, name=COMPOSITE_UPDATE_ARTIFACT_NAME)
            composite_named = [item for item in artifacts if isinstance(item, dict) and item.get("name") == COMPOSITE_UPDATE_ARTIFACT_NAME]
            legacy_rows = self._artifact_candidates(artifacts, name=LEGACY_UPDATE_ARTIFACT_NAME)
            if composite_named and not composite_rows:
                # A same-run composite that has expired is not permission to
                # fall back to APP_ONLY when the shell migration is required.
                if require_composite:
                    raise AppUpdateError("UPDATE_ARTIFACT_EXPIRED")
            if composite_rows:
                artifact_id = composite_rows[0].get("id")
                if isinstance(artifact_id, int) and not isinstance(artifact_id, bool) and artifact_id > 0:
                    return UpdateCandidate(run_id, sha, artifact_id, COMPOSITE_UPDATE_ARTIFACT_NAME, UPDATE_KIND_APP_AND_LAUNCHER)
                raise AppUpdateError("UPDATE_ARTIFACT_AMBIGUOUS")
            if require_composite and composite_named:
                raise AppUpdateError("UPDATE_ARTIFACT_EXPIRED")
            if legacy_rows:
                artifact_id = legacy_rows[0].get("id")
                if isinstance(artifact_id, int) and not isinstance(artifact_id, bool) and artifact_id > 0:
                    # This is a deliberate bootstrap candidate only when a
                    # composite is not available in the exact run.  The
                    # prepare path records the durable continuation marker.
                    return UpdateCandidate(run_id, sha, artifact_id, LEGACY_UPDATE_ARTIFACT_NAME, UPDATE_KIND_APP_ONLY)
                raise AppUpdateError("UPDATE_ARTIFACT_AMBIGUOUS")
        return None

    def _candidate_for_exact_run(self, *, source_commit: str, workflow_run_id: int) -> UpdateCandidate | None:
        """Find only the composite artifact bound to one prior APP_ONLY run."""

        if not _SHA_RE.fullmatch(source_commit) or isinstance(workflow_run_id, bool) or not isinstance(workflow_run_id, int) or not 0 < workflow_run_id <= MAX_WORKFLOW_RUN_ID:
            raise AppUpdateError("UPDATE_RUN_MISMATCH")
        artifacts = self._api_json(
            f"repos/{REPOSITORY}/actions/runs/{workflow_run_id}/artifacts",
            {"per_page": "100"},
        ).get("artifacts")
        rows = self._artifact_candidates(artifacts, name=COMPOSITE_UPDATE_ARTIFACT_NAME)
        if not rows:
            named = [item for item in artifacts if isinstance(item, dict) and item.get("name") == COMPOSITE_UPDATE_ARTIFACT_NAME]
            if named:
                raise AppUpdateError("UPDATE_ARTIFACT_EXPIRED")
            return None
        artifact_id = rows[0].get("id")
        if not isinstance(artifact_id, int) or isinstance(artifact_id, bool) or artifact_id <= 0:
            raise AppUpdateError("UPDATE_ARTIFACT_AMBIGUOUS")
        return UpdateCandidate(workflow_run_id, source_commit, artifact_id, COMPOSITE_UPDATE_ARTIFACT_NAME, UPDATE_KIND_APP_AND_LAUNCHER)

    def _current_build(self, root: Path) -> dict[str, str]:
        plan = resolve_launch_plan(root, allow_test_root=self._allow_test_root)
        build_path = plan.payload_root / "build.json"
        if not build_path.is_file() or build_path.is_symlink():
            return {"commit": "legacy", "payload_id": plan.version}
        try:
            value = _safe_json_file(build_path, max_bytes=32 * 1024)
        except (OSError, UnicodeError, json.JSONDecodeError, AppUpdateError):
            return {"commit": "unknown", "payload_id": plan.version}
        commit = str(value.get("source_commit") or "")
        if value.get("schema_version") != BUILD_INFO_SCHEMA or not _SHA_RE.fullmatch(commit):
            commit = "unknown"
        return {"commit": commit, "payload_id": plan.version}

    def _current_build_metadata(self, root: Path) -> dict[str, Any]:
        """Read the bounded source/run identity needed by bootstrap continuation."""

        plan = resolve_launch_plan(root, allow_test_root=self._allow_test_root)
        path = plan.payload_root / "build.json"
        if not path.is_file() or path.is_symlink():
            return {"commit": "legacy", "payload_id": plan.version, "workflow_run_id": None}
        try:
            value = _safe_json_file(path, max_bytes=32 * 1024)
        except (OSError, UnicodeError, UnicodeDecodeError, json.JSONDecodeError, AppUpdateError):
            return {"commit": "unknown", "payload_id": plan.version, "workflow_run_id": None}
        commit = value.get("source_commit")
        run_id = value.get("workflow_run_id")
        if value.get("schema_version") != BUILD_INFO_SCHEMA or not isinstance(commit, str) or not _SHA_RE.fullmatch(commit):
            commit = "unknown"
        if isinstance(run_id, bool) or not isinstance(run_id, int) or not 0 < run_id <= MAX_WORKFLOW_RUN_ID:
            run_id = None
        return {"commit": commit, "payload_id": plan.version, "workflow_run_id": run_id}

    def _channel_relation(self, current_commit: str, candidate_commit: str) -> str:
        """Prove update ancestry before exposing or preparing a main update."""

        if current_commit in {"legacy", "unknown"} or not _SHA_RE.fullmatch(current_commit):
            return "legacy_or_unbound"
        if current_commit == candidate_commit:
            return "same"
        try:
            comparison = self._api_json(f"repos/{REPOSITORY}/compare/{current_commit}...{candidate_commit}")
        except AppUpdateError:
            return "channel_relation_unavailable"
        return _classify_channel_relation(comparison.get("status"))

    @staticmethod
    def _verified_previous_payload_id(root: Path) -> str | None:
        """Read only an opaque, hash-bound rollback payload identifier."""

        path = root / "update-state" / "previous-current.json"
        try:
            value = _safe_json_file(path, max_bytes=32 * 1024)
        except (OSError, UnicodeError, UnicodeDecodeError, json.JSONDecodeError, AppUpdateError):
            return None
        version = value.get("version")
        relative = value.get("payload_relative")
        digest = value.get("manifest_sha256")
        if (
            value.get("schema_version") != POINTER_SCHEMA
            or not isinstance(version, str)
            or _safe_opaque_id(version) is None
            or relative != f"versions/{version}"
            or not isinstance(digest, str)
            or _SHA256_RE.fullmatch(digest) is None
        ):
            return None
        manifest = root / relative / "manifest.json"
        try:
            if manifest.is_symlink() or not manifest.is_file() or _sha256(manifest) != digest:
                return None
        except OSError:
            return None
        return version

    def _remember_update_state(
        self,
        root: Path,
        *,
        phase: str,
        progress: int,
        transaction_id: str | None = None,
        can_prepare: bool = False,
        can_restart: bool = False,
        requires_restart: bool = False,
        current_payload_id: str | None = None,
        candidate_payload_id: str | None = None,
        rollback_payload_id: str | None = None,
        reason_code: str | None = None,
        last_error_code: str | None = None,
    ) -> dict[str, Any] | None:
        return _try_write_update_state(
            root,
            phase=phase,
            progress=progress,
            transaction_id=transaction_id,
            can_prepare=can_prepare,
            can_restart=can_restart,
            requires_restart=requires_restart,
            current_payload_id=current_payload_id,
            candidate_payload_id=candidate_payload_id,
            rollback_payload_id=rollback_payload_id,
            reason_code=reason_code,
            last_error_code=last_error_code,
        )

    def _public_projection(self, result: dict[str, Any], *, root: Path | None = None) -> dict[str, Any]:
        """Add the canonical state contract while preserving legacy fields."""

        value = dict(result)
        persisted: dict[str, Any] | None = None
        staged: dict[str, Any] | None = None
        bootstrap: dict[str, Any] | None = None
        if root is not None:
            try:
                persisted = _read_update_state(root)
            except AppUpdateError:
                persisted = None
            try:
                staged = _read_staged_update(root)
            except (OSError, UnicodeError, UnicodeDecodeError, json.JSONDecodeError, AppUpdateError):
                staged = None
            try:
                bootstrap = _read_bootstrap_pending(root)
            except (OSError, UnicodeError, UnicodeDecodeError, json.JSONDecodeError, AppUpdateError):
                bootstrap = None

        status = str(value.get("status") or "unavailable")
        current_payload = _safe_opaque_id(value.get("current_payload_id") or value.get("current_payload"))
        candidate_payload = _safe_opaque_id(value.get("candidate_payload_id") or value.get("latest_payload"))
        rollback_payload = _safe_opaque_id(value.get("rollback_payload_id"))
        transaction_id = value.get("transaction_id") if isinstance(value.get("transaction_id"), str) else None
        if staged is not None:
            transaction_id = staged.get("transaction_id") if isinstance(staged.get("transaction_id"), str) else transaction_id
            candidate_payload = _safe_opaque_id(staged.get("payload_id")) or candidate_payload
            previous = staged.get("previous")
            if isinstance(previous, dict):
                rollback_payload = _safe_opaque_id(previous.get("version")) or rollback_payload
        if persisted is not None:
            transaction_id = transaction_id or persisted.get("transaction_id")
            current_payload = current_payload or _safe_opaque_id(persisted.get("current_payload_id"))
            candidate_payload = candidate_payload or _safe_opaque_id(persisted.get("candidate_payload_id"))
            rollback_payload = rollback_payload or _safe_opaque_id(persisted.get("rollback_payload_id"))
        if rollback_payload is None and staged is not None and root is not None:
            rollback_payload = self._verified_previous_payload_id(root)
        if transaction_id is not None and _TRANSACTION_RE.fullmatch(transaction_id) is None:
            transaction_id = None

        pending_exists = False
        if root is not None:
            pending_path = _pending_health_path(root)
            pending_exists = pending_path.is_file() and not pending_path.is_symlink()
        if status in {"restarting", "activated"} or pending_exists:
            phase = "restarting"
            progress = 85
            can_prepare = False
            can_restart = False
            requires_restart = True
        elif staged is not None or status in {"staged", "ready_to_restart"}:
            phase = "staged"
            progress = 100
            can_prepare = False
            can_restart = True
            requires_restart = True
        elif status in {"available", "launcher_migration_required"}:
            phase = "update_available"
            progress = 0
            can_prepare = True
            can_restart = False
            requires_restart = True
        elif status in {"checking"}:
            phase = "checking"
            progress = 0
            can_prepare = False
            can_restart = False
            requires_restart = False
        elif status in {"downloading", "verifying", "staging", "preparing"}:
            phase = "preparing"
            progress = 10 if status == "downloading" else 40 if status == "verifying" else 75
            can_prepare = False
            can_restart = False
            requires_restart = True
        elif status in {"rolled_back", "rollback"}:
            phase = "rolled_back"
            progress = 100
            can_prepare = False
            can_restart = True if status == "rollback" else False
            requires_restart = status == "rollback"
        elif status in {"blocked", "auth_required", "oauth_configuration_required", "blocked_current_ahead_of_main", "blocked_channel_diverged", "channel_relation_unavailable", "unavailable"}:
            persisted_error = (
                isinstance(persisted, dict)
                and persisted.get("phase") == "error"
                and isinstance(value.get("code"), str)
                and persisted.get("last_error_code") == value.get("code")
            )
            phase = "error" if persisted_error else "blocked"
            progress = int(persisted.get("progress", 0)) if persisted_error and isinstance(persisted.get("progress"), int) else 0
            can_prepare = bool(persisted.get("can_prepare")) if persisted_error else False
            can_restart = bool(persisted.get("can_restart")) if persisted_error else False
            requires_restart = bool(persisted.get("requires_restart")) if persisted_error else False
        elif status == "no_artifact":
            phase = "idle"
            progress = 0
            can_prepare = False
            can_restart = False
            requires_restart = False
        elif status == "up_to_date":
            phase = "succeeded" if persisted is not None and persisted.get("phase") == "succeeded" else "idle"
            progress = 100 if phase == "succeeded" else 0
            can_prepare = False
            can_restart = False
            requires_restart = False
        else:
            phase = persisted.get("phase") if persisted and persisted.get("phase") in UPDATE_PHASES else "error"
            progress = int(persisted.get("progress", 0)) if persisted and isinstance(persisted.get("progress"), int) else 0
            can_prepare = False
            can_restart = False
            requires_restart = False

        # Rollback is owned by the post-activation watchdog/recovery surface.
        # A staged candidate has not changed the current pointer yet, so it
        # must never advertise a manual rollback action.  The two fields are
        # deliberately projection-only additions; the on-disk v1 state remains
        # backward compatible with older payloads.
        if pending_exists or status in {"restarting", "activated"}:
            rollback_mode = "automatic_watchdog"
            can_rollback = rollback_payload is not None
        elif staged is not None or phase in {"staged", "confirm_restart"}:
            rollback_mode = "automatic_watchdog"
            can_rollback = False
        elif rollback_payload is not None:
            rollback_mode = "recovery_only"
            can_rollback = False
        else:
            rollback_mode = "unavailable"
            can_rollback = False

        code = value.get("code")
        reason = value.get("reason_code")
        if not isinstance(reason, str) or reason not in STABLE_UPDATE_REASON_CODES:
            reason = reason_code_for(code) if code else _STATUS_REASON_CODES.get(status)
        last_error = value.get("last_error_code")
        if not isinstance(last_error, str):
            last_error = code if isinstance(code, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{2,95}", code) else None
        if last_error is None and persisted is not None and phase in {"blocked", "error", "rolled_back"}:
            last_error = persisted.get("last_error_code")
        if transaction_id is None and self._retry_attempt is not None and phase == "checking":
            transaction_id = None
        launcher = launcher_projection(root) if root is not None else {"status": "unavailable", "format": "unknown", "migration_required": True}
        launcher_status = str(launcher.get("status") or "unavailable")
        launcher_format = str(launcher.get("format") or "unknown")
        launcher_migration_required = launcher.get("migration_required") is True
        if isinstance(staged, dict) and staged.get("update_kind") == UPDATE_KIND_APP_AND_LAUNCHER:
            launcher_status = "candidate_verified" if phase in {"staged", "confirm_restart", "restarting"} else launcher_status
            launcher_format = "onedir"
            launcher_migration_required = False if phase in {"staged", "confirm_restart", "restarting", "succeeded"} else launcher_migration_required
        payload_status = "unavailable" if current_payload is None else "satisfied" if status == "up_to_date" else "candidate_ready" if staged is not None else "available" if candidate_payload and candidate_payload != current_payload else "satisfied"
        restart_state = "pending" if pending_exists else "awaiting_restart" if staged is not None else "idle"
        value.update({
            "transaction_id": transaction_id,
            "phase": phase,
            "progress": progress,
            "can_prepare": can_prepare,
            "can_restart": can_restart,
            "requires_restart": requires_restart,
            "current_payload_id": current_payload,
            "candidate_payload_id": candidate_payload,
            "rollback_payload_id": rollback_payload,
            "can_rollback": can_rollback,
            "rollback_mode": rollback_mode,
            "reason_code": reason,
            "last_error_code": last_error,
            # Product-level status deliberately keeps payload and shell
            # contracts separate.  A legacy single-file shell therefore
            # cannot be reported as a complete up_to_date product.
            "payload_status": payload_status,
            "launcher_status": launcher_status,
            "launcher_format": launcher_format,
            "launcher_migration_required": launcher_migration_required,
            "restart_state": restart_state,
            "bootstrap_pending": bool(bootstrap and bootstrap.get("status") in {"pending", "downloading", "verifying", "staged", "restarting"}),
            "bootstrap_status": bootstrap.get("status") if isinstance(bootstrap, dict) else None,
            "bootstrap_transaction_id": bootstrap.get("transaction_id") if isinstance(bootstrap, dict) else None,
            "bootstrap_payload_id": bootstrap.get("payload_id") if isinstance(bootstrap, dict) else None,
            "bootstrap_restart_authorized": bool(bootstrap and bootstrap.get("restart_authorized") is True),
        })
        return value

    def error_projection(self, error_code: object) -> dict[str, Any]:
        """Return a complete path-free error projection for an API route."""

        code = str(error_code or "UPDATE_FAILED")
        try:
            root = self._install_root()
            current = self._current_build(root)
            staged = _read_staged_update(root)
            previous = staged.get("previous") if isinstance(staged, dict) else None
            value = update_error_projection(
                code,
                current_payload_id=current.get("payload_id"),
                candidate_payload_id=staged.get("payload_id") if isinstance(staged, dict) else None,
                rollback_payload_id=previous.get("version") if isinstance(previous, dict) else None,
                transaction_id=staged.get("transaction_id") if isinstance(staged, dict) else None,
            )
            try:
                state = _read_update_state(root)
            except AppUpdateError:
                state = None
            if isinstance(state, dict) and state.get("last_error_code") == code:
                value.update({
                    "phase": state.get("phase", value["phase"]),
                    "progress": state.get("progress", value["progress"]),
                    "transaction_id": state.get("transaction_id") or value["transaction_id"],
                    "current_payload_id": state.get("current_payload_id") or value["current_payload_id"],
                    "candidate_payload_id": state.get("candidate_payload_id") or value["candidate_payload_id"],
                    "rollback_payload_id": state.get("rollback_payload_id") or value["rollback_payload_id"],
                    "reason_code": state.get("reason_code") or value["reason_code"],
                    "last_error_code": state.get("last_error_code"),
                })
            return self._public_projection(value, root=root)
        except (AppUpdateError, OSError, UnicodeError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
            return update_error_projection(code)

    def status(self, *, refresh: bool = False) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            self._retry_attempt = None
            if not refresh and self._cached and now - self._cached[0] < CACHE_SECONDS:
                return dict(self._cached[1])
            root: Path | None = None
            current: dict[str, str] = {}
            candidate: UpdateCandidate | None = None
            try:
                root = self._install_root()
                current = self._current_build(root)
                _transport, auth = self._selected_transport()
                if auth.status != "ready":
                    result = {
                        "status": auth.status, "available": False, "product_version": PRODUCT_VERSION,
                        "current_build": current["commit"], "current_payload": current["payload_id"],
                        "latest_build": None, "transport": auth.transport, "code": auth.code, "action": auth.action,
                    }
                else:
                    shell_migration_required = launcher_projection(root).get("migration_required") is True
                    candidate = self._latest_candidate(require_composite=shell_migration_required)
                    if candidate is None:
                        result = {
                            "status": "no_artifact", "available": False, "product_version": PRODUCT_VERSION,
                            "current_build": current["commit"], "current_payload": current["payload_id"],
                            "latest_build": None, "transport": "github_cli", "action": "Chờ main CI tạo update artifact thành công.",
                        }
                    else:
                        staged_error: AppUpdateError | None = None
                        try:
                            staged = _read_staged_update(root)
                        except (OSError, UnicodeError, json.JSONDecodeError, AppUpdateError) as exc:
                            staged = None
                            staged_error = exc if isinstance(exc, AppUpdateError) else AppUpdateError("STAGED_UPDATE_INVALID")
                        if staged_error is not None:
                            result = {
                                "status": "blocked", "available": False, "product_version": PRODUCT_VERSION,
                                "current_build": current["commit"], "current_payload": current["payload_id"],
                                "latest_build": candidate.source_commit, "latest_payload": f"main-{candidate.source_commit[:12]}",
                                "transport": auth.transport, "code": staged_error.code,
                                "action": "Trạng thái candidate đã stage không hợp lệ; Hub giữ nguyên payload hiện tại.",
                            }
                        elif staged is not None and staged.get("source_commit") == candidate.source_commit:
                            pending_path = _pending_health_path(root)
                            pending_exists = pending_path.is_file() and not pending_path.is_symlink()
                            result = {
                                "status": "restarting" if pending_exists else "staged", "available": False, "product_version": PRODUCT_VERSION,
                                "current_build": current["commit"], "current_payload": current["payload_id"],
                                "latest_build": candidate.source_commit, "latest_payload": staged.get("payload_id"),
                                "staged_payload": staged.get("payload_id"), "restart_required": True,
                                "run_id": candidate.run_id, "artifact_id": candidate.artifact_id, "transport": auth.transport,
                                "action": "Candidate đã stage; khởi động lại để commit pointer an toàn.",
                            }
                        else:
                            relation = self._channel_relation(current["commit"], candidate.source_commit)
                            if relation == "blocked_current_ahead_of_main":
                                result = {
                                    "status": relation, "available": False, "product_version": PRODUCT_VERSION,
                                    "current_build": current["commit"], "current_payload": current["payload_id"],
                                    "latest_build": candidate.source_commit, "latest_payload": f"main-{candidate.source_commit[:12]}",
                                    "run_id": candidate.run_id, "artifact_id": candidate.artifact_id, "transport": auth.transport,
                                    "action": "Bản đang chạy chứa thay đổi chưa được tích hợp vào main; Hub sẽ không tự hạ cấp.",
                                }
                            elif relation == "blocked_channel_diverged":
                                result = {
                                    "status": relation, "available": False, "product_version": PRODUCT_VERSION,
                                    "current_build": current["commit"], "current_payload": current["payload_id"],
                                    "latest_build": candidate.source_commit, "latest_payload": f"main-{candidate.source_commit[:12]}",
                                    "run_id": candidate.run_id, "artifact_id": candidate.artifact_id, "transport": auth.transport,
                                    "action": "Bản đang chạy và main đã tách lịch sử; không tự thay đổi payload.",
                                }
                            elif relation == "channel_relation_unavailable":
                                result = {
                                    "status": relation, "available": False, "product_version": PRODUCT_VERSION,
                                    "current_build": current["commit"], "current_payload": current["payload_id"],
                                    "latest_build": candidate.source_commit, "latest_payload": f"main-{candidate.source_commit[:12]}",
                                    "transport": auth.transport,
                                    "action": "Không xác minh được ancestry của payload; Hub không tự cài đặt.",
                                }
                            else:
                                available = relation == "forward_update_available" or (relation == "legacy_or_unbound" and current["commit"] != candidate.source_commit) or (relation == "same" and shell_migration_required)
                                result = {
                                    "status": "available" if available else "up_to_date", "available": available,
                                    "product_version": PRODUCT_VERSION, "current_build": current["commit"],
                                    "current_payload": current["payload_id"], "latest_build": candidate.source_commit,
                                    "latest_payload": f"main-{candidate.source_commit[:12]}", "run_id": candidate.run_id,
                                    "artifact_id": candidate.artifact_id, "transport": auth.transport,
                                    "action": "Cập nhật Local AI Hub" if available else "Bạn đang dùng build main mới nhất.",
                                }
                                if relation == "same" and shell_migration_required:
                                    result.update({
                                        "status": "launcher_migration_required",
                                        "code": "UPDATE_LAUNCHER_REQUIRED",
                                        "reason_code": "launcher_migration_required",
                                        "action": "Shell Local AI Hub legacy cần được nâng cấp cùng payload trong một composite update.",
                                    })
            except AppUpdateError as exc:
                result = {
                    "status": "unavailable", "available": False, "product_version": PRODUCT_VERSION,
                    "current_build": current.get("commit"), "current_payload": current.get("payload_id"),
                    "latest_build": candidate.source_commit if candidate is not None else None,
                    "latest_payload": f"main-{candidate.source_commit[:12]}" if candidate is not None else None,
                    "transport": "updater", "code": exc.code,
                    "action": "Cài/đăng nhập GitHub CLI hoặc mở Diagnostics để kiểm tra updater.",
                }
            if self._retry_attempt is not None:
                result["retry_attempt"] = self._retry_attempt
                result["retry_attempts"] = self._retry_attempt
            result = self._public_projection(result, root=root)
            self._cached = (now, dict(result))
            return result

    def changes(self) -> dict[str, Any]:
        status = self.status(refresh=True)
        current = str(status.get("current_build") or "")
        latest = str(status.get("latest_build") or "")
        if not _SHA_RE.fullmatch(latest):
            return {"status": "unavailable", "commits": [], "reason": "Chưa có main build có update artifact."}
        if not _SHA_RE.fullmatch(current):
            return {"status": "partial", "from": current or "legacy", "to": latest, "commits": [], "reason": "Bản hiện tại chưa có build SHA; thay đổi chi tiết sẽ khả dụng sau lần cập nhật đầu tiên."}
        value = self._api_json(f"repos/{REPOSITORY}/compare/{current}...{latest}")
        rows = value.get("commits") if isinstance(value.get("commits"), list) else []
        commits: list[dict[str, str]] = []
        for row in rows[-20:]:
            if not isinstance(row, dict):
                continue
            sha = str(row.get("sha") or "")
            message = str(((row.get("commit") or {}).get("message") if isinstance(row.get("commit"), dict) else "") or "").splitlines()[0]
            if _SHA_RE.fullmatch(sha) and message:
                commits.append({"sha": sha[:12], "message": message[:180]})
        return {"status": "completed", "from": current, "to": latest, "commits": commits, "ahead_by": value.get("ahead_by")}

    def _download_candidate(self, candidate: UpdateCandidate, destination: Path) -> None:
        try:
            self._transport().download_artifact(candidate.run_id, destination, candidate.artifact_name)
        except TransportError as exc:
            raise AppUpdateError(exc.code) from exc

    def _validate_download(self, folder: Path, candidate: UpdateCandidate) -> tuple[dict[str, Any], dict[str, Any], Path]:
        manifest = _safe_update_manifest(_safe_json_file(folder / "update-manifest.json"), expected_commit=candidate.source_commit)
        contract_path = folder / UPDATE_CONTRACT_NAME
        if contract_path.is_file() and not contract_path.is_symlink():
            contract = _safe_update_contract(_safe_json_file(contract_path), expected_commit=candidate.source_commit)
            if contract.get("update_kind") == UPDATE_KIND_APP_AND_LAUNCHER:
                if manifest.get("schema_version") != COMPOSITE_UPDATE_SCHEMA:
                    raise AppUpdateError("UPDATE_PRODUCT_CONTRACT_INVALID")
                if manifest.get("workflow_run_id") != candidate.run_id or contract.get("workflow_run_id") != candidate.run_id:
                    raise AppUpdateError("UPDATE_RUN_MISMATCH")
                if manifest.get("payload_id") != contract.get("app_payload_id"):
                    raise AppUpdateError("UPDATE_PRODUCT_CONTRACT_INVALID")
                if manifest.get("launcher_format") != contract.get("launcher_format") or manifest.get("launcher_executable_sha256") != contract.get("launcher_executable_sha256") or manifest.get("launcher_manifest_sha256") != contract.get("launcher_tree_manifest_sha256"):
                    raise AppUpdateError("UPDATE_PRODUCT_CONTRACT_INVALID")
                if manifest.get("app_manifest_sha256") != contract.get("app_manifest_sha256") or manifest.get("app_file_count") != contract.get("app_file_count") or manifest.get("app_total_bytes") != contract.get("app_total_bytes"):
                    raise AppUpdateError("UPDATE_PRODUCT_CONTRACT_INVALID")
            sums_path = folder / "SHA256SUMS.txt"
            if not sums_path.is_file() or sums_path.is_symlink():
                raise AppUpdateError("UPDATE_CHECKSUM_MANIFEST_MISSING")
            expected_contract_hash = None
            for row in sums_path.read_text(encoding="utf-8").splitlines():
                parts = row.split()
                if len(parts) == 2 and parts[1] == UPDATE_CONTRACT_NAME:
                    expected_contract_hash = parts[0]
            if not isinstance(expected_contract_hash, str) or _sha256(contract_path) != expected_contract_hash:
                raise AppUpdateError("UPDATE_CONTRACT_HASH_MISMATCH")
        else:
            # Payloads created before this contract existed remain APP_ONLY;
            # FULL is never inferred from a legacy artifact.
            contract = {
                "schema_version": UPDATE_CONTRACT_SCHEMA,
                "update_kind": UPDATE_KIND_APP_ONLY,
                "app_protocol": API_PROTOCOL_VERSION,
                "runtime_contract": RUNTIME_CONTRACT_REUSE_CURRENT,
                "runtime_version": None,
                "runtime_hash": None,
                "minimum_launcher_version": MINIMUM_LAUNCHER_VERSION,
                "source_commit": candidate.source_commit,
                "legacy_manifest": True,
            }
        expected_strategy = RUNTIME_STRATEGY_BUNDLED if contract["update_kind"] == UPDATE_KIND_FULL else RUNTIME_STRATEGY
        if candidate.artifact_name == LEGACY_UPDATE_ARTIFACT_NAME and contract["update_kind"] == UPDATE_KIND_APP_AND_LAUNCHER:
            # The historical artifact is intentionally legacy-parseable.  A
            # composite payload under that identity would be offered to the
            # exact de616f4 client, which is forbidden by the bootstrap
            # contract.
            raise AppUpdateError("UPDATE_OLD_CLIENT_ARTIFACT_MISMATCH")
        if candidate.artifact_name == COMPOSITE_UPDATE_ARTIFACT_NAME and contract["update_kind"] != UPDATE_KIND_APP_AND_LAUNCHER:
            raise AppUpdateError("UPDATE_COMPOSITE_ARTIFACT_REQUIRED")
        if manifest.get("runtime_strategy") != expected_strategy:
            raise AppUpdateError("UPDATE_RUNTIME_CONTRACT_MISMATCH")
        archive = folder / str(manifest["archive"])
        if not archive.is_file() or archive.is_symlink() or _sha256(archive) != manifest["archive_sha256"]:
            raise AppUpdateError("UPDATE_ARCHIVE_HASH_MISMATCH")
        return manifest, contract, archive

    def auth_status(self) -> dict[str, Any]:
        _transport, state = self._selected_transport()
        return state.public()

    def begin_device_login(self) -> dict[str, Any]:
        try:
            if self._transport_override is not None:
                return self._transport_override.begin_device_login()
            if self._transport_selector is None:
                self._transport_selector = build_transport(runner=self._runner, gh_path=self._gh_path)
            return self._transport_selector.native.begin_device_login()
        except TransportError as exc:
            return {"status": "unavailable", "transport": "github_oauth_device", "code": exc.code}

    def logout_auth(self) -> dict[str, Any]:
        try:
            if self._transport_override is not None:
                return self._transport_override.logout()
            if self._transport_selector is None:
                self._transport_selector = build_transport(runner=self._runner, gh_path=self._gh_path)
            return self._transport_selector.native.logout()
        except TransportError as exc:
            return {"status": "unavailable", "code": exc.code}

    def poll_device_login(self, session_id: str) -> dict[str, Any]:
        try:
            if self._transport_override is not None:
                native = self._transport_override
            else:
                if self._transport_selector is None:
                    self._transport_selector = build_transport(runner=self._runner, gh_path=self._gh_path)
                native = self._transport_selector.native
            return native.poll_device_session(session_id)
        except TransportError as exc:
            return {"status": "unavailable", "transport": "github_oauth_device", "code": exc.code}

    def cancel_device_login(self, session_id: str) -> dict[str, Any]:
        try:
            if self._transport_override is not None:
                native = self._transport_override
            else:
                if self._transport_selector is None:
                    self._transport_selector = build_transport(runner=self._runner, gh_path=self._gh_path)
                native = self._transport_selector.native
            return native.cancel_device_session(session_id)
        except TransportError as exc:
            return {"status": "unavailable", "transport": "github_oauth_device", "code": exc.code}

    def _validate_staged_imports(self, app_root: Path, runtime_pythonw: Path) -> None:
        environment = dict(os.environ)
        environment.update({"PYTHONPATH": str(app_root), "PYTHONNOUSERSITE": "1", "PYTHONUTF8": "1"})
        result = subprocess.run(
            [str(runtime_pythonw), "-c", "import src.app.launcher, src.services.api.api_server"],
            cwd=str(app_root), env=environment, timeout=30.0, check=False,
            creationflags=self._creationflags(),
        )
        if result.returncode != 0:
            raise AppUpdateError("UPDATE_IMPORT_PREFLIGHT_FAILED")

    def _preflight_candidate_api(
        self,
        *,
        install_root: Path,
        app_root: Path,
        runtime_pythonw: Path,
        payload_id: str,
        source_commit: str,
        work_root: Path,
    ) -> dict[str, Any]:
        """Start a staged candidate on an owned ephemeral loopback port.

        This probe is deliberately independent of the production API port and
        data root.  The candidate must return the same bounded product/API
        identity plus the exact staged build identity while its child remains
        alive.  No pointer/history/pending marker is written until this proof
        succeeds.
        """

        if not _SHA_RE.fullmatch(source_commit) or not _PAYLOAD_RE.fullmatch(payload_id):
            raise AppUpdateError("UPDATE_CANDIDATE_IDENTITY_INVALID")
        if not app_root.is_dir() or app_root.is_symlink() or not runtime_pythonw.is_file() or runtime_pythonw.is_symlink():
            raise AppUpdateError("UPDATE_CANDIDATE_RUNTIME_UNAVAILABLE")
        try:
            preflight_data = work_root / "candidate-data"
            preflight_data.mkdir(parents=True, exist_ok=False)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                probe.bind(("127.0.0.1", 0))
                port = int(probe.getsockname()[1])
        except (OSError, ValueError) as exc:
            raise AppUpdateError("UPDATE_CANDIDATE_PORT_UNAVAILABLE") from exc

        environment = dict(os.environ)
        environment.update({
            "LOCALAIHUB_INSTALL_ROOT": str(install_root),
            "LOCALAIHUB_APP_ROOT": str(app_root),
            "LOCALAIHUB_DATA_ROOT": str(preflight_data),
            "LOCALAIHUB_PORT": str(port),
            "LOCALAIHUB_BIND_HOST": "127.0.0.1",
            "LOCALAIHUB_BUILD_SHA": source_commit,
            "LOCALAIHUB_BUILD_PAYLOAD": payload_id,
            "LOCALAIHUB_PREFLIGHT": "1",
            "PYTHONPATH": str(app_root),
            "PYTHONNOUSERSITE": "1",
            "PYTHONUTF8": "1",
        })
        log_path = work_root / "candidate-api-preflight.log"
        child: subprocess.Popen[object] | None = None
        expected_identity = api_identity(
            product_version=PRODUCT_VERSION,
            installation_root=install_root,
            app_root=app_root,
            data_root=preflight_data,
        )
        deadline = time.monotonic() + CANDIDATE_API_PREFLIGHT_TIMEOUT_SECONDS
        try:
            with log_path.open("ab") as log:
                try:
                    child = subprocess.Popen(
                        [str(runtime_pythonw), *api_server_command(app_root)],
                        cwd=str(app_root),
                        env=environment,
                        stdin=subprocess.DEVNULL,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        close_fds=True,
                        creationflags=self._creationflags(),
                    )
                except (OSError, ValueError) as exc:
                    raise AppUpdateError("UPDATE_CANDIDATE_API_START_FAILED") from exc
                while time.monotonic() < deadline:
                    if child.poll() is not None:
                        raise AppUpdateError("UPDATE_CANDIDATE_API_EXITED")
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.5) as response:
                            raw = response.read(128 * 1024 + 1)
                        payload = json.loads(raw.decode("utf-8"))
                    except (OSError, UnicodeDecodeError, json.JSONDecodeError, urllib.error.HTTPError, urllib.error.URLError, ValueError):
                        time.sleep(0.2)
                        continue
                    if (
                        isinstance(payload, dict)
                        and payload.get("status") == "healthy"
                        and all(payload.get(key) == value for key, value in expected_identity.items())
                        and payload.get("api_protocol_version") == API_PROTOCOL_VERSION
                        and payload.get("build_source_commit") == source_commit
                        and payload.get("build_payload_id") == payload_id
                    ):
                        if child.poll() is not None:
                            raise AppUpdateError("UPDATE_CANDIDATE_API_EXITED")
                        try:
                            with urllib.request.urlopen(f"http://127.0.0.1:{port}/ui/", timeout=1.0) as ui_response:
                                ui_raw = ui_response.read(256 * 1024 + 1)
                            ui_text = ui_raw.decode("utf-8")
                            if len(ui_raw) > 256 * 1024 or "Local AI Hub" not in ui_text or "/ui/app.js" not in ui_text:
                                raise AppUpdateError("UPDATE_CANDIDATE_FRONTEND_PREFLIGHT_FAILED")
                            with urllib.request.urlopen(
                                f"http://127.0.0.1:{port}/api/bootstrap",
                                timeout=CANDIDATE_BOOTSTRAP_PREFLIGHT_TIMEOUT_SECONDS,
                            ) as bootstrap_response:
                                bootstrap_raw = bootstrap_response.read(256 * 1024 + 1)
                            bootstrap = json.loads(bootstrap_raw.decode("utf-8"))
                            if len(bootstrap_raw) > 256 * 1024 or not isinstance(bootstrap, dict):
                                raise AppUpdateError("UPDATE_CANDIDATE_BOOTSTRAP_PREFLIGHT_FAILED")
                        except AppUpdateError:
                            raise
                        except (OSError, UnicodeDecodeError, UnicodeError, json.JSONDecodeError, urllib.error.HTTPError, urllib.error.URLError, ValueError) as exc:
                            raise AppUpdateError("UPDATE_CANDIDATE_FRONTEND_PREFLIGHT_FAILED") from exc
                        return {"status": "passed", "port": port, "payload_id": payload_id, "source_commit": source_commit, "frontend_static": "passed", "bootstrap": "passed"}
                    time.sleep(0.2)
            raise AppUpdateError("UPDATE_CANDIDATE_API_TIMEOUT")
        finally:
            if child is not None:
                try:
                    terminate_owned_process(child)
                except Exception:
                    pass

    def staged_update(self) -> dict[str, Any] | None:
        """Return the validated staged candidate, without changing pointers."""

        with self._lock:
            root = self._install_root()
            try:
                value = _read_staged_update(root)
            except (OSError, UnicodeError, json.JSONDecodeError):
                raise AppUpdateError("STAGED_UPDATE_INVALID") from None
            if value is None:
                return None
            self._verify_staged_payload(root, value)
            return value

    def continue_bootstrap_after_restart(self) -> dict[str, Any]:
        """Continue a legacy APP_ONLY transition without a second artifact pick.

        The exact base client can install only the historical APP_ONLY
        contract.  Once that payload starts, its build metadata identifies the
        trusted workflow run; this method binds the next lookup to that exact
        commit/run and stages the distinct composite artifact automatically.
        It never changes ``current.json`` or launches a second process by
        itself, so the normal native restart transaction remains the only
        activation path.
        """

        with self._lock:
            root = self._install_root()
            with _update_serialization_lock(root):
                metadata = self._current_build_metadata(root)
                pending = _read_bootstrap_pending(root)
                if launcher_projection(root).get("migration_required") is not True:
                    if pending is not None and pending.get("status") != "completed":
                        return _complete_bootstrap_marker(root, pending, current=metadata)
                    return {"status": "not_required", "bootstrap_pending": False}
                if pending is None:
                    commit = metadata.get("commit")
                    run_id = metadata.get("workflow_run_id")
                    if not isinstance(commit, str) or not _SHA_RE.fullmatch(commit) or not isinstance(run_id, int):
                        return {
                            "status": "blocked",
                            "code": "UPDATE_COMPOSITE_ARTIFACT_REQUIRED",
                            "bootstrap_pending": False,
                            "action": "Chưa có source/run identity để tiếp tục shell migration tự động.",
                        }
                    pending = _write_bootstrap_pending(
                        root,
                        source_commit=commit,
                        workflow_run_id=run_id,
                        status="pending",
                        transaction_id=self._transaction_id,
                    )
                if pending.get("status") in {"staged", "restarting", "completed"}:
                    return {
                        "status": pending["status"],
                        "bootstrap_pending": pending["status"] in {"staged", "restarting"},
                        "source_commit": pending["source_commit"],
                        "workflow_run_id": pending["workflow_run_id"],
                        "transaction_id": pending.get("transaction_id"),
                        "payload_id": pending.get("payload_id"),
                        "restart_authorized": pending.get("restart_authorized") is True,
                    }
                if pending.get("status") == "blocked":
                    return {
                        "status": "blocked",
                        "code": pending.get("reason_code") or "UPDATE_COMPOSITE_ARTIFACT_REQUIRED",
                        "bootstrap_pending": True,
                        "source_commit": pending["source_commit"],
                        "workflow_run_id": pending["workflow_run_id"],
                    }
                candidate = self._candidate_for_exact_run(
                    source_commit=str(pending["source_commit"]),
                    workflow_run_id=int(pending["workflow_run_id"]),
                )
                if candidate is None:
                    _write_bootstrap_pending(
                        root,
                        source_commit=str(pending["source_commit"]),
                        workflow_run_id=int(pending["workflow_run_id"]),
                        status="blocked",
                        reason_code="artifact_identity_mismatch",
                        transaction_id=pending.get("transaction_id") if isinstance(pending.get("transaction_id"), str) else None,
                        payload_id=pending.get("payload_id") if isinstance(pending.get("payload_id"), str) else None,
                    )
                    return {
                        "status": "blocked",
                        "code": "UPDATE_COMPOSITE_ARTIFACT_REQUIRED",
                        "bootstrap_pending": True,
                        "source_commit": pending["source_commit"],
                        "workflow_run_id": pending["workflow_run_id"],
                    }
                result = self._prepare_locked_impl(root, candidate_override=candidate)
                if result.get("update_kind") == UPDATE_KIND_APP_AND_LAUNCHER or result.get("status") == "staged":
                    _write_bootstrap_pending(
                        root,
                        source_commit=str(pending["source_commit"]),
                        workflow_run_id=int(pending["workflow_run_id"]),
                        status="staged",
                        transaction_id=result.get("transaction_id") if isinstance(result.get("transaction_id"), str) else pending.get("transaction_id"),
                        payload_id=result.get("payload_id") if isinstance(result.get("payload_id"), str) else pending.get("payload_id"),
                        restart_authorized=pending.get("restart_authorized") is True,
                    )
                    result["bootstrap_pending"] = True
                    result["bootstrap_status"] = "staged"
                    result["bootstrap_restart_authorized"] = pending.get("restart_authorized") is True
                return self._public_projection(result, root=root)

    @staticmethod
    def _verify_staged_payload(root: Path, staged: dict[str, Any]) -> None:
        """Revalidate a candidate immediately before pointer activation."""

        relative = str(staged.get("payload_relative") or "")
        payload_id = str(staged.get("payload_id") or "")
        source_commit = str(staged.get("source_commit") or "")
        if relative != f"versions/{payload_id}" or _PAYLOAD_RE.fullmatch(payload_id) is None or not _SHA_RE.fullmatch(source_commit) or payload_id != f"main-{source_commit[:12]}":
            raise AppUpdateError("STAGED_UPDATE_INVALID")
        payload = root / Path(relative)
        if payload.is_symlink() or not payload.is_dir():
            raise AppUpdateError("STAGED_PAYLOAD_UNAVAILABLE")
        manifest = payload / "manifest.json"
        build = payload / "build.json"
        if manifest.is_symlink() or not manifest.is_file() or _sha256(manifest) != str(staged.get("manifest_sha256")):
            raise AppUpdateError("STAGED_MANIFEST_HASH_MISMATCH")
        try:
            manifest_value = _safe_json_file(manifest, max_bytes=128 * 1024)
            build_value = _safe_json_file(build, max_bytes=32 * 1024)
        except (OSError, UnicodeError, json.JSONDecodeError, AppUpdateError) as exc:
            raise AppUpdateError("STAGED_PAYLOAD_INVALID") from exc
        if manifest_value.get("version") != payload_id or build_value.get("source_commit") != source_commit or build_value.get("schema_version") != BUILD_INFO_SCHEMA:
            raise AppUpdateError("STAGED_BUILD_IDENTITY_MISMATCH")
        runtime = payload / "runtime" / "Python312" / "pythonw.exe"
        app = payload / "app"
        if app.is_symlink() or not app.is_dir() or runtime.is_symlink() or not runtime.is_file():
            raise AppUpdateError("STAGED_RUNTIME_UNAVAILABLE")
        if staged.get("update_kind") == UPDATE_KIND_APP_AND_LAUNCHER:
            run_id = staged.get("workflow_run_id")
            if isinstance(run_id, bool) or not isinstance(run_id, int) or not 0 < run_id <= MAX_WORKFLOW_RUN_ID or build_value.get("workflow_run_id") != run_id:
                raise AppUpdateError("UPDATE_RUN_MISMATCH")
            try:
                launcher = launcher_tree_manifest(payload / "launcher" / "LocalAIHub")
            except LauncherMigrationError as exc:
                raise AppUpdateError("UPDATE_LAUNCHER_MANIFEST_MISMATCH") from exc
            if (
                staged.get("workflow_run_id") is None
                or launcher.get("format") != staged.get("launcher_format")
                or launcher.get("executable_sha256") != staged.get("launcher_executable_sha256")
                or launcher.get("tree_manifest_sha256") != staged.get("launcher_tree_manifest_sha256")
                or launcher.get("file_count") != staged.get("launcher_file_count")
                or launcher.get("total_bytes") != staged.get("launcher_total_bytes")
            ):
                raise AppUpdateError("UPDATE_LAUNCHER_MANIFEST_MISMATCH")

    def create_restart_session(self, *, payload_id: str, source_commit: str, nonce: str, parent_pid: int) -> dict[str, Any]:
        """Atomically publish the session contract consumed by the watchdog."""

        if _PAYLOAD_RE.fullmatch(payload_id) is None or not _SHA_RE.fullmatch(source_commit) or payload_id != f"main-{source_commit[:12]}":
            raise AppUpdateError("RESTART_SESSION_IDENTITY_INVALID")
        if not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{32}", nonce) or isinstance(parent_pid, bool) or not isinstance(parent_pid, int) or parent_pid <= 0:
            raise AppUpdateError("RESTART_SESSION_INVALID")
        root = self._install_root()
        value = {
            "schema_version": RESTART_SESSION_SCHEMA,
            "payload_id": payload_id,
            "source_commit": source_commit,
            "nonce": nonce,
            "parent_pid": parent_pid,
            "api_port": None,
            "api_pid": None,
            "status": "awaiting_candidate",
            "created_at": _utc_now(),
        }
        if self._transaction_id is not None and _TRANSACTION_RE.fullmatch(self._transaction_id):
            value["transaction_id"] = self._transaction_id
        _write_restart_session(root, value)
        return {key: value[key] for key in ("schema_version", "payload_id", "source_commit", "nonce", "parent_pid", "status", "transaction_id") if key in value}

    def prepare_staged_restart(self) -> dict[str, Any]:
        """Reserve a composite shell/payload restart without switching yet.

        Legacy ``commit_staged_restart`` remains available for older payloads
        and tests.  A composite product must follow the stricter ordering:
        close-preflight -> old desktop exit -> shell switch -> pointer
        activation.  This journal is the hand-off consumed by the candidate
        watchdog and contains only bounded identities and the previous
        pointer, never paths or commands in public projections.
        """

        with self._lock:
            root = self._install_root()
            with _update_serialization_lock(root):
                staged = _read_staged_update(root)
                if staged is None:
                    raise AppUpdateError("STAGED_UPDATE_UNAVAILABLE")
                self._verify_staged_payload(root, staged)
                if staged.get("update_kind") != UPDATE_KIND_APP_AND_LAUNCHER:
                    raise AppUpdateError("UPDATE_LAUNCHER_REQUIRED")
                current = load_current_pointer(root)
                previous = staged.get("previous")
                if not isinstance(previous, dict) or current != previous:
                    raise AppUpdateError("STAGED_CURRENT_POINTER_CHANGED")
                transaction_id = staged.get("transaction_id") if isinstance(staged.get("transaction_id"), str) else self._transaction_id
                if transaction_id is None or _TRANSACTION_RE.fullmatch(transaction_id) is None:
                    transaction_id = _new_transaction_id()
                value = {
                    "schema_version": RESTART_TRANSACTION_SCHEMA,
                    "transaction_id": transaction_id,
                    "payload_id": staged["payload_id"],
                    "source_commit": staged["source_commit"],
                    "update_kind": staged["update_kind"],
                    "previous": previous,
                    "manifest_sha256": staged["manifest_sha256"],
                    "workflow_run_id": staged.get("workflow_run_id"),
                    "launcher_format": staged.get("launcher_format"),
                    "launcher_executable_sha256": staged.get("launcher_executable_sha256"),
                    "launcher_tree_manifest_sha256": staged.get("launcher_tree_manifest_sha256"),
                    "launcher_file_count": staged.get("launcher_file_count"),
                    "launcher_total_bytes": staged.get("launcher_total_bytes"),
                    "status": "awaiting_old_exit",
                    "created_at": _utc_now(),
                }
                _write_json_atomic(_restart_transaction_path(root), value)
                self._transaction_id = transaction_id
                self._remember_update_state(
                    root,
                    phase="restarting",
                    progress=80,
                    transaction_id=transaction_id,
                    requires_restart=True,
                    current_payload_id=current.get("version"),
                    candidate_payload_id=staged.get("payload_id"),
                    rollback_payload_id=previous.get("version"),
                )
                return {
                    "status": "restart_prepared",
                    "source_commit": staged["source_commit"],
                    "payload_id": staged["payload_id"],
                    "transaction_id": transaction_id,
                    "previous_payload": previous.get("version"),
                    "restart_required": True,
                    "launcher_changed": True,
                    "update_kind": staged["update_kind"],
                    "commit_phase": "awaiting_old_exit",
                }

    def abort_restart_transaction(self, *, reason: str = "RESTART_TRANSACTION_FAILED") -> dict[str, Any]:
        """Cancel a deferred composite restart before pointer activation."""

        with self._lock:
            root = self._install_root()
            with _update_serialization_lock(root):
                path = _restart_transaction_path(root)
                if path.is_file() and not path.is_symlink():
                    _unlink_state(path)
                    _try_write_update_state(root, phase="error", progress=0, transaction_id=self._transaction_id, reason_code=reason_code_for(reason), last_error_code=reason if re.fullmatch(r"[A-Z][A-Z0-9_]{2,95}", reason) else "RESTART_TRANSACTION_FAILED")
                    return {"status": "aborted", "reason": str(reason)[:96]}
                return {"status": "not_pending"}

    def commit_staged_restart(self) -> dict[str, Any]:
        """Commit a staged candidate after the native close gate is ready.

        This is the only method that switches ``current.json``.  It is called
        by the native bridge after API/job ownership preflight and before the
        single authorized desktop destroy.  The method itself is atomic and
        fail-closed: any error after activation restores the exact previous
        pointer and removes pending state.
        """

        with self._lock:
            root = self._install_root()
            with _update_serialization_lock(root):
                staged = _read_staged_update(root)
                if staged is None:
                    raise AppUpdateError("STAGED_UPDATE_UNAVAILABLE")
                self._verify_staged_payload(root, staged)
                current = load_current_pointer(root)
                previous = staged.get("previous")
                if not isinstance(previous, dict) or current != previous:
                    raise AppUpdateError("STAGED_CURRENT_POINTER_CHANGED")
                payload_id = str(staged["payload_id"])
                source_commit = str(staged["source_commit"])
                manifest_hash = str(staged["manifest_sha256"])
                transaction_id = staged.get("transaction_id") if isinstance(staged.get("transaction_id"), str) else self._transaction_id
                activated = False
                try:
                    history = root / "update-state"
                    history.mkdir(parents=True, exist_ok=True)
                    _write_json_atomic(history / "previous-current.json", previous)
                    _record_pending_health(
                        root,
                        previous=previous,
                        payload_id=payload_id,
                        source_commit=source_commit,
                        transaction_id=transaction_id,
                    )
                    bootstrap_marker = _read_bootstrap_pending(root)
                    if (
                        isinstance(bootstrap_marker, dict)
                        and bootstrap_marker.get("source_commit") == source_commit
                        and bootstrap_marker.get("status") in {"pending", "restarting"}
                    ):
                        _write_bootstrap_pending(
                            root,
                            source_commit=source_commit,
                            workflow_run_id=int(bootstrap_marker["workflow_run_id"]),
                            status="restarting",
                            transaction_id=transaction_id,
                            payload_id=payload_id,
                            restart_authorized=True,
                        )
                    atomic_activate_pointer(root, version=payload_id, manifest_sha256=manifest_hash)
                    activated = True
                except (OSError, UnicodeError, json.JSONDecodeError, StableShellError, AppUpdateError) as exc:
                    switched = activated
                    if not switched:
                        try:
                            switched = load_current_pointer(root).get("version") == payload_id
                        except Exception:
                            switched = False
                    if switched:
                        try:
                            atomic_activate_pointer(root, version=str(previous["version"]), manifest_sha256=str(previous["manifest_sha256"]))
                        except Exception:
                            pass
                    _unlink_state(_pending_health_path(root))
                    _try_write_update_state(
                        root,
                        phase="error",
                        progress=0,
                        transaction_id=transaction_id,
                        current_payload_id=previous.get("version"),
                        candidate_payload_id=payload_id,
                        rollback_payload_id=previous.get("version"),
                        reason_code=reason_code_for(getattr(exc, "code", "UPDATE_COMMIT_FAILED")),
                        last_error_code=getattr(exc, "code", "UPDATE_COMMIT_FAILED"),
                    )
                    raise exc if isinstance(exc, AppUpdateError) else AppUpdateError("UPDATE_COMMIT_FAILED") from exc
                self._cached = None
                self._transaction_id = transaction_id
                self._remember_update_state(
                    root,
                    phase="restarting",
                    progress=85,
                    transaction_id=transaction_id,
                    requires_restart=True,
                    current_payload_id=payload_id,
                    candidate_payload_id=payload_id,
                    rollback_payload_id=previous.get("version"),
                )
                return {
                    "status": "activated",
                    "source_commit": source_commit,
                    "payload_id": payload_id,
                    "transaction_id": transaction_id,
                    "previous_payload": previous.get("version"),
                    "restart_required": True,
                    "launcher_changed": False,
                    "data_root_changed": False,
                    "update_kind": staged.get("update_kind"),
                    "commit_phase": "restart_pending",
                }

    def rollback_pending_restart(self, *, reason: str = "RESTART_TRANSACTION_FAILED") -> dict[str, Any]:
        """Restore the exact pre-commit pointer after a restart transaction veto."""

        with self._lock:
            root = self._install_root()
            with _update_serialization_lock(root):
                pending_path = _pending_health_path(root)
                if not pending_path.is_file() or pending_path.is_symlink():
                    return {"status": "not_pending"}
                pending = _safe_json_file(pending_path, max_bytes=64 * 1024)
                previous = pending.get("previous") if isinstance(pending, dict) else None
                if not isinstance(previous, dict):
                    raise AppUpdateError("UPDATE_PENDING_HEALTH_INVALID")
                transaction_id = pending.get("transaction_id") if isinstance(pending.get("transaction_id"), str) else self._transaction_id
                candidate_payload_id = pending.get("payload_id") if isinstance(pending.get("payload_id"), str) else None
                pointer = atomic_activate_pointer(root, version=str(previous["version"]), manifest_sha256=str(previous["manifest_sha256"]))
                _write_json_atomic(root / "update-state" / "last-rollback.json", {
                    "schema_version": PENDING_HEALTH_SCHEMA,
                    "status": "rollback",
                    "payload_id": pointer["version"],
                    "reason": str(reason)[:96],
                })
                _unlink_state(pending_path)
                _unlink_state(_staged_update_path(root))
                _unlink_state(_restart_session_path(root))
                self._cached = None
                self._transaction_id = transaction_id
                self._remember_update_state(
                    root,
                    phase="rolled_back",
                    progress=100,
                    transaction_id=transaction_id,
                    current_payload_id=pointer.get("version"),
                    candidate_payload_id=candidate_payload_id,
                    rollback_payload_id=pointer.get("version"),
                    reason_code=reason_code_for(reason),
                    last_error_code=str(reason)[:96] if re.fullmatch(r"[A-Z][A-Z0-9_]{2,95}", str(reason)[:96]) else "RESTART_TRANSACTION_FAILED",
                )
                return {"status": "rolled_back", "payload_id": pointer["version"], "reason": str(reason)[:96], "transaction_id": transaction_id}

    @staticmethod
    def _preserve_failed_staging(root: Path, work: Path, source_commit: str) -> None:
        """Keep failed candidate payload/logs available for forensic review."""

        if not work.exists():
            return
        destination = root / "staging" / "failures" / f"main-{source_commit[:12]}-{os.getpid()}"
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(work, destination)
        except OSError:
            # The original work tree is left in place rather than risking a
            # destructive cleanup after a failed activation proof.
            return

    def prepare(self) -> dict[str, Any]:
        """Download, validate and stage the latest main payload.

        ``prepare`` is deliberately a phase-1 operation.  It never writes
        ``current.json`` or ``pending-health.json``; the native restart bridge
        performs the phase-2 commit only after the close/ownership gate is
        ready.  This prevents a new pointer from being visible while the old
        desktop is still running.
        """
        with self._lock:
            root = self._install_root()
            with _update_serialization_lock(root):
                result = self._prepare_locked(root)
                return self._public_projection(result, root=root)

    def _prepare_locked(self, root: Path) -> dict[str, Any]:
        try:
            return self._prepare_locked_impl(root)
        except AppUpdateError as exc:
            current_payload_id: str | None = None
            candidate_payload_id: str | None = None
            transaction_id = self._transaction_id
            try:
                current_payload_id = self._current_build(root).get("payload_id")
            except (AppUpdateError, OSError, UnicodeError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
                pass
            try:
                staged = _read_staged_update(root)
            except (AppUpdateError, OSError, UnicodeError, UnicodeDecodeError, json.JSONDecodeError):
                staged = None
            try:
                previous_state = _read_update_state(root)
            except (AppUpdateError, OSError, UnicodeError, UnicodeDecodeError, json.JSONDecodeError):
                previous_state = None
            if isinstance(staged, dict):
                candidate_payload_id = staged.get("payload_id")
                transaction_id = transaction_id or staged.get("transaction_id")
            if isinstance(previous_state, dict):
                transaction_id = transaction_id or previous_state.get("transaction_id")
                candidate_payload_id = candidate_payload_id or previous_state.get("candidate_payload_id")
            phase = "blocked" if reason_code_for(exc.code) in {
                "authentication_required", "active_job_blocks_restart", "external_owner_detected",
                "port_ownership_unknown", "channel_update_blocked", "channel_relation_unavailable",
                "update_transaction_busy", "staged_update_invalid", "pointer_commit_failed",
            } else "error"
            self._remember_update_state(
                root,
                phase=phase,
                progress=0,
                transaction_id=transaction_id,
                # A failed download/preflight has no staged record and may be
                # retried.  The candidate id in the error projection is only
                # diagnostic; it does not mean a candidate was committed.
                can_prepare=phase == "error" and staged is None,
                can_restart=False,
                requires_restart=False,
                current_payload_id=current_payload_id if isinstance(current_payload_id, str) else None,
                candidate_payload_id=candidate_payload_id if isinstance(candidate_payload_id, str) else None,
                rollback_payload_id=self._verified_previous_payload_id(root),
                reason_code=reason_code_for(exc.code),
                last_error_code=exc.code,
            )
            raise

    def _prepare_locked_impl(
        self,
        root: Path,
        *,
        candidate_override: UpdateCandidate | None = None,
    ) -> dict[str, Any]:
        """Implementation of :meth:`prepare` under the cross-process lock."""

        self._remember_update_state(root, phase="checking", progress=0)
        _transport, auth = self._selected_transport()
        if auth.status != "ready":
            raise AppUpdateError(auth.code or "GITHUB_AUTH_REQUIRED")
        shell_migration_required = launcher_projection(root).get("migration_required") is True
        pending_bootstrap = _read_bootstrap_pending(root)
        if candidate_override is not None:
            candidate = candidate_override
        elif isinstance(pending_bootstrap, dict) and pending_bootstrap.get("status") == "pending":
            candidate = self._candidate_for_exact_run(
                source_commit=str(pending_bootstrap["source_commit"]),
                workflow_run_id=int(pending_bootstrap["workflow_run_id"]),
            )
            if candidate is None:
                raise AppUpdateError("UPDATE_COMPOSITE_ARTIFACT_REQUIRED")
        else:
            candidate = self._latest_candidate(require_composite=shell_migration_required)
        if candidate is None:
            raise AppUpdateError("UPDATE_ARTIFACT_NOT_FOUND")
        current = self._current_build(root)
        if current["commit"] == candidate.source_commit and not shell_migration_required:
            self._remember_update_state(
                root,
                phase="succeeded",
                progress=100,
                current_payload_id=current.get("payload_id"),
                candidate_payload_id=f"main-{candidate.source_commit[:12]}",
            )
            return {"status": "up_to_date", "restart_required": False, "source_commit": candidate.source_commit}
        self._transaction_id = _new_transaction_id()
        candidate_payload_id = f"main-{candidate.source_commit[:12]}"
        self._remember_update_state(
            root,
            phase="preparing",
            progress=5,
            transaction_id=self._transaction_id,
            current_payload_id=current.get("payload_id"),
            candidate_payload_id=candidate_payload_id,
            rollback_payload_id=self._verified_previous_payload_id(root),
            requires_restart=True,
        )
        relation = self._channel_relation(current["commit"], candidate.source_commit)
        if relation == "blocked_current_ahead_of_main":
            raise AppUpdateError("UPDATE_CURRENT_AHEAD_OF_MAIN")
        if relation == "blocked_channel_diverged":
            raise AppUpdateError("UPDATE_CHANNEL_DIVERGED")
        if relation == "channel_relation_unavailable" and current["commit"] not in {"legacy", "unknown"}:
            raise AppUpdateError("UPDATE_CHANNEL_RELATION_UNAVAILABLE")
        existing_staged = _read_staged_update(root)
        bootstrap_previous: dict[str, Any] | None = None
        if existing_staged is not None:
            existing_kind = existing_staged.get("update_kind")
            bootstrap_upgrade = (
                existing_staged.get("source_commit") == candidate.source_commit
                and candidate.is_composite
                and existing_kind == UPDATE_KIND_APP_ONLY
            )
            if bootstrap_upgrade:
                # The old client has already moved current.json to this app
                # payload. Preserve its original pointer as the rollback
                # target while replacing only the staged contract with the
                # same-run composite shell payload below.
                previous = existing_staged.get("previous")
                if (
                    not isinstance(previous, dict)
                    or set(previous) != {"schema_version", "version", "payload_relative", "manifest_sha256"}
                    or previous.get("schema_version") != POINTER_SCHEMA
                ):
                    raise AppUpdateError("STAGED_UPDATE_INVALID")
                bootstrap_previous = dict(previous)
                existing_staged = None
            elif existing_staged.get("source_commit") == candidate.source_commit:
                transaction_id = existing_staged.get("transaction_id")
                if not isinstance(transaction_id, str) or _TRANSACTION_RE.fullmatch(transaction_id) is None:
                    transaction_id = self._transaction_id or _new_transaction_id()
                    self._transaction_id = transaction_id
                    try:
                        _write_json_atomic(_staged_update_path(root), {**existing_staged, "transaction_id": transaction_id})
                    except (AppUpdateError, OSError, UnicodeError, ValueError):
                        pass
                else:
                    self._transaction_id = transaction_id
                previous = existing_staged.get("previous")
                rollback_payload_id = previous.get("version") if isinstance(previous, dict) else None
                self._remember_update_state(
                    root,
                    phase="staged",
                    progress=100,
                    transaction_id=transaction_id,
                    can_restart=True,
                    requires_restart=True,
                    current_payload_id=current.get("payload_id"),
                    candidate_payload_id=existing_staged.get("payload_id"),
                    rollback_payload_id=rollback_payload_id,
                )
                if existing_kind == UPDATE_KIND_APP_ONLY and shell_migration_required:
                    _write_bootstrap_pending(
                        root,
                        source_commit=candidate.source_commit,
                        workflow_run_id=candidate.run_id,
                        status="pending",
                        transaction_id=transaction_id,
                        payload_id=existing_staged.get("payload_id") if isinstance(existing_staged.get("payload_id"), str) else None,
                        restart_authorized=False,
                    )
                return {
                    "status": "staged",
                    "source_commit": candidate.source_commit,
                    "payload_id": existing_staged.get("payload_id"),
                    "transaction_id": transaction_id,
                    "previous_payload": rollback_payload_id,
                    "restart_required": True,
                    "staged": True,
                    "bootstrap_pending": existing_kind == UPDATE_KIND_APP_ONLY and shell_migration_required,
                    "update_kind": existing_kind,
                    "commit_phase": "awaiting_restart",
                }
            else:
                raise AppUpdateError("UPDATE_STAGED_UPDATE_PENDING")
        plan = resolve_launch_plan(root, allow_test_root=self._allow_test_root)
        staging_root = root / "staging"
        staging_root.mkdir(parents=True, exist_ok=True)
        work = staging_root / f"main-update-{candidate.source_commit[:12]}-{os.getpid()}"
        download = work / "download"
        stage_payload = work / "payload"
        target = root / "versions" / f"main-{candidate.source_commit[:12]}"
        try:
            work.mkdir(parents=False, exist_ok=False)
            self._remember_update_state(
                root,
                phase="preparing",
                progress=15,
                transaction_id=self._transaction_id,
                current_payload_id=current.get("payload_id"),
                candidate_payload_id=candidate_payload_id,
                rollback_payload_id=self._verified_previous_payload_id(root),
                requires_restart=True,
            )
            self._download_candidate(candidate, download)
            self._remember_update_state(
                root,
                phase="preparing",
                progress=35,
                transaction_id=self._transaction_id,
                current_payload_id=current.get("payload_id"),
                candidate_payload_id=candidate_payload_id,
                rollback_payload_id=self._verified_previous_payload_id(root),
                requires_restart=True,
            )
            manifest, contract, archive = self._validate_download(download, candidate)
            update_kind = str(contract["update_kind"])
            bootstrap_candidate = update_kind == UPDATE_KIND_APP_ONLY and shell_migration_required
            stage_payload.mkdir(parents=False, exist_ok=False)
            _safe_extract_app_archive(archive, stage_payload, expected_files=int(manifest["file_count"]), update_kind=update_kind)
            launcher_manifest: dict[str, Any] | None = None
            if update_kind == UPDATE_KIND_APP_AND_LAUNCHER:
                try:
                    launcher_manifest = launcher_tree_manifest(stage_payload / "launcher" / "LocalAIHub")
                except LauncherMigrationError as exc:
                    raise AppUpdateError("UPDATE_LAUNCHER_MANIFEST_MISMATCH") from exc
                if (
                    launcher_manifest.get("format") != contract.get("launcher_format")
                    or launcher_manifest.get("executable_sha256") != contract.get("launcher_executable_sha256")
                    or launcher_manifest.get("tree_manifest_sha256") != contract.get("launcher_tree_manifest_sha256")
                    or launcher_manifest.get("file_count") != contract.get("launcher_file_count")
                    or launcher_manifest.get("total_bytes") != contract.get("launcher_total_bytes")
                ):
                    raise AppUpdateError("UPDATE_LAUNCHER_MANIFEST_MISMATCH")
            runtime_source = plan.payload_root / "runtime"
            if update_kind in {UPDATE_KIND_APP_ONLY, UPDATE_KIND_APP_AND_LAUNCHER}:
                if not runtime_source.is_dir() or runtime_source.is_symlink():
                    raise AppUpdateError("CURRENT_RUNTIME_UNAVAILABLE")
                shutil.copytree(runtime_source, stage_payload / "runtime", symlinks=False)
            elif not (stage_payload / "runtime" / "Python312" / "pythonw.exe").is_file():
                raise AppUpdateError("FULL_RUNTIME_PAYLOAD_MISSING")
            if update_kind == UPDATE_KIND_FULL and _runtime_inventory_hash(stage_payload / "runtime") != contract["runtime_hash"]:
                raise AppUpdateError("UPDATE_RUNTIME_HASH_MISMATCH")
            version = str(manifest["payload_id"])
            version_manifest = {
                "schema_version": VERSION_MANIFEST_SCHEMA,
                "product_id": PRODUCT_ID,
                "version": version,
                "app_relative": "app",
                "runtime_relative": "runtime/Python312/pythonw.exe",
                "entrypoint": "src.app.launcher",
            }
            manifest_bytes = _canonical_json(version_manifest)
            (stage_payload / "manifest.json").write_bytes(manifest_bytes)
            build_info = {
                "schema_version": BUILD_INFO_SCHEMA,
                "source_commit": candidate.source_commit,
                "workflow_run_id": candidate.run_id,
                "channel": "main",
                "product_version": PRODUCT_VERSION,
            }
            (stage_payload / "build.json").write_bytes(_canonical_json(build_info))
            runtime_pythonw = stage_payload / "runtime" / "Python312" / "pythonw.exe"
            if not runtime_pythonw.is_file():
                raise AppUpdateError("STAGED_RUNTIME_UNAVAILABLE")
            self._validate_staged_imports(stage_payload / "app", runtime_pythonw)
            self._remember_update_state(
                root,
                phase="preparing",
                progress=70,
                transaction_id=self._transaction_id,
                current_payload_id=current.get("payload_id"),
                candidate_payload_id=candidate_payload_id,
                rollback_payload_id=self._verified_previous_payload_id(root),
                requires_restart=True,
            )
            self._preflight_candidate_api(
                install_root=root,
                app_root=stage_payload / "app",
                runtime_pythonw=runtime_pythonw,
                payload_id=version,
                source_commit=candidate.source_commit,
                work_root=work,
            )
            manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
            if target.exists():
                existing = target / "build.json"
                if not existing.is_file() or _safe_json_file(existing, max_bytes=32 * 1024).get("source_commit") != candidate.source_commit:
                    raise AppUpdateError("UPDATE_TARGET_CONFLICT")
                if update_kind == UPDATE_KIND_APP_AND_LAUNCHER:
                    target_launcher = target / "launcher" / "LocalAIHub"
                    if target_launcher.exists():
                        try:
                            verify_launcher = launcher_tree_manifest(target_launcher)
                        except LauncherMigrationError as exc:
                            raise AppUpdateError("UPDATE_TARGET_CONFLICT") from exc
                        if verify_launcher != launcher_manifest:
                            raise AppUpdateError("UPDATE_TARGET_CONFLICT")
                        shutil.rmtree(stage_payload)
                    else:
                        # A legacy APP_ONLY bootstrap already created the
                        # exact app/runtime payload at this version.  Add the
                        # independently verified onedir shell without
                        # replacing user data or weakening the source binding.
                        shutil.copytree(stage_payload / "launcher", target_launcher.parent, symlinks=False)
                        if launcher_manifest is None or launcher_tree_manifest(target_launcher) != launcher_manifest:
                            raise AppUpdateError("UPDATE_TARGET_CONFLICT")
                        shutil.rmtree(stage_payload)
                else:
                    shutil.rmtree(stage_payload)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(stage_payload, target)
            previous = bootstrap_previous or load_current_pointer(root)
            history = root / "update-state"
            history.mkdir(parents=True, exist_ok=True)
            _write_json_atomic(history / "staged-update.json", {
                "schema_version": STAGED_UPDATE_SCHEMA,
                "payload_id": version,
                "source_commit": candidate.source_commit,
                "transaction_id": self._transaction_id,
                "payload_relative": f"versions/{version}",
                "manifest_sha256": manifest_hash,
                "previous": previous,
                "staged_at": _utc_now(),
                "update_kind": update_kind,
            })
            if bootstrap_candidate:
                _write_bootstrap_pending(
                    root,
                    source_commit=candidate.source_commit,
                    workflow_run_id=candidate.run_id,
                    status="pending",
                    transaction_id=self._transaction_id,
                    payload_id=version,
                    restart_authorized=False,
                )
            if update_kind == UPDATE_KIND_APP_AND_LAUNCHER and launcher_manifest is not None:
                _write_json_atomic(_staged_update_path(root), {
                    "schema_version": STAGED_UPDATE_SCHEMA,
                    "payload_id": version,
                    "source_commit": candidate.source_commit,
                    "transaction_id": self._transaction_id,
                    "payload_relative": f"versions/{version}",
                    "manifest_sha256": manifest_hash,
                    "previous": previous,
                    "staged_at": _utc_now(),
                    "update_kind": update_kind,
                    "workflow_run_id": contract.get("workflow_run_id"),
                    "launcher_format": launcher_manifest.get("format"),
                    "launcher_executable_sha256": launcher_manifest.get("executable_sha256"),
                    "launcher_tree_manifest_sha256": launcher_manifest.get("tree_manifest_sha256"),
                    "launcher_file_count": launcher_manifest.get("file_count"),
                    "launcher_total_bytes": launcher_manifest.get("total_bytes"),
                })
            self._remember_update_state(
                root,
                phase="staged",
                progress=100,
                transaction_id=self._transaction_id,
                can_restart=True,
                requires_restart=True,
                current_payload_id=previous.get("version"),
                candidate_payload_id=version,
                rollback_payload_id=previous.get("version"),
            )
            self._cached = None
            return {
                "status": "staged", "source_commit": candidate.source_commit,
                "payload_id": version, "previous_payload": previous.get("version"),
                "transaction_id": self._transaction_id,
                "restart_required": True, "staged": True, "launcher_changed": update_kind == UPDATE_KIND_APP_AND_LAUNCHER, "data_root_changed": False,
                "update_kind": update_kind, "runtime_contract": contract["runtime_contract"],
                "bootstrap_pending": bootstrap_candidate,
                "commit_phase": "awaiting_restart",
            }
        except AppUpdateError:
            self._preserve_failed_staging(root, work, candidate.source_commit)
            raise
        except (OSError, UnicodeError, json.JSONDecodeError, zipfile.BadZipFile, StableShellError) as exc:
            self._preserve_failed_staging(root, work, candidate.source_commit)
            raise AppUpdateError("UPDATE_PREPARE_FAILED", type(exc).__name__) from exc
        finally:
            try:
                if work.exists():
                    shutil.rmtree(work)
            except OSError:
                pass

    def rollback(self) -> dict[str, Any]:
        with self._lock:
            root = self._install_root()
            with _update_serialization_lock(root):
                path = root / "update-state" / "previous-current.json"
                if not path.is_file() or path.is_symlink():
                    raise AppUpdateError("ROLLBACK_POINTER_UNAVAILABLE")
                previous = _safe_json_file(path, max_bytes=32 * 1024)
                if set(previous) != {"schema_version", "version", "payload_relative", "manifest_sha256"} or previous.get("schema_version") != POINTER_SCHEMA:
                    raise AppUpdateError("ROLLBACK_POINTER_INVALID")
                pointer = atomic_activate_pointer(root, version=str(previous["version"]), manifest_sha256=str(previous["manifest_sha256"]))
                _unlink_state(_pending_health_path(root))
                _unlink_state(_staged_update_path(root))
                _unlink_state(_restart_session_path(root))
                self._cached = None
                self._remember_update_state(
                    root,
                    phase="rolled_back",
                    progress=100,
                    transaction_id=self._transaction_id,
                    current_payload_id=pointer.get("version"),
                    rollback_payload_id=pointer.get("version"),
                    reason_code=None,
                )
                return self._public_projection(
                    {
                        "status": "rolled_back",
                        "payload_id": pointer["version"],
                        "restart_required": True,
                        "transaction_id": self._transaction_id,
                    },
                    root=root,
                )


_SERVICE: AppUpdateService | None = None
_SERVICE_LOCK = threading.Lock()


def app_update_service() -> AppUpdateService:
    global _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is None:
            _SERVICE = AppUpdateService()
        return _SERVICE


__all__ = [
    "AppUpdateError", "AppUpdateService", "BUILD_INFO_SCHEMA", "REPOSITORY", "UPDATE_ARTIFACT_NAME", "LEGACY_UPDATE_ARTIFACT_NAME", "COMPOSITE_UPDATE_ARTIFACT_NAME",
    "UPDATE_CONTRACT_NAME", "UPDATE_CONTRACT_SCHEMA", "COMPOSITE_UPDATE_SCHEMA", "PRODUCT_UPDATE_CONTRACT_SCHEMA",
    "UPDATE_KIND_APP_ONLY", "UPDATE_KIND_FULL", "UPDATE_KIND_APP_AND_LAUNCHER",
    "PENDING_HEALTH_SCHEMA", "STAGED_UPDATE_SCHEMA", "RESTART_SESSION_SCHEMA", "RESTART_TRANSACTION_SCHEMA", "BOOTSTRAP_PENDING_SCHEMA", "BOOTSTRAP_PENDING_FILE", "UPDATE_STATE_SCHEMA", "UPDATE_STATE_FILE", "UPDATE_PHASES", "STABLE_UPDATE_REASON_CODES", "CANDIDATE_API_PREFLIGHT_TIMEOUT_SECONDS", "CANDIDATE_BOOTSTRAP_PREFLIGHT_TIMEOUT_SECONDS", "UPDATE_SCHEMA", "app_update_service", "mark_startup_health", "reason_code_for", "update_error_projection", "_runtime_inventory_hash", "_safe_extract_app_archive", "_safe_update_contract", "_safe_update_manifest", "_read_staged_update", "_read_bootstrap_pending", "_write_bootstrap_pending", "_read_update_state", "_try_write_update_state", "_write_restart_session",
]
