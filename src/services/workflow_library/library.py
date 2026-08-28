"""Atomic, revision-safe persistence for the V5 Workflow Library.

This module is intentionally a pure metadata adapter.  It never starts a
worker, imports a graph engine, invokes a subprocess, accesses a device, or
resolves a user media/model path.

The production leaf is Config/workflow_library.json.  A test-root seam is
kept for bounded fixtures, but every root, ancestor, parent, leaf and
temporary file is validated with no-follow lstat evidence before it is used.
Resolved containment is only a secondary check after the no-follow chain has
been accepted.
"""

from __future__ import annotations

import copy
import json
import os
import stat
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from src.shared.paths.registry import CONFIG_ROOT
from src.shared.schemas.workflow_library import (
    MIGRATION_PLAN_SCHEMA_VERSION,
    WORKFLOW_ENTRY_SCHEMA_VERSION,
    WORKFLOW_LIBRARY_SCHEMA_VERSION,
    canonical_workflow_library,
    library_fingerprint,
    plan_localstorage_migration,
    safe_import_workflow_library,
    validate_workflow_entry,
    validate_workflow_library,
)


DEFAULT_LIBRARY_PATH = CONFIG_ROOT / "workflow_library.json"
_REPARSE_ATTRIBUTE = 0x400
_PATH_LOCKS: dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _lexical_path(path: os.PathLike[str] | str) -> Path:
    """Normalize a path without following a symlink or reparse point."""

    return Path(os.path.normpath(os.path.abspath(os.fspath(path))))


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.normpath(os.fspath(path)))


def _same_path(left: Path, right: Path) -> bool:
    return _path_key(left) == _path_key(right)


def _lexically_contained(anchor: Path, target: Path) -> bool:
    try:
        return os.path.commonpath((_path_key(anchor), _path_key(target))) == _path_key(anchor)
    except (OSError, ValueError):
        return False


def _resolved_contained(anchor: Path, target: Path) -> bool:
    """Use resolved containment only after no-follow validation."""

    try:
        anchor_value = os.path.normcase(os.path.realpath(os.fspath(anchor)))
        target_value = os.path.normcase(os.path.realpath(os.fspath(target)))
        return os.path.commonpath((anchor_value, target_value)) == anchor_value
    except (OSError, ValueError):
        return False


def _is_reparse(value: os.stat_result) -> bool:
    return stat.S_ISLNK(value.st_mode) or bool(getattr(value, "st_file_attributes", 0) & _REPARSE_ATTRIBUTE)


@dataclass(frozen=True)
class _Identity:
    kind: str
    device: int
    inode: int
    mode: int
    size: int
    mtime_ns: int
    ctime_ns: int
    reparse: bool


def _identity_key(value: _Identity | None) -> tuple[Any, ...] | None:
    if value is None:
        return None
    base = (value.kind, value.device, value.inode, value.mode, value.reparse)
    if value.kind == "directory":
        return base
    return base + (value.size, value.mtime_ns, value.ctime_ns)


def _same_identity(left: _Identity | None, right: _Identity | None) -> bool:
    return _identity_key(left) == _identity_key(right)


def _same_object(left: _Identity | None, right: _Identity | None) -> bool:
    if left is None or right is None:
        return left is right
    return (left.kind, left.device, left.inode, left.mode, left.reparse) == (
        right.kind,
        right.device,
        right.inode,
        right.mode,
        right.reparse,
    )


def _safe_identity(path: Path, *, kind: str | None = None) -> tuple[_Identity | None, str | None]:
    """Read one no-follow identity and return only a finite internal code."""

    try:
        value = os.lstat(path)
    except FileNotFoundError:
        return None, "missing"
    except OSError:
        return None, "lstat_failed"
    if _is_reparse(value):
        return None, "reparse"
    if kind == "directory" and not stat.S_ISDIR(value.st_mode):
        return None, "wrong_type"
    if kind == "file" and not stat.S_ISREG(value.st_mode):
        return None, "wrong_type"
    actual_kind = "directory" if stat.S_ISDIR(value.st_mode) else "file" if stat.S_ISREG(value.st_mode) else "other"
    return _Identity(
        kind=actual_kind,
        device=int(getattr(value, "st_dev", 0)),
        inode=int(getattr(value, "st_ino", 0)),
        mode=int(value.st_mode),
        size=int(getattr(value, "st_size", 0)),
        mtime_ns=int(getattr(value, "st_mtime_ns", 0)),
        ctime_ns=int(getattr(value, "st_ctime_ns", 0)),
        reparse=False,
    ), None


