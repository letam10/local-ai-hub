from __future__ import annotations

import os
import argparse
from pathlib import Path

from huggingface_hub import snapshot_download


ROOT = Path(os.path.expandvars(os.environ.get("LOCALAIHUB_ROOT") or os.environ.get("LOCAL_AI_HOME") or str(Path(__file__).resolve().parents[1]))).expanduser()
MODEL_ROOT = Path(os.path.expandvars(os.environ.get("QWEN3_TTS_MODEL_HOME", str(ROOT / "Models" / "Voice" / "Qwen3-TTS")))).expanduser()
MODEL_ID = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-id", default=MODEL_ID)
    parser.add_argument("--local-dir-name", default=None)
    args = parser.parse_args()
    cache_root = Path(os.path.expandvars(os.environ.get("LOCAL_AI_CACHE", str(ROOT / "Cache")))).expanduser()
    os.environ.setdefault("HF_HOME", str(cache_root / "HuggingFace"))
    os.environ.setdefault("HF_HUB_CACHE", str(cache_root / "HuggingFace" / "hub"))
    MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    local_name = args.local_dir_name or args.model_id.rsplit("/", 1)[-1]
    path = snapshot_download(repo_id=args.model_id, local_dir=str(MODEL_ROOT / local_name))
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
