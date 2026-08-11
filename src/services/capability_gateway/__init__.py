"""Server-owned, static-only capability gateway core."""

from .gateway import (
    CapabilityGateway,
    SectionLoader,
    TrustedSectionLoaderRegistry,
    build_capability_gateway,
    build_gateway_snapshot,
)
from .projection import (
    CapabilityProjectionError,
    action_text,
    project_invalid_response,
    project_section,
    reason_text,
)
from .defaults import build_server_owned_gateway_snapshot

__all__ = [
    "CapabilityGateway",
    "SectionLoader",
    "TrustedSectionLoaderRegistry",
    "build_capability_gateway",
    "build_gateway_snapshot",
    "CapabilityProjectionError",
    "action_text",
    "project_invalid_response",
    "project_section",
    "reason_text",
    "build_server_owned_gateway_snapshot",
]
