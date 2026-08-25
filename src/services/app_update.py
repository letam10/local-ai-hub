"""Installed Local AI Hub updater for successful ``main`` build artifacts.

The updater never performs ``git pull`` and never executes a repository checkout.
It consumes a bounded GitHub Actions artifact through an already-authenticated
GitHub CLI, verifies the artifact manifest/hash, reuses the currently reviewed
bundled runtime, stages a side-by-side payload, and atomically switches the
stable shell pointer.  Models, Environments and DATA_ROOT are out of scope.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
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
from src.shared.runtime_identity import API_PROTOCOL_VERSION
from src.shared.version import PRODUCT_VERSION
from src.services.update_transport import AuthState, TransportError, TransportSelector, build_transport

REPOSITORY = "letam10/local-ai-hub"
WORKFLOW_FILE = "ci.yml"
UPDATE_ARTIFACT_NAME = "local-ai-hub-main-update"
UPDATE_SCHEMA = "local-ai-hub-main-update.v1"
UPDATE_CONTRACT_SCHEMA = "local-ai-hub-update-contract.v1"
UPDATE_CONTRACT_NAME = "update-contract.json"
BUILD_INFO_SCHEMA = "local-ai-hub-build-info.v1"
PENDING_HEALTH_SCHEMA = "local-ai-hub-pending-health.v1"
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
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PAYLOAD_RE = re.compile(r"^main-[0-9a-f]{12}$")
_RUNTIME_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


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


def mark_startup_health(app_root: Path, *, health: dict[str, Any] | None = None) -> dict[str, Any]:
    """Commit or fail-closed a pending update after the new payload starts."""

    root = app_root.absolute()
    pending_path = _pending_health_path(root)
    if not pending_path.is_file() or pending_path.is_symlink():
        return {"status": "not_pending"}
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
    )
    if healthy:
        try:
            pending_path.unlink()
        except OSError as exc:
            raise AppUpdateError("UPDATE_STATE_CLEAR_FAILED") from exc
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

    def _selected_transport(self) -> tuple[Any, AuthState]:
        if self._transport_override is not None:
            state = self._transport_override.auth_state()
            return self._transport_override, state
        if self._transport_selector is None:
            self._transport_selector = build_transport(runner=self._runner, gh_path=self._gh_path)
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

    def status(self, *, refresh: bool = False) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
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
                        available = current["commit"] != candidate.source_commit
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

    def prepare(self) -> dict[str, Any]:
        """Download, validate, stage and atomically activate the latest main payload."""
        with self._lock:
            root = self._install_root()
            _transport, auth = self._selected_transport()
            if auth.status != "ready":
                raise AppUpdateError(auth.code or "GITHUB_AUTH_REQUIRED")
            candidate = self._latest_candidate()
            if candidate is None:
                raise AppUpdateError("UPDATE_ARTIFACT_NOT_FOUND")
            current = self._current_build(root)
            if current["commit"] == candidate.source_commit:
                return {"status": "up_to_date", "restart_required": False, "source_commit": candidate.source_commit}
            plan = resolve_launch_plan(root)
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
                _write_json_atomic(history / "previous-current.json", previous)
                _record_pending_health(root, previous=previous, payload_id=version, source_commit=candidate.source_commit)
                atomic_activate_pointer(root, version=version, manifest_sha256=manifest_hash)
                self._cached = None
                return {
                    "status": "activated", "source_commit": candidate.source_commit,
                    "payload_id": version, "previous_payload": previous.get("version"),
                    "restart_required": True, "launcher_changed": False, "data_root_changed": False,
                    "update_kind": update_kind, "runtime_contract": contract["runtime_contract"],
                }
            except (OSError, UnicodeError, json.JSONDecodeError, zipfile.BadZipFile, StableShellError) as exc:
                if isinstance(exc, AppUpdateError):
                    raise
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
            path = root / "update-state" / "previous-current.json"
            if not path.is_file() or path.is_symlink():
                raise AppUpdateError("ROLLBACK_POINTER_UNAVAILABLE")
            previous = _safe_json_file(path, max_bytes=32 * 1024)
            if set(previous) != {"schema_version", "version", "payload_relative", "manifest_sha256"} or previous.get("schema_version") != POINTER_SCHEMA:
                raise AppUpdateError("ROLLBACK_POINTER_INVALID")
            pointer = atomic_activate_pointer(root, version=str(previous["version"]), manifest_sha256=str(previous["manifest_sha256"]))
            try:
                _pending_health_path(root).unlink()
            except OSError:
                pass
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
    "PENDING_HEALTH_SCHEMA", "UPDATE_SCHEMA", "app_update_service", "mark_startup_health", "_runtime_inventory_hash", "_safe_extract_app_archive", "_safe_update_contract", "_safe_update_manifest",
]
