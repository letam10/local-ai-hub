"""Isolated SAM2 worker invoked by the Hub adapter over JSON stdin."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


def _emit(payload: dict[str, Any], code: int | None = None) -> int:
    print(json.dumps(payload, ensure_ascii=False))
    return int(code if code is not None else (0 if payload.get("status") == "completed" else 2))


def _request() -> dict[str, Any]:
    value = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON request phải là object.")
    return value


def _is_reparse(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        return bool(getattr(os.lstat(path), "st_file_attributes", 0) & 0x400)
    except OSError:
        return True


def _safe_existing_under(root: Path, candidate: Path, *, kind: str) -> Path | None:
    """Resolve one existing leaf without allowing a reparse traversal."""

    try:
        lexical_root = Path(os.path.abspath(str(root)))
        lexical_candidate = Path(os.path.abspath(str(candidate)))
        relative = lexical_candidate.relative_to(lexical_root)
        if not lexical_root.is_dir() or _is_reparse(lexical_root):
            return None
        current = lexical_root
        for part in relative.parts:
            current = current / part
            if not current.exists() or _is_reparse(current):
                return None
        resolved_root = lexical_root.resolve(strict=True)
        resolved = lexical_candidate.resolve(strict=True)
        resolved.relative_to(resolved_root)
        if _is_reparse(resolved):
            return None
        if kind == "file" and not resolved.is_file():
            return None
        if kind == "dir" and not resolved.is_dir():
            return None
        return resolved
    except (OSError, RuntimeError, ValueError):
        return None


def _task_output_root(hub_root: Path) -> Path | None:
    """Create only a Hub-owned SAM2 child after non-reparse containment checks."""

    output_parent = _safe_existing_under(hub_root, hub_root / "Output", kind="dir")
    if output_parent is None:
        return None
    base = output_parent / "SAM2"
    try:
        if base.exists():
            if _safe_existing_under(output_parent, base, kind="dir") is None:
                return None
        else:
            base.mkdir(exist_ok=False)
            if _safe_existing_under(output_parent, base, kind="dir") is None:
                return None
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = base / f"sam2_{stamp}"
        path.mkdir(exist_ok=False)
        return _safe_existing_under(base, path, kind="dir")
    except OSError:
        return None


def _discard_task_output(path: Path | None) -> None:
    """Remove only this worker's un-published, timestamped output directory."""

    if path is None or not path.name.startswith("sam2_"):
        return
    try:
        if path.is_dir() and not _is_reparse(path):
            shutil.rmtree(path)
    except OSError:
        pass


def _preflight(request: dict[str, Any]) -> tuple[Path, Path, Path, Path] | None:
    """Revalidate private adapter hand-off before imports, GPU work, or writes."""

    try:
        root_value = os.environ.get("LOCALAIHUB_ROOT", "").strip()
        if not root_value:
            return None
        hub_root = Path(root_value).expanduser()
        if _safe_existing_under(hub_root, hub_root / "runtime", kind="dir") is None:
            return None
        runtime = Path(str(request.get("runtime") or "")).expanduser()
        checkpoint = Path(str(request.get("checkpoint") or "")).expanduser()
        source = Path(str(request.get("path") or "")).expanduser()
        safe_runtime = _safe_existing_under(hub_root / "runtime", runtime, kind="dir")
        safe_checkpoint = _safe_existing_under(safe_runtime, checkpoint, kind="file") if safe_runtime else None
        output_parent = _safe_existing_under(hub_root, hub_root / "Output", kind="dir")
        output_base = output_parent / "SAM2" if output_parent is not None else None
        if output_base is not None and output_base.exists() and _safe_existing_under(output_parent, output_base, kind="dir") is None:
            return None
        source_roots = (hub_root / "Temp" / "uploads", hub_root / "Output", hub_root / "Archive")
        safe_source = next(
            (resolved for root in source_roots if (resolved := _safe_existing_under(root, source, kind="file")) is not None),
            None,
        )
        if safe_runtime is None or safe_checkpoint is None or safe_source is None or output_parent is None:
            return None
        return hub_root.resolve(strict=True), safe_runtime, safe_checkpoint, safe_source
    except (OSError, RuntimeError, ValueError):
        return None


