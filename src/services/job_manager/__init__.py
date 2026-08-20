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
from .durable import DurableJobContext, DurableWorkEngine, ServerOwnedAdapterRegistry


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
    "ResourceRequest",
    "ServerOwnedAdapterRegistry",
    "validate_job_spec",
]
