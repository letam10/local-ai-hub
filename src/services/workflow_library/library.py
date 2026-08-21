"""Atomic, revision-safe persistence for the V5 Workflow Library.

This module is intentionally a pure metadata adapter.  It never starts a
worker, imports a graph engine, invokes a subprocess, accesses a device, or
resolves a user media/model path.
"""

from __future__ import annotations

import copy
import json
import os
import stat
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.platform.paths import is_reparse_point
from src.shared.paths.registry import CONFIG_ROOT
from src.shared.schemas.workflow_library import (
    MAX_LIBRARY_BYTES,
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
_PATH_LOCKS: dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()
_PathIdentity = tuple[str, int, int, int, int, int]


class _LocationChanged(OSError):
    """Private signal for a fixed library target changing during an operation."""


def _lexical_path(value: Path | str) -> Path:
    """Normalize a path lexically without resolving symlinks or junctions."""

    return Path(os.path.abspath(os.fspath(value)))


def _path_key(path: Path) -> str:
    """Return a lock key without using a resolved filesystem target."""

    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _has_parent_segment(value: Path | str) -> bool:
    raw = os.fspath(value).replace("\\", "/")
    return any(part == ".." for part in raw.split("/"))


def _identity_signature(path: Path, *, expected_kind: str | None = None) -> tuple[_PathIdentity | None, str | None]:
    """Read one bounded lstat identity without following a reparse target."""

    try:
        if is_reparse_point(path):
            return None, "workflow_library_reparse"
        value = path.lstat()
    except FileNotFoundError:
        return None, "workflow_library_missing"
    except OSError:
        return None, "workflow_library_lstat_failed"
    if stat.S_ISREG(value.st_mode):
        kind = "file"
    elif stat.S_ISDIR(value.st_mode):
        kind = "directory"
    else:
        kind = "other"
    if expected_kind is not None and kind != expected_kind:
        return None, "workflow_library_parent_invalid" if expected_kind == "directory" else "workflow_library_target_invalid"
    size = 0 if kind == "directory" else int(value.st_size)
    mtime_ns = 0 if kind == "directory" else int(value.st_mtime_ns)
    ctime_ns = 0 if kind == "directory" else int(getattr(value, "st_ctime_ns", 0))
    return (
        kind,
        int(getattr(value, "st_dev", 0)),
        int(getattr(value, "st_ino", 0)),
        size,
        mtime_ns,
        ctime_ns,
    ), None


def _validate_directory_chain(parent: Path, boundary: Path) -> tuple[dict[str, _PathIdentity] | None, str | None]:
    """Validate the complete existing parent chain, including the boundary."""

    parent = _lexical_path(parent)
    boundary = _lexical_path(boundary)
    try:
        parent.relative_to(boundary)
    except ValueError:
        return None, "workflow_library_path_escape"
    identities: dict[str, _PathIdentity] = {}
    current = parent
    reached_boundary = False
    while True:
        identity, code = _identity_signature(current, expected_kind="directory")
        if code is not None:
            return None, "workflow_library_parent_missing" if code == "workflow_library_missing" else code
        assert identity is not None
        identities[_path_key(current)] = identity
        if _path_key(current) == _path_key(boundary):
            reached_boundary = True
        if current.parent == current:
            break
        current = current.parent
    if not reached_boundary:
        return None, "workflow_library_path_escape"
    return identities, None


def _validate_location(path: Path, boundary: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Validate target, parent and all ancestors without resolving reparses."""

    parent_identities, code = _validate_directory_chain(path.parent, boundary)
    if code is not None or parent_identities is None:
        return None, code or "workflow_library_location_unavailable"
    target_identity, target_code = _identity_signature(path)
    if target_code not in {None, "workflow_library_missing"}:
        return None, target_code
    parent_identity = parent_identities.get(_path_key(path.parent))
    if parent_identity is None:
        return None, "workflow_library_parent_invalid"
    return {"parent": parent_identity, "target": target_identity}, None


def _read_stable_bytes(path: Path, expected_identity: _PathIdentity) -> bytes:
    """Read only while the validated regular-file identity remains stable."""

    current, code = _identity_signature(path, expected_kind="file")
    if code is not None or current != expected_identity:
        raise _LocationChanged("workflow_library_target_changed")
    with path.open("rb") as stream:
        raw = stream.read(MAX_LIBRARY_BYTES + 1)
    after, code = _identity_signature(path, expected_kind="file")
    if code is not None or after != expected_identity:
        raise _LocationChanged("workflow_library_target_changed")
    return raw


def _identity_anchor(identity: _PathIdentity) -> _PathIdentity:
    """Keep only kind/device/inode when the operation itself changes metadata."""

    return (identity[0], identity[1], identity[2], 0, 0, 0)


def _shared_path_lock(path: Path) -> threading.RLock:
    key = _path_key(path)
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


def _location_recovery(code: str) -> dict[str, str]:
    del code
    return {
        "status": "recovery_required",
        "code": "workflow_library_location_unavailable",
        "reason": "Workflow Library local target is unavailable or unsafe; it will not be read or overwritten.",
        "action": "Review the fixed local configuration target and retry with a safe regular file.",
    }


def _cleanup_temporary(path: Path | None, expected_identity: _PathIdentity | None, expected_parent: _PathIdentity | None) -> None:
    """Remove only a task-created temp file whose parent and identity remain proven."""

    if path is None or expected_identity is None or expected_parent is None:
        return
    parent_identity, parent_code = _identity_signature(path.parent, expected_kind="directory")
    current_identity, current_code = _identity_signature(path, expected_kind="file")
    if (
        parent_code is not None
        or current_code is not None
        or parent_identity != expected_parent
        or current_identity is None
        or current_identity[:3] != expected_identity[:3]
    ):
        return
    try:
        path.unlink()
    except OSError:
        return


class WorkflowLibraryStore:
    """Thread-safe JSON store with optimistic library-level revisions."""

    def __init__(self, path: Path = DEFAULT_LIBRARY_PATH) -> None:
        raw_path = os.fspath(path)
        self.path = _lexical_path(raw_path)
        self._boundary = _lexical_path(CONFIG_ROOT) if _path_key(self.path) == _path_key(_lexical_path(DEFAULT_LIBRARY_PATH)) else self.path.parent
        self._path_policy_error = "workflow_library_path_escape" if _has_parent_segment(raw_path) else None
        # Validate before creating the lock. The result is refreshed before each
        # operation because the local target or one of its ancestors can change.
        if self._path_policy_error is None:
            _validate_location(self.path, self._boundary)
        self._lock = threading.RLock()
        self._shared_lock = _shared_path_lock(self.path)

    def _location(self) -> tuple[dict[str, Any] | None, str | None]:
        if self._path_policy_error is not None:
            return None, self._path_policy_error
        return _validate_location(self.path, self._boundary)

    def _read_with_bytes(self) -> tuple[dict[str, Any], dict[str, Any], bytes | None, _PathIdentity | None]:
        source_bytes: bytes | None = None
        location, location_code = self._location()
        if location_code is not None or location is None:
            return _default_library(), _location_recovery(location_code or "workflow_library_location_unavailable"), None, None
        target_identity = location.get("target")
        if target_identity is None:
            return _default_library(), {
                "status": "clean",
                "reason": "Workflow Library file is absent.",
                "action": "Create or import a validated workflow.",
            }, None, None
        try:
            source_bytes = _read_stable_bytes(self.path, target_identity)
            result = safe_import_workflow_library(source_bytes)
        except _LocationChanged:
            return _default_library(), _location_recovery("workflow_library_target_changed"), None, None
        except (OSError, UnicodeError):
            result = {"accepted": False}
        if not result.get("accepted"):
            return _default_library(), {
                "status": "recovery_required",
                "reason": "Workflow Library cannot be read or validated; it will not be overwritten.",
                "action": "Use a user-mediated validated export/import recovery.",
            }, source_bytes, target_identity
        return result["library"], {
            "status": "clean",
            "reason": "Workflow Library is valid.",
            "action": "Continue edits with the expected revision.",
        }, source_bytes, target_identity

    def _read(self) -> tuple[dict[str, Any], dict[str, Any]]:
        library, recovery, _source_bytes, _target_identity = self._read_with_bytes()
        return library, recovery

    def snapshot(self) -> dict[str, Any]:
        with self._shared_lock, self._lock:
            library, recovery = self._read()
            return {"library": _copy(library), "recovery": _copy(recovery)}

    def list_workflows(self) -> dict[str, Any]:
        snapshot = self.snapshot()
        library = snapshot["library"]
        return {
            "status": "partial" if snapshot["recovery"]["status"] != "clean" else "ready",
            "schema_version": WORKFLOW_LIBRARY_SCHEMA_VERSION,
            "library_revision": library["library_revision"],
            "workflows": _copy(library["workflows"]),
            "recovery": snapshot["recovery"],
        }

    def get_workflow(self, workflow_id: str) -> dict[str, Any]:
        if not isinstance(workflow_id, str):
            return {"status": "not_found", "workflow": None, "errors": [_error("not_found", "Chọn workflow ID hợp lệ rồi thử lại.")]}
        snapshot = self.snapshot()
        workflow = next((item for item in snapshot["library"]["workflows"] if item["id"] == workflow_id), None)
        if workflow is None:
            return {"status": "not_found", "workflow": None, "errors": [_error("not_found", "Chọn workflow khác hoặc import record đã validate.")]}
        return {"status": "ready", "workflow": _copy(workflow), "library_revision": snapshot["library"]["library_revision"]}

    def _atomic_write(
        self,
        library: dict[str, Any],
        *,
        expected_bytes: bytes | None = None,
        expected_identity: _PathIdentity | None = None,
    ) -> str:
        location, location_code = self._location()
        if location_code is not None or location is None:
            return "recovery"
        current_identity = location.get("target")
        if current_identity != expected_identity:
            return "conflict"
        if expected_identity is not None:
            try:
                if _read_stable_bytes(self.path, expected_identity) != expected_bytes:
                    return "conflict"
            except (_LocationChanged, OSError):
                return "recovery"
        elif expected_bytes is not None:
            return "conflict"
        parent_identity = location.get("parent")
        if not isinstance(parent_identity, tuple):
            return "recovery"
        temporary: Path | None = None
        temporary_identity: _PathIdentity | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=".workflow-library-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                temporary_observed, temporary_code = _identity_signature(temporary, expected_kind="file")
                if temporary_code is not None or temporary_observed is None:
                    return "recovery"
                temporary_identity = _identity_anchor(temporary_observed)
                current_parent, parent_code = _identity_signature(self.path.parent, expected_kind="directory")
                if parent_code is not None or current_parent != parent_identity:
                    return "recovery"
                handle.write(canonical_workflow_library(library))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            current_location, current_code = self._location()
            if current_code is not None or current_location is None:
                return "recovery"
            if current_location.get("parent") != parent_identity or current_location.get("target") != expected_identity:
                return "conflict"
            if expected_identity is not None:
                try:
                    if _read_stable_bytes(self.path, expected_identity) != expected_bytes:
                        return "conflict"
                except (_LocationChanged, OSError):
                    return "recovery"
            current_temp, temp_code = _identity_signature(temporary, expected_kind="file")
            if temp_code is not None or current_temp is None or current_temp[:3] != temporary_identity[:3]:
                return "recovery"
            os.replace(temporary, self.path)
            temporary = None
            return "written"
        except (OSError, ValueError, TypeError):
            return "error"
        finally:
            _cleanup_temporary(temporary, temporary_identity, parent_identity)

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
        value.setdefault("created_at", _now())
        value["updated_at"] = _now()
        value["revision"] = revision
        result = validate_workflow_entry(value)
        return result["workflow"] if result["valid"] else None

    def save_workflow(self, workflow: object, *, expected_revision: int | None = None) -> dict[str, Any]:
        with self._shared_lock, self._lock:
            library, recovery, source_bytes, source_identity = self._read_with_bytes()
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
            prepared = self._prepare_workflow(workflow, revision=entry_revision)
            if prepared is None:
                return {"accepted": False, "status": "invalid", "errors": [_error("invalid", "Sửa workflow theo schema rồi thử lại.")]}
            workflows = [item for item in library["workflows"] if item["id"] != prepared["id"]]
            workflows.append(prepared)
            next_library = {"schema_version": WORKFLOW_LIBRARY_SCHEMA_VERSION, "library_revision": next_revision, "workflows": workflows}
            write_status = self._atomic_write(next_library, expected_bytes=source_bytes, expected_identity=source_identity)
            if write_status == "conflict":
                return {"accepted": False, "status": "conflict", "library_revision": current_revision, "errors": [_error("revision_conflict", "Reload the Library and confirm the newer revision.")]}
            if write_status == "recovery":
                return {"accepted": False, "status": "recovery_required", "errors": [_error("recovery_required", "Keep newer data and verify local recovery before retrying.")]}
            if write_status != "written":
                return {"accepted": False, "status": "error", "errors": [_error("write_failed", "Kiểm tra local recovery rồi thử lại; dữ liệu cũ vẫn được giữ.")]}
            return {"accepted": True, "status": "ready", "library_revision": next_revision, "workflow": _copy(prepared)}

    upsert = save_workflow

    def delete_workflow(self, workflow_id: str, *, expected_revision: int | None = None) -> dict[str, Any]:
        with self._shared_lock, self._lock:
            library, recovery, source_bytes, source_identity = self._read_with_bytes()
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
            write_status = self._atomic_write(next_library, expected_bytes=source_bytes, expected_identity=source_identity)
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
        with self._shared_lock, self._lock:
            library, recovery, source_bytes, source_identity = self._read_with_bytes()
            if recovery["status"] != "clean":
                return {"accepted": False, "status": "recovery_required", "errors": [_error("recovery_required", recovery["action"])]}
            if expected_revision is not None and expected_revision != library["library_revision"]:
                return {"accepted": False, "status": "conflict", "errors": [_error("revision_conflict", "Tải lại Library trước khi import.")]}
            next_library = imported["library"]
            next_library["library_revision"] = library["library_revision"] + 1
            write_status = self._atomic_write(next_library, expected_bytes=source_bytes, expected_identity=source_identity)
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
