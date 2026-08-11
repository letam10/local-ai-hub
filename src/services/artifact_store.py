"""Private artifact registry for files staged or produced by Local AI Hub.

The API exposes opaque artifact IDs instead of workstation paths.  This keeps
the browser control plane useful without disclosing local folders, and limits
file serving/opening to Hub-owned temporary, output, and archive roots.
"""

from __future__ import annotations

import json
import hashlib
import io
import mimetypes
import os
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.shared.paths.registry import CONFIG_ROOT, OUTPUT_ROOT, ROOT, TEMP_ROOT


INDEX_PATH = CONFIG_ROOT / "artifacts.json"
UPLOAD_ROOT = TEMP_ROOT / "uploads"
ARCHIVE_ROOT = ROOT / "Archive"
DEFAULT_MAX_UPLOAD_BYTES = 8 * 1024 * 1024 * 1024
DEFAULT_UPLOAD_DISK_SAFETY_BYTES = 512 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 4 * 1024 * 1024
OWNED_UPLOAD_PREFIX = "hub-upload-"
OWNED_OUTPUT_PREFIX = "hub-job-"
DEFAULT_ORPHAN_EXPIRY_SECONDS = 24 * 60 * 60
MAX_JOB_OUTPUT_BYTES = 8 * 1024 * 1024 * 1024
# Compatibility name for private callers; public v1 upload limits are read
# from Hub configuration and default to this value.
MAX_UPLOAD_BYTES = DEFAULT_MAX_UPLOAD_BYTES
_LOCK = threading.RLock()
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._() -]+")
_LOCAL_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\)")
_LOCAL_PATH_TAIL = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\)[^\r\n]*")
_PATH_FIELDS = {
    "annotated_image",
    "audio",
    "files",
    "input",
    "json",
    "mask",
    "masks",
    "output",
    "output_root",
    "preview",
    "srt",
    "video",
}


class UploadError(ValueError):
    """A safe, user-actionable upload failure.

    The HTTP handler deliberately maps this to a client-facing message without
    exposing a local path.  The stream is always consumed only in bounded
    chunks by :func:`stage_upload_stream`.
    """


class ArtifactWriteError(ValueError):
    """Safe failure while atomically producing a Hub-owned job artifact."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict[str, dict[str, Any]]:
    try:
        value = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _save(index: dict[str, dict[str, Any]]) -> None:
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = INDEX_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(INDEX_PATH)


def _safe_name(value: str) -> str:
    name = Path(value).name.strip().replace("\x00", "")
    name = _SAFE_FILENAME.sub("_", name)
    return name[:180] or "upload.bin"


def normalize_media_type(value: str | None, *, fallback_name: str) -> str:
    """Keep a client-supplied media type syntactically small and harmless."""

    candidate = (value or "").split(";", 1)[0].strip().lower()
    if re.fullmatch(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", candidate):
        return candidate
    return mimetypes.guess_type(fallback_name)[0] or "application/octet-stream"


def _allowed(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    for root in (UPLOAD_ROOT, OUTPUT_ROOT, ARCHIVE_ROOT):
        try:
            resolved.relative_to(root.resolve())
            return True
        except (OSError, ValueError):
            continue
    return False


def _public(record: dict[str, Any]) -> dict[str, Any]:
    public = {
        "id": record["id"],
        "name": record["name"],
        "size_bytes": record.get("size_bytes", 0),
        "media_type": record.get("media_type") or "application/octet-stream",
        "url": f"/api/artifacts/{record['id']}",
        "created_at": record.get("created_at"),
        "sha256": record.get("sha256"),
    }
    provenance = record.get("provenance")
    if isinstance(provenance, dict):
        public["provenance"] = dict(provenance)
    return public


def _safe_provenance(value: Any) -> dict[str, Any]:
    """Allow only opaque, server-derived V5 artifact lineage fields."""

    if type(value) is not dict:
        raise ArtifactWriteError("Artifact provenance is invalid.")
    allowed = {"job_id", "job_spec_fingerprint", "adapter_id", "attempt", "status"}
    if set(value) != allowed:
        raise ArtifactWriteError("Artifact provenance is invalid.")
    job_id = value.get("job_id")
    fingerprint = value.get("job_spec_fingerprint")
    adapter_id = value.get("adapter_id")
    attempt = value.get("attempt")
    status = value.get("status")
    if not isinstance(job_id, str) or not re.fullmatch(r"jobv5_[a-f0-9]{32}", job_id):
        raise ArtifactWriteError("Artifact provenance is invalid.")
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
        raise ArtifactWriteError("Artifact provenance is invalid.")
    if not isinstance(adapter_id, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", adapter_id):
        raise ArtifactWriteError("Artifact provenance is invalid.")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or not 1 <= attempt <= 10_000:
        raise ArtifactWriteError("Artifact provenance is invalid.")
    if status not in {"queued", "starting", "running", "cancelling", "completed", "failed", "unavailable", "interrupted"}:
        raise ArtifactWriteError("Artifact provenance is invalid.")
    return {
        "job_id": job_id,
        "job_spec_fingerprint": fingerprint,
        "adapter_id": adapter_id,
        "attempt": attempt,
        "status": status,
    }


def register_path(
    path: str | Path,
    *,
    name: str | None = None,
    media_type: str | None = None,
    sha256: str | None = None,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Register a Hub-owned file and return its safe public reference."""

    candidate = Path(path).expanduser()
    try:
        candidate = candidate.resolve()
    except OSError:
        return None
    if not candidate.is_file() or not _allowed(candidate):
        return None
    safe_provenance = _safe_provenance(provenance) if provenance is not None else None
    with _LOCK:
        index = _load()
        for record in index.values():
            if record.get("path") == str(candidate):
                return _public(record)
        artifact_id = f"artifact_{uuid.uuid4().hex}"
        safe_name = _safe_name(name or candidate.name)
        record = {
            "id": artifact_id,
            "path": str(candidate),
            "name": safe_name,
            "size_bytes": candidate.stat().st_size,
            "media_type": normalize_media_type(media_type, fallback_name=safe_name),
            "created_at": _now(),
        }
        if sha256:
            record["sha256"] = sha256
        if safe_provenance is not None:
            record["provenance"] = safe_provenance
        index[artifact_id] = record
        _save(index)
        return _public(record)


