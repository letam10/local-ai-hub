from __future__ import annotations

import os
from typing import Any

from src.services.process_manager.managed import ProcessOwner, run_json_worker
from src.shared.utils.adapter_common import (
    bounded_timeout,
    local_root,
    normalize_worker_result,
    registered_model,
    registered_runtime,
    resolve_artifact_input,
    unavailable,
)


def _runtime():
    return registered_runtime("rfdetr", "detect_cli.py", executable_field="executable")


def detect(payload: dict[str, Any], context: ProcessOwner | None = None) -> dict[str, Any]:
    runtime = _runtime()
    source, input_error = resolve_artifact_input(payload, "source_artifact_id", "asset_id")
    model = registered_model("rfdetr", "RF-DETR")
    if input_error:
        return {"status": "error", "component": "rfdetr", "code": input_error, "error": "Chọn artifact Hub hợp lệ cho RF-DETR."}
    if runtime is None:
        return unavailable("rfdetr", "RF-DETR canonical runtime hoặc detect_cli.py chưa được registry xác nhận.", code="runtime_contract_missing")
    if model is None:
        return unavailable("rfdetr", "RF-DETR tool model chưa có local payload hợp lệ trong Models canonical; không tự tải model.", code="tool_model_missing")
    if not runtime[0].is_file() or not runtime[1].is_file() or not model[1].exists():
        return unavailable("rfdetr", "RF-DETR runtime/model leaf không còn tồn tại sau khi registry được đọc.", code="runtime_leaf_missing")
    python, helper, service = runtime
    try:
        threshold = max(0.0, min(1.0, float(payload.get("threshold", 0.5))))
    except (TypeError, ValueError):
        threshold = 0.5
    result = run_json_worker(
        [str(python), str(helper)],
        {"path": str(source), "threshold": threshold, "model_id": model[0]},
        label="rfdetr",
        cwd=service,
        env={**os.environ, "LOCALAIHUB_ROOT": str(local_root()), "RF_HOME": str(local_root() / "Models" / "Vision" / "RF-DETR"), "PYTHONIOENCODING": "utf-8"},
        owner=context,
        timeout_seconds=bounded_timeout(payload.get("timeout_seconds"), 300, maximum=300),
    )
    return normalize_worker_result(result, component_id="rfdetr", context=context, output_fields=("output", "files", "outputs"))


def capability() -> dict[str, Any]:
    runtime = _runtime()
    model = registered_model("rfdetr", "RF-DETR")
    return {
        "component": "rfdetr",
        "adapter_status": "direct-worker-configured",
        "worker_contract": "detect_cli.py.v1",
        "worker_contract_status": "unverified_until_smoke",
        "installed_api": "not_imported",
        "runtime_ready": bool(runtime and runtime[1].is_file()),
        "environment_ready": bool(runtime and runtime[0].is_file()),
        "model_registry_ready": bool(model and model[1].exists()),
        "status": "partial",
        "reason": "RF-DETR upstream Python API không được suy đoán; Hub chỉ chấp nhận helper detect_cli.py đã bind và vẫn cần smoke.",
        "next_action": "Xác minh detect_cli.py tương thích RF-DETR bằng một input nhỏ, không benchmark.",
    }
