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
import stat
import tempfile
import threading
import uuid
import ctypes
from ctypes import wintypes
from contextlib import ExitStack, contextmanager
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


def _directory_identity(path: Path) -> dict[str, int] | None:
    """Return a bounded identity for one existing, non-reparse directory."""

    try:
        info = path.lstat()
    except OSError:
        return None
    if _reparse(path) or not stat.S_ISDIR(info.st_mode):
        return None
    size = int(info.st_size)
    mtime = int(getattr(info, "st_mtime_ns", 0) or 0)
    inode = int(getattr(info, "st_ino", 0) or 0)
    if size < 0 or mtime < 0 or inode < 0:
        return None
    return {"size_bytes": size, "mtime_ns": mtime, "file_id": min(inode, 2**63 - 1)}


def _full_safe_chain(path: Path) -> bool:
    """Validate every existing ancestor up to the filesystem root."""

    try:
        absolute = path.absolute()
        boundary = Path(absolute.anchor)
        if not _safe_chain(absolute, boundary):
            return False
        return _directory_identity(absolute) is not None
    except OSError:
        return False


def _ensure_safe_directory(path: Path) -> bool:
    """Create only missing directory nodes below an already safe chain."""

    try:
        absolute = path.absolute()
        missing: list[Path] = []
        current = absolute
        while not current.exists():
            missing.append(current)
            parent = current.parent
            if parent == current:
                return False
            current = parent
        if _directory_identity(current) is None or not _full_safe_chain(current):
            return False
        for child in reversed(missing):
            child.mkdir()
            if _directory_identity(child) is None or not _full_safe_chain(child):
                return False
        return _directory_identity(absolute) is not None and _full_safe_chain(absolute)
    except OSError:
        return False


def _same_object(expected: object, current: object) -> bool:
    """Compare ownership identity without treating producer writes as replacement."""

    if not isinstance(expected, Mapping) or not isinstance(current, Mapping):
        return False
    expected_id = expected.get("file_id")
    current_id = current.get("file_id")
    return isinstance(expected_id, int) and not isinstance(expected_id, bool) and expected_id == current_id


def _valid_identity(value: object) -> bool:
    if type(value) is not dict or set(value) != {"size_bytes", "mtime_ns", "file_id"}:
        return False
    return all(isinstance(value.get(key), int) and not isinstance(value.get(key), bool) and value.get(key) >= 0 for key in ("size_bytes", "mtime_ns", "file_id"))


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


def _delete_identity_attested(path: Path, expected: Mapping[str, Any], *, directory: bool = False) -> bool:
    """Delete only the object bound to ``expected``.

    Windows opens the exact leaf with delete sharing denied and applies the
    disposition to that handle.  A pathname replacement after the preflight
    therefore cannot redirect the delete.  Other platforms refuse rather
    than falling back to a pathname-only unlink in this ownership boundary.
    """

    if not _valid_identity(dict(expected)):
        return False
    if os.name != "nt":
        return False
    desired_access = 0x80000000 | 0x00010000  # GENERIC_READ | DELETE
    share_mode = 0x00000001 | 0x00000002  # FILE_SHARE_READ | FILE_SHARE_WRITE
    flags = 0x00200000  # FILE_FLAG_OPEN_REPARSE_POINT
    if directory:
        flags |= 0x02000000  # FILE_FLAG_BACKUP_SEMANTICS
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel32.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel32.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    handle = create(str(path), desired_access, share_mode, None, 3, flags, None)  # OPEN_EXISTING
    invalid = wintypes.HANDLE(-1).value
    if handle == invalid:
        return False
    try:
        current = _identity(path) if not directory else _directory_identity(path)
        if not isinstance(current, dict) or not _same_object(expected, current):
            return False
        # FILE_DISPOSITION_INFO { BOOLEAN DeleteFile; }.
        class _Disposition(ctypes.Structure):
            _fields_ = [("DeleteFile", wintypes.BOOLEAN)]

        disposition = _Disposition(1)
        set_info = kernel32.SetFileInformationByHandle
        set_info.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD]
        set_info.restype = wintypes.BOOL
        return bool(set_info(handle, 4, ctypes.byref(disposition), ctypes.sizeof(disposition)))
    finally:
        close(handle)


