from __future__ import annotations

from Hub.core import probe_media


def probe(path: str) -> dict:
    """Read media metadata with the registered FFprobe binary."""
    return probe_media(path)
