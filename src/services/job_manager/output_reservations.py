"""Server-owned pre-execution output reservations.

The reservation is the ownership authority for a producer output.  A worker
may write only to a private scope created before the worker starts; the
artifact registry never infers ownership from a path found later in Output.
Public callers receive only opaque reservation/job identifiers and bounded
status projections.  This module deliberately performs no process, network or
runtime work.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from src.services import artifact_store


RESERVATION_SCHEMA = "job-output-reservation.v1"
RESERVATION_STATES = frozenset({
    "created", "producing", "ready_to_commit", "published", "failed",
    "cancelled", "interrupted", "manual_review",
})
RESERVATION_ID_RE = re.compile(r"output_res_[a-f0-9]{32}")
JOB_ID_RE = re.compile(r"(?:jobv5_[a-f0-9]{32}|job_[0-9]{8}_[0-9]{6}_[a-f0-9]{8})")
FINGERPRINT_RE = re.compile(r"[a-f0-9]{64}")
ADAPTER_ID_RE = re.compile(r"[a-z][a-z0-9_.-]{0,63}")
MAX_RESERVATIONS = 256
MAX_OUTPUT_COUNT = 64
MAX_OUTPUT_BYTES = 8 * 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 256 * 1024
MAX_LEAF_LENGTH = 180
MAX_DIRECTORY_COUNT = 16
_LOCK = threading.RLock()


class ReservationError(ValueError):
    """Bounded internal refusal; callers receive only a fixed code."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _manifest_path() -> Path:
    return artifact_store.INDEX_PATH.with_name(".job_output_reservations.json")


def _reservation_parent() -> Path:
    return artifact_store.TEMP_ROOT / "job-reservations"


def _reservation_root(reservation_id: str) -> Path:
    return _reservation_parent() / reservation_id


def _safe_id(value: object) -> str | None:
    return value if isinstance(value, str) and RESERVATION_ID_RE.fullmatch(value) else None


def _safe_job_id(value: object) -> str | None:
    return value if isinstance(value, str) and JOB_ID_RE.fullmatch(value) else None


def _safe_relative(value: object) -> str | None:
    if not isinstance(value, str) or not value or len(value) > MAX_LEAF_LENGTH or "\\" in value or ":" in value or "\x00" in value:
        return None
    try:
        parsed = PurePosixPath(value)
    except (TypeError, ValueError):
        return None
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        return None
    return parsed.as_posix()


def _reparse(path: Path) -> bool:
    try:
        info = path.lstat()
        flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
        return bool(stat.S_ISLNK(info.st_mode) or int(getattr(info, "st_file_attributes", 0) or 0) & flag)
    except FileNotFoundError:
        return False
    except OSError:
        return True


def _safe_chain(path: Path, stop: Path) -> bool:
    try:
        current = path.absolute()
        boundary = stop.absolute()
        current.relative_to(boundary)
    except (OSError, ValueError):
        return False
    while True:
        if _reparse(current):
            return False
        if current == boundary:
            return True
        parent = current.parent
        if parent == current:
            return False
        current = parent


def _identity(path: Path) -> dict[str, int] | None:
    try:
        info = path.lstat()
    except OSError:
        return None
    if _reparse(path) or not stat.S_ISREG(info.st_mode):
        return None
    size = int(info.st_size)
    mtime = int(getattr(info, "st_mtime_ns", 0) or 0)
    inode = int(getattr(info, "st_ino", 0) or 0)
    if size < 0 or size > MAX_OUTPUT_BYTES or mtime < 0 or inode < 0:
        return None
    return {"size_bytes": size, "mtime_ns": mtime, "file_id": min(inode, 2**63 - 1)}


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate reservation key")
        result[key] = value
    return result


def _empty_manifest() -> dict[str, Any]:
    return {"schema_version": RESERVATION_SCHEMA, "records": {}}


