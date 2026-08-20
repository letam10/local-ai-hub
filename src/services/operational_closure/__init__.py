"""V7 operational-closure source and update contracts.

The package deliberately separates upstream metadata checks from local
installation state.  It contains no startup polling and no automatic apply.
"""

from .source_availability import (
    SOURCE_STATUSES,
    SourceAvailabilityService,
    source_status_projection,
)
from .update_service import (
    UPDATE_STATUSES,
    UpdateResolver,
    UpdateSchedule,
)
from .evidence import record_runtime_smoke, runtime_evidence_passed, runtime_fingerprint

__all__ = [
    "SOURCE_STATUSES",
    "SourceAvailabilityService",
    "source_status_projection",
    "UPDATE_STATUSES",
    "UpdateResolver",
    "UpdateSchedule",
    "record_runtime_smoke",
    "runtime_evidence_passed",
    "runtime_fingerprint",
]