@contextmanager
def _open_source_locked(path: Path, expected: tuple[int, str, dict[str, int]]):
    """Hold a no-delete/no-write-sharing source handle through publication."""

    if os.name != "nt":
        # The package is fail-closed outside Windows rather than using a
        # pathname-only copy window for a reservation-owned source.
        yield None
        return
    desired_access = 0x80000000  # GENERIC_READ
    share_mode = 0x00000001  # FILE_SHARE_READ only: deny write/delete races
    flags = 0x00200000  # FILE_FLAG_OPEN_REPARSE_POINT
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel32.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    handle = create(str(path), desired_access, share_mode, None, 3, flags, None)
    invalid = wintypes.HANDLE(-1).value
    if handle == invalid:
        yield None
        return
    fd = None
    stream = None
    try:
        current = _identity(path)
        if current is None or not _same_object(expected[2], current):
            yield None
            return
        import msvcrt

        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        handle = invalid
        stream = os.fdopen(fd, "rb", closefd=True)
        fd = None
        yield stream
    finally:
        if stream is not None:
            stream.close()
        elif fd is not None:
            os.close(fd)
        elif handle != invalid:
            kernel32.CloseHandle(handle)


def _stream_matches_expected(stream: Any, expected: tuple[int, str, dict[str, int]]) -> bool:
    """Hash an already-held stream without reopening the mutable pathname."""

    try:
        stream.seek(0)
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_OUTPUT_BYTES:
                return False
            digest.update(chunk)
        stream.seek(0)
        return total == expected[0] and digest.hexdigest() == expected[1]
    except (OSError, ValueError):
        return False


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
        if not _full_safe_chain(path.parent) or _reparse(path) or path.stat().st_size > MAX_MANIFEST_BYTES:
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
    required = {"reservation_id", "job_id", "job_fingerprint", "adapter_id", "state", "created_at", "max_output_count", "max_output_bytes", "root_identity", "allowed_paths", "allowed_directories"}
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
    if not _valid_identity(value.get("root_identity")):
        return False
    paths = value.get("allowed_paths")
    directories = value.get("allowed_directories")
    if type(paths) is not dict or len(paths) > MAX_OUTPUT_COUNT or type(directories) is not dict or len(directories) > MAX_DIRECTORY_COUNT:
        return False
    if any(_safe_relative(key) is None or not _valid_identity(item) for key, item in paths.items()):
        return False
    return all(_safe_relative(key) is not None and _valid_identity(item) for key, item in directories.items())


