from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from .common import configured_path, local_cache_root, unavailable


ROOT = Path(__file__).resolve().parents[1]


def _runtime() -> tuple[Path, Path, Path]:
    service = configured_path("qwen3_tts", "path", "QWEN3_TTS_HOME") or ROOT / "Services" / "Qwen3-TTS"
    python = configured_path("qwen3_tts", "executable", "QWEN3_TTS_PYTHON") or ROOT / "Environments" / "voice-qwen" / "Scripts" / "python.exe"
    return python, service / "qwen_cli.py", service


def synthesize(payload: dict) -> dict:
    python, helper, service = _runtime()
    if not python.exists() or not helper.exists():
        return unavailable("qwen3_tts", "Qwen3-TTS helper environment is incomplete.")
    try:
        result = subprocess.run(
            [str(python), str(helper)],
            input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            capture_output=True,
            timeout=900,
            check=False,
            env={
                **os.environ,
                "LOCALAIHUB_ROOT": str(ROOT),
                "QWEN3_TTS_HOME": str(service),
                "HF_HOME": str(local_cache_root() / "HuggingFace"),
                "HF_HUB_CACHE": str(local_cache_root() / "HuggingFace" / "hub"),
                "PYTHONIOENCODING": "utf-8",
            },
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "error", "error": str(exc)}
    if result.returncode != 0:
        return {
            "status": "error",
            "error": result.stderr.decode("utf-8", errors="replace")[-6000:],
            "stdout": result.stdout.decode("utf-8", errors="replace")[-2000:],
            "returncode": result.returncode,
        }
    stdout = result.stdout.decode("utf-8", errors="replace")
    json_line = next((line for line in reversed(stdout.splitlines()) if line.lstrip().startswith("{")), "")
    try:
        return json.loads(json_line)
    except json.JSONDecodeError as exc:
        return {"status": "error", "error": f"Qwen3-TTS helper returned invalid JSON: {exc}", "stdout": stdout[-3000:]}


def capability() -> dict:
    python, helper, _ = _runtime()
    return {"component": "qwen3_tts", "status": "installed", "helper": str(helper), "environment": str(python)}
