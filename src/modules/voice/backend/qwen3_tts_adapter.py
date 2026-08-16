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
    return registered_runtime("qwen3_tts", "qwen_cli.py", executable_field="executable")


def synthesize(payload: dict[str, Any], context: ProcessOwner | None = None) -> dict[str, Any]:
    raw_error = reject_raw_worker_fields(payload)
    runtime = _runtime()
    model = registered_model("qwen3_tts", "Qwen3-TTS")
    if raw_error:
        return {"status": "error", "component": "qwen3_tts", "code": raw_error, "error": "Voice worker chỉ nhận artifact ID và tham số văn bản an toàn."}
    if runtime is None or model is None or not runtime[0].is_file() or not runtime[1].is_file() or not model[1].exists():
        return unavailable("qwen3_tts", "Qwen3-TTS helper environment chưa hoàn chỉnh.")
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
    return {"component": "qwen3_tts", "adapter_status": "direct-worker-configured", "runtime_ready": bool(runtime and runtime[1].is_file()), "environment_ready": bool(runtime and runtime[0].is_file()), "model_registry_ready": bool(model and model[1].exists()), "status": "partial"}
