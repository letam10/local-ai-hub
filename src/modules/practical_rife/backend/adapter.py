"""Hub job adapter for the configured Practical-RIFE runtime."""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from src.services.api.config import component
from src.services.process_manager.managed import ProcessOwner, run_json_worker
from src.services.artifact_store import describe, resolve
from src.shared.paths.registry import OUTPUT_ROOT, TEMP_ROOT
from src.shared.utils.adapter_common import local_root, reserve_output_namespace, reserved_worker_result, seal_output_reservations, unavailable


WORKER = Path(__file__).with_name("worker.py")
_MODEL_RELATIVE = Path("train_log") / "RIFEv4.26_0921"
_SCRIPT_RELATIVE = Path("inference_video.py")
_ARTIFACT_ID = re.compile(r"artifact_[a-f0-9]{32}")
_UNSAFE_INPUT_FIELDS = {"path", "source", "secondary_path", "input_path", "executable", "command", "model_path", "output_root"}


def _is_reparse(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        return bool(getattr(os.lstat(path), "st_file_attributes", 0) & 0x400)
    except OSError:
        return True


def _registry_path(component_id: str, field: str) -> Path | None:
    item = component(component_id)
    value = item.get(field) if isinstance(item, dict) and item.get("id") == component_id else None
    if not isinstance(value, str) or not value.strip() or value.startswith("${"):
        return None
    try:
        return Path(os.path.expandvars(value)).expanduser()
    except (OSError, ValueError):
        return None


def _configured_ffmpeg() -> tuple[Path | None, Path | None]:
    ffmpeg = _registry_path("ffmpeg", "executable")
    home = _registry_path("ffmpeg", "path")
    if home is not None and home.is_file():
        home = home.parent
    if ffmpeg is not None and ffmpeg.is_dir():
        ffmpeg = ffmpeg / "ffmpeg.exe"
    if ffmpeg is None and home is not None:
        ffmpeg = home / "ffmpeg.exe"
    if home is None and ffmpeg is not None:
        home = ffmpeg.parent
    ffprobe = home / "ffprobe.exe" if home is not None else None
    if ffmpeg is not None and ffprobe is not None:
        try:
            if ffmpeg.resolve(strict=False).parent != ffprobe.resolve(strict=False).parent:
                return None, None
        except OSError:
            return None, None
    return ffmpeg, ffprobe


def _runtime() -> tuple[Path | None, Path | None, Path | None]:
    runtime = _registry_path("practical_rife", "path")
    environment = _registry_path("practical_rife", "environment")
    python = environment / "Scripts" / "python.exe" if environment else None
    model_dir = runtime / _MODEL_RELATIVE if runtime else None
    return python, runtime, model_dir


def _safe_script(runtime: Path | None) -> Path | None:
    """Return the fixed Practical-RIFE script only when its leaf is safe."""

    if runtime is None:
        return None
    try:
        lexical_root = Path(os.path.abspath(str(runtime)))
        lexical_script = Path(os.path.abspath(str(lexical_root / _SCRIPT_RELATIVE)))
        lexical_script.relative_to(lexical_root)
    except (OSError, ValueError):
        return None
    if _is_reparse(lexical_root):
        return None
    current = lexical_root
    for part in _SCRIPT_RELATIVE.parts:
        current = current / part
        if _is_reparse(current):
            return None
    try:
        resolved_root = lexical_root.resolve(strict=True)
        resolved_script = lexical_script.resolve(strict=True)
        resolved_script.relative_to(resolved_root)
    except (OSError, ValueError):
        return None
    if not lexical_script.is_file():
        return None
    return lexical_script if current.resolve(strict=True) == resolved_script else None


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
    return source if isinstance(source, Path) and source.is_file() and media_type.startswith("video/") and not _is_reparse(source) else None


def run_practical_rife(payload: dict[str, Any], context: ProcessOwner | None = None) -> dict[str, Any]:
    """Submit interpolation through an owned worker, never ambient FFmpeg."""

    python, runtime, model_dir = _runtime()
    script = _safe_script(runtime)
    ffmpeg, ffprobe = _configured_ffmpeg()
    if (
        python is None
        or runtime is None
        or model_dir is None
        or script is None
        or ffmpeg is None
        or ffprobe is None
        or not python.is_file()
        or not runtime.is_dir()
        or not (model_dir / "flownet.pkl").is_file()
        or not ffmpeg.is_file()
        or not ffprobe.is_file()
        or not WORKER.is_file()
    ):
        return unavailable("practical_rife", "Practical-RIFE cần environment, model, fixed inference script và cặp FFmpeg/FFprobe canonical của Hub.")
    source = _video_artifact(payload)
    if source is None:
        return {"status": "error", "error": "Practical-RIFE cần VIDEO artifact do Hub quản lý."}
    try:
        target_fps = max(2, min(120, int(payload.get("target_fps", 48))))
    except (TypeError, ValueError):
        target_fps = 48
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    reservation_context = callable(getattr(context, "reserve_output_namespace", None))
    reservation = reserve_output_namespace(context, "practical_rife", expected_patterns=["*.mp4"], max_children=1)
    if reservation_context and reservation is None:
        return unavailable("practical_rife", "Practical-RIFE không nhận được output reservation server-owned.", code="output_reservation_unavailable")
    output_root = reservation["path"] if reservation else str(OUTPUT_ROOT / "Practical-RIFE")
    request = {
        "path": str(source),
        "runtime": str(runtime),
        "model_dir": str(model_dir),
        "ffmpeg": str(ffmpeg),
        "ffprobe": str(ffprobe),
        "output_root": output_root,
        "output_reserved": bool(reservation),
        "output_reservation_token": reservation.get("token") if reservation else None,
        "temp_root": str(TEMP_ROOT / "jobs" / f"rife_{stamp}"),
        "target_fps": target_fps,
        "half": bool(payload.get("half", True)),
    }
    if reservation and not seal_output_reservations(context):
        return unavailable("practical_rife", "Practical-RIFE không thể chốt output reservation trước khi chạy.", code="output_reservation_unavailable")
    result = run_json_worker(
        [str(python), str(WORKER)],
        request,
        label="practical_rife_interpolate",
        cwd=runtime,
        env={**os.environ, "PYTHONPATH": str(local_root()), "LOCALAIHUB_ROOT": str(local_root()), "PYTHONIOENCODING": "utf-8"},
        owner=context,
        timeout_seconds=float(payload.get("timeout_seconds", 1800)),
    )
    if reservation_context:
        return reserved_worker_result(result, context, {"output": reservation}, ("output",)) or unavailable("practical_rife", "Practical-RIFE output reservation could not be attested.", code="output_scope_unavailable")
    return result


def capability() -> dict[str, Any]:
    python, runtime, model_dir = _runtime()
    script = _safe_script(runtime)
    ffmpeg, ffprobe = _configured_ffmpeg()
    return {
        "component": "practical_rife",
        "adapter_status": "direct-worker-configured",
        "runtime_ready": bool(runtime and runtime.is_dir() and model_dir and (model_dir / "flownet.pkl").is_file()),
        "environment_ready": bool(python and python.is_file()),
        "ffmpeg_ready": bool(ffmpeg and ffmpeg.is_file() and ffprobe and ffprobe.is_file()),
        "script_ready": script is not None,
        "worker_ready": WORKER.is_file(),
    }
