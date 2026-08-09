from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from src.services.process_manager.managed import ProcessOwner, run_json_worker
from src.shared.utils.adapter_common import configured_path, local_cache_root, local_root, unavailable


def _runtime() -> tuple[Path, Path, Path]:
    root = local_root()
    service = configured_path("qwen3_tts", "path", "QWEN3_TTS_HOME") or root / "Services" / "Qwen3-TTS"
    python = configured_path("qwen3_tts", "executable", "QWEN3_TTS_PYTHON") or root / "Environments" / "voice-qwen" / "Scripts" / "python.exe"
    return python, service / "qwen_cli.py", service


def synthesize(payload: dict[str, Any], context: ProcessOwner | None = None) -> dict[str, Any]:
    python, helper, service = _runtime()
    if not python.exists() or not helper.exists():
        return unavailable("qwen3_tts", "Qwen3-TTS helper environment chưa hoàn chỉnh.")
    return run_json_worker(
        [str(python), str(helper)],
        payload,
        label="qwen3_tts",
        cwd=service,
        env={**os.environ, "LOCALAIHUB_ROOT": str(local_root()), "QWEN3_TTS_HOME": str(service), "HF_HOME": str(local_cache_root() / "HuggingFace"), "HF_HUB_CACHE": str(local_cache_root() / "HuggingFace" / "hub"), "PYTHONIOENCODING": "utf-8"},
        owner=context,
        timeout_seconds=float(payload.get("timeout_seconds", 900)),
    )


def capability() -> dict[str, Any]:
    python, helper, _ = _runtime()
    return {"component": "qwen3_tts", "adapter_status": "direct-worker-configured", "runtime_ready": helper.exists(), "environment_ready": python.exists()}
