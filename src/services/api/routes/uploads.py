"""Upload transport ownership marker; streaming stays in HubHandler."""

from __future__ import annotations

from typing import Mapping

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def unavailable(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    # The current handler owns Content-Length, chunking and staging.  This
    # adapter is intentionally not registered until a streaming transport
    # regression suite is attached.
    return ApiResponse(501, {"status": "unavailable", "error": "streaming_transport_legacy"})


STREAMING_CONTRACT = {
    "content_length_required": True,
    "chunk_bytes": 1024 * 1024,
    "staging": "Temp/uploads",
    "publication": "opaque_artifact_id",
    "transport": "legacy",
}


def contract_metadata() -> dict[str, object]:
    """Expose the boundary for inventory/docs without registering a duplicate."""

    return dict(STREAMING_CONTRACT)


def register(router: Router) -> None:
    # Explicit ownership is documented in architecture/api_routes.yaml.  Do
    # not register a competing /api/uploads handler during the strangler phase.
    return None
