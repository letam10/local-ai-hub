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


def component_statuses() -> list[dict[str, Any]]:
    statuses: list[dict[str, Any]] = []
    for item in components():
        status = item.get("status", "unknown")
        executable = item.get("executable")
        path = item.get("path")
        if status == "planned":
            observed = "planned"
        elif executable and _path_exists(executable):
            observed = "running" if _port_open(item.get("port")) else "installed"
        elif path and _path_exists(path):
            observed = "installed"
        else:
            observed = "missing"
        statuses.append({**item, "observed_status": observed})
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


def tool_catalog() -> list[dict[str, Any]]:
    statuses = {item["id"]: item for item in component_statuses()}
    tools = []
    for name, component_id in TOOL_COMPONENTS.items():
        item = statuses.get(component_id, {})
        tools.append({
            "name": name,
            "component": component_id,
            "status": item.get("observed_status", "missing"),
            "description": f"Allowlisted Local AI Hub tool backed by {item.get('name', component_id)}.",
        })
    tools.extend([
        {"name": "get_health", "component": "local_ai_api", "status": "running", "description": "Return Hub health and GPU policy."},
        {"name": "list_models", "component": "local_ai_api", "status": "running", "description": "Return the model registry."},
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


def _unavailable(tool: str, component_id: str, reason: str | None = None) -> dict[str, Any]:
    item = component(component_id) or {}
    return {
        "status": "unavailable",
        "tool": tool,
        "component": component_id,
        "reason": reason or f"{item.get('name', component_id)} is not ready for a backend call.",
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
    if tool == "probe_media":
        path = payload.get("path")
        if not isinstance(path, str) or not path:
            return 400, {"status": "error", "error": "Expected JSON field 'path'."}
        result = probe_media(path)
        return (200 if result.get("status") == "completed" else 400), result

    component_id = TOOL_COMPONENTS.get(tool)
    if component_id is None:
        return 404, {"status": "error", "error": f"Unknown allowlisted tool: {tool}"}

    item = component(component_id) or {}
    if item.get("status") == "planned":
        return 503, _unavailable(tool, component_id, "Component is planned but not installed.")
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
