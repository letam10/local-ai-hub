"""Job manager service boundary."""

from .contracts import (
    EXECUTION_DESCRIPTOR_VERSION,
    JOB_RECORD_VERSION,
    JOB_SPEC_VERSION,
    JOB_STATES,
    ExecutionDescriptor,
    JobContractError,
    JobSpec,
    ResourceRequest,
    validate_job_spec,
)
from .durable import (
    DurableJobContext,
    DurableWorkEngine as LegacyDurableWorkEngine,
    ServerOwnedAdapterRegistry,
)
from .v8_engine import V8DurableWorkEngine

# Production package imports use the V8 output-migrated engine. The legacy
# class remains explicitly named for compatibility tests and forensic review.
DurableWorkEngine = V8DurableWorkEngine


__all__ = [
    "EXECUTION_DESCRIPTOR_VERSION",
    "JOB_RECORD_VERSION",
    "JOB_SPEC_VERSION",
    "JOB_STATES",
    "DurableJobContext",
    "DurableWorkEngine",
    "ExecutionDescriptor",
    "JobContractError",
    "JobSpec",
    "LegacyDurableWorkEngine",
    "ResourceRequest",
    "ServerOwnedAdapterRegistry",
    "V8DurableWorkEngine",
    "validate_job_spec",
]
