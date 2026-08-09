from __future__ import annotations

import os
from pathlib import Path

from huggingface_hub import hf_hub_download


ROOT = Path(os.path.expandvars(os.environ.get("LOCALAIHUB_ROOT") or os.environ.get("LOCAL_AI_HOME") or str(Path(__file__).resolve().parents[1]))).expanduser()
MODEL_ROOT = Path(os.path.expandvars(os.environ.get("OMNIPARSER_MODEL_HOME", str(ROOT / "Models" / "Vision" / "OmniParser")))).expanduser()
REPO = "microsoft/OmniParser-v2.0"


def fetch(filename: str, revision: str | None = None) -> Path:
    target = MODEL_ROOT / filename
    if target.is_file() and target.stat().st_size > 0:
        print(f"preserved {target} ({target.stat().st_size} bytes)")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    downloaded = hf_hub_download(
        repo_id=REPO,
        filename=filename,
        revision=revision,
        local_dir=str(MODEL_ROOT),
        local_dir_use_symlinks=False,
    )
    print(f"downloaded {downloaded} ({Path(downloaded).stat().st_size} bytes)")
    return Path(downloaded)


def main() -> int:
    cache_root = Path(os.path.expandvars(os.environ.get("LOCAL_AI_CACHE", str(ROOT / "Cache")))).expanduser()
    os.environ.setdefault("HF_HOME", str(cache_root / "HuggingFace"))
    os.environ.setdefault("HF_HUB_CACHE", str(cache_root / "HuggingFace" / "hub"))
    fetch("icon_detect_v3/model.pt", revision="refs/pr/37")
    for filename in ("config.json", "generation_config.json", "model.safetensors"):
        fetch(f"icon_caption/{filename}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
