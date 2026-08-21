"""Low-level byte transport contracts used by the V7 API server.

The classes here are intentionally transport-only.  Domain route adapters do
not read files or write uploads; HubHandler remains the only owner of the
socket/file streaming operation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Iterator, Mapping


@dataclass(frozen=True)
class StreamingResponse:
    status: int
    headers: Mapping[str, str] = field(default_factory=dict)
    body: Iterator[bytes] | None = None


@dataclass(frozen=True)
class FileStreamResponse(StreamingResponse):
    path: Path | None = None
    start: int = 0
    end: int | None = None
    head_only: bool = False

    def chunks(self, source: BinaryIO, *, chunk_bytes: int = 1024 * 1024) -> Iterator[bytes]:
        if self.head_only:
            return
        source.seek(max(0, self.start))
        remaining = None if self.end is None else max(0, self.end - self.start + 1)
        while remaining is None or remaining:
            data = source.read(chunk_bytes if remaining is None else min(chunk_bytes, remaining))
            if not data:
                break
            yield data
            if remaining is not None:
                remaining -= len(data)


@dataclass(frozen=True)
class UploadStreamHandler:
    """Bounded upload policy metadata shared by the HTTP transport."""

    chunk_bytes: int = 1024 * 1024
    staging_root: str = "Temp/uploads"
    publication: str = "opaque_artifact_id"
    requires_content_length: bool = True
    single_pass_hash: str = "sha256"


TRANSPORT_REGISTRY = {
    "artifact_get": {"method": "GET", "class": "STREAM", "range": "single"},
    "artifact_head": {"method": "HEAD", "class": "STREAM", "body": "none"},
    "upload": {"method": "POST", "class": "UPLOAD", "content_length": "required", "hash": "sha256"},
    "static_ui": {"method": "GET", "class": "STATIC", "root": "src/ui"},
    "comfy_bridge": {"class": "SPECIAL_PROTOCOL", "ownership": "backend"},
}


__all__ = ["FileStreamResponse", "StreamingResponse", "TRANSPORT_REGISTRY", "UploadStreamHandler"]