def _validate_selection(request: dict[str, Any], operation: str) -> str | None:
    """Validate normalized selection before decoding, model load, or writes."""

    from src.shared.schemas.vision import normalize_tool_payload

    tool = "track_video_object" if operation == "track_video" else operation
    normalized, error = normalize_tool_payload(tool, request)
    if error:
        return error
    # The adapter normally supplies this canonical form.  Keeping the worker
    # copy equally strict protects direct invocation and stale callers.
    request.update({key: value for key, value in normalized.items() if key in {"points", "box", "normalized_box", "frame_index", "frame_time_seconds"}})
    return None


def _points(request: dict[str, Any], width: int, height: int):
    import numpy as np

    values = request.get("points")
    if not isinstance(values, list) or not values:
        values = [{"x": width / 2, "y": height / 2, "label": 1}]
    coordinates: list[list[float]] = []
    labels: list[int] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        x = float(item.get("x", width / 2))
        y = float(item.get("y", height / 2))
        if bool(item.get("normalized")):
            x *= width
            y *= height
        coordinates.append([max(0.0, min(x, width - 1)), max(0.0, min(y, height - 1))])
        labels.append(1 if int(item.get("label", 1)) > 0 else 0)
    return np.asarray(coordinates, dtype=np.float32), np.asarray(labels, dtype=np.int32)


def _box(request: dict[str, Any], width: int, height: int):
    import numpy as np

    raw = request.get("box")
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    values = [float(item) for item in raw]
    if bool(request.get("normalized_box")):
        values = [values[0] * width, values[1] * height, values[2] * width, values[3] * height]
    x1, y1, x2, y2 = values
    if x2 <= x1 or y2 <= y1:
        raise ValueError("Box SAM2 cần có tọa độ phải/dưới lớn hơn trái/trên.")
    return np.asarray([max(0, x1), max(0, y1), min(width - 1, x2), min(height - 1, y2)], dtype=np.float32)


def _save_mask(image, mask, output: Path) -> tuple[Path, Path]:
    import numpy as np
    from PIL import Image

    binary = (mask.astype(np.uint8) * 255)
    mask_path = output / "mask.png"
    overlay_path = output / "overlay.png"
    Image.fromarray(binary, mode="L").save(mask_path)
    overlay = image.copy().astype(np.uint8)
    selected = mask.astype(bool)
    tint = np.asarray([56, 210, 112], dtype=np.float32)
    overlay[selected] = (overlay[selected].astype(np.float32) * 0.45 + tint * 0.55).clip(0, 255).astype(np.uint8)
    Image.fromarray(overlay, mode="RGB").save(overlay_path)
    return mask_path, overlay_path


def _build_predictor(runtime: Path, checkpoint: Path):
    import torch

    try:
        device = _verified_cuda_device(torch)
    except RuntimeError as error:
        code = str(error) if str(error) in {"sam2_rtx4060_required", "sam2_rtx4060_unverified"} else "sam2_rtx4060_unverified"
        return {"status": "error", "code": code, "error": "SAM2 yêu cầu NVIDIA GeForce RTX 4060 đã xác minh; không có fallback thiết bị."}
    sys.path.insert(0, str(runtime))
    from sam2.build_sam import build_sam2
    from sam2.sam2_image_predictor import SAM2ImagePredictor

    config = "configs/sam2.1/sam2.1_hiera_s.yaml"
    model = build_sam2(config, str(checkpoint), device=device)
    return SAM2ImagePredictor(model), device


def _verified_cuda_device(torch_module: Any) -> str:
    """Fail closed unless the explicitly approved discrete GPU is selected."""

    if os.environ.get("LOCALAIHUB_FORCE_CPU") == "1" or not torch_module.cuda.is_available():
        raise RuntimeError("sam2_rtx4060_required")
    try:
        name = str(torch_module.cuda.get_device_name(0) or "").casefold()
    except (AttributeError, RuntimeError, TypeError):
        raise RuntimeError("sam2_rtx4060_unverified") from None
    if "nvidia" not in name or not re.search(r"\brtx[ -]?4060\b", name):
        raise RuntimeError("sam2_rtx4060_required")
    return "cuda:0"


