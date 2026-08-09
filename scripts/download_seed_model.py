from __future__ import annotations

import os
from pathlib import Path

from huggingface_hub import hf_hub_download


ROOT = Path(os.path.expandvars(os.environ.get("LOCALAIHUB_ROOT") or os.environ.get("LOCAL_AI_HOME") or str(Path(__file__).resolve().parents[1]))).expanduser()
TARGET = Path(os.path.expandvars(os.environ.get("SEED_VC_MODEL_HOME", str(ROOT / "Models" / "Voice" / "Seed-VC" / "seed-uvit-tat-xlsr-tiny")))).expanduser()


def main() -> int:
    cache_root = Path(os.path.expandvars(os.environ.get("LOCAL_AI_CACHE", str(ROOT / "Cache")))).expanduser()
    os.environ.setdefault("HF_HOME", str(cache_root / "HuggingFace"))
    os.environ.setdefault("HF_HUB_CACHE", str(cache_root / "HuggingFace" / "hub"))
    TARGET.mkdir(parents=True, exist_ok=True)
    model = TARGET / "DiT_uvit_tat_xlsr_ema.pth"
    if not model.is_file() or model.stat().st_size == 0:
        path = hf_hub_download(
            repo_id="Plachta/Seed-VC",
            filename="DiT_uvit_tat_xlsr_ema.pth",
            local_dir=str(TARGET),
        )
        print(path)
    else:
        print(f"preserved {model}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
