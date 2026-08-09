from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from src.services.process_manager.managed import ProcessOwner, run_json_worker
from src.shared.utils.adapter_common import configured_path, local_cache_root, local_root, unavailable


def _runtime() -> tuple[Path, Path, Path]:
    root = local_root()
    service = configured_path("paddleocr_vl", "path", "PADDLEOCR_HOME") or root / "Services" / "PaddleOCR"
    python = configured_path("paddleocr_vl", "executable", "PADDLEOCR_PYTHON") or root / "Environments" / "paddle-ocr" / "Scripts" / "python.exe"
    return python, service / "paddle_cli.py", service


def parse(path: str, context: ProcessOwner | None = None) -> dict[str, Any]:
    python, helper, service = _runtime()
    source = Path(os.path.expandvars(path)).expanduser()
    if not source.is_file():
        return {"status": "error", "error": "Không tìm thấy tệp OCR đầu vào."}
    if not python.exists() or not helper.exists():
        return unavailable("paddleocr_vl", "PaddleOCR-VL helper environment chưa hoàn chỉnh.")
    return run_json_worker(
        [str(python), str(helper)],
        {"path": str(source)},
        label="paddleocr",
        cwd=service,
        env={**os.environ, "LOCALAIHUB_ROOT": str(local_root()), "PADDLEOCR_HOME": str(service), "PADDLE_PDX_CACHE_HOME": str(local_cache_root() / "PaddleX"), "FLAGS_use_cuda": "1", "PYTHONIOENCODING": "utf-8"},
        owner=context,
        timeout_seconds=900,
    )


def capability() -> dict[str, Any]:
    python, helper, _ = _runtime()
    return {"component": "paddleocr_vl", "adapter_status": "direct-worker-configured", "runtime_ready": helper.exists(), "environment_ready": python.exists()}
