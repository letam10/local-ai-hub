from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import BASE_DIR, component, components, hub_config, models
from .gpu import gpu_policy, query_gpu
from .jobs import create_job, get_job, list_jobs


TOOL_COMPONENTS = {
    "parse_screen": "omniparser",
    "detect_objects": "rfdetr",
    "ground_objects": "groundingdino",
    "segment_image": "sam2",
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
}

TOOL_STATUS_VALUES = {
    "operational",
    "partial",
    "queue_only",
    "unavailable",
    "planned",
    "error",
}

READY_COMPONENT_STATUSES = {"installed", "running"}

TOOL_CAPABILITIES = {
    "parse_screen": {
        "tool_status": "partial",
        "reason": "The OmniParser adapter exists, but no bounded functional backend smoke test is recorded.",
    },
    "detect_objects": {
        "tool_status": "partial",
        "reason": "The RF-DETR adapter exists, but no bounded functional backend smoke test is recorded.",
    },
    "ground_objects": {
        "tool_status": "partial",
        "reason": "The Grounding DINO adapter exists, but no bounded functional backend smoke test is recorded.",
    },
    "segment_image": {
        "tool_status": "unavailable",
        "reason": "Direct SAM 2 backend adapter has not been verified; the existing application is GUI-only.",
    },
    "track_video_object": {
        "tool_status": "unavailable",
        "reason": "Direct SAM 2 backend adapter has not been verified; the existing application is GUI-only.",
    },
    "ocr_document": {
        "tool_status": "partial",
        "reason": "The PaddleOCR-VL adapter exists, but no bounded functional backend smoke test is recorded.",
    },
    "transcribe_media": {
        "tool_status": "partial",
        "reason": "The Faster-Whisper adapter exists, but no bounded functional backend smoke test is recorded.",
    },
    "create_subtitled_video": {
        "tool_status": "unavailable",
        "reason": "Subtitle-video output muxing has not been verified.",
    },
    "text_to_speech": {
        "tool_status": "partial",
        "reason": "The Qwen3-TTS adapter exists, but no bounded functional backend smoke test is recorded.",
    },
    "design_voice": {
        "tool_status": "partial",
        "reason": "The Qwen3-TTS adapter exists, but no bounded functional backend smoke test is recorded.",
    },
    "clone_voice": {
        "tool_status": "partial",
        "reason": "The Qwen3-TTS adapter exists, but no bounded functional backend smoke test is recorded.",
    },
    "convert_voice": {
        "tool_status": "partial",
        "reason": "The Seed-VC adapter exists, but no bounded functional backend smoke test is recorded.",
    },
    "upscale_anime_video": {
        "tool_status": "queue_only",
        "reason": "The route records a job, but no AnimeSR executor has been verified.",
    },
    "probe_media": {
        "tool_status": "partial",
        "reason": "The allowlisted FFprobe route is implemented, but a local functional smoke result is not recorded.",
    },
}

HEAVY_GPU_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path_exists(value: str | None) -> bool:
    return bool(value) and Path(os.path.expandvars(value)).exists()


def _port_open(port: int | None, host: str = "127.0.0.1") -> bool:
    if not port:
        return False
    try:
        with socket.create_connection((host, int(port)), timeout=0.35):
            return True
    except OSError:
        return False


def _configured_component_status(item: dict[str, Any]) -> str:
    value = item.get("component_status") or item.get("status") or "unknown"
    return str(value).strip().lower()


def _observed_component_status(item: dict[str, Any]) -> str:
    executable = item.get("executable")
    path = item.get("path")
    if _port_open(item.get("port")):
        return "running"
    if (executable and _path_exists(executable)) or (path and _path_exists(path)):
        return "installed"
    configured_status = _configured_component_status(item)
    if configured_status in {"planned", "not_installed"}:
        return "planned"
    if configured_status == "error":
        return "error"
    return "missing"


def component_statuses() -> list[dict[str, Any]]:
    statuses: list[dict[str, Any]] = []
    for item in components():
        observed = _observed_component_status(item)
        statuses.append({
            **item,
            "configured_component_status": _configured_component_status(item),
            "component_status": observed,
            "status": observed,
            "observed_status": observed,
        })
    return statuses


