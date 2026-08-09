"""Isolated SAM2 worker invoked by the Hub adapter over JSON stdin."""

from __future__ import annotations

import json
import os
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


def _output_root(request: dict[str, Any]) -> Path:
    root = Path(str(request["output_root"])).expanduser()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = root / f"sam2_{stamp}"
    path.mkdir(parents=True, exist_ok=False)
    return path


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

    sys.path.insert(0, str(runtime))
    from sam2.build_sam import build_sam2
    from sam2.sam2_image_predictor import SAM2ImagePredictor

    config = "configs/sam2.1/sam2.1_hiera_s.yaml"
    device = "cuda" if torch.cuda.is_available() and os.environ.get("LOCALAIHUB_FORCE_CPU") != "1" else "cpu"
    model = build_sam2(config, str(checkpoint), device=device)
    return SAM2ImagePredictor(model), device


def _segment(request: dict[str, Any]) -> dict[str, Any]:
    import numpy as np
    from PIL import Image

    runtime = Path(str(request["runtime"]))
    checkpoint = Path(str(request["checkpoint"]))
    source = Path(os.path.expandvars(str(request.get("path", "")))).expanduser()
    if not source.is_file():
        return {"status": "error", "error": "Không tìm thấy ảnh đầu vào SAM2."}
    try:
        image = np.asarray(Image.open(source).convert("RGB"))
    except (OSError, ValueError):
        return {"status": "error", "error": "SAM2 không đọc được ảnh đầu vào."}
    predictor, device = _build_predictor(runtime, checkpoint)
    predictor.set_image(image)
    height, width = image.shape[:2]
    operation = str(request.get("operation") or "segment_image")
    if operation == "segment_from_box":
        masks, scores, _ = predictor.predict(box=_box(request, width, height), multimask_output=True)
    else:
        points, labels = _points(request, width, height)
        masks, scores, _ = predictor.predict(point_coords=points, point_labels=labels, multimask_output=True)
    index = int(scores.argmax())
    output = _output_root(request)
    mask_path, overlay_path = _save_mask(image, masks[index], output)
    return {
        "status": "completed",
        "operation": operation,
        "mask": str(mask_path),
        "preview": str(overlay_path),
        "score": float(scores[index]),
        "device": device,
    }


def _track(request: dict[str, Any]) -> dict[str, Any]:
    import cv2
    import numpy as np
    import torch

    runtime = Path(str(request["runtime"]))
    checkpoint = Path(str(request["checkpoint"]))
    source = Path(os.path.expandvars(str(request.get("path", "")))).expanduser()
    if not source.is_file():
        return {"status": "error", "error": "Không tìm thấy video đầu vào SAM2."}
    sys.path.insert(0, str(runtime))
    from sam2.build_sam import build_sam2_video_predictor

    output = _output_root(request)
    frames = output / "frames"
    frames.mkdir()
    capture = cv2.VideoCapture(str(source))
    fps = capture.get(cv2.CAP_PROP_FPS) or 24.0
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    count = 0
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        cv2.imwrite(str(frames / f"{count:06d}.jpg"), frame)
        count += 1
    capture.release()
    if count == 0 or width <= 0 or height <= 0:
        return {"status": "error", "error": "Không thể trích frame từ video SAM2."}
    device = "cuda" if torch.cuda.is_available() and os.environ.get("LOCALAIHUB_FORCE_CPU") != "1" else "cpu"
    predictor = build_sam2_video_predictor("configs/sam2.1/sam2.1_hiera_s.yaml", str(checkpoint), device=device)
    state = predictor.init_state(video_path=str(frames))
    points, labels = _points(request, width, height)
    box = _box(request, width, height)
    if box is not None:
        predictor.add_new_points_or_box(state, frame_idx=0, obj_id=1, box=box)
    else:
        predictor.add_new_points_or_box(state, frame_idx=0, obj_id=1, points=points, labels=labels)
    mask_dir = output / "masks"
    mask_dir.mkdir()
    writer = cv2.VideoWriter(str(output / "mask_preview.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height), False)
    written = 0
    for frame_index, _, logits in predictor.propagate_in_video(state):
        mask = (logits[0] > 0.0).detach().cpu().numpy().astype(np.uint8) * 255
        cv2.imwrite(str(mask_dir / f"{frame_index:06d}.png"), mask)
        writer.write(mask)
        written += 1
    writer.release()
    return {
        "status": "completed",
        "operation": "track_video",
        "video": str(output / "mask_preview.mp4"),
        "masks": [str(mask_dir / "000000.png")] if written else [],
        "frame_count": written,
        "device": device,
    }


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
    except Exception as exc:
        return _emit({"status": "error", "error": str(exc)})


if __name__ == "__main__":
    raise SystemExit(main())
