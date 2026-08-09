from __future__ import annotations

from Hub.jobs import create_job

from .common import describe


def capability() -> dict:
    return {**describe("animesr"), "adapter_status": "existing-application-preserved"}


def queue_upscale(input_path: str, output_path: str | None = None) -> dict:
    """Create a guarded job record; execution is connected only after CLI verification."""
    return create_job("upscale_anime_video", {"input": input_path, "output": output_path}, device="cuda:0")