def _remove_owned_upload(path: Path | None) -> None:
    if path is None:
        return
    try:
        if not path.name.startswith(OWNED_UPLOAD_PREFIX):
            return
        path.resolve().relative_to(UPLOAD_ROOT.resolve())
        path.unlink(missing_ok=True)
    except (OSError, ValueError):
        return


def stage_upload_stream(
    filename: str,
    stream: Any,
    expected_size: int,
    content_type: str | None = None,
    *,
    max_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
    disk_safety_bytes: int = DEFAULT_UPLOAD_DISK_SAFETY_BYTES,
    chunk_bytes: int = UPLOAD_CHUNK_BYTES,
) -> dict[str, Any]:
    """Stream a declared upload to a task-owned part file and atomically register it.

    ``stream`` only needs a ``read(size)`` method.  This keeps the HTTP request
    path bounded even for multi-gigabyte user media and makes the primitive
    directly testable with a fake short/disconnecting stream.
    """

    if not isinstance(expected_size, int) or expected_size <= 0:
        raise UploadError("Tệp tải lên phải có Content-Length lớn hơn 0.")
    if not isinstance(max_bytes, int) or max_bytes <= 0 or expected_size > max_bytes:
        raise UploadError(f"Tệp tải lên vượt giới hạn {max(1, int(max_bytes) // (1024 * 1024))} MiB của Hub.")
    if not isinstance(chunk_bytes, int) or chunk_bytes < 1024:
        raise ValueError("Kích thước chunk upload không hợp lệ.")
    safe_name = _safe_name(filename)
    normalized_type = normalize_media_type(content_type, fallback_name=safe_name)
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        free_bytes = shutil.disk_usage(UPLOAD_ROOT).free
    except OSError as exc:
        raise UploadError("Không thể kiểm tra dung lượng trống cho upload Hub.") from exc
    required = expected_size + max(0, int(disk_safety_bytes))
    if free_bytes < required:
        raise UploadError("Không đủ dung lượng trống an toàn để nhận upload này.")

    token = uuid.uuid4().hex
    part_path = UPLOAD_ROOT / f"{OWNED_UPLOAD_PREFIX}{token}.part"
    final_path = UPLOAD_ROOT / f"{OWNED_UPLOAD_PREFIX}{token}_{safe_name}"
    received = 0
    digest = hashlib.sha256()
    try:
        with part_path.open("xb") as handle:
            while received < expected_size:
                requested = min(chunk_bytes, expected_size - received)
                chunk = stream.read(requested)
                if not isinstance(chunk, (bytes, bytearray)) or not chunk:
                    raise UploadError("Upload bị ngắt trước khi nhận đủ Content-Length.")
                if len(chunk) > requested:
                    raise UploadError("Luồng upload trả nhiều byte hơn Content-Length đã khai báo.")
                handle.write(chunk)
                digest.update(chunk)
                received += len(chunk)
        if received != expected_size:
            raise UploadError("Kích thước upload nhận được không khớp Content-Length.")
        part_path.replace(final_path)
        artifact = register_path(
            final_path,
            name=safe_name,
            media_type=normalized_type,
            sha256=digest.hexdigest(),
        )
        if artifact is None:  # pragma: no cover - guarded by the owned upload path
            raise RuntimeError("Hub không thể đăng ký tệp đã tải lên.")
        return artifact
    except Exception:
        # Both paths are task-owned tokens.  Do not touch any pre-existing
        # upload or artifact on a failed/disconnected request.
        _remove_owned_upload(part_path)
        _remove_owned_upload(final_path)
        raise