def _save_manifest(manifest: Mapping[str, Any]) -> bool:
    path = _manifest_path()
    temporary: Path | None = None
    temporary_identity: dict[str, int] | None = None
    parent: Path | None = None
    parent_identity: dict[str, int] | None = None
    target_before: dict[str, int] | None = None
    target_existed = False
    previous_bytes: bytes | None = None
    try:
        payload = {"schema_version": RESERVATION_SCHEMA, "records": manifest.get("records", {})}
        encoded = (json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        if len(encoded) > MAX_MANIFEST_BYTES:
            return False
        parent = path.parent
        if not _ensure_safe_directory(parent):
            return False
        parent_identity = _directory_identity(parent)
        if parent_identity is None or not _full_safe_chain(parent):
            return False
        target_existed = path.exists()
        if target_existed:
            target_before = _identity(path)
            if target_before is None:
                return False
            if path.stat().st_size > MAX_MANIFEST_BYTES:
                return False
            previous_bytes = path.read_bytes()
        elif _reparse(path):
            return False
        handle, name = tempfile.mkstemp(prefix=".job-output-reservation-", suffix=".tmp", dir=parent)
        os.close(handle)
        temporary = Path(name)
        temporary_identity = _identity(temporary)
        if temporary_identity is None:
            return False
        with temporary.open("wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        if (
            _directory_identity(parent) is None
            or not _same_object(parent_identity, _directory_identity(parent))
            or not _full_safe_chain(parent)
            or _identity(temporary) is None
            or not _same_object(temporary_identity, _identity(temporary))
            or (target_existed and (_identity(path) is None or not _same_object(target_before, _identity(path))))
            or (not target_existed and (_reparse(path) or path.exists()))
        ):
            return False
        os.replace(temporary, path)
        temporary = None
        installed_identity = _identity(path)
        installed_bytes = path.read_bytes() if installed_identity is not None and path.stat().st_size <= MAX_MANIFEST_BYTES else None
        if (
            installed_identity is None
            or installed_bytes != encoded
            or _reparse(path)
            or not _full_safe_chain(parent)
        ):
            # If a previous manifest existed, make a best-effort atomic
            # restoration before reporting refusal.  When the target was
            # absent, leave an ambiguous foreign replacement untouched.
            if previous_bytes is not None and _full_safe_chain(parent) and _same_object(parent_identity, _directory_identity(parent)):
                restore_temp: Path | None = None
                restore_identity: dict[str, int] | None = None
                try:
                    handle, name = tempfile.mkstemp(prefix=".job-output-reservation-restore-", suffix=".tmp", dir=parent)
                    os.close(handle)
                    restore_temp = Path(name)
                    restore_identity = _identity(restore_temp)
                    if restore_identity is None:
                        return False
                    with restore_temp.open("wb") as stream:
                        stream.write(previous_bytes)
                        stream.flush()
                        os.fsync(stream.fileno())
                    if _identity(restore_temp) is not None and _same_object(restore_identity, _identity(restore_temp)) and _same_object(parent_identity, _directory_identity(parent)):
                        os.replace(restore_temp, path)
                        restore_temp = None
                except OSError:
                    pass
                finally:
                    if restore_temp is not None and restore_identity is not None:
                        _delete_identity_attested(restore_temp, restore_identity)
            return False
        return True
    except (OSError, TypeError, ValueError):
        return False
    finally:
        if temporary is not None:
            if temporary_identity is not None:
                _delete_identity_attested(temporary, temporary_identity)


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
        records = manifest.get("records")
        if type(records) is not dict or len(records) >= MAX_RESERVATIONS:
            return None
        reservation_id = f"output_res_{uuid.uuid4().hex}"
        parent = _reservation_parent()
        root = _reservation_root(reservation_id)
        try:
            if not _ensure_safe_directory(parent) or root.exists() or _reparse(parent):
                return None
            root.mkdir()
            root_identity = _directory_identity(root)
            if root_identity is None or not _full_safe_chain(root):
                return None
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
            "root_identity": root_identity,
            "allowed_paths": {},
            "allowed_directories": {},
        }
        manifest["records"][reservation_id] = record
        if not _save_manifest(manifest):
            _delete_identity_attested(root, root_identity, directory=True)
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
        if (
            not _full_safe_chain(_reservation_parent())
            or not _safe_chain(root, _reservation_parent())
            or _directory_identity(root) is None
            or not _same_object(record.get("root_identity"), _directory_identity(root))
            or len(record["allowed_paths"]) + len(record["allowed_directories"]) >= int(record["max_output_count"])
        ):
            raise ReservationError("OUTPUT_RESERVATION_UNAVAILABLE")
        target = root / relative
        if target.exists() or _reparse(target) or _directory_identity(target.parent) is None:
            raise ReservationError("OUTPUT_LEAF_ALREADY_EXISTS")
        try:
            with target.open("xb") as stream:
                stream.flush()
                os.fsync(stream.fileno())
            expected = _identity(target)
        except OSError as exc:
            raise ReservationError("OUTPUT_RESERVATION_UNAVAILABLE") from exc
        if expected is None:
            raise ReservationError("OUTPUT_RESERVATION_UNAVAILABLE")
        record["allowed_paths"][relative] = expected
        if not _save_manifest(manifest):
            _delete_identity_attested(target, expected)
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
        if (
            not _full_safe_chain(_reservation_parent())
            or not _safe_chain(root, _reservation_parent())
            or not _same_object(record.get("root_identity"), _directory_identity(root))
            or len(record["allowed_paths"]) + len(record["allowed_directories"]) >= int(record["max_output_count"])
            or directory.exists()
        ):
            raise ReservationError("OUTPUT_DIRECTORY_ALREADY_EXISTS")
        try:
            directory.mkdir()
            expected = _directory_identity(directory)
        except OSError as exc:
            raise ReservationError("OUTPUT_RESERVATION_UNAVAILABLE") from exc
        if expected is None:
            raise ReservationError("OUTPUT_RESERVATION_UNAVAILABLE")
        record["allowed_directories"][relative] = expected
        if not _save_manifest(manifest):
            _delete_identity_attested(directory, expected, directory=True)
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
    if safe is None or not _full_safe_chain(root) or not _safe_chain(absolute, root) or _reparse(absolute) or not absolute.is_file():
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


