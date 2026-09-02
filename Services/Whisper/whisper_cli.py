"""Hub-owned Faster-Whisper wrapper with a path-free worker receipt."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any


_MODEL_ID = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")
_LANGUAGE = re.compile(r"(?:auto|[a-z]{2,8}(?:-[a-z]{2,8})?)\Z")
_MAX_TIMEOUT_SECONDS = 1_200
_MAX_SEGMENTS = 10_000


def _response(status: str, **fields: object) -> dict[str, object]:
    return {"status": status, **fields}


def _is_reparse(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        return bool(getattr(os.lstat(path), "st_file_attributes", 0) & 0x400)
    except OSError:
        return True


def _local_root() -> Path | None:
    value = os.environ.get("LOCALAIHUB_ROOT", "")
    if not value:
        return None
    try:
        root = Path(value).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return root if root.is_dir() and not _is_reparse(root) else None


def _finite_number(value: object, *, default: float, minimum: float, maximum: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if result != result or result in {float("inf"), float("-inf")}:
        return default
    return max(minimum, min(maximum, result))


def _request(value: object) -> tuple[dict[str, object] | None, str | None]:
    if not isinstance(value, dict):
        return None, "invalid_request"
    source_value = value.get("path")
    if not isinstance(source_value, str) or not source_value:
        return None, "input_unavailable"
    source = Path(os.path.expandvars(source_value)).expanduser()
    if not source.is_file() or _is_reparse(source):
        return None, "input_unavailable"
    requested_device = value.get("device", "cpu")
    if not isinstance(requested_device, str) or requested_device.lower() not in {"cpu", "cuda"}:
        return None, "invalid_device"
    language = value.get("language", "auto")
    if not isinstance(language, str) or not _LANGUAGE.fullmatch(language.lower()):
        return None, "invalid_language"
    start = _finite_number(value.get("start"), default=0.0, minimum=0.0, maximum=86_400.0)
    end = _finite_number(value.get("end"), default=start + 10.0, minimum=start, maximum=86_400.0)
    if end < start:
        return None, "invalid_range"
    timeout = _finite_number(value.get("timeout_seconds"), default=1_200.0, minimum=1.0, maximum=_MAX_TIMEOUT_SECONDS)
    return {
        "source": source,
        "start": start,
        "end": end,
        "device": requested_device.lower(),
        "language": language.lower(),
        "timeout": int(timeout),
    }, None


def _device_allowed(device: str) -> bool:
    """Require the approved discrete device for Hub-launched heavy work."""

    if os.environ.get("LOCALAIHUB_REQUIRE_RTX4060") != "1":
        return device in {"cpu", "cuda"}
    if device != "cuda" or os.environ.get("LOCALAIHUB_FORCE_CPU") == "1":
        return False
    try:
        import torch

        if not torch.cuda.is_available():
            return False
        name = str(torch.cuda.get_device_name(0) or "").casefold()
    except (ImportError, AttributeError, RuntimeError, TypeError):
        return False
    return "nvidia" in name and re.search(r"\brtx[ -]?4060\b", name) is not None


def srt_time(seconds: float) -> str:
    millis = max(0, round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _write_srt(transcript: Path, target: Path) -> bool:
    try:
        value = json.loads(transcript.read_text(encoding="utf-8"))
        segments = value.get("segments", []) if isinstance(value, dict) else []
        if not isinstance(segments, list) or len(segments) > _MAX_SEGMENTS or target.exists() or target.is_symlink():
            return False
        lines: list[str] = []
        for index, segment in enumerate(segments, start=1):
            if not isinstance(segment, dict):
                return False
            start = _finite_number(segment.get("start"), default=-1.0, minimum=-1.0, maximum=86_400.0)
            end = _finite_number(segment.get("end"), default=-1.0, minimum=-1.0, maximum=86_400.0)
            text = segment.get("text")
            if start < 0 or end < start or not isinstance(text, str) or not text.strip():
                return False
            lines.extend((str(index), f"{srt_time(start)} --> {srt_time(end)}", " ".join(text.split()), ""))
        with target.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines))
        return True
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        return False


def handle_request(value: object) -> dict[str, object]:
    request, error = _request(value)
    if request is None:
        return _response("error", code=error or "invalid_request")
    if not _device_allowed(str(request["device"])):
        return _response("error", code="rtx4060_required")
    root = _local_root()
    python_value = os.environ.get("WHISPER_PYTHON", "")
    model_id = os.environ.get("WHISPER_MODEL_ID", "")
    worker = Path(__file__).with_name("transcribe_japanese_clip.py")
    if root is None or not python_value or not _MODEL_ID.fullmatch(model_id) or not worker.is_file() or _is_reparse(worker):
        return _response("error", code="runtime_unavailable")
    python = Path(python_value)
    if not python.is_file() or _is_reparse(python):
        return _response("error", code="runtime_unavailable")
    token = uuid.uuid4().hex
    transcript = root / "Output" / "Speech" / f"whisper_{token}.json"
    srt = transcript.with_suffix(".srt")
    command = [
        str(python), str(worker), str(request["source"]), token, str(request["start"]), str(request["end"]),
        str(request["device"]), model_id, str(request["language"]),
    ]
    try:
        result = subprocess.run(
            command,
            cwd=str(worker.parent),
            capture_output=True,
            timeout=int(request["timeout"]),
            check=False,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
    except (OSError, subprocess.SubprocessError):
        return _response("error", code="worker_unavailable")
    if result.returncode != 0 or not transcript.is_file() or transcript.is_symlink() or not _write_srt(transcript, srt):
        return _response("error", code="transcription_failed")
    worker_result: dict[str, Any] = {}
    for line in reversed(result.stdout.decode("utf-8", errors="replace").splitlines()):
        if line.lstrip().startswith("{"):
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                break
            if isinstance(parsed, dict):
                worker_result = parsed
            break
    if worker_result.get("status") != "completed" or worker_result.get("transcript_token") != token:
        return _response("error", code="worker_receipt_invalid")
    return _response(
        "completed",
        operation="transcribe_media",
        transcript_token=token,
        segment_count=int(worker_result.get("segment_count") or 0),
        device=str(command[6]),
        srt_available=True,
    )


def main() -> int:
    try:
        request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        request = None
    response = handle_request(request)
    print(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
    return 0 if response.get("status") == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
