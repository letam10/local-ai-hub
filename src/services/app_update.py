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
from src.shared.runtime_identity import API_PROTOCOL_VERSION, api_identity
from src.shared.version import PRODUCT_VERSION
from src.services.process_manager.managed import terminate_owned_process
from src.services.update_transport import AuthState, TransportError, TransportSelector, build_transport

REPOSITORY = "letam10/local-ai-hub"
WORKFLOW_FILE = "ci.yml"
UPDATE_ARTIFACT_NAME = "local-ai-hub-main-update"
UPDATE_SCHEMA = "local-ai-hub-main-update.v1"
UPDATE_CONTRACT_SCHEMA = "local-ai-hub-update-contract.v1"
UPDATE_CONTRACT_NAME = "update-contract.json"
BUILD_INFO_SCHEMA = "local-ai-hub-build-info.v1"
PENDING_HEALTH_SCHEMA = "local-ai-hub-pending-health.v1"
STAGED_UPDATE_SCHEMA = "local-ai-hub-staged-update.v1"
RESTART_SESSION_SCHEMA = "local-ai-hub-restart-session.v1"
RUNTIME_STRATEGY = "reuse-current"
RUNTIME_STRATEGY_BUNDLED = "bundled"
UPDATE_KIND_APP_ONLY = "APP_ONLY"
UPDATE_KIND_FULL = "FULL"
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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _staged_update_path(root: Path) -> Path:
    return root / "update-state" / "staged-update.json"


def _restart_session_path(root: Path) -> Path:
    return root / "update-state" / "restart-session.json"


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


def _record_pending_health(root: Path, *, previous: dict[str, Any], payload_id: str, source_commit: str) -> None:
    _write_json_atomic(_pending_health_path(root), {
        "schema_version": PENDING_HEALTH_SCHEMA,
        "payload_id": payload_id,
        "source_commit": source_commit,
        "previous": previous,
    })


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
    if set(value) != expected or value.get("schema_version") != STAGED_UPDATE_SCHEMA:
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
        or value.get("update_kind") not in {UPDATE_KIND_APP_ONLY, UPDATE_KIND_FULL}
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
    if healthy:
        try:
            pending_path.unlink()
        except OSError as exc:
            raise AppUpdateError("UPDATE_STATE_CLEAR_FAILED") from exc
        _unlink_state(_staged_update_path(root))
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
    return {"status": "rollback", "payload_id": pointer["version"], "code": "POST_RESTART_HEALTH_FAILED"}


