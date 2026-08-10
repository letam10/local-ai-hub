"""Static model and runtime card validation for capability packs.

Cards describe provenance and constraints.  They never include a local path,
credential, executable command, or installation instruction, so they are safe
to read during static extension discovery.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping
from urllib.parse import urlparse

from src.shared.schemas.extension_manifest import (
    CAPABILITY_ALLOWLIST,
    EXTENSION_ID_PATTERN,
    MODEL_CARD_SCHEMA_VERSION,
    PLATFORMS,
    RUNTIME_CARD_SCHEMA_VERSION,
    is_semver,
    is_version_constraint,
)


MODEL_CARD_COLLECTION_SCHEMA_VERSION = "model-cards.v1"
RUNTIME_CARD_COLLECTION_SCHEMA_VERSION = "runtime-cards.v1"
RUNTIME_KINDS = frozenset({"adapter", "engine", "library", "service"})


class CardValidationError(ValueError):
    """Raised when a model or runtime card is outside the static allowlist."""

    def __init__(self, issues: list[dict[str, str]]) -> None:
        self.issues = tuple(issues)
        codes = ", ".join(sorted({issue["code"] for issue in issues})) or "invalid_card"
        super().__init__(f"Capability card validation failed: {codes}")


def _issue(issues: list[dict[str, str]], field: str, code: str, message: str) -> None:
    issues.append({"field": field, "code": code, "message": message})


def _safe_text(value: Any, *, maximum: int = 300) -> bool:
    return isinstance(value, str) and 1 <= len(value.strip()) <= maximum and all(char not in value for char in ("\x00", "\r", "\n"))


def _safe_url(value: Any) -> bool:
    if not _safe_text(value, maximum=300) or "?" in value or "#" in value:
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"https", "http"} and bool(parsed.netloc) and "@" not in parsed.netloc


def _safe_id(value: Any) -> bool:
    return isinstance(value, str) and bool(EXTENSION_ID_PATTERN.fullmatch(value))


def _validate_text_list(value: Any, field: str, issues: list[dict[str, str]]) -> None:
    if not isinstance(value, list) or not value or len(value) != len(set(value)):
        _issue(issues, field, "invalid_value", f"{field} must be a non-empty, unique list.")
    elif any(not _safe_text(item) for item in value):
        _issue(issues, field, "invalid_value", f"{field} must contain concise text only.")


def _validate_compatibility(value: Any, issues: list[dict[str, str]]) -> None:
    if not isinstance(value, Mapping):
        _issue(issues, "compatibility", "invalid_type", "compatibility must be an object.")
        return
    if set(value) - {"platforms", "required_components", "required_models"}:
        _issue(issues, "compatibility", "unknown_field", "compatibility contains a field outside the card allowlist.")
    platforms = value.get("platforms")
    if not isinstance(platforms, list) or not platforms or len(platforms) != len(set(platforms)):
        _issue(issues, "compatibility.platforms", "invalid_value", "compatibility.platforms must be a non-empty, unique list.")
    elif any(platform not in PLATFORMS for platform in platforms):
        _issue(issues, "compatibility.platforms", "unsupported_platform", "compatibility.platforms contains an unsupported platform identifier.")
    for field in ("required_components", "required_models"):
        requirements = value.get(field, [])
        if not isinstance(requirements, list):
            _issue(issues, f"compatibility.{field}", "invalid_type", f"compatibility.{field} must be a list.")
            continue
        seen: set[str] = set()
        for item in requirements:
            if not isinstance(item, Mapping) or set(item) - {"id", "version", "optional"}:
                _issue(issues, f"compatibility.{field}", "invalid_item", f"compatibility.{field} contains an invalid requirement.")
                continue
            item_id = item.get("id")
            if not _safe_id(item_id) or item_id in seen:
                _issue(issues, f"compatibility.{field}", "invalid_id", f"compatibility.{field} identifiers must be safe and unique.")
            else:
                seen.add(item_id)
            if "version" in item and not is_version_constraint(item["version"]):
                _issue(issues, f"compatibility.{field}", "invalid_version_constraint", f"compatibility.{field} has an unsupported version constraint.")
            if "optional" in item and not isinstance(item["optional"], bool):
                _issue(issues, f"compatibility.{field}", "invalid_optional", f"compatibility.{field} optional must be true or false.")


def _card_errors(value: Any, *, schema_version: str, runtime: bool) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if not isinstance(value, Mapping):
        _issue(issues, "card", "invalid_type", "a capability card must be a JSON object.")
        return issues
    allowed = {
        "schema_version",
        "id",
        "display_name",
        "version",
        "lineage",
        "license",
        "source",
        "intended_use",
        "limitations",
        "compatibility",
    }
    if runtime:
        allowed.update({"runtime_kind", "capabilities"})
    if set(value) - allowed:
        _issue(issues, "card", "unknown_field", "card contains a field outside the static card allowlist.")
    required = allowed - ({"capabilities"} if runtime else set())
    if required - set(value):
        _issue(issues, "card", "missing_field", "card is missing one or more required fields.")
    if value.get("schema_version") != schema_version:
        _issue(issues, "schema_version", "unsupported_schema", "card schema_version is not supported.")
    if not _safe_id(value.get("id")):
        _issue(issues, "id", "invalid_id", "card id must be a safe lowercase hyphenated identifier.")
    if not _safe_text(value.get("display_name"), maximum=120):
        _issue(issues, "display_name", "invalid_value", "display_name must be concise, non-empty text.")
    if not is_semver(value.get("version")):
        _issue(issues, "version", "invalid_version", "version must be a semantic version.")
    _validate_text_list(value.get("lineage"), "lineage", issues)
    if not _safe_text(value.get("license"), maximum=160):
        _issue(issues, "license", "invalid_value", "license must be concise, non-empty text.")
    if not _safe_url(value.get("source")):
        _issue(issues, "source", "invalid_url", "source must be a public HTTP(S) URL without credentials or query data.")
    _validate_text_list(value.get("intended_use"), "intended_use", issues)
    _validate_text_list(value.get("limitations"), "limitations", issues)
    _validate_compatibility(value.get("compatibility"), issues)
    if runtime:
        if value.get("runtime_kind") not in RUNTIME_KINDS:
            _issue(issues, "runtime_kind", "unsupported_runtime_kind", "runtime_kind must be adapter, engine, library, or service.")
        capabilities = value.get("capabilities", [])
        if not isinstance(capabilities, list) or len(capabilities) != len(set(capabilities)):
            _issue(issues, "capabilities", "invalid_value", "runtime card capabilities must be a unique allowlisted list.")
        elif any(capability not in CAPABILITY_ALLOWLIST for capability in capabilities):
            _issue(issues, "capabilities", "unsupported_capability", "runtime card capabilities contains an unsupported value.")
    return issues


def validate_model_card(value: Any) -> dict[str, Any]:
    """Validate a static ``model-card.v1`` descriptor and return a detached copy."""

    issues = _card_errors(value, schema_version=MODEL_CARD_SCHEMA_VERSION, runtime=False)
    if issues:
        raise CardValidationError(issues)
    return deepcopy(dict(value))


def validate_runtime_card(value: Any) -> dict[str, Any]:
    """Validate a static ``runtime-card.v1`` descriptor and return a detached copy."""

    issues = _card_errors(value, schema_version=RUNTIME_CARD_SCHEMA_VERSION, runtime=True)
    if issues:
        raise CardValidationError(issues)
    return deepcopy(dict(value))


def _validate_collection(value: Any, *, collection_schema: str, validator: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Mapping) or set(value) != {"schema_version", "cards"}:
        raise CardValidationError([{"field": "collection", "code": "invalid_collection", "message": "card collection must contain only schema_version and cards."}])
    if value.get("schema_version") != collection_schema or not isinstance(value.get("cards"), list):
        raise CardValidationError([{"field": "collection", "code": "invalid_collection", "message": "card collection schema or cards list is invalid."}])
    cards = [validator(card) for card in value["cards"]]
    identifiers = [card["id"] for card in cards]
    if len(identifiers) != len(set(identifiers)):
        raise CardValidationError([{"field": "collection.cards", "code": "duplicate_id", "message": "card identifiers must be unique within a collection."}])
    return cards


def validate_model_card_collection(value: Any) -> list[dict[str, Any]]:
    """Validate a JSON model-card collection used by a static entrypoint."""

    return _validate_collection(value, collection_schema=MODEL_CARD_COLLECTION_SCHEMA_VERSION, validator=validate_model_card)


def validate_runtime_card_collection(value: Any) -> list[dict[str, Any]]:
    """Validate a JSON runtime-card collection used by a static entrypoint."""

    return _validate_collection(value, collection_schema=RUNTIME_CARD_COLLECTION_SCHEMA_VERSION, validator=validate_runtime_card)