def _copy_reserved_file(source: Path, reservation_id: str, relative: str, expected: tuple[int, str, dict[str, int]]) -> tuple[Path, tuple[int, str, dict[str, int]]] | None:
    destination_root = artifact_store.OUTPUT_ROOT / ".hub-reserved" / reservation_id
    destination = destination_root / Path(relative).name
    temporary: Path | None = None
    parent_identity: dict[str, int] | None = None
    temporary_identity: dict[str, int] | None = None
    destination_root_identity: dict[str, int] | None = None
    destination_identity: dict[str, int] | None = None
    published = False
    try:
        output_root = artifact_store.OUTPUT_ROOT
        if not _full_safe_chain(output_root) or _directory_identity(output_root) is None:
            return None
        stage_parent = output_root / ".hub-reserved"
        if not _ensure_safe_directory(stage_parent):
            return None
        if destination_root.exists() or _reparse(destination_root):
            return None
        destination_root.mkdir()
        if not _full_safe_chain(destination_root) or destination.exists():
            return None
        parent_identity = _directory_identity(destination_root)
        destination_root_identity = parent_identity
        if parent_identity is None:
            return None
        initial = _hash_stable(source)
        if initial is None or initial != expected or not _full_safe_chain(source.parent) or not _safe_chain(source, source.parent) or _identity(source) is None:
            return None
        handle, name = tempfile.mkstemp(prefix=".reserved-output-", suffix=".tmp", dir=destination_root)
        os.close(handle)
        temporary = Path(name)
        temporary_identity = _identity(temporary)
        if temporary_identity is None:
            return None
        with _open_source_locked(source, expected) as source_stream:
            if source_stream is None:
                return None
            digest = hashlib.sha256()
            copied_bytes = 0
            with temporary.open("wb") as destination_stream:
                while True:
                    chunk = source_stream.read(1024 * 1024)
                    if not chunk:
                        break
                    copied_bytes += len(chunk)
                    if copied_bytes > MAX_OUTPUT_BYTES:
                        return None
                    digest.update(chunk)
                    destination_stream.write(chunk)
                destination_stream.flush()
                os.fsync(destination_stream.fileno())
            if copied_bytes != expected[0] or digest.hexdigest() != expected[1]:
                return None
            if (
                _directory_identity(destination_root) is None
                or not _same_object(parent_identity, _directory_identity(destination_root))
                or not _full_safe_chain(destination_root)
                or _identity(temporary) is None
                or not _same_object(temporary_identity, _identity(temporary))
                or destination.exists()
            ):
                return None
            os.replace(temporary, destination)
            temporary = None
            destination_identity = _identity(destination)
            if destination_identity is None or not _full_safe_chain(destination_root):
                return None
            published = True
            return destination, (expected[0], expected[1], destination_identity)
    except OSError:
        return None
    finally:
        if temporary is not None:
            if temporary_identity is not None:
                _delete_identity_attested(temporary, temporary_identity)
        if not published and destination_identity is not None and destination.exists():
            _delete_identity_attested(destination, destination_identity)
        if not published and destination_root_identity is not None and destination_root.exists():
            _delete_identity_attested(destination_root, destination_root_identity, directory=True)


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
        copied: list[tuple[Path, tuple[int, str, dict[str, int]]]] = []
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
            staged = _copy_reserved_file(candidate, reservation_id, relative, stable)
            if staged is None:
                record["state"] = "manual_review"
                _save_manifest(manifest)
                return {"status": "manual_review", "code": "OUTPUT_OWNERSHIP_UNPROVEN"}
            copied.append(staged)
        registration_failure = False
        artifacts: list[dict[str, Any]] | None = None
        with ExitStack() as locks:
            for staged_path, staged_expected in copied:
                stream = locks.enter_context(_open_source_locked(staged_path, staged_expected))
                if stream is None or not _stream_matches_expected(stream, staged_expected):
                    registration_failure = True
                    break
            if not registration_failure:
                try:
                    expected_outputs = {
                        str(path): {
                            "size_bytes": expected[0],
                            "sha256": expected[1],
                            "file_id": expected[2].get("file_id"),
                        }
                        for path, expected in copied
                    }
                    artifacts = artifact_store.register_worker_outputs(
                        [path for path, _expected in copied],
                        provenance=dict(provenance),
                        expected_outputs=expected_outputs,
                    )
                    if isinstance(artifacts, list):
                        for staged_path, staged_expected in copied:
                            if not _same_object(staged_expected[2], _identity(staged_path)):
                                registration_failure = True
                                break
                except (OSError, ValueError, TypeError):
                    registration_failure = True
        if registration_failure:
            for staged_path, staged_expected in copied:
                _delete_identity_attested(staged_path, staged_expected[2])
            record["state"] = "manual_review"
            _save_manifest(manifest)
            return {"status": "manual_review", "code": "OUTPUT_OWNERSHIP_UNPROVEN"}
        if not isinstance(artifacts, list) or len(artifacts) != len(copied):
            for staged_path, staged_expected in copied:
                _delete_identity_attested(staged_path, staged_expected[2])
            record["state"] = "manual_review"
            _save_manifest(manifest)
            return {"status": "manual_review", "code": "OUTPUT_OWNERSHIP_UNPROVEN"}
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
            if (
                not _full_safe_chain(_reservation_parent())
                or not _safe_chain(root, _reservation_parent())
                or not _same_object(record.get("root_identity"), _directory_identity(root))
            ):
                ambiguous = True
            else:
                allowed = dict(record["allowed_paths"])
                directories = dict(record["allowed_directories"])
                files_to_remove: list[tuple[Path, dict[str, int]]] = []
                directories_to_remove: list[tuple[Path, dict[str, int]]] = []
                for relative, expected in allowed.items():
                    candidate = root / relative
                    current = _identity(candidate)
                    if current is None or not _same_object(expected, current):
                        ambiguous = True
                    else:
                        files_to_remove.append((candidate, expected))
                for relative, expected in directories.items():
                    directory = root / relative
                    current = _directory_identity(directory)
                    if current is None or not _same_object(expected, current):
                        ambiguous = True
                    else:
                        directories_to_remove.append((directory, expected))
                for current, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
                    current_path = Path(current)
                    if _reparse(current_path):
                        ambiguous = True
                        dirnames[:] = []
                        continue
                    dirnames[:] = [name for name in dirnames if not _reparse(current_path / name)]
                    if len(dirnames) > MAX_OUTPUT_COUNT or len(filenames) > MAX_OUTPUT_COUNT:
                        ambiguous = True
                    for name in filenames:
                        candidate = current_path / name
                        relative = _safe_relative(candidate.relative_to(root).as_posix())
                        if relative not in allowed:
                            ambiguous = True
                    for name in dirnames:
                        candidate = current_path / name
                        relative = _safe_relative(candidate.relative_to(root).as_posix())
                        if relative not in directories:
                            ambiguous = True
                if not ambiguous:
                    for candidate, expected in files_to_remove:
                        if not _delete_identity_attested(candidate, expected):
                            ambiguous = True
                            break
                        removed += 1
                if not ambiguous:
                    for directory, expected in sorted(directories_to_remove, key=lambda item: len(item[0].parts), reverse=True):
                        if not _delete_identity_attested(directory, expected, directory=True):
                            ambiguous = True
                            break
                if not ambiguous and not _delete_identity_attested(root, record["root_identity"], directory=True):
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
