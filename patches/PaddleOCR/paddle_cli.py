from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(os.path.expandvars(os.environ.get("LOCALAIHUB_ROOT") or str(Path(__file__).resolve().parents[2]))).expanduser()


def main() -> int:
    request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    source = Path(os.path.expandvars(str(request.get("path", "")))).expanduser()
    if not source.is_file():
        print(json.dumps({"status": "error", "error": f"Input file does not exist: {source}"}, ensure_ascii=False))
        return 2
    from paddleocr import PaddleOCRVL

    device = "cpu" if os.environ.get("LOCALAIHUB_FORCE_CPU") == "1" else "gpu:0"
    pipeline = PaddleOCRVL(pipeline_version="v1.6", device=device, use_doc_orientation_classify=False, use_doc_unwarping=False, use_layout_detection=True)
    output_base = Path(os.path.expandvars(os.environ.get("LOCAL_AI_OUTPUT", str(ROOT / "Output")))).expanduser()
    output_root = output_base / "OCR" / "PaddleOCR-VL"
    output_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_root = output_root / f"{source.stem}_{stamp}"
    run_root.mkdir(parents=True, exist_ok=True)
    results = list(pipeline.predict(str(source)))
    for result in results:
        result.save_to_json(save_path=str(run_root))
        result.save_to_markdown(save_path=str(run_root))
    saved = [str(path) for path in run_root.rglob("*") if path.is_file()]
    print(json.dumps({"status": "completed", "input": str(source), "count": len(results), "device": device, "pipeline_version": "v1.6", "output_root": str(run_root), "files": saved}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
