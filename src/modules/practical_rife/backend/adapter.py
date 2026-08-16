"""Hub job adapter for the configured Practical-RIFE runtime."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from src.services.process_manager.managed import ProcessOwner, run_json_worker
from src.shared.paths.registry import OUTPUT_ROOT, TEMP_ROOT
from src.shared.utils.adapter_common import configured_path, local_root, unavailable


WORKER = Path(__file__).with_name("worker.py")
_MODEL_RELATIVE = Path("train_log") / "RIFEv4.26_0921"


def _configured_ffmpeg() -> tuple[Path | None, Path | None]:
    ffmpeg = configured_path("ffmpeg", "executable", "FFMPEG_PATH")
    home = configured_path("ffmpeg", "path", "FFMPEG_HOME")
    if ffmpeg is None and home is not None:
        ffmpeg = home / "ffmpeg.exe"
    ffprobe = home / "ffprobe.exe" if home is not None else None
    return ffmpeg, ffprobe


def _runtime() -> tuple[Path | None, Path | None, Path | None]:
    runtime = configured_path("practical_rife", "path", "PRACTICAL_RIFE_HOME")
    environment = configured_path("practical_rife", "environment", "PRACTICAL_RIFE_ENV")
    python = environment / "Scripts" / "python.exe" if environment else None
    model_dir = runtime / _MODEL_RELATIVE if runtime else None
    return python, runtime, model_dir


def run_practical_rife(payload: dict[str, Any], context: ProcessOwner | None = None) -> dict[str, Any]:
    """Submit interpolation through an owned worker, never ambient FFmpeg."""

    python, runtime, model_dir = _runtime()
    ffmpeg, ffprobe = _configured_ffmpeg()
    if (
        python is None
        or runtime is None
        or model_dir is None
        or ffmpeg is None
        or ffprobe is None
        or not python.is_file()
        or not runtime.is_dir()
        or not (model_dir / "flownet.pkl").is_file()
        or not ffmpeg.is_file()
        or not ffprobe.is_file()
        or not WORKER.is_file()
    ):
        return unavailable("practical_rife", "Practical-RIFE cần environment, model và cặp FFmpeg/FFprobe canonical của Hub.")
    source = Path(os.path.expandvars(str(payload.get("path", "")))).expanduser()
    if not source.is_file():
        return {"status": "error", "error": "Không tìm thấy video đầu vào cho Practical-RIFE."}
    try:
        target_fps = max(2, min(120, int(payload.get("target_fps", 48))))
    except (TypeError, ValueError):
        target_fps = 48
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    request = {
        "path": str(source),
        "runtime": str(runtime),
        "model_dir": str(model_dir),
        "ffmpeg": str(ffmpeg),
        "ffprobe": str(ffprobe),
        "output_root": str(OUTPUT_ROOT / "Practical-RIFE"),
        "temp_root": str(TEMP_ROOT / "jobs" / f"rife_{stamp}"),
        "target_fps": target_fps,
        "half": bool(payload.get("half", True)),
    }
    return run_json_worker(
        [str(python), str(WORKER)],
        request,
        label="practical_rife_interpolate",
        cwd=runtime,
        env={**os.environ, "PYTHONPATH": str(local_root()), "LOCALAIHUB_ROOT": str(local_root()), "PYTHONIOENCODING": "utf-8"},
        owner=context,
        timeout_seconds=float(payload.get("timeout_seconds", 1800)),
    )


def capability() -> dict[str, Any]:
    python, runtime, model_dir = _runtime()
    ffmpeg, ffprobe = _configured_ffmpeg()
    return {
        "component": "practical_rife",
        "adapter_status": "direct-worker-configured",
        "runtime_ready": bool(runtime and runtime.is_dir() and model_dir and (model_dir / "flownet.pkl").is_file()),
        "environment_ready": bool(python and python.is_file()),
        "ffmpeg_ready": bool(ffmpeg and ffmpeg.is_file() and ffprobe and ffprobe.is_file()),
    }
