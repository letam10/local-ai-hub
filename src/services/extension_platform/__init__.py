"""Static extension platform: discovery, dependency preflight, reports, and scaffolding."""

from .capability_pack import CapabilityPackValidationError, capability_pack_errors, validate_capability_pack
from .config import EXTENSION_CONFIG_SCHEMA_VERSION, load_extension_config
from .discovery import DISCOVERY_VERSION, ExtensionDiscovery, discover_extensions
from .generator import generate_extension_scaffold
from .preflight import PREFLIGHT_VERSION, preflight_extension, preflight_extensions
from .reporting import (
    COMPATIBILITY_REPORT_VERSION,
    build_compatibility_report,
    compatibility_report_json,
    render_compatibility_markdown,
)

__all__ = [
    "COMPATIBILITY_REPORT_VERSION",
    "CapabilityPackValidationError",
    "DISCOVERY_VERSION",
    "EXTENSION_CONFIG_SCHEMA_VERSION",
    "ExtensionDiscovery",
    "PREFLIGHT_VERSION",
    "build_compatibility_report",
    "capability_pack_errors",
    "compatibility_report_json",
    "discover_extensions",
    "generate_extension_scaffold",
    "load_extension_config",
    "preflight_extension",
    "preflight_extensions",
    "render_compatibility_markdown",
    "validate_capability_pack",
]

