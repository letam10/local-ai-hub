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
    requires_server_output_namespace,
    server_output_namespace,
    unavailable,
)


def _runtime():
    return registered_runtime("qwen3_tts", "qwen_cli.py", executable_field="executable")


def synthesize(payload: dict[str, Any], context: ProcessOwner | None = None) -> dict[str, Any]:
    raw_error = reject_raw_worker_fields(payload)
    runtime = _runtime()
    model = registered_model("qwen3_tts", "Qwen3-TTS")
    if raw_error:
        return {"status": "error", "component": "qwen3_tts", "code": raw_error, "error": "Voice worker chỉ nhận artifact ID và tham số văn bản an toàn."}
    if runtime is None:
        return unavailable("qwen3_tts", "Qwen3-TTS canonical environment hoặc qwen_cli.py chưa được registry xác nhận.", code="runtime_contract_missing")
    if model is None:
        return unavailable("qwen3_tts", "Qwen3-TTS tool model còn thiếu hoặc không có payload local hợp lệ; không tự tải model.", code="tool_model_missing")
    if not runtime[0].is_file() or not runtime[1].is_file() or not model[1].exists():
        return unavailable("qwen3_tts", "Qwen3-TTS runtime/model leaf không còn tồn tại sau khi registry được đọc.", code="runtime_leaf_missing")
    python, helper, service = runtime
    text = str(payload.get("text") or "").strip()[:2_000]
    if not text:
        return {"status": "error", "component": "qwen3_tts", "code": "text_required", "error": "Nhập một câu ngắn cho Qwen3-TTS."}
    operation = str(payload.get("operation") or "text_to_speech")
    if operation not in {"text_to_speech", "design_voice", "clone_voice"}:
        return {"status": "error", "component": "qwen3_tts", "code": "operation_not_allowed", "error": "Thao tác Qwen3-TTS không nằm trong allowlist."}
    request = {key: value for key, value in payload.items() if key in {"language", "speaker", "instruct", "reference_text"}}
    request.update({"operation": operation, "text": text, "model_id": model[0]})
    if operation == "clone_voice":
        reference, input_error = resolve_artifact_input(payload, "reference_asset_id")
        if input_error:
            return {"status": "error", "component": "qwen3_tts", "code": input_error, "error": "Voice Clone cần reference artifact Hub hợp lệ."}
        request["reference_audio"] = str(reference)
    namespace = server_output_namespace(context, "qwen3-tts")
    if requires_server_output_namespace(context) and namespace is None:
        return unavailable("qwen3_tts", "Hub không tạo được output namespace an toàn cho Qwen3-TTS.", code="output_scope_unavailable")
    request["output_namespace"] = str(namespace) if namespace is not None else None
    result = run_json_worker(
        [str(python), str(helper)],
        request,
        label="qwen3_tts",
        cwd=service,
        env={**os.environ, "LOCALAIHUB_ROOT": str(local_root()), "QWEN3_TTS_HOME": str(service), "HF_HOME": str(local_cache_root() / "HuggingFace"), "HF_HUB_CACHE": str(local_cache_root() / "HuggingFace" / "hub"), "PYTHONIOENCODING": "utf-8"},
        owner=context,
        timeout_seconds=bounded_timeout(payload.get("timeout_seconds"), 900, maximum=900),
    )
    return normalize_worker_result(result, component_id="qwen3_tts", context=context, output_fields=("output", "audio", "files", "outputs"))


def capability() -> dict[str, Any]:
    runtime = _runtime()
    model = registered_model("qwen3_tts", "Qwen3-TTS")
    return {
        "component": "qwen3_tts",
        "adapter_status": "direct-worker-configured",
        "worker_contract": "qwen_cli.py.v1",
        "worker_contract_status": "unverified_until_smoke",
        "runtime_ready": bool(runtime and runtime[1].is_file()),
        "environment_ready": bool(runtime and runtime[0].is_file()),
        "model_registry_ready": bool(model and model[1].exists()),
        "status": "partial",
        "reason": "Qwen3-TTS chỉ tạo evidence operational sau bounded audio smoke; model registry không được suy ra từ example.",
        "next_action": "Xác minh qwen_cli.py và model local bằng một câu ngắn, không cài conversational LLM.",
    }