def health() -> dict[str, Any]:
    config = hub_config()
    output_root = Path(config.get("output_root", BASE_DIR / "Output"))
    usage = None
    try:
        disk = os.statvfs(output_root)  # type: ignore[attr-defined]
        usage = {"free_bytes": disk.f_bavail * disk.f_frsize, "total_bytes": disk.f_blocks * disk.f_frsize}
    except (AttributeError, OSError):
        try:
            import shutil

            total, used, free = shutil.disk_usage(output_root)
            usage = {"free_bytes": free, "total_bytes": total, "used_bytes": used}
        except OSError:
            usage = None
    return {
        "status": "healthy",
        "service": "Local AI Hub",
        "version": "0.1.0",
        "time": _now(),
        "bind": f"{config.get('bind_host', '127.0.0.1')}:{config.get('api_port', 8765)}",
        "disk": usage,
        "gpu": query_gpu(),
        "gpu_policy": gpu_policy(config),
        "active_api_heavy_requests": int(HEAVY_GPU_LOCK.locked()),
        "active_jobs": len([item for item in list_jobs() if item.get("status") in {"starting", "running"}]),
        "loaded_models": [],
    }


def _tool_readiness(tool: str, statuses: dict[str, dict[str, Any]]) -> dict[str, Any]:
    component_id = TOOL_COMPONENTS[tool]
    component_item = statuses.get(component_id, {})
    component_status = component_item.get("component_status", "missing")
    capability = TOOL_CAPABILITIES[tool]
    tool_status = capability["tool_status"]
    reason = capability["reason"]

    if tool_status not in TOOL_STATUS_VALUES:
        return {
            "component": component_id,
            "component_status": component_status,
            "tool_status": "error",
            "reason": f"Unsupported configured tool status: {tool_status}.",
        }
    if tool_status not in {"unavailable", "planned", "error"} and component_status not in READY_COMPONENT_STATUSES:
        tool_status = "unavailable"
        reason = f"{component_item.get('name', component_id)} component is {component_status}; the local backend cannot accept calls."
    return {
        "component": component_id,
        "component_status": component_status,
        "tool_status": tool_status,
        "reason": reason,
    }


def tool_catalog() -> list[dict[str, Any]]:
    statuses = {item["id"]: item for item in component_statuses() if item.get("id")}
    tools = []
    for name, component_id in TOOL_COMPONENTS.items():
        item = statuses.get(component_id, {})
        readiness = _tool_readiness(name, statuses)
        tools.append({
            "name": name,
            "component": component_id,
            "component_status": readiness["component_status"],
            "tool_status": readiness["tool_status"],
            "status": readiness["tool_status"],
            "description": f"Allowlisted Local AI Hub tool backed by {item.get('name', component_id)}.",
            "reason": readiness["reason"],
        })
    tools.extend([
        {
            "name": "get_health",
            "component": "local_ai_api",
            "component_status": "running",
            "tool_status": "operational",
            "status": "operational",
            "description": "Return Hub health and GPU policy.",
            "reason": "Control-plane route served by the running Hub API.",
        },
        {
            "name": "list_models",
            "component": "local_ai_api",
            "component_status": "running",
            "tool_status": "operational",
            "status": "operational",
            "description": "Return the model registry.",
            "reason": "Control-plane route served by the running Hub API.",
        },
    ])
    return tools


def probe_media(input_path: str) -> dict[str, Any]:
    config = hub_config()
    source = Path(os.path.expandvars(input_path)).expanduser()
    if not source.exists() or not source.is_file():
        return {"status": "error", "error": f"Input file does not exist: {source}"}
    ffprobe = Path(config.get("ffprobe_path", ""))
    if not ffprobe.exists():
        return {"status": "error", "error": f"Configured ffprobe does not exist: {ffprobe}"}
    command = [str(ffprobe), "-v", "error", "-show_format", "-show_streams", "-of", "json", str(source)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "error", "error": str(exc)}
    if result.returncode != 0:
        return {"status": "error", "error": result.stderr.strip() or "ffprobe failed", "returncode": result.returncode}
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {"status": "error", "error": f"ffprobe returned invalid JSON: {exc}"}
    streams = data.get("streams", [])
    return {
        "status": "completed",
        "path": str(source),
        "format": data.get("format", {}),
        "streams": streams,
        "stream_count": len(streams),
    }


