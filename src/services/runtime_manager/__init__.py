"""First-class runtime metadata and bounded discovery."""

from .catalog import RUNTIME_CATALOG_SCHEMA, RuntimeCatalogError, load_catalog, validate_runtime_entry
from .core_resolver import CORE_RUNTIME_SCHEMA, CoreRuntimeCandidate, CoreRuntimeResolver, inspect_core_runtime
from .manager import RuntimeManager

__all__ = [
    "CORE_RUNTIME_SCHEMA",
    "CoreRuntimeCandidate",
    "CoreRuntimeResolver",
    "RUNTIME_CATALOG_SCHEMA",
    "RuntimeCatalogError",
    "RuntimeManager",
    "inspect_core_runtime",
    "load_catalog",
    "validate_runtime_entry",
]