def _location_code(stage: str, detail: str) -> str:
    suffix = {
        "missing": "missing",
        "reparse": "reparse",
        "lstat_failed": "lstat_failed",
        "wrong_type": "invalid",
    }.get(detail, "invalid")
    return f"workflow_library_{stage}_{suffix}"


@dataclass(frozen=True)
class _LocationGuard:
    root: Path
    parent: Path
    path: Path
    target_identity: _Identity | None
    directory_chain: tuple[tuple[str, _Identity], ...]


@dataclass(frozen=True)
class _TempGuard:
    path: Path
    parent_identity: _Identity
    file_identity: _Identity


def _same_location(left: _LocationGuard, right: _LocationGuard) -> bool:
    if not _same_location_container(left, right) or not _same_path(left.path, right.path):
        return False
    if not _same_identity(left.target_identity, right.target_identity):
        return False
    left_chain = tuple((key, _identity_key(identity)) for key, identity in left.directory_chain)
    right_chain = tuple((key, _identity_key(identity)) for key, identity in right.directory_chain)
    return left_chain == right_chain


def _same_location_container(left: _LocationGuard, right: _LocationGuard) -> bool:
    if not _same_path(left.root, right.root) or not _same_path(left.parent, right.parent):
        return False
    left_chain = tuple((key, _identity_key(identity)) for key, identity in left.directory_chain)
    right_chain = tuple((key, _identity_key(identity)) for key, identity in right.directory_chain)
    return left_chain == right_chain


def _directory_chain(path: Path, root: Path) -> tuple[list[tuple[str, _Identity]], str | None]:
    """Validate every directory from the target parent through the root."""

    current = _lexical_path(path)
    root = _lexical_path(root)
    chain: list[tuple[str, _Identity]] = []
    while True:
        if not _lexically_contained(root, current):
            return [], "workflow_library_path_escape"
        identity, detail = _safe_identity(current, kind="directory")
        if identity is None:
            stage = "root" if _same_path(current, root) else "parent" if _same_path(current, path) else "ancestor"
            return [], _location_code(stage, detail or "invalid")
        chain.append((_path_key(current), identity))
        if _same_path(current, root):
            break
        parent = _lexical_path(current.parent)
        if _same_path(parent, current):
            return [], "workflow_library_ancestor_invalid"
        current = parent

    # Validate ancestors above the fixed root as well.  This never grants
    # authority to an outside path; it rejects an unsafe chain before use.
    current = _lexical_path(root.parent)
    while True:
        identity, detail = _safe_identity(current, kind="directory")
        if identity is None:
            return [], _location_code("ancestor", detail or "invalid")
        chain.append((_path_key(current), identity))
        if _same_path(current, current.parent):
            break
        current = _lexical_path(current.parent)
    return chain, None


def _location_guard(root: Path, target: Path, *, allow_missing_leaf: bool = True) -> tuple[_LocationGuard | None, str | None]:
    root = _lexical_path(root)
    target = _lexical_path(target)
    if not _lexically_contained(root, target):
        return None, "workflow_library_path_escape"
    chain, error = _directory_chain(target.parent, root)
    if error is not None:
        return None, error
    target_identity, detail = _safe_identity(target, kind="file")
    if target_identity is None and detail != "missing":
        return None, _location_code("target", detail or "invalid")
    if target_identity is None and not allow_missing_leaf:
        return None, "workflow_library_target_missing"
    if not _resolved_contained(root, target):
        return None, "workflow_library_path_escape"
    return _LocationGuard(
        root=root,
        parent=target.parent,
        path=target,
        target_identity=target_identity,
        directory_chain=tuple(chain),
    ), None


