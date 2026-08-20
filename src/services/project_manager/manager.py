"""
  FILE NOTE
  - Mục đích: Thread-safe CRUD, import/export contracts và autosave draft cho creative project workspace (projects, recipes, assets, collections, compare boards)
  - Liên kết trực tiếp: src/services/project_manager/schemas.py, src/services/artifact_store.py, src/shared/paths/registry.py, src/services/api/
  - Vùng ảnh hưởng khi sửa: Toàn bộ creative workspace persistence, atomic write, recovery, revision detection, draft autosave
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.services.artifact_store import describe as describe_artifact
from src.services.artifact_store import list_artifacts
from src.shared.paths.registry import CONFIG_ROOT, ROOT

from .schemas import (
    ARTIFACT_ID_RE,
    ASSET_CONTRACT,
    COLLECTION_ID_RE,
    COMPARE_CONTRACT,
    COMPARE_ID_RE,
    MAX_ASSETS_PER_PROJECT,
    MAX_COLLECTIONS,
    MAX_COMPARE_ITEMS,
    MAX_PROJECTS,
    MAX_RECIPES,
    PRESET_ID_RE,
    PROJECT_CONTRACT,
    PROJECT_EXPORT_CONTRACT,
    PROJECT_ID_RE,
    RECIPE_CONTRACT,
    RECIPE_ID_RE,
    RECIPE_PACK_CONTRACT,
    WORKSPACE_CONTRACT,
    is_artifact_id,
    new_id,
    normalize_project,
    normalize_recipe,
    normalize_tags,
    now_iso,
    render_recipe,
    safe_json,
    safe_text,
)


STATE_PATH = CONFIG_ROOT / "creative_workspace.json"
_STATE_VERSION = 1
_IMAGE_MASK_STUDIO_ID_RE = re.compile(r"^studio_[a-f0-9]{32}$")
_MAX_IMAGE_MASK_LINKS_PER_ARTIFACT = 32
_MAX_IMAGE_MASK_LINKS_PER_PROJECT = 96


def _default_state() -> dict[str, Any]:
    return {
        "contract_version": WORKSPACE_CONTRACT,
        "schema_version": _STATE_VERSION,
        "projects": {},
        "recipes": {},
        "asset_metadata": {},
        "collections": {},
        "compare_boards": {},
        "recent_project_ids": [],
    }


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _timestamp(value: object) -> str:
    """Normalize local persisted timestamps before exposing Project metadata."""

    if not isinstance(value, str) or not (1 <= len(value) <= 80):
        return now_iso()
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return now_iso()
    if parsed.tzinfo is None:
        return now_iso()
    return parsed.astimezone(timezone.utc).isoformat()


class CreativeProjectManager:
    """Thread-safe CRUD and import/export contracts for creative metadata."""

    def __init__(
        self,
        path: Path = STATE_PATH,
        *,
        artifact_describer: Callable[[str], dict[str, Any] | None] = describe_artifact,
        artifact_lister: Callable[..., list[dict[str, Any]]] = list_artifacts,
    ) -> None:
        self.path = path
        self._artifact_describer = artifact_describer
        self._artifact_lister = artifact_lister
        self._lock = threading.RLock()

    def _load(self) -> tuple[dict[str, Any], dict[str, str], bool]:
        """Return normalized state and safe recovery information.

        A malformed top-level file is never overwritten automatically.  A
        structurally valid file with a few invalid records keeps every valid
        record and advertises the recovery result without exposing its path.
        """

        if not self.path.exists():
            return _default_state(), {"status": "clean", "reason": "Chưa có workspace local; sẵn sàng tạo project.", "action": "Tạo project đầu tiên hoặc import manifest đã validate."}, False
        try:
            source = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return _default_state(), {"status": "recovery_required", "reason": "Workspace local không đọc được; Hub không tự ghi đè dữ liệu này.", "action": "Import một manifest an toàn vào workspace mới hoặc kiểm tra file local bằng công cụ quản trị."}, True
        if not isinstance(source, Mapping):
            return _default_state(), {"status": "recovery_required", "reason": "Workspace local không đúng định dạng; Hub không tự ghi đè dữ liệu này.", "action": "Import một manifest an toàn vào workspace mới hoặc kiểm tra file local bằng công cụ quản trị."}, True
        if source.get("contract_version") != WORKSPACE_CONTRACT or source.get("schema_version") != _STATE_VERSION:
            return _default_state(), {"status": "recovery_required", "reason": "Workspace local dùng contract hoặc schema version không tương thích; Hub không tự ghi đè dữ liệu này.", "action": "Export bằng phiên bản Hub phù hợp hoặc import manifest M4A đã validate vào workspace mới."}, True

        state = _default_state()
        skipped = 0
        projects = source.get("projects", {})
        if isinstance(projects, Mapping):
            for project_id, raw in projects.items():
                try:
                    if not isinstance(project_id, str) or not PROJECT_ID_RE.fullmatch(project_id):
                        raise ValueError
                    project = normalize_project(raw, allow_id=True)
                    project["id"] = project_id
                    project["image_mask_studio_links"] = self._normalize_image_mask_project_links(
                        raw.get("image_mask_studio_links", []), project["asset_ids"],
                    )
                    project["created_at"] = _timestamp(raw.get("created_at") if isinstance(raw, Mapping) else None)
                    project["updated_at"] = _timestamp(raw.get("updated_at") if isinstance(raw, Mapping) else None)
                    project["compare_board_id"] = raw.get("compare_board_id") if isinstance(raw, Mapping) and isinstance(raw.get("compare_board_id"), str) and COMPARE_ID_RE.fullmatch(raw["compare_board_id"]) else None
                    state["projects"][project_id] = project
                except (TypeError, ValueError):
                    skipped += 1
        elif projects:
            skipped += 1

        recipes = source.get("recipes", {})
        if isinstance(recipes, Mapping):
            for recipe_id, raw in recipes.items():
                try:
                    if not isinstance(recipe_id, str) or not RECIPE_ID_RE.fullmatch(recipe_id):
                        raise ValueError
                    recipe = normalize_recipe(raw, allow_id=True)
                    recipe["id"] = recipe_id
                    recipe["created_at"] = _timestamp(raw.get("created_at") if isinstance(raw, Mapping) else None)
                    recipe["updated_at"] = _timestamp(raw.get("updated_at") if isinstance(raw, Mapping) else None)
                    state["recipes"][recipe_id] = recipe
                except (TypeError, ValueError):
                    skipped += 1
        elif recipes:
            skipped += 1

        metadata = source.get("asset_metadata", {})
        if isinstance(metadata, Mapping):
            for artifact_id, raw in metadata.items():
                try:
                    if not is_artifact_id(artifact_id) or not isinstance(raw, Mapping):
                        raise ValueError
                    parent = raw.get("parent_artifact_id")
                    if parent is not None and not is_artifact_id(parent):
                        raise ValueError
                    recipe_id = raw.get("recipe_id")
                    if recipe_id is not None and (not isinstance(recipe_id, str) or not RECIPE_ID_RE.fullmatch(recipe_id)):
                        raise ValueError
                    state["asset_metadata"][artifact_id] = {
                        "contract_version": ASSET_CONTRACT,
                        "tags": normalize_tags(raw.get("tags", [])),
                        "favorite": bool(raw.get("favorite", False)),
                        "parent_artifact_id": parent,
                        "recipe_id": recipe_id,
                        "provenance": safe_json(raw.get("provenance", {})),
                        "updated_at": _timestamp(raw.get("updated_at")),
                    }
                except (TypeError, ValueError):
                    skipped += 1
        elif metadata:
            skipped += 1

        collections = source.get("collections", {})
        if isinstance(collections, Mapping):
            for collection_id, raw in collections.items():
                try:
                    if not isinstance(collection_id, str) or not COLLECTION_ID_RE.fullmatch(collection_id) or not isinstance(raw, Mapping):
                        raise ValueError
                    asset_ids = raw.get("asset_ids", [])
                    if not isinstance(asset_ids, list) or not all(is_artifact_id(item) for item in asset_ids):
                        raise ValueError
                    state["collections"][collection_id] = {
                        "id": collection_id,
                        "title": safe_text(raw.get("title", ""), "Tên collection", maximum=100),
                        "asset_ids": list(dict.fromkeys(asset_ids))[:MAX_ASSETS_PER_PROJECT],
                        "tags": normalize_tags(raw.get("tags", [])),
                        "created_at": _timestamp(raw.get("created_at")),
                        "updated_at": _timestamp(raw.get("updated_at")),
                    }
                except (TypeError, ValueError):
                    skipped += 1
        elif collections:
            skipped += 1

        boards = source.get("compare_boards", {})
        if isinstance(boards, Mapping):
            for board_id, raw in boards.items():
                try:
                    if not isinstance(board_id, str) or not COMPARE_ID_RE.fullmatch(board_id) or not isinstance(raw, Mapping):
                        raise ValueError
                    project_id = raw.get("project_id")
                    items = raw.get("items", [])
                    if not isinstance(project_id, str) or not PROJECT_ID_RE.fullmatch(project_id) or not isinstance(items, list):
                        raise ValueError
                    normalized_items: list[dict[str, str]] = []
                    for item in items[:MAX_COMPARE_ITEMS]:
                        if not isinstance(item, Mapping) or not is_artifact_id(item.get("artifact_id")):
                            raise ValueError
                        normalized_items.append({
                            "artifact_id": item["artifact_id"],
                            "label": safe_text(item.get("label", item["artifact_id"]), "Nhãn so sánh", maximum=100),
                        })
                    selected = raw.get("selected_artifact_id")
                    if selected is not None and not is_artifact_id(selected):
                        raise ValueError
                    state["compare_boards"][board_id] = {
                        "contract_version": COMPARE_CONTRACT,
                        "id": board_id,
                        "project_id": project_id,
                        "items": normalized_items,
                        "selected_artifact_id": selected if selected in {item["artifact_id"] for item in normalized_items} else None,
                        "updated_at": _timestamp(raw.get("updated_at")),
                    }
                except (TypeError, ValueError):
                    skipped += 1
        elif boards:
            skipped += 1

        for project in state["projects"].values():
            board_id = project.get("compare_board_id")
            if board_id not in state["compare_boards"]:
                board_id = new_id("compare")
                project["compare_board_id"] = board_id
                state["compare_boards"][board_id] = self._new_board(board_id, project["id"])

        raw_recent = source.get("recent_project_ids", [])
        if isinstance(raw_recent, list):
            state["recent_project_ids"] = [item for item in raw_recent if item in state["projects"]][:12]
        if skipped:
            recovery = {
                "status": "partial_recovery",
                "reason": f"Đã phục hồi {len(state['projects'])} project và bỏ qua {skipped} bản ghi local không hợp lệ.",
                "action": "Kiểm tra manifest/export trước khi ghi đè các dữ liệu local cần giữ.",
            }
        else:
            recovery = {"status": "clean", "reason": "Workspace local hợp lệ.", "action": "Có thể tiếp tục tạo project, recipe và compare board."}
        return state, recovery, False

    def _save(self, state: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        import tempfile as _tempfile
        tmp: Path | None = None
        try:
            with _tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=".workspace-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                tmp = Path(handle.name)
                handle.write(json.dumps(state, ensure_ascii=False, indent=2))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
            tmp = None
        finally:
            if tmp is not None:
                try:
                    tmp.unlink()
                except OSError:
                    pass

    def _save_draft(self, draft_path: Path, data: dict[str, Any]) -> None:
        """Write an autosave recovery draft atomically without touching main state."""
        import tempfile as _tempfile
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        tmp: Path | None = None
        try:
            with _tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=draft_path.parent,
                prefix=".draft-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                tmp = Path(handle.name)
                handle.write(json.dumps(data, ensure_ascii=False, indent=2))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, draft_path)
            tmp = None
        except (OSError, TypeError, ValueError):
            pass
        finally:
            if tmp is not None:
                try:
                    tmp.unlink()
                except OSError:
                    pass

    def autosave_draft(self, project_id: str, graph: dict[str, Any]) -> dict[str, Any]:
        """Write a crash-recovery draft for the given project's graph.

        The draft file is never the main state file; it is safe to delete after
        a clean save.  Returns ``{"accepted": bool, "draft_path": str}``.
        """
        if not isinstance(project_id, str) or not project_id:
            return {"accepted": False, "reason": "Project ID không hợp lệ."}
        safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", project_id)[:80]
        draft_path = self.path.parent / f"draft_{safe_id}.json"
        draft_data = {
            "schema_version": 1,
            "project_id": project_id,
            "graph": graph if isinstance(graph, dict) else {},
        }
        self._save_draft(draft_path, draft_data)
        return {"accepted": True, "draft_path": str(draft_path)}

    def clear_draft(self, project_id: str) -> None:
        """Remove the autosave draft after a successful explicit save."""
        if not isinstance(project_id, str) or not project_id:
            return
        safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", project_id)[:80]
        draft_path = self.path.parent / f"draft_{safe_id}.json"
        try:
            draft_path.unlink(missing_ok=True)
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

    @staticmethod
    def _new_board(board_id: str, project_id: str) -> dict[str, Any]:
        return {
            "contract_version": COMPARE_CONTRACT,
            "id": board_id,
            "project_id": project_id,
            "items": [],
            "selected_artifact_id": None,
            "updated_at": now_iso(),
        }

    @staticmethod
    def _normalize_image_mask_project_links(raw: object, project_asset_ids: list[str]) -> list[dict[str, Any]]:
        """Validate small, opaque Studio provenance records owned by a Project."""

        if raw is None:
            return []
        if not isinstance(raw, list) or len(raw) > _MAX_IMAGE_MASK_LINKS_PER_PROJECT:
            raise ValueError("Danh sách lineage Image & Mask Studio của Project không hợp lệ.")
        project_assets = set(project_asset_ids)
        links: list[dict[str, Any]] = []
        identities: set[tuple[str, int]] = set()
        for item in raw:
            if not isinstance(item, Mapping):
                raise ValueError("Record lineage Image & Mask Studio của Project không hợp lệ.")
            studio_id = item.get("studio_id")
            revision = item.get("revision")
            source_id = item.get("source_artifact_id")
            artifact_ids = item.get("artifact_ids")
            mask_ids = item.get("mask_artifact_ids", [])
            if (
                not isinstance(studio_id, str)
                or not _IMAGE_MASK_STUDIO_ID_RE.fullmatch(studio_id)
                or not isinstance(revision, int)
                or isinstance(revision, bool)
                or revision < 1
                or not is_artifact_id(source_id)
                or not isinstance(artifact_ids, list)
                or not artifact_ids
                or len(artifact_ids) > MAX_ASSETS_PER_PROJECT
                or not all(is_artifact_id(value) for value in artifact_ids)
                or not isinstance(mask_ids, list)
                or not all(is_artifact_id(value) for value in mask_ids)
            ):
                raise ValueError("Record lineage Image & Mask Studio của Project không hợp lệ.")
            unique_artifacts = list(dict.fromkeys(artifact_ids))
            unique_masks = list(dict.fromkeys(mask_ids))
            if (
                source_id not in unique_artifacts
                or set(unique_artifacts) - project_assets
                or set(unique_masks) - set(unique_artifacts)
                or (studio_id, revision) in identities
            ):
                raise ValueError("Record lineage Image & Mask Studio của Project không khớp artifact/revision.")
            identities.add((studio_id, revision))
            links.append({
                "studio_id": studio_id,
                "revision": revision,
                "source_artifact_id": source_id,
                "artifact_ids": unique_artifacts,
                "mask_artifact_ids": unique_masks,
                "created_at": _timestamp(item.get("created_at")),
            })
        return links

    @staticmethod
    def _touch_project(state: dict[str, Any], project_id: str) -> None:
        project = state["projects"][project_id]
        project["updated_at"] = now_iso()
        state["recent_project_ids"] = [project_id, *[item for item in state["recent_project_ids"] if item != project_id]][:12]

    def _artifact(self, artifact_id: str) -> dict[str, Any] | None:
        if not is_artifact_id(artifact_id):
            return None
        artifact = self._artifact_describer(artifact_id)
        return _copy(artifact) if isinstance(artifact, Mapping) else None

    def _public_project(self, project: Mapping[str, Any], state: Mapping[str, Any]) -> dict[str, Any]:
        board = state["compare_boards"].get(project.get("compare_board_id"), {})
        return {
            "contract_version": PROJECT_CONTRACT,
            "id": project["id"],
            "title": project["title"],
            "description": project["description"],
            "status": project["status"],
            "tags": list(project.get("tags", [])),
            "asset_count": len(project.get("asset_ids", [])),
            "recipe_count": len(project.get("recipe_ids", [])),
            "image_mask_studio_link_count": len(project.get("image_mask_studio_links", [])),
            "selected_asset_id": project.get("selected_asset_id"),
            "selected_recipe_id": project.get("selected_recipe_id"),
            "workflow_preset": project.get("workflow_preset"),
            "compare_board_id": board.get("id"),
            "created_at": project.get("created_at"),
            "updated_at": project.get("updated_at"),
        }

    @staticmethod
    def _public_recipe(recipe: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "contract_version": RECIPE_CONTRACT,
            "id": recipe["id"],
            "title": recipe["title"],
            "version": recipe["version"],
            "prompt_template": recipe["prompt_template"],
            "variables": _copy(recipe["variables"]),
            "style_block": recipe["style_block"],
            "negative_block": recipe["negative_block"],
            "seed": recipe["seed"],
            "model": recipe["model"],
            "settings": _copy(recipe["settings"]),
            "workflow_preset": recipe.get("workflow_preset"),
            "tags": list(recipe.get("tags", [])),
            "created_at": recipe.get("created_at"),
            "updated_at": recipe.get("updated_at"),
        }

    def _decorate_asset(self, artifact_id: str, state: Mapping[str, Any], artifact: Mapping[str, Any] | None = None) -> dict[str, Any]:
        details = _copy(artifact) if artifact is not None else self._artifact(artifact_id)
        metadata = state["asset_metadata"].get(artifact_id, {})
        project_ids = [project_id for project_id, project in state["projects"].items() if artifact_id in project.get("asset_ids", [])]
        collections = [
            {"id": collection_id, "title": collection["title"]}
            for collection_id, collection in state["collections"].items()
            if artifact_id in collection.get("asset_ids", [])
        ]
        children = [asset_id for asset_id, item in state["asset_metadata"].items() if item.get("parent_artifact_id") == artifact_id]
        if details is None:
            details = {
                "id": artifact_id,
                "name": "Artifact Hub không còn khả dụng",
                "size_bytes": 0,
                "media_type": "application/octet-stream",
                "url": None,
                "available": False,
            }
        else:
            details["available"] = True
        media_type = str(details.get("media_type") or "application/octet-stream")
        return {
            "contract_version": ASSET_CONTRACT,
            **details,
            "preview_url": details.get("url") if media_type.startswith("image/") else None,
            "favorite": bool(metadata.get("favorite", False)),
            "tags": list(metadata.get("tags", [])),
            "collections": collections,
            "project_ids": project_ids,
            "lineage": {
                "parent_artifact_id": metadata.get("parent_artifact_id"),
                "derived_artifact_ids": children,
            },
            "recipe_id": metadata.get("recipe_id"),
            "provenance": _copy(metadata.get("provenance", {})),
        }

    def list_assets(
        self,
        *,
        query: str = "",
        tag: str = "",
        favorite: bool = False,
        collection_id: str = "",
        project_id: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            state, recovery, _blocked = self._load()
            try:
                artifacts = self._artifact_lister(limit=MAX_ASSETS_PER_PROJECT)
            except TypeError:
                artifacts = self._artifact_lister()
            available = {item.get("id"): item for item in artifacts if isinstance(item, Mapping) and is_artifact_id(item.get("id"))}
            referenced = {asset_id for project in state["projects"].values() for asset_id in project.get("asset_ids", [])}
            referenced.update(state["asset_metadata"])
            all_ids = list(available) + sorted(referenced - set(available))
            needle = query.strip().lower()[:120]
            requested_tag = tag.strip().lower()[:40]
            result: list[dict[str, Any]] = []
            for artifact_id in all_ids:
                item = self._decorate_asset(artifact_id, state, available.get(artifact_id))
                if favorite and not item["favorite"]:
                    continue
                if collection_id and collection_id not in {collection["id"] for collection in item["collections"]}:
                    continue
                if project_id and project_id not in item["project_ids"]:
                    continue
                if requested_tag and requested_tag not in item["tags"]:
                    continue
                if needle and needle not in f"{item['name']} {' '.join(item['tags'])}".lower():
                    continue
                result.append(item)
            return {"status": "completed", "assets": result[:MAX_ASSETS_PER_PROJECT], "recovery": recovery}

    def list_projects(self, *, include_archived: bool = True) -> dict[str, Any]:
        with self._lock:
            state, recovery, _blocked = self._load()
            projects = [self._public_project(project, state) for project in state["projects"].values() if include_archived or project["status"] == "active"]
            projects.sort(key=lambda item: (item.get("updated_at") or "", item["id"]), reverse=True)
            recent = [self._public_project(state["projects"][project_id], state) for project_id in state["recent_project_ids"] if project_id in state["projects"]]
            return {"status": "completed", "projects": projects, "recent_projects": recent, "recovery": recovery}

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        with self._lock:
            state, recovery, _blocked = self._load()
            project = state["projects"].get(project_id)
            if project is None:
                return None
            board = self._compare_payload(state, project)
            return {
                "status": "completed",
                "project": self._public_project(project, state),
                "assets": [self._decorate_asset(asset_id, state) for asset_id in project.get("asset_ids", [])],
                "recipes": [self._public_recipe(state["recipes"][recipe_id]) for recipe_id in project.get("recipe_ids", []) if recipe_id in state["recipes"]],
                "image_mask_studio_links": _copy(project.get("image_mask_studio_links", [])),
                "compare": board,
                "recovery": recovery,
            }

    def create_project(self, payload: object) -> dict[str, Any]:
        source = payload if isinstance(payload, Mapping) else {}

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            if len(state["projects"]) >= MAX_PROJECTS:
                raise ValueError("Đã đạt giới hạn project local của Hub.")
            normalized = normalize_project({**source, "status": "active"})
            project_id = new_id("project")
            timestamp = now_iso()
            board_id = new_id("compare")
            project = {
                **normalized,
                "id": project_id,
                "created_at": timestamp,
                "updated_at": timestamp,
                "compare_board_id": board_id,
                "image_mask_studio_links": [],
            }
            state["projects"][project_id] = project
            state["compare_boards"][board_id] = self._new_board(board_id, project_id)
            self._touch_project(state, project_id)
            return self._public_project(project, state)

        return {"status": "completed", "project": self._mutate(mutate)}

    def update_project(self, project_id: str, payload: object) -> dict[str, Any]:
        if not PROJECT_ID_RE.fullmatch(project_id):
            raise ValueError("Project ID không hợp lệ.")
        source = payload if isinstance(payload, Mapping) else {}

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            project = state["projects"].get(project_id)
            if project is None:
                raise KeyError(project_id)
            next_value = {**project}
            for field in ("title", "description", "tags", "workflow_preset", "selected_asset_id", "selected_recipe_id"):
                if field in source:
                    next_value[field] = source[field]
            normalized = normalize_project(next_value, allow_id=True)
            project.update(normalized)
            self._touch_project(state, project_id)
            return self._public_project(project, state)

        return {"status": "completed", "project": self._mutate(mutate)}

    def archive_project(self, project_id: str, *, archived: bool = True) -> dict[str, Any]:
        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            project = state["projects"].get(project_id)
            if project is None:
                raise KeyError(project_id)
            project["status"] = "archived" if archived else "active"
            self._touch_project(state, project_id)
            return self._public_project(project, state)

        return {"status": "completed", "project": self._mutate(mutate)}

    def add_project_asset(self, project_id: str, payload: object) -> dict[str, Any]:
        if not PROJECT_ID_RE.fullmatch(project_id) or not isinstance(payload, Mapping):
            raise ValueError("Yêu cầu thêm asset không hợp lệ.")
        artifact_id = payload.get("artifact_id")
        if not is_artifact_id(artifact_id) or self._artifact(artifact_id) is None:
            raise ValueError("Asset phải là artifact Hub hiện có.")
        parent = payload.get("parent_artifact_id")
        if parent is not None and (not is_artifact_id(parent) or self._artifact(parent) is None):
            raise ValueError("Asset nguồn của lineage không hợp lệ.")
        recipe_id = payload.get("recipe_id")
        if recipe_id is not None and (not isinstance(recipe_id, str) or not RECIPE_ID_RE.fullmatch(recipe_id)):
            raise ValueError("Recipe reference không hợp lệ.")

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            project = state["projects"].get(project_id)
            if project is None:
                raise KeyError(project_id)
            if recipe_id is not None and recipe_id not in state["recipes"]:
                raise ValueError("Recipe được chọn không tồn tại local.")
            if artifact_id not in project["asset_ids"]:
                if len(project["asset_ids"]) >= MAX_ASSETS_PER_PROJECT:
                    raise ValueError("Project đã đạt giới hạn asset reference.")
                project["asset_ids"].append(artifact_id)
            project["selected_asset_id"] = artifact_id
            metadata = state["asset_metadata"].get(artifact_id, {"contract_version": ASSET_CONTRACT, "tags": [], "favorite": False, "parent_artifact_id": None, "recipe_id": None, "provenance": {}})
            if "tags" in payload:
                metadata["tags"] = normalize_tags(payload["tags"])
            if "favorite" in payload:
                metadata["favorite"] = bool(payload["favorite"])
            if parent is not None:
                metadata["parent_artifact_id"] = parent
            if recipe_id is not None:
                metadata["recipe_id"] = recipe_id
                if recipe_id not in project["recipe_ids"]:
                    project["recipe_ids"].append(recipe_id)
            if "provenance" in payload:
                metadata["provenance"] = safe_json(payload["provenance"])
            metadata["updated_at"] = now_iso()
            state["asset_metadata"][artifact_id] = metadata
            self._touch_project(state, project_id)
            return {"project": self._public_project(project, state), "asset": self._decorate_asset(artifact_id, state)}

        result = self._mutate(mutate)
        return {"status": "completed", **result}

    def attach_image_mask_studio_revision(self, project_id: str, payload: object) -> dict[str, Any]:
        """Idempotently link already-owned Studio artifacts to one project.

        Image & Mask Studio writes its own small draft state.  This narrow
        bridge deliberately receives only opaque artifact IDs and provenance
        metadata after that draft has durably recorded a pending intent.  It
        never receives pixels, paths, canvas data URLs or arbitrary manifests.
        """

        if not PROJECT_ID_RE.fullmatch(project_id) or not isinstance(payload, Mapping):
            raise ValueError("Liên kết Image & Mask Studio không hợp lệ.")
        studio_id = payload.get("studio_id")
        if not isinstance(studio_id, str) or not _IMAGE_MASK_STUDIO_ID_RE.fullmatch(studio_id):
            raise ValueError("Studio ID không hợp lệ.")
        revision = payload.get("revision")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise ValueError("Revision Studio không hợp lệ.")
        source_id = payload.get("source_artifact_id")
        artifact_ids = payload.get("artifact_ids")
        mask_ids = payload.get("mask_artifact_ids", [])
        if not is_artifact_id(source_id) or not isinstance(artifact_ids, list) or not artifact_ids or not all(is_artifact_id(item) for item in artifact_ids):
            raise ValueError("Liên kết Studio chỉ nhận artifact ID opaque hợp lệ.")
        if not isinstance(mask_ids, list) or not all(is_artifact_id(item) for item in mask_ids):
            raise ValueError("Mask artifact IDs không hợp lệ.")
        unique_ids = list(dict.fromkeys(str(item) for item in artifact_ids))
        if source_id not in unique_ids:
            unique_ids.insert(0, source_id)
        unique_mask_ids = list(dict.fromkeys(str(item) for item in mask_ids))
        if any(item not in unique_ids for item in unique_mask_ids):
            raise ValueError("Mask artifact IDs phải thuộc đúng tập artifact Studio được liên kết.")
        if any(self._artifact(item) is None for item in unique_ids):
            raise ValueError("Một artifact Studio không còn khả dụng trong Hub.")
        studio_link = safe_json({
            "image_mask_studio_id": studio_id,
            "image_mask_revision": revision,
            "source_artifact_id": source_id,
            "mask_artifact_ids": unique_mask_ids,
        })
        project_link_core = {
            "studio_id": studio_id,
            "revision": revision,
            "source_artifact_id": source_id,
            "artifact_ids": unique_ids,
            "mask_artifact_ids": unique_mask_ids,
        }

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            project = state["projects"].get(project_id)
            if project is None:
                raise KeyError(project_id)
            additions = [item for item in unique_ids if item not in project["asset_ids"]]
            if len(project["asset_ids"]) + len(additions) > MAX_ASSETS_PER_PROJECT:
                raise ValueError("Project đã đạt giới hạn asset reference.")
            project_links = project.get("image_mask_studio_links", [])
            if not isinstance(project_links, list) or not all(isinstance(item, Mapping) for item in project_links):
                raise ValueError("Danh sách lineage Image & Mask Studio của Project không hợp lệ; không thể ghi đè.")
            same_identity = [
                item for item in project_links
                if item.get("studio_id") == studio_id and item.get("revision") == revision
            ]
            if same_identity:
                if any(any(item.get(key) != value for key, value in project_link_core.items()) for item in same_identity):
                    raise ValueError("Studio revision đã có lineage Project khác; không thể ghi đè provenance hiện có.")
            else:
                if len(project_links) >= _MAX_IMAGE_MASK_LINKS_PER_PROJECT:
                    raise ValueError("Project đã đạt giới hạn lineage Image & Mask Studio; export hoặc archive record cũ trước khi thêm.")
                project_links.append({**project_link_core, "created_at": now_iso()})
                project["image_mask_studio_links"] = project_links
            for artifact_id in unique_ids:
                if artifact_id not in project["asset_ids"]:
                    project["asset_ids"].append(artifact_id)
                # The source can be shared by many projects/Studio sessions.
                # Attaching it must not overwrite its global metadata or
                # provenance.  Non-source artifacts retain one immutable
                # parent source, while their M6 relationships are appended as
                # bounded records instead of replacing prior provenance.
                if artifact_id == source_id:
                    continue
                metadata = state["asset_metadata"].get(artifact_id, {
                    "contract_version": ASSET_CONTRACT,
                    "tags": [],
                    "favorite": False,
                    "parent_artifact_id": None,
                    "recipe_id": None,
                    "provenance": {},
                })
                existing_parent = metadata.get("parent_artifact_id")
                if existing_parent is not None and existing_parent != source_id:
                    raise ValueError("Artifact dẫn xuất/mask đã có lineage từ một ảnh nguồn khác; không thể ghi đè provenance hiện có.")
                existing_provenance = metadata.get("provenance", {})
                if not isinstance(existing_provenance, Mapping):
                    raise ValueError("Artifact dẫn xuất/mask có provenance cục bộ không tương thích; không thể ghi đè để liên kết Studio.")
                provenance_copy = _copy(dict(existing_provenance))
                existing_links = provenance_copy.get("image_mask_studio_links", [])
                if not isinstance(existing_links, list) or not all(isinstance(item, Mapping) for item in existing_links):
                    raise ValueError("Artifact dẫn xuất/mask có danh sách lineage Studio không hợp lệ; không thể ghi đè.")
                links = [_copy(dict(item)) for item in existing_links]
                if studio_link not in links:
                    if len(links) >= _MAX_IMAGE_MASK_LINKS_PER_ARTIFACT:
                        raise ValueError("Artifact đã đạt giới hạn lineage Studio; tạo artifact dẫn xuất mới hoặc dọn lineage có chủ đích.")
                    links.append(studio_link)
                provenance_copy["image_mask_studio_links"] = links
                metadata["parent_artifact_id"] = source_id
                metadata["provenance"] = safe_json(provenance_copy)
                metadata["updated_at"] = now_iso()
                state["asset_metadata"][artifact_id] = metadata
            project["selected_asset_id"] = unique_ids[-1]
            self._touch_project(state, project_id)
            return {
                "project": self._public_project(project, state),
                "artifact_ids": unique_ids,
                "provenance": studio_link,
            }

        result = self._mutate(mutate)
        return {"status": "completed", **result}

    def update_asset(self, artifact_id: str, payload: object) -> dict[str, Any]:
        if not is_artifact_id(artifact_id) or not isinstance(payload, Mapping):
            raise ValueError("Asset update không hợp lệ.")

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            known = artifact_id in state["asset_metadata"] or any(artifact_id in project.get("asset_ids", []) for project in state["projects"].values()) or self._artifact(artifact_id) is not None
            if not known:
                raise KeyError(artifact_id)
            metadata = state["asset_metadata"].get(artifact_id, {"contract_version": ASSET_CONTRACT, "tags": [], "favorite": False, "parent_artifact_id": None, "recipe_id": None, "provenance": {}})
            if "tags" in payload:
                metadata["tags"] = normalize_tags(payload["tags"])
            if "favorite" in payload:
                metadata["favorite"] = bool(payload["favorite"])
            if "parent_artifact_id" in payload:
                parent = payload["parent_artifact_id"]
                if parent is not None and not is_artifact_id(parent):
                    raise ValueError("Lineage source không hợp lệ.")
                metadata["parent_artifact_id"] = parent
            if "recipe_id" in payload:
                recipe_id = payload["recipe_id"]
                if recipe_id is not None and (not isinstance(recipe_id, str) or recipe_id not in state["recipes"]):
                    raise ValueError("Recipe reference không hợp lệ.")
                metadata["recipe_id"] = recipe_id
            if "provenance" in payload:
                metadata["provenance"] = safe_json(payload["provenance"])
            metadata["updated_at"] = now_iso()
            state["asset_metadata"][artifact_id] = metadata
            return self._decorate_asset(artifact_id, state)

        return {"status": "completed", "asset": self._mutate(mutate)}

    def list_collections(self) -> dict[str, Any]:
        with self._lock:
            state, recovery, _blocked = self._load()
            collections = []
            for collection in state["collections"].values():
                collections.append({
                    "id": collection["id"],
                    "title": collection["title"],
                    "asset_count": len(collection["asset_ids"]),
                    "asset_ids": list(collection["asset_ids"]),
                    "tags": list(collection["tags"]),
                    "updated_at": collection["updated_at"],
                })
            collections.sort(key=lambda item: (item["updated_at"], item["id"]), reverse=True)
            return {"status": "completed", "collections": collections, "recovery": recovery}

    def create_collection(self, payload: object) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise ValueError("Collection phải là đối tượng JSON.")

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            if len(state["collections"]) >= MAX_COLLECTIONS:
                raise ValueError("Đã đạt giới hạn collection local.")
            asset_ids = payload.get("asset_ids", [])
            if not isinstance(asset_ids, list) or not all(is_artifact_id(item) for item in asset_ids):
                raise ValueError("asset_ids collection không hợp lệ.")
            collection_id = new_id("collection")
            timestamp = now_iso()
            collection = {
                "id": collection_id,
                "title": safe_text(payload.get("title", ""), "Tên collection", maximum=100),
                "asset_ids": list(dict.fromkeys(asset_ids))[:MAX_ASSETS_PER_PROJECT],
                "tags": normalize_tags(payload.get("tags", [])),
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            state["collections"][collection_id] = collection
            return _copy(collection)

        return {"status": "completed", "collection": self._mutate(mutate)}

    def update_collection(self, collection_id: str, payload: object) -> dict[str, Any]:
        if not COLLECTION_ID_RE.fullmatch(collection_id) or not isinstance(payload, Mapping):
            raise ValueError("Collection update không hợp lệ.")

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            collection = state["collections"].get(collection_id)
            if collection is None:
                raise KeyError(collection_id)
            if "title" in payload:
                collection["title"] = safe_text(payload["title"], "Tên collection", maximum=100)
            if "tags" in payload:
                collection["tags"] = normalize_tags(payload["tags"])
            if "asset_ids" in payload:
                asset_ids = payload["asset_ids"]
                if not isinstance(asset_ids, list) or not all(is_artifact_id(item) for item in asset_ids):
                    raise ValueError("asset_ids collection không hợp lệ.")
                collection["asset_ids"] = list(dict.fromkeys(asset_ids))[:MAX_ASSETS_PER_PROJECT]
            collection["updated_at"] = now_iso()
            return _copy(collection)

        return {"status": "completed", "collection": self._mutate(mutate)}

    def list_recipes(self) -> dict[str, Any]:
        with self._lock:
            state, recovery, _blocked = self._load()
            recipes = [self._public_recipe(recipe) for recipe in state["recipes"].values()]
            recipes.sort(key=lambda item: (item["updated_at"] or "", item["id"]), reverse=True)
            return {"status": "completed", "recipes": recipes, "recovery": recovery}

    def get_recipe(self, recipe_id: str) -> dict[str, Any] | None:
        with self._lock:
            state, recovery, _blocked = self._load()
            recipe = state["recipes"].get(recipe_id)
            return {"status": "completed", "recipe": self._public_recipe(recipe), "recovery": recovery} if recipe else None

    def create_recipe(self, payload: object) -> dict[str, Any]:
        source = payload if isinstance(payload, Mapping) else {}

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            if len(state["recipes"]) >= MAX_RECIPES:
                raise ValueError("Đã đạt giới hạn recipe local.")
            recipe = normalize_recipe(source)
            recipe_id = new_id("recipe")
            timestamp = now_iso()
            recipe.update({"id": recipe_id, "created_at": timestamp, "updated_at": timestamp})
            state["recipes"][recipe_id] = recipe
            project_id = source.get("project_id")
            if project_id:
                project = state["projects"].get(project_id)
                if project is None:
                    raise ValueError("Project được chọn không tồn tại.")
                project["recipe_ids"] = [*project["recipe_ids"], recipe_id]
                project["selected_recipe_id"] = recipe_id
                self._touch_project(state, project_id)
            return self._public_recipe(recipe)

        return {"status": "completed", "recipe": self._mutate(mutate)}

    def update_recipe(self, recipe_id: str, payload: object) -> dict[str, Any]:
        if not RECIPE_ID_RE.fullmatch(recipe_id) or not isinstance(payload, Mapping):
            raise ValueError("Recipe update không hợp lệ.")

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            existing = state["recipes"].get(recipe_id)
            if existing is None:
                raise KeyError(recipe_id)
            merged = {**existing, **payload, "version": int(existing.get("version", 1)) + 1}
            recipe = normalize_recipe(merged, allow_id=True)
            recipe.update({"id": recipe_id, "created_at": existing["created_at"], "updated_at": now_iso()})
            state["recipes"][recipe_id] = recipe
            return self._public_recipe(recipe)

        return {"status": "completed", "recipe": self._mutate(mutate)}

    def apply_recipe(self, recipe_id: str, payload: object) -> dict[str, Any]:
        if not RECIPE_ID_RE.fullmatch(recipe_id):
            raise ValueError("Recipe ID không hợp lệ.")
        values = payload.get("values", {}) if isinstance(payload, Mapping) else {}
        project_id = payload.get("project_id") if isinstance(payload, Mapping) else None

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            recipe = state["recipes"].get(recipe_id)
            if recipe is None:
                raise KeyError(recipe_id)
            if project_id is not None:
                project = state["projects"].get(project_id)
                if project is None:
                    raise ValueError("Project được chọn không tồn tại.")
                if recipe_id not in project["recipe_ids"]:
                    project["recipe_ids"].append(recipe_id)
                project["selected_recipe_id"] = recipe_id
                self._touch_project(state, project_id)
            application = render_recipe(recipe, values)
            quick = {
                "engine": "qwen" if "qwen" in application["model"].lower() else "flux",
                "prompt": application["prompt"],
                "negative_prompt": application["negative_prompt"],
                "seed": application["seed"],
                **{key: value for key, value in application["settings"].items() if key in {"width", "height", "steps"}},
            }
            return {"recipe": self._public_recipe(recipe), "application": application, "quick": quick, "node_studio": application}

        return {"status": "completed", **self._mutate(mutate)}

    def _compare_payload(self, state: Mapping[str, Any], project: Mapping[str, Any]) -> dict[str, Any]:
        board = state["compare_boards"].get(project.get("compare_board_id"))
        if not isinstance(board, Mapping):
            return {"contract_version": COMPARE_CONTRACT, "id": None, "items": [], "selected_artifact_id": None, "differences": {}}
        items = []
        for item in board.get("items", []):
            asset = self._decorate_asset(item["artifact_id"], state)
            recipe = state["recipes"].get(asset.get("recipe_id"))
            items.append({
                "artifact_id": item["artifact_id"],
                "label": item["label"],
                "asset": asset,
                "recipe": self._public_recipe(recipe) if recipe else None,
                "settings": _copy(recipe.get("settings", {})) if recipe else {},
            })
        differences: dict[str, list[dict[str, Any]]] = {}
        for field in ("media_type", "size_bytes", "favorite", "tags", "recipe_id", "lineage", "provenance", "settings"):
            values = [{"artifact_id": item["artifact_id"], "value": item["asset"].get(field) if field in item["asset"] else item.get(field)} for item in items]
            if len({json.dumps(value["value"], ensure_ascii=False, sort_keys=True) for value in values}) > 1:
                differences[field] = values
        return {
            "contract_version": COMPARE_CONTRACT,
            "id": board["id"],
            "project_id": board["project_id"],
            "items": items,
            "selected_artifact_id": board.get("selected_artifact_id"),
            "differences": differences,
            "updated_at": board.get("updated_at"),
        }

    def get_compare(self, project_id: str) -> dict[str, Any] | None:
        with self._lock:
            state, recovery, _blocked = self._load()
            project = state["projects"].get(project_id)
            return {"status": "completed", "compare": self._compare_payload(state, project), "recovery": recovery} if project else None

    def update_compare(self, project_id: str, payload: object) -> dict[str, Any]:
        if not PROJECT_ID_RE.fullmatch(project_id) or not isinstance(payload, Mapping):
            raise ValueError("Compare board update không hợp lệ.")

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            project = state["projects"].get(project_id)
            if project is None:
                raise KeyError(project_id)
            board = state["compare_boards"].get(project["compare_board_id"])
            if board is None:
                board = self._new_board(new_id("compare"), project_id)
                project["compare_board_id"] = board["id"]
                state["compare_boards"][board["id"]] = board
            if "artifact_id" in payload:
                artifact_id = payload["artifact_id"]
                if not is_artifact_id(artifact_id) or artifact_id not in project["asset_ids"]:
                    raise ValueError("Compare chỉ nhận asset đã thuộc project hiện tại.")
                if artifact_id not in {item["artifact_id"] for item in board["items"]}:
                    if len(board["items"]) >= MAX_COMPARE_ITEMS:
                        raise ValueError("Compare board tối đa 8 artifact.")
                    board["items"].append({"artifact_id": artifact_id, "label": safe_text(payload.get("label", artifact_id), "Nhãn so sánh", maximum=100)})
            if "remove_artifact_id" in payload:
                artifact_id = payload["remove_artifact_id"]
                board["items"] = [item for item in board["items"] if item["artifact_id"] != artifact_id]
                if board.get("selected_artifact_id") == artifact_id:
                    board["selected_artifact_id"] = None
            if "selected_artifact_id" in payload:
                selected = payload["selected_artifact_id"]
                if selected is not None and selected not in {item["artifact_id"] for item in board["items"]}:
                    raise ValueError("Lựa chọn compare không thuộc board.")
                board["selected_artifact_id"] = selected
                if selected and bool(payload.get("favorite_selected", False)):
                    metadata = state["asset_metadata"].get(selected, {"contract_version": ASSET_CONTRACT, "tags": [], "favorite": False, "parent_artifact_id": None, "recipe_id": None, "provenance": {}})
                    metadata["favorite"] = True
                    metadata["updated_at"] = now_iso()
                    state["asset_metadata"][selected] = metadata
            board["updated_at"] = now_iso()
            self._touch_project(state, project_id)
            return self._compare_payload(state, project)

        return {"status": "completed", "compare": self._mutate(mutate)}

    def export_project(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            state, _recovery, _blocked = self._load()
            project = state["projects"].get(project_id)
            if project is None:
                raise KeyError(project_id)
            board = state["compare_boards"].get(project.get("compare_board_id"), {})
            assets = []
            for artifact_id in project["asset_ids"]:
                metadata = state["asset_metadata"].get(artifact_id, {})
                assets.append({
                    "artifact_id": artifact_id,
                    "tags": list(metadata.get("tags", [])),
                    "favorite": bool(metadata.get("favorite", False)),
                    "parent_artifact_id": metadata.get("parent_artifact_id"),
                    "recipe_id": metadata.get("recipe_id"),
                    "provenance": _copy(metadata.get("provenance", {})),
                })
            manifest = {
                "contract_version": PROJECT_EXPORT_CONTRACT,
                "schema_version": 1,
                "exported_at": now_iso(),
                "project": _copy(project),
                "recipes": [self._public_recipe(state["recipes"][recipe_id]) for recipe_id in project["recipe_ids"] if recipe_id in state["recipes"]],
                "assets": assets,
                "compare": _copy(board),
            }
            return {"status": "completed", "manifest": manifest}

    def import_project(self, payload: object) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise ValueError("Project manifest phải là đối tượng JSON.")
        manifest = payload.get("manifest", payload)
        conflict = payload.get("conflict", "copy")
        if conflict not in {"copy", "skip", "replace"}:
            raise ValueError("Conflict policy phải là copy, skip hoặc replace.")
        if not isinstance(manifest, Mapping) or manifest.get("contract_version") != PROJECT_EXPORT_CONTRACT:
            raise ValueError("Project manifest không đúng contract creative-project-export.v1.")
        raw_project = manifest.get("project")
        normalized_project = normalize_project(raw_project, allow_id=True)
        normalized_studio_links = self._normalize_image_mask_project_links(
            raw_project.get("image_mask_studio_links", []) if isinstance(raw_project, Mapping) else [],
            normalized_project["asset_ids"],
        )
        raw_recipes = manifest.get("recipes", [])
        raw_assets = manifest.get("assets", [])
        raw_board = manifest.get("compare", {})
        if not isinstance(raw_recipes, list) or not isinstance(raw_assets, list) or not isinstance(raw_board, Mapping):
            raise ValueError("Project manifest có phần recipes/assets/compare không hợp lệ.")
        normalized_recipes = [normalize_recipe(item, allow_id=True) for item in raw_recipes]

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            source_id = normalized_project.get("id")
            if source_id and source_id in state["projects"] and conflict == "skip":
                return {"imported": False, "project": self._public_project(state["projects"][source_id], state), "missing_asset_ids": []}
            target_id = source_id if source_id and source_id not in state["projects"] else new_id("project")
            if source_id and source_id in state["projects"] and conflict == "replace":
                target_id = source_id
            elif source_id in state["projects"] or conflict == "copy":
                normalized_project["title"] = f"{normalized_project['title']} (import)"[:120]
            if target_id not in state["projects"] and len(state["projects"]) >= MAX_PROJECTS:
                raise ValueError("Đã đạt giới hạn project local.")
            recipe_mapping: dict[str, str] = {}
            for item in normalized_recipes:
                source_recipe_id = item.get("id")
                target_recipe_id = source_recipe_id if source_recipe_id and source_recipe_id not in state["recipes"] else new_id("recipe")
                if source_recipe_id and source_recipe_id in state["recipes"] and conflict == "replace":
                    target_recipe_id = source_recipe_id
                recipe_mapping[str(source_recipe_id or target_recipe_id)] = target_recipe_id
                existing = state["recipes"].get(target_recipe_id, {})
                item.update({"id": target_recipe_id, "created_at": existing.get("created_at", now_iso()), "updated_at": now_iso()})
                state["recipes"][target_recipe_id] = item
            normalized_project["recipe_ids"] = [recipe_mapping.get(recipe_id, recipe_id) for recipe_id in normalized_project["recipe_ids"] if recipe_mapping.get(recipe_id, recipe_id) in state["recipes"]]
            if normalized_project.get("selected_recipe_id"):
                normalized_project["selected_recipe_id"] = recipe_mapping.get(normalized_project["selected_recipe_id"], normalized_project["selected_recipe_id"])
            existing_project = state["projects"].get(target_id, {})
            board_id = existing_project.get("compare_board_id") if conflict == "replace" else new_id("compare")
            timestamp = now_iso()
            project = {
                **normalized_project,
                "id": target_id,
                "created_at": existing_project.get("created_at", timestamp),
                "updated_at": timestamp,
                "compare_board_id": board_id,
                "image_mask_studio_links": _copy(normalized_studio_links),
            }
            state["projects"][target_id] = project
            missing_asset_ids: list[str] = []
            for asset in raw_assets:
                if not isinstance(asset, Mapping) or not is_artifact_id(asset.get("artifact_id")):
                    raise ValueError("Asset reference trong manifest không hợp lệ.")
                artifact_id = asset["artifact_id"]
                if artifact_id not in project["asset_ids"]:
                    continue
                parent = asset.get("parent_artifact_id")
                if parent is not None and not is_artifact_id(parent):
                    raise ValueError("Lineage reference trong manifest không hợp lệ.")
                recipe_id = asset.get("recipe_id")
                recipe_id = recipe_mapping.get(recipe_id, recipe_id) if recipe_id else None
                state["asset_metadata"][artifact_id] = {
                    "contract_version": ASSET_CONTRACT,
                    "tags": normalize_tags(asset.get("tags", [])),
                    "favorite": bool(asset.get("favorite", False)),
                    "parent_artifact_id": parent,
                    "recipe_id": recipe_id if recipe_id in state["recipes"] else None,
                    "provenance": safe_json(asset.get("provenance", {})),
                    "updated_at": timestamp,
                }
                if self._artifact(artifact_id) is None:
                    missing_asset_ids.append(artifact_id)
            items = []
            for item in raw_board.get("items", []):
                if isinstance(item, Mapping) and is_artifact_id(item.get("artifact_id")) and item["artifact_id"] in project["asset_ids"]:
                    items.append({"artifact_id": item["artifact_id"], "label": safe_text(item.get("label", item["artifact_id"]), "Nhãn compare", maximum=100)})
            selected = raw_board.get("selected_artifact_id")
            state["compare_boards"][board_id] = {
                "contract_version": COMPARE_CONTRACT,
                "id": board_id,
                "project_id": target_id,
                "items": items[:MAX_COMPARE_ITEMS],
                "selected_artifact_id": selected if selected in {item["artifact_id"] for item in items} else None,
                "updated_at": timestamp,
            }
            self._touch_project(state, target_id)
            return {"imported": True, "project": self._public_project(project, state), "missing_asset_ids": missing_asset_ids}

        return {"status": "completed", **self._mutate(mutate)}

    def export_recipe_pack(self, recipe_ids: object = None) -> dict[str, Any]:
        with self._lock:
            state, _recovery, _blocked = self._load()
            selected = recipe_ids if isinstance(recipe_ids, list) else list(state["recipes"])
            if not all(isinstance(item, str) and RECIPE_ID_RE.fullmatch(item) for item in selected):
                raise ValueError("Recipe IDs export không hợp lệ.")
            recipes = [self._public_recipe(state["recipes"][recipe_id]) for recipe_id in selected if recipe_id in state["recipes"]]
            return {"status": "completed", "pack": {"contract_version": RECIPE_PACK_CONTRACT, "schema_version": 1, "exported_at": now_iso(), "recipes": recipes}}

    def import_recipe_pack(self, payload: object) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise ValueError("Recipe pack phải là đối tượng JSON.")
        pack = payload.get("pack", payload)
        conflict = payload.get("conflict", "copy")
        if conflict not in {"copy", "skip", "replace"}:
            raise ValueError("Conflict policy phải là copy, skip hoặc replace.")
        if not isinstance(pack, Mapping) or pack.get("contract_version") != RECIPE_PACK_CONTRACT or not isinstance(pack.get("recipes"), list):
            raise ValueError("Recipe pack không đúng contract creative-recipe-pack.v1.")
        normalized = [normalize_recipe(item, allow_id=True) for item in pack["recipes"]]

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            imported: list[dict[str, Any]] = []
            for recipe in normalized:
                source_id = recipe.get("id")
                if source_id and source_id in state["recipes"] and conflict == "skip":
                    continue
                if source_id and source_id in state["recipes"] and conflict == "replace":
                    target_id = source_id
                else:
                    target_id = source_id if source_id and source_id not in state["recipes"] else new_id("recipe")
                    if source_id and source_id in state["recipes"]:
                        recipe["title"] = f"{recipe['title']} (import)"[:120]
                if target_id not in state["recipes"] and len(state["recipes"]) >= MAX_RECIPES:
                    raise ValueError("Đã đạt giới hạn recipe local.")
                existing = state["recipes"].get(target_id, {})
                recipe.update({"id": target_id, "created_at": existing.get("created_at", now_iso()), "updated_at": now_iso()})
                state["recipes"][target_id] = recipe
                imported.append(self._public_recipe(recipe))
            return imported

        return {"status": "completed", "recipes": self._mutate(mutate)}

    def workflow_gallery(self) -> dict[str, Any]:
        from src.services.node_studio.registry import get_definition

        items: list[dict[str, Any]] = []
        workflow_root = ROOT / "workflows"
        if workflow_root.is_dir():
            for path in sorted(workflow_root.glob("*.json")):
                if path.name.endswith(".local.json"):
                    continue
                try:
                    graph = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(graph, Mapping):
                    continue
                node_types = [item.get("type") for item in graph.get("nodes", []) if isinstance(item, Mapping) and isinstance(item.get("type"), str)]
                definitions = [get_definition(node_type) for node_type in node_types]
                non_operational = [definition for definition in definitions if definition and definition.status != "operational"]
                status = "unavailable" if any(definition.status == "unavailable" for definition in non_operational) else "partial" if non_operational else "operational"
                focus = next((definition for definition in non_operational if definition.status == "unavailable"), non_operational[0] if non_operational else None)
                availability = focus.public()["availability"] if focus else {"status": "operational", "reason": "Các node trong template đang có contract operational.", "action": "Chọn template để áp dụng vào Hub Nodes."}
                categories = sorted({definition.category for definition in definitions if definition})
                items.append({
                    "id": path.stem,
                    "title": str(graph.get("title") or path.stem),
                    "description": str(graph.get("description") or ""),
                    "scope": str(graph.get("scope") or "all"),
                    "stage": str(graph.get("stage") or "core"),
                    "categories": categories,
                    "node_count": len(node_types),
                    "status": status,
                    "availability": availability,
                    "preview": {"kind": "workflow", "label": ", ".join(categories[:3]) or "workflow"},
                })
        return {"status": "completed", "gallery": items}

    def search_assets(
        self,
        *,
        query: str = "",
        tags: list[str] | None = None,
        favorite: bool | None = None,
        media_type_prefix: str = "",
        project_id: str = "",
        sort_by: str = "created_at",
        sort_desc: bool = True,
    ) -> dict[str, Any]:
        """Search/filter/sort assets.  Pure metadata — never touches artifact files."""
        state, recovery, _ = self._load()
        assets: list[dict[str, Any]] = list(state.get("asset_metadata", {}).values())
        q = str(query).lower().strip()
        if q:
            assets = [a for a in assets if q in str(a.get("name", "")).lower() or q in str(a.get("description", "")).lower()]
        if tags:
            tag_set = {str(t).lower() for t in tags}
            assets = [a for a in assets if tag_set & {str(t).lower() for t in a.get("tags", [])}]
        if favorite is not None:
            assets = [a for a in assets if bool(a.get("favorite")) == favorite]
        if media_type_prefix:
            assets = [a for a in assets if str(a.get("media_type", "")).startswith(media_type_prefix)]
        if project_id:
            assets = [a for a in assets if a.get("project_id") == project_id or project_id in (a.get("project_ids") or [])]
        valid_sorts = {"created_at", "updated_at", "name", "size_bytes"}
        key = sort_by if sort_by in valid_sorts else "created_at"
        assets.sort(key=lambda a: str(a.get(key, "")), reverse=sort_desc)
        return {"status": "completed", "assets": assets, "recovery": recovery}

    def get_artifact_status(self, artifact_id: str) -> dict[str, Any]:
        """Return metadata about which projects reference an artifact."""
        if not isinstance(artifact_id, str) or not ARTIFACT_ID_RE.fullmatch(artifact_id):
            return {"found": False, "reason": "Artifact ID không hợp lệ."}
        state, _, _ = self._load()
        asset = state.get("asset_metadata", {}).get(artifact_id)
        if not asset:
            return {"found": False, "artifact_id": artifact_id, "project_ids": [], "favorite": False, "tags": []}
        project_ids = []
        for proj in state.get("projects", {}).values():
            if artifact_id in (proj.get("artifact_ids") or []):
                project_ids.append(proj.get("id"))
        return {
            "found": True,
            "artifact_id": artifact_id,
            "project_ids": project_ids,
            "favorite": bool(asset.get("favorite")),
            "tags": list(asset.get("tags") or []),
        }

    def export_manifest(self, project_id: str) -> dict[str, Any]:
        """Export a sanitised manifest for a project (no paths, no secrets)."""
        if not isinstance(project_id, str) or not PROJECT_ID_RE.fullmatch(project_id):
            return {"accepted": False, "reason": "Project ID không hợp lệ."}
        state, _, _ = self._load()
        project = state.get("projects", {}).get(project_id)
        if not project:
            return {"accepted": False, "reason": "Project không tồn tại."}
        artifact_ids = list(project.get("artifact_ids") or [])
        assets = [
            {k: v for k, v in (state.get("asset_metadata", {}).get(aid) or {}).items()
             if k not in {"local_path", "source_path", "api_key", "token", "password", "secret"}}
            for aid in artifact_ids
        ]
        import hashlib as _hl
        manifest = {
            "manifest_schema_version": 1,
            "created_at": now_iso(),
            "project_id": project_id,
            "project_title": project.get("title", ""),
            "artifact_count": len(artifact_ids),
            "artifact_ids": artifact_ids,
            "assets": assets,
            "checksum": _hl.sha256(json.dumps(artifact_ids, sort_keys=True).encode()).hexdigest(),
        }
        return {"accepted": True, "manifest": manifest}

    def missing_artifact_state(self, project_id: str) -> dict[str, Any]:
        """Report assets of a project whose artifact IDs are not in asset_metadata."""
        if not isinstance(project_id, str) or not PROJECT_ID_RE.fullmatch(project_id):
            return {"accepted": False, "reason": "Project ID không hợp lệ."}
        state, _, _ = self._load()
        project = state.get("projects", {}).get(project_id)
        if not project:
            return {"accepted": False, "reason": "Project không tồn tại."}
        artifact_ids = list(project.get("artifact_ids") or [])
        known_ids = set(state.get("asset_metadata", {}).keys())
        missing = [aid for aid in artifact_ids if aid not in known_ids]
        return {
            "accepted": True,
            "project_id": project_id,
            "total": len(artifact_ids),
            "missing_count": len(missing),
            "missing_artifact_ids": missing,
        }

    def overview(self) -> dict[str, Any]:
        projects = self.list_projects()
        recipes = self.list_recipes()
        assets = self.list_assets()
        collections = self.list_collections()
        return {
            "status": "completed",
            "contract_version": WORKSPACE_CONTRACT,
            "projects": projects["projects"],
            "recent_projects": projects["recent_projects"],
            "recipes": recipes["recipes"],
            "assets": assets["assets"],
            "collections": collections["collections"],
            "gallery": self.workflow_gallery()["gallery"],
            "recovery": projects["recovery"],
        }


project_manager = CreativeProjectManager()
