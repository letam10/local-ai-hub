"""Closed, dependency-free contracts for the static capability gateway.

The gateway is deliberately a small data boundary.  It accepts only bounded
opaque selectors and returns canonical, redacted summaries.  Nothing in this
module reads a managed root, imports a provider, probes a machine, or executes
anything outside ordinary validation and serialization.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import math
import re
from typing import Any


CAPABILITY_GATEWAY_SCHEMA_VERSION = "capability-gateway.v1"
SCHEMA_VERSION = CAPABILITY_GATEWAY_SCHEMA_VERSION

SECTION_ALLOWLIST = (
    "assets",
    "extensions",
    "privacy",
    "recipes",
    "workflow_packages",
)
SECTION_SET = frozenset(SECTION_ALLOWLIST)
SELECTOR_KEYS = frozenset(
    {
        "extension_ids",
        "package_ids",
        "catalog_ids",
        "privacy_ids",
        "recipe_ids",
    }
)
SECTION_SELECTOR_KEY = {
    "extensions": "extension_ids",
    "workflow_packages": "package_ids",
    "assets": "catalog_ids",
    "privacy": "privacy_ids",
    "recipes": "recipe_ids",
}

STATUS_ALLOWLIST = frozenset({"operational", "partial", "planned", "unavailable"})
EXECUTION_NOT_RUN = "not_run"
MAX_REQUEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 512 * 1024
MAX_DEPTH = 8
MAX_SECTIONS = len(SECTION_ALLOWLIST)
MAX_SELECTORS_PER_KEY = 32
MAX_RECORDS_PER_SECTION = 256
MAX_TYPES_PER_RECORD = 32
MAX_COUNT_KEYS = 32
MAX_ID_LENGTH = 120
MAX_CODE_LENGTH = 64

OPAQUE_SELECTOR_RE = re.compile(r"^[a-z0-9][a-z0-9._@-]{0,119}$")
SAFE_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_REQUEST_KEYS = frozenset({"schema_version", "sections", "selectors", "include_plans"})
_RESPONSE_KEYS = frozenset(
    {
        "schema_version",
        "status",
        "reason_code",
        "reason",
        "action_code",
        "action",
        "execution",
        "dry_run",
        "fingerprint",
        "sections",
        "counts",
        "errors",
    }
)
_SECTION_KEYS = frozenset(
    {
        "status",
        "reason_code",
        "action_code",
        "records",
        "counts",
        "type_summaries",
        "execution",
        "dry_run",
        "fingerprint",
    }
)
_RECORD_KEYS = frozenset({"id", "version", "status", "reason_code", "action_code", "type_summary", "counts"})
_FORBIDDEN_KEY_TOKENS = frozenset(
    {
        "api_key",
        "apikey",
        "secret",
        "token",
        "password",
        "credential",
        "path",
        "filepath",
        "outputpath",
        "inputpath",
        "command",
        "cmd",
        "shell",
        "argv",
        "executable",
        "runner",
        "loader",
        "manifest",
        "discovery",
        "catalog",
        "report",
        "host",
        "user",
        "environment",
        "env",
        "media",
        "image",
        "audio",
        "video",
        "blob",
        "weight",
        "weights",
        "checkpoint",
        "model",
        "prompt",
        "raw",
    }
)


class CapabilityGatewayValidationError(ValueError):
    """Raised only by strict helpers when a closed value cannot be normalized."""

    def __init__(self, errors: list[dict[str, str]]) -> None:
        self.errors = tuple(deepcopy(errors))
        super().__init__("Capability gateway validation failed.")


def _issue(code: str) -> dict[str, str]:
    """Return a fixed issue shape; caller-controlled values are never echoed."""

    return {"code": code}


def _is_json_value(value: object, *, depth: int = 0) -> bool:
    if depth > MAX_DEPTH:
        return False
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_is_json_value(item, depth=depth + 1) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_value(item, depth=depth + 1) for key, item in value.items())
    return False


def _encoded_size(value: object) -> int | None:
    if not _is_json_value(value):
        return None
    try:
        return len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError, OverflowError, UnicodeEncodeError):
        return None


def _safe_opaque(value: object) -> bool:
    if not isinstance(value, str) or len(value) > MAX_ID_LENGTH:
        return False
    if not OPAQUE_SELECTOR_RE.fullmatch(value):
        return False
    return ".." not in value


def _safe_code(value: object) -> bool:
    return isinstance(value, str) and len(value) <= MAX_CODE_LENGTH and bool(SAFE_CODE_RE.fullmatch(value))


def _safe_text(value: object, maximum: int = 240) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= maximum
        and not any(ord(char) < 32 for char in value)
        and "file:" not in value.lower()
        and "data:" not in value.lower()
        and not re.search(r"(?:https?://|[a-z]:[\\/]|\\\\|(?:^|\s)/etc(?:/|\s|$))", value, re.IGNORECASE)
    )


def _canonical_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _has_forbidden_key(value: object, *, depth: int = 0) -> bool:
    if depth > MAX_DEPTH:
        return True
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                return True
            normalized = _canonical_key(key)
            if normalized in {_canonical_key(token) for token in _FORBIDDEN_KEY_TOKENS}:
                return True
            if _has_forbidden_key(item, depth=depth + 1):
                return True
    elif isinstance(value, list):
        return any(_has_forbidden_key(item, depth=depth + 1) for item in value)
    elif isinstance(value, str):
        lowered = value.lower()
        if "file:" in lowered or "data:" in lowered or "bearer " in lowered or "sk-" in lowered:
            return True
        if re.search(r"(?:[a-z]:[\\/]|\\\\|(?:^|\s)/(?:etc|tmp|var|home)(?:/|\s|$)|\.\.[\\/])", value, re.IGNORECASE):
            return True
    return False


def _unique_strings(value: object, *, maximum: int) -> tuple[list[str] | None, str | None]:
    if not isinstance(value, list):
        return None, "selectors_type"
    if not value or len(value) > maximum:
        return None, "selector_limit"
    if any(not _safe_opaque(item) for item in value):
        return None, "unsafe_selector"
    if len(value) != len(set(value)):
        return None, "duplicate_selector"
    return sorted(value), None


def _normalize_request(value: object) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    errors: list[dict[str, str]] = []
    if not isinstance(value, dict):
        return None, [_issue("request_object_required")]
    if _has_forbidden_key(value):
        return None, [_issue("unsafe_request_value")]
    if set(value) - _REQUEST_KEYS:
        errors.append(_issue("unknown_request_field"))
    if value.get("schema_version") != CAPABILITY_GATEWAY_SCHEMA_VERSION:
        errors.append(_issue("unsupported_schema"))
    sections = value.get("sections")
    if not isinstance(sections, list) or not sections:
        errors.append(_issue("sections_required"))
        normalized_sections: list[str] = []
    elif len(sections) > MAX_SECTIONS:
        errors.append(_issue("section_limit"))
        normalized_sections = []
    elif any(section not in SECTION_SET for section in sections):
        errors.append(_issue("unsupported_section"))
        normalized_sections = []
    elif len(sections) != len(set(sections)):
        errors.append(_issue("duplicate_section"))
        normalized_sections = []
    else:
        normalized_sections = sorted(sections)

    selectors = value.get("selectors", {})
    normalized_selectors: dict[str, list[str]] = {key: [] for key in sorted(SELECTOR_KEYS)}
    if not isinstance(selectors, dict):
        errors.append(_issue("selectors_object_required"))
    else:
        if _has_forbidden_key(selectors):
            errors.append(_issue("unsafe_selector_value"))
        if set(selectors) - SELECTOR_KEYS:
            errors.append(_issue("unknown_selector_field"))
        for key in sorted(SELECTOR_KEYS & set(selectors)):
            normalized, error = _unique_strings(selectors[key], maximum=MAX_SELECTORS_PER_KEY)
            if error:
                errors.append(_issue(error))
            elif normalized is not None:
                normalized_selectors[key] = normalized

    include_plans = value.get("include_plans", False)
    if not isinstance(include_plans, bool):
        errors.append(_issue("include_plans_boolean_required"))
        include_plans = False

    # A selector may be supplied only for a known section.  Empty arrays are
    # normalized and are useful for explicitly requesting no identity filter.
    for section, key in SECTION_SELECTOR_KEY.items():
        if normalized_selectors[key] and section not in normalized_sections:
            errors.append(_issue("selector_section_mismatch"))

    normalized = {
        "schema_version": CAPABILITY_GATEWAY_SCHEMA_VERSION,
        "sections": normalized_sections,
        "selectors": normalized_selectors,
        "include_plans": include_plans,
    }
    return (normalized if not errors else None), errors


def _parse_json(value: str | bytes) -> tuple[object | None, list[dict[str, str]]]:
    try:
        raw = value.decode("utf-8") if isinstance(value, bytes) else value
    except UnicodeDecodeError:
        return None, [_issue("invalid_utf8")]
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_REQUEST_BYTES:
        return None, [_issue("request_size")]
    duplicate = False

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        nonlocal duplicate
        result: dict[str, object] = {}
        for key, item in items:
            if key in result:
                duplicate = True
            result[key] = item
        return result

    try:
        parsed = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, [_issue("invalid_json")]
    if duplicate:
        return None, [_issue("duplicate_json_key")]
    if not isinstance(parsed, dict):
        return None, [_issue("request_object_required")]
    return parsed, []


def validate_gateway_request(value: object) -> dict[str, Any]:
    """Validate and detach a gateway request without reflecting unsafe input."""

    parsed: object = value
    parse_errors: list[dict[str, str]] = []
    if isinstance(value, (str, bytes)):
        parsed, parse_errors = _parse_json(value)
    if parse_errors:
        return {"valid": False, "errors": parse_errors, "request": None, "execution": EXECUTION_NOT_RUN}
    normalized, errors = _normalize_request(parsed)
    if errors or normalized is None:
        return {"valid": False, "errors": errors or [_issue("invalid_request")], "request": None, "execution": EXECUTION_NOT_RUN}
    detached = deepcopy(normalized)
    return {
        "valid": True,
        "errors": [],
        "request": detached,
        "fingerprint": gateway_fingerprint(detached),
        "execution": EXECUTION_NOT_RUN,
    }


def parse_gateway_request(value: str | bytes) -> dict[str, Any]:
    """Compatibility alias for strict JSON request parsing."""

    return validate_gateway_request(value)


def canonical_gateway_json(value: object) -> str:
    """Serialize a validated JSON-compatible value deterministically."""

    if not _is_json_value(value):
        raise CapabilityGatewayValidationError([_issue("non_json_value")])
    try:
        return json.dumps(deepcopy(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, OverflowError, UnicodeEncodeError) as exc:
        del exc
        raise CapabilityGatewayValidationError([_issue("non_json_value")]) from None


def gateway_fingerprint(value: object) -> str:
    return hashlib.sha256(canonical_gateway_json(value).encode("utf-8")).hexdigest()


def _valid_counts(value: object) -> bool:
    return isinstance(value, dict) and len(value) <= MAX_COUNT_KEYS and all(
        isinstance(key, str) and _safe_code(key) and isinstance(item, int) and not isinstance(item, bool) and 0 <= item <= MAX_RECORDS_PER_SECTION
        for key, item in value.items()
    )


def _valid_record(value: object) -> bool:
    if not isinstance(value, dict) or set(value) - _RECORD_KEYS or _has_forbidden_key(value):
        return False
    if not _safe_opaque(value.get("id")):
        return False
    if "version" in value and value["version"] is not None and not _safe_opaque(value["version"]):
        return False
    if value.get("status", "partial") not in STATUS_ALLOWLIST:
        return False
    for key in ("reason_code", "action_code"):
        if key in value and not _safe_code(value[key]):
            return False
    types = value.get("type_summary", [])
    if not isinstance(types, list) or len(types) > MAX_TYPES_PER_RECORD or any(not _safe_code(item) for item in types):
        return False
    return "counts" not in value or _valid_counts(value["counts"])


def _valid_section(value: object) -> bool:
    if not isinstance(value, dict) or set(value) - _SECTION_KEYS or _has_forbidden_key(value):
        return False
    if value.get("status", "partial") not in STATUS_ALLOWLIST:
        return False
    if value.get("execution", EXECUTION_NOT_RUN) != EXECUTION_NOT_RUN:
        return False
    if "dry_run" in value and not isinstance(value["dry_run"], bool):
        return False
    records = value.get("records", [])
    if not isinstance(records, list) or len(records) > MAX_RECORDS_PER_SECTION or any(not _valid_record(item) for item in records):
        return False
    if "counts" in value and not _valid_counts(value["counts"]):
        return False
    summaries = value.get("type_summaries", [])
    return isinstance(summaries, list) and len(summaries) <= MAX_TYPES_PER_RECORD and all(_safe_code(item) for item in summaries)


def validate_gateway_response(value: object) -> dict[str, Any]:
    """Validate a projected response and return a detached copy."""

    if not isinstance(value, dict) or set(value) - _RESPONSE_KEYS or _has_forbidden_key(value):
        return {"valid": False, "errors": [_issue("invalid_response")], "response": None, "execution": EXECUTION_NOT_RUN}
    encoded_size = _encoded_size(value)
    if encoded_size is None or encoded_size > MAX_RESPONSE_BYTES:
        return {"valid": False, "errors": [_issue("response_size")], "response": None, "execution": EXECUTION_NOT_RUN}
    if value.get("schema_version") != CAPABILITY_GATEWAY_SCHEMA_VERSION:
        return {"valid": False, "errors": [_issue("unsupported_schema")], "response": None, "execution": EXECUTION_NOT_RUN}
    if value.get("status") not in STATUS_ALLOWLIST or value.get("execution") != EXECUTION_NOT_RUN:
        return {"valid": False, "errors": [_issue("status_execution_invariant")], "response": None, "execution": EXECUTION_NOT_RUN}
    if not isinstance(value.get("dry_run"), bool):
        return {"valid": False, "errors": [_issue("dry_run_boolean_required")], "response": None, "execution": EXECUTION_NOT_RUN}
    for key in ("reason_code", "action_code"):
        if not _safe_code(value.get(key)):
            return {"valid": False, "errors": [_issue("invalid_response_code")], "response": None, "execution": EXECUTION_NOT_RUN}
    if not _safe_text(value.get("reason")) or not _safe_text(value.get("action")):
        return {"valid": False, "errors": [_issue("unsafe_response_text")], "response": None, "execution": EXECUTION_NOT_RUN}
    sections = value.get("sections")
    if not isinstance(sections, dict) or set(sections) - SECTION_SET or any(not _valid_section(item) for item in sections.values()):
        return {"valid": False, "errors": [_issue("invalid_response_sections")], "response": None, "execution": EXECUTION_NOT_RUN}
    if not _valid_counts(value.get("counts")):
        return {"valid": False, "errors": [_issue("invalid_response_counts")], "response": None, "execution": EXECUTION_NOT_RUN}
    fingerprint = value.get("fingerprint")
    if not isinstance(fingerprint, dict) or set(fingerprint) != {"algorithm", "value"} or fingerprint.get("algorithm") != "sha256" or not SHA256_RE.fullmatch(str(fingerprint.get("value"))):
        return {"valid": False, "errors": [_issue("invalid_fingerprint")], "response": None, "execution": EXECUTION_NOT_RUN}
    fingerprint_source = deepcopy(value)
    fingerprint_source.pop("fingerprint", None)
    if fingerprint["value"] != gateway_fingerprint(fingerprint_source):
        return {"valid": False, "errors": [_issue("fingerprint_mismatch")], "response": None, "execution": EXECUTION_NOT_RUN}
    errors = value.get("errors", [])
    if not isinstance(errors, list) or len(errors) > MAX_COUNT_KEYS or any(not isinstance(item, dict) or set(item) != {"code"} or not _safe_code(item.get("code")) for item in errors):
        return {"valid": False, "errors": [_issue("invalid_response_errors")], "response": None, "execution": EXECUTION_NOT_RUN}
    detached = deepcopy(value)
    return {"valid": True, "errors": [], "response": detached, "fingerprint": gateway_fingerprint(detached), "execution": EXECUTION_NOT_RUN}


def gateway_request_schema() -> dict[str, Any]:
    """Return a closed Draft 2020-12 request schema for tooling and review."""

    selector_item = {"type": "string", "pattern": OPAQUE_SELECTOR_RE.pattern, "maxLength": MAX_ID_LENGTH}
    selectors = {
        "type": "object",
        "additionalProperties": False,
        "properties": {key: {"type": "array", "maxItems": MAX_SELECTORS_PER_KEY, "uniqueItems": True, "items": selector_item} for key in sorted(SELECTOR_KEYS)},
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": CAPABILITY_GATEWAY_SCHEMA_VERSION,
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "sections"],
        "properties": {
            "schema_version": {"const": CAPABILITY_GATEWAY_SCHEMA_VERSION},
            "sections": {"type": "array", "minItems": 1, "maxItems": MAX_SECTIONS, "uniqueItems": True, "items": {"enum": list(SECTION_ALLOWLIST)}},
            "selectors": selectors,
            "include_plans": {"type": "boolean"},
        },
    }


def capability_gateway_schema() -> dict[str, Any]:
    return gateway_request_schema()


__all__ = [
    "CAPABILITY_GATEWAY_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "SECTION_ALLOWLIST",
    "SECTION_SET",
    "SELECTOR_KEYS",
    "STATUS_ALLOWLIST",
    "EXECUTION_NOT_RUN",
    "MAX_REQUEST_BYTES",
    "MAX_RESPONSE_BYTES",
    "MAX_DEPTH",
    "MAX_SELECTORS_PER_KEY",
    "MAX_RECORDS_PER_SECTION",
    "CapabilityGatewayValidationError",
    "validate_gateway_request",
    "parse_gateway_request",
    "validate_gateway_response",
    "canonical_gateway_json",
    "gateway_fingerprint",
    "gateway_request_schema",
    "capability_gateway_schema",
]
