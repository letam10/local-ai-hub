from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(os.path.expandvars(os.environ.get("LOCALAIHUB_ROOT") or str(Path(__file__).resolve().parents[2]))).expanduser()
SERVICE = Path(os.path.expandvars(os.environ.get("GROUNDINGDINO_HOME") or str(Path(__file__).resolve().parent))).expanduser()


def main() -> int:
    request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    source = Path(os.path.expandvars(str(request.get("path", "")))).expanduser()
    caption = str(request.get("prompt", "person . object ."))
    box_threshold = float(request.get("box_threshold", 0.35))
    text_threshold = float(request.get("text_threshold", 0.25))
    if not source.is_file():
        print(json.dumps({"status": "error", "error": f"Input file does not exist: {source}"}, ensure_ascii=False))
        return 2

    config_path = SERVICE / "groundingdino" / "config" / "GroundingDINO_SwinT_OGC.py"
    checkpoint = Path(os.path.expandvars(os.environ.get("GROUNDINGDINO_CHECKPOINT", str(ROOT / "Models" / "Vision" / "GroundingDINO" / "groundingdino_swint_ogc.pth")))).expanduser()
    from groundingdino.util.inference import annotate, load_image, load_model, predict
    import cv2

    device = "cuda" if os.environ.get("LOCALAIHUB_FORCE_CPU") != "1" else "cpu"
    model = load_model(str(config_path), str(checkpoint), device=device)
    image_source, image = load_image(str(source))
    boxes, logits, phrases = predict(model=model, image=image, caption=caption, box_threshold=box_threshold, text_threshold=text_threshold, device=device)
    annotated = annotate(image_source=image_source, boxes=boxes, logits=logits, phrases=phrases)

    output_base = Path(os.path.expandvars(os.environ.get("LOCAL_AI_OUTPUT", str(ROOT / "Output")))).expanduser()
    output_root = output_base / "Vision"
    output_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_root / f"{source.stem}_grounded_{stamp}.json"
    image_path = output_root / f"{source.stem}_grounded_{stamp}.jpg"
    cv2.imwrite(str(image_path), annotated)
    results = []
    for index, phrase in enumerate(phrases):
        result = {"label": phrase, "confidence": float(logits[index].max().item()) if index < len(logits) else None}
        if index < len(boxes):
            result["box_normalized_cxcywh"] = [float(value) for value in boxes[index].tolist()]
        results.append(result)
    payload = {"status": "completed", "input": str(source), "prompt": caption, "count": len(results), "grounded": results, "json": str(json_path), "annotated_image": str(image_path), "device": device}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
