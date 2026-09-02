"""Opaque OCR and Whisper result contracts for the M4 workspaces.

The contracts are deliberately independent from runtime and filesystem code.
Adapters may read their private worker output, but only the bounded normalized
projection from this module is allowed into the public job record.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any


OCR_RESULT_SCHEMA = "ocr.result.v1"
WHISPER_TRANSCRIPT_SCHEMA = "whisper.transcript.v1"
MAX_OCR_PAGES = 256
MAX_OCR_BLOCKS_PER_PAGE = 2_048
MAX_TRANSCRIPT_SEGMENTS = 10_000
MAX_PAGE_NUMBER = 100_000
MAX_DIMENSION = 100_000_000
MAX_TEXT_LENGTH = 8_000
MAX_LANGUAGE_LENGTH = 32
MAX_DURATION_MS = 86_400_000
_ARTIFACT_ID = re.compile(r"artifact_[a-f0-9]{32}\Z")
_LANGUAGE = re.compile(r"(?:auto|[a-z]{2,8}(?:-[a-z]{2,8})?)\Z", re.IGNORECASE)
_LOCAL_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\)")
_OCR_OUTPUT_FORMATS = frozenset({"all", "text", "markdown", "json", "tables"})
_WHISPER_DEVICES = frozenset({"cpu", "cuda"})
_OCR_BLOCK_TYPES = frozenset({"text", "table", "formula", "image", "header", "footer", "unknown"})


class OcrWhisperContractError(ValueError):
    """A stable, finite validation failure for M4 public contracts."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def is_opaque_artifact_id(value: object) -> bool:
    return isinstance(value, str) and _ARTIFACT_ID.fullmatch(value) is not None


def _error(code: str, message: str) -> OcrWhisperContractError:
    return OcrWhisperContractError(code, message)


def _number(value: object, *, label: str, integer: bool = False, minimum: float = 0, maximum: float = 1) -> float | int:
    if isinstance(value, bool):
        raise _error("contract_number_invalid", f"{label} phải là số hữu hạn.")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise _error("contract_number_invalid", f"{label} phải là số hữu hạn.") from None
    if not math.isfinite(number) or number < minimum or number > maximum:
        raise _error("contract_number_invalid", f"{label} nằm ngoài giới hạn cho phép.")
    if integer:
        if not number.is_integer():
            raise _error("contract_number_invalid", f"{label} phải là số nguyên.")
        return int(number)
    return round(number, 8)


def _unit_box(value: object, *, label: str = "normalized_box") -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise _error("normalized_box_invalid", f"{label} phải có bốn tọa độ chuẩn hóa.")
    box = [float(_number(item, label=f"{label}[{index}]")) for index, item in enumerate(value)]
    if box[2] <= box[0] or box[3] <= box[1]:
        raise _error("normalized_box_invalid", f"{label} phải có phải/dưới lớn hơn trái/trên.")
    return [round(item, 8) for item in box]


