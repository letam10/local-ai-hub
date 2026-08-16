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
    return registered_runtime("groundingdino", "ground_cli.py", executable_field="executable")


def ground(payload: dict[str, Any], context: ProcessOwner | None = None) -> dict[str, Any]:
    runtime = _runtime()
    source, input_error = resolve_artifact_input(payload, "source_artifact_id", "asset_id")
    model = registered_model("groundingdino", "Grounding DINO")
    if input_error:
        return {"status": "error", "component": "groundingdino", "code": input_error, "error": "Chọn artifact Hub hợp lệ cho Grounding DINO."}
    if runtime is None:
        return unavailable("groundingdino", "Grounding DINO canonical runtime hoặc ground_cli.py chưa được registry xác nhận.", code="runtime_contract_missing")
    if model is None:
        return unavailable("groundingdino", "Grounding DINO tool model chưa có local payload hợp lệ trong Models canonical; không tự tải model.", code="tool_model_missing")
    if not runtime[0].is_file() or not runtime[1].is_file() or not model[1].exists():
        return unavailable("groundingdino", "Grounding DINO runtime/model leaf không còn tồn tại sau khi registry được đọc.", code="runtime_leaf_missing")
    python, helper, service = runtime
    prompt = str(payload.get("prompt") or "").strip()[:300]
    if not prompt:
        return {"status": "error", "component": "groundingdino", "code": "prompt_required", "error": "Nhập prompt Grounding DINO ngắn."}
    try:
        box_threshold = max(0.0, min(1.0, float(payload.get("box_threshold", 0.35))))
    except (TypeError, ValueError):
        box_threshold = 0.35
    try:
        text_threshold = max(0.0, min(1.0, float(payload.get("text_threshold", 0.25))))
    except (TypeError, ValueError):
        text_threshold = 0.25
    result = run_json_worker(
        [str(python), str(helper)],
        {"path": str(source), "prompt": prompt, "box_threshold": box_threshold, "text_threshold": text_threshold, "model_id": model[0]},
        label="groundingdino",
        cwd=service,
        env={**os.environ, "LOCALAIHUB_ROOT": str(local_root()), "GROUNDINGDINO_HOME": str(service), "PYTHONIOENCODING": "utf-8"},
        owner=context,
        timeout_seconds=bounded_timeout(payload.get("timeout_seconds"), 300, maximum=300),
    )
    return normalize_worker_result(result, component_id="groundingdino", context=context, output_fields=("output", "files", "outputs"))


def capability() -> dict[str, Any]:
    runtime = _runtime()
    model = registered_model("groundingdino", "Grounding DINO")
    return {
        "component": "groundingdino",
        "adapter_status": "direct-worker-configured",
        "worker_contract": "ground_cli.py.v1",
        "worker_contract_status": "unverified_until_smoke",
        "timeout_seconds": 300,
        "runtime_ready": bool(runtime and runtime[1].is_file()),
        "environment_ready": bool(runtime and runtime[0].is_file()),
        "model_registry_ready": bool(model and model[1].exists()),
        "status": "partial",
        "reason": "Grounding DINO chỉ là evidence partial cho tới khi có bounded boxes smoke.",
        "next_action": "Chạy một prompt ngắn trên ảnh nhỏ sau khi kiểm tra tài nguyên.",
    }
