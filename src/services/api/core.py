"""Truthful control plane and direct-workflow dispatch for Local AI Hub V3."""

from __future__ import annotations

import os
import re
import socket
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.services.artifact_store import resolve
from src.shared.version import PRODUCT_VERSION
from src.services.job_manager.manager import JobContext, job_manager
from src.services.tool_smoke import (
    passed as smoke_passed,
    runtime_evidence_operation_scope,
    runtime_evidence_passed,
    runtime_evidence_projection,
)

from .config import BASE_DIR, component, components, hub_config, module_manager_config
from .gpu import gpu_policy, query_gpu
from .jobs import active_jobs, get_job, list_jobs


TOOL_COMPONENTS = {
    "parse_screen": "omniparser",
    "detect_objects": "rfdetr",
    "ground_objects": "groundingdino",
    "segment_image": "sam2",
    "segment_from_box": "sam2",
    "segment_from_points": "sam2",
    "segment_from_text": "sam2",
    "track_video_object": "sam2",
    "ocr_document": "paddleocr_vl",
    "transcribe_media": "whisper",
    "create_subtitled_video": "whisper",
    "text_to_speech": "qwen3_tts",
    "design_voice": "qwen3_tts",
    "clone_voice": "qwen3_tts",
    "convert_voice": "seed_vc",
    "upscale_anime_video": "animesr",
    "probe_media": "ffmpeg",
    "run_media_operation": "ffmpeg",
    "generate_flux": "comfyui",
    "generate_qwen_image": "comfyui",
}

TOOL_STATUS_VALUES = {"operational", "partial", "queue_only", "unavailable", "planned", "error"}
READY_COMPONENT_STATUSES = {"installed", "running", "partial"}
SMOKE_ELIGIBLE_TOOLS = {
    "parse_screen",
    "detect_objects",
    "ground_objects",
    "segment_image",
    "segment_from_box",
    "segment_from_points",
    "track_video_object",
    "ocr_document",
    "transcribe_media",
    "create_subtitled_video",
    "text_to_speech",
    "design_voice",
    "clone_voice",
    "convert_voice",
    "upscale_anime_video",
    "generate_flux",
    "generate_qwen_image",
}

# Desktop shutdown must close submission admission and recheck the durable
# queue under one server-owned lock.  The desktop never races a new job into
# an API process it is about to terminate.
_submission_gate = threading.RLock()
_submissions_quiesced = False


def _submission_closed_payload() -> tuple[int, dict[str, Any]]:
    return 409, {
        "status": "closing",
        "error": "Hub đang đóng phiên desktop sở hữu API; không nhận job mới.",
        "next_action": "Chờ Hub đóng xong hoặc mở lại Hub rồi tạo tác vụ mới.",
    }


def prepare_owned_shutdown() -> tuple[int, dict[str, Any]]:
    """Atomically quiesce submission and recheck active Hub work.

    This route is meaningful only to the desktop process that owns the API.
    A nonzero recheck immediately reopens admission and asks the desktop to
    show its three-choice active-job decision instead of terminating workers.
    """

    global _submissions_quiesced
    with _submission_gate:
        _submissions_quiesced = True
        active = active_jobs()
        if active:
            _submissions_quiesced = False
            return 409, {
                "status": "active_jobs",
                "active_jobs": len(active),
                "message": "Hub phát hiện job mới hoặc đang hoạt động; cửa sổ vẫn được giữ mở để chọn cách xử lý an toàn.",
            }
        return 200, {
            "status": "ready_to_close",
            "active_jobs": 0,
            "message": "Hub đã khóa nhận job mới và có thể đóng API do desktop này sở hữu.",
        }

