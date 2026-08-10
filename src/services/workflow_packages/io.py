"""Fail-closed JSON import/export for static workflow package descriptors."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from src.shared.schemas.workflow_package import (
    MAX_PACKAGE_BYTES,
    canonical_workflow_package_json,
    validate_workflow_package,
)


class _DuplicateJsonKey(ValueError):
    """Raised internally when an import contains duplicate object keys."""


def _duplicate_key_guard(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJsonKey()
        value[key] = item
    return value


def _non_finite_number(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _failure(code: str, reason: str, action: str) -> dict[str, Any]:
    """Fixed messages avoid reflecting an unsafe parser payload to callers."""

    return {
        "accepted": False,
        "status": "unavailable",
        "reason": reason,
        "action": action,
        "errors": [{"code": code}],
        "package": None,
    }


def _decode_payload(payload: object) -> tuple[str | None, dict[str, Any] | None]:
    if isinstance(payload, str):
        try:
            raw = payload.encode("utf-8", errors="strict")
        except UnicodeError:
            return None, _failure("utf8", "Package text is not valid UTF-8.", "Export a UTF-8 JSON descriptor and try again.")
    elif isinstance(payload, (bytes, bytearray)):
        raw = bytes(payload)
    else:
        return None, _failure("payload_type", "Import accepts only UTF-8 JSON text or bytes.", "Provide a static JSON descriptor, not an object or file handle.")
    if not raw or len(raw) > MAX_PACKAGE_BYTES:
        return None, _failure("payload_size", "Package payload is empty or exceeds the static size bound.", "Keep the descriptor below the documented package size limit.")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None, _failure("utf8", "Package text is not valid UTF-8.", "Export a UTF-8 JSON descriptor and try again.")
    if text.startswith("\ufeff"):
        return None, _failure("bom", "Package JSON must not include a byte-order marker.", "Re-export the descriptor as plain UTF-8 JSON.")
    return text, None


def safe_import_workflow_package(payload: object) -> dict[str, Any]:
    """Parse and validate an external descriptor without filesystem side effects.

    This function does not accept a filename, never imports a module, and never
    persists or mutates a workflow.  Invalid payload contents are intentionally
    not echoed in the response.
    """

    text, failure = _decode_payload(payload)
    if failure is not None:
        return failure
    assert text is not None
    try:
        value = json.loads(text, object_pairs_hook=_duplicate_key_guard, parse_constant=_non_finite_number)
    except _DuplicateJsonKey:
        return _failure("duplicate_json_key", "Package JSON contains duplicate object keys.", "Use unique keys in every descriptor object.")
    except (json.JSONDecodeError, ValueError, RecursionError):
        return _failure("json_parse", "Package payload is not a supported static JSON document.", "Export a valid workflow-package.v1 JSON descriptor.")
    validation = validate_workflow_package(value)
    if not validation["valid"]:
        return {
            "accepted": False,
            "status": "unavailable",
            "reason": "Package failed static validation and was not imported.",
            "action": "Correct the reported contract codes, then validate again.",
            "errors": [{"code": item["code"], "location": item["location"]} for item in validation["errors"]],
            "package": None,
        }
    return {
        "accepted": True,
        "status": "planned",
        "reason": "Package passed static validation; no graph was executed or saved.",
        "action": "Use the server-owned validated package result for any later integration preflight.",
        "errors": [],
        "package": copy.deepcopy(validation["package"]),
        "fingerprint": validation["fingerprint"],
    }


def validated_package_result(value: object) -> dict[str, Any]:
    """Revalidate a package or a prior validation result for service boundaries."""

    candidate: object = value
    if isinstance(value, dict) and value.get("valid") is True and isinstance(value.get("package"), dict):
        candidate = value["package"]
    elif isinstance(value, dict) and value.get("accepted") is True and isinstance(value.get("package"), dict):
        candidate = value["package"]
    elif isinstance(value, dict) and value.get("found") is True and isinstance(value.get("package"), dict):
        candidate = value["package"]
    return validate_workflow_package(candidate)


def export_workflow_package(value: object) -> dict[str, Any]:
    """Create deterministic JSON text from a revalidated, detached descriptor."""

    validation = validated_package_result(value)
    if not validation["valid"]:
        return {
            "ready": False,
            "status": "unavailable",
            "reason": "Only a valid static workflow package can be exported.",
            "action": "Validate the descriptor before requesting export.",
            "errors": [{"code": item["code"], "location": item["location"]} for item in validation["errors"]],
        }
    package = validation["package"]
    assert isinstance(package, dict)
    content = canonical_workflow_package_json(package)
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return {
        "ready": True,
        "status": "planned",
        "reason": "Deterministic descriptor export is ready; it contains no executable graph action.",
        "action": "Store or review the JSON using a managed integration boundary.",
        "media_type": "application/json",
        "filename": f"{package['id']}-{package['version']}.workflow-package.json",
        "content": content,
        "sha256": digest,
    }
