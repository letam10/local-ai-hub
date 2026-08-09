from __future__ import annotations

import base64
import json
import os
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(os.path.expandvars(os.environ.get("LOCALAIHUB_ROOT") or str(Path(__file__).resolve().parents[2]))).expanduser()
SERVICE = Path(os.path.expandvars(os.environ.get("OMNIPARSER_HOME") or str(Path(__file__).resolve().parent))).expanduser()
MODEL_ROOT = Path(os.path.expandvars(os.environ.get("OMNIPARSER_MODEL_HOME", str(ROOT / "Models" / "Vision" / "OmniParser")))).expanduser()
sys.path.insert(0, str(SERVICE))


def json_safe(value):
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def main() -> int:
    request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    source = Path(os.path.expandvars(str(request.get("path", "")))).expanduser()
    if not source.is_file():
        print(json.dumps({"status": "error", "error": f"Input file does not exist: {source}"}, ensure_ascii=False))
        return 2
    from util.omniparser import Omniparser

    config = {"som_model_path": str(MODEL_ROOT / "icon_detect_v3" / "model.pt"), "caption_model_name": "florence2", "caption_model_path": str(MODEL_ROOT / "icon_caption"), "BOX_TRESHOLD": float(request.get("box_threshold", 0.05))}
    parser = Omniparser(config)
    encoded, label_coordinates, parsed_content = parser.parse(base64.b64encode(source.read_bytes()).decode("ascii"))
    output_base = Path(os.path.expandvars(os.environ.get("LOCAL_AI_OUTPUT", str(ROOT / "Output")))).expanduser()
    output_root = output_base / "Vision"
    output_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    image_path = output_root / f"{source.stem}_omniparser_{stamp}.png"
    json_path = output_root / f"{source.stem}_omniparser_{stamp}.json"
    image_path.write_bytes(base64.b64decode(encoded))
    payload = {"status": "completed", "input": str(source), "count": len(parsed_content), "parsed": json_safe(parsed_content), "labels": json_safe(label_coordinates), "annotated_image": str(image_path), "json": str(json_path), "device": "cpu" if os.environ.get("LOCALAIHUB_FORCE_CPU") == "1" else "cuda"}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
