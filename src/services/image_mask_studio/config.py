"""Safe, local configuration for the non-destructive Image & Mask Studio."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.services.api.config import load_json


CONFIG_CONTRACT = "image-mask-studio-config.v1"
CONFIG_SCHEMA_VERSION = 1

DEFAULT_CONFIG: dict[str, Any] = {
    "contract_version": CONFIG_CONTRACT,
    "schema_version": CONFIG_SCHEMA_VERSION,
    "limits": {
        "max_sessions": 120,
        "max_layers_per_session": 48,
        "max_mask_operations": 256,
        "max_points_per_stroke": 160,
        "max_undo_entries": 32,
        "max_snapshots": 24,
        "max_presets": 120,
    },
    "sam2_assist": {"configured": False},
}


def _bounded(value: object, default: int, *, minimum: int, maximum: int) -> int:
    try:
        candidate = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, candidate))


def normalize_config(value: object) -> dict[str, Any]:
    """Normalize only small, non-secret policy values.

    The configuration intentionally cannot contain executable paths, commands,
    backend URLs, model names or a switch that marks an un-smoked capability as
    operational.  A local user can opt into seeing SAM2 as *partial*, but
    cannot promote it beyond that state.
    """

    if not isinstance(value, Mapping):
        value = {}
    contract = value.get("contract_version", CONFIG_CONTRACT)
    schema_version = value.get("schema_version", CONFIG_SCHEMA_VERSION)
    if contract != CONFIG_CONTRACT or schema_version != CONFIG_SCHEMA_VERSION:
        raise ValueError("Cấu hình Image & Mask Studio không tương thích.")
    raw_limits = value.get("limits", {})
    if not isinstance(raw_limits, Mapping):
        raise ValueError("limits của Image & Mask Studio phải là object.")
    defaults = DEFAULT_CONFIG["limits"]
    raw_sam2 = value.get("sam2_assist", {})
    if not isinstance(raw_sam2, Mapping):
        raise ValueError("sam2_assist của Image & Mask Studio phải là object.")
    return {
        "contract_version": CONFIG_CONTRACT,
        "schema_version": CONFIG_SCHEMA_VERSION,
        "limits": {
            "max_sessions": _bounded(raw_limits.get("max_sessions"), defaults["max_sessions"], minimum=1, maximum=500),
            "max_layers_per_session": _bounded(raw_limits.get("max_layers_per_session"), defaults["max_layers_per_session"], minimum=2, maximum=128),
            "max_mask_operations": _bounded(raw_limits.get("max_mask_operations"), defaults["max_mask_operations"], minimum=8, maximum=1024),
            "max_points_per_stroke": _bounded(raw_limits.get("max_points_per_stroke"), defaults["max_points_per_stroke"], minimum=2, maximum=512),
            "max_undo_entries": _bounded(raw_limits.get("max_undo_entries"), defaults["max_undo_entries"], minimum=4, maximum=96),
            "max_snapshots": _bounded(raw_limits.get("max_snapshots"), defaults["max_snapshots"], minimum=4, maximum=96),
            "max_presets": _bounded(raw_limits.get("max_presets"), defaults["max_presets"], minimum=1, maximum=500),
        },
        # Only a real JSON boolean can change the conservative preflight.
        # Values such as the string "false", 1, or an object must never make
        # an un-smoked integration look partially available.
        "sam2_assist": {"configured": raw_sam2.get("configured") is True},
    }


def image_mask_studio_config() -> dict[str, Any]:
    """Load machine-local config with a tracked safe example as fallback."""

    try:
        return normalize_config(load_json("image_mask_studio.json", DEFAULT_CONFIG))
    except ValueError:
        # A malformed optional policy file must never make the local metadata
        # editor unsafe.  Its availability is still conservative by default.
        return normalize_config(DEFAULT_CONFIG)