def _temp_guard(path: Path, location: _LocationGuard) -> _TempGuard | None:
    path = _lexical_path(path)
    if not _lexically_contained(location.parent, path) or not _resolved_contained(location.parent, path):
        return None
    parent_identity, parent_detail = _safe_identity(location.parent, kind="directory")
    if parent_identity is None or parent_detail is not None:
        return None
    if not _same_identity(parent_identity, dict(location.directory_chain).get(_path_key(location.parent))):
        return None
    file_identity, file_detail = _safe_identity(path, kind="file")
    if file_identity is None or file_detail is not None:
        return None
    return _TempGuard(path=path, parent_identity=parent_identity, file_identity=file_identity)


def _remove_temp_safe(temp: Path, location: _LocationGuard, expected: _Identity | None) -> None:
    """Remove only a task-created regular temp whose identity is unchanged."""

    current = _temp_guard(temp, location)
    if current is None or expected is None or not _same_identity(current.file_identity, expected):
        return
    try:
        current.path.unlink()
    except OSError:
        return


def _fsync_directory(path: Path) -> bool:
    """Fsync a directory where the platform exposes that operation."""

    if os.name == "nt":
        return True
    descriptor: int | None = None
    try:
        descriptor = os.open(os.fspath(path), os.O_RDONLY)
        os.fsync(descriptor)
        return True
    except OSError:
        return False
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _shared_path_lock(path: Path) -> threading.RLock:
    key = _path_key(_lexical_path(path))
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_library() -> dict[str, Any]:
    return {
        "schema_version": WORKFLOW_LIBRARY_SCHEMA_VERSION,
        "library_revision": 0,
        "workflows": [],
    }


def _copy(value: Any) -> Any:
    return copy.deepcopy(value)


def _error(code: str, action: str) -> dict[str, str]:
    messages = {
        "revision_conflict": "Workflow Library đã thay đổi từ lần đọc trước.",
        "recovery_required": "Workflow Library local cần recovery trước khi ghi.",
        "not_found": "Workflow không tồn tại trong Library.",
        "invalid": "Workflow Library payload không hợp lệ.",
        "duplicate_id": "Workflow ID đã tồn tại.",
        "write_failed": "Không thể ghi Workflow Library local.",
    }
    return {"code": code, "message": messages.get(code, "Workflow Library operation không thành công."), "action": action}


def _recovery(code: str | None = None) -> dict[str, str]:
    # code is intentionally not returned: public diagnostics must not reflect
    # filesystem names, exception text or temp identifiers.
    _ = code
    return {
        "status": "recovery_required",
        "reason": "Workflow Library storage requires manual review; no overwrite was attempted.",
        "action": "Review the managed Workflow Library location before retrying.",
    }


