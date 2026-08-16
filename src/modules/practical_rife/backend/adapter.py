"""Hub job adapter for the configured Practical-RIFE runtime."""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from src.services.process_manager.managed import ProcessOwner, run_json_worker
from src.services.artifact_store import describe, resolve
from src.shared.paths.registry import OUTPUT_ROOT, TEMP_ROOT
from src.shared.utils.adapter_common import configured_path, local_root, unavailable


WORKER = Path(__file__).with_name("worker.py")
_MODEL_RELATIVE = Path("train_log") / "RIFEv4.26_0921"
_ARTIFACT_ID = re.compile(r"artifact_[a-f0-9]{32}")
_UNSAFE_INPUT_FIELDS = {"path", "source", "secondary_path", "input_path", "executable", "command", "model_path", "output_root"}


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


def _video_artifact(payload: dict[str, Any]) -> Path | None:
    if any(payload.get(name) not in (None, "", []) for name in _UNSAFE_INPUT_FIELDS):
        return None
    artifact_id = payload.get("source_artifact_id")
    if not isinstance(artifact_id, str) or not _ARTIFACT_ID.fullmatch(artifact_id):
        return None
    try:
        source = resolve(artifact_id)
        metadata = describe(artifact_id)
    except Exception:
        return None
    media_type = str(metadata.get("media_type") or "") if isinstance(metadata, dict) else ""
    return source if isinstance(source, Path) and source.is_file() and media_type.startswith("video/") else None


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
    source = _video_artifact(payload)
    if source is None:
        return {"status": "error", "error": "Practical-RIFE cần VIDEO artifact do Hub quản lý."}
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
        "worker_ready": WORKER.is_file(),
    }