def _segment(request: dict[str, Any]) -> dict[str, Any]:
    bound = _preflight(request)
    if bound is None:
        return {"status": "error", "code": "sam2_path_contract_invalid", "error": "SAM2 từ chối input hoặc runtime ngoài vùng Hub an toàn."}
    operation = str(request.get("operation") or "segment_image")
    selection_error = _validate_selection(request, operation)
    if selection_error:
        return {"status": "error", "code": "selection_contract_invalid", "error": selection_error}
    hub_root, runtime, checkpoint, source = bound
    import numpy as np
    from PIL import Image

    try:
        image = np.asarray(Image.open(source).convert("RGB"))
    except (OSError, ValueError):
        return {"status": "error", "code": "input_unreadable", "error": "SAM2 không đọc được ảnh artifact đầu vào."}
    output: Path | None = None
    try:
        predictor, _device = _build_predictor(runtime, checkpoint)
        predictor.set_image(image)
        height, width = image.shape[:2]
        if operation == "segment_from_box":
            masks, scores, _ = predictor.predict(box=_box(request, width, height), multimask_output=True)
        else:
            points, labels = _points(request, width, height)
            masks, scores, _ = predictor.predict(point_coords=points, point_labels=labels, multimask_output=True)
        index = int(scores.argmax())
        output = _task_output_root(hub_root)
        if output is None:
            return {"status": "error", "code": "output_contract_invalid", "error": "SAM2 không thể tạo output Hub an toàn."}
        mask_path, overlay_path = _save_mask(image, masks[index], output)
        # Paths are a private worker-to-Job-Manager hand-off.  Job Manager
        # replaces them with opaque, job-bound artifact records before any
        # terminal result is persisted or returned to the browser.
        return {"status": "completed", "operation": operation, "outputs": [str(mask_path), str(overlay_path)]}
    except Exception:
        _discard_task_output(output)
        return {"status": "error", "code": "sam2_execution_failed", "error": "SAM2 không thể hoàn tất segmentation bounded."}


