"""Server-owned V5 capability registry and dry-run Module Manager plans."""

from .manager import ModuleManager, build_module_plan, preflight_modules
from .registry import CapabilityRegistry, build_capability_registry, build_server_owned_records
from .resources import plan_module_resources
from .composition import compose_module_status, compose_registry

__all__ = [
    "CapabilityRegistry",
    "ModuleManager",
    "build_capability_registry",
    "build_module_plan",
    "build_server_owned_records",
    "plan_module_resources",
    "preflight_modules",
    "compose_module_status",
    "compose_registry",
]
