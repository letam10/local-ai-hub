"""Post-V8 Resource Scheduler / GPU Orchestrator V2 contract."""

from .scheduler import (
    RESOURCE_SCHEDULER_SCHEMA_VERSION,
    RESOURCE_SCHEDULER_STATES,
    ResourceScheduler,
    ResourceSchedulerError,
    server_owned_resource_profiles,
)
from .taxonomy import RESOURCE_TAXONOMY_SCHEMA_VERSION, requirement, requirement_ids, resolve_requirement

__all__ = [
    "RESOURCE_SCHEDULER_SCHEMA_VERSION",
    "RESOURCE_SCHEDULER_STATES",
    "ResourceScheduler",
    "ResourceSchedulerError",
    "server_owned_resource_profiles",
    "RESOURCE_TAXONOMY_SCHEMA_VERSION",
    "requirement",
    "requirement_ids",
    "resolve_requirement",
]
