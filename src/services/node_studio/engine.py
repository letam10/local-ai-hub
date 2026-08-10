"""DAG execution, per-node artifact ownership and content-hash reuse."""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from src.services.artifact_store import describe, publicize, register_path, resolve
from src.shared.paths.registry import OUTPUT_ROOT

from .registry import NodeDefinition, get_definition
from .schema import validate_graph
from .state import graph_runs


ToolExecutor = Callable[[str, dict[str, Any], Any], dict[str, Any]]
NODE_CACHE_MAX_ENTRIES = 256


class NodeFailure(RuntimeError):
    def __init__(self, message: str, *, status: str = "failed", next_action: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.next_action = next_action


@dataclass(frozen=True)
class ArtifactValue:
    artifact_id: str
    path: Path
    public: dict[str, Any]


def _artifact_from_id(artifact_id: object, *, expected: str | None = None) -> ArtifactValue:
    if not isinstance(artifact_id, str):
        raise NodeFailure("Artifact ID không hợp lệ.")
    path = resolve(artifact_id)
    item = describe(artifact_id)
    if path is None or item is None:
        raise NodeFailure("Artifact Hub không còn tồn tại hoặc không thuộc vùng an toàn.")
    media_type = str(item.get("media_type") or "")
    if expected and expected != "METADATA" and not media_type.startswith(expected.lower() + "/"):
        raise NodeFailure(f"Artifact cần kiểu {expected}, nhưng nhận {media_type or 'không rõ'}.")
    return ArtifactValue(artifact_id, path, item)


def _artifact_from_path(value: object) -> ArtifactValue:
    path = Path(str(value)).expanduser()
    item = register_path(path)
    if item is None:
        raise NodeFailure("Node không tạo artifact Hub hợp lệ.")
    resolved = resolve(str(item["id"]))
    if resolved is None:
        raise NodeFailure("Không thể đọc lại artifact Node Studio vừa tạo.")
    return ArtifactValue(str(item["id"]), resolved, item)


def _public_value(value: Any) -> Any:
    if isinstance(value, ArtifactValue):
        return dict(value.public)
    if isinstance(value, dict):
        return {str(key): _public_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_public_value(item) for item in value]
    return publicize(value)


def _cache_value(value: Any) -> Any:
    if isinstance(value, ArtifactValue):
        return {"artifact_id": value.artifact_id}
    if isinstance(value, dict):
        return {str(key): _cache_value(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_cache_value(item) for item in value]
    return value


def _cache_key(definition: NodeDefinition, data: dict[str, Any], inputs: dict[str, Any], *, draft: bool) -> str:
    payload = {
        "type": definition.type,
        "version": definition.version,
        "data": data,
        "inputs": _cache_value(inputs),
        "draft": bool(draft and definition.supports_draft),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _cache_is_usable(value: Any) -> bool:
    if isinstance(value, ArtifactValue):
        return value.path.is_file() and resolve(value.artifact_id) is not None
    if isinstance(value, dict):
        return all(_cache_is_usable(item) for item in value.values())
    if isinstance(value, list):
        return all(_cache_is_usable(item) for item in value)
    return True


class NodeCache:
    """Process-local cache; no graph input, media or path is persisted to Git."""

    def __init__(self, *, max_entries: int = NODE_CACHE_MAX_ENTRIES) -> None:
        self._values: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._max_entries = max(1, int(max_entries))
        self._lock = threading.RLock()

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._values.get(key)
            if value is None or not _cache_is_usable(value):
                self._values.pop(key, None)
                return None
            self._values.move_to_end(key)
            return value

    def put(self, key: str, value: dict[str, Any]) -> None:
        with self._lock:
            self._values.pop(key, None)
            self._values[key] = value
            while len(self._values) > self._max_entries:
                # This cache only forgets references.  Artifact ownership stays
                # with Artifact Store and eviction must never delete files.
                self._values.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._values.clear()


node_cache = NodeCache()


def _input_artifact(inputs: dict[str, Any], *names: str) -> ArtifactValue:
    for name in names:
        value = inputs.get(name)
        if isinstance(value, ArtifactValue):
            return value
        if isinstance(value, list) and value and isinstance(value[0], ArtifactValue):
            return value[0]
    raise NodeFailure("Node thiếu artifact input bắt buộc.")


def _number(value: object, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _integer(value: object, fallback: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return fallback


def _points(value: Any) -> dict[str, Any]:
    if isinstance(value, dict) and isinstance(value.get("points"), list):
        return {"points": value["points"]}
    raw = value.get("points") if isinstance(value, dict) else value
    if isinstance(raw, list):
        return {"points": raw}
    values: list[dict[str, Any]] = []
    if isinstance(raw, str):
        for token in raw.split(";"):
            parts = [part.strip() for part in token.split(",")]
            if len(parts) < 2:
                continue
            try:
                values.append({"x": float(parts[0]), "y": float(parts[1]), "label": int(parts[2]) if len(parts) > 2 else 1})
            except ValueError:
                continue
    return {"points": values}


def _box(value: Any) -> dict[str, Any]:
    if isinstance(value, dict) and isinstance(value.get("box"), list):
        return {"box": value["box"]}
    raw = value.get("box") if isinstance(value, dict) else value
    if isinstance(raw, str):
        try:
            raw = [float(part.strip()) for part in raw.split(",")]
        except ValueError:
            raw = []
    return {"box": raw if isinstance(raw, list) else []}


def _output_root(kind: str) -> Path:
    directory = OUTPUT_ROOT / "NodeStudio" / kind
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _image_mask_operation(image: ArtifactValue, mask: ArtifactValue, *, composite: bool) -> dict[str, Any]:
    try:
        from PIL import Image
    except ImportError as exc:
        raise NodeFailure("Pillow chưa có trong Hub environment; Mask Apply/Composite đang unavailable.", status="unavailable") from exc
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    target = _output_root("Image") / f"{image.path.stem}_{'composite' if composite else 'masked'}_{stamp}.png"
    try:
        source = Image.open(image.path).convert("RGBA")
        alpha = Image.open(mask.path).convert("L").resize(source.size)
        if composite:
            canvas = Image.new("RGBA", source.size, (0, 0, 0, 0))
            canvas.alpha_composite(source)
            canvas.putalpha(alpha)
            canvas.save(target)
        else:
            source.putalpha(alpha)
            source.save(target)
    except (OSError, ValueError) as exc:
        raise NodeFailure(f"Không thể áp mask: {exc}") from exc
    return {"image": _artifact_from_path(target)}


def _media_result(result: dict[str, Any], *, expected_output: str) -> dict[str, Any]:
    status = str(result.get("status") or "error")
    if status != "completed":
        raise NodeFailure(str(result.get("error") or result.get("reason") or "Media node không hoàn tất."), status="unavailable" if status == "unavailable" else status)
    output = result.get("output")
    if isinstance(output, str):
        return {expected_output: _artifact_from_path(output), "metadata": {key: _public_value(value) for key, value in result.items() if key not in {"status", "output"}}}
    files = result.get("files")
    if isinstance(files, list):
        artifacts: list[ArtifactValue] = []
        for item in files:
            try:
                artifacts.append(_artifact_from_path(item))
            except NodeFailure:
                continue
        return {"frames": artifacts, "metadata": {"frame_count": result.get("frame_count", len(artifacts))}}
    return {"metadata": _public_value({key: value for key, value in result.items() if key != "status"})}


def _run_media(node_type: str, data: dict[str, Any], inputs: dict[str, Any], context: Any, execute_tool: ToolExecutor) -> dict[str, Any]:
    operations = {
        "image_resize": "image_resize",
        "image_upscale": "image_upscale",
        "image_crop": "image_crop",
        "image_rotate": "image_rotate",
        "image_flip": "image_flip",
        "image_levels": "image_levels",
        "trim_cut": "trim",
        "video_crop": "crop",
        "video_resize": "resize",
        "video_rotate": "rotate",
        "video_fps": "fps",
        "extract_audio": "extract_audio",
        "replace_audio": "replace_audio",
        "subtitle_burn": "burn_subtitle",
        "extract_frames": "extract_frames",
    }
    if node_type == "concat":
        values = inputs.get("videos")
        videos = [item for item in values if isinstance(item, ArtifactValue)] if isinstance(values, list) else []
        if not videos:
            raise NodeFailure("Concat cần ít nhất một VIDEO input.")
        result = execute_tool("run_media_operation", {"operation": "concat", "path": str(videos[0].path), "input_paths": [str(item.path) for item in videos[1:]]}, context)
        return _media_result(result, expected_output="video")
    operation = operations.get(node_type)
    if operation is None:
        raise NodeFailure("Media node không nằm trong allowlist.")
    source = _input_artifact(inputs, "image", "video")
    payload = {"operation": operation, "path": str(source.path), **data}
    if node_type == "replace_audio":
        payload["secondary_path"] = str(_input_artifact(inputs, "audio").path)
    if node_type == "subtitle_burn":
        payload["secondary_path"] = str(_input_artifact(inputs, "subtitle").path)
    result = execute_tool("run_media_operation", payload, context)
    if node_type == "extract_frames":
        return _media_result(result, expected_output="frames")
    return _media_result(result, expected_output="audio" if node_type == "extract_audio" else "image" if node_type.startswith("image_") else "video")


def _run_frame_interpolate(data: dict[str, Any], inputs: dict[str, Any], context: Any, execute_tool: ToolExecutor) -> dict[str, Any]:
    source = _input_artifact(inputs, "video")
    if str(data.get("mode") or "target_fps") == "off":
        return {"video": source, "metadata": {"interpolation": "off", "reused_source": True}}
    result = execute_tool("run_media_operation", {"operation": "frame_interpolate", "path": str(source.path), **data}, context)
    output = _media_result(result, expected_output="video")
    output.setdefault("metadata", {}).update({"requested_backend": data.get("backend", "ffmpeg_minterpolate")})
    return output


def _run_video_transform(data: dict[str, Any], inputs: dict[str, Any], context: Any, execute_tool: ToolExecutor) -> dict[str, Any]:
    source = _input_artifact(inputs, "video")
    operation = str(data.get("operation") or "resize")
    if operation not in {"resize", "crop", "rotate", "fps", "transcode"}:
        raise NodeFailure("Video Transform không nằm trong allowlist.")
    result = execute_tool("run_media_operation", {"operation": operation, "path": str(source.path), **data}, context)
    output = _media_result(result, expected_output="video")
    output.setdefault("metadata", {}).update({"operation": operation, "creative_prompt": str(inputs.get("prompt") or "")})
    return output


def _run_video_upscale(data: dict[str, Any], inputs: dict[str, Any], context: Any, execute_tool: ToolExecutor) -> dict[str, Any]:
    source = _input_artifact(inputs, "video")
    backend = str(data.get("backend") or "ffmpeg_scale")
    if backend == "animesr":
        result = execute_tool("upscale_anime_video", {"path": str(source.path), **data}, context)
        if result.get("status") != "completed":
            raise NodeFailure(
                str(result.get("error") or result.get("reason") or "AnimeSR video upscale không hoàn tất."),
                status=str(result.get("status") or "failed"),
            )
        output_value = result.get("output")
        if not isinstance(output_value, str) or not output_value:
            raise NodeFailure("AnimeSR không trả output video hợp lệ.")
        return {"video": _artifact_from_path(output_value), "metadata": {"backend": "animesr", "scale": data.get("scale")}}
    if backend != "ffmpeg_scale":
        raise NodeFailure("Video Upscale backend không nằm trong allowlist.")
    output = _media_result(
        execute_tool("run_media_operation", {"operation": "video_upscale", "path": str(source.path), **data}, context),
        expected_output="video",
    )
    output.setdefault("metadata", {}).update({"backend": "ffmpeg_scale", "ai_upscaler": False, "scale": data.get("scale")})
    return output


def _run_encode(data: dict[str, Any], inputs: dict[str, Any], context: Any, execute_tool: ToolExecutor) -> dict[str, Any]:
    source = _input_artifact(inputs, "video")
    payload = {"operation": "encode", "path": str(source.path), **data}
    audio = inputs.get("audio")
    if isinstance(audio, ArtifactValue):
        payload["secondary_path"] = str(audio.path)
    return _media_result(execute_tool("run_media_operation", payload, context), expected_output="video")


def _run_image_generation(definition: NodeDefinition, data: dict[str, Any], inputs: dict[str, Any], context: Any, execute_tool: ToolExecutor, *, draft: bool) -> dict[str, Any]:
    prompt = inputs.get("prompt") or data.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise NodeFailure("Generator cần TEXT prompt.")
    settings = inputs.get("settings") if isinstance(inputs.get("settings"), dict) else {}
    width = _integer(inputs.get("width", data.get("width", 768)), 768)
    height = _integer(inputs.get("height", data.get("height", 768)), 768)
    steps = _integer(settings.get("steps", data.get("steps", 20)), 20)
    if draft and definition.supports_draft:
        width = min(width, 512)
        height = min(height, 512)
        steps = min(steps, 8)
    payload: dict[str, Any] = {
        "prompt": prompt,
        "negative_prompt": str(data.get("negative_prompt") or ""),
        "width": max(256, min(2048, width)),
        "height": max(256, min(2048, height)),
        "steps": max(1, min(80, steps)),
        "seed": _integer(inputs.get("seed", data.get("seed", 42)), 42),
    }
    image = inputs.get("image")
    if isinstance(image, ArtifactValue):
        payload["input_image"] = str(image.path)
    tool = "generate_flux" if definition.runner == "flux" else "generate_qwen_image"
    result = execute_tool(tool, payload, context)
    if result.get("status") != "completed":
        raise NodeFailure(str(result.get("error") or result.get("reason") or "Image backend không hoàn tất."), status=str(result.get("status") or "failed"))
    outputs = result.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        raise NodeFailure("Image backend không trả output image.")
    return {"image": _artifact_from_path(outputs[-1]), "metadata": {"engine": "flux" if tool == "generate_flux" else "qwen", "draft": bool(draft), "seed": payload["seed"]}}


def _run_sam2(definition: NodeDefinition, data: dict[str, Any], inputs: dict[str, Any], context: Any, execute_tool: ToolExecutor) -> dict[str, Any]:
    image = _input_artifact(inputs, "image")
    payload: dict[str, Any] = {"path": str(image.path)}
    boxes = inputs.get("boxes")
    explicit_box = inputs.get("box")
    points = inputs.get("points")
    if isinstance(boxes, dict) and isinstance(boxes.get("grounded"), list) and boxes["grounded"]:
        candidate = boxes["grounded"][0]
        raw = candidate.get("box_normalized_cxcywh") if isinstance(candidate, dict) else None
        if isinstance(raw, list) and len(raw) == 4:
            cx, cy, width, height = [float(item) for item in raw]
            payload.update({"box": [cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2], "normalized_box": True})
            tool = "segment_from_box"
        else:
            raise NodeFailure("Grounding DINO không trả normalized box hợp lệ.")
    elif explicit_box is not None:
        payload.update(_box(explicit_box))
        tool = "segment_from_box"
    else:
        payload.update(_points(points if points is not None else data))
        tool = "segment_from_points"
    result = execute_tool(tool, payload, context)
    if result.get("status") != "completed":
        raise NodeFailure(str(result.get("error") or result.get("reason") or "SAM2 không hoàn tất."), status=str(result.get("status") or "failed"))
    return {
        "mask": _artifact_from_path(result["mask"]),
        "preview": _artifact_from_path(result["preview"]),
        "metadata": {"score": result.get("score"), "device": result.get("device"), "operation": result.get("operation")},
    }


def _run_sam2_track(inputs: dict[str, Any], context: Any, execute_tool: ToolExecutor) -> dict[str, Any]:
    video = _input_artifact(inputs, "video")
    payload: dict[str, Any] = {"path": str(video.path)}
    if inputs.get("box") is not None:
        payload.update(_box(inputs["box"]))
    else:
        payload.update(_points(inputs.get("points")))
    result = execute_tool("track_video_object", payload, context)
    if result.get("status") != "completed":
        raise NodeFailure(str(result.get("error") or result.get("reason") or "SAM2 Track không hoàn tất."), status=str(result.get("status") or "failed"))
    masks = result.get("masks") if isinstance(result.get("masks"), list) else []
    video_output = result.get("video")
    if not isinstance(video_output, str) or not video_output:
        raise NodeFailure("SAM2 Track không trả về video kết quả hợp lệ.")
    if not masks or not isinstance(masks[0], str) or not masks[0]:
        raise NodeFailure("SAM2 Track không trả về MASK hợp lệ; Hub không gán nhầm video vào cổng MASK.")
    return {
        "video": _artifact_from_path(video_output),
        "mask": _artifact_from_path(masks[0]),
        "metadata": {"frame_count": result.get("frame_count"), "device": result.get("device")},
    }


def _run_comfyui_workflow(data: dict[str, Any], inputs: dict[str, Any], context: Any, *, draft: bool) -> dict[str, Any]:
    """Bridge typed Hub artifacts into an allowlisted ComfyUI API workflow."""

    from src.modules.image_generation.backend.comfyui import run_bridge_workflow

    bridge_inputs: dict[str, Any] = {}
    for name in ("image", "mask", "video", "audio"):
        value = inputs.get(name)
        if isinstance(value, ArtifactValue):
            bridge_inputs[name] = str(value.path)
    if "text" in inputs:
        bridge_inputs["text"] = str(inputs["text"])
    elif data.get("prompt"):
        bridge_inputs["text"] = str(data["prompt"])
    if isinstance(inputs.get("metadata"), dict):
        bridge_inputs["metadata"] = inputs["metadata"]
    request = dict(data)
    if draft:
        request["width"] = min(_integer(request.get("width"), 768), 512)
        request["height"] = min(_integer(request.get("height"), 768), 512)
        request["steps"] = min(_integer(request.get("steps"), 20), 8)
    workflow_id = str(data.get("workflow_id") or "flux_quick")
    result = run_bridge_workflow(workflow_id, bridge_inputs, request, context)
    status = str(result.get("status") or "error")
    if status != "completed":
        raise NodeFailure(str(result.get("error") or result.get("reason") or "ComfyUI bridge không hoàn tất."), status="unavailable" if status == "unavailable" else status)
    outputs = result.get("outputs")
    if not isinstance(outputs, list) or not outputs or not isinstance(outputs[-1], str):
        raise NodeFailure("ComfyUI bridge không trả image output hợp lệ.")
    return {
        "image": _artifact_from_path(outputs[-1]),
        "metadata": {
            "bridge_workflow": result.get("bridge_workflow", workflow_id),
            "engine": result.get("engine"),
            "draft": bool(draft),
            "seed": result.get("seed"),
        },
    }


def _run_node(definition: NodeDefinition, data: dict[str, Any], inputs: dict[str, Any], context: Any, execute_tool: ToolExecutor, *, draft: bool) -> dict[str, Any]:
    runner = definition.runner
    if runner == "annotation":
        return {}
    if runner == "load_artifact":
        output = definition.outputs[0]
        return {output.name: _artifact_from_id(data.get("asset_id"), expected=output.type)}
    if runner == "text":
        return {definition.outputs[0].name: str(data.get("text") or "")}
    if runner == "number":
        return {definition.outputs[0].name: _number(data.get("value"), 0)}
    if runner == "boolean":
        return {definition.outputs[0].name: bool(data.get("value"))}
    if runner == "points":
        return {"points": _points(data)}
    if runner == "box":
        return {"box": _box(data)}
    if runner == "resolution":
        width = _integer(data.get("width"), 768)
        height = _integer(data.get("height"), 768)
        return {"width": width, "height": height, "settings": {"width": width, "height": height}}
    if runner == "metadata":
        return {definition.outputs[0].name: dict(data)}
    if runner == "passthrough":
        for port in definition.inputs:
            if port.name in inputs:
                return {definition.outputs[0].name: inputs[port.name]}
        raise NodeFailure("Node passthrough thiếu input.")
    if runner == "compare":
        before = _input_artifact(inputs, "a")
        after = _input_artifact(inputs, "b")
        return {"a": before, "b": after, "comparison": {"before": before, "after": after}}
    if runner in {"media", "image_upscale"}:
        return _run_media(definition.type, data, inputs, context, execute_tool)
    if runner == "frame_interpolate":
        return _run_frame_interpolate(data, inputs, context, execute_tool)
    if runner == "video_transform":
        return _run_video_transform(data, inputs, context, execute_tool)
    if runner == "video_upscale":
        return _run_video_upscale(data, inputs, context, execute_tool)
    if runner == "encode":
        return _run_encode(data, inputs, context, execute_tool)
    if runner in {"flux", "qwen"}:
        return _run_image_generation(definition, data, inputs, context, execute_tool, draft=draft)
    if runner == "video_generate":
        raise NodeFailure(
            "Video generation backend chưa khả dụng trong Hub.",
            status="unavailable",
            next_action=definition.status_action,
        )
    if runner == "comfyui_workflow":
        return _run_comfyui_workflow(data, inputs, context, draft=draft)
    if runner == "grounding":
        image = _input_artifact(inputs, "image")
        prompt = inputs.get("prompt")
        result = execute_tool("ground_objects", {"path": str(image.path), "prompt": str(prompt or ""), **data}, context)
        if result.get("status") != "completed":
            raise NodeFailure(str(result.get("error") or result.get("reason") or "Grounding DINO không hoàn tất."), status=str(result.get("status") or "failed"))
        return {"boxes": {"grounded": result.get("grounded") or []}}
    if runner == "rfdetr":
        image = _input_artifact(inputs, "image")
        result = execute_tool("detect_objects", {"path": str(image.path), **data}, context)
        if result.get("status") != "completed":
            raise NodeFailure(str(result.get("error") or result.get("reason") or "RF-DETR không hoàn tất."), status=str(result.get("status") or "failed"))
        return {"detections": _public_value(result)}
    if runner == "sam2_segment":
        return _run_sam2(definition, data, inputs, context, execute_tool)
    if runner == "sam2_track":
        return _run_sam2_track(inputs, context, execute_tool)
    if runner == "mask_apply":
        return _image_mask_operation(_input_artifact(inputs, "image"), _input_artifact(inputs, "mask"), composite=False)
    if runner == "mask_composite":
        return _image_mask_operation(_input_artifact(inputs, "image"), _input_artifact(inputs, "mask"), composite=True)
    if runner == "probe":
        source = _input_artifact(inputs, "media")
        result = execute_tool("probe_media", {"path": str(source.path)}, context)
        if result.get("status") != "completed":
            raise NodeFailure(str(result.get("error") or result.get("reason") or "FFprobe không hoàn tất."), status=str(result.get("status") or "failed"))
        return {"metadata": _public_value(result)}
    if runner == "animesr":
        source = _input_artifact(inputs, "video")
        result = execute_tool("upscale_anime_video", {"path": str(source.path), **data}, context)
        if result.get("status") != "completed":
            raise NodeFailure(str(result.get("error") or result.get("reason") or "AnimeSR không hoàn tất."), status=str(result.get("status") or "failed"))
        return {"video": _artifact_from_path(result["output"])}
    if runner == "realesrgan":
        raise NodeFailure("Real-ESRGAN vẫn partial: chưa có CLI contract Hub được smoke bounded.", status="unavailable")
    raise NodeFailure("Node runner không có trong allowlist.")


def _inputs_for_node(graph: dict[str, Any], node_id: str, outputs: dict[str, dict[str, Any]], definition: NodeDefinition) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for port in definition.inputs:
        values: list[Any] = []
        for edge in graph["edges"]:
            if edge["target"]["node"] != node_id or edge["target"]["port"] != port.name:
                continue
            source_outputs = outputs.get(edge["source"]["node"], {})
            if edge["source"]["port"] in source_outputs:
                values.append(source_outputs[edge["source"]["port"]])
        if values:
            result[port.name] = values if port.multi else values[0]
    return result


def execute_graph(graph: dict[str, Any], context: Any, execute_tool: ToolExecutor, *, draft: bool = False) -> dict[str, Any]:
    """Execute a validated DAG and reuse only still-valid upstream artifact outputs."""

    validation = validate_graph(graph, require_runnable=True)
    if not validation["valid"]:
        return {"status": "error", "error": "Graph không hợp lệ.", "validation": {"errors": validation["errors"]}}
    value = validation["graph"]
    graph_runs.begin(str(context.job_id), value)
    node_lookup = {str(node["id"]): node for node in value["nodes"]}
    outputs: dict[str, dict[str, Any]] = {}
    public_nodes: list[dict[str, Any]] = []
    total = max(1, len(validation["order"]))
    try:
        for index, node_id in enumerate(validation["order"]):
            if bool(getattr(context, "cancelled", False)):
                graph_runs.update_node(str(context.job_id), node_id, status="cancelled", progress=0, message="Đã hủy Run Graph.")
                graph_runs.finish(str(context.job_id), status="cancelled")
                return {"status": "cancelled", "reason": "Người dùng đã hủy Run Graph."}
            node = node_lookup[node_id]
            definition = get_definition(str(node["type"]))
            assert definition is not None
            data = node.get("data") if isinstance(node.get("data"), dict) else {}
            inputs = _inputs_for_node(value, node_id, outputs, definition)
            key = _cache_key(definition, data, inputs, draft=draft)
            graph_runs.update_node(str(context.job_id), node_id, status="running", progress=10, message="Đang chạy node.")
            cached = node_cache.get(key)
            if cached is not None:
                node_outputs = cached
                cache_hit = True
                message = "Đã dùng lại output cache hợp lệ."
            else:
                node_outputs = _run_node(definition, data, inputs, context, execute_tool, draft=draft)
                node_cache.put(key, node_outputs)
                cache_hit = False
                message = "Đã hoàn tất node."
            outputs[node_id] = node_outputs
            public_output = _public_value(node_outputs)
            graph_runs.update_node(str(context.job_id), node_id, status="completed", progress=100, message=message, output=public_output)
            public_nodes.append({"id": node_id, "type": definition.type, "status": "completed", "cache_hit": cache_hit, "output": public_output})
            progress = max(5, min(99, int((index + 1) / total * 100)))
            context.progress(progress, f"Node Studio: {index + 1}/{total} node hoàn tất.")
    except NodeFailure as exc:
        message = str(publicize(str(exc)))
        definition = get_definition(str(node.get("type") or ""))
        next_action = exc.next_action or (definition.status_action if definition and definition.status != "operational" else None)
        graph_runs.update_node(str(context.job_id), node_id, status=exc.status, progress=0, message=message, error=message, next_action=next_action)
        graph_runs.finish(str(context.job_id), status=exc.status, error=message, next_action=next_action)
        snapshot = graph_runs.snapshot(str(context.job_id)) or {}
        return {"status": "unavailable" if exc.status == "unavailable" else "error", "error": message, "next_action": next_action, "failed_node": node_id, "nodes": public_nodes, "provenance": snapshot.get("provenance", [])}
    except Exception as exc:  # pragma: no cover - protects the background job thread
        message = str(publicize(str(exc)))
        graph_runs.update_node(str(context.job_id), node_id, status="failed", progress=0, message=message, error=message, next_action="Kiểm tra log job và cấu hình backend rồi thử lại.")
        graph_runs.finish(str(context.job_id), status="failed", error=message, next_action="Kiểm tra log job và cấu hình backend rồi thử lại.")
        snapshot = graph_runs.snapshot(str(context.job_id)) or {}
        return {"status": "error", "error": message, "next_action": "Kiểm tra log job và cấu hình backend rồi thử lại.", "failed_node": node_id, "nodes": public_nodes, "provenance": snapshot.get("provenance", [])}
    graph_runs.finish(str(context.job_id), status="completed")
    snapshot = graph_runs.snapshot(str(context.job_id)) or {}
    return {"status": "completed", "graph_id": value.get("id"), "draft": bool(draft), "nodes": public_nodes, "provenance": snapshot.get("provenance", [])}