# A direct adapter stays partial until its bounded functional smoke records a
# completion.  The UI may still submit it; the result remains truthful.
TOOL_CAPABILITIES = {
    "parse_screen": ("partial", "OmniParser direct worker đã cấu hình; chưa có smoke V3 được ghi nhận."),
    "detect_objects": ("partial", "RF-DETR direct worker đã cấu hình; chưa có smoke V3 được ghi nhận."),
    "ground_objects": ("partial", "Grounding DINO direct worker đã cấu hình; chưa có smoke V3 được ghi nhận."),
    "segment_image": ("partial", "SAM2 direct worker đã cấu hình; chưa có smoke V3 được ghi nhận."),
    "segment_from_box": ("partial", "SAM2 box workflow đã cấu hình; chưa có smoke V3 được ghi nhận."),
    "segment_from_points": ("partial", "SAM2 point workflow đã cấu hình; chưa có smoke V3 được ghi nhận."),
    "segment_from_text": ("partial", "Grounding DINO → SAM2 workflow đã cấu hình; chưa có smoke V3 được ghi nhận."),
    "track_video_object": ("partial", "SAM2 video tracking đã cấu hình; chưa có smoke V3 được ghi nhận."),
    "ocr_document": ("partial", "PaddleOCR-VL direct worker đã cấu hình; chưa có smoke V3 được ghi nhận."),
    "transcribe_media": ("partial", "Whisper direct worker đã cấu hình; chưa có smoke V3 được ghi nhận."),
    "create_subtitled_video": ("partial", "Workflow Whisper + FFmpeg đã cấu hình; chưa có smoke V3 được ghi nhận."),
    "text_to_speech": ("partial", "Qwen3-TTS direct worker đã cấu hình; chưa có smoke V3 được ghi nhận."),
    "design_voice": ("partial", "Qwen3-TTS Voice Design đã cấu hình; chưa có smoke V3 được ghi nhận."),
    "clone_voice": ("partial", "Qwen3-TTS Voice Clone đã cấu hình; chưa có smoke V3 được ghi nhận."),
    "convert_voice": ("partial", "Seed-VC direct worker đã cấu hình; chưa có smoke V3 được ghi nhận."),
    "upscale_anime_video": ("partial", "AnimeSR direct worker đã cấu hình; smoke bị hoãn nếu môi trường đang phục vụ job người dùng."),
    "probe_media": ("operational", "FFprobe canonical là thao tác đọc-only đã có smoke bounded."),
    "run_media_operation": ("partial", "FFmpeg allowlist đã cấu hình; mỗi thao tác ghi output cần smoke V3 riêng."),
    "generate_flux": ("partial", "FLUX workflow gọi trực tiếp ComfyUI API; chưa có smoke generation V3 được ghi nhận."),
    "generate_qwen_image": ("partial", "Qwen Image workflow gọi trực tiếp ComfyUI API; chưa có smoke generation V3 được ghi nhận."),
}

