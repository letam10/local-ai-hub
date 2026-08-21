"""Faster-Whisper adapter for Hub-owned jobs and opaque artifacts."""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Any

from src.services.artifact_store import describe, resolve
from src.services.api.config import models
from src.services.process_manager.managed import ProcessOwner, run_json_worker
from src.shared.utils.adapter_common import attest_worker_output_paths, configured_path, local_root, requires_server_output_namespace, server_output_namespace, unavailable


_MODEL_ID = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")
_TOKEN = re.compile(r"[a-f0-9]{32}\Z")
_ARTIFACT_ID = re.compile(r"artifact_[a-f0-9]{32}\Z")
_MAX_TIMEOUT_SECONDS = 1_200
_PATH_LIKE_FIELDS = frozenset({
    "path",
    "source",
    "secondary_path",
    "reference_audio",
    "target",
    "input_image",
    "input_paths",
    "output",
    "outputs",
    "files",
    "command",
    "executable",
    "runtime",
    "model",
    "model_id",
    "local_path",
    "asset_id",
    "input_asset_id",
    "source_asset_id",
    "target_asset_id",
    "manifest",
    "callable",
    "secret",
})


def _runtime() -> tuple[Path | None, Path, Path]:
    root = local_root()
    python = configured_path("whisper", "executable", "WHISPER_PYTHON")
    return python, root / "Services" / "Whisper" / "whisper_cli.py", root


