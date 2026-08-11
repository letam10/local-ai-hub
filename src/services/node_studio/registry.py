"""Node definitions for the Hub-owned, offline Node Studio.

This is an original, small graph vocabulary.  It deliberately does not import
or copy the ComfyUI frontend.  Definitions describe real adapters or mark an
incomplete adapter as ``partial``/``unavailable``; the browser never receives
a shell command or a workstation path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from typing import Any, Iterable


GRAPH_SCHEMA_VERSION = 1
PORT_TYPES = ("IMAGE", "MASK", "VIDEO", "AUDIO", "TEXT", "NUMBER", "BOOLEAN", "MODEL", "METADATA")
OPAQUE_ARTIFACT_ID = re.compile(r"^artifact_[a-f0-9]{32}$")
_UNSAFE_PROPERTY_NAMES = {
    "command", "commands", "executable", "executable_path", "filter", "filter_complex",
    "font", "font_path", "path", "path_override", "secret", "token", "url", "vf",
}


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


def validate_node_data(graph: object) -> list[dict[str, Any]]:
    """Validate closed node properties without echoing unsafe values.

    The graph schema owns graph topology.  This companion contract keeps new
    media controls closed over finite numbers, enum values and opaque artifact
    IDs while rejecting command/filter/path-shaped client fields.
    """

    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list):
        return []
    errors: list[dict[str, Any]] = []
    for node in graph["nodes"]:
        if not isinstance(node, dict):
            continue
        node_id = node.get("id")
        definition = get_definition(str(node.get("type") or ""))
        data = node.get("data")
        if definition is None or not isinstance(data, dict):
            continue
        properties = {str(item.get("name")): item for item in definition.properties}
        for name, value in data.items():
            key = str(name)
            lowered = key.casefold()
            if lowered in _UNSAFE_PROPERTY_NAMES or any(token in lowered for token in ("command", "executable", "filter", "font_path", "path_override", "secret")):
                errors.append({"code": "unsafe_node_property", "message": "Node property khong nam trong allowlist an toan.", "node_id": node_id, "property": key})
                continue
            property_definition = properties.get(key)
            if property_definition is None:
                continue
            kind = property_definition.get("kind")
            if kind == "asset" and value not in (None, "") and (not isinstance(value, str) or not OPAQUE_ARTIFACT_ID.fullmatch(value)):
                errors.append({"code": "invalid_asset_id", "message": "Asset property phai dung opaque artifact ID cua Hub.", "node_id": node_id, "property": key})
            elif kind == "number":
                try:
                    numeric_value = float(value)
                except (TypeError, ValueError, OverflowError):
                    numeric_value = math.nan
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(numeric_value):
                    errors.append({"code": "invalid_number", "message": "Number property phai la gia tri huu han.", "node_id": node_id, "property": key})
                    continue
                if property_definition.get("min") is not None and numeric_value < float(property_definition["min"]):
                    errors.append({"code": "number_below_minimum", "message": "Number property vuot gioi han toi thieu.", "node_id": node_id, "property": key})
                if property_definition.get("max") is not None and numeric_value > float(property_definition["max"]):
                    errors.append({"code": "number_above_maximum", "message": "Number property vuot gioi han toi da.", "node_id": node_id, "property": key})
            elif kind in {"select", "encoder"}:
                options = property_definition.get("options") if isinstance(property_definition.get("options"), list) else []
                allowed_encoder_fallback = kind == "encoder" and not options and value == "auto"
                allowed_values = {str(item) for item in options}
                if str(value) not in allowed_values and not allowed_encoder_fallback:
                    errors.append({"code": "invalid_option", "message": "Select property khong nam trong allowlist.", "node_id": node_id, "property": key})
            elif kind in {"text", "textarea"} and not isinstance(value, str):
                errors.append({"code": "invalid_text", "message": "Text property phai la chuoi.", "node_id": node_id, "property": key})
            if isinstance(value, str) and property_definition.get("max_length") is not None and len(value) > int(property_definition["max_length"]):
                errors.append({"code": "text_too_long", "message": "Text property vuot gioi han.", "node_id": node_id, "property": key})
    return errors


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
    _node("export_video", "Export Video", "video", "Xuất video artifact cuối cùng trong vùng Hub; không ghi đè source.", "passthrough", inputs=(_port("video", "VIDEO", required=True),), outputs=(_port("video", "VIDEO"),)),
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

    # Video creative workflow
    _node(
        "video_generate",
        "Video Generate (backend partial)",
        "video",
        "Prompt TEXT → video backend local; giữ rõ unavailable khi chưa có adapter generation đã smoke.",
        "video_generate",
        inputs=(_port("prompt", "TEXT", required=True), _port("video", "VIDEO")),
        outputs=(_port("video", "VIDEO"), _port("metadata", "METADATA")),
        properties=(_prop("width", "Width", "number", 768, min=64, max=2048, step=2), _prop("height", "Height", "number", 432, min=64, max=2048, step=2), _prop("duration", "Duration (s)", "number", 4, min=1, max=30, step=1), _prop("fps", "FPS", "number", 24, min=1, max=60), _prop("seed", "Seed", "number", 42, min=0)),
        status="unavailable",
        status_reason="Hub chưa có video-generation adapter local được bounded smoke; không giả nhận prompt thành output.",
        status_action="Dùng Video Transform với video artifact hoặc cấu hình backend video local rồi chạy smoke trước.",
        heavy=True,
    ),
    _node(
        "video_transform",
        "Video Transform",
        "video",
        "Video artifact + creative prompt tuỳ chọn → transform FFmpeg allowlist.",
        "video_transform",
        inputs=(_port("video", "VIDEO", required=True), _port("prompt", "TEXT")),
        outputs=(_port("video", "VIDEO"), _port("metadata", "METADATA")),
        properties=(_prop("operation", "Transform", "select", "resize", options=["resize", "crop", "rotate", "fps", "transcode"]), _prop("width", "Width", "number", 1280, min=2), _prop("height", "Height", "number", -2, min=-2), _prop("degrees", "Degrees", "select", 90, options=[90, 180, 270]), _prop("fps", "FPS", "number", 30, min=1, max=120), _prop("crf", "CRF", "number", 18, min=14, max=32)),
        status="partial",
        status_reason="FFmpeg transform có adapter nhưng chưa có bounded video smoke trong phiên này.",
        status_action="Chọn một video nhỏ, chạy transform rồi kiểm tra preview/provenance trong Jobs.",
    ),
    _node(
        "video_upscale",
        "Video Upscale (AnimeSR / FFmpeg)",
        "video",
        "Upscale video qua AnimeSR khi sẵn sàng hoặc fallback FFmpeg scale; status không giả nhận AI.",
        "video_upscale",
        inputs=(_port("video", "VIDEO", required=True),),
        outputs=(_port("video", "VIDEO"), _port("metadata", "METADATA")),
        properties=(_prop("backend", "Backend", "select", "ffmpeg_scale", options=["ffmpeg_scale", "animesr"]), _prop("scale", "Scale", "select", 2, options=[2, 3, 4])),
        status="partial",
        status_reason="FFmpeg scale fallback có contract; AnimeSR direct worker vẫn cần bounded smoke riêng.",
        status_action="Dùng ffmpeg_scale để smoke pipeline; chọn AnimeSR chỉ khi environment và worker đã sẵn sàng.",
        heavy=True,
    ),

    # Safe video production vocabulary.  Each control is closed over bounded
    # numeric/select properties; no arbitrary filter, font or command enters
    # the graph contract.
    _node(
        "video_grade",
        "Video Grade",
        "video",
        "Color grade VIDEO voi brightness, contrast, saturation, gamma va tuy chon denoise/sharpen bounded; khong nhan filter tuy y.",
        "video_grade",
        inputs=(_port("video", "VIDEO", required=True),),
        outputs=(_port("video", "VIDEO"), _port("metadata", "METADATA")),
        properties=(
            _prop("brightness", "Brightness", "number", 0, min=-1, max=1, step=0.05),
            _prop("contrast", "Contrast", "number", 1, min=0, max=3, step=0.05),
            _prop("saturation", "Saturation", "number", 1, min=0, max=3, step=0.05),
            _prop("gamma", "Gamma", "number", 1, min=0.1, max=4, step=0.05),
            _prop("denoise", "Denoise", "select", "off", options=["off", "light", "medium"]),
            _prop("sharpen", "Sharpen", "select", "off", options=["off", "light", "medium"]),
        ),
        status="partial",
        status_reason="Video grade chi co safe FFmpeg command contract; chua co bounded video smoke trong lane nay.",
        status_action="Kiem tra FFmpeg canonical va chay smoke voi video nho truoc khi dung production.",
    ),
    _node(
        "logo_overlay",
        "Logo / Image Overlay",
        "video",
        "Overlay IMAGE artifact opaque voi position co dinh va opacity bounded; khong nhan raw path, font hay filter.",
        "logo_overlay",
        inputs=(_port("video", "VIDEO", required=True), _port("image", "IMAGE", required=True, label="Logo image")),
        outputs=(_port("video", "VIDEO"), _port("metadata", "METADATA")),
        properties=(
            _prop("position", "Position", "select", "top_right", options=["top_left", "top_right", "bottom_left", "bottom_right", "center"]),
            _prop("opacity", "Opacity", "number", 0.85, min=0, max=1, step=0.05),
        ),
        status="partial",
        status_reason="Logo overlay chi cho phep IMAGE artifact va vi tri/opacity allowlist; chua co bounded video smoke.",
        status_action="Upload mot IMAGE artifact, kiem tra adapter va chay smoke voi video nho.",
    ),
    _node(
        "audio_loudness",
        "Audio Loudness",
        "media",
        "Normalize AUDIO bang loudnorm va gain bounded; dau vao/dau ra deu la AUDIO, khong co filter tuy y.",
        "audio_loudness",
        inputs=(_port("audio", "AUDIO", required=True),),
        outputs=(_port("audio", "AUDIO"), _port("metadata", "METADATA")),
        properties=(
            _prop("target_lufs", "Target LUFS", "number", -16, min=-40, max=-5, step=0.5),
            _prop("true_peak", "True peak", "number", -1.5, min=-9, max=0, step=0.1),
            _prop("gain_db", "Gain dB", "number", 0, min=-24, max=24, step=0.5),
        ),
        status="partial",
        status_reason="Audio loudness co safe FFmpeg filter contract; chua co bounded audio smoke trong lane nay.",
        status_action="Chon AUDIO artifact, kiem tra loudness output va chay smoke bounded khi duoc phep.",
    ),
    _node(
        "text_overlay",
        "Text Overlay (unavailable)",
        "video",
        "Text overlay chua co server-owned font va escaping contract day du; dung Subtitle Burn voi SRT/ASS artifact thay the.",
        "text_overlay",
        inputs=(_port("video", "VIDEO", required=True), _port("text", "TEXT", required=True)),
        outputs=(_port("video", "VIDEO"),),
        status="unavailable",
        status_reason="Hub chua co font registry server-owned va escaping contract an toan cho text overlay.",
        status_action="Dung Load Subtitle -> Subtitle Burn voi SRT/ASS artifact da upload.",
    ),

    # SAM2 and vision
    _node("grounding_prompt", "Grounding Prompt", "vision", "Prompt TEXT cho Grounding DINO.", "text", outputs=(_port("text", "TEXT"),), properties=(_prop("text", "Prompt", "text", "person . object ."),)),
    _node("grounding_dino", "Grounding DINO", "vision", "Prompt → boxes; output có thể nối trực tiếp SAM2 Segment.", "grounding", inputs=(_port("image", "IMAGE", required=True), _port("prompt", "TEXT", required=True)), outputs=(_port("boxes", "METADATA"),), properties=(_prop("box_threshold", "Box threshold", "number", 0.35, min=0, max=1, step=0.01), _prop("text_threshold", "Text threshold", "number", 0.25, min=0, max=1, step=0.01)), status="partial", heavy=True),
    _node("rfdetr_detect", "RF-DETR Detect", "vision", "Object detection metadata qua direct worker.", "rfdetr", inputs=(_port("image", "IMAGE", required=True),), outputs=(_port("detections", "METADATA"),), properties=(_prop("threshold", "Threshold", "number", 0.5, min=0, max=1, step=0.01),), status="partial", heavy=True),
    _node("sam2_segment", "SAM2 Segment", "vision", "Points/box/Grounding boxes → MASK và image preview.", "sam2_segment", inputs=(_port("image", "IMAGE", required=True), _port("points", "METADATA"), _port("box", "METADATA"), _port("boxes", "METADATA")), outputs=(_port("mask", "MASK"), _port("preview", "IMAGE"), _port("metadata", "METADATA")), status="partial", heavy=True),
    _node("sam2_track", "SAM2 Track", "vision", "Theo dõi object trên VIDEO; chỉ chạy khi user bấm Run Graph.", "sam2_track", inputs=(_port("video", "VIDEO", required=True), _port("points", "METADATA"), _port("box", "METADATA")), outputs=(_port("video", "VIDEO"), _port("mask", "MASK"), _port("metadata", "METADATA")), status="partial", heavy=True),
    _node("mask_preview", "Mask Preview", "vision", "Preview typed MASK trong Inspector.", "passthrough", inputs=(_port("mask", "MASK", required=True),), outputs=(_port("mask", "MASK"),)),

    # Keep media probes typed: VIDEO and AUDIO are distinct contracts.
    _node("probe_audio", "Probe Audio", "media", "FFprobe doc metadata cua AUDIO typed.", "probe_audio", inputs=(_port("audio", "AUDIO", required=True),), outputs=(_port("metadata", "METADATA"),), status="operational"),

    # Media, encoding and video AI
    _node("probe_media", "Probe Video", "media", "FFprobe đọc metadata của VIDEO typed.", "probe", inputs=(_port("media", "VIDEO", required=True),), outputs=(_port("metadata", "METADATA"),), status="operational"),
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


def _safe_encoder_snapshot(value: object) -> dict[str, Any]:
    """Project only cached, server-owned encoder fields to the public palette."""

    fallback = {
        "status": "not_run",
        "execution": "not_run",
        "available": False,
        "reason": "Encoder discovery is not run when the Node Studio registry opens.",
        "encoders": [],
        "containers": ["mp4", "mkv", "webm"],
        "audio_encoders": [],
    }
    if not isinstance(value, dict):
        return fallback
    status = str(value.get("status") or ("completed" if value.get("available") else "unavailable"))
    if status not in {"completed", "unavailable", "not_run"}:
        status = "unavailable"
    raw_reason = str(value.get("reason") or fallback["reason"])
    reason = "Server-owned encoder snapshot reason redacted." if re.search(r"(?:[A-Za-z]:[\\/]|/|\\\\)", raw_reason) else raw_reason[:240]
    raw_execution = str(value.get("execution") or "completed")
    execution = raw_execution if raw_execution in {"not_run", "completed", "unavailable"} else "unavailable"
    result = {
        "status": status,
        "execution": "not_run" if status == "not_run" else execution,
        "available": bool(value.get("available")) if status == "completed" else False,
        "reason": reason,
        "encoders": [],
        "containers": [item for item in value.get("containers", []) if item in {"mp4", "mkv", "webm"}] if isinstance(value.get("containers"), list) else fallback["containers"],
        "audio_encoders": [item for item in value.get("audio_encoders", []) if isinstance(item, str) and re.fullmatch(r"[a-z0-9_]{1,40}", item)] if isinstance(value.get("audio_encoders"), list) else [],
    }
    if result["available"] and isinstance(value.get("encoders"), list):
        for item in value["encoders"]:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not re.fullmatch(r"[a-z0-9_]{1,64}", item["id"]):
                continue
            result["encoders"].append({
                "id": item["id"],
                "codec": str(item.get("codec") or "")[:32],
                "available": bool(item.get("available")),
                "pixel_formats": [fmt for fmt in item.get("pixel_formats", []) if isinstance(fmt, str) and re.fullmatch(r"[a-z0-9_]{1,32}", fmt)] if isinstance(item.get("pixel_formats"), list) else [],
                "rate_controls": [rate for rate in item.get("rate_controls", []) if rate in {"quality", "vbr", "cbr"}] if isinstance(item.get("rate_controls"), list) else [],
                "quality_option": item.get("quality_option") if item.get("quality_option") in {"crf", "cq", None} else None,
                "preset_supported": bool(item.get("preset_supported")),
                "multipass_supported": bool(item.get("multipass_supported")),
                "hardware": bool(item.get("hardware")),
            })
    return result


def registry_payload(scope: str | None = None) -> dict[str, Any]:
    """Return the offline palette plus a cached FFmpeg capability snapshot."""

    capabilities: dict[str, Any] = {"available": False, "reason": "Chưa dò FFmpeg."}
    try:
        # This stays lazy: opening the API or desktop shell never starts FFmpeg.
        from src.modules.media_editor.backend.adapter import cached_encoder_capabilities

        capabilities = _safe_encoder_snapshot(cached_encoder_capabilities())
    except Exception:  # pragma: no cover - optional runtime may be absent
        capabilities = _safe_encoder_snapshot({"status": "unavailable", "execution": "not_run", "available": False, "reason": "Cached encoder snapshot is unavailable."})
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
