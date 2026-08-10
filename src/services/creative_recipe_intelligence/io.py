"""Fail-closed JSON import/export for Creative Recipe Intelligence contracts."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Mapping
from typing import Any

from src.shared.schemas.creative_recipes import (
    MAX_DESCRIPTOR_BYTES,
    canonical_compatibility_report_json,
    canonical_generation_intent_json,
    canonical_json,
    canonical_prompt_slot_json,
    canonical_prompt_variant_json,
    canonical_recipe_catalog_json,
    canonical_recipe_json,
    canonical_style_pack_json,
    canonical_target_card_json,
    validate_compatibility_report,
    validate_generation_intent,
    validate_lint_finding,
    validate_prompt_slot,
    validate_prompt_variant,
    validate_recipe,
    validate_recipe_catalog,
    validate_style_pack,
    validate_target_card,
)


class _DuplicateKey(ValueError):
    pass


class _NonFinite(ValueError):
    pass


def _duplicate_key_guard(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(key)
        result[key] = value
    return result


def _non_finite(value: str) -> None:
    raise _NonFinite(value)


def _parse(payload: object) -> tuple[object | None, list[dict[str, str]]]:
    if isinstance(payload, str):
        try:
            raw = payload.encode("utf-8")
        except UnicodeEncodeError:
            return None, [{"code": "invalid_utf8", "location": "input"}]
    elif isinstance(payload, bytes):
        raw = payload
    elif isinstance(payload, Mapping):
        return copy.deepcopy(dict(payload)), []
    else:
        return None, [{"code": "json_type", "location": "input"}]
    if len(raw) > MAX_DESCRIPTOR_BYTES:
        return None, [{"code": "descriptor_size", "location": "input"}]
    try:
        text = raw.decode("utf-8", errors="strict")
        if text.startswith("\ufeff"):
            return None, [{"code": "bom", "location": "input"}]
        return json.loads(text, object_pairs_hook=_duplicate_key_guard, parse_constant=_non_finite), []
    except _DuplicateKey:
        return None, [{"code": "duplicate_json_key", "location": "input"}]
    except _NonFinite:
        return None, [{"code": "nonfinite_json", "location": "input"}]
    except UnicodeDecodeError:
        return None, [{"code": "invalid_utf8", "location": "input"}]
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, [{"code": "invalid_json", "location": "input"}]


def _import(payload: object, validator: Callable[[object], dict[str, Any]], key: str) -> dict[str, Any]:
    value, parse_errors = _parse(payload)
    if parse_errors:
        return {"accepted": False, "errors": parse_errors, key: None, "execution": "not_run"}
    validation = validator(value)
    if not validation.get("valid"):
        return {"accepted": False, "errors": [{"code": item.get("code", "invalid"), "location": item.get("location", key)} for item in validation.get("errors", [])], key: None, "execution": "not_run"}
    return {"accepted": True, "errors": [], key: copy.deepcopy(validation[key]), "fingerprint": validation["fingerprint"], "execution": "not_run"}


def safe_import_recipe_catalog(payload: object) -> dict[str, Any]:
    return _import(payload, validate_recipe_catalog, "catalog")


def safe_import_recipe(payload: object) -> dict[str, Any]:
    return _import(payload, validate_recipe, "recipe")


def safe_import_style_pack(payload: object) -> dict[str, Any]:
    return _import(payload, validate_style_pack, "style_pack")


def safe_import_prompt_slot(payload: object) -> dict[str, Any]:
    return _import(payload, validate_prompt_slot, "slot")


def safe_import_prompt_variant(payload: object) -> dict[str, Any]:
    return _import(payload, validate_prompt_variant, "variant")


def safe_import_target_card(payload: object) -> dict[str, Any]:
    return _import(payload, validate_target_card, "target")


def safe_import_generation_intent(payload: object) -> dict[str, Any]:
    return _import(payload, validate_generation_intent, "intent")


def safe_import_lint_finding(payload: object) -> dict[str, Any]:
    return _import(payload, validate_lint_finding, "finding")


def safe_import_compatibility_report(payload: object) -> dict[str, Any]:
    return _import(payload, validate_compatibility_report, "report")


def _export(value: object, validator: Callable[[object], dict[str, Any]], key: str, canonicalizer: Callable[[dict[str, Any]], str]) -> dict[str, Any]:
    validation = validator(value)
    if not validation.get("valid"):
        return {"ready": False, "errors": [{"code": item.get("code", "invalid"), "location": item.get("location", key)} for item in validation.get("errors", [])], "content": None, "execution": "not_run"}
    normalized = validation[key]
    assert isinstance(normalized, dict)
    content = canonicalizer(normalized).encode("utf-8")
    return {"ready": True, "errors": [], "content": content, "fingerprint": validation["fingerprint"], "execution": "not_run"}


def export_recipe_catalog(value: object) -> dict[str, Any]:
    return _export(value, validate_recipe_catalog, "catalog", canonical_recipe_catalog_json)


def export_recipe(value: object) -> dict[str, Any]:
    return _export(value, validate_recipe, "recipe", canonical_recipe_json)


def export_style_pack(value: object) -> dict[str, Any]:
    return _export(value, validate_style_pack, "style_pack", canonical_style_pack_json)


def export_prompt_slot(value: object) -> dict[str, Any]:
    return _export(value, validate_prompt_slot, "slot", canonical_prompt_slot_json)


def export_prompt_variant(value: object) -> dict[str, Any]:
    return _export(value, validate_prompt_variant, "variant", canonical_prompt_variant_json)


def export_target_card(value: object) -> dict[str, Any]:
    return _export(value, validate_target_card, "target", canonical_target_card_json)


def export_generation_intent(value: object) -> dict[str, Any]:
    return _export(value, validate_generation_intent, "intent", canonical_generation_intent_json)


def export_lint_finding(value: object) -> dict[str, Any]:
    return _export(value, validate_lint_finding, "finding", canonical_json)


def export_compatibility_report(value: object) -> dict[str, Any]:
    return _export(value, validate_compatibility_report, "report", canonical_compatibility_report_json)


# Descriptive aliases keep integrations independent from the concise catalog
# terminology while preserving one implementation and one canonical format.
safe_import_catalog = safe_import_recipe_catalog
safe_import_creative_recipe_catalog = safe_import_recipe_catalog
safe_import_creative_recipe = safe_import_recipe
export_catalog = export_recipe_catalog
export_creative_recipe_catalog = export_recipe_catalog
export_creative_recipe = export_recipe
safe_export_recipe_catalog = export_recipe_catalog
safe_export_recipe = export_recipe


__all__ = [
    "safe_import_recipe_catalog", "safe_import_catalog", "safe_import_creative_recipe_catalog", "safe_import_recipe", "safe_import_creative_recipe", "safe_import_style_pack", "safe_import_prompt_slot", "safe_import_prompt_variant", "safe_import_target_card", "safe_import_generation_intent", "safe_import_lint_finding", "safe_import_compatibility_report", "export_recipe_catalog", "export_catalog", "export_creative_recipe_catalog", "export_recipe", "export_creative_recipe", "safe_export_recipe_catalog", "safe_export_recipe", "export_style_pack", "export_prompt_slot", "export_prompt_variant", "export_target_card", "export_generation_intent", "export_lint_finding", "export_compatibility_report",
]
