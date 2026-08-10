"""Local, declarative Image & Mask Studio state.

The editor intentionally stores no pixels, paths, commands or data URLs.  A
session is a bounded layer/mask recipe that refers to immutable Artifact Store
objects through opaque IDs.  This gives the user undo, snapshots and recovery
without pretending that a GPU-backed mask or inpaint engine has run.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from src.services.artifact_store import describe as describe_artifact
from src.shared.paths.registry import CONFIG_ROOT

from .config import image_mask_studio_config
from .schemas import (
    LAYER_ID_RE,
    MASK_EXPORT_CONTRACT,
    PRESET_CONTRACT,
    PRESET_ID_RE,
    PROJECT_ID_RE,
    SNAPSHOT_ID_RE,
    STATE_CONTRACT,
    STUDIO_CONTRACT,
    STUDIO_ID_RE,
    canonical_json,
    copy_json,
    layer_summary,
    new_id,
    new_layer,
    normalize_artifact_id,
    normalize_layer_update,
    normalize_mask_operation,
    opaque_id,
    optional_project_id,
    public_operation,
    safe_json,
    safe_text,
    now_iso,
)


STATE_PATH = CONFIG_ROOT / "image_mask_studio.json"
STATE_SCHEMA_VERSION = 1


class StudioConflictError(ValueError):
    """An optimistic-concurrency conflict that is safe to project publicly."""

    def __init__(self, current_revision: int) -> None:
        self.current_revision = current_revision
        super().__init__("Bản nháp đã thay đổi ở một cửa sổ khác; tải lại hoặc tạo recovery copy trước khi ghi tiếp.")


def _default_state() -> dict[str, Any]:
    return {
        "contract_version": STATE_CONTRACT,
        "schema_version": STATE_SCHEMA_VERSION,
        "sessions": {},
        "presets": {},
        "recent_session_ids": [],
    }


def _timestamp(value: object) -> str:
    return value if isinstance(value, str) and 1 <= len(value) <= 80 else now_iso()


def _session_document(session: Mapping[str, Any]) -> dict[str, Any]:
    """The small non-private document used by history and snapshots."""

    return {
        "title": str(session["title"]),
        "project_id": session.get("project_id"),
        "source_artifact_id": str(session["source_artifact_id"]),
        "layers": [layer_summary(layer) for layer in session.get("layers", []) if isinstance(layer, Mapping)],
        "active_layer_id": session.get("active_layer_id"),
    }


def _document_signature(document: Mapping[str, Any]) -> str:
    return canonical_json(document)


def _snapshot_public(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": snapshot["id"],
        "label": snapshot["label"],
        "revision": snapshot["revision"],
        "created_at": snapshot["created_at"],
    }


class ImageMaskStudioManager:
    """Owns bounded, recoverable declarative image editing sessions."""

    def __init__(
        self,
        path: Path = STATE_PATH,
        *,
        artifact_describer: Callable[[str], dict[str, Any] | None] = describe_artifact,
        config_provider: Callable[[], Mapping[str, Any]] = image_mask_studio_config,
    ) -> None:
        self.path = path
        self._artifact_describer = artifact_describer
        self._config_provider = config_provider
        self._lock = threading.RLock()

    def _config(self) -> Mapping[str, Any]:
        value = self._config_provider()
        if not isinstance(value, Mapping):  # defensive boundary for injected tests
            return image_mask_studio_config()
        return value

    def _limits(self) -> Mapping[str, int]:
        limits = self._config().get("limits", {})
        return limits if isinstance(limits, Mapping) else image_mask_studio_config()["limits"]

    def _load(self) -> tuple[dict[str, Any], dict[str, str], bool]:
        """Load valid local state without overwriting a corrupted file."""

        if not self.path.exists():
            return _default_state(), {
                "status": "clean",
                "reason": "Chưa có phiên Chỉnh sửa ảnh cục bộ.",
                "action": "Chọn một artifact ảnh Hub để tạo layer nguồn không phá hủy.",
            }, False
        try:
            source = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return _default_state(), {
                "status": "recovery_required",
                "reason": "Bản nháp Image & Mask Studio không đọc được; Hub không tự ghi đè dữ liệu này.",
                "action": "Khôi phục từ export an toàn hoặc kiểm tra file local bằng công cụ quản trị.",
            }, True
        if not isinstance(source, Mapping) or source.get("contract_version") != STATE_CONTRACT or source.get("schema_version") != STATE_SCHEMA_VERSION:
            return _default_state(), {
                "status": "recovery_required",
                "reason": "Contract hoặc schema của Image & Mask Studio không tương thích; Hub không tự ghi đè dữ liệu này.",
                "action": "Dùng bản Hub phù hợp hoặc import một manifest mask đã validate vào workspace mới.",
            }, True

        state = _default_state()
        skipped = 0
        raw_sessions = source.get("sessions", {})
        if isinstance(raw_sessions, Mapping):
            for session_id, raw in raw_sessions.items():
                try:
                    session = self._normalize_loaded_session(session_id, raw)
                    state["sessions"][session_id] = session
                except (TypeError, ValueError, KeyError):
                    skipped += 1
        elif raw_sessions:
            skipped += 1

        raw_presets = source.get("presets", {})
        if isinstance(raw_presets, Mapping):
            for preset_id, raw in raw_presets.items():
                try:
                    preset = self._normalize_loaded_preset(preset_id, raw)
                    state["presets"][preset_id] = preset
                except (TypeError, ValueError, KeyError):
                    skipped += 1
        elif raw_presets:
            skipped += 1

        recent = source.get("recent_session_ids", [])
        if isinstance(recent, list):
            state["recent_session_ids"] = [
                item for item in recent
                if isinstance(item, str) and item in state["sessions"]
            ][:12]
        if skipped:
            return state, {
                "status": "recovered_partial",
                "reason": f"Đã phục hồi {len(state['sessions'])} phiên và bỏ qua {skipped} bản ghi Studio không hợp lệ.",
                "action": "Kiểm tra snapshot/export trước khi xóa dữ liệu local cũ.",
            }, False
        return state, {
            "status": "clean",
            "reason": "Bản nháp Image & Mask Studio hợp lệ.",
            "action": "Có thể tiếp tục chỉnh lớp, mask, snapshot và liên kết project.",
        }, False

    def _normalize_loaded_session(self, session_id: object, raw: object) -> dict[str, Any]:
        studio_id = opaque_id(session_id, STUDIO_ID_RE, "Studio ID")
        if not isinstance(raw, Mapping) or raw.get("contract_version") != STUDIO_CONTRACT:
            raise ValueError("Session contract không hợp lệ.")
        title = safe_text(raw.get("title", ""), "Tên phiên", maximum=120)
        source_id = normalize_artifact_id(raw.get("source_artifact_id"), "Ảnh nguồn")
        project_id = optional_project_id(raw.get("project_id"))
        layers_raw = raw.get("layers", [])
        if not isinstance(layers_raw, list) or not layers_raw:
            raise ValueError("Session cần layer nguồn.")
        layers = [self._normalize_loaded_layer(layer) for layer in layers_raw]
        source_layers = [layer for layer in layers if layer["kind"] == "source"]
        if len(source_layers) != 1 or source_layers[0].get("artifact_id") != source_id:
            raise ValueError("Layer nguồn không khớp artifact nguồn.")
        if len({layer["id"] for layer in layers}) != len(layers):
            raise ValueError("Layer ID bị trùng.")
        active = raw.get("active_layer_id")
        if active not in {layer["id"] for layer in layers}:
            active = source_layers[0]["id"]
        revision = raw.get("revision", 1)
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1 or revision > 1_000_000:
            raise ValueError("Revision Studio không hợp lệ.")
        document = {
            "title": title,
            "project_id": project_id,
            "source_artifact_id": source_id,
            "layers": layers,
            "active_layer_id": active,
        }
        undo = self._normalize_history(raw.get("undo", []))
        redo = self._normalize_history(raw.get("redo", []))
        snapshots = self._normalize_snapshots(raw.get("snapshots", []))
        saved_document = raw.get("saved_document")
        if not isinstance(saved_document, Mapping):
            saved_document = copy_json(document)
        else:
            saved_document = self._normalize_document(saved_document)
        source_snapshot = raw.get("source_snapshot", {})
        if not isinstance(source_snapshot, Mapping):
            source_snapshot = {}
        return {
            "contract_version": STUDIO_CONTRACT,
            "id": studio_id,
            "title": title,
            "project_id": project_id,
            "source_artifact_id": source_id,
            "source_snapshot": safe_json(source_snapshot),
            "layers": layers,
            "active_layer_id": active,
            "revision": revision,
            "undo": undo,
            "redo": redo,
            "snapshots": snapshots,
            "saved_document": saved_document,
            "dirty": bool(raw.get("dirty", _document_signature(document) != _document_signature(saved_document))),
            "autosaved_at": _timestamp(raw.get("autosaved_at")),
            "explicit_saved_at": _timestamp(raw.get("explicit_saved_at")),
            "pending_project_attach": self._normalize_pending_attachment(raw.get("pending_project_attach")),
            "created_at": _timestamp(raw.get("created_at")),
            "updated_at": _timestamp(raw.get("updated_at")),
            "last_action": safe_text(raw.get("last_action", "Khôi phục bản nháp"), "Mô tả thao tác", maximum=120),
        }

    def _normalize_loaded_layer(self, raw: object) -> dict[str, Any]:
        if not isinstance(raw, Mapping):
            raise ValueError("Layer phải là object.")
        kind = raw.get("kind")
        if kind not in {"source", "mask", "adjustment", "generated"}:
            raise ValueError("Layer kind không hợp lệ.")
        layer = new_layer(
            str(kind),
            name=raw.get("name"),
            artifact_id=raw.get("artifact_id"),
            adjustment=raw.get("adjustment"),
            parent_layer_id=raw.get("parent_layer_id"),
            provenance=raw.get("provenance"),
        )
        layer["id"] = opaque_id(raw.get("id"), LAYER_ID_RE, "Layer ID")
        layer["visible"] = bool(raw.get("visible", True))
        opacity = raw.get("opacity", 1)
        layer["opacity"] = max(0.0, min(1.0, float(opacity)))
        if kind == "mask":
            operations = raw.get("operations", [])
            if not isinstance(operations, list):
                raise ValueError("Mask operations không hợp lệ.")
            max_points = int(self._limits()["max_points_per_stroke"])
            normalized_operations: list[dict[str, Any]] = []
            for operation in operations[: int(self._limits()["max_mask_operations"])]:
                normalized = normalize_mask_operation(operation, max_points=max_points)
                if isinstance(operation, Mapping) and isinstance(operation.get("id"), str):
                    normalized["id"] = safe_text(operation["id"], "Mask operation ID", maximum=80)
                if isinstance(operation, Mapping) and isinstance(operation.get("created_at"), str):
                    normalized["created_at"] = _timestamp(operation["created_at"])
                normalized_operations.append(normalized)
            layer["operations"] = normalized_operations
        return layer

    def _normalize_document(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        source_id = normalize_artifact_id(raw.get("source_artifact_id"), "Ảnh nguồn")
        layers_raw = raw.get("layers", [])
        if not isinstance(layers_raw, list) or not layers_raw:
            raise ValueError("Snapshot không có layer.")
        layers = [self._normalize_loaded_layer(item) for item in layers_raw]
        source = [item for item in layers if item["kind"] == "source"]
        if len(source) != 1 or source[0].get("artifact_id") != source_id:
            raise ValueError("Snapshot có layer nguồn không hợp lệ.")
        active = raw.get("active_layer_id")
        if active not in {item["id"] for item in layers}:
            active = source[0]["id"]
        return {
            "title": safe_text(raw.get("title", ""), "Tên phiên", maximum=120),
            "project_id": optional_project_id(raw.get("project_id")),
            "source_artifact_id": source_id,
            "layers": layers,
            "active_layer_id": active,
        }

    def _normalize_history(self, raw: object) -> list[dict[str, Any]]:
        if not isinstance(raw, list):
            return []
        limit = int(self._limits()["max_undo_entries"])
        values: list[dict[str, Any]] = []
        for item in raw[-limit:]:
            if isinstance(item, Mapping):
                try:
                    values.append(self._normalize_document(item))
                except ValueError:
                    continue
        return values

    def _normalize_snapshots(self, raw: object) -> list[dict[str, Any]]:
        if not isinstance(raw, list):
            return []
        limit = int(self._limits()["max_snapshots"])
        snapshots: list[dict[str, Any]] = []
        for item in raw[-limit:]:
            if not isinstance(item, Mapping):
                continue
            try:
                snapshot_id = opaque_id(item.get("id"), SNAPSHOT_ID_RE, "Snapshot ID")
                revision = item.get("revision")
                if not isinstance(revision, int) or revision < 1:
                    raise ValueError
                snapshots.append({
                    "id": snapshot_id,
                    "label": safe_text(item.get("label", "Snapshot"), "Nhãn snapshot", maximum=120),
                    "revision": revision,
                    "created_at": _timestamp(item.get("created_at")),
                    "document": self._normalize_document(item.get("document", {})),
                })
            except (TypeError, ValueError):
                continue
        return snapshots

    def _normalize_loaded_preset(self, preset_id: object, raw: object) -> dict[str, Any]:
        identifier = opaque_id(preset_id, PRESET_ID_RE, "Preset ID")
        if not isinstance(raw, Mapping) or raw.get("contract_version") != PRESET_CONTRACT:
            raise ValueError("Preset contract không hợp lệ.")
        layers = raw.get("layers", [])
        if not isinstance(layers, list):
            raise ValueError("Layers preset không hợp lệ.")
        normalized = [self._normalize_loaded_layer(item) for item in layers]
        # Presets never own a source image; applying one must preserve the
        # target session's immutable source layer.
        if any(item["kind"] == "source" for item in normalized):
            raise ValueError("Preset không được thay layer nguồn.")
        return {
            "contract_version": PRESET_CONTRACT,
            "id": identifier,
            "title": safe_text(raw.get("title", ""), "Tên preset", maximum=120),
            "layers": [layer_summary(item) for item in normalized],
            "created_at": _timestamp(raw.get("created_at")),
            "updated_at": _timestamp(raw.get("updated_at")),
            "source_studio_id": raw.get("source_studio_id") if isinstance(raw.get("source_studio_id"), str) and STUDIO_ID_RE.fullmatch(raw["source_studio_id"]) else None,
        }

    @staticmethod
    def _normalize_pending_attachment(raw: object) -> dict[str, Any] | None:
        if not isinstance(raw, Mapping):
            return None
        project_id = raw.get("project_id")
        artifacts = raw.get("artifacts")
        if not isinstance(project_id, str) or not PROJECT_ID_RE.fullmatch(project_id) or not isinstance(artifacts, list):
            return None
        safe_artifacts = [item for item in artifacts if isinstance(item, str) and item.startswith("artifact_")]
        return {
            "project_id": project_id,
            "artifacts": safe_artifacts[:48],
            "revision": raw.get("revision") if isinstance(raw.get("revision"), int) else 1,
            "created_at": _timestamp(raw.get("created_at")),
        }

    def _save(self, state: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(self.path)
        finally:
            if temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass

    def _mutate(self, callback: Callable[[dict[str, Any]], Any]) -> Any:
        with self._lock:
            state, recovery, blocked = self._load()
            if blocked:
                raise ValueError(recovery["reason"])
            result = callback(state)
            self._save(state)
            return result

    def _artifact(self, artifact_id: str, *, require_image: bool = True) -> dict[str, Any] | None:
        try:
            result = self._artifact_describer(artifact_id)
        except (OSError, ValueError):
            return None
        if not isinstance(result, Mapping):
            return None
        public = copy_json(dict(result))
        if public.get("id") != artifact_id:
            return None
        media_type = str(public.get("media_type") or "")
        if require_image and not media_type.startswith("image/"):
            return None
        return public

    def _require_image_artifact(self, artifact_id: object, field: str = "Artifact ảnh") -> tuple[str, dict[str, Any]]:
        identifier = normalize_artifact_id(artifact_id, field)
        artifact = self._artifact(identifier, require_image=True)
        if artifact is None:
            raise ValueError(f"{field} phải là artifact ảnh hiện có của Hub.")
        return identifier, artifact

    @staticmethod
    def _touch_recent(state: dict[str, Any], session_id: str) -> None:
        state["recent_session_ids"] = [session_id, *[item for item in state["recent_session_ids"] if item != session_id]][:12]

    def _get_session(self, state: Mapping[str, Any], session_id: str) -> dict[str, Any]:
        identifier = opaque_id(session_id, STUDIO_ID_RE, "Studio ID")
        session = state["sessions"].get(identifier)
        if not isinstance(session, dict):
            raise KeyError(identifier)
        return session

    def _assert_base_revision(self, session: Mapping[str, Any], value: object) -> None:
        if value is None:
            return
        if not isinstance(value, int) or isinstance(value, bool) or value != session["revision"]:
            raise StudioConflictError(int(session["revision"]))

    @staticmethod
    def _find_layer(session: Mapping[str, Any], layer_id: object, *, kind: str | None = None) -> dict[str, Any]:
        identifier = opaque_id(layer_id, LAYER_ID_RE, "Layer ID")
        layer = next((item for item in session["layers"] if item["id"] == identifier), None)
        if not isinstance(layer, dict) or (kind is not None and layer.get("kind") != kind):
            raise KeyError(identifier)
        return layer

    def _push_snapshot(self, session: dict[str, Any], label: str) -> None:
        snapshots = session["snapshots"]
        snapshots.append({
            "id": new_id("snapshot"),
            "label": safe_text(label, "Nhãn snapshot", maximum=120),
            "revision": session["revision"],
            "created_at": now_iso(),
            "document": _session_document(session),
        })
        limit = int(self._limits()["max_snapshots"])
        del snapshots[:-limit]

    def _mark_mutation(self, session: dict[str, Any], before: Mapping[str, Any], label: str) -> None:
        undo = session["undo"]
        undo.append(copy_json(before))
        limit = int(self._limits()["max_undo_entries"])
        del undo[:-limit]
        session["redo"] = []
        session["revision"] += 1
        session["autosaved_at"] = now_iso()
        session["updated_at"] = session["autosaved_at"]
        session["last_action"] = safe_text(label, "Mô tả thao tác", maximum=120)
        session["dirty"] = _document_signature(_session_document(session)) != _document_signature(session["saved_document"])
        self._push_snapshot(session, label)

    @staticmethod
    def _apply_document(session: dict[str, Any], document: Mapping[str, Any]) -> None:
        session["title"] = document["title"]
        session["project_id"] = document.get("project_id")
        session["source_artifact_id"] = document["source_artifact_id"]
        session["layers"] = copy_json(document["layers"])
        session["active_layer_id"] = document.get("active_layer_id")

    def _session_preview_artifact(self, document: Mapping[str, Any]) -> dict[str, Any] | None:
        layers = document.get("layers", [])
        if not isinstance(layers, list):
            return None
        for layer in reversed(layers):
            if not isinstance(layer, Mapping) or not layer.get("visible"):
                continue
            artifact_id = layer.get("artifact_id")
            if isinstance(artifact_id, str) and layer.get("kind") in {"source", "generated"}:
                artifact = self._artifact(artifact_id, require_image=True)
                if artifact:
                    return artifact
        return self._artifact(str(document.get("source_artifact_id") or ""), require_image=True)

    def _public_layer(self, layer: Mapping[str, Any]) -> dict[str, Any]:
        result = layer_summary(layer)
        artifact_id = result.get("artifact_id")
        if isinstance(artifact_id, str):
            artifact = self._artifact(artifact_id, require_image=True)
            result["artifact"] = artifact or {
                "id": artifact_id,
                "available": False,
                "name": "Artifact ảnh Hub không còn khả dụng",
                "media_type": "application/octet-stream",
                "url": None,
            }
        if result.get("kind") == "mask":
            result["operation_count"] = len(result.get("operations", []))
        return result

    def _public_session(self, session: Mapping[str, Any], *, detail: bool = False) -> dict[str, Any]:
        source = self._artifact(str(session["source_artifact_id"]), require_image=True)
        result: dict[str, Any] = {
            "contract_version": STUDIO_CONTRACT,
            "id": session["id"],
            "title": session["title"],
            "project_id": session.get("project_id"),
            "source_artifact_id": session["source_artifact_id"],
            "source_artifact": source or {
                "id": session["source_artifact_id"],
                "available": False,
                "name": "Ảnh nguồn Hub không còn khả dụng",
                "media_type": "application/octet-stream",
                "url": None,
            },
            "revision": session["revision"],
            "dirty": bool(session["dirty"]),
            "autosaved_at": session["autosaved_at"],
            "explicit_saved_at": session["explicit_saved_at"],
            "last_action": session["last_action"],
            "layer_count": len(session["layers"]),
            "mask_count": sum(1 for layer in session["layers"] if layer["kind"] == "mask"),
            "history": {"can_undo": bool(session["undo"]), "can_redo": bool(session["redo"]), "undo_count": len(session["undo"]), "redo_count": len(session["redo"])},
            "snapshots": [_snapshot_public(item) for item in session["snapshots"]],
            "pending_project_attach": copy_json(session.get("pending_project_attach")),
            "created_at": session["created_at"],
            "updated_at": session["updated_at"],
            "provenance": {
                "source_artifact_id": session["source_artifact_id"],
                "project_id": session.get("project_id"),
                "contract": STUDIO_CONTRACT,
                "revision": session["revision"],
            },
        }
        if detail:
            result["active_layer_id"] = session["active_layer_id"]
            result["layers"] = [self._public_layer(layer) for layer in session["layers"]]
        return result

    def _public_preset(self, preset: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "contract_version": PRESET_CONTRACT,
            "id": preset["id"],
            "title": preset["title"],
            "layer_count": len(preset.get("layers", [])),
            "source_studio_id": preset.get("source_studio_id"),
            "created_at": preset["created_at"],
            "updated_at": preset["updated_at"],
        }

    def preflight(self) -> dict[str, Any]:
        """Return capability-specific, non-executable status truthfully."""

        configured = bool(self._config().get("sam2_assist", {}).get("configured", False))
        sam2 = {
            "id": "sam2_assisted_mask",
            "title": "SAM2-assisted mask",
            "status": "partial" if configured else "unavailable",
            "reason": "SAM2 mới được khai báo để chuẩn bị mask; chưa có bằng chứng runtime được ủy quyền cho Studio này."
            if configured else "SAM2 chưa được cấu hình cho Studio và chưa có smoke runtime được ủy quyền.",
            "action": "Hoàn tất một smoke SAM2 bounded được ủy quyền trước khi dùng trợ lý phân vùng." if configured else "Cấu hình SAM2 theo tài liệu local, rồi thực hiện smoke riêng được ủy quyền.",
        }
        return {
            "status": "completed",
            "contract_version": STUDIO_CONTRACT,
            "capabilities": [
                {
                    "id": "local_non_destructive_layers",
                    "title": "Layer, mask và snapshot cục bộ",
                    "status": "operational",
                    "reason": "Studio chỉ lưu metadata/vector bounded và artifact ID opaque; không gọi model hoặc ghi đè ảnh nguồn.",
                    "action": "Chọn artifact ảnh Hub, tạo layer và lưu snapshot cục bộ.",
                },
                sam2,
                {
                    "id": "image_inpaint",
                    "title": "Inpaint có mask",
                    "status": "unavailable",
                    "reason": "Chưa có adapter inpaint có mask và smoke runtime riêng; Qwen image-to-image không được dùng làm bằng chứng thay thế.",
                    "action": "Dùng layer/mask metadata hoặc chờ adapter masked inpaint được ủy quyền và kiểm thử riêng.",
                },
                {
                    "id": "image_outpaint",
                    "title": "Outpaint canvas",
                    "status": "unavailable",
                    "reason": "Chưa có descriptor outpaint an toàn và smoke runtime riêng cho Studio.",
                    "action": "Chuẩn bị recipe/canvas không phá hủy; chỉ chạy outpaint sau một contract và smoke được ủy quyền.",
                },
            ],
        }

    def overview(self, *, project_id: object = None) -> dict[str, Any]:
        requested_project = optional_project_id(project_id)
        with self._lock:
            state, recovery, _blocked = self._load()
            sessions = [
                self._public_session(session)
                for session in state["sessions"].values()
                if requested_project is None or session.get("project_id") == requested_project
            ]
            sessions.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
            recent = [self._public_session(state["sessions"][item]) for item in state["recent_session_ids"] if item in state["sessions"]]
            return {
                "status": "completed",
                "contract_version": STUDIO_CONTRACT,
                "sessions": sessions[: int(self._limits()["max_sessions"])],
                "recent_sessions": recent,
                "presets": [self._public_preset(item) for item in sorted(state["presets"].values(), key=lambda value: str(value.get("updated_at") or ""), reverse=True)[:48]],
                "recovery": recovery,
                "preflight": self.preflight(),
            }

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            state, recovery, _blocked = self._load()
            session = state["sessions"].get(session_id)
            if not isinstance(session, Mapping):
                return None
            return {"status": "completed", "session": self._public_session(session, detail=True), "recovery": recovery, "preflight": self.preflight()}

    def create_session(self, payload: object) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise ValueError("Yêu cầu tạo Studio phải là object.")
        source_id, artifact = self._require_image_artifact(payload.get("source_artifact_id"), "Ảnh nguồn")
        project_id = optional_project_id(payload.get("project_id"))
        title = safe_text(payload.get("title") or artifact.get("name") or "Chỉnh sửa ảnh", "Tên phiên", maximum=120)

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            if len(state["sessions"]) >= int(self._limits()["max_sessions"]):
                raise ValueError("Đã đạt giới hạn phiên Image & Mask Studio cục bộ.")
            session_id = new_id("studio")
            source_layer = new_layer("source", name="Ảnh nguồn", artifact_id=source_id)
            timestamp = now_iso()
            session: dict[str, Any] = {
                "contract_version": STUDIO_CONTRACT,
                "id": session_id,
                "title": title,
                "project_id": project_id,
                "source_artifact_id": source_id,
                "source_snapshot": {
                    "media_type": artifact.get("media_type"),
                    "size_bytes": artifact.get("size_bytes", 0),
                    "sha256": artifact.get("sha256"),
                },
                "layers": [source_layer],
                "active_layer_id": source_layer["id"],
                "revision": 1,
                "undo": [],
                "redo": [],
                "snapshots": [],
                "saved_document": {},
                "dirty": False,
                "autosaved_at": timestamp,
                "explicit_saved_at": timestamp,
                "pending_project_attach": None,
                "created_at": timestamp,
                "updated_at": timestamp,
                "last_action": "Tạo phiên không phá hủy",
            }
            session["saved_document"] = _session_document(session)
            self._push_snapshot(session, "Tạo phiên")
            state["sessions"][session_id] = session
            self._touch_recent(state, session_id)
            return self._public_session(session, detail=True)

        return {"status": "completed", "session": self._mutate(mutate)}

    def update_session(self, session_id: str, payload: object) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise ValueError("Cập nhật Studio phải là object.")

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            session = self._get_session(state, session_id)
            self._assert_base_revision(session, payload.get("base_revision"))
            before = _session_document(session)
            changed = False
            if "title" in payload:
                session["title"] = safe_text(payload["title"], "Tên phiên", maximum=120)
                changed = True
            if "project_id" in payload:
                session["project_id"] = optional_project_id(payload["project_id"])
                changed = True
            if not changed:
                raise ValueError("Chưa có trường Studio hợp lệ để cập nhật.")
            self._mark_mutation(session, before, "Cập nhật metadata Studio")
            self._touch_recent(state, session_id)
            return self._public_session(session, detail=True)

        return {"status": "completed", "session": self._mutate(mutate)}

    def add_layer(self, session_id: str, payload: object) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise ValueError("Yêu cầu thêm layer phải là object.")
        kind = payload.get("kind")
        if kind not in {"mask", "adjustment", "generated"}:
            raise ValueError("Studio chỉ cho phép thêm mask, adjustment hoặc generated layer.")
        artifact_id = payload.get("artifact_id")
        if kind in {"mask", "generated"} and artifact_id is not None:
            self._require_image_artifact(artifact_id, "Artifact layer")
        if kind == "generated" and artifact_id is None:
            raise ValueError("Generated layer cần artifact ảnh dẫn xuất có sẵn của Hub.")

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            session = self._get_session(state, session_id)
            self._assert_base_revision(session, payload.get("base_revision"))
            if len(session["layers"]) >= int(self._limits()["max_layers_per_session"]):
                raise ValueError("Đã đạt giới hạn layer của Studio.")
            before = _session_document(session)
            parent_layer = payload.get("parent_layer_id") or session.get("active_layer_id")
            if kind == "generated" and parent_layer is not None:
                self._find_layer(session, parent_layer)
            layer = new_layer(
                str(kind),
                name=payload.get("name"),
                artifact_id=artifact_id,
                adjustment=payload.get("adjustment"),
                parent_layer_id=parent_layer if kind == "generated" else None,
                provenance={
                    "studio_id": session_id,
                    "source_artifact_id": session["source_artifact_id"],
                    **(safe_json(payload.get("provenance", {})) if kind == "generated" else {}),
                } if kind == "generated" else None,
            )
            session["layers"].append(layer)
            session["active_layer_id"] = layer["id"]
            label = {"mask": "Thêm mask layer", "adjustment": "Thêm adjustment layer", "generated": "Thêm artifact dẫn xuất"}[str(kind)]
            self._mark_mutation(session, before, label)
            self._touch_recent(state, session_id)
            return {"session": self._public_session(session, detail=True), "layer": self._public_layer(layer)}

        result = self._mutate(mutate)
        return {"status": "completed", **result}

    def update_layer(self, session_id: str, layer_id: str, payload: object) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise ValueError("Cập nhật layer phải là object.")

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            session = self._get_session(state, session_id)
            self._assert_base_revision(session, payload.get("base_revision"))
            layer = self._find_layer(session, layer_id)
            if layer["kind"] == "source" and "artifact_id" in payload:
                raise ValueError("Ảnh nguồn là immutable; tạo Studio mới để đổi source.")
            before = _session_document(session)
            updated = normalize_layer_update(layer, payload)
            layer.clear()
            layer.update(updated)
            if payload.get("active") is True:
                session["active_layer_id"] = layer["id"]
            self._mark_mutation(session, before, "Cập nhật layer")
            self._touch_recent(state, session_id)
            return {"session": self._public_session(session, detail=True), "layer": self._public_layer(layer)}

        result = self._mutate(mutate)
        return {"status": "completed", **result}

    def move_layer(self, session_id: str, layer_id: str, payload: object) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise ValueError("Di chuyển layer phải là object.")
        direction = payload.get("direction")
        if direction not in {"up", "down"}:
            raise ValueError("Hướng di chuyển layer phải là up hoặc down.")

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            session = self._get_session(state, session_id)
            self._assert_base_revision(session, payload.get("base_revision"))
            layer = self._find_layer(session, layer_id)
            if layer["kind"] == "source":
                raise ValueError("Layer nguồn luôn giữ ở đáy stack không phá hủy.")
            index = session["layers"].index(layer)
            target = index + (1 if direction == "up" else -1)
            if target <= 0 or target >= len(session["layers"]):
                raise ValueError("Layer đã ở biên của stack.")
            before = _session_document(session)
            session["layers"][index], session["layers"][target] = session["layers"][target], session["layers"][index]
            self._mark_mutation(session, before, "Sắp xếp lại layer")
            self._touch_recent(state, session_id)
            return self._public_session(session, detail=True)

        return {"status": "completed", "session": self._mutate(mutate)}

    def remove_layer(self, session_id: str, layer_id: str, payload: object = None) -> dict[str, Any]:
        source = payload if isinstance(payload, Mapping) else {}

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            session = self._get_session(state, session_id)
            self._assert_base_revision(session, source.get("base_revision"))
            layer = self._find_layer(session, layer_id)
            if layer["kind"] == "source":
                raise ValueError("Không thể xóa layer nguồn immutable.")
            before = _session_document(session)
            session["layers"] = [item for item in session["layers"] if item["id"] != layer["id"]]
            if session.get("active_layer_id") == layer["id"]:
                session["active_layer_id"] = session["layers"][-1]["id"]
            self._mark_mutation(session, before, "Gỡ layer khỏi stack")
            self._touch_recent(state, session_id)
            return self._public_session(session, detail=True)

        return {"status": "completed", "session": self._mutate(mutate)}

    def apply_mask_operation(self, session_id: str, layer_id: str, payload: object) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise ValueError("Thao tác mask phải là object.")

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            session = self._get_session(state, session_id)
            self._assert_base_revision(session, payload.get("base_revision"))
            layer = self._find_layer(session, layer_id, kind="mask")
            operations = layer.get("operations", [])
            if len(operations) >= int(self._limits()["max_mask_operations"]):
                raise ValueError("Đã đạt giới hạn thao tác mask; tạo snapshot hoặc export rồi tiếp tục trong Studio mới.")
            before = _session_document(session)
            operation = normalize_mask_operation(payload, max_points=int(self._limits()["max_points_per_stroke"]))
            operations.append(operation)
            layer["operations"] = operations
            session["active_layer_id"] = layer["id"]
            self._mark_mutation(session, before, {
                "brush": "Vẽ nét mask non-destructive",
                "invert": "Đảo mask",
                "feather": "Làm mềm mask",
                "grow": "Mở rộng mask",
                "shrink": "Thu nhỏ mask",
            }[operation["operation"]])
            self._touch_recent(state, session_id)
            return {"session": self._public_session(session, detail=True), "operation": public_operation(operation)}

        result = self._mutate(mutate)
        return {"status": "completed", **result}

    def undo(self, session_id: str, payload: object = None) -> dict[str, Any]:
        source = payload if isinstance(payload, Mapping) else {}

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            session = self._get_session(state, session_id)
            self._assert_base_revision(session, source.get("base_revision"))
            if not session["undo"]:
                raise ValueError("Không còn thao tác nào để hoàn tác.")
            current = _session_document(session)
            previous = session["undo"].pop()
            session["redo"].append(copy_json(current))
            del session["redo"][:-int(self._limits()["max_undo_entries"])]
            self._apply_document(session, previous)
            session["revision"] += 1
            session["autosaved_at"] = now_iso()
            session["updated_at"] = session["autosaved_at"]
            session["last_action"] = "Hoàn tác"
            session["dirty"] = _document_signature(_session_document(session)) != _document_signature(session["saved_document"])
            self._push_snapshot(session, "Hoàn tác")
            self._touch_recent(state, session_id)
            return self._public_session(session, detail=True)

        return {"status": "completed", "session": self._mutate(mutate)}

    def redo(self, session_id: str, payload: object = None) -> dict[str, Any]:
        source = payload if isinstance(payload, Mapping) else {}

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            session = self._get_session(state, session_id)
            self._assert_base_revision(session, source.get("base_revision"))
            if not session["redo"]:
                raise ValueError("Không còn thao tác nào để làm lại.")
            current = _session_document(session)
            following = session["redo"].pop()
            session["undo"].append(copy_json(current))
            del session["undo"][:-int(self._limits()["max_undo_entries"])]
            self._apply_document(session, following)
            session["revision"] += 1
            session["autosaved_at"] = now_iso()
            session["updated_at"] = session["autosaved_at"]
            session["last_action"] = "Làm lại"
            session["dirty"] = _document_signature(_session_document(session)) != _document_signature(session["saved_document"])
            self._push_snapshot(session, "Làm lại")
            self._touch_recent(state, session_id)
            return self._public_session(session, detail=True)

        return {"status": "completed", "session": self._mutate(mutate)}

    def save(self, session_id: str, payload: object = None) -> dict[str, Any]:
        source = payload if isinstance(payload, Mapping) else {}

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            session = self._get_session(state, session_id)
            self._assert_base_revision(session, source.get("base_revision"))
            session["saved_document"] = _session_document(session)
            timestamp = now_iso()
            session["explicit_saved_at"] = timestamp
            session["autosaved_at"] = timestamp
            session["updated_at"] = timestamp
            session["dirty"] = False
            session["last_action"] = "Lưu bản nháp cục bộ"
            self._push_snapshot(session, "Lưu bản nháp cục bộ")
            self._touch_recent(state, session_id)
            return self._public_session(session, detail=True)

        return {"status": "completed", "session": self._mutate(mutate)}

    def restore_snapshot(self, session_id: str, snapshot_id: str, payload: object = None) -> dict[str, Any]:
        source = payload if isinstance(payload, Mapping) else {}

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            session = self._get_session(state, session_id)
            self._assert_base_revision(session, source.get("base_revision"))
            identifier = opaque_id(snapshot_id, SNAPSHOT_ID_RE, "Snapshot ID")
            snapshot = next((item for item in session["snapshots"] if item["id"] == identifier), None)
            if not isinstance(snapshot, Mapping):
                raise KeyError(identifier)
            before = _session_document(session)
            self._apply_document(session, snapshot["document"])
            self._mark_mutation(session, before, f"Khôi phục snapshot: {snapshot['label']}")
            self._touch_recent(state, session_id)
            return self._public_session(session, detail=True)

        return {"status": "completed", "session": self._mutate(mutate)}

    def compare(self, session_id: str, *, before_snapshot_id: object = None, after_snapshot_id: object = None) -> dict[str, Any] | None:
        with self._lock:
            state, recovery, _blocked = self._load()
            session = state["sessions"].get(session_id)
            if not isinstance(session, Mapping):
                return None
            snapshots = list(session.get("snapshots", []))
            if not snapshots:
                return {"status": "completed", "compare": {"before": None, "after": None, "differences": {}}, "recovery": recovery}
            def select(raw: object, fallback: Mapping[str, Any]) -> Mapping[str, Any]:
                if raw is None:
                    return fallback
                identifier = opaque_id(raw, SNAPSHOT_ID_RE, "Snapshot ID")
                selected = next((item for item in snapshots if item["id"] == identifier), None)
                if not isinstance(selected, Mapping):
                    raise KeyError(identifier)
                return selected
            after = select(after_snapshot_id, snapshots[-1])
            before = select(before_snapshot_id, snapshots[-2] if len(snapshots) > 1 else snapshots[-1])
            before_doc = before["document"]
            after_doc = after["document"]
            differences: dict[str, Any] = {}
            for key in ("title", "project_id", "active_layer_id", "layers"):
                before_value = before_doc.get(key)
                after_value = after_doc.get(key)
                if canonical_json(before_value) != canonical_json(after_value):
                    differences[key] = {"before": copy_json(before_value), "after": copy_json(after_value)}
            def public_side(snapshot: Mapping[str, Any], document: Mapping[str, Any]) -> dict[str, Any]:
                return {
                    **_snapshot_public(snapshot),
                    "preview_artifact": self._session_preview_artifact(document),
                    "layers": [self._public_layer(layer) for layer in document.get("layers", []) if isinstance(layer, Mapping)],
                }
            return {
                "status": "completed",
                "compare": {
                    "contract_version": STUDIO_CONTRACT,
                    "session_id": session_id,
                    "before": public_side(before, before_doc),
                    "after": public_side(after, after_doc),
                    "differences": differences,
                },
                "recovery": recovery,
            }

    def export_mask(self, session_id: str, layer_id: str) -> dict[str, Any]:
        with self._lock:
            state, _recovery, _blocked = self._load()
            session = self._get_session(state, session_id)
            layer = self._find_layer(session, layer_id, kind="mask")
            artifact_id = layer.get("artifact_id")
            artifact = self._artifact(artifact_id, require_image=True) if isinstance(artifact_id, str) else None
            return {
                "status": "completed",
                "mask": {
                    "contract_version": MASK_EXPORT_CONTRACT,
                    "schema_version": 1,
                    "exported_at": now_iso(),
                    "source_artifact_id": session["source_artifact_id"],
                    "mask": layer_summary(layer),
                    "artifact": artifact,
                    "provenance": {
                        "studio_id": session_id,
                        "project_id": session.get("project_id"),
                        "revision": session["revision"],
                    },
                },
            }

    def import_mask(self, session_id: str, payload: object) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise ValueError("Import mask phải là object.")
        source_mask = payload.get("mask", payload)
        if not isinstance(source_mask, Mapping):
            raise ValueError("Manifest mask không hợp lệ.")
        if source_mask.get("contract_version") == MASK_EXPORT_CONTRACT:
            source_mask = source_mask.get("mask", {})
        if not isinstance(source_mask, Mapping):
            raise ValueError("Mask export không có dữ liệu layer.")
        if source_mask.get("kind", "mask") != "mask":
            raise ValueError("Chỉ import được mask layer an toàn.")
        artifact_id = source_mask.get("artifact_id")
        if artifact_id is not None:
            self._require_image_artifact(artifact_id, "Artifact mask import")
        operations = source_mask.get("operations", [])
        if not isinstance(operations, list):
            raise ValueError("Danh sách thao tác mask import không hợp lệ.")
        normalized_ops = [normalize_mask_operation(item, max_points=int(self._limits()["max_points_per_stroke"])) for item in operations[: int(self._limits()["max_mask_operations"])]]

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            session = self._get_session(state, session_id)
            self._assert_base_revision(session, payload.get("base_revision"))
            if len(session["layers"]) >= int(self._limits()["max_layers_per_session"]):
                raise ValueError("Đã đạt giới hạn layer của Studio.")
            before = _session_document(session)
            layer = new_layer("mask", name=source_mask.get("name") or "Mask import", artifact_id=artifact_id)
            layer["operations"] = normalized_ops
            session["layers"].append(layer)
            session["active_layer_id"] = layer["id"]
            self._mark_mutation(session, before, "Import mask an toàn")
            self._touch_recent(state, session_id)
            return {"session": self._public_session(session, detail=True), "layer": self._public_layer(layer)}

        result = self._mutate(mutate)
        return {"status": "completed", **result}

    def capture_preset(self, session_id: str, payload: object) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise ValueError("Capture preset phải là object.")

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            session = self._get_session(state, session_id)
            title = safe_text(payload.get("title") or f"{session['title']} preset", "Tên preset", maximum=120)
            layers = [layer_summary(layer) for layer in session["layers"] if layer["kind"] != "source"]
            preset_id = new_id("maskpreset")
            timestamp = now_iso()
            preset = {
                "contract_version": PRESET_CONTRACT,
                "id": preset_id,
                "title": title,
                "layers": layers,
                "source_studio_id": session_id,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            state["presets"][preset_id] = preset
            return self._public_preset(preset)

        return {"status": "completed", "preset": self._mutate(mutate)}

    def apply_preset(self, session_id: str, preset_id: str, payload: object = None) -> dict[str, Any]:
        source = payload if isinstance(payload, Mapping) else {}

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            session = self._get_session(state, session_id)
            self._assert_base_revision(session, source.get("base_revision"))
            preset = state["presets"].get(opaque_id(preset_id, PRESET_ID_RE, "Preset ID"))
            if not isinstance(preset, Mapping):
                raise KeyError(preset_id)
            copied_layers: list[dict[str, Any]] = []
            for raw in preset.get("layers", []):
                layer = self._normalize_loaded_layer(raw)
                layer["id"] = new_id("layer")
                if layer["kind"] == "generated" and layer.get("parent_layer_id") not in {item["id"] for item in session["layers"]}:
                    layer.pop("parent_layer_id", None)
                copied_layers.append(layer)
            if len(session["layers"]) + len(copied_layers) > int(self._limits()["max_layers_per_session"]):
                raise ValueError("Preset vượt giới hạn layer của Studio đích.")
            before = _session_document(session)
            session["layers"].extend(copied_layers)
            if copied_layers:
                session["active_layer_id"] = copied_layers[-1]["id"]
            self._mark_mutation(session, before, f"Áp dụng preset: {preset['title']}")
            self._touch_recent(state, session_id)
            return self._public_session(session, detail=True)

        return {"status": "completed", "session": self._mutate(mutate)}

    def prepare_project_attachment(self, session_id: str, payload: object) -> dict[str, Any]:
        """Persist idempotent project-link intent before a cross-store write."""

        if not isinstance(payload, Mapping):
            raise ValueError("Liên kết project phải là object.")
        project_id = optional_project_id(payload.get("project_id"))
        if project_id is None:
            raise ValueError("Chọn project opaque trước khi liên kết artifact.")
        requested = payload.get("artifact_ids")
        if requested is not None and not isinstance(requested, list):
            raise ValueError("artifact_ids phải là danh sách opaque ID.")

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            session = self._get_session(state, session_id)
            self._assert_base_revision(session, payload.get("base_revision"))
            allowed = {session["source_artifact_id"]}
            for layer in session["layers"]:
                artifact_id = layer.get("artifact_id")
                if isinstance(artifact_id, str):
                    allowed.add(artifact_id)
            artifact_ids = [normalize_artifact_id(item) for item in requested] if isinstance(requested, list) else sorted(allowed)
            if not artifact_ids or any(item not in allowed for item in artifact_ids):
                raise ValueError("Chỉ có thể liên kết artifact thuộc Studio hiện tại.")
            for artifact_id in artifact_ids:
                self._require_image_artifact(artifact_id, "Artifact liên kết project")
            session["project_id"] = project_id
            session["pending_project_attach"] = {
                "project_id": project_id,
                "artifacts": list(dict.fromkeys(artifact_ids)),
                "revision": session["revision"],
                "created_at": now_iso(),
            }
            session["updated_at"] = now_iso()
            self._touch_recent(state, session_id)
            return {"session": self._public_session(session, detail=True), "attachment": copy_json(session["pending_project_attach"])}

        result = self._mutate(mutate)
        return {"status": "pending_project_attach", **result}

    def complete_project_attachment(self, session_id: str, *, project_id: str, artifact_ids: list[str]) -> dict[str, Any]:
        """Mark a previously persisted attachment intent as completed."""

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            session = self._get_session(state, session_id)
            pending = session.get("pending_project_attach")
            if not isinstance(pending, Mapping) or pending.get("project_id") != project_id:
                raise ValueError("Không có liên kết project pending tương ứng.")
            if set(artifact_ids) != set(pending.get("artifacts", [])):
                raise ValueError("Kết quả liên kết project không khớp intent đã lưu.")
            session["project_id"] = project_id
            session["pending_project_attach"] = None
            session["updated_at"] = now_iso()
            session["last_action"] = "Đã liên kết artifact Studio vào project"
            self._touch_recent(state, session_id)
            return self._public_session(session, detail=True)

        return {"status": "completed", "session": self._mutate(mutate)}


image_mask_studio = ImageMaskStudioManager()
