"""Hub job adapter for the configured Real-ESRGAN runtime and model record."""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from src.services.api.config import component, models
from src.services.process_manager.managed import ProcessOwner, run_json_worker
from src.services.artifact_store import describe, resolve
from src.shared.paths.registry import MODEL_ROOT, OUTPUT_ROOT, TEMP_ROOT
from src.shared.utils.adapter_common import local_root, reserve_output_namespace, reserved_worker_result, seal_output_reservations, unavailable


WORKER = Path(__file__).with_name("worker.py")
_MODEL_ID = "realesr-animevideov3"
_MODEL_RELATIVE = Path("Video") / "Real-ESRGAN" / "realesr-animevideov3.pth"
_ARTIFACT_ID = re.compile(r"artifact_[a-f0-9]{32}")
_UNSAFE_INPUT_FIELDS = {"path", "source", "secondary_path", "input_path", "executable", "command", "model_path", "output_root"}


def _registry_path(component_id: str, field: str) -> Path | None:
    item = component(component_id)
    value = item.get(field) if isinstance(item, dict) and item.get("id") == component_id else None
    if not isinstance(value, str) or not value.strip() or value.startswith("${"):
        return None
    try:
        return Path(os.path.expandvars(value)).expanduser()
    except (OSError, ValueError):
        return None


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
        lexical_root = Path(os.path.abspath(str(MODEL_ROOT)))
        lexical_candidate = Path(os.path.abspath(str(candidate)))
        relative = lexical_candidate.relative_to(lexical_root)
        expected = Path(os.path.abspath(str(lexical_root / _MODEL_RELATIVE)))
    except (OSError, ValueError):
        return False
    if os.path.normcase(str(lexical_candidate)) != os.path.normcase(str(expected)):
        return False
    current = lexical_root
    if _is_reparse(current):
        return False
    for part in relative.parts:
        current = current / part
        if (current.exists() or current.is_symlink()) and _is_reparse(current):
            return False
    try:
        resolved_root = lexical_root.resolve(strict=True)
        resolved = lexical_candidate.resolve(strict=True)
        resolved.relative_to(resolved_root)
    except (OSError, ValueError):
        return False
    return lexical_candidate.is_file() and current.resolve(strict=True) == resolved


def _runtime() -> tuple[Path | None, Path | None]:
    runtime = _registry_path("real_esrgan", "path")
    environment = _registry_path("real_esrgan", "environment")
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


def _image_artifact(payload: dict[str, Any]) -> Path | None:
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
    return source if isinstance(source, Path) and source.is_file() and media_type.startswith("image/") and not _is_reparse(source) else None


def run_realesrgan(payload: dict[str, Any], context: ProcessOwner | None = None) -> dict[str, Any]:
    python, runtime = _runtime()
    model = _selected_model()
    if python is None or runtime is None or model is None or not python.is_file() or not runtime.is_dir() or not (runtime / "inference_realesrgan.py").is_file() or not WORKER.is_file():
        return unavailable("real_esrgan", "Real-ESRGAN cần environment, runtime và tool model local đã được registry xác nhận.")
    source = _image_artifact(payload)
    if source is None or source.suffix.casefold() not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
        return {"status": "error", "error": "Real-ESRGAN chỉ nhận IMAGE artifact hợp lệ của Hub."}
    try:
        scale = max(1, min(4, int(payload.get("scale", 2))))
        tile = max(0, min(2048, int(payload.get("tile", 0))))
    except (TypeError, ValueError):
        scale, tile = 2, 0
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    reservation_context = callable(getattr(context, "reserve_output_namespace", None))
    reservation = reserve_output_namespace(context, "real_esrgan", expected_patterns=["*.png"], max_children=1)
    if reservation_context and reservation is None:
        return unavailable("real_esrgan", "Real-ESRGAN không nhận được output reservation server-owned.", code="output_reservation_unavailable")
    output_root = reservation["path"] if reservation else str(OUTPUT_ROOT / "Real-ESRGAN")
    request = {
        "path": str(source),
        "runtime": str(runtime),
        "model_path": str(model),
        "model_id": _MODEL_ID,
        "output_root": output_root,
        "output_reserved": bool(reservation),
        "output_reservation_token": reservation.get("token") if reservation else None,
        "temp_root": str(TEMP_ROOT / "jobs" / f"realesrgan_{stamp}"),
        "scale": scale,
        "tile": tile,
    }
    if reservation and not seal_output_reservations(context):
        return unavailable("real_esrgan", "Real-ESRGAN không thể chốt output reservation trước khi chạy.", code="output_reservation_unavailable")
    result = run_json_worker(
        [str(python), str(WORKER)],
        request,
        label="real_esrgan_upscale",
        cwd=runtime,
        env={**os.environ, "PYTHONPATH": str(local_root()), "LOCALAIHUB_ROOT": str(local_root()), "PYTHONIOENCODING": "utf-8"},
        owner=context,
        timeout_seconds=float(payload.get("timeout_seconds", 1200)),
    )
    if reservation_context:
        return reserved_worker_result(result, context, {"output": reservation}, ("output",)) or unavailable("real_esrgan", "Real-ESRGAN output reservation could not be attested.", code="output_scope_unavailable")
    return result


def capability() -> dict[str, Any]:
    python, runtime = _runtime()
    model = _selected_model()
    return {
        "component": "real_esrgan",
        "adapter_status": "direct-worker-configured",
        "runtime_ready": bool(runtime and runtime.is_dir()),
        "environment_ready": bool(python and python.is_file()),
        "model_ready": bool(model and model.is_file()),
        "worker_ready": WORKER.is_file(),
        "script_ready": bool(runtime and (runtime / "inference_realesrgan.py").is_file()),
    }
