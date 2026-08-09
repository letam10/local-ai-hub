from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from src.services.process_manager.managed import ProcessOwner, run_json_worker
from src.shared.utils.adapter_common import configured_path, local_root, unavailable


def _runtime() -> tuple[Path | None, Path | None, Path]:
    root = local_root()
    python = configured_path("whisper", "executable", "WHISPER_PYTHON")
    service = configured_path("whisper", "path", "WHISPER_HOME")
    return python, service, root / "Services" / "Whisper" / "whisper_cli.py"


def transcribe(payload: dict[str, Any], context: ProcessOwner | None = None) -> dict[str, Any]:
    python, whisper_home, helper = _runtime()
    source = Path(os.path.expandvars(str(payload.get("path", "")))).expanduser()
    if not source.is_file():
        return {"status": "error", "error": "Không tìm thấy media đầu vào Whisper."}
    if python is None or not python.exists() or not helper.exists():
        return unavailable("whisper", "Faster-Whisper environment hoặc Hub wrapper chưa hoàn chỉnh.")
    return run_json_worker(
        [str(python), str(helper)],
        {**payload, "path": str(source)},
        label="whisper",
        cwd=helper.parent,
        env={**os.environ, "LOCALAIHUB_ROOT": str(local_root()), "WHISPER_HOME": str(whisper_home) if whisper_home else "", "WHISPER_PYTHON": str(python), "PYTHONIOENCODING": "utf-8"},
        owner=context,
        timeout_seconds=float(payload.get("timeout_seconds", 1200)),
    )


def capability() -> dict[str, Any]:
    python, whisper_home, helper = _runtime()
    return {"component": "whisper", "adapter_status": "direct-worker-configured", "runtime_ready": helper.exists() and bool(whisper_home and whisper_home.exists()), "environment_ready": bool(python and python.exists())}
