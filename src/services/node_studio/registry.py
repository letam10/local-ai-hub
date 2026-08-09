"""Node definitions for the Hub-owned, offline Node Studio.

This is an original, small graph vocabulary.  It deliberately does not import
or copy the ComfyUI frontend.  Definitions describe real adapters or mark an
incomplete adapter as ``partial``/``unavailable``; the browser never receives
a shell command or a workstation path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


GRAPH_SCHEMA_VERSION = 1
PORT_TYPES = ("IMAGE", "MASK", "VIDEO", "AUDIO", "TEXT", "NUMBER", "BOOLEAN", "MODEL", "METADATA")


@dataclass(frozen=True)
class Port:
    name: str
    type: str
    required: bool = False
    multi: bool = False
    label: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "required": self.required,
            "multi": self.multi,
            "label": self.label or self.name.replace("_", " ").title(),
        }


@dataclass(frozen=True)
class NodeDefinition:
    type: str
    title: str
    category: str
    description: str
    runner: str
    inputs: tuple[Port, ...] = ()
    outputs: tuple[Port, ...] = ()
    properties: tuple[dict[str, Any], ...] = ()
    status: str = "operational"
    status_reason: str | None = None
    status_action: str | None = None
    heavy: bool = False
    annotation: bool = False
    supports_draft: bool = False
    version: int = 1

    def public(self) -> dict[str, Any]:
        default_reason = "Sẵn sàng trong Hub." if self.status == "operational" else "Adapter đã khai báo nhưng chưa có bounded smoke tương ứng."
        default_action = "Có thể chạy khi input hợp lệ." if self.status == "operational" else "Kiểm tra backend và chạy bounded smoke trước khi dùng production."
        return {
            "type": self.type,
            "title": self.title,
            "category": self.category,
            "description": self.description,
            "runner": self.runner,
            "inputs": [item.public() for item in self.inputs],
            "outputs": [item.public() for item in self.outputs],
            "properties": [dict(item) for item in self.properties],
            "status": self.status,
            "availability": {
                "status": self.status,
                "reason": self.status_reason or default_reason,
                "action": self.status_action or default_action,
            },
            "heavy": self.heavy,
            "annotation": self.annotation,
            "supports_draft": self.supports_draft,
            "version": self.version,
        }


def _port(name: str, kind: str, *, required: bool = False, multi: bool = False, label: str | None = None) -> Port:
    if kind not in PORT_TYPES:
        raise ValueError(f"Unsupported Node Studio port type: {kind}")
    return Port(name, kind, required=required, multi=multi, label=label)


def _prop(name: str, label: str, kind: str, default: Any = None, **extra: Any) -> dict[str, Any]:
    result = {"name": name, "label": label, "kind": kind, "default": default}
    result.update(extra)
    return result


def _node(
    type: str,
    title: str,
    category: str,
    description: str,
    runner: str,
    *,
    inputs: tuple[Port, ...] = (),
    outputs: tuple[Port, ...] = (),
    properties: tuple[dict[str, Any], ...] = (),
    status: str = "operational",
    status_reason: str | None = None,
    status_action: str | None = None,
    heavy: bool = False,
    annotation: bool = False,
    supports_draft: bool = False,
) -> NodeDefinition:
    return NodeDefinition(
        type=type,
        title=title,
        category=category,
        description=description,
        runner=runner,
        inputs=inputs,
        outputs=outputs,
        properties=properties,
        status=status,
        status_reason=status_reason,
        status_action=status_action,
        heavy=heavy,
        annotation=annotation,
        supports_draft=supports_draft,
    )


_DEFINITIONS: tuple[NodeDefinition, ...] = (
    # Utility and asset nodes
    _node("load_image", "Load Image", "utility", "Chọn image artifact đã upload vào Hub.", "load_artifact", outputs=(_port("image", "IMAGE"),), properties=(_prop("asset_id", "Image artifact", "asset", "", accept="image/*"),)),
    _node("load_video", "Load Video", "utility", "Chọn video artifact đã upload vào Hub.", "load_artifact", outputs=(_port("video", "VIDEO"),), properties=(_prop("asset_id", "Video artifact", "asset", "", accept="video/*"),)),
    _node("load_audio", "Load Audio", "utility", "Chọn audio artifact đã upload vào Hub.", "load_artifact", outputs=(_port("audio", "AUDIO"),), properties=(_prop("asset_id", "Audio artifact", "asset", "", accept="audio/*"),)),
    _node("load_subtitle", "Load Subtitle", "utility", "Chọn SRT/ASS artifact; dùng socket METADATA vì schema không có type Subtitle riêng.", "load_artifact", outputs=(_port("subtitle", "METADATA"),), properties=(_prop("asset_id", "Subtitle artifact", "asset", "", accept=".srt,.ass,text/plain"),)),
    _node("prompt_text", "Prompt / Text", "utility", "Text input dùng cho prompt và metadata.", "text", outputs=(_port("text", "TEXT"),), properties=(_prop("text", "Text", "textarea", ""),)),
    _node("number", "Number", "utility", "Giá trị số có typed socket NUMBER.", "number", outputs=(_port("number", "NUMBER"),), properties=(_prop("value", "Value", "number", 0),)),
    _node("boolean", "Boolean", "utility", "Giá trị bật/tắt có typed socket BOOLEAN.", "boolean", outputs=(_port("value", "BOOLEAN"),), properties=(_prop("value", "Bật", "boolean", False),)),
    _node("point_input", "Point Input", "vision", "Điểm x,y,label cho SAM2.", "points", outputs=(_port("points", "METADATA"),), properties=(_prop("points", "Points", "text", "256,256,1", hint="x,y,label; x,y,label"),)),
    _node("box_input", "Box Input", "vision", "Box x1,y1,x2,y2 cho SAM2.", "box", outputs=(_port("box", "METADATA"),), properties=(_prop("box", "Box", "text", "64,64,512,512"),)),
    _node("preview_image", "Preview Image", "utility", "Hiển thị output IMAGE/MASK trong Inspector.", "passthrough", inputs=(_port("image", "IMAGE", required=True),), outputs=(_port("image", "IMAGE"),)),
    _node("preview_video", "Preview Video", "utility", "Hiển thị output VIDEO trong Inspector.", "passthrough", inputs=(_port("video", "VIDEO", required=True),), outputs=(_port("video", "VIDEO"),)),
    _node("save_image", "Save Image", "utility", "Đánh dấu artifact image cuối cùng; không ghi đè source.", "passthrough", inputs=(_port("image", "IMAGE", required=True),), outputs=(_port("image", "IMAGE"),)),
    _node("save_video", "Save Video", "utility", "Đánh dấu artifact video cuối cùng; không ghi đè source.", "passthrough", inputs=(_port("video", "VIDEO", required=True),), outputs=(_port("video", "VIDEO"),)),
    _node("export_mask", "Export Mask", "vision", "Xuất mask artifact Hub.", "passthrough", inputs=(_port("mask", "MASK", required=True),), outputs=(_port("mask", "MASK"),)),
    _node("export_video", "Export Mask/Video", "vision", "Xuất video mask/tracking artifact Hub.", "passthrough", inputs=(_port("video", "VIDEO", required=True),), outputs=(_port("video", "VIDEO"),)),
    _node("comment", "Comment", "annotation", "Ghi chú không tham gia execution.", "annotation", properties=(_prop("text", "Comment", "textarea", "Ghi chú workflow"),), annotation=True),
    _node("group", "Group", "annotation", "Nhóm trực quan cho các node trong graph JSON.", "annotation", properties=(_prop("title", "Tên nhóm", "text", "Nhóm"), _prop("color", "Màu", "color", "#4d7dff")), annotation=True),

    # Image AI
    _node("resolution", "Resolution", "image", "Width/height có thể nối tới generator.", "resolution", outputs=(_port("width", "NUMBER"), _port("height", "NUMBER"), _port("settings", "METADATA")), properties=(_prop("width", "Width", "number", 768, min=256, max=2048, step=64), _prop("height", "Height", "number", 768, min=256, max=2048, step=64))),
    _node("seed", "Seed", "image", "Seed tái lập khi backend hỗ trợ.", "number", outputs=(_port("seed", "NUMBER"),), properties=(_prop("value", "Seed", "number", 42, min=0),)),
    _node("sampler_settings", "Steps / Sampler", "image", "Steps và sampler được adapter image hỗ trợ.", "metadata", outputs=(_port("settings", "METADATA"),), properties=(_prop("steps", "Steps", "number", 20, min=1, max=80), _prop("sampler", "Sampler", "select", "backend_default", options=["backend_default"]))),
    _node("flux_generate", "FLUX Generate", "image", "Compile graph Hub thành request ComfyUI FLUX.", "flux", inputs=(_port("prompt", "TEXT", required=True), _port("image", "IMAGE"), _port("width", "NUMBER"), _port("height", "NUMBER"), _port("seed", "NUMBER"), _port("settings", "METADATA")), outputs=(_port("image", "IMAGE"), _port("metadata", "METADATA")), properties=(_prop("width", "Width", "number", 768, min=256, max=2048, step=64), _prop("height", "Height", "number", 768, min=256, max=2048, step=64), _prop("steps", "Steps", "number", 20, min=1, max=80), _prop("seed", "Seed", "number", 42, min=0), _prop("negative_prompt", "Negative prompt", "text", "")), status="partial", status_reason="ComfyUI và bộ model FLUX chưa có bounded generation smoke trong Hub.", status_action="Khởi động ComfyUI, kiểm tra model FLUX và chạy một ảnh thử nhỏ.", heavy=True, supports_draft=True),
    _node("qwen_image", "Qwen Image Generate", "image", "Compile graph Hub thành request ComfyUI Qwen Image.", "qwen", inputs=(_port("prompt", "TEXT", required=True), _port("image", "IMAGE"), _port("width", "NUMBER"), _port("height", "NUMBER"), _port("seed", "NUMBER"), _port("settings", "METADATA")), outputs=(_port("image", "IMAGE"), _port("metadata", "METADATA")), properties=(_prop("width", "Width", "number", 768, min=256, max=2048, step=64), _prop("height", "Height", "number", 768, min=256, max=2048, step=64), _prop("steps", "Steps", "number", 20, min=1, max=80), _prop("seed", "Seed", "number", 42, min=0), _prop("negative_prompt", "Negative prompt", "text", "")), status="partial", status_reason="ComfyUI và bộ model Qwen Image chưa có bounded generation smoke trong Hub.", status_action="Khởi động ComfyUI, kiểm tra model Qwen Image và chạy một ảnh thử nhỏ.", heavy=True, supports_draft=True),
    _node("image_edit", "Image Edit / Image-to-Image", "image", "Chỉnh ảnh bằng prompt và IMAGE artifact qua Qwen Image.", "qwen", inputs=(_port("prompt", "TEXT", required=True), _port("image", "IMAGE", required=True), _port("width", "NUMBER"), _port("height", "NUMBER"), _port("seed", "NUMBER"), _port("settings", "METADATA")), outputs=(_port("image", "IMAGE"), _port("metadata", "METADATA")), properties=(_prop("width", "Width", "number", 768, min=256, max=2048, step=64), _prop("height", "Height", "number", 768, min=256, max=2048, step=64), _prop("steps", "Steps", "number", 20, min=1, max=80), _prop("seed", "Seed", "number", 42, min=0), _prop("negative_prompt", "Negative prompt", "text", "")), status="partial", status_reason="Qwen Image edit cần ComfyUI workflow hỗ trợ input image và chưa có bounded smoke trong Hub.", status_action="Cấu hình workflow image-to-image trong ComfyUI rồi chạy smoke với một ảnh nhỏ.", heavy=True, supports_draft=True),
    _node("image_resize", "Resize", "image", "Resize image qua FFmpeg allowlist.", "media", inputs=(_port("image", "IMAGE", required=True),), outputs=(_port("image", "IMAGE"),), properties=(_prop("width", "Width", "number", 1280, min=2), _prop("height", "Height", "number", -2, min=-2)), status="partial", status_reason="FFmpeg image transform chưa có bounded smoke trong phiên Hub này.", status_action="Kiểm tra FFmpeg canonical rồi chạy smoke với một ảnh nhỏ."),
    _node("image_upscale", "Upscale Image (FFmpeg fallback)", "image", "Phóng ảnh theo tỉ lệ qua FFmpeg; không giả nhận đây là AI upscaler.", "image_upscale", inputs=(_port("image", "IMAGE", required=True),), outputs=(_port("image", "IMAGE"), _port("metadata", "METADATA")), properties=(_prop("scale", "Scale", "select", 2, options=[2, 3, 4]),), status="partial", status_reason="FFmpeg scale fallback có contract; AI upscaler chuyên dụng chưa được bounded smoke.", status_action="Dùng fallback để kiểm tra pipeline; cấu hình Real-ESRGAN/AnimeSR riêng nếu cần AI upscale."),
    _node("image_crop", "Crop", "image", "Crop image qua FFmpeg allowlist.", "media", inputs=(_port("image", "IMAGE", required=True),), outputs=(_port("image", "IMAGE"),), properties=(_prop("width", "Width", "number", 720, min=2), _prop("height", "Height", "number", 720, min=2), _prop("x", "X", "number", 0, min=0), _prop("y", "Y", "number", 0, min=0)), status="partial"),
    _node("image_rotate", "Rotate", "image", "Rotate image qua FFmpeg allowlist.", "media", inputs=(_port("image", "IMAGE", required=True),), outputs=(_port("image", "IMAGE"),), properties=(_prop("degrees", "Degrees", "select", 90, options=[90, 180, 270]),), status="partial"),
    _node("image_flip", "Flip", "image", "Flip image qua FFmpeg allowlist.", "media", inputs=(_port("image", "IMAGE", required=True),), outputs=(_port("image", "IMAGE"),), properties=(_prop("axis", "Axis", "select", "horizontal", options=["horizontal", "vertical"]),), status="partial"),
    _node("image_levels", "Color / Levels", "image", "Brightness, contrast và saturation qua FFmpeg eq filter.", "media", inputs=(_port("image", "IMAGE", required=True),), outputs=(_port("image", "IMAGE"),), properties=(_prop("brightness", "Brightness", "number", 0, min=-1, max=1, step=0.05), _prop("contrast", "Contrast", "number", 1, min=0, max=3, step=0.05), _prop("saturation", "Saturation", "number", 1, min=0, max=3, step=0.05)), status="partial"),
    _node("mask_apply", "Mask Apply", "image", "Áp mask bằng Pillow khi environment Hub có Pillow.", "mask_apply", inputs=(_port("image", "IMAGE", required=True), _port("mask", "MASK", required=True)), outputs=(_port("image", "IMAGE"),), status="partial"),
    _node("mask_composite", "Mask Composite", "image", "Composite foreground/mask bằng Pillow khi khả dụng.", "mask_composite", inputs=(_port("image", "IMAGE", required=True), _port("mask", "MASK", required=True)), outputs=(_port("image", "IMAGE"),), status="partial"),
    _node("image_compare", "Image Compare A/B", "image", "Giữ hai IMAGE artifacts để Inspector hiển thị before/after.", "compare", inputs=(_port("a", "IMAGE", required=True, label="Before"), _port("b", "IMAGE", required=True, label="After")), outputs=(_port("a", "IMAGE"), _port("b", "IMAGE"), _port("comparison", "METADATA"))),

    # SAM2 and vision
    _node("grounding_prompt", "Grounding Prompt", "vision", "Prompt TEXT cho Grounding DINO.", "text", outputs=(_port("text", "TEXT"),), properties=(_prop("text", "Prompt", "text", "person . object ."),)),
    _node("grounding_dino", "Grounding DINO", "vision", "Prompt → boxes; output có thể nối trực tiếp SAM2 Segment.", "grounding", inputs=(_port("image", "IMAGE", required=True), _port("prompt", "TEXT", required=True)), outputs=(_port("boxes", "METADATA"),), properties=(_prop("box_threshold", "Box threshold", "number", 0.35, min=0, max=1, step=0.01), _prop("text_threshold", "Text threshold", "number", 0.25, min=0, max=1, step=0.01)), status="partial", heavy=True),
    _node("rfdetr_detect", "RF-DETR Detect", "vision", "Object detection metadata qua direct worker.", "rfdetr", inputs=(_port("image", "IMAGE", required=True),), outputs=(_port("detections", "METADATA"),), properties=(_prop("threshold", "Threshold", "number", 0.5, min=0, max=1, step=0.01),), status="partial", heavy=True),
    _node("sam2_segment", "SAM2 Segment", "vision", "Points/box/Grounding boxes → MASK và image preview.", "sam2_segment", inputs=(_port("image", "IMAGE", required=True), _port("points", "METADATA"), _port("box", "METADATA"), _port("boxes", "METADATA")), outputs=(_port("mask", "MASK"), _port("preview", "IMAGE"), _port("metadata", "METADATA")), status="partial", heavy=True),
    _node("sam2_track", "SAM2 Track", "vision", "Theo dõi object trên VIDEO; chỉ chạy khi user bấm Run Graph.", "sam2_track", inputs=(_port("video", "VIDEO", required=True), _port("points", "METADATA"), _port("box", "METADATA")), outputs=(_port("video", "VIDEO"), _port("mask", "MASK"), _port("metadata", "METADATA")), status="partial", heavy=True),
    _node("mask_preview", "Mask Preview", "vision", "Preview typed MASK trong Inspector.", "passthrough", inputs=(_port("mask", "MASK", required=True),), outputs=(_port("mask", "MASK"),)),

    # Media, encoding and video AI
    _node("probe_media", "Probe", "media", "FFprobe đọc metadata của VIDEO/AUDIO.", "probe", inputs=(_port("media", "VIDEO", required=True),), outputs=(_port("metadata", "METADATA"),), status="operational"),
    _node("trim_cut", "Trim / Cut", "media", "Cắt VIDEO bằng FFmpeg allowlist.", "media", inputs=(_port("video", "VIDEO", required=True),), outputs=(_port("video", "VIDEO"),), properties=(_prop("start", "Start", "number", 0, min=0, step=0.1), _prop("end", "End", "number", 5, min=0.1, step=0.1)), status="partial"),
    _node("concat", "Concat", "media", "Ghép nhiều VIDEO artifacts theo thứ tự dây nối.", "media", inputs=(_port("videos", "VIDEO", required=True, multi=True),), outputs=(_port("video", "VIDEO"),), status="partial"),
    _node("video_crop", "Crop", "media", "Crop VIDEO bằng FFmpeg allowlist.", "media", inputs=(_port("video", "VIDEO", required=True),), outputs=(_port("video", "VIDEO"),), properties=(_prop("width", "Width", "number", 720, min=2), _prop("height", "Height", "number", 720, min=2), _prop("x", "X", "number", 0, min=0), _prop("y", "Y", "number", 0, min=0)), status="partial"),
    _node("video_resize", "Resize", "media", "Resize VIDEO bằng FFmpeg allowlist.", "media", inputs=(_port("video", "VIDEO", required=True),), outputs=(_port("video", "VIDEO"),), properties=(_prop("width", "Width", "number", 1280, min=2), _prop("height", "Height", "number", -2, min=-2)), status="partial"),
    _node("video_rotate", "Rotate", "media", "Rotate VIDEO bằng FFmpeg allowlist.", "media", inputs=(_port("video", "VIDEO", required=True),), outputs=(_port("video", "VIDEO"),), properties=(_prop("degrees", "Degrees", "select", 90, options=[90, 180, 270]),), status="partial"),
    _node("video_fps", "FPS", "media", "Đổi FPS qua FFmpeg allowlist.", "media", inputs=(_port("video", "VIDEO", required=True),), outputs=(_port("video", "VIDEO"),), properties=(_prop("fps", "FPS", "number", 30, min=1, max=120),), status="partial"),
    _node("extract_audio", "Extract Audio", "media", "Tách AUDIO khỏi VIDEO.", "media", inputs=(_port("video", "VIDEO", required=True),), outputs=(_port("audio", "AUDIO"),), status="partial"),
    _node("replace_audio", "Replace Audio", "media", "Thay AUDIO trong VIDEO, giữ duration sync bằng shortest.", "media", inputs=(_port("video", "VIDEO", required=True), _port("audio", "AUDIO", required=True)), outputs=(_port("video", "VIDEO"),), status="partial"),
    _node("subtitle_burn", "Subtitle Burn", "media", "Burn SRT/ASS artifact vào VIDEO.", "media", inputs=(_port("video", "VIDEO", required=True), _port("subtitle", "METADATA", required=True, label="Subtitle artifact")), outputs=(_port("video", "VIDEO"),), status="partial"),
    _node("extract_frames", "Extract Frames", "media", "Tách frame PNG vào Output Hub.", "media", inputs=(_port("video", "VIDEO", required=True),), outputs=(_port("frames", "METADATA"),), status="partial"),
    _node("frame_interpolate", "Frame Interpolation", "media", "Ưu tiên Practical-RIFE khi có contract; fallback FFmpeg minterpolate hiện rõ.", "frame_interpolate", inputs=(_port("video", "VIDEO", required=True),), outputs=(_port("video", "VIDEO"), _port("metadata", "METADATA")), properties=(_prop("mode", "Mode", "select", "target_fps", options=["off", "2x", "4x", "target_fps"]), _prop("target_fps", "Target FPS", "number", 60, min=1, max=120), _prop("backend", "Backend", "select", "ffmpeg_minterpolate", options=["ffmpeg_minterpolate", "practical_rife"])), status="partial", heavy=True),
    _node("animesr_upscale", "AnimeSR Upscale", "video", "AnimeSR direct worker, không mở Anime Upscale Studio.", "animesr", inputs=(_port("video", "VIDEO", required=True),), outputs=(_port("video", "VIDEO"),), properties=(_prop("scale", "Scale", "select", 2, options=[1, 2, 3, 4]),), status="partial", heavy=True),
    _node("realesrgan_upscale", "Real-ESRGAN", "video", "Hiển thị partial cho đến khi CLI contract được smoke.", "realesrgan", inputs=(_port("image", "IMAGE", required=True),), outputs=(_port("image", "IMAGE"),), status="partial", heavy=True),
    _node("encode", "Encode", "media", "Encode theo capabilities FFmpeg được dò và cache cục bộ.", "encode", inputs=(_port("video", "VIDEO", required=True), _port("audio", "AUDIO")), outputs=(_port("video", "VIDEO"), _port("metadata", "METADATA")), properties=(_prop("container", "Container", "select", "mp4", options=["mp4", "mkv", "webm"]), _prop("codec", "Codec", "encoder", "auto"), _prop("rate_control", "Rate control", "select", "quality", options=["quality", "vbr", "cbr"]), _prop("quality", "CRF / CQ", "number", 20, min=0, max=51), _prop("bitrate_kbps", "Target bitrate (kbps)", "number", 6000, min=100), _prop("max_bitrate_kbps", "Max bitrate (kbps)", "number", 9000, min=100), _prop("buffer_kbps", "Buffer (kbps)", "number", 12000, min=100), _prop("preset", "Preset", "text", "medium"), _prop("pixel_format", "Pixel format", "text", ""), _prop("audio_codec", "Audio codec", "text", "aac"), _prop("audio_bitrate_kbps", "Audio bitrate (kbps)", "number", 192, min=32), _prop("prefer_gpu", "Ưu tiên NVIDIA NVENC", "boolean", True), _prop("multipass", "Multipass nếu encoder hỗ trợ", "boolean", False)), status="partial", heavy=True),
    _node(
        "comfyui_workflow",
        "ComfyUI Workflow",
        "image",
        "Chạy bridge workflow ComfyUI đã lưu; TEXT/IMAGE/MASK nối từ Hub, không nhận raw path.",
        "comfyui_workflow",
        inputs=(_port("text", "TEXT"), _port("image", "IMAGE"), _port("mask", "MASK"), _port("video", "VIDEO"), _port("audio", "AUDIO"), _port("metadata", "METADATA")),
        outputs=(_port("image", "IMAGE"), _port("metadata", "METADATA")),
        properties=(_prop("workflow_id", "Bridge workflow ID", "text", "flux_quick"), _prop("prompt", "Prompt fallback", "textarea", ""), _prop("width", "Width", "number", 768, min=256, max=2048, step=64), _prop("height", "Height", "number", 768, min=256, max=2048, step=64), _prop("steps", "Steps", "number", 20, min=1, max=80), _prop("seed", "Seed", "number", 42, min=0), _prop("negative_prompt", "Negative prompt", "text", "")),
        status="partial",
        status_reason="ComfyUI bridge chưa có bounded generation smoke trong Hub.",
        status_action="Kiểm tra bridge workflow và ComfyUI trước khi chạy.",
        heavy=True,
        supports_draft=True,
    ),
)

NODE_DEFINITIONS = {item.type: item for item in _DEFINITIONS}
SCOPE_CATEGORIES = {
    "image": {"utility", "image", "vision", "annotation"},
    "sam2": {"utility", "image", "vision", "annotation"},
    "media": {"utility", "media", "video", "annotation"},
    "animesr": {"utility", "media", "video", "annotation"},
}


def get_definition(type_name: str) -> NodeDefinition | None:
    return NODE_DEFINITIONS.get(type_name)


def definitions_for_scope(scope: str | None = None) -> Iterable[NodeDefinition]:
    allowed = SCOPE_CATEGORIES.get(scope or "", None)
    return (item for item in _DEFINITIONS if allowed is None or item.category in allowed)


def graph_has_heavy_nodes(graph: object) -> bool:
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list):
        return False
    for node in graph["nodes"]:
        definition = get_definition(str(node.get("type") or "")) if isinstance(node, dict) else None
        if definition and definition.heavy:
            return True
    return False


def registry_payload(scope: str | None = None) -> dict[str, Any]:
    """Return the offline palette plus the detected FFmpeg encode capabilities."""

    capabilities: dict[str, Any] = {"available": False, "reason": "Chưa dò FFmpeg."}
    try:
        # This stays lazy: opening the API or desktop shell never starts FFmpeg.
        from src.modules.media_editor.backend.adapter import encoder_capabilities

        capabilities = encoder_capabilities()
    except Exception as exc:  # pragma: no cover - optional runtime may be absent
        capabilities = {"available": False, "reason": str(exc)}
    definitions = list(definitions_for_scope(scope))
    counts = {status: sum(1 for item in definitions if item.status == status) for status in ("operational", "partial", "unavailable")}
    return {
        "status": "completed",
        "contract_version": "node-studio.v2",
        "schema_version": GRAPH_SCHEMA_VERSION,
        "port_types": list(PORT_TYPES),
        "scope": scope or "all",
        "nodes": [item.public() for item in definitions],
        "availability": {
            "counts": counts,
            "honest_statuses": ["operational", "partial", "unavailable"],
            "rule": "Node partial/unavailable không được coi là đã chạy nếu chưa có bounded smoke.",
        },
        "encoder_capabilities": capabilities,
    }
