"""Canonical Local AI Hub V2 paths.

The registry is source-only. Runtime, model and output trees are machine-local
and ignored by Git. Existing installations may remain external-managed and be
referenced by the local migration manifest.
"""

from __future__ import annotations

import os
from pathlib import Path

from src.platform.paths import get_paths

# Compatibility aliases. New code should use ``src.platform.paths`` directly;
# existing V5/V6 imports continue to resolve the same legacy single-root tree.
_paths = get_paths(
    app_root=os.environ.get("LOCALAIHUB_ROOT") or os.environ.get("LOCALAIHUB_APP_ROOT"),
    data_root=os.environ.get("LOCALAIHUB_ROOT") or os.environ.get("LOCALAIHUB_DATA_ROOT"),
)
ROOT = _paths.data_root
APP_ROOT = _paths.app_root
DATA_ROOT = _paths.data_root
# Config/Models/Cache/etc. are retained as the repository's canonical Windows
# case. Do not create parallel `config`/`models` directories on Windows.
CONFIG_ROOT = ROOT / "Config"
ENVIRONMENTS_ROOT = ROOT / "Environments"
RUNTIME_ROOT = ROOT / "runtime"
MODEL_ROOT = ROOT / "Models"
CACHE_ROOT = ROOT / "Cache"
OUTPUT_ROOT = ROOT / "Output"
TEMP_ROOT = ROOT / "Temp"
LOG_ROOT = ROOT / "Logs"
REPORT_ROOT = ROOT / "Reports"
BACKUP_ROOT = ROOT / "Backups"

RUNTIME_PATHS = {
    "vision.omniparser": RUNTIME_ROOT / "engines" / "vision" / "OmniParser",
    "vision.rfdetr": RUNTIME_ROOT / "engines" / "vision" / "RF-DETR",
    "vision.groundingdino": RUNTIME_ROOT / "engines" / "vision" / "GroundingDINO",
    "vision.paddleocr": RUNTIME_ROOT / "engines" / "vision" / "PaddleOCR",
    "vision.sam2": RUNTIME_ROOT / "engines" / "vision" / "SAM2",
    "speech.faster-whisper": RUNTIME_ROOT / "engines" / "speech" / "Faster-Whisper",
    "voice.qwen3-tts": RUNTIME_ROOT / "engines" / "voice" / "Qwen3-TTS",
    "voice.seed-vc": RUNTIME_ROOT / "engines" / "voice" / "Seed-VC",
    "image.qwen-image": RUNTIME_ROOT / "engines" / "image" / "Qwen-Image",
    "image.flux": RUNTIME_ROOT / "engines" / "image" / "FLUX",
    "image.comfyui": RUNTIME_ROOT / "engines" / "image" / "ComfyUI",
    "video.animesr": RUNTIME_ROOT / "engines" / "video" / "AnimeSR",
    "video.practical-rife": RUNTIME_ROOT / "engines" / "video" / "Practical-RIFE",
    "video.real-esrgan": RUNTIME_ROOT / "engines" / "video" / "Real-ESRGAN",
    "tools.ffmpeg": RUNTIME_ROOT / "tools" / "ffmpeg",
    "app.sam2-mask-studio": RUNTIME_ROOT / "applications" / "SAM2-Mask-Studio",
    "app.anime-upscale-studio": RUNTIME_ROOT / "applications" / "Anime-Upscale-Studio",
    "app.flux-klein-studio": RUNTIME_ROOT / "applications" / "FLUX-Klein-Studio",
    "app.qwen-image-studio": RUNTIME_ROOT / "applications" / "Qwen-Image-Studio",
    "app.external": RUNTIME_ROOT / "applications" / "external",
}

MODEL_PATHS = {
    "vision.omniparser": MODEL_ROOT / "Vision" / "OmniParser",
    "vision.rfdetr": MODEL_ROOT / "Vision" / "RF-DETR",
    "vision.groundingdino": MODEL_ROOT / "Vision" / "GroundingDINO",
    "vision.sam2": MODEL_ROOT / "Vision" / "SAM2",
    "ocr.paddleocr-vl": MODEL_ROOT / "OCR" / "PaddleOCR-VL",
    "speech.whisper": MODEL_ROOT / "Speech" / "Whisper",
    "voice.qwen3-tts": MODEL_ROOT / "Voice" / "Qwen3-TTS",
    "voice.seed-vc": MODEL_ROOT / "Voice" / "Seed-VC",
    "image.qwen-image": MODEL_ROOT / "Image" / "Qwen-Image",
    "image.flux": MODEL_ROOT / "Image" / "FLUX",
    "video.animesr": MODEL_ROOT / "Video" / "AnimeSR",
}


def ensure_managed_directories() -> None:
    """Create empty ignored roots without touching existing user data."""

    for path in (RUNTIME_ROOT, MODEL_ROOT, CACHE_ROOT, OUTPUT_ROOT, TEMP_ROOT, LOG_ROOT):
        path.mkdir(parents=True, exist_ok=True)


def managed_path(key: str, *, kind: str = "runtime") -> Path:
    table = RUNTIME_PATHS if kind == "runtime" else MODEL_PATHS
    try:
        return table[key]
    except KeyError as exc:
        raise KeyError(f"unknown {kind} path: {key}") from exc
