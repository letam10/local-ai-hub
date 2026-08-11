"""Atomic, revision-safe persistence for the V5 Workflow Library.

This module is intentionally a pure metadata adapter.  It never starts a
worker, imports a graph engine, invokes a subprocess, accesses a device, or
resolves a user media/model path.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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


class WorkflowLibraryStore:
    """Thread-safe JSON store with optimistic library-level revisions."""

    def __init__(self, path: Path = DEFAULT_LIBRARY_PATH) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def _read(self) -> tuple[dict[str, Any], dict[str, Any]]:
        if not self.path.exists():
            return _default_library(), {"status": "clean", "reason": "Workflow Library local chưa có record.", "action": "Tạo hoặc import workflow đã validate."}
        try:
            raw = self.path.read_text(encoding="utf-8")
            result = safe_import_workflow_library(raw)
        except (OSError, UnicodeError):
            result = {"accepted": False}
        if not result.get("accepted"):
            return _default_library(), {
                "status": "recovery_required",
                "reason": "Workflow Library local không đọc/validate được; Hub không tự ghi đè.",
                "action": "Export/import manifest an toàn hoặc khôi phục bản local bằng thao tác user-mediated.",
            }
        return result["library"], {"status": "clean", "reason": "Workflow Library local hợp lệ.", "action": "Có thể tiếp tục chỉnh sửa với expected revision."}

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
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

    def _atomic_write(self, library: dict[str, Any]) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
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
                handle.write(canonical_workflow_library(library))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            temporary = None
            return True
        except (OSError, ValueError, TypeError):
            return False
        finally:
            if temporary is not None:
                try:
                    temporary.unlink()
                except OSError:
                    pass

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
        with self._lock:
            library, recovery = self._read()
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
            if not self._atomic_write(next_library):
                return {"accepted": False, "status": "error", "errors": [_error("write_failed", "Kiểm tra local recovery rồi thử lại; dữ liệu cũ vẫn được giữ.")]}
            return {"accepted": True, "status": "ready", "library_revision": next_revision, "workflow": _copy(prepared)}

    upsert = save_workflow

    def delete_workflow(self, workflow_id: str, *, expected_revision: int | None = None) -> dict[str, Any]:
        with self._lock:
            library, recovery = self._read()
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
            if not self._atomic_write(next_library):
                return {"accepted": False, "status": "error", "errors": [_error("write_failed", "Dữ liệu cũ vẫn được giữ; thử lại sau.")]}
            return {"accepted": True, "status": "ready", "library_revision": next_library["library_revision"]}

    def import_json(self, payload: str | bytes, *, expected_revision: int | None = None) -> dict[str, Any]:
        imported = safe_import_workflow_library(payload)
        if not imported.get("accepted"):
            return {"accepted": False, "status": "invalid", "errors": imported.get("errors", [_error("invalid", "Sửa JSON theo schema rồi thử lại.")])}
        with self._lock:
            library, recovery = self._read()
            if recovery["status"] != "clean":
                return {"accepted": False, "status": "recovery_required", "errors": [_error("recovery_required", recovery["action"])]}
            if expected_revision is not None and expected_revision != library["library_revision"]:
                return {"accepted": False, "status": "conflict", "errors": [_error("revision_conflict", "Tải lại Library trước khi import.")]}
            next_library = imported["library"]
            next_library["library_revision"] = library["library_revision"] + 1
            if not self._atomic_write(next_library):
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
