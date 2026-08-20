"""Truthful control plane and direct-workflow dispatch for Local AI Hub V3."""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.services.artifact_store import resolve
from src.shared.version import PRODUCT_VERSION
from src.services.job_manager.manager import JobContext, job_manager
from src.services.tool_smoke import (
    completion_receipts,
    passed as smoke_passed,
    record_unavailable,
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
    "probe_media",
    "generate_flux",
    "generate_qwen_image",
}

# A component may expose that its leaves are present without promoting the
# corresponding tool.  Operational is reserved for one recent, bounded smoke
# record that names the tool and matches the current non-sensitive runtime
# fingerprint.  The fingerprint only represents boolean leaf observations;
# it never includes workstation paths, commands, models, or input/output data.
COMPONENT_RUNTIME_EVIDENCE_SCHEMA = "component-runtime-evidence.v1"
COMPONENT_RUNTIME_EVIDENCE_MAX_AGE = timedelta(days=1)
_COMPONENT_RUNTIME_EVIDENCE_KEYS = frozenset({
    "schema_version",
    "tool",
    "outcome",
    "execution",
    "recorded_at",
    "runtime_fingerprint",
})

SMOKE_FAILURE_REASON = "Lần smoke bounded gần nhất thất bại hoặc không tạo artifact; evidence operational cũ đã bị vô hiệu hóa."
SMOKE_UNAVAILABLE_REASON = "Lần smoke bounded gần nhất không khả dụng; evidence operational cũ đã bị vô hiệu hóa."
SMOKE_FAILURE_ACTION = "Kiểm tra backend và artifact output, sau đó chạy lại một smoke bounded."

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

# ``run_media_operation`` is intentionally generic at the control-plane level,
# but selected video backends have stronger static prerequisites than FFmpeg
# alone.  These checks inspect only server-owned registry/runtime leaves; they
# do not execute a worker or promote a backend to operational.
_MEDIA_BACKEND_CONTRACTS = {
    "animesr": {
        "operation": "upscale_anime_video",
        "component": "animesr",
        "label": "AnimeSR",
        "required": ("runtime_ready", "environment_ready", "model_ready", "script_ready", "ffmpeg_ready", "worker_ready"),
        "action": "Khôi phục registry model, environment, runtime, fixed AnimeSR inference script và FFmpeg canonical rồi chạy smoke clip ngắn.",
    },
    "practical_rife": {
        "operation": "frame_interpolate",
        "component": "practical_rife",
        "label": "Practical-RIFE",
        "required": ("runtime_ready", "environment_ready", "script_ready", "ffmpeg_ready", "worker_ready"),
        "action": "Khôi phục đủ runtime, environment, model, fixed Practical-RIFE inference script và cặp FFmpeg/FFprobe rồi chạy smoke clip ngắn.",
    },
    "real_esrgan": {
        "operation": "image_upscale",
        "component": "real_esrgan",
        "label": "Real-ESRGAN",
        "required": ("runtime_ready", "environment_ready", "model_ready", "script_ready", "worker_ready"),
        "action": "Khôi phục environment, script và model Real-ESRGAN rồi chạy smoke một ảnh nhỏ.",
    },
}


def _media_backend_status(backend: str) -> dict[str, Any]:
    contract = _MEDIA_BACKEND_CONTRACTS[backend]
    try:
        if backend == "animesr":
            from src.modules.animesr.backend.adapter import capability
        elif backend == "practical_rife":
            from src.modules.practical_rife.backend.adapter import capability
        else:
            from src.modules.real_esrgan.backend.adapter import capability
        observed = capability()
    except Exception:
        observed = {}
    static_ready = isinstance(observed, dict) and all(observed.get(key) is True for key in contract["required"])
    return {
        "backend": backend,
        "component": contract["component"],
        "component_status": "partial" if static_ready else "missing",
        "tool_status": "partial" if static_ready else "unavailable",
        "status": "partial" if static_ready else "unavailable",
        "queue_allowed": static_ready,
        "reason": (
            f"{contract['label']} có đủ leaf tĩnh; vẫn cần bounded smoke trước khi ghi operational."
            if static_ready
            else f"{contract['label']} thiếu runtime, environment, model/script, worker hoặc dependency bắt buộc; không xếp hàng job."
        ),
        "action": contract["action"],
    }


