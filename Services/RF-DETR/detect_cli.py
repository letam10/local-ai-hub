from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path


def main() -> int:
    request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    source = Path(os.path.expandvars(str(request.get("path", "")))).expanduser()
    threshold = float(request.get("threshold", 0.5))
    if not source.is_file():
        print(json.dumps({"status": "error", "error": f"Input file does not exist: {source}"}, ensure_ascii=False))
        return 2

    os.environ.setdefault("RF_HOME", str(Path(__file__).resolve().parents[2] / "Models" / "Vision" / "RF-DETR"))
    from rfdetr import RFDETRNano

    model = RFDETRNano()
    detections = model.predict(str(source), threshold=threshold)
    xyxy = getattr(detections, "xyxy", [])
    confidence = getattr(detections, "confidence", [])
    class_id = getattr(detections, "class_id", [])
    data = getattr(detections, "data", {}) or {}
    names = data.get("class_name")

    def values(value):
        return value.tolist() if hasattr(value, "tolist") else list(value)

    boxes = values(xyxy)
    confidences = values(confidence)
    class_ids = values(class_id)
    results = []
    for index, box in enumerate(boxes):
        numeric_id = int(class_ids[index]) if index < len(class_ids) else None
        label = str(names[index]) if names is not None and index < len(names) else str(numeric_id)
        results.append({
            "box": [float(item) for item in box],
            "label": label,
            "class_id": numeric_id,
            "confidence": float(confidences[index]) if index < len(confidences) else None,
        })

    output_root = Path(__file__).resolve().parents[2] / "Output" / "Vision"
    output_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_root / f"{source.stem}_detected_{stamp}.json"
    image_path = output_root / f"{source.stem}_detected_{stamp}.png"
    result = {"status": "completed", "input": str(source), "count": len(results), "detections": results, "json": str(json_path), "annotated_image": str(image_path)}
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    try:
        import numpy as np
        import supervision as sv
        from PIL import Image

        image = np.array(Image.open(source).convert("RGB"))
        annotated = sv.BoxAnnotator().annotate(scene=image.copy(), detections=detections)
        annotated = sv.LabelAnnotator().annotate(annotated, detections, labels=[item["label"] for item in results])
        Image.fromarray(annotated).save(image_path)
    except Exception as exc:
        result["annotated_image"] = None
        result["annotation_note"] = str(exc)
        json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
