from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from src.services.process_manager.managed import ProcessOwner, run_json_worker
from src.shared.utils.adapter_common import configured_path, local_root, unavailable


def _runtime() -> tuple[Path, Path, Path]:
    root = local_root()
    service = configured_path("groundingdino", "path", "GROUNDINGDINO_HOME") or root / "Services" / "GroundingDINO"
    python = configured_path("groundingdino", "executable", "GROUNDINGDINO_PYTHON") or root / "Environments" / "groundingdino" / "Scripts" / "python.exe"
    return python, service / "ground_cli.py", service


def ground(path: str, prompt: str, box_threshold: float = 0.35, text_threshold: float = 0.25, context: ProcessOwner | None = None) -> dict[str, Any]:
    python, helper, service = _runtime()
    source = Path(os.path.expandvars(path)).expanduser()
    if not source.is_file():
        return {"status": "error", "error": "Không tìm thấy ảnh Grounding DINO đầu vào."}
    if not python.exists() or not helper.exists():
        return unavailable("groundingdino", "Grounding DINO helper environment chưa hoàn chỉnh.")
    return run_json_worker(
        [str(python), str(helper)],
        {"path": str(source), "prompt": prompt, "box_threshold": float(box_threshold), "text_threshold": float(text_threshold)},
        label="groundingdino",
        cwd=service,
        env={**os.environ, "LOCALAIHUB_ROOT": str(local_root()), "GROUNDINGDINO_HOME": str(service), "PYTHONIOENCODING": "utf-8"},
        owner=context,
        timeout_seconds=300,
    )


def capability() -> dict[str, Any]:
    python, helper, _ = _runtime()
    return {"component": "groundingdino", "adapter_status": "direct-worker-configured", "runtime_ready": helper.exists(), "environment_ready": python.exists()}
