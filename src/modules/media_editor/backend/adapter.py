from __future__ import annotations

from src.services.api.core import probe_media


def probe(path: str) -> dict:
    """Read media metadata with the registered FFprobe binary."""
    return probe_media(path)
