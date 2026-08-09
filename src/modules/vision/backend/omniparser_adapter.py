from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from src.services.process_manager.managed import ProcessOwner, run_json_worker
from src.shared.utils.adapter_common import configured_path, local_cache_root, local_root, unavailable


def _runtime() -> tuple[Path, Path, Path]:
    root = local_root()
    service = configured_path("omniparser", "path", "OMNIPARSER_HOME") or root / "Services" / "OmniParser"
    python = configured_path("omniparser", "executable", "OMNIPARSER_PYTHON") or root / "Environments" / "omniparser" / "Scripts" / "python.exe"
    return python, service / "omni_cli.py", service


def parse(path: str, box_threshold: float = 0.05, context: ProcessOwner | None = None) -> dict[str, Any]:
    python, helper, service = _runtime()
    source = Path(os.path.expandvars(path)).expanduser()
    if not source.is_file():
        return {"status": "error", "error": "Không tìm thấy ảnh OmniParser đầu vào."}
    if not python.exists() or not helper.exists():
        return unavailable("omniparser", "OmniParser helper environment chưa hoàn chỉnh.")
    return run_json_worker(
        [str(python), str(helper)],
        {"path": str(source), "box_threshold": float(box_threshold)},
        label="omniparser",
        cwd=service,
        env={**os.environ, "LOCALAIHUB_ROOT": str(local_root()), "OMNIPARSER_HOME": str(service), "HF_HOME": str(local_cache_root() / "HuggingFace"), "HF_HUB_CACHE": str(local_cache_root() / "HuggingFace" / "hub"), "EASYOCR_MODULE_PATH": str(local_cache_root() / "EasyOCR"), "PYTHONIOENCODING": "utf-8"},
        owner=context,
        timeout_seconds=300,
    )


def capability() -> dict[str, Any]:
    python, helper, _ = _runtime()
    return {"component": "omniparser", "adapter_status": "direct-worker-configured", "runtime_ready": helper.exists(), "environment_ready": python.exists()}
