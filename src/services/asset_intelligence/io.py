"""Fail-closed JSON import/export helpers for static asset contracts."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Callable

from src.shared.schemas.asset_intelligence import (
    MAX_DESCRIPTOR_BYTES,
    canonical_asset_catalog_json,
    canonical_asset_record_json,
    canonical_provenance_lineage_json,
    canonical_smart_collection_json,
    validate_asset_catalog,
    validate_asset_record,
    validate_provenance_lineage,
    validate_smart_collection,
)


class _DuplicateJsonKey(ValueError):
    """Raised internally when a JSON object repeats an object key."""


def _duplicate_key_guard(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJsonKey()
        value[key] = item
    return value


def _non_finite_number(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _failure(code: str, reason: str, action: str, key: str) -> dict[str, Any]:
    return {
        "accepted": False,
        "status": "unavailable",
        "reason": reason,
        "action": action,
        "errors": [{"code": code}],
        key: None,
    }


def _decode_payload(payload: object, *, kind: str, key: str) -> tuple[object | None, dict[str, Any] | None]:
    if isinstance(payload, str):
        try:
            raw = payload.encode("utf-8", errors="strict")
        except UnicodeError:
            return None, _failure("utf8", f"{kind} text is not valid UTF-8.", "Export a UTF-8 JSON descriptor and try again.", key)
    elif isinstance(payload, (bytes, bytearray)):
        raw = bytes(payload)
    else:
        return None, _failure("payload_type", f"{kind} import accepts only UTF-8 JSON text or bytes.", "Provide a static JSON descriptor, not an object or file handle.", key)
    if not raw or len(raw) > MAX_DESCRIPTOR_BYTES:
        return None, _failure("payload_size", f"{kind} payload is empty or exceeds the static size bound.", "Keep the descriptor below the documented static size limit.", key)
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None, _failure("utf8", f"{kind} text is not valid UTF-8.", "Export a UTF-8 JSON descriptor and try again.", key)
    if text.startswith("\ufeff"):
        return None, _failure("bom", f"{kind} JSON must not include a byte-order marker.", "Re-export the descriptor as plain UTF-8 JSON.", key)
    try:
        return json.loads(text, object_pairs_hook=_duplicate_key_guard, parse_constant=_non_finite_number), None
    except _DuplicateJsonKey:
        return None, _failure("duplicate_json_key", f"{kind} JSON contains duplicate object keys.", "Use unique keys in every descriptor object.", key)
    except (json.JSONDecodeError, ValueError, RecursionError):
        return None, _failure("json_parse", f"{kind} payload is not a supported static JSON document.", "Export a valid versioned static descriptor.", key)


def _safe_import(payload: object, *, kind: str, key: str, validator: Callable[[object], dict[str, Any]]) -> dict[str, Any]:
    value, failure = _decode_payload(payload, kind=kind, key=key)
    if failure is not None:
        return failure
    validation = validator(value)
    if not validation["valid"]:
        return {
            "accepted": False,
            "status": "unavailable",
            "reason": f"{kind} failed static validation and was not imported.",
            "action": "Correct the reported contract codes, then validate again.",
            "errors": [{"code": item["code"], "location": item["location"]} for item in validation["errors"]],
            key: None,
        }
    return {
        "accepted": True,
        "status": "partial",
        "reason": f"{kind} passed static validation; no asset, provider, workflow, or retention action was run.",
        "action": "Use the detached server-owned validation result only for static planning.",
        "errors": [],
        key: copy.deepcopy(validation[key]),
        "fingerprint": validation["fingerprint"],
        "execution": "not_run",
    }


def safe_import_asset_record(payload: object) -> dict[str, Any]:
    return _safe_import(payload, kind="Asset record", key="asset", validator=validate_asset_record)


def safe_import_asset_catalog(payload: object) -> dict[str, Any]:
    return _safe_import(payload, kind="Asset catalog", key="catalog", validator=validate_asset_catalog)


def safe_import_provenance_lineage(payload: object) -> dict[str, Any]:
    return _safe_import(payload, kind="Provenance lineage", key="lineage", validator=validate_provenance_lineage)


def safe_import_smart_collection(payload: object) -> dict[str, Any]:
    return _safe_import(payload, kind="Smart collection", key="collection", validator=validate_smart_collection)


def _validated_result(value: object, *, key: str, validator: Callable[[object], dict[str, Any]]) -> dict[str, Any]:
    candidate: object = value
    if isinstance(value, dict) and value.get("accepted") is True and isinstance(value.get(key), dict):
        candidate = value[key]
    elif isinstance(value, dict) and value.get("valid") is True and isinstance(value.get(key), dict):
        candidate = value[key]
    elif isinstance(value, dict) and value.get("found") is True and isinstance(value.get(key), dict):
        candidate = value[key]
    return validator(candidate)


def validated_asset_record_result(value: object) -> dict[str, Any]:
    return _validated_result(value, key="asset", validator=validate_asset_record)


def validated_asset_catalog_result(value: object) -> dict[str, Any]:
    return _validated_result(value, key="catalog", validator=validate_asset_catalog)


def validated_provenance_lineage_result(value: object) -> dict[str, Any]:
    return _validated_result(value, key="lineage", validator=validate_provenance_lineage)


def validated_smart_collection_result(value: object) -> dict[str, Any]:
    return _validated_result(value, key="collection", validator=validate_smart_collection)


def _export(value: object, *, key: str, validator: Callable[[object], dict[str, Any]], canonical: Callable[[dict[str, Any]], str], suffix: str) -> dict[str, Any]:
    validation = _validated_result(value, key=key, validator=validator)
    if not validation["valid"]:
        return {
            "ready": False,
            "status": "unavailable",
            "reason": "Only a valid static descriptor can be exported.",
            "action": "Correct static validation errors before requesting export.",
            "errors": [{"code": item["code"], "location": item["location"]} for item in validation["errors"]],
        }
    descriptor = validation[key]
    assert isinstance(descriptor, dict)
    content = canonical(descriptor)
    return {
        "ready": True,
        "status": "partial",
        "reason": "Deterministic static descriptor export is ready; no asset or runtime action was performed.",
        "action": "Store or review the JSON through a managed integration boundary.",
        "media_type": "application/json",
        "filename": f"{descriptor['id']}.{suffix}.json",
        "content": content,
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "execution": "not_run",
    }


def export_asset_record(value: object) -> dict[str, Any]:
    return _export(value, key="asset", validator=validate_asset_record, canonical=canonical_asset_record_json, suffix="asset-record")


def export_asset_catalog(value: object) -> dict[str, Any]:
    return _export(value, key="catalog", validator=validate_asset_catalog, canonical=canonical_asset_catalog_json, suffix="asset-catalog")


def export_provenance_lineage(value: object) -> dict[str, Any]:
    return _export(value, key="lineage", validator=validate_provenance_lineage, canonical=canonical_provenance_lineage_json, suffix="provenance-lineage")


def export_smart_collection(value: object) -> dict[str, Any]:
    return _export(value, key="collection", validator=validate_smart_collection, canonical=canonical_smart_collection_json, suffix="smart-collection")
