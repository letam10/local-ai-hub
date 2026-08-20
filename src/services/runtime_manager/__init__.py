"""First-class runtime metadata and bounded discovery."""

from .catalog import RUNTIME_CATALOG_SCHEMA, RuntimeCatalogError, load_catalog, validate_runtime_entry
from .manager import RuntimeManager

__all__ = ["RUNTIME_CATALOG_SCHEMA", "RuntimeCatalogError", "RuntimeManager", "load_catalog", "validate_runtime_entry"]
