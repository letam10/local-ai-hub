"""Validation for static, declarative capability-pack descriptors."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from src.shared.schemas.extension_manifest import (
    CAPABILITY_ALLOWLIST,
    CAPABILITY_PACK_SCHEMA_VERSION,
    EXTENSION_ID_PATTERN,
)


class CapabilityPackValidationError(ValueError):
    """Raised when a capability pack is not safe for static discovery."""

    def __init__(self, issues: list[dict[str, str]]) -> None:
        self.issues = tuple(issues)
        codes = ", ".join(sorted({issue["code"] for issue in issues})) or "invalid_capability_pack"
        super().__init__(f"Capability pack validation failed: {codes}")


def _issue(issues: list[dict[str, str]], field: str, code: str, message: str) -> None:
    issues.append({"field": field, "code": code, "message": message})


def _safe_text(value: Any, *, maximum: int = 300) -> bool:
    return isinstance(value, str) and 1 <= len(value.strip()) <= maximum and all(char not in value for char in ("\x00", "\r", "\n"))


def _safe_id(value: Any) -> bool:
    return isinstance(value, str) and bool(EXTENSION_ID_PATTERN.fullmatch(value))


def capability_pack_errors(value: Any) -> list[dict[str, str]]:
    """Return sanitized capability-pack violations without touching the filesystem."""

    issues: list[dict[str, str]] = []
    if not isinstance(value, Mapping):
        _issue(issues, "capability_pack", "invalid_type", "capability pack must be a JSON object.")
        return issues
    allowed = {"schema_version", "id", "display_name", "description", "capabilities", "model_card_ids", "runtime_card_ids"}
    required = {"schema_version", "id", "display_name", "capabilities"}
    if set(value) - allowed:
        _issue(issues, "capability_pack", "unknown_field", "capability pack contains a field outside the static allowlist.")
    if required - set(value):
        _issue(issues, "capability_pack", "missing_field", "capability pack is missing one or more required fields.")
    if value.get("schema_version") != CAPABILITY_PACK_SCHEMA_VERSION:
        _issue(issues, "schema_version", "unsupported_schema", "schema_version must be capability-pack.v1.")
    if not _safe_id(value.get("id")):
        _issue(issues, "id", "invalid_id", "capability pack id must be a safe lowercase hyphenated identifier.")
    if not _safe_text(value.get("display_name"), maximum=120):
        _issue(issues, "display_name", "invalid_value", "display_name must be concise, non-empty text.")
    if "description" in value and not _safe_text(value["description"], maximum=500):
        _issue(issues, "description", "invalid_value", "description must be concise text without control characters.")
    capabilities = value.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities or len(capabilities) != len(set(capabilities)):
        _issue(issues, "capabilities", "invalid_value", "capabilities must be a non-empty, unique list.")
    elif any(item not in CAPABILITY_ALLOWLIST for item in capabilities):
        _issue(issues, "capabilities", "unsupported_capability", "capabilities contains a value outside the v1 allowlist.")
    for field in ("model_card_ids", "runtime_card_ids"):
        identifiers = value.get(field, [])
        if not isinstance(identifiers, list) or len(identifiers) != len(set(identifiers)):
            _issue(issues, field, "invalid_value", f"{field} must be a unique list of card identifiers.")
        elif any(not _safe_id(identifier) for identifier in identifiers):
            _issue(issues, field, "invalid_id", f"{field} contains an unsafe card identifier.")
    return issues


def validate_capability_pack(value: Any) -> dict[str, Any]:
    """Validate and return a detached static capability-pack descriptor."""

    issues = capability_pack_errors(value)
    if issues:
        raise CapabilityPackValidationError(issues)
    return deepcopy(dict(value))
