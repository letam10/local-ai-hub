"""Validation and public contracts for Milestone 4A creative workspace data.

Creative projects deliberately store only small JSON metadata and opaque Hub
references.  Files remain owned by the artifact store; models, outputs,
machine paths and secrets are never copied into project or recipe manifests.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any


WORKSPACE_CONTRACT = "creative-workspace.v1"
PROJECT_CONTRACT = "creative-project.v1"
PROJECT_EXPORT_CONTRACT = "creative-project-export.v1"
ASSET_CONTRACT = "creative-asset.v1"
RECIPE_CONTRACT = "creative-recipe.v1"
RECIPE_PACK_CONTRACT = "creative-recipe-pack.v1"
COMPARE_CONTRACT = "creative-compare.v1"

ARTIFACT_ID_RE = re.compile(r"^artifact_[a-f0-9]{32}$")
PROJECT_ID_RE = re.compile(r"^project_[a-f0-9]{32}$")
RECIPE_ID_RE = re.compile(r"^recipe_[a-f0-9]{32}$")
COLLECTION_ID_RE = re.compile(r"^collection_[a-f0-9]{32}$")
COMPARE_ID_RE = re.compile(r"^compare_[a-f0-9]{32}$")
PRESET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
VARIABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,39}$")
LOCAL_PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\)")

MAX_PROJECTS = 120
MAX_RECIPES = 160
MAX_ASSETS_PER_PROJECT = 240
MAX_COMPARE_ITEMS = 8
MAX_COLLECTIONS = 80
MAX_TAGS = 16

_FORBIDDEN_KEYS = {
    "api_key",
    "credential",
    "credentials",
    "password",
    "path",
    "paths",
    "secret",
    "token",
    "output_path",
    "input_path",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def is_artifact_id(value: object) -> bool:
    return isinstance(value, str) and bool(ARTIFACT_ID_RE.fullmatch(value))


def _text(value: object, field: str, *, maximum: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} phải là chuỗi văn bản.")
    result = value.strip()
    if not result and not allow_empty:
        raise ValueError(f"{field} không được để trống.")
    if len(result) > maximum:
        raise ValueError(f"{field} vượt giới hạn {maximum} ký tự.")
    if "\x00" in result or LOCAL_PATH_RE.search(result):
        raise ValueError(f"{field} không được chứa đường dẫn máy cục bộ.")
    return result


def safe_text(value: object, field: str, *, maximum: int = 500, allow_empty: bool = False) -> str:
    return _text(value, field, maximum=maximum, allow_empty=allow_empty)


def normalize_tags(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("tags phải là danh sách.")
    tags: list[str] = []
    for item in value:
        tag = _text(item, "Tag", maximum=40).lower()
        if tag not in tags:
            tags.append(tag)
        if len(tags) >= MAX_TAGS:
            break
    return tags


def safe_json(value: Any, *, depth: int = 0) -> Any:
    """Accept small display/settings metadata while rejecting private material."""

    if depth > 4:
        raise ValueError("Metadata lồng quá sâu.")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _text(value, "Metadata", maximum=800, allow_empty=True)
    if isinstance(value, list):
        if len(value) > 80:
            raise ValueError("Metadata có quá nhiều phần tử.")
        return [safe_json(item, depth=depth + 1) for item in value]
    if isinstance(value, Mapping):
        if len(value) > 80:
            raise ValueError("Metadata có quá nhiều trường.")
        result: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = _text(raw_key, "Tên trường metadata", maximum=80)
            lowered = key.lower()
            if lowered in _FORBIDDEN_KEYS or lowered.endswith("_path") or lowered.endswith("_secret"):
                raise ValueError("Metadata không được chứa path hoặc secret.")
            result[key] = safe_json(item, depth=depth + 1)
        return result
    raise ValueError("Metadata phải là JSON an toàn.")


def normalize_recipe(value: object, *, allow_id: bool = False) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("Recipe phải là đối tượng JSON.")
    title = _text(value.get("title", ""), "Tên recipe", maximum=120)
    prompt_template = _text(value.get("prompt_template", ""), "Prompt template", maximum=4000, allow_empty=True)
    style_block = _text(value.get("style_block", ""), "Style block", maximum=2000, allow_empty=True)
    negative_block = _text(value.get("negative_block", ""), "Negative block", maximum=2000, allow_empty=True)
    model = _text(value.get("model", "flux"), "Model", maximum=120, allow_empty=True) or "flux"
    raw_seed = value.get("seed", 42)
    if not isinstance(raw_seed, int) or isinstance(raw_seed, bool) or raw_seed < 0:
        raise ValueError("Seed phải là số nguyên không âm.")
    raw_variables = value.get("variables", [])
    if not isinstance(raw_variables, list) or len(raw_variables) > 32:
        raise ValueError("Variables phải là danh sách tối đa 32 mục.")
    variables: list[dict[str, Any]] = []
    names: set[str] = set()
    for item in raw_variables:
        if not isinstance(item, Mapping):
            raise ValueError("Mỗi variable phải là đối tượng.")
        name = _text(item.get("name", ""), "Tên variable", maximum=40)
        if not VARIABLE_RE.fullmatch(name) or name in names:
            raise ValueError("Tên variable không hợp lệ hoặc bị lặp.")
        names.add(name)
        variables.append({
            "name": name,
            "label": _text(item.get("label", name), "Nhãn variable", maximum=100),
            "default": _text(item.get("default", ""), "Giá trị mặc định", maximum=800, allow_empty=True),
            "required": bool(item.get("required", False)),
        })
    workflow_preset = value.get("workflow_preset") or None
    if workflow_preset is not None and (not isinstance(workflow_preset, str) or not PRESET_ID_RE.fullmatch(workflow_preset)):
        raise ValueError("workflow_preset không hợp lệ.")
    version = value.get("version", 1)
    if not isinstance(version, int) or isinstance(version, bool) or version < 1 or version > 999:
        raise ValueError("Version recipe không hợp lệ.")
    result = {
        "contract_version": RECIPE_CONTRACT,
        "title": title,
        "version": version,
        "prompt_template": prompt_template,
        "variables": variables,
        "style_block": style_block,
        "negative_block": negative_block,
        "seed": raw_seed,
        "model": model,
        "settings": safe_json(value.get("settings", {})),
        "workflow_preset": workflow_preset,
        "tags": normalize_tags(value.get("tags", [])),
    }
    if allow_id and isinstance(value.get("id"), str) and RECIPE_ID_RE.fullmatch(value["id"]):
        result["id"] = value["id"]
    return result


def normalize_project(value: object, *, allow_id: bool = False) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("Project phải là đối tượng JSON.")
    title = _text(value.get("title", ""), "Tên project", maximum=120)
    description = _text(value.get("description", ""), "Mô tả project", maximum=1000, allow_empty=True)
    status = value.get("status", "active")
    if status not in {"active", "archived"}:
        raise ValueError("Trạng thái project không hợp lệ.")
    asset_ids = value.get("asset_ids", [])
    if not isinstance(asset_ids, list) or len(asset_ids) > MAX_ASSETS_PER_PROJECT or not all(is_artifact_id(item) for item in asset_ids):
        raise ValueError("asset_ids phải là danh sách artifact ID hợp lệ.")
    recipe_ids = value.get("recipe_ids", [])
    if not isinstance(recipe_ids, list) or len(recipe_ids) > MAX_RECIPES or not all(isinstance(item, str) and RECIPE_ID_RE.fullmatch(item) for item in recipe_ids):
        raise ValueError("recipe_ids không hợp lệ.")
    result = {
        "contract_version": PROJECT_CONTRACT,
        "title": title,
        "description": description,
        "status": status,
        "tags": normalize_tags(value.get("tags", [])),
        "asset_ids": list(dict.fromkeys(asset_ids)),
        "recipe_ids": list(dict.fromkeys(recipe_ids)),
        "selected_asset_id": value.get("selected_asset_id") if is_artifact_id(value.get("selected_asset_id")) else None,
        "selected_recipe_id": value.get("selected_recipe_id") if isinstance(value.get("selected_recipe_id"), str) and RECIPE_ID_RE.fullmatch(value["selected_recipe_id"]) else None,
        "workflow_preset": value.get("workflow_preset") if isinstance(value.get("workflow_preset"), str) and PRESET_ID_RE.fullmatch(value["workflow_preset"]) else None,
    }
    if result["selected_asset_id"] and result["selected_asset_id"] not in result["asset_ids"]:
        result["selected_asset_id"] = None
    if result["selected_recipe_id"] and result["selected_recipe_id"] not in result["recipe_ids"]:
        result["selected_recipe_id"] = None
    if allow_id and isinstance(value.get("id"), str) and PROJECT_ID_RE.fullmatch(value["id"]):
        result["id"] = value["id"]
    return result


def render_recipe(recipe: Mapping[str, Any], values: object) -> dict[str, Any]:
    raw_values = values if isinstance(values, Mapping) else {}
    substitutions: dict[str, str] = {}
    for variable in recipe.get("variables", []):
        name = str(variable.get("name", ""))
        supplied = raw_values.get(name, variable.get("default", ""))
        text = _text(supplied, f"Giá trị {name}", maximum=800, allow_empty=not bool(variable.get("required")))
        if bool(variable.get("required")) and not text:
            raise ValueError(f"Variable bắt buộc '{name}' chưa có giá trị.")
        substitutions[name] = text
    prompt = str(recipe.get("prompt_template", ""))
    for name, text in substitutions.items():
        prompt = prompt.replace("{{" + name + "}}", text).replace("${" + name + "}", text)
    style = str(recipe.get("style_block", "")).strip()
    if style:
        prompt = "\n".join(part for part in (prompt.strip(), style) if part)
    return {
        "prompt": prompt.strip(),
        "negative_prompt": str(recipe.get("negative_block", "")).strip(),
        "seed": recipe.get("seed", 42),
        "model": str(recipe.get("model", "flux")),
        "settings": safe_json(recipe.get("settings", {})),
        "workflow_preset": recipe.get("workflow_preset"),
        "recipe_id": recipe.get("id"),
        "recipe_version": recipe.get("version", 1),
    }
