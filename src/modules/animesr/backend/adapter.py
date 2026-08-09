from __future__ import annotations

from src.shared.utils.adapter_common import describe


def capability() -> dict:
    return {**describe("animesr"), "adapter_status": "existing-application-preserved"}


def queue_upscale(input_path: str, output_path: str | None = None) -> dict:
    """Report the truthful adapter limitation without creating a misleading job."""
    return {
        "status": "unavailable",
        "tool": "upscale_anime_video",
        "input": input_path,
        "output": output_path,
        "reason": "AnimeSR is available through the verified desktop application; a direct Hub inference adapter is not verified.",
    }
