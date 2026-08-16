from __future__ import annotations

import os
from typing import Any

from src.services.process_manager.managed import ProcessOwner, run_json_worker
from src.shared.utils.adapter_common import (
    bounded_timeout,
    local_cache_root,
    local_root,
    normalize_worker_result,
    registered_model,
    registered_runtime,
    resolve_artifact_input,
    unavailable,
)


def _runtime():
    return registered_runtime("paddleocr_vl", "paddle_cli.py", executable_field="executable")


def parse(payload: dict[str, Any], context: ProcessOwner | None = None) -> dict[str, Any]:
    runtime = _runtime()
    source, input_error = resolve_artifact_input(payload, "source_artifact_id", "asset_id")
    model = registered_model("paddleocr_vl", "PaddleOCR-VL")
    if input_error:
        return {"status": "error", "component": "paddleocr_vl", "code": input_error, "error": "Chọn artifact Hub hợp lệ cho PaddleOCR-VL."}
    if runtime is None or model is None or not runtime[0].is_file() or not runtime[1].is_file() or not model[1].exists():
        return unavailable("paddleocr_vl", "PaddleOCR-VL helper environment chưa hoàn chỉnh.")
    python, helper, service = runtime
    result = run_json_worker(
        [str(python), str(helper)],
        {"path": str(source), "model_id": model[0], "output_format": str(payload.get("output_format") or "all")[:20]},
        label="paddleocr",
        cwd=service,
        env={**os.environ, "LOCALAIHUB_ROOT": str(local_root()), "PADDLEOCR_HOME": str(service), "PADDLE_PDX_CACHE_HOME": str(local_cache_root() / "PaddleX"), "FLAGS_use_cuda": "1", "PYTHONIOENCODING": "utf-8"},
        owner=context,
        timeout_seconds=bounded_timeout(payload.get("timeout_seconds"), 900, maximum=900),
    )
    return normalize_worker_result(result, component_id="paddleocr_vl", context=context, output_fields=("output", "files", "outputs"))


def capability() -> dict[str, Any]:
    runtime = _runtime()
    model = registered_model("paddleocr_vl", "PaddleOCR-VL")
    return {"component": "paddleocr_vl", "adapter_status": "direct-worker-configured", "runtime_ready": bool(runtime and runtime[1].is_file()), "environment_ready": bool(runtime and runtime[0].is_file()), "model_registry_ready": bool(model and model[1].exists()), "status": "partial"}
