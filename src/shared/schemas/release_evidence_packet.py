"""Closed, deterministic schema for release-evidence-packet.v1.

This module validates only bounded, server-owned static values. It never
inspects Git, starts a process, opens a file, contacts a network, loads a
provider, or touches a device, model, or media asset.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from typing import Any


RELEASE_EVIDENCE_PACKET_SCHEMA_VERSION = "release-evidence-packet.v1"

MAX_PACKET_BYTES = 256 * 1024
MAX_JSON_DEPTH = 16
MAX_COMMANDS = 32
MAX_EXPECTED_SKIPS = 8
MAX_REJECTION_CODES = 8
MAX_CHANGED_FILES = 1_000_000
MAX_TEXT_LENGTH = 160
MAX_ID_LENGTH = 128
MAX_TOOLCHAIN_VALUE_LENGTH = 64

STATIC_VERDICTS = ("pass", "fail", "not_run")
OPERATIONAL_STATUSES = ("operational", "partial", "planned", "unavailable", "not_run")
PROVENANCE_STATUSES = ("server_owned_validated", "manager_qa", "runtime_smoke", "not_run")
RUNTIME_SMOKE_STATUSES = ("not_run", "not_authorized", "authorized", "completed")
ADMISSION_STATUSES = ("pending_manager_qa", "admitted", "rejected", "not_run")
DURATION_CLASSES = ("instant", "short", "medium", "long", "not_run")
RUNTIME_EVIDENCE_STATUSES = ("accepted", "rejected", "not_run")

COMMAND_LABELS = (
    "ci_validate",
    "diff_check",
    "json_validator",
    "markdown_validator",
    "schema_validate",
    "static_probe",
    "unit_tests",
)
EXPECTED_SKIP_CODES = (
    "deferred",
    "expected_environment",
    "jsonschema_unavailable",
    "not_applicable",
    "optional_dependency",
    "runtime_not_authorized",
)
REJECTION_CODES = (
    "artifact_leak",
    "base_drift",
    "client_mapping_echo",
    "command_failure",
    "dirty_worktree",
    "head_drift",
    "merge_base_drift",
    "missing_manager_qa",
    "process_leak",
    "redaction_failure",
    "scope_drift",
    "unexpected_skip",
)

_OPAQUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
_SUMMARY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.: -]{0,159}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_SECRET_RE = re.compile(
    r"(?:\bsk-[A-Za-z0-9_-]{8,}|\bgh[pous]_[A-Za-z0-9]{8,}|\bgithub_pat_[A-Za-z0-9_]{8,}|"
    r"\bAKIA[0-9A-Z]{8,}|\bBearer\s+[A-Za-z0-9._~+/-]{8,}|\b(?:api[_-]?key|secret|token|password|credential)\s*(?:=|:))",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"\b(?:https?|ftp|file|data):", re.IGNORECASE)
_CREDENTIAL_URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^@\s]+@", re.IGNORECASE)
_COMMAND_RE = re.compile(
    r"(?:^|\s)(?:cmd(?:\.exe)?|powershell(?:\.exe)?|pwsh|bash|sh|zsh|curl|wget|ffmpeg|python(?:\.exe)?|node(?:\.exe)?)(?:\s|$)|"
    r"(?:&&|\|\||;|\$\()",
    re.IGNORECASE,
)
_BACKTICK_RE = re.compile(r"\x60")
_BLOB_RE = re.compile(r"(?:;base64,|(?:^|\s)(?:data|blob|weights?|checkpoint|model)(?:\s|:|=)|\.(?:bin|ckpt|gguf|onnx|pt|pth|safetensors)(?:$|[?#]))", re.IGNORECASE)
_PATH_RE = re.compile(r"(?:^|[\\/])\.\.(?:[\\/]|$)|^[A-Za-z]:[\\/]|^(?:\\\\|//)|^(?:/|~[\\/])")


_MESSAGES = {
    "object_required": "A plain object is required.",
    "unknown_field": "Unknown fields are not permitted.",
    "required_field": "A required field is missing.",
    "packet_size": "The packet exceeds the static byte bound.",
    "json_depth": "JSON nesting exceeds the static bound.",
    "invalid_text": "Text is outside the safe bounded contract.",
    "unsafe_value": "The value contains a forbidden path, secret, command, URL, or payload.",
    "invalid_identifier": "The identifier is not a bounded opaque value.",
    "invalid_ref": "The reference is not a bounded pinned value.",
    "invalid_digest": "The digest is not a lowercase SHA-256 value.",
    "invalid_enum": "The value is not part of the fixed allowlist.",
    "invalid_integer": "The value is not a bounded integer.",
    "duplicate_value": "Values in this collection must be unique.",
    "count_invariant": "Changed-file counts are inconsistent.",
    "runtime_claim_unauthorized": "Operational status requires named accepted runtime evidence.",
    "admission_invalid": "Admission status and evidence are inconsistent.",
    "fingerprint_mismatch": "The packet fingerprint does not match its canonical projection.",
    "invalid_json": "The input is not bounded valid JSON.",
    "duplicate_json_key": "Duplicate JSON keys are rejected.",
    "nonfinite_json": "Non-finite JSON numbers are rejected.",
    "invalid_utf8": "Input must be valid UTF-8.",
}


def _issue(code: str, location: str) -> dict[str, str]:
    return {"code": code, "message": _MESSAGES.get(code, "The static contract rejected this value."), "location": location}


def _append(errors: list[dict[str, str]], code: str, location: str) -> None:
    errors.append(_issue(code, location))


def is_plain_static_value(value: object, *, depth: int = 0, _seen: set[int] | None = None) -> bool:
    """Return whether value is a bounded JSON-compatible static value."""

    if depth > MAX_JSON_DEPTH:
        return False
    if value is None or type(value) in (str, int, bool):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if type(value) is list:
        seen = _seen if _seen is not None else set()
        marker = id(value)
        if marker in seen:
            return False
        seen.add(marker)
        result = all(is_plain_static_value(item, depth=depth + 1, _seen=seen) for item in value)
        seen.remove(marker)
        return result
    if type(value) is dict:
        seen = _seen if _seen is not None else set()
        marker = id(value)
        if marker in seen:
            return False
        seen.add(marker)
        result = all(
            type(key) is str and is_plain_static_value(item, depth=depth + 1, _seen=seen)
            for key, item in value.items()
        )
        seen.remove(marker)
        return result
    return False


def _safe_string(value: object, errors: list[dict[str, str]], location: str, *, maximum: int = MAX_TEXT_LENGTH) -> bool:
    if type(value) is not str or not 1 <= len(value) <= maximum:
        _append(errors, "invalid_text", location)
        return False
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        _append(errors, "invalid_text", location)
        return False
    if _SECRET_RE.search(value) or _URL_RE.search(value) or _CREDENTIAL_URL_RE.search(value) or _COMMAND_RE.search(value) or _BACKTICK_RE.search(value) or _BLOB_RE.search(value):
        _append(errors, "unsafe_value", location)
        return False
    return True


def _opaque(value: object, errors: list[dict[str, str]], location: str) -> bool:
    if not _safe_string(value, errors, location, maximum=MAX_ID_LENGTH):
        return False
    if not _OPAQUE_RE.fullmatch(value):
        _append(errors, "invalid_identifier", location)
        return False
    return True


def _ref(value: object, errors: list[dict[str, str]], location: str) -> bool:
    if not _safe_string(value, errors, location, maximum=MAX_ID_LENGTH):
        return False
    if not _REF_RE.fullmatch(value) or _PATH_RE.search(value) or "\\" in value or ".." in value:
        _append(errors, "invalid_ref", location)
        return False
    return True


def _digest(value: object, errors: list[dict[str, str]], location: str) -> bool:
    if type(value) is not str or not _DIGEST_RE.fullmatch(value):
        _append(errors, "invalid_digest", location)
        return False
    return True


def _summary(value: object, errors: list[dict[str, str]], location: str) -> bool:
    if not _safe_string(value, errors, location, maximum=MAX_TEXT_LENGTH):
        return False
    if not _SUMMARY_RE.fullmatch(value):
        _append(errors, "invalid_text", location)
        return False
    return True


def _enum(value: object, allowed: tuple[str, ...], errors: list[dict[str, str]], location: str) -> bool:
    if type(value) is not str or value not in allowed:
        _append(errors, "invalid_enum", location)
        return False
    return True


def _integer(value: object, errors: list[dict[str, str]], location: str, *, maximum: int) -> bool:
    if type(value) is not int or not 0 <= value <= maximum:
        _append(errors, "invalid_integer", location)
        return False
    return True


def _plain_object(value: object, errors: list[dict[str, str]], location: str) -> bool:
    if type(value) is not dict:
        _append(errors, "object_required", location)
        return False
    return True


def _strict_keys(value: object, allowed: set[str], required: tuple[str, ...], errors: list[dict[str, str]], location: str) -> bool:
    if not _plain_object(value, errors, location):
        return False
    assert type(value) is dict
    for key in value:
        if type(key) is not str or key not in allowed:
            _append(errors, "unknown_field", location)
    for key in required:
        if key not in value:
            _append(errors, "required_field", f"{location}.{key}")
    return True


def _unique_strings(value: object, allowed: tuple[str, ...], maximum: int, errors: list[dict[str, str]], location: str) -> None:
    if type(value) is not list:
        _append(errors, "object_required", location)
        return
    if len(value) > maximum:
        _append(errors, "invalid_integer", location)
    seen: set[str] = set()
    for item in value:
        if type(item) is not str or item not in allowed:
            _append(errors, "invalid_enum", location)
        elif item in seen:
            _append(errors, "duplicate_value", location)
        else:
            seen.add(item)


def _validate_identity(value: object, errors: list[dict[str, str]], location: str) -> None:
    fields = (
        "candidate_branch",
        "candidate_ref",
        "base_ref",
        "base_sha",
        "head_sha",
        "merge_base_sha",
        "lane_or_candidate",
        "owner",
        "changed_file_summary",
        "toolchain",
    )
    if not _strict_keys(value, set(fields), fields, errors, location):
        return
    assert type(value) is dict
    _ref(value.get("candidate_branch"), errors, f"{location}.candidate_branch")
    _ref(value.get("candidate_ref"), errors, f"{location}.candidate_ref")
    _ref(value.get("base_ref"), errors, f"{location}.base_ref")
    for field in ("base_sha", "head_sha", "merge_base_sha"):
        _opaque(value.get(field), errors, f"{location}.{field}")
    _opaque(value.get("lane_or_candidate"), errors, f"{location}.lane_or_candidate")
    _opaque(value.get("owner"), errors, f"{location}.owner")

    summary = value.get("changed_file_summary")
    summary_fields = ("added", "modified", "deleted", "total", "scope_fingerprint")
    if _strict_keys(summary, set(summary_fields), summary_fields, errors, f"{location}.changed_file_summary"):
        assert type(summary) is dict
        for field in ("added", "modified", "deleted", "total"):
            _integer(summary.get(field), errors, f"{location}.changed_file_summary.{field}", maximum=MAX_CHANGED_FILES)
        if all(type(summary.get(field)) is int for field in ("added", "modified", "deleted", "total")):
            if summary["total"] != summary["added"] + summary["modified"] + summary["deleted"]:
                _append(errors, "count_invariant", f"{location}.changed_file_summary")
        _digest(summary.get("scope_fingerprint"), errors, f"{location}.changed_file_summary.scope_fingerprint")

    toolchain = value.get("toolchain")
    tool_fields = ("git", "python", "validator", "ci")
    if _strict_keys(toolchain, set(tool_fields), tool_fields, errors, f"{location}.toolchain"):
        assert type(toolchain) is dict
        for field in tool_fields:
            _summary(toolchain.get(field), errors, f"{location}.toolchain.{field}")


def _validate_commands(value: object, errors: list[dict[str, str]], location: str) -> None:
    if type(value) is not list:
        _append(errors, "object_required", location)
        return
    if not 1 <= len(value) <= MAX_COMMANDS:
        _append(errors, "invalid_integer", location)
    labels: set[str] = set()
    fields = ("label", "exit_status", "duration_class", "expected_skips", "deterministic_summary")
    for index, item in enumerate(value):
        item_location = f"{location}[{index}]"
        if not _strict_keys(item, set(fields), fields, errors, item_location):
            continue
        assert type(item) is dict
        label = item.get("label")
        if not _enum(label, COMMAND_LABELS, errors, f"{item_location}.label"):
            continue
        if label in labels:
            _append(errors, "duplicate_value", f"{item_location}.label")
        labels.add(label)
        _integer(item.get("exit_status"), errors, f"{item_location}.exit_status", maximum=255)
        _enum(item.get("duration_class"), DURATION_CLASSES, errors, f"{item_location}.duration_class")
        _unique_strings(item.get("expected_skips"), EXPECTED_SKIP_CODES, MAX_EXPECTED_SKIPS, errors, f"{item_location}.expected_skips")
        _summary(item.get("deterministic_summary"), errors, f"{item_location}.deterministic_summary")


def _validate_runtime_evidence(value: object, errors: list[dict[str, str]], location: str) -> None:
    if value is None:
        return
    fields = ("id", "approval_id", "status")
    if not _strict_keys(value, set(fields), fields, errors, location):
        return
    assert type(value) is dict
    _opaque(value.get("id"), errors, f"{location}.id")
    _opaque(value.get("approval_id"), errors, f"{location}.approval_id")
    _enum(value.get("status"), RUNTIME_EVIDENCE_STATUSES, errors, f"{location}.status")


def _validate_verdict(value: object, runtime_evidence: object, errors: list[dict[str, str]], location: str) -> None:
    fields = ("static", "operational", "provenance", "runtime_smoke")
    if not _strict_keys(value, set(fields), fields, errors, location):
        return
    assert type(value) is dict
    static = value.get("static")
    operational = value.get("operational")
    provenance = value.get("provenance")
    runtime_smoke = value.get("runtime_smoke")
    _enum(static, STATIC_VERDICTS, errors, f"{location}.static")
    _enum(operational, OPERATIONAL_STATUSES, errors, f"{location}.operational")
    _enum(provenance, PROVENANCE_STATUSES, errors, f"{location}.provenance")
    _enum(runtime_smoke, RUNTIME_SMOKE_STATUSES, errors, f"{location}.runtime_smoke")
    if operational == "operational":
        accepted = (
            static == "pass"
            and runtime_smoke == "completed"
            and type(runtime_evidence) is dict
            and runtime_evidence.get("status") == "accepted"
            and provenance in ("manager_qa", "runtime_smoke")
        )
        if not accepted:
            _append(errors, "runtime_claim_unauthorized", location)
    if static == "fail" and operational == "operational":
        _append(errors, "runtime_claim_unauthorized", location)


def _validate_admission(value: object, errors: list[dict[str, str]], location: str) -> None:
    fields = ("status", "rejection_codes", "manager_qa_id")
    if not _strict_keys(value, set(fields), fields, errors, location):
        return
    assert type(value) is dict
    status = value.get("status")
    _enum(status, ADMISSION_STATUSES, errors, f"{location}.status")
    codes = value.get("rejection_codes")
    if type(codes) is not list:
        _append(errors, "object_required", f"{location}.rejection_codes")
    else:
        if len(codes) > MAX_REJECTION_CODES:
            _append(errors, "invalid_integer", f"{location}.rejection_codes")
        seen: set[str] = set()
        for code in codes:
            if type(code) is not str or code not in REJECTION_CODES:
                _append(errors, "invalid_enum", f"{location}.rejection_codes")
            elif code in seen:
                _append(errors, "duplicate_value", f"{location}.rejection_codes")
            else:
                seen.add(code)
    manager_id = value.get("manager_qa_id")
    if manager_id is not None:
        _opaque(manager_id, errors, f"{location}.manager_qa_id")
    if status == "admitted" and (type(manager_id) is not str or not manager_id):
        _append(errors, "admission_invalid", location)
    if status == "rejected" and (not isinstance(codes, list) or not codes):
        _append(errors, "admission_invalid", location)
    if status in ("pending_manager_qa", "not_run") and isinstance(codes, list) and codes:
        _append(errors, "admission_invalid", location)


def _validate_fingerprint(value: object, errors: list[dict[str, str]], location: str) -> None:
    fields = ("algorithm", "value", "projection")
    if not _strict_keys(value, set(fields), fields, errors, location):
        return
    assert type(value) is dict
    if value.get("algorithm") != "sha256":
        _append(errors, "invalid_enum", f"{location}.algorithm")
    _digest(value.get("value"), errors, f"{location}.value")
    if value.get("projection") != "canonical_redacted_v1":
        _append(errors, "invalid_enum", f"{location}.projection")


def _normalized_packet(value: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(value)
    normalized["commands"] = sorted(
        ({**item, "expected_skips": sorted(item["expected_skips"])} for item in normalized["commands"]),
        key=lambda item: item["label"],
    )
    normalized["admission"]["rejection_codes"] = sorted(normalized["admission"]["rejection_codes"])
    return normalized


def canonical_release_evidence_packet_json(value: dict[str, Any]) -> str:
    normalized = _normalized_packet(value)
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _fingerprint_input(value: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalized_packet(value)
    normalized["fingerprint"] = dict(normalized["fingerprint"])
    normalized["fingerprint"].pop("value", None)
    return normalized


def release_evidence_packet_fingerprint(value: dict[str, Any]) -> str:
    canonical = json.dumps(_fingerprint_input(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_release_evidence_packet(value: object) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    if type(value) is not dict:
        _append(errors, "object_required", "packet")
        return {"valid": False, "errors": errors, "packet": None, "fingerprint": None, "execution": "not_run"}
    if not is_plain_static_value(value):
        _append(errors, "unsafe_value", "packet")
        return {"valid": False, "errors": errors, "packet": None, "fingerprint": None, "execution": "not_run"}
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        _append(errors, "invalid_json", "packet")
        return {"valid": False, "errors": errors, "packet": None, "fingerprint": None, "execution": "not_run"}
    if len(encoded) > MAX_PACKET_BYTES:
        _append(errors, "packet_size", "packet")

    fields = ("contract", "identity", "commands", "verdict", "runtime_evidence", "admission", "fingerprint")
    if not _strict_keys(value, set(fields), fields, errors, "packet"):
        return {"valid": False, "errors": errors, "packet": None, "fingerprint": None, "execution": "not_run"}
    assert type(value) is dict
    if value.get("contract") != RELEASE_EVIDENCE_PACKET_SCHEMA_VERSION:
        _append(errors, "invalid_enum", "packet.contract")
    _validate_identity(value.get("identity"), errors, "packet.identity")
    _validate_commands(value.get("commands"), errors, "packet.commands")
    _validate_runtime_evidence(value.get("runtime_evidence"), errors, "packet.runtime_evidence")
    _validate_verdict(value.get("verdict"), value.get("runtime_evidence"), errors, "packet.verdict")
    _validate_admission(value.get("admission"), errors, "packet.admission")
    _validate_fingerprint(value.get("fingerprint"), errors, "packet.fingerprint")

    if errors:
        return {"valid": False, "errors": errors, "packet": None, "fingerprint": None, "execution": "not_run"}

    normalized = _normalized_packet(value)
    expected = release_evidence_packet_fingerprint(normalized)
    if normalized["fingerprint"]["value"] != expected:
        _append(errors, "fingerprint_mismatch", "packet.fingerprint.value")
        return {"valid": False, "errors": errors, "packet": None, "fingerprint": None, "execution": "not_run"}
    return {
        "valid": True,
        "errors": [],
        "packet": copy.deepcopy(normalized),
        "fingerprint": expected,
        "execution": "not_run",
    }


def _schema_object(properties: dict[str, Any], required: tuple[str, ...]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


_ID_SCHEMA = {"type": "string", "pattern": _OPAQUE_RE.pattern, "maxLength": MAX_ID_LENGTH}
_REF_SCHEMA = {"type": "string", "pattern": _REF_RE.pattern, "maxLength": MAX_ID_LENGTH}
_DIGEST_SCHEMA = {"type": "string", "pattern": _DIGEST_RE.pattern, "minLength": 64, "maxLength": 64}
_SAFE_SCHEMA = {"type": "string", "minLength": 1, "maxLength": MAX_TEXT_LENGTH}
_RUNTIME_EVIDENCE_SCHEMA = {
    "oneOf": [
        {"type": "null"},
        _schema_object(
            {"id": _ID_SCHEMA, "approval_id": _ID_SCHEMA, "status": {"type": "string", "enum": list(RUNTIME_EVIDENCE_STATUSES)}},
            ("id", "approval_id", "status"),
        ),
    ]
}

_RELEASE_EVIDENCE_PACKET_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://local-ai-hub.invalid/schemas/release-evidence-packet.v1.json",
    "title": "Local AI Hub release-evidence-packet.v1",
    **_schema_object(
        {
            "contract": {"const": RELEASE_EVIDENCE_PACKET_SCHEMA_VERSION},
            "identity": _schema_object(
                {
                    "candidate_branch": _REF_SCHEMA,
                    "candidate_ref": _REF_SCHEMA,
                    "base_ref": _REF_SCHEMA,
                    "base_sha": _ID_SCHEMA,
                    "head_sha": _ID_SCHEMA,
                    "merge_base_sha": _ID_SCHEMA,
                    "lane_or_candidate": _ID_SCHEMA,
                    "owner": _ID_SCHEMA,
                    "changed_file_summary": _schema_object(
                        {
                            "added": {"type": "integer", "minimum": 0, "maximum": MAX_CHANGED_FILES},
                            "modified": {"type": "integer", "minimum": 0, "maximum": MAX_CHANGED_FILES},
                            "deleted": {"type": "integer", "minimum": 0, "maximum": MAX_CHANGED_FILES},
                            "total": {"type": "integer", "minimum": 0, "maximum": MAX_CHANGED_FILES},
                            "scope_fingerprint": _DIGEST_SCHEMA,
                        },
                        ("added", "modified", "deleted", "total", "scope_fingerprint"),
                    ),
                    "toolchain": _schema_object({key: _SAFE_SCHEMA for key in ("git", "python", "validator", "ci")}, ("git", "python", "validator", "ci")),
                },
                ("candidate_branch", "candidate_ref", "base_ref", "base_sha", "head_sha", "merge_base_sha", "lane_or_candidate", "owner", "changed_file_summary", "toolchain"),
            ),
            "commands": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_COMMANDS,
                "items": _schema_object(
                    {
                        "label": {"type": "string", "enum": list(COMMAND_LABELS)},
                        "exit_status": {"type": "integer", "minimum": 0, "maximum": 255},
                        "duration_class": {"type": "string", "enum": list(DURATION_CLASSES)},
                        "expected_skips": {"type": "array", "maxItems": MAX_EXPECTED_SKIPS, "uniqueItems": True, "items": {"type": "string", "enum": list(EXPECTED_SKIP_CODES)}},
                        "deterministic_summary": _SAFE_SCHEMA,
                    },
                    ("label", "exit_status", "duration_class", "expected_skips", "deterministic_summary"),
                ),
            },
            "verdict": _schema_object(
                {
                    "static": {"type": "string", "enum": list(STATIC_VERDICTS)},
                    "operational": {"type": "string", "enum": list(OPERATIONAL_STATUSES)},
                    "provenance": {"type": "string", "enum": list(PROVENANCE_STATUSES)},
                    "runtime_smoke": {"type": "string", "enum": list(RUNTIME_SMOKE_STATUSES)},
                },
                ("static", "operational", "provenance", "runtime_smoke"),
            ),
            "runtime_evidence": _RUNTIME_EVIDENCE_SCHEMA,
            "admission": _schema_object(
                {
                    "status": {"type": "string", "enum": list(ADMISSION_STATUSES)},
                    "rejection_codes": {"type": "array", "maxItems": MAX_REJECTION_CODES, "uniqueItems": True, "items": {"type": "string", "enum": list(REJECTION_CODES)}},
                    "manager_qa_id": {"oneOf": [{"type": "null"}, _ID_SCHEMA]},
                },
                ("status", "rejection_codes", "manager_qa_id"),
            ),
            "fingerprint": _schema_object(
                {
                    "algorithm": {"const": "sha256"},
                    "value": _DIGEST_SCHEMA,
                    "projection": {"const": "canonical_redacted_v1"},
                },
                ("algorithm", "value", "projection"),
            ),
        },
        ("contract", "identity", "commands", "verdict", "runtime_evidence", "admission", "fingerprint"),
    ),
}


def release_evidence_packet_schema() -> dict[str, Any]:
    return copy.deepcopy(_RELEASE_EVIDENCE_PACKET_SCHEMA)


def release_evidence_packet_schemas() -> tuple[dict[str, Any], ...]:
    return (release_evidence_packet_schema(),)


# Short aliases mirror the surrounding schema modules.
canonical_packet_json = canonical_release_evidence_packet_json
packet_fingerprint = release_evidence_packet_fingerprint
validate_packet = validate_release_evidence_packet


__all__ = [
    "ADMISSION_STATUSES",
    "COMMAND_LABELS",
    "DURATION_CLASSES",
    "EXPECTED_SKIP_CODES",
    "MAX_PACKET_BYTES",
    "OPERATIONAL_STATUSES",
    "PROVENANCE_STATUSES",
    "RELEASE_EVIDENCE_PACKET_SCHEMA_VERSION",
    "REJECTION_CODES",
    "RUNTIME_SMOKE_STATUSES",
    "STATIC_VERDICTS",
    "canonical_packet_json",
    "canonical_release_evidence_packet_json",
    "is_plain_static_value",
    "packet_fingerprint",
    "release_evidence_packet_fingerprint",
    "release_evidence_packet_schema",
    "release_evidence_packet_schemas",
    "validate_packet",
    "validate_release_evidence_packet",
]
