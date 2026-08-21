"""V7 final productization services.

The package composes the existing model/runtime/component managers into a
single, server-owned catalog and lifecycle contract.  It deliberately keeps
machine-local discovery separate from inference and never downloads anything
at startup.
"""

from .catalog import ProductionCatalog, catalog_snapshot
from .lifecycle import ComponentLifecycle, lifecycle_for_paths
from .runtime_probe import inspect_runtime_environment, verify_python_environment

__all__ = ["ComponentLifecycle", "ProductionCatalog", "catalog_snapshot", "inspect_runtime_environment", "lifecycle_for_paths", "verify_python_environment"]