def cleanup_owned_upload_orphans(*, expiry_seconds: int = DEFAULT_ORPHAN_EXPIRY_SECONDS, now: float | None = None) -> dict[str, int]:
    """Remove only expired Hub-owned upload remnants from the managed root.

    The prefix is an ownership marker created by :func:`stage_upload_stream`.
    Registered final files are retained.  The return value contains counts,
    never filesystem paths or filenames.
    """

    if isinstance(expiry_seconds, bool) or not isinstance(expiry_seconds, int) or expiry_seconds < 0:
        raise ValueError("Upload orphan expiry must be a non-negative integer.")
    current = datetime.now(timezone.utc).timestamp() if now is None else float(now)
    cutoff = current - expiry_seconds
    try:
        root = UPLOAD_ROOT.resolve()
        records = _load()
        registered = {
            str(Path(record["path"]).resolve())
            for record in records.values()
            if isinstance(record, dict) and isinstance(record.get("path"), str)
        }
    except (OSError, ValueError):
        return {"removed": 0, "retained": 0}
    removed = 0
    retained = 0
    try:
        candidates = list(root.iterdir()) if root.is_dir() else []
    except OSError:
        candidates = []
    for candidate in candidates:
        if not candidate.name.startswith(OWNED_UPLOAD_PREFIX):
            continue
        try:
            resolved = candidate.resolve()
            resolved.relative_to(root)
            stale = candidate.stat().st_mtime <= cutoff
            is_partial_or_unregistered = candidate.suffix == ".part" or str(resolved) not in registered
            if stale and is_partial_or_unregistered:
                candidate.unlink(missing_ok=True)
                removed += 1
            else:
                retained += 1
        except (OSError, ValueError):
            retained += 1
    return {"removed": removed, "retained": retained}


def _ensure_disk_capacity(root: Path, expected_bytes: int, disk_safety_bytes: int) -> None:
    if isinstance(expected_bytes, bool) or not isinstance(expected_bytes, int) or expected_bytes < 0:
        raise ArtifactWriteError("Artifact output size is invalid.")
    if isinstance(disk_safety_bytes, bool) or not isinstance(disk_safety_bytes, int) or disk_safety_bytes < 0:
        raise ArtifactWriteError("Artifact disk safety threshold is invalid.")
    try:
        free_bytes = shutil.disk_usage(root).free
    except OSError as exc:
        raise ArtifactWriteError("Hub cannot verify free disk capacity for this output.") from exc
    if free_bytes < expected_bytes + disk_safety_bytes:
        raise ArtifactWriteError("Hub does not have enough free disk capacity for this output.")


def _remove_owned_output(path: Path | None) -> None:
    if path is None:
        return
    try:
        if not path.name.startswith(OWNED_OUTPUT_PREFIX):
            return
        path.resolve().relative_to(OUTPUT_ROOT.resolve())
        path.unlink(missing_ok=True)
    except (OSError, ValueError):
        return