def _media_backend_readiness(*, operation: str | None = None, backend: str | None = None) -> dict[str, Any] | None:
    """Return static backend readiness, or the selected backend gate."""

    if operation is not None or backend is not None:
        contract = _MEDIA_BACKEND_CONTRACTS.get(str(backend or ""))
        if contract is None or contract["operation"] != str(operation or ""):
            return None
        return _media_backend_status(str(backend))
    return {name: _media_backend_status(name) for name in _MEDIA_BACKEND_CONTRACTS}


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


def _runtime_observation(item: dict[str, Any]) -> dict[str, bool]:
    """Read only the component's local leaf presence without projecting paths."""

    return {
        "port_open": _port_open(item.get("port")),
        "executable_present": _path_exists(item.get("executable")),
        "path_present": _path_exists(item.get("path")),
        "environment_present": _path_exists(item.get("environment")),
        "model_required": isinstance(item.get("model"), str) and bool(str(item.get("model")).strip()),
        "model_present": _path_exists(item.get("model")),
    }


def _runtime_fingerprint(item: dict[str, Any], observation: dict[str, bool]) -> str:
    """Return a stable, path-free digest of the current component leaves."""

    payload = {
        "id": str(item.get("id") or "")[:128],
        "configured_status": _configured_status(item),
        "recovered_static": item.get("recovery_state") == "recovered_static",
        "observation": {
            key: bool(observation.get(key))
            for key in ("port_open", "executable_present", "path_present", "environment_present", "model_required", "model_present")
        },
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def runtime_evidence_binding(tool: str) -> dict[str, str] | None:
    """Return the current path-free component binding for one tool.

    Job completion calls this read-only observation after the worker returns.
    It repeats the same local Config/leaf observation used by the component
    projection, but never probes a provider or executes a runtime.
    """

    component_id = TOOL_COMPONENTS.get(tool)
    if not component_id:
        return None
    for item in components():
        if not isinstance(item, dict) or str(item.get("id") or "") != component_id:
            continue
        observation = _runtime_observation(item)
        fingerprint = _runtime_fingerprint(item, observation)
        if re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None:
            return None
        return {"component": component_id, "runtime_fingerprint": fingerprint}
    return None


def _observed_status(item: dict[str, Any], observation: dict[str, bool] | None = None) -> str:
    """Project local leaf presence without promoting a configured row to a smoke."""

    configured = _configured_status(item)
    if item.get("recovery_state") == "recovered_static":
        return "unavailable"
    current = observation if observation is not None else _runtime_observation(item)
    if current["port_open"]:
        return "running"
    executable_exists = current["executable_present"]
    environment_exists = current["environment_present"]
    has_runtime = executable_exists or current["path_present"]
    if configured == "not_installed":
        return "not_installed"
    if configured == "planned" and not has_runtime:
        return "planned"
    if current.get("model_required") and not current.get("model_present"):
        return "partial" if has_runtime or environment_exists else "missing"
    if has_runtime:
        if item.get("environment") and not environment_exists:
            return "partial"
        return "installed"
    if environment_exists:
        return "partial"
    if configured in {"external_system_app", "external_managed", "reused"}:
        return "partial"
    return "missing"


def _parsed_smoke_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _last_smoke_projection(
    item: dict[str, Any],
    runtime_fingerprint: str,
    *,
    component_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fail closed unless a bounded smoke names this exact current runtime."""

    expected_component = component_id or str(item.get("id") or "")
    item_evidence = item.get("last_smoke")
    receipt_candidates: list[dict[str, Any]] = []
    for tool, receipt in completion_receipts().items():
        if not isinstance(receipt, dict) or receipt.get("component") != expected_component:
            continue
        if TOOL_COMPONENTS.get(tool) != expected_component or receipt.get("tool") != tool:
            continue
        receipt_candidates.append(receipt)
    # Server-owned local receipts represent the latest direct-job attempt and
    # therefore supersede any serialized/config-provided last_smoke snapshot,
    # even if a clock or stale snapshot would sort it differently.
    if receipt_candidates:
        evidence = max(receipt_candidates, key=lambda value: str(value.get("recorded_at") or ""))
    else:
        evidence = item_evidence if isinstance(item_evidence, dict) else None
    fallback = {
        "status": "not_run",
        "execution": "not_run",
        "fresh": False,
        "runtime_fingerprint_match": False,
    }
    if not isinstance(evidence, dict):
        return fallback
    if evidence is item_evidence and set(item_evidence) != _COMPONENT_RUNTIME_EVIDENCE_KEYS:
        return fallback
    if evidence.get("component") not in {None, expected_component}:
        return fallback
    normalized = {key: evidence.get(key) for key in _COMPONENT_RUNTIME_EVIDENCE_KEYS}
    if (
        normalized.get("schema_version") != COMPONENT_RUNTIME_EVIDENCE_SCHEMA
        or not isinstance(normalized.get("tool"), str)
        or normalized.get("tool") not in TOOL_COMPONENTS
        or TOOL_COMPONENTS.get(normalized["tool"]) != expected_component
        or normalized.get("outcome") not in {"completed", "failed", "unavailable"}
        or normalized.get("execution") not in {"completed", "attempted", "not_run"}
        or (normalized.get("outcome") == "completed" and normalized.get("execution") != "completed")
        or (normalized.get("outcome") == "failed" and normalized.get("execution") != "attempted")
        or (normalized.get("outcome") == "unavailable" and normalized.get("execution") not in {"attempted", "not_run"})
        or not isinstance(normalized.get("runtime_fingerprint"), str)
        or re.fullmatch(r"[0-9a-f]{64}", normalized["runtime_fingerprint"]) is None
    ):
        return fallback
    recorded_at = _parsed_smoke_time(normalized.get("recorded_at"))
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    fresh = bool(
        recorded_at is not None
        and timedelta(0) <= reference - recorded_at <= COMPONENT_RUNTIME_EVIDENCE_MAX_AGE
    )
    fingerprint_matches = normalized["runtime_fingerprint"] == runtime_fingerprint
    projection = {
        "status": normalized["outcome"],
        "execution": normalized["execution"],
        "fresh": fresh,
        "runtime_fingerprint_match": fingerprint_matches,
        "tool": normalized["tool"],
    }
    if normalized["outcome"] == "failed":
        projection.update({"reason": SMOKE_FAILURE_REASON, "next_action": SMOKE_FAILURE_ACTION})
    elif normalized["outcome"] == "unavailable":
        projection.update({"reason": SMOKE_UNAVAILABLE_REASON, "next_action": SMOKE_FAILURE_ACTION})
    return projection


def _smoke_is_current_for_tool(item: dict[str, Any], tool: str) -> bool:
    smoke = item.get("last_smoke")
    return bool(
        isinstance(smoke, dict)
        and smoke.get("tool") == tool
        and smoke.get("status") == "completed"
        and smoke.get("execution") == "completed"
        and smoke.get("fresh") is True
        and smoke.get("runtime_fingerprint_match") is True
        and smoke_passed(tool)
    )


def component_statuses() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in components():
        observation = _runtime_observation(item)
        observed = _observed_status(item, observation)
        fingerprint = _runtime_fingerprint(item, observation)
        last_smoke = _last_smoke_projection(item, fingerprint, component_id=str(item.get("id") or ""))
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
            "current_readiness": observed,
            "runtime_fingerprint": fingerprint,
            "last_smoke": last_smoke,
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
    if component_status not in READY_COMPONENT_STATUSES:
        tool_status = "unavailable"
        reason = f"{component_item.get('name', component_id)} đang ở trạng thái {component_status}; worker không thể nhận job."
    elif tool in SMOKE_ELIGIBLE_TOOLS:
        smoke = component_item.get("last_smoke")
        smoke_status = smoke.get("status") if isinstance(smoke, dict) else None
        if _smoke_is_current_for_tool(component_item, tool):
            tool_status = "operational"
            reason = "Một bounded smoke gần đây khớp runtime hiện tại đã hoàn tất; evidence cục bộ không chứa đường dẫn hoặc dữ liệu input."
        elif smoke_status == "failed":
            if tool_status in {"operational", "partial"}:
                tool_status = "partial"
            reason = SMOKE_FAILURE_REASON
        elif smoke_status == "unavailable":
            if tool_status in {"operational", "partial"}:
                tool_status = "partial"
            reason = SMOKE_UNAVAILABLE_REASON
        elif tool_status == "operational":
            tool_status = "partial"
            reason = "Runtime hiện tại cần một bounded smoke mới khớp runtime fingerprint trước khi tool được xem là operational."
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
        if name == "run_media_operation":
            item["backend_readiness"] = _media_backend_readiness()
        elif name == "upscale_anime_video":
            item["backend_readiness"] = {"animesr": _media_backend_readiness(operation="upscale_anime_video", backend="animesr")}
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

    if tool in {
        "parse_screen",
        "detect_objects",
        "ground_objects",
        "segment_from_text",
        "ocr_document",
        "text_to_speech",
        "design_voice",
        "clone_voice",
        "convert_voice",
    }:
        return True
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
    raw_path_fields = {
        "path",
        "secondary_path",
        "reference_audio",
        "source",
        "target",
        "input_image",
        "input_paths",
        "output",
        "outputs",
        "files",
        "command",
        "executable",
        "runtime",
        "model",
        "model_id",
    }
    if any(field in payload and payload[field] not in (None, "", []) for field in raw_path_fields):
        return dict(payload), "Dùng artifact ID do Hub tạo thay vì gửi đường dẫn cục bộ."
    value = dict(payload)
    if _opaque_media_request(tool, value):
        if tool in {"text_to_speech", "design_voice"}:
            return value, None
        if tool == "clone_voice":
            selected = value.get("reference_asset_id")
            if selected is not None and (not isinstance(selected, str) or not selected):
                return value, "Artifact input không hợp lệ."
            return value, None
        if tool == "convert_voice":
            if not all(isinstance(value.get(field), str) and value.get(field) for field in ("source_asset_id", "target_asset_id")):
                return value, "Chọn source và target artifact Hub hợp lệ trước khi chạy worker."
            return value, None
        source_id = value.get("source_artifact_id")
        asset_id = value.get("asset_id")
        if source_id is not None and asset_id is not None and source_id != asset_id:
            return value, "Artifact input không hợp lệ."
        selected = source_id if source_id is not None else asset_id
        if not isinstance(selected, str) or not selected:
            return value, "Chọn artifact Hub hợp lệ trước khi chạy worker media."
        # Do not turn the opaque identifier back into a workstation path. The
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

        return parse(payload, context)
    if tool == "detect_objects":
        from src.modules.vision.backend.rfdetr_adapter import detect

        return detect(payload, context)
    if tool == "ground_objects":
        from src.modules.vision.backend.groundingdino_adapter import ground

        return ground(payload, context)
    if tool == "segment_from_text":
        from src.modules.sam2.backend import adapter as sam2
        from src.modules.vision.backend.groundingdino_adapter import ground

        grounded = ground(payload, context)
        if grounded.get("status") != "completed":
            return grounded
        candidates = grounded.get("grounded")
        if not isinstance(candidates, list) or not candidates:
            return {"status": "error", "error": "Grounding DINO không trả box nào cho prompt này."}
        raw_box = candidates[0].get("box_normalized_cxcywh") if isinstance(candidates[0], dict) else None
        if not isinstance(raw_box, list) or len(raw_box) != 4:
            return {"status": "error", "error": "Grounding DINO không trả normalized box hợp lệ."}
        cx, cy, width, height = [float(item) for item in raw_box]
        # Keep the opaque artifact ID through the composed Grounding DINO →
        # SAM2 hand-off.  The SAM2 adapter resolves and type-checks it inside
        # its server-owned boundary; no local input path reaches the worker.
        return sam2.segment_from_box(
            {
                **payload,
                "box": [cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2],
                "normalized_box": True,
            },
            context,
        )
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

        return parse(payload, context)
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
        if tool in SMOKE_ELIGIBLE_TOOLS:
            record_unavailable(tool, failure_code="PREFLIGHT_UNAVAILABLE", execution="not_run")
        return 503, _unavailable(tool, readiness)
    if tool == "run_media_operation" and isinstance(payload, dict):
        backend_readiness = _media_backend_readiness(
            operation=str(payload.get("operation") or ""),
            backend=str(payload.get("backend") or ""),
        )
        if backend_readiness is not None and not backend_readiness["queue_allowed"]:
            if tool in SMOKE_ELIGIBLE_TOOLS:
                record_unavailable(tool, failure_code="PREFLIGHT_UNAVAILABLE", execution="not_run")
            return 503, {
                "status": "unavailable",
                "tool": tool,
                "backend": backend_readiness["backend"],
                "component": backend_readiness["component"],
                "component_status": backend_readiness["component_status"],
                "tool_status": backend_readiness["tool_status"],
                "reason": backend_readiness["reason"],
                "action": backend_readiness["action"],
            }
    if tool == "upscale_anime_video":
        backend_readiness = _media_backend_readiness(operation="upscale_anime_video", backend="animesr")
        if backend_readiness is not None and not backend_readiness["queue_allowed"]:
            record_unavailable(tool, failure_code="PREFLIGHT_UNAVAILABLE", execution="not_run")
            return 503, {
                "status": "unavailable",
                "tool": tool,
                "backend": backend_readiness["backend"],
                "component": backend_readiness["component"],
                "component_status": backend_readiness["component_status"],
                "tool_status": backend_readiness["tool_status"],
                "reason": backend_readiness["reason"],
                "action": backend_readiness["action"],
            }
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
