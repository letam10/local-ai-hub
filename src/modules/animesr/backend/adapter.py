"""Direct AnimeSR worker adapter for the Hub workspace."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from src.services.process_manager.managed import ProcessOwner, run_json_worker
from src.shared.paths.registry import OUTPUT_ROOT
from src.shared.utils.adapter_common import configured_path, local_root, unavailable


WORKER = Path(__file__).with_name("worker.py")


def _runtime() -> tuple[Path | None, Path | None]:
    runtime = configured_path("animesr", "path", "ANIMESR_HOME")
    environment = configured_path("animesr", "environment", "ANIMESR_ENV")
    python = environment / "Scripts" / "python.exe" if environment else None
    return python, runtime


def inspect_video(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a direct worker plan without starting an external desktop GUI."""

    source = Path(os.path.expandvars(str(payload.get("path", "")))).expanduser()
    if not source.is_file():
        return {"status": "error", "error": "Không tìm thấy video AnimeSR đầu vào."}
    return {
        "status": "completed",
        "operation": "inspect_video",
        "input_name": source.name,
        "requested_scale": max(1, min(4, int(payload.get("scale", 2)))),
        "chunk_seconds": max(10, int(payload.get("chunk_seconds", 120))),
    }


def create_plan(payload: dict[str, Any]) -> dict[str, Any]:
    result = inspect_video(payload)
    if result.get("status") != "completed":
        return result
    return {
        **result,
        "operation": "create_plan",
        "steps": ["inspect", "AnimeSR", "optional RIFE", "optional Real-ESRGAN", "merge", "cleanup"],
        "rife": bool(payload.get("use_rife")),
        "realesrgan": bool(payload.get("use_realesrgan")),
    }


def split_video(payload: dict[str, Any]) -> dict[str, Any]:
    # Chunks are intentionally created by the direct worker only when the job
    # runs, keeping source media untouched and avoiding a second public path.
    return {"status": "completed", "operation": "split_video", "message": "Chunk sẽ được worker AnimeSR tạo trong Temp của Hub khi job bắt đầu."}


def run_animesr(payload: dict[str, Any], context: ProcessOwner | None = None) -> dict[str, Any]:
    python, runtime = _runtime()
    if python is None or runtime is None or not python.is_file() or not runtime.is_dir() or not WORKER.is_file():
        return unavailable("animesr", "AnimeSR runtime hoặc environment trực tiếp chưa hoàn chỉnh.")
    source = Path(os.path.expandvars(str(payload.get("path", "")))).expanduser()
    if not source.is_file():
        return {"status": "error", "error": "Không tìm thấy video AnimeSR đầu vào."}
    ffmpeg = configured_path("ffmpeg", "executable", "FFMPEG_PATH")
    if ffmpeg is None:
        home = configured_path("ffmpeg", "path", "FFMPEG_HOME")
        ffmpeg = home / "ffmpeg.exe" if home else None
    if ffmpeg is None or not ffmpeg.is_file():
        return unavailable("ffmpeg", "Không tìm thấy FFmpeg canonical của Hub cho AnimeSR.")
    request = {
        **payload,
        "path": str(source),
        "runtime": str(runtime),
        "output_root": str(OUTPUT_ROOT / "AnimeSR"),
        # AnimeSR's checked-in inference script reads this exact environment
        # variable.  Passing the canonical executable avoids an ambient PATH
        # lookup without modifying the installed runtime.
        "ffmpeg": str(ffmpeg),
    }
    return run_json_worker(
        [str(python), str(WORKER)],
        request,
        label="animesr_upscale",
        cwd=runtime,
        env={**os.environ, "PYTHONPATH": str(local_root()), "LOCALAIHUB_ROOT": str(local_root()), "PYTHONIOENCODING": "utf-8"},
        owner=context,
        timeout_seconds=float(payload.get("timeout_seconds", 3600)),
    )


def run_optional_rife(payload: dict[str, Any], _context: ProcessOwner | None = None) -> dict[str, Any]:
    if not payload.get("use_rife"):
        return {"status": "completed", "operation": "run_optional_rife", "skipped": True}
    return unavailable("practical_rife", "Practical-RIFE chưa có CLI contract đã xác minh cho worker Hub; lựa chọn này được giữ partial.")


def run_optional_realesrgan(payload: dict[str, Any], _context: ProcessOwner | None = None) -> dict[str, Any]:
    if not payload.get("use_realesrgan"):
        return {"status": "completed", "operation": "run_optional_realesrgan", "skipped": True}
    return unavailable("real_esrgan", "Real-ESRGAN chưa có CLI contract đã xác minh cho worker Hub; lựa chọn này được giữ partial.")


def merge(payload: dict[str, Any], _context: ProcessOwner | None = None) -> dict[str, Any]:
    return {"status": "completed", "operation": "merge", "message": "Worker AnimeSR ghép output trong cùng một job; không cần mở Studio bên ngoài."}


def cleanup_temp(payload: dict[str, Any], _context: ProcessOwner | None = None) -> dict[str, Any]:
    return {"status": "completed", "operation": "cleanup_temp", "message": "Temp của job chỉ được dọn sau khi output đã được xuất ra Output/AnimeSR."}


def cancel(_job_id: str) -> dict[str, Any]:
    return {"status": "completed", "operation": "cancel", "message": "Job Manager dừng đúng process AnimeSR do Hub sở hữu."}


def resume(_job_id: str) -> dict[str, Any]:
    return {"status": "completed", "operation": "resume", "message": "Job Manager tạo lại job từ payload đã lưu trong phiên Hub."}


def queue_upscale(input_path: str, output_path: str | None = None) -> dict[str, Any]:
    """Compatibility shim; callers should submit ``run_animesr`` through Job Manager."""

    return run_animesr({"path": input_path, "requested_output": output_path})


def capability() -> dict[str, Any]:
    python, runtime = _runtime()
    return {
        "component": "animesr",
        "adapter_status": "direct-worker-configured",
        "runtime_ready": bool(runtime and runtime.is_dir()),
        "environment_ready": bool(python and python.is_file()),
    }