def _load_manifest() -> dict[str, Any] | None:
    path = _manifest_path()
    try:
        if _reparse(path) or path.stat().st_size > MAX_MANIFEST_BYTES:
            return None
        raw = path.read_text(encoding="utf-8")
        value = json.loads(raw, object_pairs_hook=_strict_pairs, parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("nonfinite")))
    except FileNotFoundError:
        return _empty_manifest()
    except (OSError, UnicodeError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if type(value) is not dict or set(value) != {"schema_version", "records"} or value.get("schema_version") != RESERVATION_SCHEMA:
        return None
    records = value.get("records")
    if type(records) is not dict or len(records) > MAX_RESERVATIONS:
        return None
    normalized: dict[str, Any] = {}
    for reservation_id, record in records.items():
        if _safe_id(reservation_id) is None or not _valid_record(record, reservation_id):
            return None
        normalized[reservation_id] = record
    return {"schema_version": RESERVATION_SCHEMA, "records": normalized}


def _valid_record(value: object, reservation_id: str) -> bool:
    if type(value) is not dict:
        return False
    required = {"reservation_id", "job_id", "job_fingerprint", "adapter_id", "state", "created_at", "max_output_count", "max_output_bytes", "allowed_paths", "allowed_directories"}
    if set(value) != required or value.get("reservation_id") != reservation_id:
        return False
    if _safe_job_id(value.get("job_id")) is None or not isinstance(value.get("job_fingerprint"), str) or FINGERPRINT_RE.fullmatch(value["job_fingerprint"]) is None:
        return False
    if not isinstance(value.get("adapter_id"), str) or ADAPTER_ID_RE.fullmatch(value["adapter_id"]) is None:
        return False
    if value.get("state") not in RESERVATION_STATES or not isinstance(value.get("created_at"), str):
        return False
    count = value.get("max_output_count")
    total = value.get("max_output_bytes")
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_OUTPUT_COUNT:
        return False
    if isinstance(total, bool) or not isinstance(total, int) or not 1 <= total <= MAX_OUTPUT_BYTES:
        return False
    paths = value.get("allowed_paths")
    directories = value.get("allowed_directories")
    if type(paths) is not dict or len(paths) > MAX_OUTPUT_COUNT or type(directories) is not list or len(directories) > MAX_DIRECTORY_COUNT:
        return False
    if any(_safe_relative(key) is None or item is not True for key, item in paths.items()):
        return False
    return all(_safe_relative(item) is not None for item in directories)


def _save_manifest(manifest: Mapping[str, Any]) -> bool:
    path = _manifest_path()
    temporary: Path | None = None
    try:
        payload = {"schema_version": RESERVATION_SCHEMA, "records": manifest.get("records", {})}
        encoded = (json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        if len(encoded) > MAX_MANIFEST_BYTES:
            return False
        parent = path.parent
        parent.mkdir(parents=True, exist_ok=True)
        if not _safe_chain(parent, parent):
            return False
        handle, name = tempfile.mkstemp(prefix=".job-output-reservation-", suffix=".tmp", dir=parent)
        os.close(handle)
        temporary = Path(name)
        with temporary.open("wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        return True
    except (OSError, TypeError, ValueError):
        return False
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _copy_detached(value: object) -> object:
    return json.loads(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False))


@dataclass(frozen=True)
class ReservationHandle:
    """Private capability passed only to a server-owned producer context."""

    reservation_id: str
    job_id: str

    def output_path(self, filename: str) -> Path:
        return reserve_output_path(self.reservation_id, filename)

    def output_directory(self, name: str) -> Path:
        return reserve_output_directory(self.reservation_id, name)


def create_reservation(job_id: str, job_fingerprint: str, adapter_id: str, *, max_output_count: int = MAX_OUTPUT_COUNT, max_output_bytes: int = MAX_OUTPUT_BYTES) -> dict[str, Any] | None:
    if _safe_job_id(job_id) is None or not isinstance(job_fingerprint, str) or FINGERPRINT_RE.fullmatch(job_fingerprint) is None or not isinstance(adapter_id, str) or ADAPTER_ID_RE.fullmatch(adapter_id) is None:
        return None
    count = max(1, min(MAX_OUTPUT_COUNT, int(max_output_count)))
    total = max(1, min(MAX_OUTPUT_BYTES, int(max_output_bytes)))
    with _LOCK:
        manifest = _load_manifest()
        if manifest is None:
            return None
        reservation_id = f"output_res_{uuid.uuid4().hex}"
        parent = _reservation_parent()
        root = _reservation_root(reservation_id)
        try:
            parent.mkdir(parents=True, exist_ok=True)
            if not _safe_chain(parent, parent) or root.exists() or _reparse(parent):
                return None
            root.mkdir()
        except OSError:
            return None
        record = {
            "reservation_id": reservation_id,
            "job_id": job_id,
            "job_fingerprint": job_fingerprint,
            "adapter_id": adapter_id,
            "state": "created",
            "created_at": _now(),
            "max_output_count": count,
            "max_output_bytes": total,
            "allowed_paths": {},
            "allowed_directories": [],
        }
        manifest["records"][reservation_id] = record
        if not _save_manifest(manifest):
            try:
                root.rmdir()
            except OSError:
                pass
            return None
        return {"reservation_id": reservation_id, "job_id": job_id, "state": "created", "max_output_count": count, "max_output_bytes": total}


def handle(reservation_id: str, job_id: str) -> ReservationHandle | None:
    if _safe_id(reservation_id) is None or _safe_job_id(job_id) is None:
        return None
    with _LOCK:
        manifest = _load_manifest()
        record = manifest.get("records", {}).get(reservation_id) if isinstance(manifest, dict) else None
        if not isinstance(record, dict) or record.get("job_id") != job_id or record.get("state") not in {"created", "producing"}:
            return None
    return ReservationHandle(reservation_id, job_id)


def begin_producing(reservation_id: str) -> bool:
    return _transition(reservation_id, {"created"}, "producing")


def _transition(reservation_id: str, allowed: set[str], state: str) -> bool:
    with _LOCK:
        manifest = _load_manifest()
        record = manifest.get("records", {}).get(reservation_id) if isinstance(manifest, dict) else None
        if not isinstance(record, dict) or record.get("state") not in allowed:
            return False
        record["state"] = state
        return _save_manifest(manifest)


def reserve_output_path(reservation_id: str, filename: str) -> Path:
    relative = _safe_relative(filename)
    if relative is None:
        raise ReservationError("OUTPUT_LEAF_INVALID")
    with _LOCK:
        manifest = _load_manifest()
        record = manifest.get("records", {}).get(reservation_id) if isinstance(manifest, dict) else None
        if not isinstance(record, dict) or record.get("state") not in {"created", "producing"}:
            raise ReservationError("OUTPUT_RESERVATION_UNAVAILABLE")
        root = _reservation_root(reservation_id)
        if not _safe_chain(root, _reservation_parent()) or not root.is_dir():
            raise ReservationError("OUTPUT_RESERVATION_UNAVAILABLE")
        target = root / relative
        if target.exists() or _reparse(target.parent):
            raise ReservationError("OUTPUT_LEAF_ALREADY_EXISTS")
        record["allowed_paths"][relative] = True
        if not _save_manifest(manifest):
            raise ReservationError("OUTPUT_RESERVATION_UNAVAILABLE")
        return target


def reserve_output_directory(reservation_id: str, name: str) -> Path:
    relative = _safe_relative(name)
    if relative is None:
        raise ReservationError("OUTPUT_DIRECTORY_INVALID")
    with _LOCK:
        manifest = _load_manifest()
        record = manifest.get("records", {}).get(reservation_id) if isinstance(manifest, dict) else None
        if not isinstance(record, dict) or record.get("state") not in {"created", "producing"}:
            raise ReservationError("OUTPUT_RESERVATION_UNAVAILABLE")
        root = _reservation_root(reservation_id)
        directory = root / relative
        if not _safe_chain(root, _reservation_parent()) or directory.exists():
            raise ReservationError("OUTPUT_DIRECTORY_ALREADY_EXISTS")
        directory.mkdir()
        record["allowed_directories"].append(relative)
        if not _save_manifest(manifest):
            try:
                directory.rmdir()
            except OSError:
                pass
            raise ReservationError("OUTPUT_RESERVATION_UNAVAILABLE")
        return directory


def _candidate_paths(result: object) -> list[Path]:
    if not isinstance(result, Mapping):
        return []
    values: list[object] = []
    for field in ("output", "files", "outputs"):
        raw = result.get(field)
        if isinstance(raw, (str, Path)):
            values.append(raw)
        elif isinstance(raw, list):
            values.extend(raw[:MAX_OUTPUT_COUNT])
    return [Path(value) for value in values if isinstance(value, (str, Path)) and str(value)]


def _relative_candidate(root: Path, candidate: Path) -> str | None:
    try:
        absolute = candidate.absolute()
        relative = absolute.relative_to(root.absolute())
    except (OSError, ValueError):
        return None
    safe = _safe_relative(relative.as_posix())
    if safe is None or not _safe_chain(absolute, root) or _reparse(absolute) or not absolute.is_file():
        return None
    return safe


def _hash_stable(path: Path) -> tuple[int, str, dict[str, int]] | None:
    before = _identity(path)
    if before is None:
        return None
    digest = hashlib.sha256()
    total = 0
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                total += len(chunk)
                if total > MAX_OUTPUT_BYTES:
                    return None
                digest.update(chunk)
    except OSError:
        return None
    after = _identity(path)
    if after is None or before != after or total != before["size_bytes"]:
        return None
    return total, digest.hexdigest(), after


def _copy_reserved_file(source: Path, reservation_id: str, relative: str) -> Path | None:
    destination_root = artifact_store.OUTPUT_ROOT / ".hub-reserved" / reservation_id
    destination = destination_root / Path(relative).name
    temporary: Path | None = None
    try:
        destination_root.mkdir(parents=True, exist_ok=True)
        if not _safe_chain(destination_root, artifact_store.OUTPUT_ROOT) or destination.exists():
            return None
        handle, name = tempfile.mkstemp(prefix=".reserved-output-", suffix=".tmp", dir=destination_root)
        os.close(handle)
        temporary = Path(name)
        with source.open("rb") as source_stream, temporary.open("wb") as destination_stream:
            shutil.copyfileobj(source_stream, destination_stream, length=1024 * 1024)
            destination_stream.flush()
            os.fsync(destination_stream.fileno())
        os.replace(temporary, destination)
        temporary = None
        return destination
    except OSError:
        return None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def commit_reservation(reservation_id: str, result: object, *, provenance: Mapping[str, Any], require_output: bool = False) -> dict[str, Any]:
    """Commit only files allocated in the pre-created reservation scope."""

    with _LOCK:
        manifest = _load_manifest()
        record = manifest.get("records", {}).get(reservation_id) if isinstance(manifest, dict) else None
        if not isinstance(record, dict) or record.get("state") not in {"producing", "ready_to_commit"}:
            return {"status": "unavailable", "code": "OUTPUT_RESERVATION_UNAVAILABLE"}
        root = _reservation_root(reservation_id)
        candidates = _candidate_paths(result)
        if not candidates:
            record["state"] = "failed" if require_output else "ready_to_commit"
            _save_manifest(manifest)
            return {"status": "no_output", "code": "OUTPUT_MISSING" if require_output else None, "artifacts": []}
        if len(candidates) > int(record["max_output_count"]):
            record["state"] = "manual_review"
            _save_manifest(manifest)
            return {"status": "manual_review", "code": "OUTPUT_COUNT_LIMIT"}
        copied: list[Path] = []
        seen: set[str] = set()
        total_bytes = 0
        for candidate in candidates:
            relative = _relative_candidate(root, candidate)
            allowed = isinstance(relative, str) and (relative in record["allowed_paths"] or any(relative == directory or relative.startswith(directory + "/") for directory in record["allowed_directories"]))
            stable = _hash_stable(candidate) if relative is not None and allowed else None
            if relative is None or not allowed or stable is None or relative in seen:
                record["state"] = "manual_review"
                _save_manifest(manifest)
                return {"status": "manual_review", "code": "OUTPUT_OWNERSHIP_UNPROVEN"}
            seen.add(relative)
            total_bytes += stable[0]
            if total_bytes > int(record["max_output_bytes"]):
                record["state"] = "manual_review"
                _save_manifest(manifest)
                return {"status": "manual_review", "code": "OUTPUT_SIZE_LIMIT"}
            destination = _copy_reserved_file(candidate, reservation_id, relative)
            if destination is None:
                record["state"] = "failed"
                _save_manifest(manifest)
                return {"status": "failed", "code": "OUTPUT_STAGE_FAILED"}
            copied.append(destination)
        artifacts = artifact_store.register_worker_outputs(copied, provenance=dict(provenance))
        if not isinstance(artifacts, list) or len(artifacts) != len(copied):
            for staged_path in copied:
                try:
                    staged_path.unlink(missing_ok=True)
                except OSError:
                    pass
            record["state"] = "failed"
            _save_manifest(manifest)
            return {"status": "failed", "code": "OUTPUT_PUBLISH_FAILED"}
        record["state"] = "published"
        if not _save_manifest(manifest):
            return {"status": "unavailable", "code": "OUTPUT_RESERVATION_PERSISTENCE_FAILED"}
        return {"status": "published", "artifacts": artifacts}


def abort_reservation(reservation_id: str, *, state: str = "failed") -> dict[str, Any]:
    """Clean only files in the exact private reservation scope."""

    if state not in {"failed", "cancelled", "interrupted"}:
        state = "failed"
    with _LOCK:
        manifest = _load_manifest()
        record = manifest.get("records", {}).get(reservation_id) if isinstance(manifest, dict) else None
        if not isinstance(record, dict):
            return {"status": "unavailable", "removed_count": 0}
        root = _reservation_root(reservation_id)
        removed = 0
        ambiguous = False
        try:
            if not _safe_chain(root, _reservation_parent()) or _reparse(root):
                ambiguous = True
            else:
                allowed = set(record["allowed_paths"])
                directories = tuple(record["allowed_directories"])
                for current, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
                    current_path = Path(current)
                    if _reparse(current_path):
                        ambiguous = True
                        dirnames[:] = []
                        continue
                    dirnames[:] = [name for name in dirnames if not _reparse(current_path / name)]
                    for name in filenames:
                        candidate = current_path / name
                        relative = _safe_relative(candidate.relative_to(root).as_posix())
                        owned = relative in allowed if relative is not None else False
                        owned = owned or (relative is not None and any(relative == item or relative.startswith(item + "/") for item in directories))
                        if not owned or _identity(candidate) is None:
                            ambiguous = True
                            continue
                        try:
                            candidate.unlink()
                            removed += 1
                        except OSError:
                            ambiguous = True
                if not ambiguous:
                    for current, dirnames, _filenames in os.walk(root, topdown=False, followlinks=False):
                        for name in dirnames:
                            try:
                                (Path(current) / name).rmdir()
                            except OSError:
                                ambiguous = True
                    try:
                        root.rmdir()
                    except OSError:
                        ambiguous = True
        except OSError:
            ambiguous = True
        record["state"] = "manual_review" if ambiguous else state
        _save_manifest(manifest)
        return {"status": "manual_review" if ambiguous else "cleaned", "removed_count": removed}


def reconcile_reservations(*, active_job_ids: set[str] | None = None) -> dict[str, int]:
    active = active_job_ids or set()
    cleaned = 0
    manual_review = 0
    with _LOCK:
        manifest = _load_manifest()
        records = manifest.get("records", {}) if isinstance(manifest, dict) else {}
        for reservation_id, record in list(records.items()):
            if record.get("state") in {"published", "failed", "cancelled", "interrupted", "manual_review"}:
                continue
            if record.get("job_id") in active:
                continue
            result = abort_reservation(reservation_id, state="interrupted")
            if result.get("status") == "manual_review":
                manual_review += 1
            else:
                cleaned += int(result.get("removed_count") or 0)
    return {"cleaned": cleaned, "manual_review": manual_review}


def inspect_reservation(reservation_id: str) -> dict[str, Any] | None:
    with _LOCK:
        manifest = _load_manifest()
        record = manifest.get("records", {}).get(reservation_id) if isinstance(manifest, dict) else None
        if not isinstance(record, dict):
            return None
        return {
            "reservation_id": reservation_id,
            "job_id": record["job_id"],
            "adapter_id": record["adapter_id"],
            "state": record["state"],
            "allowed_count": len(record["allowed_paths"]) + len(record["allowed_directories"]),
        }


__all__ = [
    "MAX_OUTPUT_BYTES",
    "MAX_OUTPUT_COUNT",
    "RESERVATION_ID_RE",
    "RESERVATION_SCHEMA",
    "ReservationError",
    "ReservationHandle",
    "abort_reservation",
    "begin_producing",
    "commit_reservation",
    "create_reservation",
    "handle",
    "inspect_reservation",
    "reconcile_reservations",
    "reserve_output_directory",
    "reserve_output_path",
]
