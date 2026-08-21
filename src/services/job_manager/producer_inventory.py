"""Machine-readable inventory of output-producing Hub callsites.

The inventory is deliberately declarative.  It is used by correctness tests
and diagnostics to ensure that every known producer is either reservation
aware or explicitly blocked-safe; ``unknown`` is not an accepted state.
"""

from __future__ import annotations

from typing import Any


PRODUCER_INVENTORY: tuple[dict[str, Any], ...] = (
    {"tool": "run_media_operation", "adapter": "src.modules.media_editor.backend.adapter.run_operation", "status": "MIGRATED_RESERVATION", "output_contract": "reservation_handle"},
    {"tool": "frame_interpolate", "adapter": "src.modules.media_editor.backend.adapter.run_operation", "status": "MIGRATED_RESERVATION", "output_contract": "reservation_handle"},
    {"tool": "upscale_anime_video", "adapter": "src.modules.animesr.backend.adapter.run_animesr", "status": "BLOCKED_SAFE_LEGACY", "output_contract": "no_publication_without_reservation"},
    {"tool": "segment_image", "adapter": "src.modules.sam2.backend.adapter.segment_image", "status": "BLOCKED_SAFE_LEGACY", "output_contract": "no_publication_without_reservation"},
    {"tool": "segment_from_box", "adapter": "src.modules.sam2.backend.adapter.segment_from_box", "status": "BLOCKED_SAFE_LEGACY", "output_contract": "no_publication_without_reservation"},
    {"tool": "segment_from_points", "adapter": "src.modules.sam2.backend.adapter.segment_from_points", "status": "BLOCKED_SAFE_LEGACY", "output_contract": "no_publication_without_reservation"},
    {"tool": "track_video_object", "adapter": "src.modules.sam2.backend.adapter.track_video", "status": "BLOCKED_SAFE_LEGACY", "output_contract": "no_publication_without_reservation"},
    {"tool": "transcribe_media", "adapter": "src.modules.whisper.backend.adapter.transcribe", "status": "BLOCKED_SAFE_LEGACY", "output_contract": "no_publication_without_reservation"},
    {"tool": "create_subtitled_video", "adapter": "src.services.api.core._run_operation", "status": "BLOCKED_SAFE_LEGACY", "output_contract": "no_publication_without_reservation"},
    {"tool": "ocr_document", "adapter": "src.modules.ocr.backend.adapter.parse", "status": "BLOCKED_SAFE_LEGACY", "output_contract": "no_publication_without_reservation"},
    {"tool": "text_to_speech", "adapter": "src.modules.voice.backend.qwen3_tts_adapter.synthesize", "status": "BLOCKED_SAFE_LEGACY", "output_contract": "no_publication_without_reservation"},
    {"tool": "design_voice", "adapter": "src.modules.voice.backend.qwen3_tts_adapter.synthesize", "status": "BLOCKED_SAFE_LEGACY", "output_contract": "no_publication_without_reservation"},
    {"tool": "clone_voice", "adapter": "src.modules.voice.backend.qwen3_tts_adapter.synthesize", "status": "BLOCKED_SAFE_LEGACY", "output_contract": "no_publication_without_reservation"},
    {"tool": "convert_voice", "adapter": "src.modules.voice.backend.seed_vc_adapter.convert", "status": "BLOCKED_SAFE_LEGACY", "output_contract": "no_publication_without_reservation"},
    {"tool": "generate_flux", "adapter": "src.modules.image_generation.backend.comfyui.generate", "status": "BLOCKED_SAFE_LEGACY", "output_contract": "no_publication_without_reservation"},
    {"tool": "generate_qwen_image", "adapter": "src.modules.image_generation.backend.comfyui.generate", "status": "BLOCKED_SAFE_LEGACY", "output_contract": "no_publication_without_reservation"},
    {"tool": "parse_screen", "adapter": "src.modules.vision.backend.omniparser_adapter.parse", "status": "NO_OUTPUT_OR_BLOCKED_SAFE", "output_contract": "no_publication_without_reservation"},
    {"tool": "detect_objects", "adapter": "src.modules.vision.backend.rfdetr_adapter.detect", "status": "NO_OUTPUT_OR_BLOCKED_SAFE", "output_contract": "no_publication_without_reservation"},
    {"tool": "ground_objects", "adapter": "src.modules.vision.backend.groundingdino_adapter.ground", "status": "NO_OUTPUT_OR_BLOCKED_SAFE", "output_contract": "no_publication_without_reservation"},
)

INVENTORY_STATUSES = frozenset({"MIGRATED_RESERVATION", "BLOCKED_SAFE_LEGACY", "NO_OUTPUT_OR_BLOCKED_SAFE"})


def producer_inventory() -> list[dict[str, Any]]:
    """Return a detached finite inventory for diagnostics/tests."""

    return [dict(item) for item in PRODUCER_INVENTORY]


def validate_producer_inventory() -> bool:
    return bool(PRODUCER_INVENTORY) and all(
        isinstance(item.get("tool"), str)
        and isinstance(item.get("adapter"), str)
        and item.get("status") in INVENTORY_STATUSES
        and item.get("status") != "UNKNOWN"
        for item in PRODUCER_INVENTORY
    )


__all__ = ["INVENTORY_STATUSES", "PRODUCER_INVENTORY", "producer_inventory", "validate_producer_inventory"]
