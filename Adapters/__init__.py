"""Deprecated compatibility package; use ``src.modules`` backends."""

from __future__ import annotations

import importlib
import sys


for _legacy, _canonical in {
    "airi_adapter": "src.modules.airi.backend.adapter",
    "animesr_adapter": "src.modules.animesr.backend.adapter",
    "common": "src.shared.utils.adapter_common",
    "ffmpeg_adapter": "src.modules.media_editor.backend.adapter",
    "groundingdino_adapter": "src.modules.vision.backend.groundingdino_adapter",
    "ollama_adapter": "src.services.model_manager.ollama_adapter",
    "omniparser_adapter": "src.modules.vision.backend.omniparser_adapter",
    "paddleocr_adapter": "src.modules.ocr.backend.adapter",
    "qwen3_tts_adapter": "src.modules.voice.backend.qwen3_tts_adapter",
    "rfdetr_adapter": "src.modules.vision.backend.rfdetr_adapter",
    "sam2_adapter": "src.modules.sam2.backend.adapter",
    "seed_vc_adapter": "src.modules.voice.backend.seed_vc_adapter",
    "whisper_adapter": "src.modules.whisper.backend.adapter",
}.items():
    sys.modules[f"{__name__}.{_legacy}"] = importlib.import_module(_canonical)
