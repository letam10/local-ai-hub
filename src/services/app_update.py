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
from src.shared.version import PRODUCT_VERSION

REPOSITORY = "letam10/local-ai-hub"
WORKFLOW_FILE = "ci.yml"
UPDATE_ARTIFACT_NAME = "local-ai-hub-main-update"
UPDATE_SCHEMA = "local-ai-hub-main-update.v1"
BUILD_INFO_SCHEMA = "local-ai-hub-build-info.v1"
RUNTIME_STRATEGY = "reuse-current"
MAX_GH_JSON_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_EXTRACTED_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_FILES = 12_000
CACHE_SECONDS = 120.0
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PAYLOAD_RE = re.compile(r"^main-[0-9a-f]{12}$")


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
    if value.get("runtime_strategy") != RUNTIME_STRATEGY:
        raise AppUpdateError("UPDATE_RUNTIME_STRATEGY_UNSUPPORTED")
    if archive != "LocalAIHub-main-update.zip" or not _SHA256_RE.fullmatch(digest):
        raise AppUpdateError("UPDATE_ARCHIVE_IDENTITY_INVALID")
    if isinstance(count, bool) or not isinstance(count, int) or count < 1 or count > MAX_ARCHIVE_FILES:
        raise AppUpdateError("UPDATE_FILE_COUNT_INVALID")
    return dict(value)


def _zip_member_is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_ISLNK(mode)


def _safe_extract_app_archive(archive: Path, destination: Path, *, expected_files: int) -> int:
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
            if (
                not name.startswith("app/")
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

    def __init__(self, *, runner: Runner | None = None, gh_path: str | None = None) -> None:
        self._runner = runner or subprocess.run
        self._gh_path = gh_path
        self._lock = threading.RLock()
        self._cached: tuple[float, dict[str, Any]] | None = None

    def _install_root(self) -> Path:
        raw = os.environ.get("LOCALAIHUB_INSTALL_ROOT")
        if not raw:
            raise AppUpdateError("INSTALLED_PRODUCT_REQUIRED")
        root = Path(raw).expanduser().absolute()
        # Stable-shell validation also rejects Temp/.git/reparse product roots.
        resolve_launch_plan(root)
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
        result = self._run(["auth", "status", "--hostname", "github.com"], timeout=10.0)
        return result.returncode == 0

    def _api_json(self, endpoint: str, fields: dict[str, str] | None = None) -> dict[str, Any]:
        args = ["api", "--method", "GET", endpoint]
        for key, value in (fields or {}).items():
            args.extend(["-f", f"{key}={value}"])
        result = self._run(args)
        if result.returncode != 0:
            raise AppUpdateError("GITHUB_API_FAILED")
        if len(result.stdout.encode("utf-8", "replace")) > MAX_GH_JSON_BYTES:
            raise AppUpdateError("GITHUB_RESPONSE_TOO_LARGE")
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise AppUpdateError("GITHUB_RESPONSE_INVALID") from exc
        if not isinstance(value, dict):
            raise AppUpdateError("GITHUB_RESPONSE_INVALID")
        return value

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
        plan = resolve_launch_plan(root)
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
                gh = self._gh()
                if not self._authenticated():
                    result = {
                        "status": "auth_required", "available": False, "product_version": PRODUCT_VERSION,
                        "current_build": current["commit"], "current_payload": current["payload_id"],
                        "latest_build": None, "transport": "github_cli", "action": "Đăng nhập GitHub CLI để kiểm tra bản cập nhật từ repo private.",
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
                            "artifact_id": candidate.artifact_id, "transport": "github_cli",
                            "action": "Cập nhật Local AI Hub" if available else "Bạn đang dùng build main mới nhất.",
                        }
            except AppUpdateError as exc:
                result = {
                    "status": "unavailable", "available": False, "product_version": PRODUCT_VERSION,
                    "current_build": None, "latest_build": None, "transport": "github_cli", "code": exc.code,
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
        destination.mkdir(parents=True, exist_ok=False)
        result = self._run(
            ["run", "download", str(candidate.run_id), "--repo", REPOSITORY, "--name", UPDATE_ARTIFACT_NAME, "--dir", str(destination)],
            timeout=180.0,
        )
        if result.returncode != 0:
            raise AppUpdateError("UPDATE_DOWNLOAD_FAILED")

    def _validate_download(self, folder: Path, candidate: UpdateCandidate) -> tuple[dict[str, Any], Path]:
        manifest = _safe_update_manifest(_safe_json_file(folder / "update-manifest.json"), expected_commit=candidate.source_commit)
        archive = folder / str(manifest["archive"])
        if not archive.is_file() or archive.is_symlink() or _sha256(archive) != manifest["archive_sha256"]:
            raise AppUpdateError("UPDATE_ARCHIVE_HASH_MISMATCH")
        return manifest, archive

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
            if not self._authenticated():
                raise AppUpdateError("GITHUB_AUTH_REQUIRED")
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
                manifest, archive = self._validate_download(download, candidate)
                stage_payload.mkdir(parents=False, exist_ok=False)
                _safe_extract_app_archive(archive, stage_payload, expected_files=int(manifest["file_count"]))
                runtime_source = plan.payload_root / "runtime"
                if not runtime_source.is_dir() or runtime_source.is_symlink():
                    raise AppUpdateError("CURRENT_RUNTIME_UNAVAILABLE")
                shutil.copytree(runtime_source, stage_payload / "runtime", symlinks=False)
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
                (history / "previous-current.json").write_bytes(_canonical_json(previous))
                atomic_activate_pointer(root, version=version, manifest_sha256=manifest_hash)
                self._cached = None
                return {
                    "status": "activated", "source_commit": candidate.source_commit,
                    "payload_id": version, "previous_payload": previous.get("version"),
                    "restart_required": True, "launcher_changed": False, "data_root_changed": False,
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
    "UPDATE_SCHEMA", "app_update_service", "_safe_extract_app_archive", "_safe_update_manifest",
]