def _artifact_ids(value: object, *, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 64:
        raise _error("artifact_id_invalid", f"{label} phải là danh sách artifact ID opaque.")
    if not all(is_opaque_artifact_id(item) for item in value):
        raise _error("artifact_id_invalid", f"{label} phải là danh sách artifact ID opaque.")
    return list(dict.fromkeys(value))


def _safe_text(value: object, *, label: str, required: bool = True) -> str:
    if not isinstance(value, str):
        if not required and value is None:
            return ""
        raise _error("contract_text_invalid", f"{label} phải là text.")
    text = _LOCAL_PATH.sub("[đường-dẫn-cục-bộ]", value.replace("\x00", ""))[:MAX_TEXT_LENGTH]
    if required and not text.strip():
        raise _error("contract_text_invalid", f"{label} không được rỗng.")
    return text


def _page_number(value: object, *, default: int | None = None) -> int:
    if value is None and default is not None:
        return default
    return int(_number(value, label="page_number", integer=True, minimum=1, maximum=MAX_PAGE_NUMBER))


def _dimension(value: object, *, label: str) -> int:
    return int(_number(value if value is not None else 0, label=label, integer=True, minimum=0, maximum=MAX_DIMENSION))


def _confidence(value: object, *, label: str = "confidence") -> float | None:
    if value is None:
        return None
    return float(_number(value, label=label, minimum=0, maximum=1))


def _pixel_box(value: object, *, width: int, height: int) -> list[float] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 4 or width <= 0 or height <= 0:
        return None
    try:
        return _unit_box([
            float(value[0]) / width,
            float(value[1]) / height,
            float(value[2]) / width,
            float(value[3]) / height,
        ], label="bbox")
    except (TypeError, ValueError, OcrWhisperContractError):
        return None


def _build_block(value: Mapping[str, Any], *, width: int, height: int) -> dict[str, Any] | None:
    candidate = value.get("normalized_box")
    if candidate is None:
        candidate = value.get("box_normalized_xyxy")
    box: list[float] | None
    try:
        box = _unit_box(candidate) if candidate is not None else _pixel_box(value.get("bbox", value.get("box")), width=width, height=height)
    except OcrWhisperContractError:
        box = None
    if box is None:
        return None
    raw_text = value.get("text", value.get("content", ""))
    if not isinstance(raw_text, str):
        return None
    block_type = str(value.get("block_type", value.get("type", "text"))).casefold()
    if block_type not in _OCR_BLOCK_TYPES:
        block_type = "unknown"
    return {
        "normalized_box": box,
        "text": _safe_text(raw_text, label="block text", required=False),
        "confidence": _confidence(value.get("confidence")),
        "block_type": block_type,
    }


def _build_page(value: Mapping[str, Any], *, index: int, strict: bool = False) -> dict[str, Any] | None:
    try:
        page_number = _page_number(value.get("page_number", value.get("page")), default=index + 1)
        width = _dimension(value.get("width"), label="page width")
        height = _dimension(value.get("height"), label="page height")
        raw_blocks = value.get("blocks")
        if not isinstance(raw_blocks, list):
            raw_blocks = value.get("detections", [])
        if not isinstance(raw_blocks, list) or len(raw_blocks) > MAX_OCR_BLOCKS_PER_PAGE:
            raise _error("ocr_blocks_limit", "OCR page có quá nhiều block.")
        blocks: list[dict[str, Any]] = []
        for raw_block in raw_blocks:
            if not isinstance(raw_block, Mapping):
                if strict:
                    raise _error("ocr_block_invalid", "OCR block không hợp lệ.")
                continue
            built = _build_block(raw_block, width=width, height=height)
            if built is None:
                if strict:
                    raise _error("ocr_block_invalid", "OCR block thiếu normalized box hoặc text hợp lệ.")
                continue
            blocks.append(built)
        return {"page_number": page_number, "width": width, "height": height, "blocks": blocks}
    except OcrWhisperContractError:
        if strict:
            raise
        return None


def build_ocr_result(
    source_artifact_id: object,
    worker_result: Mapping[str, Any] | None = None,
    *,
    text_artifact_id: object = None,
    markdown_artifact_id: object = None,
    json_artifact_id: object = None,
    table_artifact_ids: object = None,
    annotated_artifact_ids: object = None,
) -> dict[str, Any]:
    if not is_opaque_artifact_id(source_artifact_id):
        raise _error("artifact_id_invalid", "Nguồn OCR phải là artifact ID opaque.")
    worker = worker_result if isinstance(worker_result, Mapping) else {}
    raw_pages = worker.get("pages")
    if not isinstance(raw_pages, list):
        raw_blocks = worker.get("blocks", worker.get("detections", []))
        raw_pages = [{
            "page_number": worker.get("page_number", 1),
            "width": worker.get("width", 0),
            "height": worker.get("height", 0),
            "blocks": raw_blocks,
        }] if isinstance(raw_blocks, list) and raw_blocks else []
    pages = [
        page
        for index, raw_page in enumerate(raw_pages[:MAX_OCR_PAGES])
        if isinstance(raw_page, Mapping)
        for page in [_build_page(raw_page, index=index)]
        if page is not None
    ]
    return {
        "schema_version": OCR_RESULT_SCHEMA,
        "source_artifact_id": source_artifact_id,
        "pages": pages,
        "text_artifact_id": text_artifact_id if is_opaque_artifact_id(text_artifact_id) else None,
        "markdown_artifact_id": markdown_artifact_id if is_opaque_artifact_id(markdown_artifact_id) else None,
        "json_artifact_id": json_artifact_id if is_opaque_artifact_id(json_artifact_id) else None,
        "table_artifact_ids": _artifact_ids(table_artifact_ids, label="table_artifact_ids"),
        "annotated_artifact_ids": _artifact_ids(annotated_artifact_ids, label="annotated_artifact_ids"),
    }


def validate_ocr_result_contract(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("schema_version") != OCR_RESULT_SCHEMA:
        raise _error("result_contract_invalid", "OCR result không đúng ocr.result.v1.")
    source = value.get("source_artifact_id")
    if not is_opaque_artifact_id(source):
        raise _error("result_contract_invalid", "OCR result thiếu source artifact opaque.")
    raw_pages = value.get("pages")
    if not isinstance(raw_pages, list) or len(raw_pages) > MAX_OCR_PAGES:
        raise _error("ocr_pages_limit", "OCR result có số trang không hợp lệ.")
    pages: list[dict[str, Any]] = []
    for index, raw_page in enumerate(raw_pages):
        if not isinstance(raw_page, Mapping):
            raise _error("ocr_page_invalid", "OCR page không hợp lệ.")
        page = _build_page(raw_page, index=index, strict=True)
        if page is None:
            raise _error("ocr_page_invalid", "OCR page không hợp lệ.")
        pages.append(page)
    def artifact_or_none(name: str) -> str | None:
        item = value.get(name)
        if item is not None and not is_opaque_artifact_id(item):
            raise _error("artifact_id_invalid", f"{name} không hợp lệ.")
        return item
    return {
        "schema_version": OCR_RESULT_SCHEMA,
        "source_artifact_id": source,
        "pages": pages,
        "text_artifact_id": artifact_or_none("text_artifact_id"),
        "markdown_artifact_id": artifact_or_none("markdown_artifact_id"),
        "json_artifact_id": artifact_or_none("json_artifact_id"),
        "table_artifact_ids": _artifact_ids(value.get("table_artifact_ids"), label="table_artifact_ids"),
        "annotated_artifact_ids": _artifact_ids(value.get("annotated_artifact_ids"), label="annotated_artifact_ids"),
    }


def normalize_ocr_payload(payload: object) -> tuple[dict[str, Any], str | None]:
    if not isinstance(payload, Mapping):
        return {}, "OCR request phải là JSON object."
    result = dict(payload)
    try:
        source = result.get("source_artifact_id", result.get("asset_id"))
        if source is not None and not is_opaque_artifact_id(source):
            raise _error("artifact_id_invalid", "OCR input phải là artifact ID opaque do Hub cấp.")
        output_format = str(result.get("output_format", "all")).casefold()
        if output_format not in _OCR_OUTPUT_FORMATS:
            raise _error("ocr_output_format_invalid", "Định dạng OCR không được hỗ trợ.")
        if "output_format" in result:
            result["output_format"] = output_format
        if "language" in result and result["language"] not in (None, ""):
            language = str(result["language"]).strip().casefold()
            if len(language) > MAX_LANGUAGE_LENGTH:
                raise _error("language_invalid", "Ngôn ngữ OCR quá dài.")
            result["language"] = language
        if "page_number" in result and result["page_number"] not in (None, ""):
            result["page_number"] = _page_number(result["page_number"])
        region = result.get("normalized_box", result.get("region"))
        if region is not None:
            result["normalized_box"] = _unit_box(region, label="OCR region")
            result.pop("region", None)
    except OcrWhisperContractError as exc:
        return result, str(exc)
    return result, None


def _artifact_record(item: object) -> tuple[str, str, str] | None:
    if not isinstance(item, Mapping):
        return None
    artifact_id = item.get("id")
    if not is_opaque_artifact_id(artifact_id):
        return None
    name = str(item.get("name") or "").casefold()
    media_type = str(item.get("media_type") or "").casefold().split(";", 1)[0]
    return artifact_id, name, media_type


def attach_ocr_artifacts(contract: Mapping[str, Any], artifacts: object) -> dict[str, Any]:
    safe = validate_ocr_result_contract(contract)
    records = [record for record in (_artifact_record(item) for item in artifacts) if record] if isinstance(artifacts, list) else []
    json_items = [item[0] for item in records if item[1].endswith(".json") or item[2] == "application/json"]
    markdown_items = [item[0] for item in records if item[1].endswith((".md", ".markdown")) or item[2] == "text/markdown"]
    text_items = [item[0] for item in records if (item[1].endswith((".txt", ".text")) or item[2].startswith("text/plain")) and not item[1].endswith((".srt", ".md", ".markdown"))]
    table_items = [item[0] for item in records if item[1].endswith((".csv", ".tsv", ".xlsx", ".xls")) or "csv" in item[2] or "spreadsheet" in item[2]]
    image_items = [item[0] for item in records if item[2].startswith("image/") and any(token in item[1] for token in ("ocr", "annot", "box", "layout"))]
    safe["text_artifact_id"] = text_items[0] if text_items else None
    safe["markdown_artifact_id"] = markdown_items[0] if markdown_items else None
    safe["json_artifact_id"] = json_items[0] if json_items else None
    safe["table_artifact_ids"] = list(dict.fromkeys(table_items))
    safe["annotated_artifact_ids"] = list(dict.fromkeys(image_items))
    return safe


def _seconds_to_ms(value: object) -> int | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0 or number > MAX_DURATION_MS / 1000:
        return None
    return int(round(number * 1000))


def _timestamp_ms(value: object, *, milliseconds: bool) -> int | None:
    if isinstance(value, bool):
        return None
    if milliseconds:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number) or number < 0 or number > MAX_DURATION_MS:
            return None
        return int(round(number))
    return _seconds_to_ms(value)


def _segment(value: Mapping[str, Any], *, milliseconds: bool) -> dict[str, Any] | None:
    start_value = value.get("start_ms") if "start_ms" in value else value.get("start")
    end_value = value.get("end_ms") if "end_ms" in value else value.get("end")
    start = _timestamp_ms(start_value, milliseconds="start_ms" in value)
    end = _timestamp_ms(end_value, milliseconds="end_ms" in value)
    text = value.get("text")
    if start is None or end is None or end < start or not isinstance(text, str) or not text.strip():
        return None
    return {"start_ms": start, "end_ms": end, "text": _safe_text(text, label="transcript text")}


def _language(value: object, *, strict: bool = False) -> str:
    candidate = str(value or "auto").strip().casefold()
    if not _LANGUAGE.fullmatch(candidate) or len(candidate) > MAX_LANGUAGE_LENGTH:
        if strict:
            raise _error("language_invalid", "Ngôn ngữ transcript không hợp lệ.")
        return "auto"
    return candidate


def build_whisper_transcript(
    source_artifact_id: object,
    worker_result: Mapping[str, Any] | None = None,
    *,
    transcript_document: Mapping[str, Any] | None = None,
    transcript_artifact_id: object = None,
    srt_artifact_id: object = None,
    json_artifact_id: object = None,
) -> dict[str, Any]:
    if not is_opaque_artifact_id(source_artifact_id):
        raise _error("artifact_id_invalid", "Nguồn transcript phải là artifact ID opaque.")
    worker = transcript_document if isinstance(transcript_document, Mapping) else worker_result if isinstance(worker_result, Mapping) else {}
    raw_segments = worker.get("segments", [])
    if not isinstance(raw_segments, list):
        raw_segments = []
    segments = [
        segment
        for raw in raw_segments[:MAX_TRANSCRIPT_SEGMENTS]
        if isinstance(raw, Mapping)
        for segment in [_segment(raw, milliseconds="start_ms" in raw or "end_ms" in raw)]
        if segment is not None
    ]
    duration_ms = worker.get("duration_ms")
    if duration_ms is None:
        duration_ms = _seconds_to_ms(worker.get("duration_seconds"))
    try:
        duration = int(round(float(duration_ms))) if duration_ms is not None else 0
    except (TypeError, ValueError):
        duration = 0
    duration = max(duration, max((item["end_ms"] for item in segments), default=0))
    duration = min(duration, MAX_DURATION_MS)
    return {
        "schema_version": WHISPER_TRANSCRIPT_SCHEMA,
        "source_artifact_id": source_artifact_id,
        "language": _language(worker.get("language")),
        "duration_ms": duration,
        "segments": segments,
        "transcript_artifact_id": transcript_artifact_id if is_opaque_artifact_id(transcript_artifact_id) else None,
        "srt_artifact_id": srt_artifact_id if is_opaque_artifact_id(srt_artifact_id) else None,
        "json_artifact_id": json_artifact_id if is_opaque_artifact_id(json_artifact_id) else None,
    }


def validate_whisper_transcript_contract(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("schema_version") != WHISPER_TRANSCRIPT_SCHEMA:
        raise _error("result_contract_invalid", "Whisper result không đúng whisper.transcript.v1.")
    source = value.get("source_artifact_id")
    if not is_opaque_artifact_id(source):
        raise _error("result_contract_invalid", "Whisper result thiếu source artifact opaque.")
    language = _language(value.get("language"), strict=True)
    duration_ms = int(_number(value.get("duration_ms"), label="duration_ms", integer=True, minimum=0, maximum=MAX_DURATION_MS))
    raw_segments = value.get("segments")
    if not isinstance(raw_segments, list) or len(raw_segments) > MAX_TRANSCRIPT_SEGMENTS:
        raise _error("transcript_segments_limit", "Transcript có số segment không hợp lệ.")
    segments: list[dict[str, Any]] = []
    previous_start = 0
    for raw in raw_segments:
        if not isinstance(raw, Mapping):
            raise _error("transcript_segment_invalid", "Transcript segment không hợp lệ.")
        start = int(_number(raw.get("start_ms"), label="segment start_ms", integer=True, minimum=0, maximum=duration_ms))
        end = int(_number(raw.get("end_ms"), label="segment end_ms", integer=True, minimum=start, maximum=duration_ms))
        text = _safe_text(raw.get("text"), label="segment text")
        if start < previous_start:
            raise _error("transcript_order_invalid", "Transcript segment không theo thứ tự thời gian.")
        previous_start = start
        segments.append({"start_ms": start, "end_ms": end, "text": text})
    def artifact_or_none(name: str) -> str | None:
        item = value.get(name)
        if item is not None and not is_opaque_artifact_id(item):
            raise _error("artifact_id_invalid", f"{name} không hợp lệ.")
        return item
    return {
        "schema_version": WHISPER_TRANSCRIPT_SCHEMA,
        "source_artifact_id": source,
        "language": language,
        "duration_ms": duration_ms,
        "segments": segments,
        "transcript_artifact_id": artifact_or_none("transcript_artifact_id"),
        "srt_artifact_id": artifact_or_none("srt_artifact_id"),
        "json_artifact_id": artifact_or_none("json_artifact_id"),
    }


def normalize_whisper_payload(payload: object) -> tuple[dict[str, Any], str | None]:
    if not isinstance(payload, Mapping):
        return {}, "Whisper request phải là JSON object."
    result = dict(payload)
    try:
        source = result.get("source_artifact_id", result.get("asset_id"))
        if source is not None and not is_opaque_artifact_id(source):
            raise _error("artifact_id_invalid", "Whisper input phải là artifact ID opaque do Hub cấp.")
        if "device" in result:
            device = str(result.get("device", "cpu")).casefold()
            if device not in _WHISPER_DEVICES:
                raise _error("device_invalid", "Thiết bị Whisper chỉ nhận cpu hoặc cuda.")
            result["device"] = device
        if "language" in result:
            result["language"] = _language(result.get("language", "auto"), strict=True)
        for field in ("start", "end"):
            if field in result and result[field] not in (None, ""):
                result[field] = float(_number(result[field], label=field, minimum=0, maximum=MAX_DURATION_MS / 1000)) / 1
        if "start" in result and "end" in result and result["end"] < result["start"]:
            raise _error("range_invalid", "Mốc kết thúc phải lớn hơn hoặc bằng mốc bắt đầu.")
        if "workflow" in result:
            workflow = str(result.get("workflow", "transcribe")).casefold()
            if workflow not in {"transcribe", "burn"}:
                raise _error("workflow_invalid", "Workflow Whisper không được hỗ trợ.")
            result["workflow"] = workflow
    except OcrWhisperContractError as exc:
        return result, str(exc)
    return result, None


def attach_whisper_artifacts(contract: Mapping[str, Any], artifacts: object) -> dict[str, Any]:
    safe = validate_whisper_transcript_contract(contract)
    records = [record for record in (_artifact_record(item) for item in artifacts) if record] if isinstance(artifacts, list) else []
    json_items = [item[0] for item in records if item[1].endswith(".json") or item[2] == "application/json"]
    srt_items = [item[0] for item in records if item[1].endswith(".srt") or item[2] in {"application/x-subrip", "text/srt"}]
    text_items = [item[0] for item in records if item[1].endswith((".txt", ".vtt")) or item[2] in {"text/plain", "text/vtt"}]
    json_id = json_items[0] if json_items else None
    transcript_id = text_items[0] if text_items else json_id
    safe["transcript_artifact_id"] = transcript_id
    safe["srt_artifact_id"] = srt_items[0] if srt_items else None
    safe["json_artifact_id"] = json_id
    return safe


__all__ = [
    "MAX_DURATION_MS",
    "MAX_OCR_BLOCKS_PER_PAGE",
    "MAX_OCR_PAGES",
    "MAX_TRANSCRIPT_SEGMENTS",
    "OCR_RESULT_SCHEMA",
    "OcrWhisperContractError",
    "WHISPER_TRANSCRIPT_SCHEMA",
    "attach_ocr_artifacts",
    "attach_whisper_artifacts",
    "build_ocr_result",
    "build_whisper_transcript",
    "is_opaque_artifact_id",
    "normalize_ocr_payload",
    "normalize_whisper_payload",
    "validate_ocr_result_contract",
    "validate_whisper_transcript_contract",
]
