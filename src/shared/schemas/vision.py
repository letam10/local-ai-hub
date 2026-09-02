"""Opaque result and selection contracts shared by Vision and SAM2.

The browser works with normalized geometry and opaque artifact identifiers.
This module deliberately contains no filesystem or runtime access so it can be
used at the API boundary, by adapters, and by the public job projection
without making a worker decision on behalf of the server.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any


VISION_ANNOTATION_SCHEMA = "vision.annotation.v1"
SAM2_SELECTION_SCHEMA = "sam2.selection.v1"
MAX_SELECTION_POINTS = 32
MAX_DETECTIONS = 256
MAX_FRAME_INDEX = 10_000_000
_ARTIFACT_ID = re.compile(r"artifact_[a-f0-9]{32}\Z")


class VisionContractError(ValueError):
    """A finite, safe validation failure for the public vision boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def is_opaque_artifact_id(value: object) -> bool:
    return isinstance(value, str) and _ARTIFACT_ID.fullmatch(value) is not None


def _finite_number(value: object, *, code: str, label: str) -> float:
    if isinstance(value, bool):
        raise VisionContractError(code, f"{label} phải là số hữu hạn.")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise VisionContractError(code, f"{label} phải là số hữu hạn.") from None
    if not math.isfinite(number):
        raise VisionContractError(code, f"{label} phải là số hữu hạn.")
    return number


def _bounded_unit(value: object, *, code: str, label: str) -> float:
    number = _finite_number(value, code=code, label=label)
    if not 0.0 <= number <= 1.0:
        raise VisionContractError(code, f"{label} phải nằm trong khoảng [0,1].")
    return round(number, 8)


def normalize_point(value: object, *, label: str = "Điểm") -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise VisionContractError("selection_point_invalid", f"{label} không hợp lệ.")
    # Public points are always normalized.  A false marker is rejected rather
    # than silently treating pixel coordinates as normalized values.
    if "normalized" in value and value.get("normalized") is not True:
        raise VisionContractError("selection_coordinates_not_normalized", f"{label} phải dùng tọa độ chuẩn hóa.")
    x = _bounded_unit(value.get("x"), code="selection_point_invalid", label=f"{label} x")
    y = _bounded_unit(value.get("y"), code="selection_point_invalid", label=f"{label} y")
    raw_label = value.get("label", 1)
    if isinstance(raw_label, bool) or raw_label not in (0, 1, "0", "1"):
        raise VisionContractError("selection_point_label_invalid", f"Nhãn {label.lower()} phải là 0 hoặc 1.")
    return {"x": x, "y": y, "label": int(raw_label), "normalized": True}


def normalize_points(value: object, *, required: bool = False) -> list[dict[str, Any]]:
    if value is None:
        if required:
            raise VisionContractError("selection_points_required", "Cần ít nhất một điểm SAM2.")
        return []
    if not isinstance(value, list) or len(value) > MAX_SELECTION_POINTS:
        raise VisionContractError("selection_points_limit", f"SAM2 chỉ nhận tối đa {MAX_SELECTION_POINTS} điểm.")
    if required and not value:
        raise VisionContractError("selection_points_required", "Cần ít nhất một điểm SAM2.")
    return [normalize_point(item, label=f"Điểm {index + 1}") for index, item in enumerate(value)]


def normalize_box(value: object, *, required: bool = False) -> list[float] | None:
    if value is None:
        if required:
            raise VisionContractError("selection_box_required", "Cần một box SAM2.")
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise VisionContractError("selection_box_invalid", "Box SAM2 phải có bốn tọa độ chuẩn hóa.")
    box = [
        _bounded_unit(item, code="selection_box_invalid", label=f"Box[{index}]")
        for index, item in enumerate(value)
    ]
    if box[2] <= box[0] or box[3] <= box[1]:
        raise VisionContractError("selection_box_invalid", "Box SAM2 phải có phải/dưới lớn hơn trái/trên.")
    return box


def _bounded_threshold(value: object, *, label: str) -> float:
    return _bounded_unit(value, code="threshold_invalid", label=label)


