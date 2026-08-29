"""Canonical logical resource taxonomy for Post-V8 contracts.

Logical requirements are deliberately distinct from concrete scheduler profiles.
Providers and workflow runners declare only one of the bounded requirement IDs
below; Resource Scheduler remains the only authority which can resolve that
requirement against server-owned profiles and inventory.  The module performs
no hardware probe, model lookup, provider call, or dispatch.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any


RESOURCE_TAXONOMY_SCHEMA_VERSION = "resource-taxonomy.v1"

# Image generation deliberately has no default concrete profile.  A model
# catalog must first supply bounded VRAM evidence; assuming the 2 GiB vision
# profile would make a false fit claim for FLUX/Qwen/ComfyUI workflows.
_REQUIREMENTS: dict[str, dict[str, Any]] = {
    "cpu.light": {"execution_class": "cpu", "default_profile_id": "cpu_light", "requires_gpu": False},
    "media.ffmpeg": {"execution_class": "cpu", "default_profile_id": "ffmpeg_probe", "requires_gpu": False},
    "gpu.vision": {"execution_class": "gpu", "default_profile_id": "vision_gpu_2gb", "requires_gpu": True},
    "gpu.speech": {"execution_class": "gpu", "default_profile_id": "whisper_gpu_2gb", "requires_gpu": True},
    "gpu.video": {"execution_class": "gpu", "default_profile_id": "video_gpu_4gb", "requires_gpu": True},
    "gpu.voice": {"execution_class": "gpu", "default_profile_id": None, "requires_gpu": True},
    "gpu.image_generation": {"execution_class": "gpu", "default_profile_id": None, "requires_gpu": True},
}


def requirement_ids() -> tuple[str, ...]:
    """Return the closed logical vocabulary in stable order."""

    return tuple(sorted(_REQUIREMENTS))


def requirement(requirement_id: object) -> dict[str, Any] | None:
    """Return a detached schema-safe declaration for one logical requirement."""

    value = _REQUIREMENTS.get(requirement_id) if isinstance(requirement_id, str) else None
    if value is None:
        return None
    return {"requirement_id": requirement_id, **deepcopy(value)}


def resolve_requirement(requirement_id: object, profiles: object) -> dict[str, Any]:
    """Resolve a logical requirement through published scheduler profiles only.

    The result is a bounded preflight projection, never a reservation.  An
    unproven generation/voice estimate remains unavailable rather than falling
    back to an unrelated profile.
    """

    declared = requirement(requirement_id)
    if declared is None:
        return {
            "requirement_id": str(requirement_id or ""),
            "state": "unavailable",
            "code": "resource_requirement_unknown",
            "profile_id": None,
            "estimated_vram_mb": None,
            "estimate_source": "not_available",
            "dispatchable": False,
            "reason": "The logical resource requirement is not recognized by the server-owned taxonomy.",
        }
    rows = profiles if isinstance(profiles, Sequence) and not isinstance(profiles, (str, bytes, bytearray)) else []
    matches = [
        row for row in rows
        if isinstance(row, Mapping)
        and row.get("resource_requirement") == requirement_id
        and isinstance(row.get("profile_id"), str)
    ]
    # A pre-closure snapshot can lack the additive public classification.  The
    # compatibility path is an exact lookup through this table's declared
    # profile ID, never a profile-name heuristic; current scheduler projections
    # always carry ``resource_requirement``.
    if not matches and isinstance(declared.get("default_profile_id"), str):
        matches = [
            row for row in rows
            if isinstance(row, Mapping) and row.get("profile_id") == declared["default_profile_id"]
        ]
    matches.sort(key=lambda row: str(row.get("profile_id")))
    if matches:
        profile = matches[0]
        estimate = profile.get("estimated_vram_mb")
        return {
            "requirement_id": requirement_id,
            "state": "resolved",
            "code": "resource_profile_resolved",
            "profile_id": profile["profile_id"],
            "estimated_vram_mb": estimate if isinstance(estimate, int) and not isinstance(estimate, bool) else None,
            "estimate_source": "scheduler_profile_declaration",
            "dispatchable": True,
            "reason": "A server-owned scheduler profile declares this logical requirement; no reservation or hardware probe was made.",
        }
    code = "model_vram_evidence_required" if requirement_id == "gpu.image_generation" else "resource_profile_not_published"
    return {
        "requirement_id": requirement_id,
        "state": "unavailable",
        "code": code,
        "profile_id": None,
        "estimated_vram_mb": None,
        "estimate_source": "not_available",
        "dispatchable": False,
        "reason": "Image-generation dispatch needs model-catalog VRAM evidence before a concrete scheduler profile can be selected." if code == "model_vram_evidence_required" else "No compatible server-owned scheduler profile is published for this logical requirement.",
    }


__all__ = ["RESOURCE_TAXONOMY_SCHEMA_VERSION", "requirement", "requirement_ids", "resolve_requirement"]
