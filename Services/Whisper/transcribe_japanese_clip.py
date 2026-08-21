"""Bounded Faster-Whisper worker used only by the Hub-owned wrapper.

The worker accepts a server-selected model ID and emits a small, path-free
receipt.  The wrapper owns input/output paths and converts the transcript into
an SRT artifact; clients never select a model path or command.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any


_MODEL_ID = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")
_TOKEN = re.compile(r"[a-f0-9]{32}\Z")
_LANGUAGE = re.compile(r"(?:auto|[a-z]{2,8}(?:-[a-z]{2,8})?)\Z")
_MAX_REGISTRY_BYTES = 512 * 1024
_MAX_SEGMENTS = 10_000
_MAX_SEGMENT_TEXT = 4_000


def _result(status: str, **fields: object) -> int:
    print(json.dumps({"status": status, **fields}, ensure_ascii=False, separators=(",", ":")))
    return 0 if status == "completed" else 2


def _is_reparse(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        return bool(getattr(os.lstat(path), "st_file_attributes", 0) & 0x400)
    except OSError:
        return True


def _inside(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _output_target(root: Path, token: str) -> tuple[Path, Path] | None:
    """Validate the complete Hub-owned output chain without following a reparse."""

    if not _TOKEN.fullmatch(token):
        return None
    output_base = root / "Output"
    output_root = output_base / "Speech"
    output = output_root / f"whisper_{token}.json"
    try:
        root_resolved = root.resolve(strict=True)
        base_resolved = output_base.resolve(strict=False)
        speech_resolved = output_root.resolve(strict=False)
        output_parent = output.parent.resolve(strict=False)
        base_resolved.relative_to(root_resolved)
        speech_resolved.relative_to(root_resolved)
        output_parent.relative_to(root_resolved)
    except (OSError, RuntimeError, ValueError):
        return None
    if output_base.parent.resolve(strict=False) != root_resolved or output_root.parent.resolve(strict=False) != base_resolved:
        return None
    for item in (root, output_base, output_root, output):
        if (item.exists() or item.is_symlink()) and _is_reparse(item):
            return None
    if output.exists() or output.is_symlink():
        return None
    return output_root, output


def _requested_output_target(root: Path, token: str, requested: object) -> tuple[Path, Path] | None:
    if not _TOKEN.fullmatch(token) or not isinstance(requested, str) or not requested:
        return None
    try:
        output_base = (root / "Output").resolve(strict=True)
        output = Path(requested).expanduser().resolve(strict=False)
        output_root = output.parent.resolve(strict=True)
        output_root.relative_to(output_base)
        if not output_root.is_dir() or _is_reparse(output_root):
            return None
        if output.name != f"whisper_{token}.json" or output.exists() or output.is_symlink() or _is_reparse(output):
            return None
        return output_root, output
    except (OSError, RuntimeError, ValueError):
        return None


def _local_root() -> Path | None:
    value = os.environ.get("LOCALAIHUB_ROOT", "")
    if not value:
        return None
    try:
        root = Path(value).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return root if root.is_dir() and not _is_reparse(root) else None


def _model_snapshot(root: Path, model_id: str) -> Path | None:
    if not _MODEL_ID.fullmatch(model_id):
        return None
    config_root = root / "Config"
    models_root = root / "Models"
    registry = config_root / "model_registry.json"
    if any(_is_reparse(item) for item in (config_root, models_root, registry)):
        return None
    try:
        if registry.stat().st_size > _MAX_REGISTRY_BYTES:
            return None
        data = json.loads(registry.read_text(encoding="utf-8"))
        rows = data.get("models", []) if isinstance(data, dict) else []
    except (OSError, UnicodeError, ValueError, TypeError):
        return None
    if not isinstance(rows, list):
        return None
    matches = [row for row in rows if isinstance(row, dict) and row.get("id") == model_id]
    if len(matches) != 1 or matches[0].get("engine") != "Faster-Whisper":
        return None
    value = matches[0].get("local_path")
    if not isinstance(value, str) or not value or "\x00" in value:
        return None
    try:
        raw = Path(os.path.expandvars(value)).expanduser()
        candidate = raw.resolve(strict=True)
        root_resolved = models_root.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    model_file = candidate / "model.bin"
    if not _inside(candidate, root_resolved) or any(_is_reparse(item) for item in (raw, candidate, model_file)):
        return None
    return candidate if candidate.is_dir() and model_file.is_file() else None


def _normalise_timestamp(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0 or number != number or number in {float("inf"), float("-inf")}:
        return None
    return round(number, 3)


def _segments(source: Any, *, start: float, end: float) -> list[dict[str, object]] | None:
    result: list[dict[str, object]] = []
    try:
        iterator = iter(source)
    except TypeError:
        return None
    for item in iterator:
        if len(result) >= _MAX_SEGMENTS:
            return None
        segment_start = _normalise_timestamp(getattr(item, "start", None))
        segment_end = _normalise_timestamp(getattr(item, "end", None))
        text = getattr(item, "text", "")
        if segment_start is None or segment_end is None or not isinstance(text, str):
            return None
        segment_start = max(start, segment_start)
        segment_end = min(end, segment_end)
        normalized = " ".join(text.split())[:_MAX_SEGMENT_TEXT]
        if not normalized:
            continue
        if segment_end < segment_start:
            return None
        result.append({"start": segment_start, "end": segment_end, "text": normalized})
    return result


def main(argv: list[str]) -> int:
    if len(argv) not in {8, 9}:
        return _result("error", code="invalid_request")
    root = _local_root()
    source = Path(argv[1])
    token, model_id, language = argv[2], argv[6], argv[7].lower()
    if root is None or not _TOKEN.fullmatch(token) or not _MODEL_ID.fullmatch(model_id) or not _LANGUAGE.fullmatch(language):
        return _result("error", code="invalid_request")
    try:
        start = max(0.0, float(argv[3]))
        end = max(start, float(argv[4]))
    except ValueError:
        return _result("error", code="invalid_range")
    device = argv[5].lower()
    if device not in {"cpu", "cuda"}:
        return _result("error", code="invalid_device")
    if not source.is_file() or _is_reparse(source):
        return _result("error", code="input_unavailable")
    model_path = _model_snapshot(root, model_id)
    if model_path is None:
        return _result("error", code="tool_model_unavailable")
    targets = _requested_output_target(root, token, argv[8]) if len(argv) == 9 else _output_target(root, token)
    if targets is None:
        return _result("error", code="output_unavailable")
    output_root, output = targets
    try:
        from faster_whisper import WhisperModel

        compute_type = "int8_float16" if device == "cuda" else "int8"
        model = WhisperModel(str(model_path), device=device, compute_type=compute_type)
        raw_segments, info = model.transcribe(
            str(source),
            clip_timestamps=f"{start},{end}",
            language=None if language == "auto" else language,
        )
        segments = _segments(raw_segments, start=start, end=end)
        if segments is None:
            return _result("error", code="transcript_invalid")
        output_root.mkdir(parents=True, exist_ok=True)
        targets = _output_target(root, token)
        if targets is None:
            return _result("error", code="output_unavailable")
        _output_root, output = targets
        payload = {
            "schema_version": "localaihub-transcript.v2",
            "language": getattr(info, "language", None) if isinstance(getattr(info, "language", None), str) else None,
            "segments": segments,
            "text": " ".join(str(item["text"]) for item in segments),
        }
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return _result("error", code="transcription_failed")
    return _result(
        "completed",
        operation="transcribe_media",
        transcript_token=token,
        segment_count=len(segments),
        language=payload["language"],
        device=device,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