# Keep the UI's "Bước tiếp theo" contract alongside the truthful capability
# status.  These actions are guidance only; they never imply that an un-smoked
# backend is operational.
TOOL_ACTIONS = {
    "parse_screen": "Chọn một screenshot nhỏ rồi chạy bounded smoke khi tài nguyên sẵn sàng.",
    "detect_objects": "Chọn một ảnh nhỏ và xác minh detector trước khi dùng batch.",
    "ground_objects": "Nhập prompt ngắn, kiểm tra boxes rồi mới nối sang SAM2.",
    "segment_image": "Tải ảnh và kiểm tra mask trong Jobs; chưa có smoke thì giữ partial.",
    "segment_from_box": "Kéo box trên preview, sau đó kiểm tra mask artifact trong Jobs.",
    "segment_from_points": "Chọn điểm trên preview và kiểm tra mask artifact trong Jobs.",
    "segment_from_text": "Xác minh Grounding DINO trước khi chạy pipeline text → mask.",
    "track_video_object": "Chỉ chạy với clip ngắn sau khi resource override được gỡ.",
    "ocr_document": "Tải một ảnh/PDF nhỏ và kiểm tra text artifact trước khi chạy batch.",
    "transcribe_media": "Chọn media ngắn và kiểm tra transcript/SRT trước khi dịch hoặc burn.",
    "create_subtitled_video": "Xác minh transcript trước; video smoke hiện deferred do resource contention.",
    "text_to_speech": "Nhập một câu ngắn và kiểm tra audio artifact sau bounded smoke.",
    "design_voice": "Dùng sample ngắn, không đưa reference cá nhân vào log hoặc PR.",
    "clone_voice": "Chỉ dùng reference đã được phép và kiểm tra output local.",
    "convert_voice": "Kiểm tra source/target artifact ID trước khi queue.",
    "upscale_anime_video": "Video smoke hiện deferred do resource contention; giữ trạng thái partial.",
    "probe_media": "Đọc metadata là read-only; mở JSON result trong Jobs.",
    "run_media_operation": "Chọn operation allowlist và kiểm tra output artifact, không ghi đè source.",
    "generate_flux": "Chọn template Image AI; generation smoke hiện deferred nếu ComfyUI/GPU đang bận.",
    "generate_qwen_image": "Chọn ảnh input nếu edit; generation smoke hiện deferred nếu ComfyUI/GPU đang bận.",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path_exists(value: object) -> bool:
    if not isinstance(value, str) or not value or value.startswith("${"):
        return False
    try:
        return Path(os.path.expandvars(value)).exists()
    except OSError:
        return False


def _port_open(port: object, host: str = "127.0.0.1") -> bool:
    if not port:
        return False
    try:
        with socket.create_connection((host, int(port)), timeout=0.35):
            return True
    except OSError:
        return False


def _configured_status(item: dict[str, Any]) -> str:
    return str(item.get("component_status") or item.get("status") or "unknown").strip().lower()


def _safe_text(value: object, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    text = value.strip()
    lowered = text.casefold()
    if (
        not text
        or re.match(r"^[A-Za-z]:[\\/]", text)
        or text.startswith("\\\\")
        or lowered.startswith(("http://", "https://", "file:"))
        or any(marker in lowered for marker in ("secret", "bearer ", "cmd.exe", "powershell", "python -c"))
    ):
        return fallback
    return text


def _observed_status(item: dict[str, Any]) -> str:
    configured = _configured_status(item)
    if item.get("recovery_state") == "recovered_static" or (
        item.get("runtime_status") == "not_run" and item.get("execution") == "not_run"
    ):
        return "unavailable"
    if _port_open(item.get("port")):
        return "running"
    executable = item.get("executable")
    path = item.get("path")
    env = item.get("environment")
    executable_exists = _path_exists(executable)
    environment_exists = _path_exists(env)
    has_runtime = executable_exists or _path_exists(path)
    if configured == "not_installed" and not executable_exists and not environment_exists:
        return "not_installed"
    if configured == "planned" and not has_runtime:
        return "planned" if configured == "planned" else "not_installed"
    if has_runtime:
        if env and not _path_exists(env):
            return "partial"
        return "installed"
    if configured in {"external_system_app", "external_managed", "reused"}:
        return "partial"
    return "missing"


def component_statuses() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in components():
        observed = _observed_status(item)
        result.append({
            "id": item.get("id"),
            "name": item.get("name") or item.get("id"),
            "kind": item.get("kind") or "component",
            "version": item.get("version") or "unknown",
            "adapter": _safe_text(item.get("adapter"), "configured"),
            "source": _safe_text(item.get("source"), "local configuration"),
            "port": item.get("port"),
            "configured_component_status": _configured_status(item),
            "component_status": observed,
            "status": observed,
        })
    return result


def health(*, probe_gpu: bool = False) -> dict[str, Any]:
    import shutil

    config = hub_config()
    try:
        total, used, free = shutil.disk_usage(Path(config.get("output_root", BASE_DIR / "Output")))
        disk: dict[str, int] | None = {"free_bytes": free, "total_bytes": total, "used_bytes": used}
    except OSError:
        disk = None
    active = [item for item in list_jobs() if item.get("status") in {"queued", "starting", "running", "cancelling"}]
    return {
        "status": "healthy",
        "service": "Local AI Hub",
        "version": PRODUCT_VERSION,
        "time": _now(),
        "bind": f"{config.get('bind_host', '127.0.0.1')}:{config.get('api_port', 8765)}",
        "disk": disk,
        "gpu": query_gpu(probe=probe_gpu),
        "gpu_policy": gpu_policy(config),
        "active_jobs": len(active),
        "loaded_models": [],
    }


def capability_control_plane(*, hardware: dict[str, Any] | None = None, sources: dict[str, dict[str, Any]] | None = None, mode: str | None = None) -> dict[str, Any]:
    """Return a server-owned V5 capability registry and Module Manager preflight.

    This is a read-only composition boundary.  It does not accept a client
    manifest, execute a provider, download anything, or mutate a filesystem.
    """

    from src.services.module_manager import ModuleManager, build_capability_registry

    configuration = module_manager_config()
    registry = build_capability_registry(sources=sources)
    manager = ModuleManager(registry)
    configured_hardware = hardware if hardware is not None else configuration.get("hardware_snapshot")
    configured_mode = mode if mode in {"parallel", "serial"} else str(configuration.get("resource_mode", "parallel"))
    if configured_mode not in {"parallel", "serial"}:
        configured_mode = "parallel"
    plan = manager.preflight(hardware=configured_hardware, mode=configured_mode)
    return {
        "status": plan.get("status", registry.get("status", "unavailable")),
        "schema_version": "capability-control-plane.v1",
        "execution": "not_run",
        "dry_run": True,
        "registry": registry,
        "module_manager": plan,
        "runtime_evidence": runtime_evidence_projection(),
        "media_operation_scope": runtime_evidence_operation_scope(),
    }


def _tool_readiness(tool: str, statuses: dict[str, dict[str, Any]]) -> dict[str, Any]:
    component_id = TOOL_COMPONENTS[tool]
    component_item = statuses.get(component_id, {})
    component_status = str(component_item.get("component_status") or "missing")
    tool_status, reason = TOOL_CAPABILITIES[tool]
    operation_scope = runtime_evidence_operation_scope() if tool == "run_media_operation" else None
    if component_status not in READY_COMPONENT_STATUSES and tool_status not in {"operational"}:
        tool_status = "unavailable"
        reason = f"{component_item.get('name', component_id)} đang ở trạng thái {component_status}; worker không thể nhận job."
    elif tool_status == "partial" and tool in SMOKE_ELIGIBLE_TOOLS and smoke_passed(tool):
        tool_status = "operational"
        reason = "Đã có một direct job bounded hoàn tất trên máy này; trạng thái được lưu cục bộ, không chứa đường dẫn hoặc dữ liệu input."
    return {
        "component": component_id,
        "component_status": component_status,
        "tool_status": tool_status,
        "reason": reason,
        "operation_scope": operation_scope,
        "action": TOOL_ACTIONS.get(tool, "Kiểm tra trạng thái backend rồi thử lại trong Jobs."),
    }


def tool_catalog(component_items: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    statuses = {str(item["id"]): item for item in (component_items if component_items is not None else component_statuses()) if item.get("id")}
    tools = []
    for name, component_id in TOOL_COMPONENTS.items():
        readiness = _tool_readiness(name, statuses)
        item = {
            "name": name,
            "component": component_id,
            "component_status": readiness["component_status"],
            "tool_status": readiness["tool_status"],
            "status": readiness["tool_status"],
            "description": f"Allowlisted Local AI Hub workflow backed by {component_id}.",
            "reason": readiness["reason"],
            "action": readiness["action"],
        }
        if readiness.get("operation_scope") is not None:
            item["operation_scope"] = readiness["operation_scope"]
        tools.append(item)
    tools.extend([
        {"name": "get_health", "component": "local_ai_api", "component_status": "running", "tool_status": "operational", "status": "operational", "description": "Return Hub health.", "reason": "Loopback control-plane route.", "action": "Mở Dashboard để xem health, disk và job summary."},
        {"name": "list_models", "component": "local_ai_api", "component_status": "running", "tool_status": "operational", "status": "operational", "description": "Return safe model inventory.", "reason": "Loopback control-plane route.", "action": "Mở Models & Storage và quét lại khi cần."},
    ])
    return tools


def _unavailable(tool: str, readiness: dict[str, Any], reason: str | None = None) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "tool": tool,
        "component": readiness["component"],
        "component_status": readiness["component_status"],
        "tool_status": readiness["tool_status"],
        "reason": reason or readiness["reason"],
        "action": readiness.get("action") or TOOL_ACTIONS.get(tool, "Kiểm tra backend rồi thử lại."),
    }


def _opaque_media_request(tool: str, payload: dict[str, Any]) -> bool:
    """Keep GPU/video workers on opaque artifact identifiers end-to-end."""

    if tool == "upscale_anime_video":
        return True
    if tool != "run_media_operation":
        return False
    operation = str(payload.get("operation") or "")
    backend = str(payload.get("backend") or "")
    return operation in {"image_upscale", "frame_interpolate"}


def _resolve_assets(payload: dict[str, Any], *, tool: str = "") -> tuple[dict[str, Any], str | None]:
    # Public loopback requests use opaque artifact IDs.  Raw workstation paths
    # are never accepted from the browser/API surface; internal composition
    # between already-resolved workers happens below this boundary.
    raw_path_fields = {"path", "secondary_path", "reference_audio", "source", "target", "input_image", "input_paths"}
    if any(field in payload and payload[field] not in (None, "", []) for field in raw_path_fields):
        return dict(payload), "Dùng artifact ID do Hub tạo thay vì gửi đường dẫn cục bộ."
    value = dict(payload)
    if _opaque_media_request(tool, value):
        source_id = value.get("source_artifact_id")
        asset_id = value.get("asset_id")
        if source_id is not None and asset_id is not None and source_id != asset_id:
            return value, "Artifact input không hợp lệ."
        selected = source_id if source_id is not None else asset_id
        if not isinstance(selected, str) or not selected:
            return value, "Chọn artifact Hub hợp lệ trước khi chạy worker media."
        # Do not turn the opaque identifier back into a workstation path.  The
        # concrete adapter resolves type and containment server-side.
        value["source_artifact_id"] = selected
        value.pop("asset_id", None)
        return value, None
    fields = {
        "asset_id": "path",
        "input_asset_id": "path",
        "secondary_asset_id": "secondary_path",
        "reference_asset_id": "reference_audio",
        "source_asset_id": "source",
        "target_asset_id": "target",
        "input_image_asset_id": "input_image",
    }
    for artifact_field, target_field in fields.items():
        artifact_id = value.get(artifact_field)
        if artifact_id is None:
            continue
        if not isinstance(artifact_id, str):
            return value, "Artifact ID không hợp lệ."
        path = resolve(artifact_id)
        if path is None:
            return value, "Artifact Hub không còn tồn tại hoặc không thuộc vùng an toàn."
        value[target_field] = str(path)
    list_fields = {
        "input_asset_ids": "input_paths",
    }
    for artifact_field, target_field in list_fields.items():
        artifact_ids = value.get(artifact_field)
        if artifact_ids is None:
            continue
        if not isinstance(artifact_ids, list) or not artifact_ids or not all(isinstance(item, str) for item in artifact_ids):
            return value, "Danh sách artifact Hub không hợp lệ."
        paths = [resolve(item) for item in artifact_ids]
        if any(path is None for path in paths):
            return value, "Một hoặc nhiều artifact Hub không còn tồn tại hoặc không thuộc vùng an toàn."
        value[target_field] = [str(path) for path in paths if path is not None]
    return value, None


def _run_operation(tool: str, payload: dict[str, Any], context: JobContext | None = None) -> dict[str, Any]:
    if tool == "parse_screen":
        from src.modules.vision.backend.omniparser_adapter import parse

        return parse(str(payload.get("path", "")), float(payload.get("box_threshold", 0.05)), context)
    if tool == "detect_objects":
        from src.modules.vision.backend.rfdetr_adapter import detect

        return detect(str(payload.get("path", "")), float(payload.get("threshold", 0.5)), context)
    if tool == "ground_objects":
        from src.modules.vision.backend.groundingdino_adapter import ground

        return ground(str(payload.get("path", "")), str(payload.get("prompt", "")), float(payload.get("box_threshold", 0.35)), float(payload.get("text_threshold", 0.25)), context)
    if tool == "segment_from_text":
        from src.modules.sam2.backend import adapter as sam2
        from src.modules.vision.backend.groundingdino_adapter import ground

        grounded = ground(
            str(payload.get("path", "")),
            str(payload.get("prompt", "")),
            float(payload.get("box_threshold", 0.35)),
            float(payload.get("text_threshold", 0.25)),
            context,
        )
        if grounded.get("status") != "completed":
            return grounded
        candidates = grounded.get("grounded")
        if not isinstance(candidates, list) or not candidates:
            return {"status": "error", "error": "Grounding DINO không trả box nào cho prompt này."}
        raw_box = candidates[0].get("box_normalized_cxcywh") if isinstance(candidates[0], dict) else None
        if not isinstance(raw_box, list) or len(raw_box) != 4:
            return {"status": "error", "error": "Grounding DINO không trả normalized box hợp lệ."}
        cx, cy, width, height = [float(item) for item in raw_box]
        return sam2.segment_from_box({**payload, "box": [cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2], "normalized_box": True}, context)
    if tool in {"segment_image", "segment_from_box", "segment_from_points", "track_video_object"}:
        from src.modules.sam2.backend import adapter as sam2

        method = {
            "segment_image": sam2.segment_image,
            "segment_from_box": sam2.segment_from_box,
            "segment_from_points": sam2.segment_from_points,
            "track_video_object": sam2.track_video,
        }[tool]
        return method(payload, context)
    if tool == "ocr_document":
        from src.modules.ocr.backend.adapter import parse

        return parse(str(payload.get("path", "")), context)
    if tool == "transcribe_media":
        from src.modules.whisper.backend.adapter import transcribe

        return transcribe(payload, context)
    if tool == "create_subtitled_video":
        from src.modules.whisper.backend.adapter import transcribe
        from src.modules.media_editor.backend.adapter import run_operation

        transcript = transcribe(payload, context)
        if transcript.get("status") != "completed":
            return transcript
        srt = transcript.get("srt")
        if not isinstance(srt, str) or not Path(srt).is_file():
            return {"status": "error", "error": "Whisper không tạo SRT để burn subtitle."}
        return run_operation({"operation": "burn_subtitle", "path": payload.get("path"), "secondary_path": srt}, context)
    if tool in {"text_to_speech", "design_voice", "clone_voice"}:
        from src.modules.voice.backend.qwen3_tts_adapter import synthesize

        request = dict(payload)
        request["operation"] = tool
        return synthesize(request, context)
    if tool == "convert_voice":
        from src.modules.voice.backend.seed_vc_adapter import convert

        return convert(payload, context)
    if tool == "upscale_anime_video":
        from src.modules.animesr.backend.adapter import run_animesr

        return run_animesr(payload, context)
    if tool in {"probe_media", "run_media_operation"}:
        from src.modules.media_editor.backend.adapter import run_operation

        request = dict(payload)
        request.setdefault("operation", "probe" if tool == "probe_media" else "transcode")
        return run_operation(request, context)
    if tool in {"generate_flux", "generate_qwen_image"}:
        from src.modules.image_generation.backend.comfyui import generate

        return generate("flux" if tool == "generate_flux" else "qwen", payload, context)
    return {"status": "error", "error": "Tool Hub không được allowlist."}


def submit_tool(tool: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    component_id = TOOL_COMPONENTS.get(tool)
    if component_id is None:
        return 404, {"status": "error", "error": "Tool Hub không được allowlist."}
    statuses = {str(item["id"]): item for item in component_statuses() if item.get("id")}
    readiness = _tool_readiness(tool, statuses)
    if readiness["tool_status"] in {"unavailable", "planned", "error"}:
        return 503, _unavailable(tool, readiness)
    request, error = _resolve_assets(payload, tool=tool)
    if error:
        return 400, {"status": "error", "error": error}
    with _submission_gate:
        if _submissions_quiesced:
            return _submission_closed_payload()
        record = job_manager.submit(tool, request, lambda item, context: _run_operation(tool, item, context), device="gpu" if tool not in {"probe_media", "run_media_operation"} else None, heavy=tool != "probe_media")
    return 202, {"status": "queued", "job": get_job(record["id"])}


def submit_graph(graph: object, *, draft: bool = False) -> tuple[int, dict[str, Any]]:
    """Queue an owned Node Studio DAG without exposing any local path to the UI."""

    from src.services.node_studio.engine import execute_graph
    from src.services.node_studio.registry import graph_has_heavy_nodes
    from src.services.node_studio.schema import validate_graph
    from src.services.node_studio.state import graph_runs

    validation = validate_graph(graph, require_runnable=True)
    if not validation["valid"]:
        return 400, {"status": "error", "error": "Graph không hợp lệ.", "validation": {"errors": validation["errors"]}}
    normalized = validation["graph"]
    heavy = graph_has_heavy_nodes(normalized)
    request = {"graph": normalized, "draft": bool(draft)}
    with _submission_gate:
        if _submissions_quiesced:
            return _submission_closed_payload()
        record = job_manager.submit(
            "node_graph",
            request,
            lambda item, context: execute_graph(item["graph"], context, _run_operation, draft=bool(item.get("draft"))),
            device="gpu" if heavy else None,
            heavy=heavy,
        )
        graph_runs.begin(record["id"], normalized)
    return 202, {
        "status": "queued",
        "contract_version": "node-run.v2",
        "graph_id": normalized.get("id"),
        "heavy": heavy,
        "job": get_job(record["id"]),
        "validation": {"order": validation["order"]},
    }


def dispatch_tool(tool: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Compatibility endpoint: submit a real Hub job rather than a fake result."""

    return submit_tool(tool, payload)


def get_job_or_error(job_id: str) -> tuple[int, dict[str, Any]]:
    record = get_job(job_id)
    if record is None:
        return 404, {"status": "error", "error": "Không tìm thấy job Hub."}
    return 200, record
