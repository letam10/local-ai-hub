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
    reject_raw_worker_fields,
    resolve_artifact_input,
    unavailable,
)


def _runtime():
    return registered_runtime("seed_vc", "seed_cli.py", executable_field="executable")


def convert(payload: dict[str, Any], context: ProcessOwner | None = None) -> dict[str, Any]:
    raw_error = reject_raw_worker_fields(payload)
    runtime = _runtime()
    model = registered_model("seed_vc", "Seed-VC")
    if raw_error:
        return {"status": "error", "component": "seed_vc", "code": raw_error, "error": "Seed-VC chỉ nhận source/target artifact ID."}
    if runtime is None or model is None or not runtime[0].is_file() or not runtime[1].is_file() or not model[1].exists():
        return unavailable("seed_vc", "Seed-VC helper environment chưa hoàn chỉnh.")
    source, source_error = resolve_artifact_input(payload, "source_asset_id")
    target, target_error = resolve_artifact_input(payload, "target_asset_id", "reference_asset_id")
    if source_error or target_error:
        return {"status": "error", "component": "seed_vc", "code": source_error or target_error, "error": "Seed-VC cần source và target artifact Hub hợp lệ."}
    python, helper, service = runtime
    request = {key: value for key, value in payload.items() if key in {"diffusion_steps", "f0_condition", "auto_f0_adjust"}}
    request.update({"source": str(source), "target": str(target), "model_id": model[0]})
    result = run_json_worker(
        [str(python), str(helper)],
        request,
        label="seed_vc",
        cwd=service,
        env={**os.environ, "LOCALAIHUB_ROOT": str(local_root()), "SEED_VC_HOME": str(service), "HF_HOME": str(local_cache_root() / "HuggingFace"), "HF_HUB_CACHE": str(local_cache_root() / "HuggingFace" / "hub"), "LOCALAIHUB_HF_HUB_CACHE": str(local_cache_root() / "HuggingFace" / "hub"), "PYTHONIOENCODING": "utf-8"},
        owner=context,
        timeout_seconds=bounded_timeout(payload.get("timeout_seconds"), 1200, maximum=1200),
    )
    return normalize_worker_result(result, component_id="seed_vc", context=context, output_fields=("output", "audio", "files", "outputs"))


def capability() -> dict[str, Any]:
    runtime = _runtime()
    model = registered_model("seed_vc", "Seed-VC")
    return {"component": "seed_vc", "adapter_status": "direct-worker-configured", "runtime_ready": bool(runtime and runtime[1].is_file()), "environment_ready": bool(runtime and runtime[0].is_file()), "model_registry_ready": bool(model and model[1].exists()), "status": "partial"}
