"""Direct AnimeSR worker adapter for the Hub workspace."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from src.services.api.config import component, models
from src.services.process_manager.managed import ProcessOwner, run_json_worker
from src.services.artifact_store import describe, resolve
from src.shared.paths.registry import MODEL_ROOT, OUTPUT_ROOT
from src.shared.utils.adapter_common import local_root, reserve_output_namespace, reserved_worker_result, seal_output_reservations, unavailable


WORKER = Path(__file__).with_name("worker.py")
_ARTIFACT_ID = re.compile(r"artifact_[a-f0-9]{32}")
_MODEL_ID = "animesr-v2"
_MODEL_SPEC = {"model": "AnimeSR_v2", "expname": "animesr_v2"}
_MODEL_RELATIVE = Path("Video") / "AnimeSR" / "AnimeSR_v2.pth"
_SCRIPT_RELATIVE = Path("scripts") / "inference_animesr_video.py"
_UNSAFE_INPUT_FIELDS = {
    "path", "source", "secondary_path", "input_path", "executable", "command",
    "model_path", "output_root", "model_id", "expname",
}


def _registry_path(component_id: str, field: str) -> Path | None:
    item = component(component_id)
    value = item.get(field) if isinstance(item, dict) and item.get("id") == component_id else None
    if not isinstance(value, str) or not value.strip() or value.startswith("${"):
        return None
    try:
        return Path(os.path.expandvars(value)).expanduser()
    except (OSError, ValueError):
        return None


def _runtime() -> tuple[Path | None, Path | None]:
    runtime = _registry_path("animesr", "path")
    environment = _registry_path("animesr", "environment")
    python = environment / "Scripts" / "python.exe" if environment else None
    return python, runtime


def _safe_script(runtime: Path | None) -> Path | None:
    """Return the fixed AnimeSR script only when its leaf is safe and real."""

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


def _is_reparse(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        return bool(getattr(os.lstat(path), "st_file_attributes", 0) & 0x400)
    except OSError:
        return True


def _safe_model_path(candidate: Path) -> bool:
    """Accept only one registry model leaf below the fixed Models root."""

    try:
        # Check the lexical path and every original ancestor before resolving:
        # a symlink/junction below Models must never be allowed to redirect a
        # registry leaf to another location.
        root = Path(os.path.abspath(str(MODEL_ROOT)))
        lexical = Path(os.path.abspath(str(candidate)))
        relative = lexical.relative_to(root)
        expected = Path(os.path.abspath(str(root / _MODEL_RELATIVE)))
    except (OSError, ValueError):
        return False
    if os.path.normcase(str(lexical)) != os.path.normcase(str(expected)):
        return False
    if _is_reparse(root):
        return False
    current = root
    for part in relative.parts:
        current = current / part
        if _is_reparse(current):
            return False
    try:
        resolved_root = root.resolve(strict=True)
        resolved = lexical.resolve(strict=True)
        resolved.relative_to(resolved_root)
    except (OSError, ValueError):
        return False
    return (resolved.is_file() or resolved.is_dir()) and current.resolve(strict=True) == resolved


def _selected_model() -> Path | None:
    """Resolve the fixed AnimeSR model from the server-owned local registry."""

    matches = [
        item for item in models()
        if isinstance(item, dict)
        and str(item.get("id")) == _MODEL_ID
        and str(item.get("engine")).casefold() == "animesr"
    ]
    if len(matches) != 1:
        return None
    value = matches[0].get("local_path")
    if not isinstance(value, str) or not value or value.startswith("${"):
        return None
    candidate = Path(os.path.expandvars(value)).expanduser()
    return candidate if _safe_model_path(candidate) else None


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


def inspect_video(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a direct worker plan without starting an external desktop GUI."""

    if _selected_model() is None:
        return unavailable("animesr", "AnimeSR tool model chưa được registry xác nhận dưới Models canonical.")
    source = _video_artifact(payload)
    if source is None:
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
    script = _safe_script(runtime)
    model_path = _selected_model()
    if model_path is None:
        return unavailable("animesr", "AnimeSR tool model chưa được registry xác nhận dưới Models canonical.")
    if python is None or runtime is None or script is None or not python.is_file() or not runtime.is_dir() or not WORKER.is_file():
        return unavailable("animesr", "AnimeSR runtime, environment hoặc fixed inference script chưa hoàn chỉnh.")
    source = _video_artifact(payload)
    if source is None:
        return {"status": "error", "error": "AnimeSR cần VIDEO artifact do Hub quản lý."}
    ffmpeg = _registry_path("ffmpeg", "executable")
    if ffmpeg is None:
        home = _registry_path("ffmpeg", "path")
        ffmpeg = home / "ffmpeg.exe" if home else None
    if ffmpeg is None or not ffmpeg.is_file():
        return unavailable("ffmpeg", "Không tìm thấy FFmpeg canonical của Hub cho AnimeSR.")
    reservation_context = callable(getattr(context, "reserve_output_namespace", None))
    reservation = reserve_output_namespace(context, "animesr", expected_patterns=["*.mp4"], max_children=1)
    if reservation_context and reservation is None:
        return unavailable("animesr", "AnimeSR không nhận được output reservation server-owned.", code="output_reservation_unavailable")
    output_root = reservation["path"] if reservation else str(OUTPUT_ROOT / "AnimeSR")
    try:
        scale = max(1, min(4, int(payload.get("scale", 2))))
    except (TypeError, ValueError):
        scale = 2
    request = {
        "path": str(source),
        "runtime": str(runtime),
        "output_root": output_root,
        "output_reserved": bool(reservation),
        "output_reservation_token": reservation.get("token") if reservation else None,
        "scale": scale,
        "model_id": _MODEL_ID,
        "model": _MODEL_SPEC["model"],
        "model_path": str(model_path),
        "expname": _MODEL_SPEC["expname"],
        "half": bool(payload.get("half", True)),
        # AnimeSR's checked-in inference script reads this exact environment
        # variable.  Passing the canonical executable avoids an ambient PATH
        # lookup without modifying the installed runtime.
        "ffmpeg": str(ffmpeg),
    }
    if reservation and not seal_output_reservations(context):
        return unavailable("animesr", "AnimeSR không thể chốt output reservation trước khi chạy.", code="output_reservation_unavailable")
    result = run_json_worker(
        [str(python), str(WORKER)],
        request,
        label="animesr_upscale",
        cwd=runtime,
        env={**os.environ, "PYTHONPATH": str(local_root()), "LOCALAIHUB_ROOT": str(local_root()), "PYTHONIOENCODING": "utf-8"},
        owner=context,
        timeout_seconds=3600,
    )
    if reservation_context:
        return reserved_worker_result(result, context, {"output": reservation}, ("output",)) or unavailable("animesr", "AnimeSR output reservation could not be attested.", code="output_scope_unavailable")
    return result


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


def queue_upscale(_input_path: str, _output_path: str | None = None) -> dict[str, Any]:
    """Deprecated raw-path shim; public callers must submit a Hub artifact."""

    return {"status": "unavailable", "reason": "AnimeSR chỉ nhận VIDEO artifact do Hub quản lý."}


def capability() -> dict[str, Any]:
    python, runtime = _runtime()
    script = _safe_script(runtime)
    model_path = _selected_model()
    ffmpeg = _registry_path("ffmpeg", "executable")
    if ffmpeg is None:
        home = _registry_path("ffmpeg", "path")
        ffmpeg = home / "ffmpeg.exe" if home else None
    return {
        "component": "animesr",
        "adapter_status": "direct-worker-configured",
        "runtime_ready": bool(runtime and runtime.is_dir()),
        "environment_ready": bool(python and python.is_file()),
        "model_ready": bool(model_path),
        "script_ready": script is not None,
        "model_id": _MODEL_ID,
        "cli_compatible": bool(model_path),
        "ffmpeg_ready": bool(ffmpeg and ffmpeg.is_file()),
        "worker_ready": WORKER.is_file(),
    }
