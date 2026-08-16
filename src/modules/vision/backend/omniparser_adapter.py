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
    return registered_runtime("omniparser", "omni_cli.py", executable_field="executable")


def parse(payload: dict[str, Any], context: ProcessOwner | None = None) -> dict[str, Any]:
    runtime = _runtime()
    source, input_error = resolve_artifact_input(payload, "source_artifact_id", "asset_id")
    model = registered_model("omniparser", "OmniParser")
    if input_error:
        return {"status": "error", "component": "omniparser", "code": input_error, "error": "Chọn artifact Hub hợp lệ cho OmniParser."}
    if runtime is None:
        return unavailable("omniparser", "OmniParser canonical runtime hoặc omni_cli.py chưa được registry xác nhận.", code="runtime_contract_missing")
    if model is None:
        return unavailable("omniparser", "OmniParser tool model chưa có local payload hợp lệ trong Models canonical; không tự tải model.", code="tool_model_missing")
    if not runtime[0].is_file() or not runtime[1].is_file() or not model[1].exists():
        return unavailable("omniparser", "OmniParser runtime/model leaf không còn tồn tại sau khi registry được đọc.", code="runtime_leaf_missing")
    python, helper, service = runtime
    try:
        threshold = max(0.0, min(1.0, float(payload.get("box_threshold", 0.05))))
    except (TypeError, ValueError):
        threshold = 0.05
    result = run_json_worker(
        [str(python), str(helper)],
        {"path": str(source), "box_threshold": threshold, "model_id": model[0]},
        label="omniparser",
        cwd=service,
        env={**os.environ, "LOCALAIHUB_ROOT": str(local_root()), "OMNIPARSER_HOME": str(service), "HF_HOME": str(local_cache_root() / "HuggingFace"), "HF_HUB_CACHE": str(local_cache_root() / "HuggingFace" / "hub"), "EASYOCR_MODULE_PATH": str(local_cache_root() / "EasyOCR"), "PYTHONIOENCODING": "utf-8"},
        owner=context,
        timeout_seconds=bounded_timeout(payload.get("timeout_seconds"), 300, maximum=300),
    )
    return normalize_worker_result(result, component_id="omniparser", context=context, output_fields=("output", "files", "outputs"))


def capability() -> dict[str, Any]:
    runtime = _runtime()
    model = registered_model("omniparser", "OmniParser")
    return {
        "component": "omniparser",
        "adapter_status": "direct-worker-configured",
        "worker_contract": "omni_cli.py.v1",
        "worker_contract_status": "unverified_until_smoke",
        "timeout_seconds": 300,
        "runtime_ready": bool(runtime and runtime[1].is_file()),
        "environment_ready": bool(runtime and runtime[0].is_file()),
        "model_registry_ready": bool(model and model[1].exists()),
        "status": "partial",
        "reason": "OmniParser chỉ được nâng khỏi partial sau bounded worker smoke; timeout có mã worker_timeout riêng.",
        "next_action": "Chạy một ảnh nhỏ qua omni_cli.py với tài nguyên GPU đã kiểm tra.",
    }