def _track(request: dict[str, Any]) -> dict[str, Any]:
    bound = _preflight(request)
    if bound is None:
        return {"status": "error", "code": "sam2_path_contract_invalid", "error": "SAM2 từ chối input hoặc runtime ngoài vùng Hub an toàn."}
    selection_error = _validate_selection(request, "track_video")
    if selection_error:
        return {"status": "error", "code": "selection_contract_invalid", "error": selection_error}
    hub_root, runtime, checkpoint, source = bound
    import cv2
    import numpy as np
    import torch
    # Verify the selected discrete device before creating a task output or
    # decoding the clip.  A heavy Hub-launched worker must fail closed before
    # doing any meaningful work; it may not quietly continue on CPU/iGPU.
    device = _verified_cuda_device(torch)
    output: Path | None = _task_output_root(hub_root)
    if output is None:
        return {"status": "error", "code": "output_contract_invalid", "error": "SAM2 không thể tạo output Hub an toàn."}
    capture = None
    writer = None
    try:
        sys.path.insert(0, str(runtime))
        from sam2.build_sam import build_sam2_video_predictor

        frames = output / "frames"
        frames.mkdir()
        capture = cv2.VideoCapture(str(source))
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        verified_rate = request.get("verified_frame_rate") is True and request.get("variable_frame_rate") is not True
        requested_frame = request.get("frame_index")
        requested_time = request.get("frame_time_seconds")
        # A verified fixed-rate request may use an index.  If FPS/frame count
        # was not verified, only the explicit time-based contract is allowed;
        # the decoder below maps that time to its actual decoded timestamps.
        if not fps > 0 or (not verified_rate and requested_time is None) or (verified_rate and requested_frame is None and requested_time is None):
            capture.release()
            capture = None
            _discard_task_output(output)
            return {"status": "error", "code": "video_metadata_unavailable", "error": "SAM2 cần FPS hoặc thời điểm video hợp lệ đã được xác minh; không đoán FPS hoặc frame index."}
        if not verified_rate:
            requested_frame = None
        if requested_frame is None and requested_time is None:
            requested_frame = 0
        target_frame = int(requested_frame) if isinstance(requested_frame, int) and not isinstance(requested_frame, bool) else None
        target_time = float(requested_time) if requested_time is not None else None
        if target_time is not None and (not target_time >= 0 or not target_time < 86400):
            capture.release()
            capture = None
            _discard_task_output(output)
            return {"status": "error", "code": "frame_time_invalid", "error": "Thời điểm video không hợp lệ."}
        count = 0
        nearest_frame = None
        nearest_distance = float("inf")
        last_timestamp_seconds = 0.0
        while True:
            timestamp_ms = float(capture.get(cv2.CAP_PROP_POS_MSEC) or 0.0)
            ok, frame = capture.read()
            if not ok:
                break
            if timestamp_ms >= 0:
                last_timestamp_seconds = max(last_timestamp_seconds, timestamp_ms / 1000.0)
            cv2.imwrite(str(frames / f"{count:06d}.jpg"), frame)
            if target_time is not None and timestamp_ms >= 0 and abs(timestamp_ms / 1000.0 - target_time) < nearest_distance:
                nearest_distance = abs(timestamp_ms / 1000.0 - target_time)
                nearest_frame = count
            count += 1
        capture.release()
        capture = None
        if count == 0 or width <= 0 or height <= 0:
            _discard_task_output(output)
            return {"status": "error", "code": "video_unreadable", "error": "SAM2 không thể trích frame từ video artifact."}
        if target_time is not None and target_time > last_timestamp_seconds + max(1.0 / fps, 0.05):
            _discard_task_output(output)
            return {"status": "error", "code": "frame_time_invalid", "error": "Thời điểm video vượt thời lượng thực tế của artifact."}
        frame_index = target_frame if target_frame is not None else nearest_frame
        if frame_index is None:
            _discard_task_output(output)
            return {"status": "error", "code": "frame_time_unavailable", "error": "Decoder không cung cấp timestamp đủ để chọn frame theo thời gian."}
        if frame_index < 0 or frame_index >= count:
            _discard_task_output(output)
            return {"status": "error", "code": "frame_index_invalid", "error": "Frame được chọn không tồn tại trong video artifact."}
        predictor = build_sam2_video_predictor("configs/sam2.1/sam2.1_hiera_s.yaml", str(checkpoint), device=device)
        state = predictor.init_state(video_path=str(frames))
        points, labels = _points(request, width, height)
        box = _box(request, width, height)
        if box is not None:
            predictor.add_new_points_or_box(state, frame_idx=frame_index, obj_id=1, box=box)
        else:
            predictor.add_new_points_or_box(state, frame_idx=frame_index, obj_id=1, points=points, labels=labels)
        mask_dir = output / "masks"
        mask_dir.mkdir()
        preview = output / "mask_preview.mp4"
        writer = cv2.VideoWriter(str(preview), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height), False)
        written = 0
        first_mask: Path | None = None
        for propagated_frame_index, _, logits in predictor.propagate_in_video(state, start_frame_idx=frame_index):
            mask = (logits[0] > 0.0).detach().cpu().numpy().astype(np.uint8) * 255
            mask_path = mask_dir / f"{propagated_frame_index:06d}.png"
            cv2.imwrite(str(mask_path), mask)
            if first_mask is None:
                first_mask = mask_path
            writer.write(mask)
            written += 1
        writer.release()
        writer = None
        if written <= 0 or first_mask is None or not preview.is_file() or not first_mask.is_file():
            _discard_task_output(output)
            return {"status": "error", "code": "sam2_output_missing", "error": "SAM2 không tạo đủ output tracking."}
        return {"status": "completed", "operation": "track_video", "outputs": [str(preview), str(first_mask)]}
    except Exception:
        _discard_task_output(output)
        return {"status": "error", "code": "sam2_execution_failed", "error": "SAM2 không thể hoàn tất tracking bounded."}
    finally:
        if capture is not None:
            capture.release()
        if writer is not None:
            writer.release()


def main() -> int:
    try:
        request = _request()
        operation = str(request.get("operation") or "segment_image")
        if operation == "load_model":
            # A worker process loads model only while serving an explicit job.
            return _emit({"status": "completed", "operation": operation, "message": "SAM2 sẽ nạp theo yêu cầu trong worker cách ly."})
        if operation == "track_video":
            return _emit(_track(request))
        if operation in {"segment_image", "segment_from_box", "segment_from_points"}:
            return _emit(_segment(request))
        return _emit({"status": "error", "error": "Thao tác SAM2 không được allowlist."})
    except Exception:
        return _emit({"status": "error", "code": "sam2_worker_failed", "error": "SAM2 worker không thể hoàn tất job."})


if __name__ == "__main__":
    raise SystemExit(main())
