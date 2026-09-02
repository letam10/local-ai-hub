from __future__ import annotations

import os
from typing import Any

from src.services.artifact_store import describe
from src.services.process_manager.managed import ProcessOwner, run_json_worker
from src.shared.schemas.ocr_whisper import OcrWhisperContractError, build_ocr_result
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
    source_artifact_id = payload.get("source_artifact_id") or payload.get("asset_id") if isinstance(payload, dict) else None
    model = registered_model("paddleocr_vl", "PaddleOCR-VL")
    if input_error:
        return {"status": "error", "component": "paddleocr_vl", "code": input_error, "error": "Chọn artifact Hub hợp lệ cho PaddleOCR-VL."}
    metadata = describe(source_artifact_id) if isinstance(source_artifact_id, str) else None
    media_type = str(metadata.get("media_type") or "").casefold() if isinstance(metadata, dict) else ""
    if media_type and not (media_type.startswith("image/") or media_type == "application/pdf"):
        return {"status": "error", "component": "paddleocr_vl", "code": "input_media_type_invalid", "error": "PaddleOCR-VL chỉ nhận artifact ảnh hoặc PDF."}
    if runtime is None:
        return unavailable("paddleocr_vl", "PaddleOCR-VL canonical runtime hoặc paddle_cli.py chưa được registry xác nhận.", code="runtime_contract_missing")
    if model is None:
        return unavailable("paddleocr_vl", "Tool model PaddleOCR-VL còn thiếu hoặc không có payload local hợp lệ; package này không tải model.", code="tool_model_missing")
    if not runtime[0].is_file() or not runtime[1].is_file() or not model[1].exists():
        return unavailable("paddleocr_vl", "PaddleOCR-VL runtime/model leaf không còn tồn tại sau khi registry được đọc.", code="runtime_leaf_missing")
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
    safe = normalize_worker_result(result, component_id="paddleocr_vl", context=context, output_fields=("output", "files", "outputs"))
    if safe.get("status") == "completed":
        try:
            safe["ocr_result"] = build_ocr_result(source_artifact_id, result)
        except OcrWhisperContractError:
            return {"status": "error", "component": "paddleocr_vl", "code": "ocr_result_contract_invalid", "error": "PaddleOCR-VL không trả result contract OCR hợp lệ."}
    return safe


def capability() -> dict[str, Any]:
    runtime = _runtime()
    model = registered_model("paddleocr_vl", "PaddleOCR-VL")
    return {
        "component": "paddleocr_vl",
        "adapter_status": "direct-worker-configured",
        "worker_contract": "paddle_cli.py.v1",
        "worker_contract_status": "unverified_until_smoke",
        "runtime_ready": bool(runtime and runtime[1].is_file()),
        "environment_ready": bool(runtime and runtime[0].is_file()),
        "model_registry_ready": bool(model and model[1].exists()),
        "status": "partial",
        "reason": "PaddleOCR-VL tool model phải tồn tại trong registry local; example/missing model không được nâng capability.",
        "next_action": "Đặt đúng tool model đã được phê duyệt hoặc giữ unavailable; không tự download trong adapter.",
    }
