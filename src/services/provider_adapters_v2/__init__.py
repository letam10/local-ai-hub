"""Milestone 3 provider-adapter and external-integration contracts.

These services are deliberately server-owned metadata contracts.  They do not
import a legacy provider, inspect a workstation path, start a process, or
load a model.  A later, separately reviewed execution owner may bind to the
same stable identifiers without widening the browser API.
"""

from .external import (
    EXTERNAL_INTEGRATIONS_V2_SCHEMA_VERSION,
    ExternalIntegrationRegistry,
)
from .registry import (
    PROVIDER_ADAPTERS_V2_SCHEMA_VERSION,
    ProviderAdapterRegistry,
)

__all__ = [
    "EXTERNAL_INTEGRATIONS_V2_SCHEMA_VERSION",
    "ExternalIntegrationRegistry",
    "PROVIDER_ADAPTERS_V2_SCHEMA_VERSION",
    "ProviderAdapterRegistry",
]
