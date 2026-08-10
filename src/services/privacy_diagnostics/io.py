"""Fail-closed JSON import/export for privacy diagnostics contracts."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Callable

from src.shared.schemas.privacy_diagnostics import (
    MAX_DESCRIPTOR_BYTES,
    canonical_diagnostic_finding_json,
    canonical_diagnostic_snapshot_json,
    canonical_privacy_policy_json,
    canonical_remediation_plan_json,
    canonical_support_bundle_manifest_json,
    validate_diagnostic_finding,
    validate_diagnostic_snapshot,
    validate_privacy_policy,
    validate_remediation_plan,
    validate_support_bundle_manifest,
)


class _DuplicateJsonKey(ValueError):
    """Internal duplicate-key marker that never reaches a public error."""


def _duplicate_key_guard(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey()
        result[key] = value
    return result


def _non_finite_number(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _failure(code: str, reason: str, action: str, key: str) -> dict[str, Any]:
    return {"accepted": False, "status": "unavailable", "reason": reason, "action": action, "errors": [{"code": code}], key: None, "execution": "not_run"}


def _decode_payload(payload: object, *, kind: str, key: str) -> tuple[object | None, dict[str, Any] | None]:
    if isinstance(payload, str):
        raw = payload.encode("utf-8", errors="strict")
    elif isinstance(payload, (bytes, bytearray)):
        raw = bytes(payload)
    else:
        return None, _failure("payload_type", f"{kind} import accepts only UTF-8 JSON text or bytes.", "Provide a bounded static descriptor, not an object, path, or file handle.", key)
    if not raw or len(raw) > MAX_DESCRIPTOR_BYTES:
        return None, _failure("payload_size", f"{kind} payload is empty or exceeds the static size bound.", "Keep the descriptor within the documented byte limit.", key)
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None, _failure("utf8", f"{kind} payload is not valid UTF-8.", "Export plain UTF-8 JSON and retry.", key)
    if text.startswith("\ufeff"):
        return None, _failure("bom", f"{kind} JSON must not include a byte-order marker.", "Export plain UTF-8 JSON and retry.", key)
    try:
        return json.loads(text, object_pairs_hook=_duplicate_key_guard, parse_constant=_non_finite_number), None
    except _DuplicateJsonKey:
        return None, _failure("duplicate_json_key", f"{kind} JSON contains duplicate object keys.", "Use unique keys in every descriptor object.", key)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, RecursionError):
        return None, _failure("json_parse", f"{kind} payload is not supported static JSON.", "Export a valid versioned descriptor and retry.", key)


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
            "action": "Correct the fixed contract codes and retry.",
            "errors": [{"code": item["code"], "location": item["location"]} for item in validation["errors"]],
            key: None,
            "execution": "not_run",
        }
    return {
        "accepted": True,
        "status": "partial",
        "reason": f"{kind} passed static validation. No probe, path, command, or runtime action was performed.",
        "action": "Use the detached server-owned result only for static planning.",
        "errors": [],
        key: copy.deepcopy(validation[key]),
        "fingerprint": validation["fingerprint"],
        "execution": "not_run",
    }


def safe_import_privacy_policy(payload: object) -> dict[str, Any]:
    return _safe_import(payload, kind="Privacy policy", key="policy", validator=validate_privacy_policy)


def safe_import_diagnostic_snapshot(payload: object) -> dict[str, Any]:
    return _safe_import(payload, kind="Diagnostic snapshot", key="snapshot", validator=validate_diagnostic_snapshot)


def safe_import_diagnostic_finding(payload: object) -> dict[str, Any]:
    return _safe_import(payload, kind="Diagnostic finding", key="finding", validator=validate_diagnostic_finding)


def safe_import_support_bundle_manifest(payload: object) -> dict[str, Any]:
    return _safe_import(payload, kind="Support bundle manifest", key="manifest", validator=validate_support_bundle_manifest)


def safe_import_remediation_plan(payload: object) -> dict[str, Any]:
    return _safe_import(payload, kind="Remediation plan", key="plan", validator=validate_remediation_plan)


def _validated_result(value: object, *, key: str, validator: Callable[[object], dict[str, Any]]) -> dict[str, Any]:
    candidate = value
    if isinstance(value, dict) and value.get("accepted") is True and isinstance(value.get(key), dict):
        candidate = value[key]
    elif isinstance(value, dict) and value.get("valid") is True and isinstance(value.get(key), dict):
        candidate = value[key]
    return validator(candidate)


def validated_privacy_policy_result(value: object) -> dict[str, Any]:
    return _validated_result(value, key="policy", validator=validate_privacy_policy)


def validated_diagnostic_snapshot_result(value: object) -> dict[str, Any]:
    return _validated_result(value, key="snapshot", validator=validate_diagnostic_snapshot)


def validated_diagnostic_finding_result(value: object) -> dict[str, Any]:
    return _validated_result(value, key="finding", validator=validate_diagnostic_finding)


def validated_support_bundle_manifest_result(value: object) -> dict[str, Any]:
    return _validated_result(value, key="manifest", validator=validate_support_bundle_manifest)


def validated_remediation_plan_result(value: object) -> dict[str, Any]:
    return _validated_result(value, key="plan", validator=validate_remediation_plan)


def _export(value: object, *, key: str, suffix: str, validator: Callable[[object], dict[str, Any]], canonicalizer: Callable[[dict[str, Any]], str]) -> dict[str, Any]:
    validation = _validated_result(value, key=key, validator=validator)
    if not validation["valid"]:
        return {"ready": False, "status": "unavailable", "reason": "Only a valid static descriptor can be exported.", "action": "Correct fixed contract errors before export.", "errors": [{"code": item["code"], "location": item["location"]} for item in validation["errors"]], "execution": "not_run"}
    descriptor = validation[key]
    assert isinstance(descriptor, dict)
    content = canonicalizer(descriptor)
    return {"ready": True, "status": "partial", "reason": "Canonical static export is ready. No runtime action was performed.", "action": "Store through an authorized managed integration boundary.", "media_type": "application/json", "filename": f"{descriptor['id']}.{suffix}.json", "content": content, "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(), "execution": "not_run"}


def export_privacy_policy(value: object) -> dict[str, Any]:
    return _export(value, key="policy", suffix="privacy-policy", validator=validate_privacy_policy, canonicalizer=canonical_privacy_policy_json)


def export_diagnostic_snapshot(value: object) -> dict[str, Any]:
    return _export(value, key="snapshot", suffix="diagnostic-snapshot", validator=validate_diagnostic_snapshot, canonicalizer=canonical_diagnostic_snapshot_json)


def export_diagnostic_finding(value: object) -> dict[str, Any]:
    return _export(value, key="finding", suffix="diagnostic-finding", validator=validate_diagnostic_finding, canonicalizer=canonical_diagnostic_finding_json)


def export_support_bundle_manifest(value: object) -> dict[str, Any]:
    return _export(value, key="manifest", suffix="support-bundle-manifest", validator=validate_support_bundle_manifest, canonicalizer=canonical_support_bundle_manifest_json)


def export_remediation_plan(value: object) -> dict[str, Any]:
    return _export(value, key="plan", suffix="remediation-plan", validator=validate_remediation_plan, canonicalizer=canonical_remediation_plan_json)
