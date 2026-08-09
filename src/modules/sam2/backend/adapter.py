"""Direct SAM2 adapter used by the Hub job manager.

The adapter invokes the current canonical SAM2 source through its configured
Python environment.  It does not launch SAM2 Mask Studio; the legacy GUI is
kept as an advanced fallback only.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from src.services.process_manager.managed import ProcessOwner, run_json_worker
from src.shared.paths.registry import OUTPUT_ROOT
from src.shared.utils.adapter_common import configured_path, local_root, unavailable


WORKER = Path(__file__).with_name("worker.py")


def _runtime() -> tuple[Path | None, Path | None]:
    runtime = configured_path("sam2", "path", "SAM2_HOME")
    environment = configured_path("sam2", "environment", "SAM2_ENV")
    python = environment / "Scripts" / "python.exe" if environment else None
    return python, runtime


def _run(operation: str, payload: dict[str, Any], context: ProcessOwner | None = None) -> dict[str, Any]:
    python, runtime = _runtime()
    if python is None or runtime is None or not python.is_file() or not runtime.is_dir() or not WORKER.is_file():
        return unavailable("sam2", "SAM2 runtime hoặc environment trực tiếp chưa hoàn chỉnh.")
    checkpoint = runtime / "checkpoints" / "sam2.1_hiera_small.pt"
    if not checkpoint.is_file():
        return unavailable("sam2", "Không tìm thấy checkpoint SAM2.1 Hiera Small tại runtime canonical.")
    request = {
        **payload,
        "operation": operation,
        "runtime": str(runtime),
        "checkpoint": str(checkpoint),
        "output_root": str(OUTPUT_ROOT / "SAM2"),
    }
    return run_json_worker(
        [str(python), str(WORKER)],
        request,
        label=f"sam2_{operation}",
        cwd=runtime,
        env={**os.environ, "PYTHONPATH": str(local_root()), "LOCALAIHUB_ROOT": str(local_root()), "PYTHONIOENCODING": "utf-8"},
        owner=context,
        timeout_seconds=float(payload.get("timeout_seconds", 1200)),
    )


def load_model(payload: dict[str, Any] | None = None, context: ProcessOwner | None = None) -> dict[str, Any]:
    return _run("load_model", payload or {}, context)


def segment_image(payload: dict[str, Any], context: ProcessOwner | None = None) -> dict[str, Any]:
    return _run("segment_image", payload, context)


def segment_from_box(payload: dict[str, Any], context: ProcessOwner | None = None) -> dict[str, Any]:
    return _run("segment_from_box", payload, context)


def segment_from_points(payload: dict[str, Any], context: ProcessOwner | None = None) -> dict[str, Any]:
    return _run("segment_from_points", payload, context)


def track_video(payload: dict[str, Any], context: ProcessOwner | None = None) -> dict[str, Any]:
    return _run("track_video", payload, context)


def release_model() -> dict[str, Any]:
    # Each direct worker is isolated and releases Torch allocations on exit.
    return {"status": "completed", "operation": "release_model", "message": "SAM2 worker theo yêu cầu không giữ model giữa các job."}


def capability() -> dict[str, Any]:
    python, runtime = _runtime()
    return {
        "component": "sam2",
        "adapter_status": "direct-worker-configured",
        "runtime_ready": bool(runtime and runtime.is_dir()),
        "environment_ready": bool(python and python.is_file()),
    }
