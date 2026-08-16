"""Hub job adapter for the configured Real-ESRGAN runtime and model record."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from src.services.api.config import models
from src.services.process_manager.managed import ProcessOwner, run_json_worker
from src.shared.paths.registry import MODEL_ROOT, OUTPUT_ROOT, TEMP_ROOT
from src.shared.utils.adapter_common import configured_path, local_root, unavailable


WORKER = Path(__file__).with_name("worker.py")
_MODEL_ID = "realesr-animevideov3"


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except (OSError, ValueError):
        return False


def _is_reparse(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        return bool(getattr(os.lstat(path), "st_file_attributes", 0) & 0x400)
    except OSError:
        return True


def _safe_model_path(candidate: Path) -> bool:
    try:
        relative = candidate.resolve(strict=False).relative_to(MODEL_ROOT.resolve(strict=False))
    except (OSError, ValueError):
        return False
    current = MODEL_ROOT
    if _is_reparse(current):
        return False
    for part in relative.parts:
        current = current / part
        if (current.exists() or current.is_symlink()) and _is_reparse(current):
            return False
    return candidate.is_file() and current.resolve(strict=False) == candidate.resolve(strict=False)


def _runtime() -> tuple[Path | None, Path | None]:
    runtime = configured_path("real_esrgan", "path", "REAL_ESRGAN_HOME")
    environment = configured_path("real_esrgan", "environment", "REAL_ESRGAN_ENV")
    return environment / "Scripts" / "python.exe" if environment else None, runtime


def _selected_model() -> Path | None:
    """Select one server-owned tool-model record; never accept a model path."""

    for item in models():
        if str(item.get("id")) != _MODEL_ID or str(item.get("engine")).casefold() != "real-esrgan":
            continue
        value = item.get("local_path")
        if not isinstance(value, str) or not value:
            return None
        candidate = Path(os.path.expandvars(value)).expanduser()
        if _safe_model_path(candidate):
            return candidate
    return None


def run_realesrgan(payload: dict[str, Any], context: ProcessOwner | None = None) -> dict[str, Any]:
    python, runtime = _runtime()
    model = _selected_model()
    if python is None or runtime is None or model is None or not python.is_file() or not runtime.is_dir() or not (runtime / "inference_realesrgan.py").is_file() or not WORKER.is_file():
        return unavailable("real_esrgan", "Real-ESRGAN cần environment, runtime và tool model local đã được registry xác nhận.")
    source = Path(os.path.expandvars(str(payload.get("path", "")))).expanduser()
    if not source.is_file() or source.suffix.casefold() not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
        return {"status": "error", "error": "Real-ESRGAN chỉ nhận IMAGE artifact hợp lệ của Hub."}
    try:
        scale = max(1, min(4, int(payload.get("scale", 2))))
        tile = max(0, min(2048, int(payload.get("tile", 0))))
    except (TypeError, ValueError):
        scale, tile = 2, 0
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    request = {
        "path": str(source),
        "runtime": str(runtime),
        "model_path": str(model),
        "model_id": _MODEL_ID,
        "output_root": str(OUTPUT_ROOT / "Real-ESRGAN"),
        "temp_root": str(TEMP_ROOT / "jobs" / f"realesrgan_{stamp}"),
        "scale": scale,
        "tile": tile,
    }
    return run_json_worker(
        [str(python), str(WORKER)],
        request,
        label="real_esrgan_upscale",
        cwd=runtime,
        env={**os.environ, "PYTHONPATH": str(local_root()), "LOCALAIHUB_ROOT": str(local_root()), "PYTHONIOENCODING": "utf-8"},
        owner=context,
        timeout_seconds=float(payload.get("timeout_seconds", 1200)),
    )


def capability() -> dict[str, Any]:
    python, runtime = _runtime()
    model = _selected_model()
    return {
        "component": "real_esrgan",
        "adapter_status": "direct-worker-configured",
        "runtime_ready": bool(runtime and runtime.is_dir()),
        "environment_ready": bool(python and python.is_file()),
        "model_ready": bool(model and model.is_file()),
    }
