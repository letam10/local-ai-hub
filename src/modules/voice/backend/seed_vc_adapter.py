from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from src.services.process_manager.managed import ProcessOwner, run_json_worker
from src.shared.utils.adapter_common import configured_path, local_cache_root, local_root, unavailable


def _runtime() -> tuple[Path, Path, Path]:
    root = local_root()
    service = configured_path("seed_vc", "path", "SEED_VC_HOME") or root / "Services" / "Seed-VC"
    python = configured_path("seed_vc", "executable", "SEED_VC_PYTHON") or root / "Environments" / "voice-seed" / "Scripts" / "python.exe"
    return python, service / "seed_cli.py", service


def convert(payload: dict[str, Any], context: ProcessOwner | None = None) -> dict[str, Any]:
    python, helper, service = _runtime()
    if not python.exists() or not helper.exists():
        return unavailable("seed_vc", "Seed-VC helper environment chưa hoàn chỉnh.")
    return run_json_worker(
        [str(python), str(helper)],
        payload,
        label="seed_vc",
        cwd=service,
        env={**os.environ, "LOCALAIHUB_ROOT": str(local_root()), "SEED_VC_HOME": str(service), "HF_HOME": str(local_cache_root() / "HuggingFace"), "HF_HUB_CACHE": str(local_cache_root() / "HuggingFace" / "hub"), "LOCALAIHUB_HF_HUB_CACHE": str(local_cache_root() / "HuggingFace" / "hub"), "PYTHONIOENCODING": "utf-8"},
        owner=context,
        timeout_seconds=float(payload.get("timeout_seconds", 1200)),
    )


def capability() -> dict[str, Any]:
    python, helper, _ = _runtime()
    return {"component": "seed_vc", "adapter_status": "direct-worker-configured", "runtime_ready": helper.exists(), "environment_ready": python.exists()}
