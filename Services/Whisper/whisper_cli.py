from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(
    os.path.expandvars(
        os.environ.get("LOCALAIHUB_ROOT") or os.environ.get("LOCAL_AI_HOME") or str(Path(__file__).resolve().parents[2])
    )
).expanduser()


def _configured_path(field: str, environment_variable: str) -> Path | None:
    value = os.environ.get(environment_variable, "")
    if not value:
        try:
            sys.path.insert(0, str(ROOT))
            from Hub.config import component

            value = str((component("whisper") or {}).get(field) or "")
        except (ImportError, OSError):
            value = ""
    return Path(os.path.expandvars(value)).expanduser() if value else None


def srt_time(seconds: float) -> str:
    millis = max(0, round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def main() -> int:
    try:
        request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": f"Invalid JSON request: {exc}"}, ensure_ascii=False))
        return 2
    source = Path(os.path.expandvars(str(request.get("path", "")))).expanduser()
    if not source.is_file():
        print(json.dumps({"status": "error", "error": f"Input file does not exist: {source}"}, ensure_ascii=False))
        return 2
    asr_root = _configured_path("path", "WHISPER_HOME")
    python = _configured_path("executable", "WHISPER_PYTHON")
    script = asr_root / "transcribe_japanese_clip.py" if asr_root else None
    if asr_root is None or python is None or script is None or not python.exists() or not script.exists():
        print(json.dumps({"status": "error", "error": "Existing Faster-Whisper environment or script is missing."}, ensure_ascii=False))
        return 2
    output_base = Path(os.path.expandvars(os.environ.get("LOCAL_AI_OUTPUT", str(ROOT / "Output")))).expanduser()
    output_root = output_base / "Speech"
    output_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    output = output_root / f"whisper_{stamp}.json"
    start = max(0.0, float(request.get("start", 0.0)))
    end = max(start + 0.1, float(request.get("end", start + 10.0)))
    requested_device = str(request.get("device", "cpu"))
    device = requested_device
    command = [str(python), str(script), str(source), str(output), str(start), str(end), device]
    try:
        result = subprocess.run(command, cwd=str(asr_root), capture_output=True, timeout=int(request.get("timeout_seconds", 1200)), check=False, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        fallback_reason = None
        if result.returncode != 0 and requested_device == "cuda" and "cublas64_12.dll" in result.stderr.decode("utf-8", errors="replace"):
            device = "cpu"
            command[-1] = device
            fallback_reason = "CUDA ASR unavailable because cublas64_12.dll could not be loaded; reused environment fell back to CPU int8."
            result = subprocess.run(command, cwd=str(asr_root), capture_output=True, timeout=int(request.get("timeout_seconds", 1200)), check=False, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    except (OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 2
    stdout = result.stdout.decode("utf-8", errors="replace")
    if result.returncode != 0 or not output.exists():
        print(json.dumps({
            "status": "error",
            "error": "Existing Faster-Whisper clip script failed.",
            "returncode": result.returncode,
            "stdout": stdout[-3000:],
            "stderr": result.stderr.decode("utf-8", errors="replace")[-5000:],
        }, ensure_ascii=False))
        return 2
    summary_line = next((line for line in reversed(stdout.splitlines()) if line.lstrip().startswith("{")), "{}")
    try:
        summary = json.loads(summary_line)
    except json.JSONDecodeError:
        summary = {}
    srt_output = output.with_suffix(".srt")
    try:
        transcript = json.loads(output.read_text(encoding="utf-8"))
        with srt_output.open("w", encoding="utf-8", newline="\n") as handle:
            for index, segment in enumerate(transcript.get("segments", []), start=1):
                handle.write(f"{index}\n{srt_time(float(segment['start']))} --> {srt_time(float(segment['end']))}\n{segment.get('text', '').strip()}\n\n")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        srt_output = None
    response = {
        "status": "completed",
        "operation": "transcribe_media",
        "input": str(source),
        "output": str(output),
        "srt": str(srt_output) if srt_output else None,
        "device": device,
        "requested_device": requested_device,
        "start": start,
        "end": end,
        **summary,
    }
    if fallback_reason:
        response["fallback_reason"] = fallback_reason
    print(json.dumps(response, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