def _unavailable(
    tool: str,
    component_id: str,
    reason: str | None = None,
    readiness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item = component(component_id) or {}
    if readiness is None:
        statuses = {entry["id"]: entry for entry in component_statuses() if entry.get("id")}
        readiness = _tool_readiness(tool, statuses)
    return {
        "status": "unavailable",
        "tool": tool,
        "component": component_id,
        "component_status": readiness["component_status"],
        "tool_status": readiness["tool_status"],
        "reason": reason or readiness["reason"] or f"{item.get('name', component_id)} is not ready for a backend call.",
    }


def _run_heavy(tool: str, operation: Any) -> tuple[int, dict[str, Any]]:
    config = hub_config()
    if int(config.get("max_heavy_gpu_jobs", 1)) < 1:
        return 503, {"status": "unavailable", "tool": tool, "reason": "Heavy GPU execution is disabled by configuration."}
    if not HEAVY_GPU_LOCK.acquire(blocking=False):
        return 409, {"status": "busy", "tool": tool, "reason": "The single heavy-GPU slot is occupied; retry after the current request completes."}
    try:
        result = operation()
        return (200 if result.get("status") == "completed" else 400), result
    finally:
        HEAVY_GPU_LOCK.release()


def dispatch_tool(tool: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    component_id = TOOL_COMPONENTS.get(tool)
    if component_id is None:
        return 404, {"status": "error", "error": f"Unknown allowlisted tool: {tool}"}

    statuses = {entry["id"]: entry for entry in component_statuses() if entry.get("id")}
    readiness = _tool_readiness(tool, statuses)
    if readiness["tool_status"] in {"unavailable", "planned", "error"}:
        return 503, _unavailable(tool, component_id, readiness=readiness)

    if tool == "probe_media":
        path = payload.get("path")
        if not isinstance(path, str) or not path:
            return 400, {"status": "error", "error": "Expected JSON field 'path'."}
        result = probe_media(path)
        return (200 if result.get("status") == "completed" else 400), result
    if tool == "detect_objects":
        from Adapters.rfdetr_adapter import detect

        return _run_heavy(tool, lambda: detect(str(payload.get("path", "")), float(payload.get("threshold", 0.5))))
    if tool == "ground_objects":
        from Adapters.groundingdino_adapter import ground

        prompt = str(payload.get("prompt", "person . object ."))
        return _run_heavy(tool, lambda: ground(str(payload.get("path", "")), prompt, float(payload.get("box_threshold", 0.35)), float(payload.get("text_threshold", 0.25))))
    if tool == "parse_screen":
        from Adapters.omniparser_adapter import parse

        return _run_heavy(tool, lambda: parse(str(payload.get("path", "")), float(payload.get("box_threshold", 0.05))))
    if tool == "ocr_document":
        from Adapters.paddleocr_adapter import parse

        return _run_heavy(tool, lambda: parse(str(payload.get("path", ""))))
    if tool == "text_to_speech":
        from Adapters.qwen3_tts_adapter import synthesize

        return _run_heavy(tool, lambda: synthesize(payload))
    if tool in {"design_voice", "clone_voice"}:
        from Adapters.qwen3_tts_adapter import synthesize

        request = {**payload, "operation": tool}
        return _run_heavy(tool, lambda: synthesize(request))
    if tool == "convert_voice":
        from Adapters.seed_vc_adapter import convert

        return _run_heavy(tool, lambda: convert(payload))
    if tool == "upscale_anime_video":
        job = create_job(tool, payload, device="cuda:0")
        return 202, {"status": "queued", "job_id": job["id"], "note": "Existing AnimeSR application is preserved; execution adapter is being connected."}
    if tool == "transcribe_media":
        from Adapters.whisper_adapter import transcribe

        return _run_heavy(tool, lambda: transcribe(payload))
    if tool == "create_subtitled_video":
        return 503, _unavailable(tool, component_id, "Existing ASR is connected for transcript JSON/SRT; subtitle video muxing is not enabled until its output contract is verified.")
    if tool in {"segment_image", "track_video_object"}:
        return 503, _unavailable(tool, component_id, "Existing SAM 2 is currently exposed as a GUI; direct backend adapter is not yet verified.")
    return 503, _unavailable(tool, component_id)


def get_job_or_error(job_id: str) -> tuple[int, dict[str, Any]]:
    record = get_job(job_id)
    if record is None:
        return 404, {"status": "error", "error": f"Unknown job: {job_id}"}
    return 200, record
