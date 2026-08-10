"""Typed, path-safe contracts for non-destructive Image & Mask Studio data."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from typing import Any

from src.services.project_manager.schemas import is_artifact_id, new_id, now_iso, safe_json, safe_text


STUDIO_CONTRACT = "image-mask-studio.v1"
STATE_CONTRACT = "image-mask-studio-state.v1"
MASK_EXPORT_CONTRACT = "image-mask-export.v1"
PRESET_CONTRACT = "image-mask-preset.v1"

STUDIO_ID_RE = re.compile(r"^studio_[a-f0-9]{32}$")
LAYER_ID_RE = re.compile(r"^layer_[a-f0-9]{32}$")
SNAPSHOT_ID_RE = re.compile(r"^snapshot_[a-f0-9]{32}$")
PRESET_ID_RE = re.compile(r"^maskpreset_[a-f0-9]{32}$")
PROJECT_ID_RE = re.compile(r"^project_[a-f0-9]{32}$")

LAYER_KINDS = {"source", "mask", "adjustment", "generated"}
ADJUSTMENT_KINDS = {"brightness", "contrast", "saturation", "exposure", "temperature", "crop"}
MASK_OPERATIONS = {"brush", "invert", "feather", "grow", "shrink"}
BRUSH_MODES = {"add", "subtract"}


def copy_json(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def opaque_id(value: object, pattern: re.Pattern[str], field: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"{field} không hợp lệ.")
    return value


def optional_project_id(value: object) -> str | None:
    if value is None or value == "":
        return None
    return opaque_id(value, PROJECT_ID_RE, "Project ID")


def bounded_float(value: object, field: str, *, minimum: float, maximum: float, default: float | None = None) -> float:
    if value is None and default is not None:
        return default
    if isinstance(value, bool):
        raise ValueError(f"{field} phải là số.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} phải là số.") from exc
    if not math.isfinite(number) or number < minimum or number > maximum:
        raise ValueError(f"{field} phải nằm trong khoảng {minimum:g}–{maximum:g}.")
    return round(number, 6)


def normalize_artifact_id(value: object, field: str = "Artifact ID") -> str:
    if not is_artifact_id(value):
        raise ValueError(f"{field} phải là artifact ID opaque của Hub.")
    return str(value)


def layer_summary(layer: Mapping[str, Any]) -> dict[str, Any]:
    """Return the declarative portion allowed in snapshots/presets."""

    result = {
        "id": str(layer["id"]),
        "kind": str(layer["kind"]),
        "name": str(layer["name"]),
        "visible": bool(layer.get("visible", True)),
        "opacity": float(layer.get("opacity", 1)),
    }
    if layer.get("artifact_id") is not None:
        result["artifact_id"] = normalize_artifact_id(layer["artifact_id"])
    if layer["kind"] == "mask":
        result["operations"] = copy_json(layer.get("operations", []))
    if layer["kind"] == "adjustment":
        result["adjustment"] = copy_json(layer.get("adjustment", {}))
    if layer["kind"] == "generated":
        parent_layer_id = layer.get("parent_layer_id")
        if parent_layer_id:
            result["parent_layer_id"] = opaque_id(parent_layer_id, LAYER_ID_RE, "Layer nguồn")
        result["provenance"] = copy_json(layer.get("provenance", {}))
    return result


def normalize_mask_operation(value: object, *, max_points: int) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("Thao tác mask phải là object.")
    operation = value.get("operation")
    if operation not in MASK_OPERATIONS:
        raise ValueError("Thao tác mask không thuộc allowlist.")
    result: dict[str, Any] = {
        "id": new_id("maskop"),
        "operation": operation,
        "created_at": now_iso(),
    }
    if operation == "brush":
        mode = value.get("mode", "add")
        if mode not in BRUSH_MODES:
            raise ValueError("Brush mask chỉ hỗ trợ add hoặc subtract.")
        points = value.get("points")
        if not isinstance(points, list) or not (1 <= len(points) <= max_points):
            raise ValueError(f"Brush cần 1–{max_points} điểm tọa độ chuẩn hóa.")
        normalized_points: list[dict[str, float]] = []
        for point in points:
            if not isinstance(point, Mapping):
                raise ValueError("Điểm brush phải là object.")
            normalized_points.append({
                "x": bounded_float(point.get("x"), "Tọa độ X", minimum=0, maximum=1),
                "y": bounded_float(point.get("y"), "Tọa độ Y", minimum=0, maximum=1),
            })
        result.update({
            "mode": mode,
            "points": normalized_points,
            "size": bounded_float(value.get("size"), "Kích thước brush", minimum=0.002, maximum=1, default=0.06),
            "strength": bounded_float(value.get("strength"), "Cường độ brush", minimum=0.01, maximum=1, default=1),
        })
    elif operation in {"feather", "grow", "shrink"}:
        result["amount"] = bounded_float(value.get("amount"), "Mức điều chỉnh mask", minimum=0.001, maximum=1, default=0.08)
    return result


def normalize_adjustment(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("Adjustment layer phải có cài đặt hợp lệ.")
    kind = value.get("kind")
    if kind not in ADJUSTMENT_KINDS:
        raise ValueError("Loại adjustment không thuộc allowlist.")
    settings = safe_json(value.get("settings", {}))
    return {"kind": kind, "settings": settings}


def new_layer(
    kind: str,
    *,
    name: object | None = None,
    artifact_id: object | None = None,
    adjustment: object | None = None,
    parent_layer_id: object | None = None,
    provenance: object | None = None,
) -> dict[str, Any]:
    if kind not in LAYER_KINDS:
        raise ValueError("Loại layer không hợp lệ.")
    label = safe_text(name if name is not None else {"source": "Ảnh nguồn", "mask": "Mặt nạ", "adjustment": "Điều chỉnh", "generated": "Dẫn xuất"}[kind], "Tên layer", maximum=120)
    layer: dict[str, Any] = {
        "id": new_id("layer"),
        "kind": kind,
        "name": label,
        "visible": True,
        "opacity": 1.0,
    }
    if kind in {"source", "generated"}:
        layer["artifact_id"] = normalize_artifact_id(artifact_id)
    elif kind == "mask":
        if artifact_id is not None:
            layer["artifact_id"] = normalize_artifact_id(artifact_id)
        layer["operations"] = []
    elif kind == "adjustment":
        layer["adjustment"] = normalize_adjustment(adjustment)
    if kind == "generated":
        if parent_layer_id is not None:
            layer["parent_layer_id"] = opaque_id(parent_layer_id, LAYER_ID_RE, "Layer nguồn")
        layer["provenance"] = safe_json(provenance or {})
    return layer


def normalize_layer_update(layer: Mapping[str, Any], payload: object) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("Cập nhật layer phải là object.")
    result = layer_summary(layer)
    if "name" in payload:
        result["name"] = safe_text(payload["name"], "Tên layer", maximum=120)
    if "visible" in payload:
        result["visible"] = bool(payload["visible"])
    if "opacity" in payload:
        result["opacity"] = bounded_float(payload["opacity"], "Độ mờ", minimum=0, maximum=1)
    if layer.get("kind") == "adjustment" and "adjustment" in payload:
        result["adjustment"] = normalize_adjustment(payload["adjustment"])
    return result


def public_operation(operation: Mapping[str, Any]) -> dict[str, Any]:
    return copy_json(dict(operation))
