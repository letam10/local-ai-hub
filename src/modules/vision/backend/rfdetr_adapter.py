from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from src.services.process_manager.managed import ProcessOwner, run_json_worker
from src.shared.utils.adapter_common import configured_path, local_root, unavailable


def _runtime() -> tuple[Path, Path, Path]:
    root = local_root()
    service = configured_path("rfdetr", "path", "RFDETR_HOME") or root / "Services" / "RF-DETR"
    python = configured_path("rfdetr", "executable", "RFDETR_PYTHON") or root / "Environments" / "vision-torch" / "Scripts" / "python.exe"
    return python, service / "detect_cli.py", service


def detect(path: str, threshold: float = 0.5, context: ProcessOwner | None = None) -> dict[str, Any]:
    python, helper, service = _runtime()
    source = Path(os.path.expandvars(path)).expanduser()
    if not source.is_file():
        return {"status": "error", "error": "Không tìm thấy ảnh/video RF-DETR đầu vào."}
    if not python.exists() or not helper.exists():
        return unavailable("rfdetr", "RF-DETR helper environment chưa hoàn chỉnh.")
    return run_json_worker(
        [str(python), str(helper)],
        {"path": str(source), "threshold": float(threshold)},
        label="rfdetr",
        cwd=service,
        env={**os.environ, "LOCALAIHUB_ROOT": str(local_root()), "RF_HOME": str(local_root() / "Models" / "Vision" / "RF-DETR"), "PYTHONIOENCODING": "utf-8"},
        owner=context,
        timeout_seconds=300,
    )


def capability() -> dict[str, Any]:
    python, helper, _ = _runtime()
    return {"component": "rfdetr", "adapter_status": "direct-worker-configured", "runtime_ready": helper.exists(), "environment_ready": python.exists()}