def atomic_write_job_output(
    job_id: str,
    content: bytes,
    *,
    name: str = "result.bin",
    media_type: str | None = None,
    provenance: dict[str, Any],
    disk_safety_bytes: int = 0,
) -> dict[str, Any]:
    """Write one bounded Hub job output atomically and register safe provenance."""

    if not isinstance(job_id, str) or not re.fullmatch(r"jobv5_[a-f0-9]{32}", job_id):
        raise ArtifactWriteError("Hub job ID is invalid.")
    if not isinstance(content, bytes):
        raise ArtifactWriteError("Artifact output must be bytes.")
    if len(content) > MAX_JOB_OUTPUT_BYTES:
        raise ArtifactWriteError("Artifact output exceeds the Hub safety limit.")
    safe_provenance = _safe_provenance(provenance)
    if safe_provenance["job_id"] != job_id:
        raise ArtifactWriteError("Artifact provenance does not match the Hub job.")
    safe_name = _safe_name(name)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    _ensure_disk_capacity(OUTPUT_ROOT, len(content), disk_safety_bytes)
    token = uuid.uuid4().hex
    prefix = f"{OWNED_OUTPUT_PREFIX}{job_id[-8:]}-{token}"
    part_path = OUTPUT_ROOT / f"{prefix}.part"
    final_path = OUTPUT_ROOT / f"{prefix}-{safe_name}"
    digest = hashlib.sha256(content).hexdigest()
    try:
        with part_path.open("xb") as handle:
            handle.write(content)
            handle.flush()
        part_path.replace(final_path)
        artifact = register_path(
            final_path,
            name=safe_name,
            media_type=media_type,
            sha256=digest,
            provenance=safe_provenance,
        )
        if artifact is None:  # pragma: no cover - final path is managed output
            raise ArtifactWriteError("Hub cannot register the completed artifact output.")
        return artifact
    except Exception:
        _remove_owned_output(part_path)
        _remove_owned_output(final_path)
        raise


def stage_upload(filename: str, body: bytes, content_type: str | None = None) -> dict[str, Any]:
    """Compatibility helper for internal callers; HTTP uploads use streaming."""

    if not isinstance(body, bytes):
        raise UploadError("Dữ liệu upload không hợp lệ.")
    return stage_upload_stream(
        filename,
        io.BytesIO(body),
        len(body),
        content_type,
        max_bytes=DEFAULT_MAX_UPLOAD_BYTES,
        disk_safety_bytes=0,
    )


def resolve(artifact_id: str) -> Path | None:
    if not re.fullmatch(r"artifact_[a-f0-9]{32}", artifact_id or ""):
        return None
    with _LOCK:
        record = _load().get(artifact_id)
    if not isinstance(record, dict) or not isinstance(record.get("path"), str):
        return None
    candidate = Path(record["path"])
    return candidate if candidate.is_file() and _allowed(candidate) else None


def describe(artifact_id: str) -> dict[str, Any] | None:
    with _LOCK:
        record = _load().get(artifact_id)
    if not isinstance(record, dict):
        return None
    path = resolve(artifact_id)
    return _public(record) if path is not None else None


def list_artifacts(*, limit: int = 240) -> list[dict[str, Any]]:
    """List safe metadata for Hub-owned artifacts without disclosing paths."""

    bounded = max(1, min(500, int(limit)))
    with _LOCK:
        records = list(_load().values())
    result: list[dict[str, Any]] = []
    for record in sorted(records, key=lambda item: str(item.get("created_at") or ""), reverse=True):
        if not isinstance(record, dict) or not isinstance(record.get("id"), str):
            continue
        path = resolve(record["id"])
        if path is not None:
            result.append(_public(record))
        if len(result) >= bounded:
            break
    return result


def open_artifact(artifact_id: str) -> tuple[bool, str]:
    path = resolve(artifact_id)
    if path is None:
        return False, "Không tìm thấy artifact Hub yêu cầu."
    try:
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:  # pragma: no cover - the desktop target is Windows
            return False, "Mở artifact chỉ được hỗ trợ từ bản Hub cho Windows."
    except OSError as exc:
        return False, str(exc)
    return True, "Đã yêu cầu Windows mở artifact Hub."


def _safe_text(value: str) -> str:
    return _LOCAL_PATH_TAIL.sub("[đường-dẫn-cục-bộ]", value)


def publicize(value: Any, *, key: str | None = None) -> Any:
    """Convert private output paths into artifact references for API responses."""

    if isinstance(value, Path):
        artifact = register_path(value)
        return artifact or value.name
    if isinstance(value, str):
        candidate = Path(value)
        if key in _PATH_FIELDS or _LOCAL_PATH.search(value):
            artifact = register_path(candidate)
            if artifact:
                return artifact
            return candidate.name if _LOCAL_PATH.search(value) else _safe_text(value)
        return _safe_text(value)
    if isinstance(value, list):
        return [publicize(item, key=key) for item in value]
    if isinstance(value, dict):
        return {str(item_key): publicize(item_value, key=str(item_key)) for item_key, item_value in value.items()}
    return value