def _is_reparse(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        return bool(getattr(os.lstat(path), "st_file_attributes", 0) & 0x400)
    except OSError:
        return True


def _model_id() -> str | None:
    candidates: list[str] = []
    for item in models():
        if not isinstance(item, dict) or item.get("engine") != "Faster-Whisper":
            continue
        identifier = item.get("id")
        if isinstance(identifier, str) and _MODEL_ID.fullmatch(identifier) and isinstance(item.get("local_path"), str):
            candidates.append(identifier)
    return candidates[0] if len(candidates) == 1 else None


def _timeout(value: object) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        return float(_MAX_TIMEOUT_SECONDS)
    if timeout != timeout or timeout <= 0:
        return float(_MAX_TIMEOUT_SECONDS)
    return min(timeout, float(_MAX_TIMEOUT_SECONDS))


def _outputs(root: Path, token: str, namespace: Path | None = None) -> tuple[Path, Path] | None:
    if not _TOKEN.fullmatch(token):
        return None
    output_base = root / "Output"
    output_root = namespace or (output_base / "Speech")
    transcript = output_root / f"whisper_{token}.json"
    srt = transcript.with_suffix(".srt")
    try:
        root_resolved = root.resolve(strict=True)
        base_resolved = output_base.resolve(strict=True)
        resolved_root = output_root.resolve(strict=True)
        transcript_resolved = transcript.resolve(strict=True)
        srt_resolved = srt.resolve(strict=True)
        base_resolved.relative_to(root_resolved)
        resolved_root.relative_to(root_resolved)
        transcript_resolved.relative_to(resolved_root)
        srt_resolved.relative_to(resolved_root)
    except (OSError, RuntimeError, ValueError):
        return None
    if output_base.parent.resolve(strict=False) != root_resolved:
        return None
    if namespace is None and output_root.parent.resolve(strict=False) != base_resolved:
        return None
    if any(_is_reparse(item) for item in (root, output_base, output_root, transcript, srt)) or not transcript.is_file() or not srt.is_file():
        return None
    return transcript, srt


def _source_artifact(payload: object) -> Path | None:
    """Resolve one server-owned audio/video artifact without accepting paths."""

    if not isinstance(payload, dict):
        return None
    if any(
        key in payload and payload[key] not in (None, "", [])
        for key in _PATH_LIKE_FIELDS
    ):
        return None
    artifact_id = payload.get("source_artifact_id")
    if not isinstance(artifact_id, str) or _ARTIFACT_ID.fullmatch(artifact_id) is None:
        return None
    try:
        source = resolve(artifact_id)
        metadata = describe(artifact_id)
    except Exception:
        return None
    media_type = str(metadata.get("media_type") or "").casefold() if isinstance(metadata, dict) else ""
    if (
        not isinstance(source, Path)
        or not source.is_file()
        or _is_reparse(source)
        or not (media_type.startswith("audio/") or media_type.startswith("video/"))
    ):
        return None
    return source


def _invalid_source_artifact() -> dict[str, Any]:
    return {
        "status": "error",
        "code": "input_artifact_invalid",
        "error": "Whisper yêu cầu source_artifact_id của Hub và media audio/video hợp lệ.",
    }


def transcribe(payload: dict[str, Any], context: ProcessOwner | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return _invalid_source_artifact()
    source = _source_artifact(payload)
    if source is None:
        return _invalid_source_artifact()
    namespace = server_output_namespace(context, "whisper")
    if requires_server_output_namespace(context) and namespace is None:
        return unavailable("whisper", "Hub không tạo được output namespace an toàn cho transcript.", code="output_scope_unavailable")
    python, wrapper, root = _runtime()
    model_id = _model_id()
    if python is None or not python.is_file() or _is_reparse(python) or not wrapper.is_file() or _is_reparse(wrapper) or model_id is None:
        return unavailable("whisper", "Faster-Whisper runtime hoặc local model registry chưa sẵn sàng.")
    token = uuid.uuid4().hex
    request = {
        "path": str(source),
        "start": payload.get("start", 0),
        "end": payload.get("end", 10),
        "device": payload.get("device", "cpu"),
        "language": payload.get("language", "auto"),
        "timeout_seconds": _timeout(payload.get("timeout_seconds", _MAX_TIMEOUT_SECONDS)),
        "output_namespace": str(namespace) if namespace is not None else None,
    }
    result = run_json_worker(
        [str(python), str(wrapper)],
        request,
        label="whisper",
        cwd=wrapper.parent,
        env={
            **os.environ,
            "LOCALAIHUB_ROOT": str(root),
            "WHISPER_PYTHON": str(python),
            "WHISPER_MODEL_ID": model_id,
            "PYTHONPATH": str(local_root()),
            "PYTHONIOENCODING": "utf-8",
        },
        owner=context,
        timeout_seconds=_timeout(payload.get("timeout_seconds", _MAX_TIMEOUT_SECONDS)),
    )
    if result.get("status") != "completed" or result.get("operation") != "transcribe_media":
        return {
            "status": "error",
            "code": "transcription_failed",
            "error": "Faster-Whisper không hoàn tất transcript Hub.",
            "next_action": "Kiểm tra local model registry, environment Faster-Whisper và thử lại bằng một job mới.",
        }
    outputs = _outputs(root, str(result.get("transcript_token") or ""), namespace)
    if outputs is None:
        return {
            "status": "error",
            "code": "transcript_artifact_unavailable",
            "error": "Faster-Whisper không tạo artifact transcript hợp lệ.",
        }
    transcript, srt = outputs
    segment_count = result.get("segment_count")
    output_result = {
        "status": "completed",
        "operation": "transcribe_media",
        "files": [str(transcript), str(srt)],
        "segment_count": int(segment_count) if isinstance(segment_count, int) and segment_count >= 0 else 0,
        "device": result.get("device") if result.get("device") in {"cpu", "cuda"} else "cpu",
    }
    return output_result if attest_worker_output_paths(output_result, context, ("files",)) else unavailable("whisper", "Producer output ownership could not be attested.", code="output_scope_unavailable")


def capability() -> dict[str, Any]:
    python, wrapper, _root = _runtime()
    model_id = _model_id()
    return {
        "component": "whisper",
        "adapter_status": "first-party-job-worker",
        "runtime_ready": wrapper.is_file() and not _is_reparse(wrapper),
        "environment_ready": bool(python and python.is_file() and not _is_reparse(python)),
        "model_registry_ready": model_id is not None,
    }
