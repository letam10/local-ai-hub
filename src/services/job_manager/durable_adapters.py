"""Server-owned durable adapter contracts for the V6 admission boundary.

The registry builder is intentionally lazy and read-only: opening or reading
durable state never probes an executable and never starts an adapter.  The
initial adapter is a queue/recovery identity only; runtime execution remains a
separate, explicitly authorized capability.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from src.services import artifact_store

from .contracts import JobContractError, JobSpec, is_artifact_id, validate_job_spec
from .durable import ServerOwnedAdapterRegistry


MEDIA_VIDEO_GRADE_ADAPTER_ID = "media.video_grade.v1"
MEDIA_VIDEO_GRADE_TOOL = "media.video_grade"
MEDIA_VIDEO_GRADE_OPERATION = "run"
MEDIA_VIDEO_GRADE_RESOURCES = {
    "cpu_slots": 1,
    "gpu_slots": 0,
    "ram_mb": 0,
    "disk_mb": 0,
    "exclusive_group": None,
}
MEDIA_VIDEO_GRADE_ARGUMENTS = frozenset({
    "source_artifact_id",
    "brightness",
    "contrast",
    "saturation",
    "gamma",
    "denoise",
    "sharpen",
})
MEDIA_VIDEO_GRADE_DIAGNOSTIC_ACTION = "Create a new allowlisted media.video_grade.v1 job."
MEDIA_VIDEO_GRADE_UNAVAILABLE_ACTION = "Create a new allowlisted media.video_grade.v1 job after the server adapter is available."
_DENOISE_SHARPEN = frozenset({"off", "light", "medium"})


def _contract_error(code: str, *, action: str = MEDIA_VIDEO_GRADE_DIAGNOSTIC_ACTION) -> JobContractError:
    return JobContractError(code, action)


def _bounded_number(value: object, minimum: float, maximum: float) -> bool:
    return (
        type(value) in {int, float}
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and minimum <= float(value) <= maximum
    )


def _validate_video_artifact(source_artifact_id: object) -> None:
    """Resolve and type-check one opaque Hub-owned VIDEO artifact only."""

    if not is_artifact_id(source_artifact_id):
        raise _contract_error("INVALID_SOURCE_ARTIFACT")
    try:
        resolved = artifact_store.resolve(source_artifact_id)
        described = artifact_store.describe(source_artifact_id) if resolved is not None else None
    except Exception as exc:  # read-only artifact failures are unavailable, never reflected
        raise _contract_error("SOURCE_ARTIFACT_UNAVAILABLE", action=MEDIA_VIDEO_GRADE_UNAVAILABLE_ACTION) from exc
    if (
        resolved is None
        or not isinstance(described, Mapping)
        or described.get("id") != source_artifact_id
        or not isinstance(described.get("media_type"), str)
        or not described["media_type"].casefold().startswith("video/")
    ):
        raise _contract_error("SOURCE_ARTIFACT_UNAVAILABLE", action=MEDIA_VIDEO_GRADE_UNAVAILABLE_ACTION)


def validate_media_video_grade_spec(value: object, *, resolve_input: bool = True) -> JobSpec:
    """Validate the sole production JobSpec without accepting client policy."""

    try:
        spec = validate_job_spec(value)
    except JobContractError:
        raise
    except Exception as exc:
        raise _contract_error("INVALID_JOB_SPEC") from exc

    descriptor = spec.descriptor
    resources = descriptor.resources.to_mapping()
    if (
        spec.tool != MEDIA_VIDEO_GRADE_TOOL
        or descriptor.adapter_id != MEDIA_VIDEO_GRADE_ADAPTER_ID
        or descriptor.operation != MEDIA_VIDEO_GRADE_OPERATION
        or descriptor.reconstructable is not True
        or resources != MEDIA_VIDEO_GRADE_RESOURCES
    ):
        raise _contract_error("UNSUPPORTED_MEDIA_JOB")
    arguments = descriptor.arguments
    if type(arguments) is not dict or "source_artifact_id" not in arguments or set(arguments) - MEDIA_VIDEO_GRADE_ARGUMENTS:
        raise _contract_error("INVALID_MEDIA_ARGUMENTS")
    if not is_artifact_id(arguments.get("source_artifact_id")):
        raise _contract_error("INVALID_SOURCE_ARTIFACT")
    numeric_bounds = {
        "brightness": (-1.0, 1.0),
        "contrast": (0.0, 3.0),
        "saturation": (0.0, 3.0),
        "gamma": (0.1, 4.0),
    }
    for name, (minimum, maximum) in numeric_bounds.items():
        if name in arguments and not _bounded_number(arguments[name], minimum, maximum):
            raise _contract_error("INVALID_MEDIA_ARGUMENTS")
    for name in ("denoise", "sharpen"):
        if name in arguments and (not isinstance(arguments[name], str) or arguments[name] not in _DENOISE_SHARPEN):
            raise _contract_error("INVALID_MEDIA_ARGUMENTS")
    if resolve_input:
        _validate_video_artifact(arguments.get("source_artifact_id"))
    return spec


def _media_video_grade_no_run_adapter(_descriptor: object, _context: object) -> dict[str, str]:
    """Keep accidental trusted starts truthful until a future runtime adapter exists."""

    return {
        "status": "unavailable",
        "reason_code": "ADAPTER_UNAVAILABLE",
        "action_code": "CHECK_SERVER_ADAPTER",
    }


def build_production_registry() -> ServerOwnedAdapterRegistry:
    """Build the code-owned V6 registry without probing or executing anything."""

    registry = ServerOwnedAdapterRegistry()
    registry.register(MEDIA_VIDEO_GRADE_ADAPTER_ID, _media_video_grade_no_run_adapter)
    return registry


__all__ = [
    "MEDIA_VIDEO_GRADE_ADAPTER_ID",
    "MEDIA_VIDEO_GRADE_ARGUMENTS",
    "MEDIA_VIDEO_GRADE_DIAGNOSTIC_ACTION",
    "MEDIA_VIDEO_GRADE_OPERATION",
    "MEDIA_VIDEO_GRADE_RESOURCES",
    "MEDIA_VIDEO_GRADE_TOOL",
    "MEDIA_VIDEO_GRADE_UNAVAILABLE_ACTION",
    "build_production_registry",
    "validate_media_video_grade_spec",
]