def _frame_index(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_FRAME_INDEX:
        raise VisionContractError("frame_index_invalid", "Frame index phải là số nguyên không âm trong giới hạn.")
    return value


def normalize_tool_payload(tool: str, payload: object) -> tuple[dict[str, Any], str | None]:
    """Normalize M3 request geometry without resolving an artifact to a path.

    Artifact ownership and media type are checked by the adapter's existing
    server-owned artifact resolver immediately before worker launch.  Keeping
    that lookup out of this pure function also preserves compatibility with
    queued/retry contract tests that use opaque fixture IDs.
    """

    if not isinstance(payload, Mapping):
        return {}, "Yêu cầu tool phải là JSON object."
    result = dict(payload)
    try:
        if tool in {"parse_screen", "detect_objects", "ground_objects"}:
            for field in ("box_threshold", "threshold", "text_threshold"):
                if field in result and result[field] not in (None, ""):
                    result[field] = _bounded_threshold(result[field], label=field)
            if tool == "ground_objects":
                prompt = result.get("prompt")
                if not isinstance(prompt, str) or not prompt.strip():
                    raise VisionContractError("prompt_required", "Grounding DINO cần prompt ngắn.")
                result["prompt"] = prompt.strip()[:300]
        if tool in {"segment_from_points", "track_video_object"} and "points" in result:
            result["points"] = normalize_points(result.get("points"), required=False)
        if tool in {"segment_from_box", "track_video_object", "segment_from_text"} and "box" in result:
            result["box"] = normalize_box(result.get("box"), required=True)
            result["normalized_box"] = True
        if tool == "segment_from_box" and "box" not in result:
            raise VisionContractError("selection_box_required", "Cần kéo một box trên canvas SAM2.")
        if tool == "segment_from_points" and "box" in result:
            raise VisionContractError("selection_mode_mismatch", "Tool chọn điểm không nhận box.")
        if tool == "track_video_object":
            result["frame_index"] = _frame_index(result.get("frame_index", 0))
            has_points = bool(result.get("points"))
            has_box = result.get("box") is not None
            if not has_points and not has_box:
                raise VisionContractError("tracking_selection_required", "Theo dõi video cần điểm hoặc box.")
        if tool == "segment_from_points" and "points" in result and not result["points"]:
            raise VisionContractError("selection_points_required", "Cần ít nhất một điểm SAM2.")
        for field in ("source_artifact_id", "asset_id"):
            if field in result and result[field] not in (None, "") and not is_opaque_artifact_id(result[field]):
                raise VisionContractError("artifact_id_invalid", "Artifact input phải là ID opaque do Hub cấp.")
    except VisionContractError as exc:
        return result, str(exc)
    return result, None


def _artifact_ids(value: object, *, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 64 or not all(is_opaque_artifact_id(item) for item in value):
        raise VisionContractError("artifact_id_invalid", f"{label} phải là danh sách artifact ID opaque.")
    return list(dict.fromkeys(value))


def _detection_box(value: Mapping[str, Any], *, width: int, height: int) -> list[float] | None:
    candidate = value.get("normalized_box")
    if candidate is None:
        candidate = value.get("box_normalized_xyxy")
    if candidate is None:
        cxcywh = value.get("box_normalized_cxcywh")
        if isinstance(cxcywh, Sequence) and not isinstance(cxcywh, (str, bytes)) and len(cxcywh) == 4:
            try:
                cx, cy, box_width, box_height = [float(item) for item in cxcywh]
                candidate = [cx - box_width / 2, cy - box_height / 2, cx + box_width / 2, cy + box_height / 2]
            except (TypeError, ValueError):
                candidate = None
    if candidate is None:
        candidate = value.get("bbox", value.get("box"))
        if (
            isinstance(candidate, Sequence)
            and not isinstance(candidate, (str, bytes))
            and len(candidate) == 4
            and width > 0
            and height > 0
        ):
            candidate = [
                float(candidate[0]) / width,
                float(candidate[1]) / height,
                float(candidate[2]) / width,
                float(candidate[3]) / height,
            ]
    try:
        return normalize_box(candidate, required=True) if candidate is not None else None
    except (TypeError, ValueError, VisionContractError):
        return None


def build_vision_annotation(
    source_artifact_id: object,
    worker_result: Mapping[str, Any] | None = None,
    *,
    annotated_artifact_ids: object = None,
) -> dict[str, Any]:
    """Build a safe annotation projection from common worker box shapes."""

    if not is_opaque_artifact_id(source_artifact_id):
        raise VisionContractError("artifact_id_invalid", "Nguồn annotation phải là artifact ID opaque.")
    worker = worker_result if isinstance(worker_result, Mapping) else {}
    raw_width = worker.get("width", 0)
    raw_height = worker.get("height", 0)
    width = int(raw_width) if isinstance(raw_width, int) and not isinstance(raw_width, bool) and 0 <= raw_width <= 100_000_000 else 0
    height = int(raw_height) if isinstance(raw_height, int) and not isinstance(raw_height, bool) and 0 <= raw_height <= 100_000_000 else 0
    raw_detections = worker.get("detections")
    if not isinstance(raw_detections, list):
        raw_detections = worker.get("objects")
    if not isinstance(raw_detections, list):
        raw_detections = worker.get("grounded")
    detections: list[dict[str, Any]] = []
    for item in raw_detections[:MAX_DETECTIONS] if isinstance(raw_detections, list) else []:
        if not isinstance(item, Mapping):
            continue
        box = _detection_box(item, width=width, height=height)
        if box is None:
            continue
        label = str(item.get("label", item.get("phrase", item.get("class_name", "object"))))[:120].strip() or "object"
        confidence_value = item.get("confidence", item.get("score"))
        confidence = None
        if confidence_value is not None:
            try:
                confidence = _bounded_unit(confidence_value, code="detection_confidence_invalid", label="confidence")
            except VisionContractError:
                continue
        detections.append({"normalized_box": box, "label": label, "confidence": confidence})
    return {
        "schema_version": VISION_ANNOTATION_SCHEMA,
        "source_artifact_id": source_artifact_id,
        "width": width,
        "height": height,
        "detections": detections,
        "annotated_artifact_ids": _artifact_ids(annotated_artifact_ids, label="annotated_artifact_ids"),
    }


def build_sam2_selection(
    source_artifact_id: object,
    *,
    frame_index: object = 0,
    points: object = None,
    box: object = None,
    mask_artifact_ids: object = None,
    overlay_artifact_ids: object = None,
    candidate_score: object = None,
) -> dict[str, Any]:
    if not is_opaque_artifact_id(source_artifact_id):
        raise VisionContractError("artifact_id_invalid", "Nguồn SAM2 phải là artifact ID opaque.")
    normalized_points = normalize_points(points, required=False)
    normalized_box = normalize_box(box, required=False)
    score = None
    if candidate_score is not None:
        score = _bounded_unit(candidate_score, code="candidate_score_invalid", label="candidate score")
    result: dict[str, Any] = {
        "schema_version": SAM2_SELECTION_SCHEMA,
        "source_artifact_id": source_artifact_id,
        "frame_index": _frame_index(frame_index),
        "positive_points": [{key: point[key] for key in ("x", "y")} for point in normalized_points if point["label"] == 1],
        "negative_points": [{key: point[key] for key in ("x", "y")} for point in normalized_points if point["label"] == 0],
        "normalized_box": normalized_box,
        "mask_artifact_ids": _artifact_ids(mask_artifact_ids, label="mask_artifact_ids"),
        "overlay_artifact_ids": _artifact_ids(overlay_artifact_ids, label="overlay_artifact_ids"),
    }
    if score is not None:
        result["candidate_score"] = score
    return result


def validate_public_result_contract(value: object, *, key: str) -> dict[str, Any]:
    """Validate and canonicalize a worker contract before job persistence."""

    if not isinstance(value, Mapping):
        raise VisionContractError("result_contract_invalid", f"{key} result contract không hợp lệ.")
    if key == "annotation":
        if value.get("schema_version") != VISION_ANNOTATION_SCHEMA:
            raise VisionContractError("result_contract_invalid", "Vision result không đúng vision.annotation.v1.")
        source = value.get("source_artifact_id")
        if not is_opaque_artifact_id(source):
            raise VisionContractError("result_contract_invalid", "Vision result thiếu source artifact opaque.")
        return build_vision_annotation(source, value, annotated_artifact_ids=value.get("annotated_artifact_ids"))
    if key == "selection":
        if value.get("schema_version") != SAM2_SELECTION_SCHEMA:
            raise VisionContractError("result_contract_invalid", "SAM2 result không đúng sam2.selection.v1.")
        raw_positive = value.get("positive_points", [])
        raw_negative = value.get("negative_points", [])
        if not isinstance(raw_positive, list) or not isinstance(raw_negative, list):
            raise VisionContractError("result_contract_invalid", "SAM2 result có danh sách điểm không hợp lệ.")
        positive = normalize_points([{**item, "label": 1} for item in raw_positive if isinstance(item, Mapping)], required=False)
        if len(positive) != len(raw_positive):
            raise VisionContractError("result_contract_invalid", "SAM2 result có điểm dương không hợp lệ.")
        negative = normalize_points([{**item, "label": 0} for item in raw_negative if isinstance(item, Mapping)], required=False)
        if len(negative) != len(raw_negative):
            raise VisionContractError("result_contract_invalid", "SAM2 result có điểm âm không hợp lệ.")
        return build_sam2_selection(
            value.get("source_artifact_id"),
            frame_index=value.get("frame_index", 0),
            points=[*positive, *negative],
            box=value.get("normalized_box"),
            mask_artifact_ids=value.get("mask_artifact_ids"),
            overlay_artifact_ids=value.get("overlay_artifact_ids"),
            candidate_score=value.get("candidate_score"),
        )
    raise VisionContractError("result_contract_invalid", "Result contract không được allowlist.")


def attach_published_artifacts(contract: Mapping[str, Any], artifacts: object, *, key: str) -> dict[str, Any]:
    """Attach only IDs from the Job Manager's already-published artifacts."""

    safe = validate_public_result_contract(contract, key=key)
    published = [
        item.get("id")
        for item in artifacts if isinstance(item, Mapping) and is_opaque_artifact_id(item.get("id"))
    ] if isinstance(artifacts, list) else []
    if key == "annotation":
        safe["annotated_artifact_ids"] = list(dict.fromkeys(published))
    else:
        image_ids = [
            item.get("id")
            for item in artifacts
            if isinstance(item, Mapping)
            and is_opaque_artifact_id(item.get("id"))
            and str(item.get("media_type") or "").startswith("image/")
        ] if isinstance(artifacts, list) else []
        video_ids = [
            item.get("id")
            for item in artifacts
            if isinstance(item, Mapping)
            and is_opaque_artifact_id(item.get("id"))
            and str(item.get("media_type") or "").startswith("video/")
        ] if isinstance(artifacts, list) else []
        safe["mask_artifact_ids"] = list(dict.fromkeys(image_ids))
        safe["overlay_artifact_ids"] = list(dict.fromkeys([*video_ids, *[item for item in image_ids if item not in safe["mask_artifact_ids"]]]))
        # A segmentation job normally has one mask and one overlay.  Keep the
        # first image as mask and expose any remaining image/video artifacts as
        # overlay without inventing IDs.
        if image_ids:
            safe["mask_artifact_ids"] = [image_ids[0]]
            safe["overlay_artifact_ids"] = list(dict.fromkeys([*video_ids, *image_ids[1:]]))
    return safe


__all__ = [
    "MAX_FRAME_INDEX",
    "MAX_SELECTION_POINTS",
    "SAM2_SELECTION_SCHEMA",
    "VISION_ANNOTATION_SCHEMA",
    "VisionContractError",
    "attach_published_artifacts",
    "build_sam2_selection",
    "build_vision_annotation",
    "is_opaque_artifact_id",
    "normalize_box",
    "normalize_points",
    "normalize_tool_payload",
    "validate_public_result_contract",
]
