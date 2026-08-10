"""Closed, bounded contracts for the static Creative Recipe Intelligence core.

The contracts describe prompt composition before execution.  They deliberately
contain no filesystem paths, commands, model payloads, runtime configuration or
media bytes.  Python validation is fail-closed and the exported Draft 2020-12
schemas mirror the structural/type rules that can be expressed declaratively.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Mapping
from typing import Any, Callable


CATALOG_CONTRACT = "creative-recipe-catalog.v1"
RECIPE_CONTRACT = "creative-recipe.v1"
STYLE_PACK_CONTRACT = "creative-style-pack.v1"
PROMPT_SLOT_CONTRACT = "creative-prompt-slot.v1"
PROMPT_VARIANT_CONTRACT = "creative-prompt-variant.v1"
TARGET_CARD_CONTRACT = "creative-target-card.v1"
GENERATION_INTENT_CONTRACT = "creative-generation-intent.v1"
LINT_FINDING_CONTRACT = "creative-recipe-lint-finding.v1"
COMPATIBILITY_REPORT_CONTRACT = "creative-compatibility-report.v1"

MAX_DESCRIPTOR_BYTES = 512 * 1024
MAX_CATALOG_RECIPES = 64
MAX_CATALOG_STYLE_PACKS = 64
MAX_CATALOG_TARGETS = 32
MAX_SLOTS = 32
MAX_VARIANTS = 32
MAX_SLOT_VALUES = 32
MAX_ENUM_VALUES = 32
MAX_TAGS = 16
MAX_PROMPT_CHARS = 4000
MAX_NEGATIVE_PROMPT_CHARS = 2500
MAX_LABEL_CHARS = 120
MAX_DESCRIPTION_CHARS = 900
MAX_PREVIEW_CHARS = 320
MAX_FINDINGS = 64
MAX_COMPATIBILITY_CHECKS = 32
MAX_VARIANT_PLANS = 32
MAX_TEXT_VALUE_CHARS = 800
MAX_DEPTH = 10

MEDIA_KINDS = ("image", "video")
SLOT_TYPES = ("text", "enum", "number", "integer", "boolean", "style", "aspect_ratio", "duration", "seed")
MODEL_FAMILIES = ("flux", "sdxl", "sd-video", "generic-image", "generic-video")
CAPABILITIES = (
    "image.generate",
    "image.edit",
    "image.upscale",
    "video.generate",
    "video.edit",
    "video.upscale",
    "video.interpolate",
)
TARGET_STATUSES = ("operational", "partial", "unavailable", "planned")
LINT_SEVERITIES = ("error", "warning", "info")
LINT_STATUSES = ("unavailable", "partial", "manual_review", "planned")
REPORT_STATUSES = ("operational", "partial", "unavailable", "planned", "manual_review")
LICENSES = ("CC0-1.0", "MIT", "Apache-2.0", "proprietary", "unknown")
PARAMETER_KEYS = ("width", "height", "steps", "seed", "fps", "duration_seconds", "frames", "aspect_ratio")

CATALOG_ID_RE = re.compile(r"^catalog_[a-z0-9][a-z0-9_-]{0,55}$")
RECIPE_ID_RE = re.compile(r"^recipe_[a-z0-9][a-z0-9_-]{0,55}$")
STYLE_PACK_ID_RE = re.compile(r"^style_[a-z0-9][a-z0-9_-]{0,55}$")
SLOT_ID_RE = re.compile(r"^slot_[a-z0-9][a-z0-9_-]{0,55}$")
VARIANT_ID_RE = re.compile(r"^variant_[a-z0-9][a-z0-9_-]{0,55}$")
TARGET_ID_RE = re.compile(r"^target_[a-z0-9][a-z0-9_-]{0,55}$")
INTENT_ID_RE = re.compile(r"^intent_[a-z0-9][a-z0-9_-]{0,55}$")
FINDING_ID_RE = re.compile(r"^finding_[a-z0-9][a-z0-9_-]{0,55}$")
REPORT_ID_RE = re.compile(r"^report_[a-z0-9][a-z0-9_-]{0,55}$")
SEMVER_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
ASPECT_RATIO_RE = re.compile(r"^[1-9][0-9]{0,1}:[1-9][0-9]{0,1}$")

# JSON Schema has no portable case-insensitive flag.  The runtime scrubber is
# stricter; this pattern still rejects representative unsafe values when a
# consumer uses only the published schema.
# Keep the public schema conservative as well as the runtime scrubber.  The
# negative lookaheads intentionally reject paths/URLs/secrets/commands even
# when a consumer validates a descriptor without importing this module.
SAFE_TEXT_PATTERN = r"^(?![\s\S]*(?:[A-Za-z]:[\\/]|\\\\|\\[A-Za-z]|(?:(?:^|[\s])/)(?:etc|usr|var|tmp|home)(?:/|$)|\.\.(?:[\\/])|https?://|file:|data:|javascript:|api[_-]?key\s*[:=]|secret\s*[:=]|token\s*[:=]|password\s*[:=]|credential\s*[:=]|bearer\s+[A-Za-z0-9._-]{8,}|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}|[A-Za-z0-9+/]{96,}={0,2}|<\s*/?\s*(?:script|iframe|img|svg|a)\b|cmd\.exe|powershell|bash\s+-c|python\s+-c))[\s\S]*$"
SAFE_REQUIRED_TEXT_PATTERN = r"^(?![\s\S]*(?:[A-Za-z]:[\\/]|\\\\|\\[A-Za-z]|(?:(?:^|[\s])/)(?:etc|usr|var|tmp|home)(?:/|$)|\.\.(?:[\\/])|https?://|file:|data:|javascript:|api[_-]?key\s*[:=]|secret\s*[:=]|token\s*[:=]|password\s*[:=]|credential\s*[:=]|bearer\s+[A-Za-z0-9._-]{8,}|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}|[A-Za-z0-9+/]{96,}={0,2}|<\s*/?\s*(?:script|iframe|img|svg|a)\b|cmd\.exe|powershell|bash\s+-c|python\s+-c))(?=[\s\S]*\S)[\s\S]*$"
SAFE_ID_PATTERN = r"^[a-z][a-z0-9_-]{2,63}$"
SEMVER_PATTERN = SEMVER_RE.pattern

_SECRET_RE = re.compile(r"(?:api[_-]?key|secret|token|password|credential)\s*[:=]|bearer\s+[A-Za-z0-9._-]{8,}|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}|(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{8,}", re.IGNORECASE)
_URL_RE = re.compile(r"(?:https?|file|data|javascript):", re.IGNORECASE)
_PATH_RE = re.compile(r"(?:^[A-Za-z]:|(?:^|\s)[A-Za-z]:[\\/A-Za-z0-9_.-]|^[\\/]|(?:^|\s)\\[A-Za-z]|\\\\|(?:^|[\\/])\.\.(?:[\\/]|$)|(?:^|\s)/(?:etc|usr|var|tmp|home)(?:/|$))")
_COMMAND_RE = re.compile(r"(?:cmd(?:\.exe)?|powershell|bash|sh|python)\s+(?:-c|/c)|(?:&&|\|\||`)", re.IGNORECASE)
_LONG_BLOB_RE = re.compile(r"[A-Za-z0-9+/]{96,}={0,2}")
_HTML_RE = re.compile(r"<\s*/?\s*(?:script|iframe|img|svg|a)\b", re.IGNORECASE)

_MESSAGES = {
    "object_required": "Value must be a JSON object.",
    "schema_version": "Contract version is unsupported.",
    "unknown_field": "Unknown fields are not permitted.",
    "required": "A required field is missing.",
    "id": "Opaque ID is invalid.",
    "version": "Version must be a supported SemVer value.",
    "text": "Text is missing, unsafe or over the bounded limit.",
    "array": "Value must be a bounded array.",
    "duplicate_id": "Entity IDs must be unique.",
    "duplicate_identity": "Managed entity identity is ambiguous.",
    "enum": "Value is not in the fixed allowlist.",
    "boolean": "Value must be a boolean.",
    "number": "Value must be a finite bounded number.",
    "integer": "Value must be a bounded integer.",
    "slot_default": "Slot default or enum value has the wrong type.",
    "bounds": "Numeric bounds are inconsistent.",
    "reference": "Reference does not resolve inside the validated catalog.",
    "cross_reference": "Nested reference does not resolve inside its recipe.",
    "parameter": "Parameter is not part of the closed allowlist.",
    "status": "Status is not part of the fixed allowlist.",
    "finding": "Lint finding field is invalid.",
    "execution": "This contract is planning-only and must remain not_run.",
    "dry_run": "Generation intent must be dry_run.",
    "descriptor_size": "Descriptor is too large or not bounded JSON.",
    "json_value": "Value is not safe finite JSON.",
}


def _issue(code: str, location: str) -> dict[str, str]:
    return {"code": code, "message": _MESSAGES.get(code, "Value failed closed validation."), "location": location}


def _json_bytes(value: object) -> bytes | None:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError):
        return None


def _depth(value: object, current: int = 0) -> bool:
    if current > MAX_DEPTH:
        return False
    if isinstance(value, Mapping):
        return all(_depth(key, current + 1) and _depth(item, current + 1) for key, item in value.items())
    if isinstance(value, list):
        return all(_depth(item, current + 1) for item in value)
    return True


def _unsafe_text(value: str) -> bool:
    return bool(_SECRET_RE.search(value) or _URL_RE.search(value) or _PATH_RE.search(value) or _COMMAND_RE.search(value) or _LONG_BLOB_RE.search(value.strip()) or _HTML_RE.search(value))


def _scan_json(value: object, errors: list[dict[str, str]], location: str, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        errors.append(_issue("descriptor_size", location))
        return
    if isinstance(value, float) and not math.isfinite(value):
        errors.append(_issue("json_value", location))
        return
    if isinstance(value, str):
        if "\x00" in value or any(ord(char) < 32 and char not in "\r\n\t" for char in value) or _unsafe_text(value):
            errors.append(_issue("text", location))
        return
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            if not isinstance(raw_key, str) or "\x00" in raw_key or _unsafe_text(raw_key):
                errors.append(_issue("text", location))
            # Never copy an untrusted object key into a public error location.
            # Known contract fields remain visible through the caller's static
            # locations; unknown/malicious keys are represented generically.
            safe_key = raw_key if isinstance(raw_key, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", raw_key) and not _unsafe_text(raw_key) else "field"
            _scan_json(item, errors, f"{location}.{safe_key}", depth + 1)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _scan_json(item, errors, f"{location}[{index}]", depth + 1)
    elif value is not None and not isinstance(value, (bool, int, float)):
        errors.append(_issue("json_value", location))


def _base(value: object, location: str) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    errors: list[dict[str, str]] = []
    encoded = _json_bytes(value)
    if encoded is None or len(encoded) > MAX_DESCRIPTOR_BYTES or not _depth(value):
        return None, [_issue("descriptor_size", location)]
    if not isinstance(value, dict):
        return None, [_issue("object_required", location)]
    _scan_json(value, errors, location)
    return value, errors


def _strict(value: Mapping[str, Any], fields: set[str], required: tuple[str, ...], errors: list[dict[str, str]], location: str) -> None:
    for key in value:
        if key not in fields:
            # Keep validation diagnostics detached from attacker-controlled
            # field names; callers only need the stable issue code/location.
            errors.append(_issue("unknown_field", location))
    for key in required:
        if key not in value:
            errors.append(_issue("required", f"{location}.{key}"))


def _text(value: object, errors: list[dict[str, str]], location: str, maximum: int, *, allow_empty: bool = False) -> str | None:
    if not isinstance(value, str):
        errors.append(_issue("text", location))
        return None
    if not allow_empty and not value.strip():
        errors.append(_issue("text", location))
    if len(value) > maximum or "\x00" in value or any(ord(char) < 32 and char not in "\r\n\t" for char in value) or _unsafe_text(value):
        errors.append(_issue("text", location))
    return value.strip()


def _id(value: object, pattern: re.Pattern[str], errors: list[dict[str, str]], location: str) -> str | None:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        errors.append(_issue("id", location))
        return None
    return value


def _version(value: object, errors: list[dict[str, str]], location: str) -> str | None:
    if not isinstance(value, str) or SEMVER_RE.fullmatch(value) is None:
        errors.append(_issue("version", location))
        return None
    return value


def _boolean(value: object, errors: list[dict[str, str]], location: str) -> bool:
    if not isinstance(value, bool):
        errors.append(_issue("boolean", location))
        return False
    return value


def _number(value: object, errors: list[dict[str, str]], location: str, *, integer: bool = False, minimum: float | int | None = None, maximum: float | int | None = None) -> int | float | None:
    try:
        finite = math.isfinite(float(value)) if isinstance(value, (int, float)) and not isinstance(value, bool) else False
    except (OverflowError, ValueError):
        finite = False
    if not finite:
        errors.append(_issue("integer" if integer else "number", location))
        return None
    if integer and not isinstance(value, int):
        errors.append(_issue("integer", location))
        return None
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        errors.append(_issue("number" if not integer else "integer", location))
        return None
    return value


def _array(value: object, errors: list[dict[str, str]], location: str, maximum: int, *, minimum: int = 0) -> list[Any]:
    if not isinstance(value, list) or len(value) < minimum or len(value) > maximum:
        errors.append(_issue("array", location))
        return []
    return value


def _unique_ids(items: list[dict[str, Any]], key: str, errors: list[dict[str, str]], location: str, *, identity_key: Callable[[dict[str, Any]], str] | None = None) -> None:
    seen: set[str] = set()
    for index, item in enumerate(items):
        identity = identity_key(item) if identity_key else str(item.get(key, ""))
        if identity in seen:
            errors.append(_issue("duplicate_identity" if identity_key else "duplicate_id", f"{location}[{index}].{key}"))
        seen.add(identity)


def _tags(value: object, errors: list[dict[str, str]], location: str) -> list[str]:
    raw = _array(value, errors, location, MAX_TAGS)
    result: list[str] = []
    for index, item in enumerate(raw):
        text = _text(item, errors, f"{location}[{index}]", 40)
        if text is not None and text.lower() not in result:
            result.append(text.lower())
    return sorted(result)


def _preview(value: object, errors: list[dict[str, str]], location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(_issue("object_required", location))
        return {"title": "", "summary": "", "tags": [], "icon": "generic"}
    fields = {"title", "summary", "tags", "icon"}
    _strict(value, fields, ("title", "summary", "tags", "icon"), errors, location)
    title = _text(value.get("title"), errors, f"{location}.title", MAX_PREVIEW_CHARS)
    summary = _text(value.get("summary"), errors, f"{location}.summary", MAX_PREVIEW_CHARS, allow_empty=True)
    tags = _tags(value.get("tags"), errors, f"{location}.tags")
    icon = value.get("icon")
    if icon not in {"image", "video", "portrait", "product", "edit", "generic"}:
        errors.append(_issue("enum", f"{location}.icon"))
        icon = "generic"
    return {"title": title or "", "summary": summary or "", "tags": tags, "icon": icon}


def _typed_slot_value(slot_type: str, value: object, errors: list[dict[str, str]], location: str, *, minimum: float | int | None = None, maximum: float | int | None = None, enum_values: list[Any] | None = None) -> Any:
    if slot_type in {"text", "style", "aspect_ratio"}:
        if not isinstance(value, str):
            errors.append(_issue("slot_default", location))
            return None
        if slot_type == "aspect_ratio" and ASPECT_RATIO_RE.fullmatch(value) is None:
            errors.append(_issue("slot_default", location))
        if _unsafe_text(value) or len(value) > MAX_TEXT_VALUE_CHARS:
            errors.append(_issue("text", location))
    elif slot_type == "enum":
        if not isinstance(value, str) or enum_values is None or value not in enum_values:
            errors.append(_issue("slot_default", location))
    elif slot_type == "number":
        return _number(value, errors, location, minimum=minimum, maximum=maximum)
    elif slot_type == "integer":
        return _number(value, errors, location, integer=True, minimum=minimum, maximum=maximum)
    elif slot_type == "duration":
        return _number(value, errors, location, minimum=0 if minimum is None else minimum, maximum=3600 if maximum is None else maximum)
    elif slot_type == "seed":
        return _number(value, errors, location, integer=True, minimum=0, maximum=4294967295)
    elif slot_type == "boolean":
        return _boolean(value, errors, location)
    return value


def _validate_slot(value: object, location: str = "slot") -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    candidate, errors = _base(value, location)
    if candidate is None:
        return None, errors
    fields = {"schema_version", "id", "label", "type", "required", "default", "enum", "minimum", "maximum", "unit", "description"}
    _strict(candidate, fields, ("schema_version", "id", "label", "type", "required"), errors, location)
    if candidate.get("schema_version") != PROMPT_SLOT_CONTRACT:
        errors.append(_issue("schema_version", f"{location}.schema_version"))
    slot_id = _id(candidate.get("id"), SLOT_ID_RE, errors, f"{location}.id")
    label = _text(candidate.get("label"), errors, f"{location}.label", MAX_LABEL_CHARS)
    slot_type = candidate.get("type")
    if slot_type not in SLOT_TYPES:
        errors.append(_issue("enum", f"{location}.type"))
        slot_type = "text"
    required = _boolean(candidate.get("required"), errors, f"{location}.required")
    minimum = candidate.get("minimum")
    maximum = candidate.get("maximum")
    if minimum is not None:
        minimum = _number(minimum, errors, f"{location}.minimum")
    if maximum is not None:
        maximum = _number(maximum, errors, f"{location}.maximum")
    if minimum is not None and maximum is not None and minimum > maximum:
        errors.append(_issue("bounds", location))
    enum_values: list[Any] | None = None
    if "enum" in candidate:
        enum_values = _array(candidate.get("enum"), errors, f"{location}.enum", MAX_ENUM_VALUES, minimum=1)
        normalized_enum: list[Any] = []
        for index, item in enumerate(enum_values):
            before = len(errors)
            normalized_enum.append(_typed_slot_value(str(slot_type), item, errors, f"{location}.enum[{index}]", minimum=minimum, maximum=maximum, enum_values=enum_values))
            if len(errors) > before and slot_type == "enum":
                continue
        if len({json.dumps(item, sort_keys=True, allow_nan=False) for item in normalized_enum}) != len(normalized_enum):
            errors.append(_issue("duplicate_id", f"{location}.enum"))
        enum_values = normalized_enum
    if "default" in candidate:
        _typed_slot_value(str(slot_type), candidate.get("default"), errors, f"{location}.default", minimum=minimum, maximum=maximum, enum_values=enum_values)
    if slot_type not in {"number", "integer", "duration"} and any(key in candidate for key in ("minimum", "maximum")):
        errors.append(_issue("parameter", f"{location}.minimum"))
    unit = None
    if "unit" in candidate:
        unit = _text(candidate.get("unit"), errors, f"{location}.unit", 32, allow_empty=True)
    description = None
    if "description" in candidate:
        description = _text(candidate.get("description"), errors, f"{location}.description", MAX_DESCRIPTION_CHARS, allow_empty=True)
    normalized: dict[str, Any] = {"schema_version": PROMPT_SLOT_CONTRACT, "id": slot_id or "slot_invalid", "label": label or "", "type": slot_type, "required": required}
    if "default" in candidate:
        normalized["default"] = copy.deepcopy(candidate.get("default"))
    if enum_values is not None:
        normalized["enum"] = copy.deepcopy(enum_values)
    if minimum is not None:
        normalized["minimum"] = minimum
    if maximum is not None:
        normalized["maximum"] = maximum
    if unit is not None:
        normalized["unit"] = unit
    if description is not None:
        normalized["description"] = description
    return (normalized if slot_id else None), errors


def _validate_variant(value: object, location: str = "variant") -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    candidate, errors = _base(value, location)
    if candidate is None:
        return None, errors
    fields = {"schema_version", "id", "label", "prompt_template", "negative_prompt", "slot_values", "style_pack_ids"}
    _strict(candidate, fields, ("schema_version", "id", "label", "prompt_template", "negative_prompt", "slot_values", "style_pack_ids"), errors, location)
    if candidate.get("schema_version") != PROMPT_VARIANT_CONTRACT:
        errors.append(_issue("schema_version", f"{location}.schema_version"))
    variant_id = _id(candidate.get("id"), VARIANT_ID_RE, errors, f"{location}.id")
    label = _text(candidate.get("label"), errors, f"{location}.label", MAX_LABEL_CHARS)
    prompt = _text(candidate.get("prompt_template"), errors, f"{location}.prompt_template", MAX_PROMPT_CHARS, allow_empty=True)
    negative = _text(candidate.get("negative_prompt"), errors, f"{location}.negative_prompt", MAX_NEGATIVE_PROMPT_CHARS, allow_empty=True)
    raw_values = _array(candidate.get("slot_values"), errors, f"{location}.slot_values", MAX_SLOT_VALUES)
    values: list[dict[str, Any]] = []
    seen_slots: set[str] = set()
    for index, item in enumerate(raw_values):
        item_location = f"{location}.slot_values[{index}]"
        if not isinstance(item, dict):
            errors.append(_issue("object_required", item_location))
            continue
        _strict(item, {"slot_id", "value"}, ("slot_id", "value"), errors, item_location)
        slot_id = _id(item.get("slot_id"), SLOT_ID_RE, errors, f"{item_location}.slot_id")
        if slot_id and slot_id in seen_slots:
            errors.append(_issue("duplicate_id", f"{item_location}.slot_id"))
        if slot_id:
            seen_slots.add(slot_id)
        raw_value = item.get("value")
        if not isinstance(raw_value, (str, int, float, bool)) or isinstance(raw_value, float) and not math.isfinite(raw_value):
            errors.append(_issue("json_value", f"{item_location}.value"))
        elif isinstance(raw_value, str) and (_unsafe_text(raw_value) or len(raw_value) > MAX_TEXT_VALUE_CHARS):
            errors.append(_issue("text", f"{item_location}.value"))
        values.append({"slot_id": slot_id or "slot_invalid", "value": copy.deepcopy(raw_value)})
    raw_styles = _array(candidate.get("style_pack_ids"), errors, f"{location}.style_pack_ids", MAX_CATALOG_STYLE_PACKS)
    style_ids: list[str] = []
    for index, item in enumerate(raw_styles):
        style_id = _id(item, STYLE_PACK_ID_RE, errors, f"{location}.style_pack_ids[{index}]")
        if style_id and style_id not in style_ids:
            style_ids.append(style_id)
        elif style_id:
            errors.append(_issue("duplicate_id", f"{location}.style_pack_ids[{index}]"))
    normalized = {"schema_version": PROMPT_VARIANT_CONTRACT, "id": variant_id or "variant_invalid", "label": label or "", "prompt_template": prompt or "", "negative_prompt": negative or "", "slot_values": sorted(values, key=lambda item: item["slot_id"]), "style_pack_ids": sorted(style_ids)}
    return (normalized if variant_id else None), errors


def _validate_style_pack(value: object, location: str = "style_pack") -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    candidate, errors = _base(value, location)
    if candidate is None:
        return None, errors
    fields = {"schema_version", "id", "version", "label", "description", "positive_prompt", "negative_prompt", "media_kinds", "tags", "preview", "license"}
    _strict(candidate, fields, ("schema_version", "id", "version", "label", "description", "positive_prompt", "negative_prompt", "media_kinds", "tags", "preview", "license"), errors, location)
    if candidate.get("schema_version") != STYLE_PACK_CONTRACT:
        errors.append(_issue("schema_version", f"{location}.schema_version"))
    style_id = _id(candidate.get("id"), STYLE_PACK_ID_RE, errors, f"{location}.id")
    version = _version(candidate.get("version"), errors, f"{location}.version")
    label = _text(candidate.get("label"), errors, f"{location}.label", MAX_LABEL_CHARS)
    description = _text(candidate.get("description"), errors, f"{location}.description", MAX_DESCRIPTION_CHARS, allow_empty=True)
    positive = _text(candidate.get("positive_prompt"), errors, f"{location}.positive_prompt", MAX_PROMPT_CHARS, allow_empty=True)
    negative = _text(candidate.get("negative_prompt"), errors, f"{location}.negative_prompt", MAX_NEGATIVE_PROMPT_CHARS, allow_empty=True)
    raw_media = _array(candidate.get("media_kinds"), errors, f"{location}.media_kinds", 2, minimum=1)
    media: list[str] = []
    for index, item in enumerate(raw_media):
        if item not in MEDIA_KINDS:
            errors.append(_issue("enum", f"{location}.media_kinds[{index}]"))
        elif item not in media:
            media.append(item)
    tags = _tags(candidate.get("tags"), errors, f"{location}.tags")
    preview = _preview(candidate.get("preview"), errors, f"{location}.preview")
    license_value = candidate.get("license")
    if license_value not in LICENSES:
        errors.append(_issue("enum", f"{location}.license"))
        license_value = "unknown"
    normalized = {"schema_version": STYLE_PACK_CONTRACT, "id": style_id or "style_invalid", "version": version or "0.0.0", "label": label or "", "description": description or "", "positive_prompt": positive or "", "negative_prompt": negative or "", "media_kinds": sorted(media), "tags": tags, "preview": preview, "license": license_value}
    return (normalized if style_id and version else None), errors


def _validate_compatibility(value: object, errors: list[dict[str, str]], location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(_issue("object_required", location))
        return {"target_ids": [], "required_capabilities": [], "model_families": []}
    fields = {"target_ids", "required_capabilities", "model_families"}
    _strict(value, fields, ("target_ids", "required_capabilities", "model_families"), errors, location)
    targets_raw = _array(value.get("target_ids"), errors, f"{location}.target_ids", MAX_CATALOG_TARGETS)
    targets: list[str] = []
    for index, item in enumerate(targets_raw):
        target_id = _id(item, TARGET_ID_RE, errors, f"{location}.target_ids[{index}]")
        if target_id and target_id not in targets:
            targets.append(target_id)
        elif target_id:
            errors.append(_issue("duplicate_id", f"{location}.target_ids[{index}]"))
    cap_raw = _array(value.get("required_capabilities"), errors, f"{location}.required_capabilities", len(CAPABILITIES))
    capabilities: list[str] = []
    for index, item in enumerate(cap_raw):
        if item not in CAPABILITIES:
            errors.append(_issue("enum", f"{location}.required_capabilities[{index}]"))
        elif item not in capabilities:
            capabilities.append(item)
        else:
            errors.append(_issue("duplicate_id", f"{location}.required_capabilities[{index}]"))
    model_raw = _array(value.get("model_families"), errors, f"{location}.model_families", len(MODEL_FAMILIES))
    models: list[str] = []
    for index, item in enumerate(model_raw):
        if item not in MODEL_FAMILIES:
            errors.append(_issue("enum", f"{location}.model_families[{index}]"))
        elif item not in models:
            models.append(item)
        else:
            errors.append(_issue("duplicate_id", f"{location}.model_families[{index}]"))
    return {"target_ids": sorted(targets), "required_capabilities": sorted(capabilities), "model_families": sorted(models)}


def _validate_constraints(value: object, errors: list[dict[str, str]], location: str) -> dict[str, Any]:
    fields = {"aspect_ratios", "min_width", "max_width", "min_height", "max_height", "max_frames", "max_duration_seconds", "max_fps", "default_parameters"}
    if not isinstance(value, dict):
        errors.append(_issue("object_required", location))
        return {"aspect_ratios": [], "default_parameters": {}}
    _strict(value, fields, ("aspect_ratios", "default_parameters"), errors, location)
    ratios_raw = _array(value.get("aspect_ratios"), errors, f"{location}.aspect_ratios", 16)
    ratios: list[str] = []
    for index, item in enumerate(ratios_raw):
        if not isinstance(item, str) or ASPECT_RATIO_RE.fullmatch(item) is None:
            errors.append(_issue("enum", f"{location}.aspect_ratios[{index}]"))
        elif item not in ratios:
            ratios.append(item)
    limits: dict[str, Any] = {}
    integer_limits = {"min_width": (1, 16384), "max_width": (1, 16384), "min_height": (1, 16384), "max_height": (1, 16384), "max_frames": (1, 10000)}
    number_limits = {"max_duration_seconds": (0.1, 3600), "max_fps": (1, 240)}
    for key, (minimum, maximum) in integer_limits.items():
        if key in value:
            limits[key] = _number(value.get(key), errors, f"{location}.{key}", integer=True, minimum=minimum, maximum=maximum)
    for key, (minimum, maximum) in number_limits.items():
        if key in value:
            limits[key] = _number(value.get(key), errors, f"{location}.{key}", minimum=minimum, maximum=maximum)
    if limits.get("min_width") is not None and limits.get("max_width") is not None and limits["min_width"] > limits["max_width"]:
        errors.append(_issue("bounds", location))
    if limits.get("min_height") is not None and limits.get("max_height") is not None and limits["min_height"] > limits["max_height"]:
        errors.append(_issue("bounds", location))
    defaults = _validate_parameters(value.get("default_parameters"), errors, f"{location}.default_parameters")
    normalized = {"aspect_ratios": sorted(ratios), "default_parameters": defaults}
    normalized.update({key: item for key, item in limits.items() if item is not None})
    return normalized


def _validate_parameters(value: object, errors: list[dict[str, str]], location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(_issue("object_required", location))
        return {}
    _strict(value, set(PARAMETER_KEYS), (), errors, location)
    result: dict[str, Any] = {}
    if "width" in value:
        result["width"] = _number(value["width"], errors, f"{location}.width", integer=True, minimum=1, maximum=16384)
    if "height" in value:
        result["height"] = _number(value["height"], errors, f"{location}.height", integer=True, minimum=1, maximum=16384)
    if "steps" in value:
        result["steps"] = _number(value["steps"], errors, f"{location}.steps", integer=True, minimum=1, maximum=200)
    if "seed" in value:
        result["seed"] = _number(value["seed"], errors, f"{location}.seed", integer=True, minimum=0, maximum=4294967295)
    if "fps" in value:
        result["fps"] = _number(value["fps"], errors, f"{location}.fps", minimum=1, maximum=240)
    if "duration_seconds" in value:
        result["duration_seconds"] = _number(value["duration_seconds"], errors, f"{location}.duration_seconds", minimum=0.1, maximum=3600)
    if "frames" in value:
        result["frames"] = _number(value["frames"], errors, f"{location}.frames", integer=True, minimum=1, maximum=10000)
    if "aspect_ratio" in value:
        ratio = value["aspect_ratio"]
        if not isinstance(ratio, str) or ASPECT_RATIO_RE.fullmatch(ratio) is None:
            errors.append(_issue("enum", f"{location}.aspect_ratio"))
        else:
            result["aspect_ratio"] = ratio
    return {key: item for key, item in result.items() if item is not None}


def _validate_recipe(value: object, location: str = "recipe") -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    candidate, errors = _base(value, location)
    if candidate is None:
        return None, errors
    fields = {"schema_version", "id", "version", "label", "description", "author", "license", "media_kind", "prompt_template", "negative_prompt", "slots", "variants", "style_pack_ids", "compatibility", "constraints", "preview", "tags"}
    _strict(candidate, fields, ("schema_version", "id", "version", "label", "description", "author", "license", "media_kind", "prompt_template", "negative_prompt", "slots", "variants", "style_pack_ids", "compatibility", "constraints", "preview", "tags"), errors, location)
    if candidate.get("schema_version") != RECIPE_CONTRACT:
        errors.append(_issue("schema_version", f"{location}.schema_version"))
    recipe_id = _id(candidate.get("id"), RECIPE_ID_RE, errors, f"{location}.id")
    version = _version(candidate.get("version"), errors, f"{location}.version")
    label = _text(candidate.get("label"), errors, f"{location}.label", MAX_LABEL_CHARS)
    description = _text(candidate.get("description"), errors, f"{location}.description", MAX_DESCRIPTION_CHARS, allow_empty=True)
    author = _text(candidate.get("author"), errors, f"{location}.author", 80)
    license_value = candidate.get("license")
    if license_value not in LICENSES:
        errors.append(_issue("enum", f"{location}.license"))
        license_value = "unknown"
    media_kind = candidate.get("media_kind")
    if media_kind not in MEDIA_KINDS:
        errors.append(_issue("enum", f"{location}.media_kind"))
        media_kind = "image"
    prompt = _text(candidate.get("prompt_template"), errors, f"{location}.prompt_template", MAX_PROMPT_CHARS)
    negative = _text(candidate.get("negative_prompt"), errors, f"{location}.negative_prompt", MAX_NEGATIVE_PROMPT_CHARS, allow_empty=True)
    raw_slots = _array(candidate.get("slots"), errors, f"{location}.slots", MAX_SLOTS)
    slots: list[dict[str, Any]] = []
    for index, item in enumerate(raw_slots):
        normalized, slot_errors = _validate_slot(item, f"{location}.slots[{index}]")
        errors.extend(slot_errors)
        if normalized is not None:
            slots.append(normalized)
    _unique_ids(slots, "id", errors, f"{location}.slots")
    slot_ids = {item["id"] for item in slots}
    raw_variants = _array(candidate.get("variants"), errors, f"{location}.variants", MAX_VARIANTS)
    variants: list[dict[str, Any]] = []
    for index, item in enumerate(raw_variants):
        normalized, variant_errors = _validate_variant(item, f"{location}.variants[{index}]")
        errors.extend(variant_errors)
        if normalized is not None:
            variants.append(normalized)
            for slot_value in normalized["slot_values"]:
                if slot_value["slot_id"] not in slot_ids:
                    errors.append(_issue("cross_reference", f"{location}.variants[{index}].slot_values"))
    _unique_ids(variants, "id", errors, f"{location}.variants")
    raw_styles = _array(candidate.get("style_pack_ids"), errors, f"{location}.style_pack_ids", MAX_CATALOG_STYLE_PACKS)
    styles: list[str] = []
    for index, item in enumerate(raw_styles):
        style_id = _id(item, STYLE_PACK_ID_RE, errors, f"{location}.style_pack_ids[{index}]")
        if style_id and style_id not in styles:
            styles.append(style_id)
        elif style_id:
            errors.append(_issue("duplicate_id", f"{location}.style_pack_ids[{index}]"))
    compatibility = _validate_compatibility(candidate.get("compatibility"), errors, f"{location}.compatibility")
    constraints = _validate_constraints(candidate.get("constraints"), errors, f"{location}.constraints")
    preview = _preview(candidate.get("preview"), errors, f"{location}.preview")
    tags = _tags(candidate.get("tags"), errors, f"{location}.tags")
    normalized = {"schema_version": RECIPE_CONTRACT, "id": recipe_id or "recipe_invalid", "version": version or "0.0.0", "label": label or "", "description": description or "", "author": author or "", "license": license_value, "media_kind": media_kind, "prompt_template": prompt or "", "negative_prompt": negative or "", "slots": sorted(slots, key=lambda item: item["id"]), "variants": sorted(variants, key=lambda item: item["id"]), "style_pack_ids": sorted(styles), "compatibility": compatibility, "constraints": constraints, "preview": preview, "tags": tags}
    return (normalized if recipe_id and version else None), errors


def _validate_target(value: object, location: str = "target") -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    candidate, errors = _base(value, location)
    if candidate is None:
        return None, errors
    fields = {"schema_version", "id", "version", "label", "description", "media_kind", "capabilities", "model_families", "status", "reason_code", "action_code", "preview"}
    _strict(candidate, fields, ("schema_version", "id", "version", "label", "description", "media_kind", "capabilities", "model_families", "status", "reason_code", "action_code", "preview"), errors, location)
    if candidate.get("schema_version") != TARGET_CARD_CONTRACT:
        errors.append(_issue("schema_version", f"{location}.schema_version"))
    target_id = _id(candidate.get("id"), TARGET_ID_RE, errors, f"{location}.id")
    version = _version(candidate.get("version"), errors, f"{location}.version")
    label = _text(candidate.get("label"), errors, f"{location}.label", MAX_LABEL_CHARS)
    description = _text(candidate.get("description"), errors, f"{location}.description", MAX_DESCRIPTION_CHARS, allow_empty=True)
    media_kind = candidate.get("media_kind")
    if media_kind not in MEDIA_KINDS:
        errors.append(_issue("enum", f"{location}.media_kind"))
        media_kind = "image"
    capabilities_raw = _array(candidate.get("capabilities"), errors, f"{location}.capabilities", len(CAPABILITIES), minimum=1)
    capabilities: list[str] = []
    for index, item in enumerate(capabilities_raw):
        if item not in CAPABILITIES:
            errors.append(_issue("enum", f"{location}.capabilities[{index}]"))
        elif item not in capabilities:
            capabilities.append(item)
        else:
            errors.append(_issue("duplicate_id", f"{location}.capabilities[{index}]"))
    models_raw = _array(candidate.get("model_families"), errors, f"{location}.model_families", len(MODEL_FAMILIES), minimum=1)
    models: list[str] = []
    for index, item in enumerate(models_raw):
        if item not in MODEL_FAMILIES:
            errors.append(_issue("enum", f"{location}.model_families[{index}]"))
        elif item not in models:
            models.append(item)
        else:
            errors.append(_issue("duplicate_id", f"{location}.model_families[{index}]"))
    status = candidate.get("status")
    if status not in TARGET_STATUSES:
        errors.append(_issue("status", f"{location}.status"))
        status = "unavailable"
    reason_code = candidate.get("reason_code")
    action_code = candidate.get("action_code")
    if not isinstance(reason_code, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", reason_code):
        errors.append(_issue("enum", f"{location}.reason_code"))
        reason_code = "target_unverified"
    if not isinstance(action_code, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", action_code):
        errors.append(_issue("enum", f"{location}.action_code"))
        action_code = "review_target"
    preview = _preview(candidate.get("preview"), errors, f"{location}.preview")
    normalized = {"schema_version": TARGET_CARD_CONTRACT, "id": target_id or "target_invalid", "version": version or "0.0.0", "label": label or "", "description": description or "", "media_kind": media_kind, "capabilities": sorted(capabilities), "model_families": sorted(models), "status": status, "reason_code": reason_code, "action_code": action_code, "preview": preview}
    return (normalized if target_id and version else None), errors


def _validate_catalog(value: object, location: str = "catalog") -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    candidate, errors = _base(value, location)
    if candidate is None:
        return None, errors
    fields = {"schema_version", "id", "version", "label", "description", "author", "license", "recipes", "style_packs", "targets", "preview", "tags"}
    _strict(candidate, fields, ("schema_version", "id", "version", "label", "description", "author", "license", "recipes", "style_packs", "targets", "preview", "tags"), errors, location)
    if candidate.get("schema_version") != CATALOG_CONTRACT:
        errors.append(_issue("schema_version", f"{location}.schema_version"))
    catalog_id = _id(candidate.get("id"), CATALOG_ID_RE, errors, f"{location}.id")
    version = _version(candidate.get("version"), errors, f"{location}.version")
    label = _text(candidate.get("label"), errors, f"{location}.label", MAX_LABEL_CHARS)
    description = _text(candidate.get("description"), errors, f"{location}.description", MAX_DESCRIPTION_CHARS, allow_empty=True)
    author = _text(candidate.get("author"), errors, f"{location}.author", 80)
    license_value = candidate.get("license")
    if license_value not in LICENSES:
        errors.append(_issue("enum", f"{location}.license"))
        license_value = "unknown"
    raw_recipes = _array(candidate.get("recipes"), errors, f"{location}.recipes", MAX_CATALOG_RECIPES, minimum=1)
    recipes: list[dict[str, Any]] = []
    for index, item in enumerate(raw_recipes):
        normalized, item_errors = _validate_recipe(item, f"{location}.recipes[{index}]")
        errors.extend(item_errors)
        if normalized is not None:
            recipes.append(normalized)
    _unique_ids(recipes, "id", errors, f"{location}.recipes", identity_key=lambda item: f"{item.get('id')}@{item.get('version')}")
    _unique_ids(recipes, "id", errors, f"{location}.recipes")
    raw_styles = _array(candidate.get("style_packs"), errors, f"{location}.style_packs", MAX_CATALOG_STYLE_PACKS)
    styles: list[dict[str, Any]] = []
    for index, item in enumerate(raw_styles):
        normalized, item_errors = _validate_style_pack(item, f"{location}.style_packs[{index}]")
        errors.extend(item_errors)
        if normalized is not None:
            styles.append(normalized)
    _unique_ids(styles, "id", errors, f"{location}.style_packs", identity_key=lambda item: f"{item.get('id')}@{item.get('version')}")
    _unique_ids(styles, "id", errors, f"{location}.style_packs")
    raw_targets = _array(candidate.get("targets"), errors, f"{location}.targets", MAX_CATALOG_TARGETS, minimum=1)
    targets: list[dict[str, Any]] = []
    for index, item in enumerate(raw_targets):
        normalized, item_errors = _validate_target(item, f"{location}.targets[{index}]")
        errors.extend(item_errors)
        if normalized is not None:
            targets.append(normalized)
    _unique_ids(targets, "id", errors, f"{location}.targets", identity_key=lambda item: f"{item.get('id')}@{item.get('version')}")
    _unique_ids(targets, "id", errors, f"{location}.targets")
    style_ids = {item["id"] for item in styles}
    target_ids = {item["id"] for item in targets}
    for index, recipe in enumerate(recipes):
        for style_id in recipe["style_pack_ids"]:
            if style_id not in style_ids:
                errors.append(_issue("reference", f"{location}.recipes[{index}].style_pack_ids"))
        for target_id in recipe["compatibility"]["target_ids"]:
            if target_id not in target_ids:
                errors.append(_issue("reference", f"{location}.recipes[{index}].compatibility.target_ids"))
        for variant in recipe["variants"]:
            for style_id in variant["style_pack_ids"]:
                if style_id not in style_ids:
                    errors.append(_issue("reference", f"{location}.recipes[{index}].variants"))
    preview = _preview(candidate.get("preview"), errors, f"{location}.preview")
    tags = _tags(candidate.get("tags"), errors, f"{location}.tags")
    normalized = {"schema_version": CATALOG_CONTRACT, "id": catalog_id or "catalog_invalid", "version": version or "0.0.0", "label": label or "", "description": description or "", "author": author or "", "license": license_value, "recipes": sorted(recipes, key=lambda item: (item["id"], item["version"])), "style_packs": sorted(styles, key=lambda item: (item["id"], item["version"])), "targets": sorted(targets, key=lambda item: (item["id"], item["version"])), "preview": preview, "tags": tags}
    return (normalized if catalog_id and version else None), errors


def _validate_finding(value: object, location: str = "finding") -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    candidate, errors = _base(value, location)
    if candidate is None:
        return None, errors
    fields = {"schema_version", "id", "code", "severity", "status", "subject_kind", "subject_id", "reason_code", "reason", "action_code", "action", "evidence_count"}
    _strict(candidate, fields, ("schema_version", "id", "code", "severity", "status", "subject_kind", "subject_id", "reason_code", "reason", "action_code", "action", "evidence_count"), errors, location)
    if candidate.get("schema_version") != LINT_FINDING_CONTRACT:
        errors.append(_issue("schema_version", f"{location}.schema_version"))
    finding_id = _id(candidate.get("id"), FINDING_ID_RE, errors, f"{location}.id")
    code = candidate.get("code")
    if not isinstance(code, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", code):
        errors.append(_issue("finding", f"{location}.code"))
        code = "invalid_finding"
    severity = candidate.get("severity")
    if severity not in LINT_SEVERITIES:
        errors.append(_issue("finding", f"{location}.severity"))
        severity = "error"
    status = candidate.get("status")
    if status not in LINT_STATUSES:
        errors.append(_issue("status", f"{location}.status"))
        status = "unavailable"
    subject_kind = candidate.get("subject_kind")
    if subject_kind not in {"catalog", "recipe", "slot", "variant", "style_pack", "target", "compatibility"}:
        errors.append(_issue("finding", f"{location}.subject_kind"))
        subject_kind = "recipe"
    subject_id = _text(candidate.get("subject_id"), errors, f"{location}.subject_id", 80)
    reason_code = candidate.get("reason_code")
    action_code = candidate.get("action_code")
    if not isinstance(reason_code, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", reason_code):
        errors.append(_issue("finding", f"{location}.reason_code"))
        reason_code = "lint_review"
    if not isinstance(action_code, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", action_code):
        errors.append(_issue("finding", f"{location}.action_code"))
        action_code = "review_recipe"
    reason = _text(candidate.get("reason"), errors, f"{location}.reason", MAX_DESCRIPTION_CHARS)
    action = _text(candidate.get("action"), errors, f"{location}.action", MAX_DESCRIPTION_CHARS)
    evidence_count = _number(candidate.get("evidence_count"), errors, f"{location}.evidence_count", integer=True, minimum=0, maximum=100000)
    normalized = {"schema_version": LINT_FINDING_CONTRACT, "id": finding_id or "finding_invalid", "code": code, "severity": severity, "status": status, "subject_kind": subject_kind, "subject_id": subject_id or "unknown", "reason_code": reason_code, "reason": reason or "", "action_code": action_code, "action": action or "", "evidence_count": evidence_count or 0}
    return (normalized if finding_id else None), errors


def _validate_intent(value: object, location: str = "intent") -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    candidate, errors = _base(value, location)
    if candidate is None:
        return None, errors
    fields = {"schema_version", "id", "recipe_id", "recipe_version", "variant_id", "target_id", "media_kind", "prompt", "negative_prompt", "slot_values", "style_pack_ids", "parameters", "status", "reason_code", "reason", "action_code", "action", "dry_run", "execution", "source_fingerprint"}
    _strict(candidate, fields, ("schema_version", "id", "recipe_id", "recipe_version", "variant_id", "target_id", "media_kind", "prompt", "negative_prompt", "slot_values", "style_pack_ids", "parameters", "status", "reason_code", "reason", "action_code", "action", "dry_run", "execution", "source_fingerprint"), errors, location)
    if candidate.get("schema_version") != GENERATION_INTENT_CONTRACT:
        errors.append(_issue("schema_version", f"{location}.schema_version"))
    intent_id = _id(candidate.get("id"), INTENT_ID_RE, errors, f"{location}.id")
    recipe_id = _id(candidate.get("recipe_id"), RECIPE_ID_RE, errors, f"{location}.recipe_id")
    recipe_version = _version(candidate.get("recipe_version"), errors, f"{location}.recipe_version")
    variant_id = candidate.get("variant_id")
    if variant_id is not None:
        variant_id = _id(variant_id, VARIANT_ID_RE, errors, f"{location}.variant_id")
    target_id = _id(candidate.get("target_id"), TARGET_ID_RE, errors, f"{location}.target_id")
    media_kind = candidate.get("media_kind")
    if media_kind not in MEDIA_KINDS:
        errors.append(_issue("enum", f"{location}.media_kind"))
        media_kind = "image"
    prompt = _text(candidate.get("prompt"), errors, f"{location}.prompt", MAX_PROMPT_CHARS)
    negative = _text(candidate.get("negative_prompt"), errors, f"{location}.negative_prompt", MAX_NEGATIVE_PROMPT_CHARS, allow_empty=True)
    raw_values = _array(candidate.get("slot_values"), errors, f"{location}.slot_values", MAX_SLOT_VALUES)
    values: list[dict[str, Any]] = []
    for index, item in enumerate(raw_values):
        item_location = f"{location}.slot_values[{index}]"
        if not isinstance(item, dict):
            errors.append(_issue("object_required", item_location))
            continue
        _strict(item, {"slot_id", "value"}, ("slot_id", "value"), errors, item_location)
        slot_id = _id(item.get("slot_id"), SLOT_ID_RE, errors, f"{item_location}.slot_id")
        raw = item.get("value")
        if not isinstance(raw, (str, int, float, bool)) or isinstance(raw, float) and not math.isfinite(raw):
            errors.append(_issue("json_value", f"{item_location}.value"))
        values.append({"slot_id": slot_id or "slot_invalid", "value": copy.deepcopy(raw)})
    _unique_ids(values, "slot_id", errors, f"{location}.slot_values")
    raw_styles = _array(candidate.get("style_pack_ids"), errors, f"{location}.style_pack_ids", MAX_CATALOG_STYLE_PACKS)
    styles: list[str] = []
    for index, item in enumerate(raw_styles):
        style_id = _id(item, STYLE_PACK_ID_RE, errors, f"{location}.style_pack_ids[{index}]")
        if style_id and style_id not in styles:
            styles.append(style_id)
    parameters = _validate_parameters(candidate.get("parameters"), errors, f"{location}.parameters")
    status = candidate.get("status")
    if status not in {"planned", "manual_review", "unavailable"}:
        errors.append(_issue("status", f"{location}.status"))
        status = "unavailable"
    reason_code = candidate.get("reason_code")
    action_code = candidate.get("action_code")
    if not isinstance(reason_code, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", reason_code):
        errors.append(_issue("enum", f"{location}.reason_code"))
        reason_code = "intent_review"
    if not isinstance(action_code, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", action_code):
        errors.append(_issue("enum", f"{location}.action_code"))
        action_code = "review_intent"
    reason = _text(candidate.get("reason"), errors, f"{location}.reason", MAX_DESCRIPTION_CHARS)
    action = _text(candidate.get("action"), errors, f"{location}.action", MAX_DESCRIPTION_CHARS)
    dry_run = _boolean(candidate.get("dry_run"), errors, f"{location}.dry_run")
    if dry_run is not True:
        errors.append(_issue("dry_run", f"{location}.dry_run"))
    if candidate.get("execution") != "not_run":
        errors.append(_issue("execution", f"{location}.execution"))
    fingerprint = candidate.get("source_fingerprint")
    if not isinstance(fingerprint, str) or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None:
        errors.append(_issue("id", f"{location}.source_fingerprint"))
        fingerprint = "0" * 64
    normalized = {"schema_version": GENERATION_INTENT_CONTRACT, "id": intent_id or "intent_invalid", "recipe_id": recipe_id or "recipe_invalid", "recipe_version": recipe_version or "0.0.0", "variant_id": variant_id, "target_id": target_id or "target_invalid", "media_kind": media_kind, "prompt": prompt or "", "negative_prompt": negative or "", "slot_values": sorted(values, key=lambda item: item["slot_id"]), "style_pack_ids": sorted(styles), "parameters": parameters, "status": status, "reason_code": reason_code, "reason": reason or "", "action_code": action_code, "action": action or "", "dry_run": True, "execution": "not_run", "source_fingerprint": fingerprint}
    return (normalized if intent_id and recipe_id and recipe_version and target_id else None), errors


def _validate_report(value: object, location: str = "report") -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    candidate, errors = _base(value, location)
    if candidate is None:
        return None, errors
    fields = {"schema_version", "id", "recipe_id", "recipe_version", "target_id", "status", "reason_code", "reason", "action_code", "action", "missing_capabilities", "checks", "execution"}
    _strict(candidate, fields, ("schema_version", "id", "recipe_id", "recipe_version", "target_id", "status", "reason_code", "reason", "action_code", "action", "missing_capabilities", "checks", "execution"), errors, location)
    if candidate.get("schema_version") != COMPATIBILITY_REPORT_CONTRACT:
        errors.append(_issue("schema_version", f"{location}.schema_version"))
    report_id = _id(candidate.get("id"), REPORT_ID_RE, errors, f"{location}.id")
    recipe_id = _id(candidate.get("recipe_id"), RECIPE_ID_RE, errors, f"{location}.recipe_id")
    recipe_version = _version(candidate.get("recipe_version"), errors, f"{location}.recipe_version")
    target_id = _id(candidate.get("target_id"), TARGET_ID_RE, errors, f"{location}.target_id")
    status = candidate.get("status")
    if status not in REPORT_STATUSES:
        errors.append(_issue("status", f"{location}.status"))
        status = "unavailable"
    reason_code = candidate.get("reason_code")
    action_code = candidate.get("action_code")
    if not isinstance(reason_code, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", reason_code):
        errors.append(_issue("enum", f"{location}.reason_code"))
        reason_code = "compatibility_review"
    if not isinstance(action_code, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", action_code):
        errors.append(_issue("enum", f"{location}.action_code"))
        action_code = "review_compatibility"
    reason = _text(candidate.get("reason"), errors, f"{location}.reason", MAX_DESCRIPTION_CHARS)
    action = _text(candidate.get("action"), errors, f"{location}.action", MAX_DESCRIPTION_CHARS)
    raw_missing = _array(candidate.get("missing_capabilities"), errors, f"{location}.missing_capabilities", len(CAPABILITIES))
    missing: list[str] = []
    for index, item in enumerate(raw_missing):
        if item not in CAPABILITIES:
            errors.append(_issue("enum", f"{location}.missing_capabilities[{index}]"))
        elif item not in missing:
            missing.append(item)
    raw_checks = _array(candidate.get("checks"), errors, f"{location}.checks", MAX_COMPATIBILITY_CHECKS)
    checks: list[dict[str, Any]] = []
    for index, item in enumerate(raw_checks):
        item_location = f"{location}.checks[{index}]"
        if not isinstance(item, dict):
            errors.append(_issue("object_required", item_location))
            continue
        _strict(item, {"code", "status", "reason_code", "action_code"}, ("code", "status", "reason_code", "action_code"), errors, item_location)
        code = item.get("code")
        if not isinstance(code, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", code):
            errors.append(_issue("enum", f"{item_location}.code"))
            code = "check_invalid"
        check_status = item.get("status")
        if check_status not in REPORT_STATUSES:
            errors.append(_issue("status", f"{item_location}.status"))
            check_status = "unavailable"
        check_reason = item.get("reason_code")
        check_action = item.get("action_code")
        if not isinstance(check_reason, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", check_reason):
            errors.append(_issue("enum", f"{item_location}.reason_code"))
            check_reason = "check_review"
        if not isinstance(check_action, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", check_action):
            errors.append(_issue("enum", f"{item_location}.action_code"))
            check_action = "review_check"
        checks.append({"code": code, "status": check_status, "reason_code": check_reason, "action_code": check_action})
    execution = candidate.get("execution")
    if execution != "not_run":
        errors.append(_issue("execution", f"{location}.execution"))
        execution = "not_run"
    normalized = {"schema_version": COMPATIBILITY_REPORT_CONTRACT, "id": report_id or "report_invalid", "recipe_id": recipe_id or "recipe_invalid", "recipe_version": recipe_version or "0.0.0", "target_id": target_id or "target_invalid", "status": status, "reason_code": reason_code, "reason": reason or "", "action_code": action_code, "action": action or "", "missing_capabilities": sorted(missing), "checks": sorted(checks, key=lambda item: item["code"]), "execution": "not_run"}
    return (normalized if report_id and recipe_id and recipe_version and target_id else None), errors


def _finish(value: dict[str, Any] | None, errors: list[dict[str, str]], key: str, canonicalizer: Callable[[dict[str, Any]], str]) -> dict[str, Any]:
    if errors or value is None:
        return {"valid": False, "errors": errors, key: None, "execution": "not_run"}
    normalized = copy.deepcopy(value)
    canonical = canonicalizer(normalized)
    return {"valid": True, "errors": [], key: normalized, "fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest(), "execution": "not_run"}


def _canonical(value: dict[str, Any]) -> str:
    return json.dumps(copy.deepcopy(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def canonical_json(value: dict[str, Any]) -> str:
    return _canonical(value)


def canonical_recipe_catalog_json(value: dict[str, Any]) -> str:
    return _canonical(value)


def canonical_recipe_json(value: dict[str, Any]) -> str:
    return _canonical(value)


def canonical_style_pack_json(value: dict[str, Any]) -> str:
    return _canonical(value)


def canonical_prompt_slot_json(value: dict[str, Any]) -> str:
    return _canonical(value)


def canonical_prompt_variant_json(value: dict[str, Any]) -> str:
    return _canonical(value)


def canonical_target_card_json(value: dict[str, Any]) -> str:
    return _canonical(value)


def canonical_generation_intent_json(value: dict[str, Any]) -> str:
    return _canonical(value)


def canonical_compatibility_report_json(value: dict[str, Any]) -> str:
    return _canonical(value)


def validate_prompt_slot(value: object) -> dict[str, Any]:
    normalized, errors = _validate_slot(value)
    return _finish(normalized, errors, "slot", canonical_json)


def validate_prompt_variant(value: object) -> dict[str, Any]:
    normalized, errors = _validate_variant(value)
    return _finish(normalized, errors, "variant", canonical_json)


def validate_style_pack(value: object) -> dict[str, Any]:
    normalized, errors = _validate_style_pack(value)
    return _finish(normalized, errors, "style_pack", canonical_style_pack_json)


def validate_recipe(value: object) -> dict[str, Any]:
    normalized, errors = _validate_recipe(value)
    return _finish(normalized, errors, "recipe", canonical_recipe_json)


def validate_target_card(value: object) -> dict[str, Any]:
    normalized, errors = _validate_target(value)
    return _finish(normalized, errors, "target", canonical_json)


def validate_recipe_catalog(value: object) -> dict[str, Any]:
    normalized, errors = _validate_catalog(value)
    return _finish(normalized, errors, "catalog", canonical_recipe_catalog_json)


def validate_generation_intent(value: object) -> dict[str, Any]:
    normalized, errors = _validate_intent(value)
    return _finish(normalized, errors, "intent", canonical_generation_intent_json)


def validate_lint_finding(value: object) -> dict[str, Any]:
    normalized, errors = _validate_finding(value)
    return _finish(normalized, errors, "finding", canonical_json)


def validate_compatibility_report(value: object) -> dict[str, Any]:
    normalized, errors = _validate_report(value)
    return _finish(normalized, errors, "report", canonical_compatibility_report_json)


def recipe_catalog_schema() -> dict[str, Any]:
    return copy.deepcopy(_SCHEMA)


def _ref_schema(ref: str) -> dict[str, Any]:
    return {"$schema": _SCHEMA["$schema"], "$id": _SCHEMA["$id"], "$defs": copy.deepcopy(_SCHEMA["$defs"]), "$ref": ref}


def recipe_schema() -> dict[str, Any]:
    return _ref_schema("#/$defs/recipe")


def style_pack_schema() -> dict[str, Any]:
    return _ref_schema("#/$defs/style_pack")


def prompt_slot_schema() -> dict[str, Any]:
    return _ref_schema("#/$defs/prompt_slot")


def prompt_variant_schema() -> dict[str, Any]:
    return _ref_schema("#/$defs/prompt_variant")


def target_card_schema() -> dict[str, Any]:
    return _ref_schema("#/$defs/target")


def generation_intent_schema() -> dict[str, Any]:
    return _ref_schema("#/$defs/intent")


def lint_finding_schema() -> dict[str, Any]:
    return _ref_schema("#/$defs/finding")


def compatibility_report_schema() -> dict[str, Any]:
    return _ref_schema("#/$defs/report")


def creative_recipe_schemas() -> list[dict[str, Any]]:
    return [recipe_catalog_schema(), recipe_schema(), style_pack_schema(), prompt_slot_schema(), prompt_variant_schema(), target_card_schema(), generation_intent_schema(), lint_finding_schema(), compatibility_report_schema()]


def _text_schema(maximum: int, *, allow_empty: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {"type": "string", "maxLength": maximum, "pattern": SAFE_TEXT_PATTERN if allow_empty else SAFE_REQUIRED_TEXT_PATTERN}
    if not allow_empty:
        result["minLength"] = 1
    return result


def _id_schema(prefix: str) -> dict[str, Any]:
    return {"type": "string", "pattern": rf"^{re.escape(prefix)}[a-z0-9][a-z0-9_-]{{0,55}}$"}


def _version_schema() -> dict[str, Any]:
    return {"type": "string", "pattern": SEMVER_PATTERN}


def _array_schema(items: dict[str, Any], maximum: int, *, minimum: int = 0, unique: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {"type": "array", "minItems": minimum, "maxItems": maximum, "items": items}
    if unique:
        result["uniqueItems"] = True
    return result


_SAFE_ID = {"type": "string", "pattern": SAFE_ID_PATTERN}
_CAPABILITY_SCHEMA = {"type": "string", "enum": list(CAPABILITIES)}
_MEDIA_SCHEMA = {"type": "string", "enum": list(MEDIA_KINDS)}
_LICENSE_SCHEMA = {"type": "string", "enum": list(LICENSES)}
_PREVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "summary", "tags", "icon"],
    "properties": {"title": _text_schema(MAX_PREVIEW_CHARS), "summary": _text_schema(MAX_PREVIEW_CHARS, allow_empty=True), "tags": _array_schema(_text_schema(40), MAX_TAGS, unique=True), "icon": {"type": "string", "enum": ["image", "video", "portrait", "product", "edit", "generic"]}},
}

_PARAMETERS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "width": {"type": "integer", "minimum": 1, "maximum": 16384},
        "height": {"type": "integer", "minimum": 1, "maximum": 16384},
        "steps": {"type": "integer", "minimum": 1, "maximum": 200},
        "seed": {"type": "integer", "minimum": 0, "maximum": 4294967295},
        "fps": {"type": "number", "minimum": 1, "maximum": 240},
        "duration_seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": 3600},
        "frames": {"type": "integer", "minimum": 1, "maximum": 10000},
        "aspect_ratio": {"type": "string", "pattern": r"^[1-9][0-9]{0,1}:[1-9][0-9]{0,1}$"},
    },
}

_PROMPT_SLOT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "id", "label", "type", "required"],
    "properties": {
        "schema_version": {"const": PROMPT_SLOT_CONTRACT},
        "id": _id_schema("slot_"),
        "label": _text_schema(MAX_LABEL_CHARS),
        "type": {"type": "string", "enum": list(SLOT_TYPES)},
        "required": {"type": "boolean"},
        "default": {"type": ["string", "number", "integer", "boolean", "null"]},
        "enum": _array_schema({"type": ["string", "number", "integer", "boolean"]}, MAX_ENUM_VALUES, minimum=1, unique=True),
        "minimum": {"type": "number"},
        "maximum": {"type": "number"},
        "unit": _text_schema(32, allow_empty=True),
        "description": _text_schema(MAX_DESCRIPTION_CHARS, allow_empty=True),
    },
    "allOf": [
        {"if": {"properties": {"type": {"const": "text"}}}, "then": {"properties": {"default": {"type": "string"}, "enum": _array_schema({"type": "string"}, MAX_ENUM_VALUES, minimum=1)}}},
        {"if": {"properties": {"type": {"const": "style"}}}, "then": {"properties": {"default": {"type": "string"}, "enum": _array_schema({"type": "string"}, MAX_ENUM_VALUES, minimum=1)}}},
        {"if": {"properties": {"type": {"const": "aspect_ratio"}}}, "then": {"properties": {"default": {"type": "string", "pattern": r"^[1-9][0-9]{0,1}:[1-9][0-9]{0,1}$"}, "enum": _array_schema({"type": "string", "pattern": r"^[1-9][0-9]{0,1}:[1-9][0-9]{0,1}$"}, MAX_ENUM_VALUES, minimum=1)}}},
        {"if": {"properties": {"type": {"const": "enum"}}}, "then": {"required": ["enum"], "properties": {"default": {"type": "string"}, "enum": _array_schema({"type": "string"}, MAX_ENUM_VALUES, minimum=1)}}},
        {"if": {"properties": {"type": {"const": "integer"}}}, "then": {"properties": {"default": {"type": "integer"}}}},
        {"if": {"properties": {"type": {"const": "number"}}}, "then": {"properties": {"default": {"type": "number"}}}},
        {"if": {"properties": {"type": {"const": "duration"}}}, "then": {"properties": {"default": {"type": "number", "minimum": 0, "maximum": 3600}}}},
        {"if": {"properties": {"type": {"const": "seed"}}}, "then": {"properties": {"default": {"type": "integer", "minimum": 0, "maximum": 4294967295}}}},
        {"if": {"properties": {"type": {"const": "boolean"}}}, "then": {"properties": {"default": {"type": "boolean"}}}},
        {"if": {"properties": {"type": {"enum": ["text", "enum", "boolean", "style", "aspect_ratio", "seed"]}}}, "then": {"not": {"anyOf": [{"required": ["minimum"], "properties": {"minimum": {}}}, {"required": ["maximum"], "properties": {"maximum": {}}}]}}},
    ],
}

_PROMPT_VARIANT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "id", "label", "prompt_template", "negative_prompt", "slot_values", "style_pack_ids"],
    "properties": {
        "schema_version": {"const": PROMPT_VARIANT_CONTRACT},
        "id": _id_schema("variant_"),
        "label": _text_schema(MAX_LABEL_CHARS),
        "prompt_template": _text_schema(MAX_PROMPT_CHARS, allow_empty=True),
        "negative_prompt": _text_schema(MAX_NEGATIVE_PROMPT_CHARS, allow_empty=True),
        "slot_values": _array_schema({"$ref": "#/$defs/slot_value"}, MAX_SLOT_VALUES, unique=True),
        "style_pack_ids": _array_schema(_id_schema("style_"), MAX_CATALOG_STYLE_PACKS, unique=True),
    },
}

_SLOT_VALUE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["slot_id", "value"], "properties": {"slot_id": _id_schema("slot_"), "value": {"anyOf": [{"type": "string", "maxLength": MAX_TEXT_VALUE_CHARS, "pattern": SAFE_TEXT_PATTERN}, {"type": "number"}, {"type": "boolean"}]}}}
_STYLE_PACK_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "id", "version", "label", "description", "positive_prompt", "negative_prompt", "media_kinds", "tags", "preview", "license"],
    "properties": {
        "schema_version": {"const": STYLE_PACK_CONTRACT}, "id": _id_schema("style_"), "version": _version_schema(), "label": _text_schema(MAX_LABEL_CHARS), "description": _text_schema(MAX_DESCRIPTION_CHARS, allow_empty=True), "positive_prompt": _text_schema(MAX_PROMPT_CHARS, allow_empty=True), "negative_prompt": _text_schema(MAX_NEGATIVE_PROMPT_CHARS, allow_empty=True), "media_kinds": _array_schema(_MEDIA_SCHEMA, 2, minimum=1, unique=True), "tags": _array_schema(_text_schema(40), MAX_TAGS, unique=True), "preview": {"$ref": "#/$defs/preview"}, "license": _LICENSE_SCHEMA,
    },
}
_COMPATIBILITY_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["target_ids", "required_capabilities", "model_families"], "properties": {"target_ids": _array_schema(_id_schema("target_"), MAX_CATALOG_TARGETS, unique=True), "required_capabilities": _array_schema(_CAPABILITY_SCHEMA, len(CAPABILITIES), unique=True), "model_families": _array_schema({"type": "string", "enum": list(MODEL_FAMILIES)}, len(MODEL_FAMILIES), unique=True)}}
_CONSTRAINTS_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["aspect_ratios", "default_parameters"], "properties": {"aspect_ratios": _array_schema({"type": "string", "pattern": r"^[1-9][0-9]{0,1}:[1-9][0-9]{0,1}$"}, 16, unique=True), "min_width": {"type": "integer", "minimum": 1, "maximum": 16384}, "max_width": {"type": "integer", "minimum": 1, "maximum": 16384}, "min_height": {"type": "integer", "minimum": 1, "maximum": 16384}, "max_height": {"type": "integer", "minimum": 1, "maximum": 16384}, "max_frames": {"type": "integer", "minimum": 1, "maximum": 10000}, "max_duration_seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": 3600}, "max_fps": {"type": "number", "minimum": 1, "maximum": 240}, "default_parameters": _PARAMETERS_SCHEMA}}
_TARGET_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["schema_version", "id", "version", "label", "description", "media_kind", "capabilities", "model_families", "status", "reason_code", "action_code", "preview"], "properties": {"schema_version": {"const": TARGET_CARD_CONTRACT}, "id": _id_schema("target_"), "version": _version_schema(), "label": _text_schema(MAX_LABEL_CHARS), "description": _text_schema(MAX_DESCRIPTION_CHARS, allow_empty=True), "media_kind": _MEDIA_SCHEMA, "capabilities": _array_schema(_CAPABILITY_SCHEMA, len(CAPABILITIES), minimum=1, unique=True), "model_families": _array_schema({"type": "string", "enum": list(MODEL_FAMILIES)}, len(MODEL_FAMILIES), minimum=1, unique=True), "status": {"type": "string", "enum": list(TARGET_STATUSES)}, "reason_code": _SAFE_ID, "action_code": _SAFE_ID, "preview": {"$ref": "#/$defs/preview"}}}
_RECIPE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["schema_version", "id", "version", "label", "description", "author", "license", "media_kind", "prompt_template", "negative_prompt", "slots", "variants", "style_pack_ids", "compatibility", "constraints", "preview", "tags"], "properties": {"schema_version": {"const": RECIPE_CONTRACT}, "id": _id_schema("recipe_"), "version": _version_schema(), "label": _text_schema(MAX_LABEL_CHARS), "description": _text_schema(MAX_DESCRIPTION_CHARS, allow_empty=True), "author": _text_schema(80), "license": _LICENSE_SCHEMA, "media_kind": _MEDIA_SCHEMA, "prompt_template": _text_schema(MAX_PROMPT_CHARS), "negative_prompt": _text_schema(MAX_NEGATIVE_PROMPT_CHARS, allow_empty=True), "slots": _array_schema({"$ref": "#/$defs/prompt_slot"}, MAX_SLOTS), "variants": _array_schema({"$ref": "#/$defs/prompt_variant"}, MAX_VARIANTS), "style_pack_ids": _array_schema(_id_schema("style_"), MAX_CATALOG_STYLE_PACKS, unique=True), "compatibility": {"$ref": "#/$defs/compatibility"}, "constraints": {"$ref": "#/$defs/constraints"}, "preview": {"$ref": "#/$defs/preview"}, "tags": _array_schema(_text_schema(40), MAX_TAGS, unique=True)}}
_CATALOG_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["schema_version", "id", "version", "label", "description", "author", "license", "recipes", "style_packs", "targets", "preview", "tags"], "properties": {"schema_version": {"const": CATALOG_CONTRACT}, "id": _id_schema("catalog_"), "version": _version_schema(), "label": _text_schema(MAX_LABEL_CHARS), "description": _text_schema(MAX_DESCRIPTION_CHARS, allow_empty=True), "author": _text_schema(80), "license": _LICENSE_SCHEMA, "recipes": _array_schema({"$ref": "#/$defs/recipe"}, MAX_CATALOG_RECIPES, minimum=1), "style_packs": _array_schema({"$ref": "#/$defs/style_pack"}, MAX_CATALOG_STYLE_PACKS), "targets": _array_schema({"$ref": "#/$defs/target"}, MAX_CATALOG_TARGETS, minimum=1), "preview": {"$ref": "#/$defs/preview"}, "tags": _array_schema(_text_schema(40), MAX_TAGS, unique=True)}}
_INTENT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["schema_version", "id", "recipe_id", "recipe_version", "variant_id", "target_id", "media_kind", "prompt", "negative_prompt", "slot_values", "style_pack_ids", "parameters", "status", "reason_code", "reason", "action_code", "action", "dry_run", "execution", "source_fingerprint"], "properties": {"schema_version": {"const": GENERATION_INTENT_CONTRACT}, "id": _id_schema("intent_"), "recipe_id": _id_schema("recipe_"), "recipe_version": _version_schema(), "variant_id": {"anyOf": [{"$ref": "#/$defs/variant_id"}, {"type": "null"}]}, "target_id": _id_schema("target_"), "media_kind": _MEDIA_SCHEMA, "prompt": _text_schema(MAX_PROMPT_CHARS), "negative_prompt": _text_schema(MAX_NEGATIVE_PROMPT_CHARS, allow_empty=True), "slot_values": _array_schema({"$ref": "#/$defs/slot_value"}, MAX_SLOT_VALUES, unique=True), "style_pack_ids": _array_schema(_id_schema("style_"), MAX_CATALOG_STYLE_PACKS, unique=True), "parameters": {"$ref": "#/$defs/parameters"}, "status": {"type": "string", "enum": ["planned", "manual_review", "unavailable"]}, "reason_code": _SAFE_ID, "reason": _text_schema(MAX_DESCRIPTION_CHARS), "action_code": _SAFE_ID, "action": _text_schema(MAX_DESCRIPTION_CHARS), "dry_run": {"const": True}, "execution": {"const": "not_run"}, "source_fingerprint": {"type": "string", "pattern": r"^[0-9a-f]{64}$"}}}
_FINDING_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["schema_version", "id", "code", "severity", "status", "subject_kind", "subject_id", "reason_code", "reason", "action_code", "action", "evidence_count"], "properties": {"schema_version": {"const": LINT_FINDING_CONTRACT}, "id": _id_schema("finding_"), "code": _SAFE_ID, "severity": {"type": "string", "enum": list(LINT_SEVERITIES)}, "status": {"type": "string", "enum": list(LINT_STATUSES)}, "subject_kind": {"type": "string", "enum": ["catalog", "recipe", "slot", "variant", "style_pack", "target", "compatibility"]}, "subject_id": _text_schema(80), "reason_code": _SAFE_ID, "reason": _text_schema(MAX_DESCRIPTION_CHARS), "action_code": _SAFE_ID, "action": _text_schema(MAX_DESCRIPTION_CHARS), "evidence_count": {"type": "integer", "minimum": 0, "maximum": 100000}}}
_CHECK_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["code", "status", "reason_code", "action_code"], "properties": {"code": _SAFE_ID, "status": {"type": "string", "enum": list(REPORT_STATUSES)}, "reason_code": _SAFE_ID, "action_code": _SAFE_ID}}
_REPORT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["schema_version", "id", "recipe_id", "recipe_version", "target_id", "status", "reason_code", "reason", "action_code", "action", "missing_capabilities", "checks", "execution"], "properties": {"schema_version": {"const": COMPATIBILITY_REPORT_CONTRACT}, "id": _id_schema("report_"), "recipe_id": _id_schema("recipe_"), "recipe_version": _version_schema(), "target_id": _id_schema("target_"), "status": {"type": "string", "enum": list(REPORT_STATUSES)}, "reason_code": _SAFE_ID, "reason": _text_schema(MAX_DESCRIPTION_CHARS), "action_code": _SAFE_ID, "action": _text_schema(MAX_DESCRIPTION_CHARS), "missing_capabilities": _array_schema(_CAPABILITY_SCHEMA, len(CAPABILITIES), unique=True), "checks": _array_schema(_CHECK_SCHEMA, MAX_COMPATIBILITY_CHECKS), "execution": {"const": "not_run"}}}

_SCHEMA = {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "https://local-ai-hub/schemas/creative-recipe-intelligence.v1.json", "$defs": {"preview": _PREVIEW_SCHEMA, "parameters": _PARAMETERS_SCHEMA, "slot_value": _SLOT_VALUE_SCHEMA, "variant_id": _id_schema("variant_"), "prompt_slot": _PROMPT_SLOT_SCHEMA, "prompt_variant": _PROMPT_VARIANT_SCHEMA, "style_pack": _STYLE_PACK_SCHEMA, "compatibility": _COMPATIBILITY_SCHEMA, "constraints": _CONSTRAINTS_SCHEMA, "target": _TARGET_SCHEMA, "recipe": _RECIPE_SCHEMA, "catalog": _CATALOG_SCHEMA, "intent": _INTENT_SCHEMA, "finding": _FINDING_SCHEMA, "check": _CHECK_SCHEMA, "report": _REPORT_SCHEMA}, **_CATALOG_SCHEMA}


# Friendly aliases make the module discoverable for callers that spell out
# "creative" while retaining concise names used in the service.
creative_recipe_catalog_schema = recipe_catalog_schema
creative_recipe_schema = recipe_schema
creative_style_pack_schema = style_pack_schema
creative_target_card_schema = target_card_schema
creative_prompt_slot_schema = prompt_slot_schema
creative_prompt_variant_schema = prompt_variant_schema
creative_generation_intent_schema = generation_intent_schema
creative_recipe_lint_finding_schema = lint_finding_schema
creative_lint_finding_schema = lint_finding_schema
creative_compatibility_report_schema = compatibility_report_schema
validate_creative_recipe_catalog = validate_recipe_catalog
validate_creative_recipe = validate_recipe
validate_creative_style_pack = validate_style_pack
validate_creative_prompt_slot = validate_prompt_slot
validate_creative_prompt_variant = validate_prompt_variant
validate_creative_target_card = validate_target_card
validate_creative_generation_intent = validate_generation_intent
validate_creative_recipe_lint_finding = validate_lint_finding
validate_creative_compatibility_report = validate_compatibility_report


def schema_for_contract(contract: object) -> dict[str, Any] | None:
    """Return a detached schema for one allowlisted contract string."""

    mapping = {
        CATALOG_CONTRACT: recipe_catalog_schema,
        RECIPE_CONTRACT: recipe_schema,
        STYLE_PACK_CONTRACT: style_pack_schema,
        PROMPT_SLOT_CONTRACT: prompt_slot_schema,
        PROMPT_VARIANT_CONTRACT: prompt_variant_schema,
        TARGET_CARD_CONTRACT: target_card_schema,
        GENERATION_INTENT_CONTRACT: generation_intent_schema,
        LINT_FINDING_CONTRACT: lint_finding_schema,
        COMPATIBILITY_REPORT_CONTRACT: compatibility_report_schema,
    }
    factory = mapping.get(contract)
    return factory() if factory is not None else None


__all__ = [
    "CATALOG_CONTRACT", "RECIPE_CONTRACT", "STYLE_PACK_CONTRACT", "PROMPT_SLOT_CONTRACT", "PROMPT_VARIANT_CONTRACT", "TARGET_CARD_CONTRACT", "GENERATION_INTENT_CONTRACT", "LINT_FINDING_CONTRACT", "COMPATIBILITY_REPORT_CONTRACT", "MAX_DESCRIPTOR_BYTES", "MAX_CATALOG_RECIPES", "MAX_CATALOG_STYLE_PACKS", "MAX_CATALOG_TARGETS", "MAX_SLOTS", "MAX_VARIANTS", "MAX_SLOT_VALUES", "MAX_ENUM_VALUES", "MAX_TAGS", "MAX_PROMPT_CHARS", "MAX_NEGATIVE_PROMPT_CHARS", "MAX_LABEL_CHARS", "MAX_DESCRIPTION_CHARS", "MAX_PREVIEW_CHARS", "MAX_FINDINGS", "MAX_COMPATIBILITY_CHECKS", "MAX_VARIANT_PLANS", "MAX_TEXT_VALUE_CHARS", "MAX_DEPTH", "CAPABILITIES", "MEDIA_KINDS", "MODEL_FAMILIES", "SLOT_TYPES", "TARGET_STATUSES", "LINT_SEVERITIES", "LINT_STATUSES", "REPORT_STATUSES", "LICENSES", "PARAMETER_KEYS", "creative_recipe_schemas", "schema_for_contract", "recipe_catalog_schema", "recipe_schema", "style_pack_schema", "prompt_slot_schema", "prompt_variant_schema", "target_card_schema", "generation_intent_schema", "lint_finding_schema", "compatibility_report_schema", "creative_recipe_catalog_schema", "creative_recipe_schema", "creative_style_pack_schema", "creative_target_card_schema", "creative_prompt_slot_schema", "creative_prompt_variant_schema", "creative_generation_intent_schema", "creative_recipe_lint_finding_schema", "creative_lint_finding_schema", "creative_compatibility_report_schema", "validate_recipe_catalog", "validate_recipe", "validate_style_pack", "validate_prompt_slot", "validate_prompt_variant", "validate_target_card", "validate_generation_intent", "validate_lint_finding", "validate_compatibility_report", "validate_creative_recipe_catalog", "validate_creative_recipe", "validate_creative_style_pack", "validate_creative_prompt_slot", "validate_creative_prompt_variant", "validate_creative_target_card", "validate_creative_generation_intent", "validate_creative_recipe_lint_finding", "validate_creative_compatibility_report", "canonical_json", "canonical_recipe_catalog_json", "canonical_recipe_json", "canonical_style_pack_json", "canonical_prompt_slot_json", "canonical_prompt_variant_json", "canonical_target_card_json", "canonical_generation_intent_json", "canonical_compatibility_report_json",
]