class WorkflowLibraryStore:
    """Thread-safe JSON store with optimistic library-level revisions."""

    def __init__(self, path: Path = DEFAULT_LIBRARY_PATH, *, root: Path | None = None) -> None:
        raw_path = _lexical_path(path)
        default_path = _lexical_path(DEFAULT_LIBRARY_PATH)
        self.path = raw_path
        self.root = _lexical_path(root if root is not None else CONFIG_ROOT if _same_path(raw_path, default_path) else raw_path.parent)
        self._lock = threading.RLock()

    def _location(self, *, allow_missing_leaf: bool = True) -> tuple[_LocationGuard | None, str | None]:
        return _location_guard(self.root, self.path, allow_missing_leaf=allow_missing_leaf)

    @contextmanager
    def _locked(self) -> Iterator[tuple[_LocationGuard | None, str | None]]:
        initial, code = self._location()
        if initial is None:
            yield None, code
            return
        shared_lock = _shared_path_lock(initial.path)
        with shared_lock, self._lock:
            current, code = self._location()
            yield current, code

    def _read_with_bytes(self, location: _LocationGuard) -> tuple[dict[str, Any], dict[str, Any], bytes | None]:
        if location.target_identity is None:
            return _default_library(), {
                "status": "clean",
                "reason": "Workflow Library file is absent.",
                "action": "Create or import a validated workflow.",
            }, None
        try:
            source_bytes = location.path.read_bytes()
        except (OSError, UnicodeError):
            return _default_library(), _recovery("workflow_library_read_failed"), None
        current, code = self._location(allow_missing_leaf=False)
        if current is None or code is not None or not _same_location(location, current):
            return _default_library(), _recovery(code), source_bytes
        try:
            result = safe_import_workflow_library(source_bytes)
        except (OSError, UnicodeError, TypeError, ValueError):
            result = {"accepted": False}
        if not result.get("accepted"):
            return _default_library(), _recovery("workflow_library_payload_invalid"), source_bytes
        return result["library"], {
            "status": "clean",
            "reason": "Workflow Library is valid.",
            "action": "Continue edits with the expected revision.",
        }, source_bytes

    def _read(self) -> tuple[dict[str, Any], dict[str, Any]]:
        with self._locked() as (location, code):
            if location is None:
                return _default_library(), _recovery(code)
            library, recovery, _ = self._read_with_bytes(location)
            return library, recovery

    def snapshot(self) -> dict[str, Any]:
        with self._locked() as (location, code):
            if location is None:
                return {"library": _default_library(), "recovery": _copy(_recovery(code))}
            library, recovery, _ = self._read_with_bytes(location)
            return {"library": _copy(library), "recovery": _copy(recovery)}

    def list_workflows(self) -> dict[str, Any]:
        snapshot = self.snapshot()
        library = snapshot["library"]
        return {
            "status": "partial" if snapshot["recovery"]["status"] != "clean" else "ready",
            "schema_version": WORKFLOW_LIBRARY_SCHEMA_VERSION,
            "library_revision": library["library_revision"],
            "workflows": _copy(library["workflows"]),
            "recent_workflows": _copy(sorted(
                library["workflows"],
                key=lambda item: (str(item.get("last_opened_at") or ""), str(item.get("updated_at") or ""), str(item.get("id") or "")),
                reverse=True,
            )[:12]),
            "recovery": snapshot["recovery"],
        }

    def get_workflow(self, workflow_id: str) -> dict[str, Any]:
        if not isinstance(workflow_id, str):
            return {"status": "not_found", "workflow": None, "errors": [_error("not_found", "Chọn workflow ID hợp lệ rồi thử lại.")]}
        snapshot = self.snapshot()
        if snapshot["recovery"]["status"] != "clean":
            return {"status": "recovery_required", "workflow": None, "errors": [_error("recovery_required", snapshot["recovery"]["action"])]}
        workflow = next((item for item in snapshot["library"]["workflows"] if item["id"] == workflow_id), None)
        if workflow is None:
            return {"status": "not_found", "workflow": None, "errors": [_error("not_found", "Chọn workflow khác hoặc import record đã validate.")]}
        return {"status": "ready", "workflow": _copy(workflow), "library_revision": snapshot["library"]["library_revision"]}

    def _atomic_write(
        self,
        library: dict[str, Any],
        *,
        expected_bytes: bytes | None = None,
        expected_guard: _LocationGuard | None = None,
    ) -> str:
        initial, code = self._location()
        if initial is None or code is not None:
            return "recovery"
        guard = expected_guard or initial
        if expected_guard is not None and not _same_location(expected_guard, initial):
            if not _same_location_container(expected_guard, initial) or initial.target_identity is None:
                return "recovery"
            try:
                current_bytes = initial.path.read_bytes()
            except (OSError, UnicodeError):
                return "recovery"
            reread, reread_code = self._location(allow_missing_leaf=False)
            if reread is None or reread_code is not None or not _same_location(initial, reread):
                return "recovery"
            return "conflict" if current_bytes != expected_bytes else "recovery"
        temporary: Path | None = None
        temporary_identity: _Identity | None = None
        try:
            payload = (canonical_workflow_library(library) + "\n").encode("utf-8")
            before = self._location()
            if before[0] is None or before[1] is not None or not _same_location(guard, before[0]):
                return "recovery"
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=os.fspath(guard.parent),
                prefix=".workflow-library-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = _lexical_path(handle.name)
                temp_guard = _temp_guard(temporary, guard)
                if temp_guard is None:
                    return "recovery"
                created_temp_identity = temp_guard.file_identity
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            after_temp = _temp_guard(temporary, guard)
            if after_temp is None or not _same_object(created_temp_identity, after_temp.file_identity):
                return "recovery"
            temporary_identity = after_temp.file_identity

            current, current_code = self._location()
            if current is None or current_code is not None or not _same_location(guard, current):
                return "recovery"
            current_bytes: bytes | None = None
            if current.target_identity is not None:
                try:
                    current_bytes = current.path.read_bytes()
                except (OSError, UnicodeError):
                    return "recovery"
                reread, reread_code = self._location(allow_missing_leaf=False)
                if reread is None or reread_code is not None or not _same_location(current, reread):
                    return "recovery"
            if current_bytes != expected_bytes:
                return "conflict"

            before_replace, replace_code = self._location()
            if before_replace is None or replace_code is not None or not _same_location(guard, before_replace):
                return "recovery"
            final_temp = _temp_guard(temporary, before_replace)
            if final_temp is None or not _same_identity(temporary_identity, final_temp.file_identity):
                return "recovery"
            os.replace(temporary, self.path)
            temporary = None
            if not _fsync_directory(guard.parent):
                return "recovery"
            published, published_code = self._location(allow_missing_leaf=False)
            if published is None or published_code is not None:
                return "recovery"
            return "written"
        except (OSError, ValueError, TypeError):
            return "error"
        finally:
            if temporary is not None:
                _remove_temp_safe(temporary, guard, temporary_identity)

    @staticmethod
    def _prepare_workflow(workflow: object, *, revision: int) -> dict[str, Any] | None:
        if not isinstance(workflow, dict):
            return None
        value = _copy(workflow)
        value.setdefault("schema_version", WORKFLOW_ENTRY_SCHEMA_VERSION)
        value.setdefault("description", "")
        value.setdefault("status", "draft")
        value.setdefault("source", "local")
        value.setdefault("tags", [])
        value.setdefault("favorite", False)
        value.setdefault("last_opened_at", "")
        value.setdefault("created_at", _now())
        value["updated_at"] = _now()
        value["revision"] = revision
        result = validate_workflow_entry(value)
        return result["workflow"] if result["valid"] else None

    def save_workflow(self, workflow: object, *, expected_revision: int | None = None) -> dict[str, Any]:
        with self._locked() as (location, code):
            if location is None:
                return {"accepted": False, "status": "recovery_required", "errors": [_error("recovery_required", "Review the managed Workflow Library location before retrying.")]}
            library, recovery, source_bytes = self._read_with_bytes(location)
            if recovery["status"] != "clean":
                return {"accepted": False, "status": "recovery_required", "errors": [_error("recovery_required", recovery["action"])]}
            current_revision = library["library_revision"]
            if expected_revision is not None and expected_revision != current_revision:
                return {"accepted": False, "status": "conflict", "library_revision": current_revision, "errors": [_error("revision_conflict", "Tải lại Library, xem diff rồi xác nhận ghi revision mới.")]}
            if not isinstance(workflow, dict) or not isinstance(workflow.get("id"), str):
                return {"accepted": False, "status": "invalid", "errors": [_error("invalid", "Sửa workflow theo schema rồi thử lại.")]}
            existing = next((item for item in library["workflows"] if item["id"] == workflow["id"]), None)
            next_revision = current_revision + 1
            entry_revision = int(existing["revision"]) + 1 if existing else 1
            candidate = _copy(workflow)
            if existing is not None:
                candidate.setdefault("favorite", existing.get("favorite", False))
                candidate.setdefault("last_opened_at", existing.get("last_opened_at", ""))
            prepared = self._prepare_workflow(candidate, revision=entry_revision)
            if prepared is None:
                return {"accepted": False, "status": "invalid", "errors": [_error("invalid", "Sửa workflow theo schema rồi thử lại.")]}
            workflows = [item for item in library["workflows"] if item["id"] != prepared["id"]]
            workflows.append(prepared)
            next_library = {"schema_version": WORKFLOW_LIBRARY_SCHEMA_VERSION, "library_revision": next_revision, "workflows": workflows}
            write_status = self._atomic_write(next_library, expected_bytes=source_bytes, expected_guard=location)
            if write_status == "conflict":
                return {"accepted": False, "status": "conflict", "library_revision": current_revision, "errors": [_error("revision_conflict", "Reload the Library and confirm the newer revision.")]}
            if write_status == "recovery":
                return {"accepted": False, "status": "recovery_required", "errors": [_error("recovery_required", "Keep newer data and verify local recovery before retrying.")]}
            if write_status != "written":
                return {"accepted": False, "status": "error", "errors": [_error("write_failed", "Kiểm tra local recovery rồi thử lại; dữ liệu cũ vẫn được giữ.")]}
            return {"accepted": True, "status": "ready", "library_revision": next_revision, "workflow": _copy(prepared)}

    upsert = save_workflow

    def set_favorite(self, workflow_id: str, favorite: object, *, expected_revision: int | None = None) -> dict[str, Any]:
        """Persist one explicit favorite marker without touching its graph."""

        if not isinstance(workflow_id, str) or type(favorite) is not bool:
            return {"accepted": False, "status": "invalid", "errors": [_error("invalid", "Chọn workflow và trạng thái favorite hợp lệ.")]}
        with self._locked() as (location, code):
            if location is None:
                return {"accepted": False, "status": "recovery_required", "errors": [_error("recovery_required", "Review the managed Workflow Library location before retrying.")]}
            library, recovery, source_bytes = self._read_with_bytes(location)
            if recovery["status"] != "clean":
                return {"accepted": False, "status": "recovery_required", "errors": [_error("recovery_required", recovery["action"])]}
            if expected_revision is not None and expected_revision != library["library_revision"]:
                return {"accepted": False, "status": "conflict", "library_revision": library["library_revision"], "errors": [_error("revision_conflict", "Tải lại Library trước khi cập nhật favorite.")]}
            existing = next((item for item in library["workflows"] if item["id"] == workflow_id), None)
            if existing is None:
                return {"accepted": False, "status": "not_found", "errors": [_error("not_found", "Chọn workflow tồn tại trước khi cập nhật favorite.")]}
            next_entry = _copy(existing)
            next_entry["favorite"] = favorite
            next_entry["updated_at"] = _now()
            next_entry["revision"] = int(existing["revision"]) + 1
            workflows = [next_entry if item["id"] == workflow_id else item for item in library["workflows"]]
            next_library = {"schema_version": WORKFLOW_LIBRARY_SCHEMA_VERSION, "library_revision": library["library_revision"] + 1, "workflows": workflows}
            write_status = self._atomic_write(next_library, expected_bytes=source_bytes, expected_guard=location)
            if write_status == "written":
                return {"accepted": True, "status": "ready", "library_revision": next_library["library_revision"], "workflow": _copy(next_entry)}
            if write_status == "conflict":
                return {"accepted": False, "status": "conflict", "library_revision": library["library_revision"], "errors": [_error("revision_conflict", "Tải lại Library trước khi cập nhật favorite.")]}
            return {"accepted": False, "status": "recovery_required" if write_status == "recovery" else "error", "errors": [_error("recovery_required" if write_status == "recovery" else "write_failed", "Giữ dữ liệu hiện có và kiểm tra recovery trước khi thử lại.")]}

    def mark_opened(self, workflow_id: str, *, expected_revision: int | None = None) -> dict[str, Any]:
        """Record an explicit library-open event; GET remains side-effect free."""

        if not isinstance(workflow_id, str):
            return {"accepted": False, "status": "invalid", "errors": [_error("invalid", "Chọn workflow hợp lệ trước khi đánh dấu gần đây.")]}
        with self._locked() as (location, code):
            if location is None:
                return {"accepted": False, "status": "recovery_required", "errors": [_error("recovery_required", "Review the managed Workflow Library location before retrying.")]}
            library, recovery, source_bytes = self._read_with_bytes(location)
            if recovery["status"] != "clean":
                return {"accepted": False, "status": "recovery_required", "errors": [_error("recovery_required", recovery["action"])]}
            if expected_revision is not None and expected_revision != library["library_revision"]:
                return {"accepted": False, "status": "conflict", "library_revision": library["library_revision"], "errors": [_error("revision_conflict", "Tải lại Library trước khi đánh dấu workflow gần đây.")]}
            existing = next((item for item in library["workflows"] if item["id"] == workflow_id), None)
            if existing is None:
                return {"accepted": False, "status": "not_found", "errors": [_error("not_found", "Chọn workflow tồn tại trước khi đánh dấu gần đây.")]}
            next_entry = _copy(existing)
            now = _now()
            next_entry["last_opened_at"] = now
            next_entry["updated_at"] = now
            next_entry["revision"] = int(existing["revision"]) + 1
            workflows = [next_entry if item["id"] == workflow_id else item for item in library["workflows"]]
            next_library = {"schema_version": WORKFLOW_LIBRARY_SCHEMA_VERSION, "library_revision": library["library_revision"] + 1, "workflows": workflows}
            write_status = self._atomic_write(next_library, expected_bytes=source_bytes, expected_guard=location)
            if write_status == "written":
                return {"accepted": True, "status": "ready", "library_revision": next_library["library_revision"], "workflow": _copy(next_entry)}
            if write_status == "conflict":
                return {"accepted": False, "status": "conflict", "library_revision": library["library_revision"], "errors": [_error("revision_conflict", "Tải lại Library trước khi đánh dấu workflow gần đây.")]}
            return {"accepted": False, "status": "recovery_required" if write_status == "recovery" else "error", "errors": [_error("recovery_required" if write_status == "recovery" else "write_failed", "Giữ dữ liệu hiện có và kiểm tra recovery trước khi thử lại.")]}

    def delete_workflow(self, workflow_id: str, *, expected_revision: int | None = None) -> dict[str, Any]:
        with self._locked() as (location, code):
            if location is None:
                return {"accepted": False, "status": "recovery_required", "errors": [_error("recovery_required", "Review the managed Workflow Library location before retrying.")]}
            library, recovery, source_bytes = self._read_with_bytes(location)
            if recovery["status"] != "clean":
                return {"accepted": False, "status": "recovery_required", "errors": [_error("recovery_required", recovery["action"])]}
            if expected_revision is not None and expected_revision != library["library_revision"]:
                return {"accepted": False, "status": "conflict", "errors": [_error("revision_conflict", "Tải lại Library trước khi xóa.")]}
            if not any(item["id"] == workflow_id for item in library["workflows"]):
                return {"accepted": False, "status": "not_found", "errors": [_error("not_found", "Không xóa gì; chọn workflow ID tồn tại.")]}
            next_library = {
                "schema_version": WORKFLOW_LIBRARY_SCHEMA_VERSION,
                "library_revision": library["library_revision"] + 1,
                "workflows": [item for item in library["workflows"] if item["id"] != workflow_id],
            }
            write_status = self._atomic_write(next_library, expected_bytes=source_bytes, expected_guard=location)
            if write_status == "conflict":
                return {"accepted": False, "status": "conflict", "library_revision": library["library_revision"], "errors": [_error("revision_conflict", "Reload the Library before deleting.")]}
            if write_status == "recovery":
                return {"accepted": False, "status": "recovery_required", "errors": [_error("recovery_required", "Keep newer data and verify local recovery before retrying.")]}
            if write_status != "written":
                return {"accepted": False, "status": "error", "errors": [_error("write_failed", "Dữ liệu cũ vẫn được giữ; thử lại sau.")]}
            return {"accepted": True, "status": "ready", "library_revision": next_library["library_revision"]}

    def import_json(self, payload: str | bytes, *, expected_revision: int | None = None) -> dict[str, Any]:
        imported = safe_import_workflow_library(payload)
        if not imported.get("accepted"):
            return {"accepted": False, "status": "invalid", "errors": imported.get("errors", [_error("invalid", "Sửa JSON theo schema rồi thử lại.")])}
        with self._locked() as (location, code):
            if location is None:
                return {"accepted": False, "status": "recovery_required", "errors": [_error("recovery_required", "Review the managed Workflow Library location before retrying.")]}
            library, recovery, source_bytes = self._read_with_bytes(location)
            if recovery["status"] != "clean":
                return {"accepted": False, "status": "recovery_required", "errors": [_error("recovery_required", recovery["action"])]}
            if expected_revision is not None and expected_revision != library["library_revision"]:
                return {"accepted": False, "status": "conflict", "errors": [_error("revision_conflict", "Tải lại Library trước khi import.")]}
            next_library = imported["library"]
            next_library["library_revision"] = library["library_revision"] + 1
            write_status = self._atomic_write(next_library, expected_bytes=source_bytes, expected_guard=location)
            if write_status == "conflict":
                return {"accepted": False, "status": "conflict", "library_revision": library["library_revision"], "errors": [_error("revision_conflict", "Reload the Library before importing.")]}
            if write_status == "recovery":
                return {"accepted": False, "status": "recovery_required", "errors": [_error("recovery_required", "Keep newer data and verify local recovery before retrying.")]}
            if write_status != "written":
                return {"accepted": False, "status": "error", "errors": [_error("write_failed", "Dữ liệu cũ vẫn được giữ; thử lại sau.")]}
            return {"accepted": True, "status": "ready", "library_revision": next_library["library_revision"], "workflows": _copy(next_library["workflows"])}

    def export_json(self) -> dict[str, Any]:
        snapshot = self.snapshot()
        if snapshot["recovery"]["status"] != "clean":
            return {"ready": False, "status": "recovery_required", "errors": [_error("recovery_required", snapshot["recovery"]["action"])]}
        content = canonical_workflow_library(snapshot["library"])
        fingerprint = library_fingerprint(snapshot["library"])
        return {"ready": True, "status": "ready", "content": content, "fingerprint": fingerprint, "library_revision": snapshot["library"]["library_revision"]}

    def plan_migration(self, entries: object) -> dict[str, Any]:
        return plan_localstorage_migration(entries)

    def confirm_migration(self, entries: object, *, expected_revision: int | None = None) -> dict[str, Any]:
        """Explicitly apply only a previously reviewed, validated migration."""

        plan = plan_localstorage_migration(entries)
        if plan["status"] != "ready":
            return {"accepted": False, "status": plan["status"], "plan": plan}
        snapshot = self.snapshot()
        if expected_revision is not None and expected_revision != snapshot["library"]["library_revision"]:
            return {"accepted": False, "status": "conflict", "plan": plan, "errors": [_error("revision_conflict", "Tải lại Library rồi xác nhận migration lại.")]}
        imported = {
            "schema_version": WORKFLOW_LIBRARY_SCHEMA_VERSION,
            "library_revision": snapshot["library"]["library_revision"],
            "workflows": list(entries),
        }
        result = self.import_json(json.dumps(imported, ensure_ascii=False), expected_revision=snapshot["library"]["library_revision"])
        result["plan"] = plan
        return result


def canonical_export(library: dict[str, Any]) -> dict[str, Any]:
    result = validate_workflow_library(library)
    if not result["valid"]:
        return {"ready": False, "errors": result["errors"]}
    content = canonical_workflow_library(result["library"])
    return {"ready": True, "status": "ready", "content": content, "fingerprint": library_fingerprint(result["library"])}


def safe_import(payload: str | bytes) -> dict[str, Any]:
    return safe_import_workflow_library(payload)


def migration_plan(entries: object) -> dict[str, Any]:
    return plan_localstorage_migration(entries)


__all__ = [
    "DEFAULT_LIBRARY_PATH",
    "WorkflowLibraryStore",
    "canonical_export",
    "safe_import",
    "migration_plan",
]
