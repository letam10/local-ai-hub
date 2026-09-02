from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime
from pathlib import Path


ROOT = Path(os.path.expandvars(os.environ.get("LOCALAIHUB_ROOT") or str(Path(__file__).resolve().parents[2]))).expanduser()
_LANGUAGES = frozenset({"auto", "vi", "en", "ja", "zh"})
_FORMATS = frozenset({"all", "text", "markdown", "json", "tables"})


def _verified_device() -> str | None:
    """Return Paddle's GPU selector only after an exact RTX 4060 check."""

    if os.environ.get("LOCALAIHUB_REQUIRE_RTX4060") != "1" or os.environ.get("LOCALAIHUB_FORCE_CPU") == "1":
        return None
    try:
        import torch

        if not torch.cuda.is_available():
            return None
        name = str(torch.cuda.get_device_name(0) or "").casefold()
    except (ImportError, AttributeError, RuntimeError, TypeError):
        return None
    return "gpu:0" if "nvidia" in name and re.search(r"\brtx[ -]?4060\b", name) else None


def _safe_options(request: dict[str, object]) -> dict[str, object] | None:
    language = str(request.get("language") or "auto").strip().casefold()
    output_format = str(request.get("output_format") or "all").strip().casefold()
    if language not in _LANGUAGES or output_format not in _FORMATS:
        return None
    result: dict[str, object] = {"language": language, "output_format": output_format}
    raw_page = request.get("page_number")
    if raw_page not in (None, ""):
        if isinstance(raw_page, bool) or not isinstance(raw_page, int) or not 1 <= raw_page <= 100_000:
            return None
        result["page_number"] = raw_page
    raw_box = request.get("normalized_box")
    if raw_box not in (None, ""):
        if not isinstance(raw_box, list) or len(raw_box) != 4:
            return None
        try:
            box = [float(value) for value in raw_box]
        except (TypeError, ValueError):
            return None
        if any(not math.isfinite(value) or not 0 <= value <= 1 for value in box) or box[2] <= box[0] or box[3] <= box[1]:
            return None
        result["normalized_box"] = [round(value, 8) for value in box]
    return result


def _normalized_box(block: object, width: int, height: int) -> list[float] | None:
    if not isinstance(block, dict):
        return None
    candidate = block.get("normalized_box") or block.get("box_normalized_xyxy")
    if candidate is None:
        candidate = block.get("bbox") or block.get("box")
        if isinstance(candidate, (list, tuple)) and len(candidate) == 4 and width > 0 and height > 0:
            try:
                candidate = [float(candidate[0]) / width, float(candidate[1]) / height, float(candidate[2]) / width, float(candidate[3]) / height]
            except (TypeError, ValueError):
                return None
    if not isinstance(candidate, (list, tuple)) or len(candidate) != 4:
        return None
    try:
        values = [float(value) for value in candidate]
    except (TypeError, ValueError):
        return None
    if any(not math.isfinite(value) or not 0 <= value <= 1 for value in values) or values[2] <= values[0] or values[3] <= values[1]:
        return None
    return values


def _intersects(left: list[float], right: list[float]) -> bool:
    return left[0] < right[2] and left[2] > right[0] and left[1] < right[3] and left[3] > right[1]


def _filter_pages(pages: object, options: dict[str, object]) -> list[object]:
    if not isinstance(pages, list):
        return []
    requested_page = options.get("page_number")
    requested_box = options.get("normalized_box")
    filtered: list[object] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        if requested_page is not None and page.get("page_number", page.get("page")) != requested_page:
            continue
        if isinstance(requested_box, list):
            width = int(page.get("width") or 0) if isinstance(page.get("width"), int) else 0
            height = int(page.get("height") or 0) if isinstance(page.get("height"), int) else 0
            blocks = page.get("blocks")
            if isinstance(blocks, list):
                page = {**page, "blocks": [block for block in blocks if (box := _normalized_box(block, width, height)) is not None and _intersects(box, requested_box)]}
        filtered.append(page)
    return filtered[:256]


def _extract_pages(run_root: Path) -> list[object]:
    pages: list[object] = []
    for path in sorted(run_root.glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict) and isinstance(value.get("pages"), list):
            pages.extend(value["pages"])
        elif isinstance(value, list):
            pages.extend(value)
    return pages[:256]


def _write_text_projection(run_root: Path, pages: list[object]) -> Path:
    path = run_root / "ocr.txt"
    lines: list[str] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        for block in page.get("blocks", []):
            if isinstance(block, dict) and isinstance(block.get("text"), str) and block["text"].strip():
                lines.append(block["text"].strip())
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return path


def main() -> int:
    try:
        request = json.loads(os.read(0, 8 * 1024 * 1024).decode("utf-8"))
        if not isinstance(request, dict):
            raise ValueError("request")
        source = Path(os.path.expandvars(str(request.get("path", "")))).expanduser()
        if not source.is_file():
            print(json.dumps({"status": "error", "code": "input_unreadable", "error": "Input artifact is unavailable."}, ensure_ascii=False))
            return 2
        options = _safe_options(request)
        if options is None:
            print(json.dumps({"status": "error", "code": "ocr_options_invalid", "error": "OCR options are not supported by the server-owned helper contract."}, ensure_ascii=False))
            return 2
        device = _verified_device()
        if device is None:
            print(json.dumps({"status": "error", "code": "rtx4060_required", "error": "PaddleOCR workload requires a verified NVIDIA GeForce RTX 4060; no fallback device is allowed."}, ensure_ascii=False))
            return 2
        from paddleocr import PaddleOCRVL

        pipeline = PaddleOCRVL(pipeline_version="v1.6", device=device, use_doc_orientation_classify=False, use_doc_unwarping=False, use_layout_detection=True)
        output_base = Path(os.path.expandvars(os.environ.get("LOCAL_AI_OUTPUT", str(ROOT / "Output")))).expanduser()
        output_root = output_base / "OCR" / "PaddleOCR-VL"
        output_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        run_root = output_root / f"ocr_{stamp}"
        run_root.mkdir(parents=True, exist_ok=False)
        results = list(pipeline.predict(str(source)))
        for result in results:
            result.save_to_json(save_path=str(run_root))
            result.save_to_markdown(save_path=str(run_root))
        pages = _filter_pages(_extract_pages(run_root), options)
        saved = [path for path in run_root.rglob("*") if path.is_file()]
        if options["output_format"] == "text":
            saved = [_write_text_projection(run_root, pages)]
        elif options["output_format"] == "markdown":
            saved = [path for path in saved if path.suffix.casefold() in {".md", ".markdown"}]
        elif options["output_format"] == "json":
            saved = [path for path in saved if path.suffix.casefold() == ".json"]
        elif options["output_format"] == "tables":
            saved = [path for path in saved if path.suffix.casefold() in {".csv", ".tsv", ".xlsx", ".xls"}]
        print(json.dumps({"status": "completed", "count": len(results), "device": device, "pipeline_version": "v1.6", "options": options, "pages": pages, "files": [str(path) for path in saved]}, ensure_ascii=False))
        return 0
    except Exception:
        print(json.dumps({"status": "error", "code": "ocr_execution_failed", "error": "PaddleOCR-VL không thể hoàn tất OCR bounded."}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
