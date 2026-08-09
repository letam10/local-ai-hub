from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from src.shared.utils.adapter_common import configured_path, unavailable


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "Services" / "Whisper" / "whisper_cli.py"


def transcribe(payload: dict) -> dict:
    existing_python = configured_path("whisper", "executable", "WHISPER_PYTHON")
    whisper_home = configured_path("whisper", "path", "WHISPER_HOME")
    if existing_python is None or not existing_python.exists() or not HELPER.exists():
        return unavailable("whisper", "Existing Faster-Whisper environment or Hub wrapper is incomplete.")
    try:
        result = subprocess.run(
            [str(existing_python), str(HELPER)],
            input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            capture_output=True,
            timeout=1200,
            check=False,
            env={
                **os.environ,
                "LOCALAIHUB_ROOT": str(ROOT),
                "WHISPER_HOME": str(whisper_home) if whisper_home else "",
                "WHISPER_PYTHON": str(existing_python),
                "PYTHONIOENCODING": "utf-8",
            },
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "error", "error": str(exc)}
    stdout = result.stdout.decode("utf-8", errors="replace")
    json_line = next((line for line in reversed(stdout.splitlines()) if line.lstrip().startswith("{")), "")
    try:
        value = json.loads(json_line)
    except json.JSONDecodeError as exc:
        return {"status": "error", "error": f"Whisper helper returned invalid JSON: {exc}", "stdout": stdout[-3000:]}
    if result.returncode != 0:
        value.setdefault("status", "error")
    return value


def capability() -> dict:
    whisper_home = configured_path("whisper", "path", "WHISPER_HOME")
    return {"component": "whisper", "status": "installed", "helper": str(HELPER), "environment": str(whisper_home) if whisper_home else None}
