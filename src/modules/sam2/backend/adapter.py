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
from src.services.artifact_store import describe
from src.shared.utils.adapter_common import (
    bounded_timeout,
    configured_path,
    local_root,
    normalize_worker_result,
    resolve_artifact_input,
    unavailable,
)


WORKER = Path(__file__).with_name("worker.py")


def _runtime() -> tuple[Path | None, Path | None]:
    runtime = configured_path("sam2", "path", "SAM2_HOME")
    environment = configured_path("sam2", "environment", "SAM2_ENV")
    python = environment / "Scripts" / "python.exe" if environment else None
    return python, runtime


def _is_reparse(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        return bool(getattr(os.lstat(path), "st_file_attributes", 0) & 0x400)
    except OSError:
        return True


def _safe_existing_under(root: Path, candidate: Path, *, kind: str) -> Path | None:
    """Return a non-reparse existing leaf under one fixed Hub root."""

    try:
        lexical_root = Path(os.path.abspath(str(root)))
        lexical_candidate = Path(os.path.abspath(str(candidate)))
        relative = lexical_candidate.relative_to(lexical_root)
        if not lexical_root.is_dir() or _is_reparse(lexical_root):
            return None
        current = lexical_root
        for part in relative.parts:
            current = current / part
            if not current.exists() or _is_reparse(current):
                return None
        resolved_root = lexical_root.resolve(strict=True)
        resolved = lexical_candidate.resolve(strict=True)
        resolved.relative_to(resolved_root)
        if _is_reparse(resolved):
            return None
        if kind == "file" and not resolved.is_file():
            return None
        if kind == "dir" and not resolved.is_dir():
            return None
        return resolved
    except (OSError, RuntimeError, ValueError):
        return None


def _runtime_contract() -> tuple[Path, Path, Path] | None:
    """Bind Python, runtime and checkpoint to canonical non-reparse roots."""

    python, runtime = _runtime()
    root = local_root()
    if python is None or runtime is None:
        return None
    safe_runtime = _safe_existing_under(root / "runtime", runtime, kind="dir")
    safe_python = _safe_existing_under(root / "Environments", python, kind="file")
    checkpoint = _safe_existing_under(
        safe_runtime,
        safe_runtime / "checkpoints" / "sam2.1_hiera_small.pt",
        kind="file",
    ) if safe_runtime is not None else None
    if safe_python is None or safe_runtime is None or checkpoint is None:
        return None
    return safe_python, safe_runtime, checkpoint


def _source_artifact(payload: dict[str, Any], *, video: bool) -> tuple[Path | None, str | None]:
    """Resolve one typed Hub artifact without accepting caller filesystem input."""

    source, error = resolve_artifact_input(payload, "source_artifact_id", "asset_id")
    if error:
        return None, error
    artifact_id = payload.get("source_artifact_id") or payload.get("asset_id")
    metadata = describe(str(artifact_id)) if isinstance(artifact_id, str) else None
    media_type = str((metadata or {}).get("media_type") or "").lower()
    expected = "video/" if video else "image/"
    if not media_type.startswith(expected):
        return None, "artifact_media_type_invalid"
    return source, None


def _input_error(code: str) -> dict[str, Any]:
    return {
        "status": "error",
        "component": "sam2",
        "code": code,
        "error": "Chọn artifact Hub hợp lệ, đúng loại media cho SAM2.",
    }


def _run(operation: str, payload: dict[str, Any], context: ProcessOwner | None = None) -> dict[str, Any]:
    contract = _runtime_contract()
    if contract is None or not WORKER.is_file():
        return unavailable("sam2", "SAM2 runtime, checkpoint hoặc environment canonical chưa hoàn chỉnh.", code="runtime_contract_missing")
    python, runtime, checkpoint = contract
    source: Path | None = None
    if operation != "load_model":
        source, error = _source_artifact(payload, video=operation == "track_video")
        if error:
            return _input_error(error)
    request = {
        **payload,
        "operation": operation,
        "runtime": str(runtime),
        "checkpoint": str(checkpoint),
    }
    if source is not None:
        # The worker receives this private, already-resolved hand-off only.
        # Public callers can never supply ``path`` because the artifact input
        # resolver rejects every raw path-shaped field above.
        request["path"] = str(source)
    result = run_json_worker(
        [str(python), str(WORKER)],
        request,
        label=f"sam2_{operation}",
        cwd=runtime,
        env={**os.environ, "PYTHONPATH": str(local_root()), "LOCALAIHUB_ROOT": str(local_root()), "PYTHONIOENCODING": "utf-8"},
        owner=context,
        timeout_seconds=bounded_timeout(payload.get("timeout_seconds"), 1200, maximum=1200),
    )
    return normalize_worker_result(result, component_id="sam2", context=context, output_fields=("output", "files", "outputs"))


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
        "worker_contract": "sam2.artifact-output.v1",
        "worker_contract_status": "unverified_until_smoke",
        "runtime_ready": bool(runtime and runtime.is_dir()),
        "environment_ready": bool(python and python.is_file()),
        "status": "partial",
        "execution": "not_run",
        "reason": "SAM2 chỉ sẵn sàng nhận artifact Hub và vẫn cần bounded smoke trước khi được coi là operational.",
        "next_action": "Chạy một segmentation artifact nhỏ sau khi tài nguyên GPU được xác nhận rảnh.",
    }