def _safe_update_manifest(value: object, *, expected_commit: str | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AppUpdateError("UPDATE_MANIFEST_INVALID")
    required = {
        "schema_version", "product_id", "product_version", "channel", "source_commit",
        "payload_id", "runtime_strategy", "archive", "archive_sha256", "file_count",
    }
    if set(value) != required:
        raise AppUpdateError("UPDATE_MANIFEST_INVALID")
    source_commit = str(value.get("source_commit") or "")
    payload_id = str(value.get("payload_id") or "")
    digest = str(value.get("archive_sha256") or "")
    archive = str(value.get("archive") or "")
    count = value.get("file_count")
    if value.get("schema_version") != UPDATE_SCHEMA or value.get("product_id") != PRODUCT_ID:
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
    return dict(value)


def _safe_update_contract(value: object, *, expected_commit: str | None = None) -> dict[str, Any]:
    """Validate the optional contract without weakening legacy manifests."""

    if not isinstance(value, dict):
        raise AppUpdateError("UPDATE_CONTRACT_INVALID")
    required = {
        "schema_version", "update_kind", "app_protocol", "runtime_contract",
        "runtime_version", "runtime_hash", "minimum_launcher_version", "source_commit",
    }
    if set(value) != required or value.get("schema_version") != UPDATE_CONTRACT_SCHEMA:
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
            allowed_prefix = name.startswith("app/") or (update_kind == UPDATE_KIND_FULL and name.startswith("runtime/"))
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

    def _latest_candidate(self) -> UpdateCandidate | None:
        runs = self._api_json(
            f"repos/{REPOSITORY}/actions/workflows/{WORKFLOW_FILE}/runs",
            {"branch": "main", "event": "push", "status": "success", "per_page": "10"},
        ).get("workflow_runs")
        if not isinstance(runs, list):
            raise AppUpdateError("GITHUB_RUNS_INVALID")
        for row in runs:
            if not isinstance(row, dict) or row.get("conclusion") != "success" or row.get("head_branch") != "main":
                continue
            sha = str(row.get("head_sha") or "")
            run_id = row.get("id")
            if not _SHA_RE.fullmatch(sha) or isinstance(run_id, bool) or not isinstance(run_id, int):
                continue
            artifacts = self._api_json(f"repos/{REPOSITORY}/actions/runs/{run_id}/artifacts", {"per_page": "100"}).get("artifacts")
            if not isinstance(artifacts, list):
                continue
            for artifact in artifacts:
                if not isinstance(artifact, dict) or artifact.get("name") != UPDATE_ARTIFACT_NAME or artifact.get("expired") is True:
                    continue
                artifact_id = artifact.get("id")
                if isinstance(artifact_id, int) and not isinstance(artifact_id, bool):
                    return UpdateCandidate(run_id, sha, artifact_id, UPDATE_ARTIFACT_NAME)
        return None

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

    def status(self, *, refresh: bool = False) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            self._retry_attempt = None
            if not refresh and self._cached and now - self._cached[0] < CACHE_SECONDS:
                return dict(self._cached[1])
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
                    candidate = self._latest_candidate()
                    if candidate is None:
                        result = {
                            "status": "no_artifact", "available": False, "product_version": PRODUCT_VERSION,
                            "current_build": current["commit"], "current_payload": current["payload_id"],
                            "latest_build": None, "transport": "github_cli", "action": "Chờ main CI tạo update artifact thành công.",
                        }
                    else:
                        try:
                            staged = _read_staged_update(root)
                        except (OSError, UnicodeError, json.JSONDecodeError, AppUpdateError):
                            staged = None
                        if staged is not None and staged.get("source_commit") == candidate.source_commit:
                            result = {
                                "status": "staged", "available": False, "product_version": PRODUCT_VERSION,
                                "current_build": current["commit"], "current_payload": current["payload_id"],
                                "latest_build": candidate.source_commit, "latest_payload": staged.get("payload_id"),
                                "staged_payload": staged.get("payload_id"), "restart_required": True,
                                "run_id": candidate.run_id, "artifact_id": candidate.artifact_id, "transport": auth.transport,
                                "action": "Candidate đã stage; khởi động lại để commit pointer an toàn.",
                            }
                            self._cached = (now, dict(result))
                            return result
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
                            available = relation == "forward_update_available" or (relation == "legacy_or_unbound" and current["commit"] != candidate.source_commit)
                            result = {
                            "status": "available" if available else "up_to_date", "available": available,
                            "product_version": PRODUCT_VERSION, "current_build": current["commit"],
                            "current_payload": current["payload_id"], "latest_build": candidate.source_commit,
                            "latest_payload": f"main-{candidate.source_commit[:12]}", "run_id": candidate.run_id,
                            "artifact_id": candidate.artifact_id, "transport": auth.transport,
                            "action": "Cập nhật Local AI Hub" if available else "Bạn đang dùng build main mới nhất.",
                            }
            except AppUpdateError as exc:
                result = {
                    "status": "unavailable", "available": False, "product_version": PRODUCT_VERSION,
                    "current_build": None, "latest_build": None, "transport": "updater", "code": exc.code,
                    "action": "Cài/đăng nhập GitHub CLI hoặc mở Diagnostics để kiểm tra updater.",
                }
            self._cached = (now, dict(result))
            if self._retry_attempt is not None:
                result["retry_attempt"] = self._retry_attempt
                result["retry_attempts"] = self._retry_attempt
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
            self._transport().download_artifact(candidate.run_id, destination, UPDATE_ARTIFACT_NAME)
        except TransportError as exc:
            raise AppUpdateError(exc.code) from exc

    def _validate_download(self, folder: Path, candidate: UpdateCandidate) -> tuple[dict[str, Any], dict[str, Any], Path]:
        manifest = _safe_update_manifest(_safe_json_file(folder / "update-manifest.json"), expected_commit=candidate.source_commit)
        contract_path = folder / UPDATE_CONTRACT_NAME
        if contract_path.is_file() and not contract_path.is_symlink():
            contract = _safe_update_contract(_safe_json_file(contract_path), expected_commit=candidate.source_commit)
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
                        [str(runtime_pythonw), "-m", "src.services.api.api_server"],
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
        _write_restart_session(root, value)
        return {key: value[key] for key in ("schema_version", "payload_id", "source_commit", "nonce", "parent_pid", "status")}

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
                activated = False
                try:
                    history = root / "update-state"
                    history.mkdir(parents=True, exist_ok=True)
                    _write_json_atomic(history / "previous-current.json", previous)
                    _record_pending_health(root, previous=previous, payload_id=payload_id, source_commit=source_commit)
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
                    raise exc if isinstance(exc, AppUpdateError) else AppUpdateError("UPDATE_COMMIT_FAILED") from exc
                self._cached = None
                return {
                    "status": "activated",
                    "source_commit": source_commit,
                    "payload_id": payload_id,
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
                return {"status": "rolled_back", "payload_id": pointer["version"], "reason": str(reason)[:96]}

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
                return self._prepare_locked(root)

    def _prepare_locked(self, root: Path) -> dict[str, Any]:
        """Implementation of :meth:`prepare` under the cross-process lock."""

        _transport, auth = self._selected_transport()
        if auth.status != "ready":
            raise AppUpdateError(auth.code or "GITHUB_AUTH_REQUIRED")
        candidate = self._latest_candidate()
        if candidate is None:
            raise AppUpdateError("UPDATE_ARTIFACT_NOT_FOUND")
        current = self._current_build(root)
        if current["commit"] == candidate.source_commit:
            return {"status": "up_to_date", "restart_required": False, "source_commit": candidate.source_commit}
        relation = self._channel_relation(current["commit"], candidate.source_commit)
        if relation == "blocked_current_ahead_of_main":
            raise AppUpdateError("UPDATE_CURRENT_AHEAD_OF_MAIN")
        if relation == "blocked_channel_diverged":
            raise AppUpdateError("UPDATE_CHANNEL_DIVERGED")
        if relation == "channel_relation_unavailable" and current["commit"] not in {"legacy", "unknown"}:
            raise AppUpdateError("UPDATE_CHANNEL_RELATION_UNAVAILABLE")
        existing_staged = _read_staged_update(root)
        if existing_staged is not None:
            if existing_staged.get("source_commit") == candidate.source_commit:
                return {
                    "status": "staged",
                    "source_commit": candidate.source_commit,
                    "payload_id": existing_staged.get("payload_id"),
                    "restart_required": True,
                    "staged": True,
                    "commit_phase": "awaiting_restart",
                }
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
            self._download_candidate(candidate, download)
            manifest, contract, archive = self._validate_download(download, candidate)
            update_kind = str(contract["update_kind"])
            stage_payload.mkdir(parents=False, exist_ok=False)
            _safe_extract_app_archive(archive, stage_payload, expected_files=int(manifest["file_count"]), update_kind=update_kind)
            runtime_source = plan.payload_root / "runtime"
            if update_kind == UPDATE_KIND_APP_ONLY:
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
                shutil.rmtree(stage_payload)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(stage_payload, target)
            previous = load_current_pointer(root)
            history = root / "update-state"
            history.mkdir(parents=True, exist_ok=True)
            _write_json_atomic(history / "staged-update.json", {
                "schema_version": STAGED_UPDATE_SCHEMA,
                "payload_id": version,
                "source_commit": candidate.source_commit,
                "payload_relative": f"versions/{version}",
                "manifest_sha256": manifest_hash,
                "previous": previous,
                "staged_at": _utc_now(),
                "update_kind": update_kind,
            })
            self._cached = None
            return {
                "status": "staged", "source_commit": candidate.source_commit,
                "payload_id": version, "previous_payload": previous.get("version"),
                "restart_required": True, "staged": True, "launcher_changed": False, "data_root_changed": False,
                "update_kind": update_kind, "runtime_contract": contract["runtime_contract"],
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
                return {"status": "rolled_back", "payload_id": pointer["version"], "restart_required": True}


_SERVICE: AppUpdateService | None = None
_SERVICE_LOCK = threading.Lock()


def app_update_service() -> AppUpdateService:
    global _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is None:
            _SERVICE = AppUpdateService()
        return _SERVICE


__all__ = [
    "AppUpdateError", "AppUpdateService", "BUILD_INFO_SCHEMA", "REPOSITORY", "UPDATE_ARTIFACT_NAME",
    "UPDATE_CONTRACT_NAME", "UPDATE_CONTRACT_SCHEMA", "UPDATE_KIND_APP_ONLY", "UPDATE_KIND_FULL",
    "PENDING_HEALTH_SCHEMA", "STAGED_UPDATE_SCHEMA", "RESTART_SESSION_SCHEMA", "CANDIDATE_API_PREFLIGHT_TIMEOUT_SECONDS", "CANDIDATE_BOOTSTRAP_PREFLIGHT_TIMEOUT_SECONDS", "UPDATE_SCHEMA", "app_update_service", "mark_startup_health", "_runtime_inventory_hash", "_safe_extract_app_archive", "_safe_update_contract", "_safe_update_manifest", "_read_staged_update", "_write_restart_session",
]
