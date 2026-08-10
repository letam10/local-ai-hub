"""Fail-closed static privacy and diagnostics contracts.

The contracts in this module describe server-owned metadata only.  They never
open a path, inspect a process or device, execute a command, accept a log or
attachment, or mutate a configuration/asset/runtime.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Iterable


PRIVACY_POLICY_SCHEMA_VERSION = "privacy-policy.v1"
DIAGNOSTIC_SNAPSHOT_SCHEMA_VERSION = "diagnostic-snapshot.v1"
SUPPORT_BUNDLE_MANIFEST_SCHEMA_VERSION = "support-bundle-manifest.v1"
DIAGNOSTIC_FINDING_SCHEMA_VERSION = "diagnostic-finding.v1"
REMEDIATION_PLAN_SCHEMA_VERSION = "remediation-plan.v1"

MAX_DESCRIPTOR_BYTES = 512 * 1024
MAX_TEXT_LENGTH = 160
MAX_JSON_DEPTH = 32
MAX_POLICY_LIST = 32
MAX_SNAPSHOT_RECORDS = 128
MAX_FINDINGS = 256
MAX_BUNDLE_ENTRIES = 256
MAX_REMEDIATION_ACTIONS = 128
MAX_EVIDENCE_COUNT = 1_000_000
MAX_REVISION = 1_000_000

COMPONENT_IDS = (
    "asset-catalog",
    "hub-core",
    "job-manager",
    "node-studio",
    "privacy-diagnostics",
    "support-bundle",
)
CONFIG_KEYS = (
    "diagnostic_consent",
    "privacy_policy",
    "provider_capabilities",
    "retention",
    "support_bundle",
)
TOOL_IDS = (
    "animesr",
    "asset-catalog",
    "caption",
    "comfyui",
    "embedding",
    "ffmpeg",
    "rife",
    "sam2",
    "tag",
)
CONTRACT_IDS = (
    "diagnostic-finding",
    "diagnostic-snapshot",
    "privacy-policy",
    "remediation-plan",
    "support-bundle-manifest",
)

COMPONENT_STATUSES = ("operational", "partial", "unavailable", "planned", "not_run")
CONFIG_STATUSES = ("in_sync", "drifted", "missing", "unknown", "not_run")
TOOL_STATUSES = COMPONENT_STATUSES
CONTRACT_STATUSES = ("current", "stale", "missing", "not_run")
EVIDENCE_STATUSES = ("complete", "partial", "missing")
CONSENT_STATES = ("granted", "required", "revoked", "not_recorded")
CONSENT_SCOPES = ("diagnostics", "support-bundle", "diagnostics-and-support")
RETENTION_CLASSES = ("diagnostic", "support", "ephemeral")
REDACTION_PROFILES = ("strict",)
FINDING_RULE_IDS = (
    "component_status",
    "config_drift",
    "consent_required",
    "contract_stale",
    "missing_evidence",
    "tool_status",
)
FINDING_SEVERITIES = ("info", "warning", "error")
FINDING_AVAILABILITY = ("partial", "unavailable", "planned")
FINDING_SUBJECT_KINDS = ("component", "config", "contract", "policy", "tool")
ACTION_CODES = (
    "collect_missing_evidence",
    "record_consent",
    "refresh_contract_evidence",
    "review_component_status",
    "review_config_drift",
    "review_tool_status",
)
REMEDIATION_RISKS = ("low", "medium", "high")
REMEDIATION_STATUSES = ("planned", "manual_review", "unavailable")
PREREQUISITE_CODES = (
    "consent-granted",
    "policy-loaded",
    "server-owned-snapshot",
    "static-review",
)
ROLLBACK_CODES = ("no-op-review-only", "discard-dry-run", "restore-declared-metadata")

REASON_TEXT = {
    "component_status": "A server-owned component status is not operational.",
    "config_drift": "A server-owned configuration digest differs from its declared policy digest.",
    "consent_required": "Diagnostic or support consent is not granted in the server-owned ledger.",
    "contract_stale": "A server-owned contract record is stale or missing current evidence.",
    "missing_evidence": "The server-owned snapshot is missing bounded evidence for an expected subject.",
    "tool_status": "A server-owned tool status is not operational and no runtime probe was performed.",
}
ACTION_TEXT = {
    "collect_missing_evidence": "Request a separately authorized bounded evidence collection. Do not probe from this plan.",
    "record_consent": "Record an authorized consent decision in the server-owned ledger before support projection.",
    "refresh_contract_evidence": "Review and refresh the declared contract evidence through an authorized static process.",
    "review_component_status": "Review the component status and authorize a separate bounded smoke if needed.",
    "review_config_drift": "Review configuration drift against the declared policy without editing the configuration.",
    "review_tool_status": "Review the tool status and authorize a separate bounded smoke if needed.",
}
REASON_CODE_BY_RULE = {
    "component_status": "component_status",
    "config_drift": "config_drift",
    "consent_required": "consent_required",
    "contract_stale": "contract_stale",
    "missing_evidence": "missing_evidence",
    "tool_status": "tool_status",
}

OPAQUE_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+){0,11}$")
VERSION_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_WINDOWS_ROOT_RE = re.compile(r"^[\\/](?![\\/])")
_UNC_RE = re.compile(r"^(?:\\\\|//)")
_ABSOLUTE_RE = re.compile(r"^(?:/|~[\\/])")
_PARENT_RE = re.compile(r"(?:^|[\\/])\.\.(?:[\\/]|$)")
_RELATIVE_PATH_RE = re.compile(r"^(?:[A-Za-z0-9._-]+[\\/])+[A-Za-z0-9._-]+$")
_URI_RE = re.compile(r"\b(?:file|https?|ftp|data):", re.IGNORECASE)
_SECRET_RE = re.compile(
    r"(?:\bsk-[A-Za-z0-9_-]{12,}|\bgh[pous]_[A-Za-z0-9]{12,}|\bgithub_pat_[A-Za-z0-9_]{12,}|"
    r"\bAKIA[0-9A-Z]{12,}|\bBearer\s+[A-Za-z0-9._~+/-]{12,})",
    re.IGNORECASE,
)
_ASSIGNMENT_RE = re.compile(r"\b(?:api[_-]?key|authorization|credential|password|secret|token)\s*(?:=|:)\s*[^\s,;]+", re.IGNORECASE)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_COMMAND_RE = re.compile(
    r"(?:^|\s)(?:cmd(?:\.exe)?|powershell(?:\.exe)?|pwsh|bash|sh|zsh|curl|wget|ffmpeg|python(?:\.exe)?|node(?:\.exe)?)(?:\s|$)|"
    r"(?:&&|\|\||;|`|\$\()",
    re.IGNORECASE,
)
_PAYLOAD_RE = re.compile(r"(?:^data:|;base64,|\.(?:zip|tar|gz|7z|safetensors|ckpt|pth|pt|onnx|gguf|bin)(?:$|[?#]))", re.IGNORECASE)
_LONG_BLOB_RE = re.compile(r"^[A-Za-z0-9+/=_-]{512,}$")
_FORBIDDEN_KEY_TOKENS = {
    "apikey",
    "argv",
    "attachment",
    "authorization",
    "blob",
    "cmd",
    "command",
    "constructor",
    "credential",
    "directory",
    "env",
    "executable",
    "file",
    "filepath",
    "inputpath",
    "log",
    "logs",
    "outputpath",
    "password",
    "path",
    "proto",
    "prototype",
    "runner",
    "secret",
    "shell",
    "token",
    "url",
    "weights",
}


def _schema_object(properties: dict[str, Any], required: Iterable[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


_OPAQUE_SCHEMA = {"type": "string", "pattern": OPAQUE_ID_RE.pattern, "maxLength": 120, "allOf": [{"not": {"pattern": r"[\u0000-\u001F]"}}]}
_VERSION_SCHEMA = {"type": "string", "pattern": VERSION_RE.pattern, "maxLength": 32}
_DIGEST_SCHEMA = {"type": "string", "pattern": SHA256_RE.pattern, "minLength": 64, "maxLength": 64}
_SAFE_TEXT_SCHEMA = {
    "type": "string",
    "minLength": 1,
    "maxLength": MAX_TEXT_LENGTH,
    "allOf": [
        {"not": {"pattern": r"[\u0000-\u001F]"}},
        {"not": {"pattern": r"^(?:[A-Za-z]:|[\\/]|~[\\/])"}},
        {"not": {"pattern": r"^(?:[A-Za-z0-9._-]+[\\/])+[A-Za-z0-9._-]+$"}},
        {"not": {"pattern": r"(?:^|[\\/])\.\.(?:[\\/]|$)"}},
        {"not": {"pattern": r"(?:[Ff][Ii][Ll][Ee]|[Hh][Tt][Tt][Pp][Ss]?|[Ff][Tt][Pp]|[Dd][Aa][Tt][Aa]):"}},
        {"not": {"pattern": r"\b(?:api[_-]?key|authorization|credential|password|secret|token)\s*(?:=|:)"}},
        {"not": {"pattern": r"(?:\bsk-[A-Za-z0-9_-]{12,}|\bgh[pous]_[A-Za-z0-9]{12,}|\bgithub_pat_[A-Za-z0-9_]{12,}|\bAKIA[0-9A-Z]{12,}|\bBearer\s+[A-Za-z0-9._~+/-]{12,})"}},
        {"not": {"pattern": r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"}},
        {"not": {"pattern": r"(?:^|\s)(?:cmd(?:\.exe)?|powershell(?:\.exe)?|pwsh|bash|sh|zsh|curl|wget|ffmpeg|python(?:\.exe)?|node(?:\.exe)?)(?:\s|$)"}},
        {"not": {"pattern": r"(?:&&|\|\||;|`|\$\()"}},
        {"not": {"pattern": r"(?:;base64,|\.(?:zip|tar|gz|7z|safetensors|ckpt|pth|pt|onnx|gguf|bin)(?:$|[?#]))"}},
        {"not": {"pattern": r"^[A-Za-z0-9+/=_-]{512,}$"}},
    ],
}
_MANAGED_SOURCE_SCHEMA = _schema_object({"kind": {"const": "managed"}, "reference": _OPAQUE_SCHEMA}, ("kind", "reference"))
_SERVER_SOURCE_SCHEMA = _schema_object({"kind": {"const": "server-owned"}, "reference": _OPAQUE_SCHEMA}, ("kind", "reference"))
_LEDGER_SCHEMA = _schema_object(
    {"consent_ref": _OPAQUE_SCHEMA, "retention_ref": _OPAQUE_SCHEMA, "revision": {"type": "integer", "minimum": 0, "maximum": MAX_REVISION}},
    ("consent_ref", "retention_ref", "revision"),
)
_CONSENT_SCHEMA = _schema_object(
    {
        "state": {"type": "string", "enum": list(CONSENT_STATES)},
        "scope": {"type": "string", "enum": list(CONSENT_SCOPES)},
        "ledger_ref": _OPAQUE_SCHEMA,
        "revision": {"type": "integer", "minimum": 0, "maximum": MAX_REVISION},
    },
    ("state", "scope", "ledger_ref", "revision"),
)
_RETENTION_SCHEMA = _schema_object(
    {
        "classification": {"type": "string", "enum": list(RETENTION_CLASSES)},
        "days": {"type": "integer", "minimum": 0, "maximum": 3650},
        "ledger_ref": _OPAQUE_SCHEMA,
    },
    ("classification", "days", "ledger_ref"),
)

_PRIVACY_POLICY_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://local-ai-hub.invalid/schemas/privacy-policy.v1.json",
    "title": "Local AI Hub privacy-policy.v1",
    **_schema_object(
        {
            "schema_version": {"const": PRIVACY_POLICY_SCHEMA_VERSION},
            "id": _OPAQUE_SCHEMA,
            "label": {**_SAFE_TEXT_SCHEMA, "maxLength": MAX_TEXT_LENGTH},
            "revision": {"type": "integer", "minimum": 0, "maximum": MAX_REVISION},
            "source": _MANAGED_SOURCE_SCHEMA,
            "ledger": _LEDGER_SCHEMA,
            "consent": _CONSENT_SCHEMA,
            "retention": _RETENTION_SCHEMA,
            "redaction_profile": {"type": "string", "enum": list(REDACTION_PROFILES)},
            "components": {"type": "array", "minItems": 1, "maxItems": MAX_POLICY_LIST, "uniqueItems": True, "items": {"type": "string", "enum": list(COMPONENT_IDS)}},
            "config_keys": {"type": "array", "minItems": 1, "maxItems": MAX_POLICY_LIST, "uniqueItems": True, "items": {"type": "string", "enum": list(CONFIG_KEYS)}},
            "tool_ids": {"type": "array", "minItems": 1, "maxItems": MAX_POLICY_LIST, "uniqueItems": True, "items": {"type": "string", "enum": list(TOOL_IDS)}},
            "contract_ids": {"type": "array", "minItems": 1, "maxItems": MAX_POLICY_LIST, "uniqueItems": True, "items": {"type": "string", "enum": list(CONTRACT_IDS)}},
        },
        ("schema_version", "id", "label", "revision", "source", "ledger", "consent", "retention", "redaction_profile", "components", "config_keys", "tool_ids", "contract_ids"),
    ),
}

_COMPONENT_RECORD_SCHEMA = _schema_object(
    {"id": _OPAQUE_SCHEMA, "status": {"type": "string", "enum": list(COMPONENT_STATUSES)}, "evidence_count": {"type": "integer", "minimum": 0, "maximum": MAX_EVIDENCE_COUNT}, "metadata_digest": _DIGEST_SCHEMA},
    ("id", "status", "evidence_count", "metadata_digest"),
)
_CONFIG_RECORD_SCHEMA = _schema_object(
    {"id": _OPAQUE_SCHEMA, "status": {"type": "string", "enum": list(CONFIG_STATUSES)}, "expected_digest": _DIGEST_SCHEMA, "observed_digest": _DIGEST_SCHEMA, "evidence_count": {"type": "integer", "minimum": 0, "maximum": MAX_EVIDENCE_COUNT}},
    ("id", "status", "expected_digest", "observed_digest", "evidence_count"),
)
_TOOL_RECORD_SCHEMA = _schema_object(
    {"id": _OPAQUE_SCHEMA, "status": {"type": "string", "enum": list(TOOL_STATUSES)}, "evidence_count": {"type": "integer", "minimum": 0, "maximum": MAX_EVIDENCE_COUNT}, "metadata_digest": _DIGEST_SCHEMA},
    ("id", "status", "evidence_count", "metadata_digest"),
)
_CONTRACT_RECORD_SCHEMA = _schema_object(
    {"id": _OPAQUE_SCHEMA, "expected_version": _VERSION_SCHEMA, "observed_version": _VERSION_SCHEMA, "status": {"type": "string", "enum": list(CONTRACT_STATUSES)}, "evidence_count": {"type": "integer", "minimum": 0, "maximum": MAX_EVIDENCE_COUNT}, "metadata_digest": _DIGEST_SCHEMA},
    ("id", "expected_version", "observed_version", "status", "evidence_count", "metadata_digest"),
)
_EVIDENCE_SCHEMA = _schema_object(
    {"status": {"type": "string", "enum": list(EVIDENCE_STATUSES)}, "components": {"type": "integer", "minimum": 0, "maximum": MAX_SNAPSHOT_RECORDS}, "configs": {"type": "integer", "minimum": 0, "maximum": MAX_SNAPSHOT_RECORDS}, "tools": {"type": "integer", "minimum": 0, "maximum": MAX_SNAPSHOT_RECORDS}, "contracts": {"type": "integer", "minimum": 0, "maximum": MAX_SNAPSHOT_RECORDS}, "digest": _DIGEST_SCHEMA},
    ("status", "components", "configs", "tools", "contracts", "digest"),
)

_DIAGNOSTIC_SNAPSHOT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://local-ai-hub.invalid/schemas/diagnostic-snapshot.v1.json",
    "title": "Local AI Hub diagnostic-snapshot.v1",
    **_schema_object(
        {
            "schema_version": {"const": DIAGNOSTIC_SNAPSHOT_SCHEMA_VERSION},
            "id": _OPAQUE_SCHEMA,
            "label": _SAFE_TEXT_SCHEMA,
            "policy_id": _OPAQUE_SCHEMA,
            "source": _SERVER_SOURCE_SCHEMA,
            "ledger": _LEDGER_SCHEMA,
            "consent": _CONSENT_SCHEMA,
            "components": {"type": "array", "maxItems": MAX_SNAPSHOT_RECORDS, "items": _COMPONENT_RECORD_SCHEMA},
            "configs": {"type": "array", "maxItems": MAX_SNAPSHOT_RECORDS, "items": _CONFIG_RECORD_SCHEMA},
            "tools": {"type": "array", "maxItems": MAX_SNAPSHOT_RECORDS, "items": _TOOL_RECORD_SCHEMA},
            "contracts": {"type": "array", "maxItems": MAX_SNAPSHOT_RECORDS, "items": _CONTRACT_RECORD_SCHEMA},
            "evidence": _EVIDENCE_SCHEMA,
        },
        ("schema_version", "id", "label", "policy_id", "source", "ledger", "consent", "components", "configs", "tools", "contracts", "evidence"),
    ),
}

_FINDING_SUBJECT_SCHEMA = _schema_object({"kind": {"type": "string", "enum": list(FINDING_SUBJECT_KINDS)}, "id": _OPAQUE_SCHEMA}, ("kind", "id"))
_DIAGNOSTIC_FINDING_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://local-ai-hub.invalid/schemas/diagnostic-finding.v1.json",
    "title": "Local AI Hub diagnostic-finding.v1",
    **_schema_object(
        {
            "schema_version": {"const": DIAGNOSTIC_FINDING_SCHEMA_VERSION},
            "id": _OPAQUE_SCHEMA,
            "rule_id": {"type": "string", "enum": list(FINDING_RULE_IDS)},
            "severity": {"type": "string", "enum": list(FINDING_SEVERITIES)},
            "subject": _FINDING_SUBJECT_SCHEMA,
            "availability": {"type": "string", "enum": list(FINDING_AVAILABILITY)},
            "reason_code": {"type": "string", "enum": list(FINDING_RULE_IDS)},
            "reason": _SAFE_TEXT_SCHEMA,
            "action_code": {"type": "string", "enum": list(ACTION_CODES)},
            "action": _SAFE_TEXT_SCHEMA,
            "evidence_count": {"type": "integer", "minimum": 0, "maximum": MAX_EVIDENCE_COUNT},
            "evidence_digest": _DIGEST_SCHEMA,
            "execution": {"const": "not_run"},
        },
        ("schema_version", "id", "rule_id", "severity", "subject", "availability", "reason_code", "reason", "action_code", "action", "evidence_count", "evidence_digest", "execution"),
    ),
}

_BUNDLE_FINDING_SCHEMA = _schema_object({"id": _OPAQUE_SCHEMA, "severity": {"type": "string", "enum": list(FINDING_SEVERITIES)}, "availability": {"type": "string", "enum": list(FINDING_AVAILABILITY)}, "digest": _DIGEST_SCHEMA}, ("id", "severity", "availability", "digest"))
_BUNDLE_STATUS_VALUES = tuple(dict.fromkeys(COMPONENT_STATUSES + CONFIG_STATUSES + CONTRACT_STATUSES))
_BUNDLE_STATUS_SCHEMA = _schema_object({"id": _OPAQUE_SCHEMA, "status": {"type": "string", "enum": list(_BUNDLE_STATUS_VALUES)}, "digest": _DIGEST_SCHEMA}, ("id", "status", "digest"))
_BUNDLE_SUMMARY_SCHEMA = _schema_object({"findings": {"type": "integer", "minimum": 0, "maximum": MAX_BUNDLE_ENTRIES}, "components": {"type": "integer", "minimum": 0, "maximum": MAX_BUNDLE_ENTRIES}, "configs": {"type": "integer", "minimum": 0, "maximum": MAX_BUNDLE_ENTRIES}, "tools": {"type": "integer", "minimum": 0, "maximum": MAX_BUNDLE_ENTRIES}, "contracts": {"type": "integer", "minimum": 0, "maximum": MAX_BUNDLE_ENTRIES}}, ("findings", "components", "configs", "tools", "contracts"))
_SUPPORT_BUNDLE_MANIFEST_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://local-ai-hub.invalid/schemas/support-bundle-manifest.v1.json",
    "title": "Local AI Hub support-bundle-manifest.v1",
    **_schema_object(
        {
            "schema_version": {"const": SUPPORT_BUNDLE_MANIFEST_SCHEMA_VERSION},
            "id": _OPAQUE_SCHEMA,
            "label": _SAFE_TEXT_SCHEMA,
            "policy_id": _OPAQUE_SCHEMA,
            "snapshot_id": _OPAQUE_SCHEMA,
            "source": _SERVER_SOURCE_SCHEMA,
            "ledger": _LEDGER_SCHEMA,
            "consent": _CONSENT_SCHEMA,
            "retention": _RETENTION_SCHEMA,
            "findings": {"type": "array", "maxItems": MAX_BUNDLE_ENTRIES, "items": _BUNDLE_FINDING_SCHEMA},
            "components": {"type": "array", "maxItems": MAX_BUNDLE_ENTRIES, "items": _BUNDLE_STATUS_SCHEMA},
            "configs": {"type": "array", "maxItems": MAX_BUNDLE_ENTRIES, "items": _BUNDLE_STATUS_SCHEMA},
            "tools": {"type": "array", "maxItems": MAX_BUNDLE_ENTRIES, "items": _BUNDLE_STATUS_SCHEMA},
            "contracts": {"type": "array", "maxItems": MAX_BUNDLE_ENTRIES, "items": _BUNDLE_STATUS_SCHEMA},
            "summary": _BUNDLE_SUMMARY_SCHEMA,
            "execution": {"const": "not_run"},
        },
        ("schema_version", "id", "label", "policy_id", "snapshot_id", "source", "ledger", "consent", "retention", "findings", "components", "configs", "tools", "contracts", "summary", "execution"),
    ),
}

_REMEDIATION_ACTION_SCHEMA = _schema_object(
    {
        "id": _OPAQUE_SCHEMA,
        "finding_id": _OPAQUE_SCHEMA,
        "action_code": {"type": "string", "enum": list(ACTION_CODES)},
        "risk": {"type": "string", "enum": list(REMEDIATION_RISKS)},
        "owner": {"type": "string", "enum": list(COMPONENT_IDS)},
        "prerequisites": {"type": "array", "maxItems": 8, "uniqueItems": True, "items": {"type": "string", "enum": list(PREREQUISITE_CODES)}},
        "rollback_code": {"type": "string", "enum": list(ROLLBACK_CODES)},
        "rollback_notes": _SAFE_TEXT_SCHEMA,
        "status": {"type": "string", "enum": list(REMEDIATION_STATUSES)},
    },
    ("id", "finding_id", "action_code", "risk", "owner", "prerequisites", "rollback_code", "rollback_notes", "status"),
)
_REMEDIATION_PLAN_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://local-ai-hub.invalid/schemas/remediation-plan.v1.json",
    "title": "Local AI Hub remediation-plan.v1",
    **_schema_object(
        {
            "schema_version": {"const": REMEDIATION_PLAN_SCHEMA_VERSION},
            "id": _OPAQUE_SCHEMA,
            "label": _SAFE_TEXT_SCHEMA,
            "policy_id": _OPAQUE_SCHEMA,
            "snapshot_id": _OPAQUE_SCHEMA,
            "source": _SERVER_SOURCE_SCHEMA,
            "ledger": _LEDGER_SCHEMA,
            "consent": _CONSENT_SCHEMA,
            "retention": _RETENTION_SCHEMA,
            "dry_run": {"const": True},
            "status": {"type": "string", "enum": list(REMEDIATION_STATUSES)},
            "actions": {"type": "array", "maxItems": MAX_REMEDIATION_ACTIONS, "items": _REMEDIATION_ACTION_SCHEMA},
            "execution": {"const": "not_run"},
        },
        ("schema_version", "id", "label", "policy_id", "snapshot_id", "source", "ledger", "consent", "retention", "dry_run", "status", "actions", "execution"),
    ),
}


def privacy_policy_schema() -> dict[str, Any]:
    return copy.deepcopy(_PRIVACY_POLICY_SCHEMA)


def diagnostic_snapshot_schema() -> dict[str, Any]:
    return copy.deepcopy(_DIAGNOSTIC_SNAPSHOT_SCHEMA)


def support_bundle_manifest_schema() -> dict[str, Any]:
    return copy.deepcopy(_SUPPORT_BUNDLE_MANIFEST_SCHEMA)


def diagnostic_finding_schema() -> dict[str, Any]:
    return copy.deepcopy(_DIAGNOSTIC_FINDING_SCHEMA)


def remediation_plan_schema() -> dict[str, Any]:
    return copy.deepcopy(_REMEDIATION_PLAN_SCHEMA)


def diagnostic_schemas() -> tuple[dict[str, Any], ...]:
    """Return every published Milestone 7C Draft 2020-12 schema."""

    return (
        privacy_policy_schema(),
        diagnostic_snapshot_schema(),
        diagnostic_finding_schema(),
        support_bundle_manifest_schema(),
        remediation_plan_schema(),
    )


def _issue(code: str, message: str, location: str) -> dict[str, str]:
    return {"code": code, "message": message, "location": location}


def _append(errors: list[dict[str, str]], code: str, message: str, location: str) -> None:
    errors.append(_issue(code, message, location))


def _json_bytes(value: object) -> bytes | None:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        return None


def _within_depth(value: object, errors: list[dict[str, str]], location: str) -> bool:
    pending: list[tuple[object, int]] = [(value, 1)]
    while pending:
        current, depth = pending.pop()
        if depth > MAX_JSON_DEPTH:
            _append(errors, "json_depth", "Descriptor nesting exceeds the static safety bound.", location)
            return False
        if isinstance(current, dict):
            pending.extend((child, depth + 1) for child in current.values())
        elif isinstance(current, list):
            pending.extend((child, depth + 1) for child in current)
    return True


def _strict_keys(value: object, allowed: set[str], errors: list[dict[str, str]], location: str) -> bool:
    if not isinstance(value, dict):
        _append(errors, "object_required", "Value must be a JSON object.", location)
        return False
    for key in value:
        if not isinstance(key, str) or key not in allowed:
            _append(errors, "unknown_field", "Unknown fields are not permitted in this contract.", location)
    return True


def _require(value: dict[str, Any], fields: Iterable[str], errors: list[dict[str, str]], location: str) -> None:
    for field in fields:
        if field not in value:
            _append(errors, "required_field", f"Required field '{field}' is missing.", f"{location}.{field}")


def _safe_text(value: object, errors: list[dict[str, str]], location: str, *, maximum: int = MAX_TEXT_LENGTH) -> bool:
    if not isinstance(value, str):
        _append(errors, "string_required", "Value must be a string.", location)
        return False
    if not 1 <= len(value) <= maximum:
        _append(errors, "text_length", "Text is outside the allowed bound.", location)
    if any(ord(char) < 32 for char in value):
        _append(errors, "control_character", "Control characters are not permitted.", location)
    if _WINDOWS_DRIVE_RE.search(value) or _WINDOWS_ROOT_RE.search(value) or _UNC_RE.search(value) or _ABSOLUTE_RE.search(value) or _PARENT_RE.search(value) or _RELATIVE_PATH_RE.fullmatch(value):
        _append(errors, "raw_path", "Filesystem paths are not permitted in diagnostics metadata.", location)
    if _URI_RE.search(value):
        _append(errors, "unsafe_uri", "URLs and filesystem/data URIs are not permitted.", location)
    if _SECRET_RE.search(value) or _ASSIGNMENT_RE.search(value) or _JWT_RE.search(value):
        _append(errors, "secret_detected", "Secrets and credential-like values are not permitted.", location)
    if _COMMAND_RE.search(value):
        _append(errors, "command_detected", "Commands and shell syntax are not permitted.", location)
    if _PAYLOAD_RE.search(value) or _LONG_BLOB_RE.fullmatch(value):
        _append(errors, "embedded_payload", "Payloads, archives, and model references are not permitted.", location)
    return True


def _scan_forbidden_content(value: object, errors: list[dict[str, str]], location: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z]", "", key.casefold()) if isinstance(key, str) else ""
            if normalized in _FORBIDDEN_KEY_TOKENS:
                _append(errors, "forbidden_field", "Paths, commands, secrets, logs, attachments, and payloads are not permitted.", location)
            _scan_forbidden_content(child, errors, f"{location}.{key}" if isinstance(key, str) else location)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_forbidden_content(child, errors, f"{location}[{index}]")
    elif isinstance(value, str):
        _safe_text(value, errors, location)


def _opaque(value: object, errors: list[dict[str, str]], location: str) -> bool:
    if not _safe_text(value, errors, location, maximum=120):
        return False
    if isinstance(value, str) and not OPAQUE_ID_RE.fullmatch(value):
        _append(errors, "opaque_id", "Value is not a bounded opaque identifier.", location)
        return False
    return isinstance(value, str)


def _digest(value: object, errors: list[dict[str, str]], location: str) -> bool:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        _append(errors, "digest", "Value must be a lowercase SHA-256 metadata digest.", location)
        return False
    return True


def _version(value: object, errors: list[dict[str, str]], location: str) -> bool:
    if not isinstance(value, str) or not VERSION_RE.fullmatch(value):
        _append(errors, "version", "Value must be a bounded contract version token.", location)
        return False
    return True


def _bounded_int(value: object, errors: list[dict[str, str]], location: str, maximum: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        _append(errors, "integer_bound", "Value must be a bounded non-negative integer.", location)
        return None
    return value


def _allowlisted_array(value: object, allowed: tuple[str, ...], errors: list[dict[str, str]], location: str, *, minimum: int = 0, maximum: int = MAX_POLICY_LIST) -> list[str]:
    if not isinstance(value, list):
        _append(errors, "array_required", "Value must be a bounded array.", location)
        return []
    if not minimum <= len(value) <= maximum:
        _append(errors, "array_bound", "Array is outside the static bound.", location)
    seen: set[str] = set()
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or item not in allowed:
            _append(errors, "allowlist", "Value is not part of the fixed allowlist.", f"{location}[{index}]")
            continue
        if item in seen:
            _append(errors, "duplicate_id", "Allowlist entries must be unique.", f"{location}[{index}]")
        seen.add(item)
        result.append(item)
    return result


def _validate_source(value: object, errors: list[dict[str, str]], location: str, kind: str) -> None:
    fields = ("kind", "reference")
    if not _strict_keys(value, set(fields), errors, location):
        return
    assert isinstance(value, dict)
    _require(value, fields, errors, location)
    if value.get("kind") != kind:
        _append(errors, "source_kind", f"Source kind must be {kind}.", f"{location}.kind")
    _opaque(value.get("reference"), errors, f"{location}.reference")


def _validate_ledger(value: object, errors: list[dict[str, str]], location: str) -> None:
    fields = ("consent_ref", "retention_ref", "revision")
    if not _strict_keys(value, set(fields), errors, location):
        return
    assert isinstance(value, dict)
    _require(value, fields, errors, location)
    _opaque(value.get("consent_ref"), errors, f"{location}.consent_ref")
    _opaque(value.get("retention_ref"), errors, f"{location}.retention_ref")
    _bounded_int(value.get("revision"), errors, f"{location}.revision", MAX_REVISION)


def _validate_consent(value: object, errors: list[dict[str, str]], location: str) -> None:
    fields = ("state", "scope", "ledger_ref", "revision")
    if not _strict_keys(value, set(fields), errors, location):
        return
    assert isinstance(value, dict)
    _require(value, fields, errors, location)
    if value.get("state") not in CONSENT_STATES:
        _append(errors, "consent_state", "Consent state is not part of the fixed allowlist.", f"{location}.state")
    if value.get("scope") not in CONSENT_SCOPES:
        _append(errors, "consent_scope", "Consent scope is not part of the fixed allowlist.", f"{location}.scope")
    _opaque(value.get("ledger_ref"), errors, f"{location}.ledger_ref")
    _bounded_int(value.get("revision"), errors, f"{location}.revision", MAX_REVISION)


def _validate_retention(value: object, errors: list[dict[str, str]], location: str) -> None:
    fields = ("classification", "days", "ledger_ref")
    if not _strict_keys(value, set(fields), errors, location):
        return
    assert isinstance(value, dict)
    _require(value, fields, errors, location)
    if value.get("classification") not in RETENTION_CLASSES:
        _append(errors, "retention_class", "Retention class is not part of the fixed allowlist.", f"{location}.classification")
    _bounded_int(value.get("days"), errors, f"{location}.days", 3650)
    _opaque(value.get("ledger_ref"), errors, f"{location}.ledger_ref")


def _validate_ledger_links(value: dict[str, Any], errors: list[dict[str, str]], location: str, *, retention_required: bool) -> None:
    ledger = value.get("ledger")
    consent = value.get("consent")
    if isinstance(ledger, dict) and isinstance(consent, dict) and ledger.get("consent_ref") != consent.get("ledger_ref"):
        _append(errors, "ledger_link", "Consent metadata must reference the declared consent ledger.", f"{location}.consent.ledger_ref")
    if retention_required:
        retention = value.get("retention")
        if isinstance(ledger, dict) and isinstance(retention, dict) and ledger.get("retention_ref") != retention.get("ledger_ref"):
            _append(errors, "ledger_link", "Retention metadata must reference the declared retention ledger.", f"{location}.retention.ledger_ref")


def _base_validation(value: object, key: str, location: str) -> tuple[list[dict[str, str]], dict[str, Any] | None]:
    errors: list[dict[str, str]] = []
    encoded = _json_bytes(value)
    if encoded is None or len(encoded) > MAX_DESCRIPTOR_BYTES:
        return [{"code": "descriptor_size", "message": "Descriptor must be bounded JSON.", "location": location}], None
    if not _within_depth(value, errors, location):
        return errors, None
    if not isinstance(value, dict):
        return [{"code": "object_required", "message": "Descriptor must be a JSON object.", "location": location}], None
    _scan_forbidden_content(value, errors, location)
    return errors, value


def _finish(value: dict[str, Any], key: str, errors: list[dict[str, str]], canonicalizer: Any) -> dict[str, Any]:
    if errors:
        return {"valid": False, "errors": errors, key: None}
    normalized = copy.deepcopy(value)
    canonical = canonicalizer(normalized)
    return {"valid": True, "errors": [], key: normalized, "fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest(), "execution": "not_run"}


def validate_privacy_policy(value: object) -> dict[str, Any]:
    errors, candidate = _base_validation(value, "policy", "policy")
    if candidate is None:
        return {"valid": False, "errors": errors, "policy": None}
    fields = ("schema_version", "id", "label", "revision", "source", "ledger", "consent", "retention", "redaction_profile", "components", "config_keys", "tool_ids", "contract_ids")
    _strict_keys(candidate, set(fields), errors, "policy")
    _require(candidate, fields, errors, "policy")
    if candidate.get("schema_version") != PRIVACY_POLICY_SCHEMA_VERSION:
        _append(errors, "schema_version", "Policy must declare privacy-policy.v1.", "policy.schema_version")
    _opaque(candidate.get("id"), errors, "policy.id")
    _safe_text(candidate.get("label"), errors, "policy.label")
    _bounded_int(candidate.get("revision"), errors, "policy.revision", MAX_REVISION)
    _validate_source(candidate.get("source"), errors, "policy.source", "managed")
    _validate_ledger(candidate.get("ledger"), errors, "policy.ledger")
    _validate_consent(candidate.get("consent"), errors, "policy.consent")
    _validate_retention(candidate.get("retention"), errors, "policy.retention")
    _validate_ledger_links(candidate, errors, "policy", retention_required=True)
    if candidate.get("redaction_profile") not in REDACTION_PROFILES:
        _append(errors, "redaction_profile", "Redaction profile is not part of the fixed allowlist.", "policy.redaction_profile")
    _allowlisted_array(candidate.get("components"), COMPONENT_IDS, errors, "policy.components", minimum=1)
    _allowlisted_array(candidate.get("config_keys"), CONFIG_KEYS, errors, "policy.config_keys", minimum=1)
    _allowlisted_array(candidate.get("tool_ids"), TOOL_IDS, errors, "policy.tool_ids", minimum=1)
    _allowlisted_array(candidate.get("contract_ids"), CONTRACT_IDS, errors, "policy.contract_ids", minimum=1)
    return _finish(candidate, "policy", errors, canonical_privacy_policy_json)


def _validate_record_array(value: object, errors: list[dict[str, str]], location: str, allowed: tuple[str, ...], statuses: tuple[str, ...], fields: tuple[str, ...], *, digest_fields: tuple[str, ...] = ("metadata_digest",)) -> None:
    if not isinstance(value, list):
        _append(errors, "array_required", "Snapshot records must be arrays.", location)
        return
    if len(value) > MAX_SNAPSHOT_RECORDS:
        _append(errors, "record_limit", "Snapshot record count exceeds the static bound.", location)
    seen: set[str] = set()
    for index, item in enumerate(value):
        item_location = f"{location}[{index}]"
        if not _strict_keys(item, set(fields), errors, item_location):
            continue
        assert isinstance(item, dict)
        _require(item, fields, errors, item_location)
        item_id = item.get("id")
        if not isinstance(item_id, str) or item_id not in allowed:
            _append(errors, "allowlist", "Snapshot record ID is not part of the fixed allowlist.", f"{item_location}.id")
        if isinstance(item_id, str) and item_id in seen:
            _append(errors, "duplicate_id", "Snapshot record IDs must be unique.", f"{item_location}.id")
        if isinstance(item_id, str):
            seen.add(item_id)
        if item.get("status") not in statuses:
            _append(errors, "status", "Snapshot status is not part of the fixed allowlist.", f"{item_location}.status")
        _bounded_int(item.get("evidence_count"), errors, f"{item_location}.evidence_count", MAX_EVIDENCE_COUNT)
        for digest_field in digest_fields:
            _digest(item.get(digest_field), errors, f"{item_location}.{digest_field}")
        for version_field in ("expected_version", "observed_version"):
            if version_field in item:
                _version(item.get(version_field), errors, f"{item_location}.{version_field}")


def validate_diagnostic_snapshot(value: object) -> dict[str, Any]:
    errors, candidate = _base_validation(value, "snapshot", "snapshot")
    if candidate is None:
        return {"valid": False, "errors": errors, "snapshot": None}
    fields = ("schema_version", "id", "label", "policy_id", "source", "ledger", "consent", "components", "configs", "tools", "contracts", "evidence")
    _strict_keys(candidate, set(fields), errors, "snapshot")
    _require(candidate, fields, errors, "snapshot")
    if candidate.get("schema_version") != DIAGNOSTIC_SNAPSHOT_SCHEMA_VERSION:
        _append(errors, "schema_version", "Snapshot must declare diagnostic-snapshot.v1.", "snapshot.schema_version")
    _opaque(candidate.get("id"), errors, "snapshot.id")
    _safe_text(candidate.get("label"), errors, "snapshot.label")
    _opaque(candidate.get("policy_id"), errors, "snapshot.policy_id")
    _validate_source(candidate.get("source"), errors, "snapshot.source", "server-owned")
    _validate_ledger(candidate.get("ledger"), errors, "snapshot.ledger")
    _validate_consent(candidate.get("consent"), errors, "snapshot.consent")
    _validate_ledger_links(candidate, errors, "snapshot", retention_required=False)
    _validate_record_array(candidate.get("components"), errors, "snapshot.components", COMPONENT_IDS, COMPONENT_STATUSES, ("id", "status", "evidence_count", "metadata_digest"))
    _validate_record_array(candidate.get("configs"), errors, "snapshot.configs", CONFIG_KEYS, CONFIG_STATUSES, ("id", "status", "expected_digest", "observed_digest", "evidence_count"), digest_fields=("expected_digest", "observed_digest"))
    configs = candidate.get("configs")
    if isinstance(configs, list):
        for index, record in enumerate(configs):
            if not isinstance(record, dict):
                continue
            if record.get("status") == "in_sync" and record.get("expected_digest") != record.get("observed_digest"):
                _append(errors, "config_digest_mismatch", "An in_sync configuration must have matching declared digests.", f"snapshot.configs[{index}]")
            if record.get("status") == "drifted" and record.get("expected_digest") == record.get("observed_digest"):
                _append(errors, "config_status_mismatch", "A drifted configuration must have differing declared digests.", f"snapshot.configs[{index}]")
    _validate_record_array(candidate.get("tools"), errors, "snapshot.tools", TOOL_IDS, TOOL_STATUSES, ("id", "status", "evidence_count", "metadata_digest"))
    _validate_record_array(candidate.get("contracts"), errors, "snapshot.contracts", CONTRACT_IDS, CONTRACT_STATUSES, ("id", "expected_version", "observed_version", "status", "evidence_count", "metadata_digest"), digest_fields=("metadata_digest",))
    evidence = candidate.get("evidence")
    evidence_fields = ("status", "components", "configs", "tools", "contracts", "digest")
    if _strict_keys(evidence, set(evidence_fields), errors, "snapshot.evidence"):
        assert isinstance(evidence, dict)
        _require(evidence, evidence_fields, errors, "snapshot.evidence")
        if evidence.get("status") not in EVIDENCE_STATUSES:
            _append(errors, "evidence_status", "Evidence status is not part of the fixed allowlist.", "snapshot.evidence.status")
        for count_key in ("components", "configs", "tools", "contracts"):
            _bounded_int(evidence.get(count_key), errors, f"snapshot.evidence.{count_key}", MAX_SNAPSHOT_RECORDS)
        _digest(evidence.get("digest"), errors, "snapshot.evidence.digest")
    return _finish(candidate, "snapshot", errors, canonical_diagnostic_snapshot_json)


def validate_diagnostic_finding(value: object) -> dict[str, Any]:
    errors, candidate = _base_validation(value, "finding", "finding")
    if candidate is None:
        return {"valid": False, "errors": errors, "finding": None}
    fields = ("schema_version", "id", "rule_id", "severity", "subject", "availability", "reason_code", "reason", "action_code", "action", "evidence_count", "evidence_digest", "execution")
    _strict_keys(candidate, set(fields), errors, "finding")
    _require(candidate, fields, errors, "finding")
    if candidate.get("schema_version") != DIAGNOSTIC_FINDING_SCHEMA_VERSION:
        _append(errors, "schema_version", "Finding must declare diagnostic-finding.v1.", "finding.schema_version")
    _opaque(candidate.get("id"), errors, "finding.id")
    rule_id = candidate.get("rule_id")
    if rule_id not in FINDING_RULE_IDS:
        _append(errors, "rule_id", "Finding rule is not part of the fixed allowlist.", "finding.rule_id")
    if candidate.get("severity") not in FINDING_SEVERITIES:
        _append(errors, "severity", "Finding severity is not part of the fixed allowlist.", "finding.severity")
    subject = candidate.get("subject")
    subject_fields = ("kind", "id")
    if _strict_keys(subject, set(subject_fields), errors, "finding.subject"):
        assert isinstance(subject, dict)
        _require(subject, subject_fields, errors, "finding.subject")
        if subject.get("kind") not in FINDING_SUBJECT_KINDS:
            _append(errors, "subject_kind", "Finding subject kind is not part of the fixed allowlist.", "finding.subject.kind")
        _opaque(subject.get("id"), errors, "finding.subject.id")
    if candidate.get("availability") not in FINDING_AVAILABILITY:
        _append(errors, "availability", "Finding availability is not part of the fixed allowlist.", "finding.availability")
    reason_code = candidate.get("reason_code")
    if reason_code not in FINDING_RULE_IDS:
        _append(errors, "reason_code", "Finding reason code is not part of the fixed allowlist.", "finding.reason_code")
    _safe_text(candidate.get("reason"), errors, "finding.reason")
    action_code = candidate.get("action_code")
    if action_code not in ACTION_CODES:
        _append(errors, "action_code", "Finding action code is not part of the fixed allowlist.", "finding.action_code")
    _safe_text(candidate.get("action"), errors, "finding.action")
    _bounded_int(candidate.get("evidence_count"), errors, "finding.evidence_count", MAX_EVIDENCE_COUNT)
    _digest(candidate.get("evidence_digest"), errors, "finding.evidence_digest")
    if candidate.get("execution") != "not_run":
        _append(errors, "execution", "Diagnostic findings are always not_run.", "finding.execution")
    if isinstance(rule_id, str) and reason_code != REASON_CODE_BY_RULE.get(rule_id):
        _append(errors, "reason_mismatch", "Finding reason code must match its rule.", "finding.reason_code")
    if isinstance(reason_code, str) and isinstance(candidate.get("reason"), str) and candidate["reason"] != REASON_TEXT.get(reason_code):
        _append(errors, "reason_mismatch", "Finding reason text must be the fixed rule text.", "finding.reason")
    if isinstance(action_code, str) and isinstance(candidate.get("action"), str) and candidate["action"] != ACTION_TEXT.get(action_code):
        _append(errors, "action_mismatch", "Finding action text must be the fixed action text.", "finding.action")
    return _finish(candidate, "finding", errors, canonical_diagnostic_finding_json)


def _validate_bundle_status_array(value: object, errors: list[dict[str, str]], location: str) -> None:
    if not isinstance(value, list):
        _append(errors, "array_required", "Bundle entries must be arrays.", location)
        return
    if len(value) > MAX_BUNDLE_ENTRIES:
        _append(errors, "entry_limit", "Bundle entry count exceeds the static bound.", location)
    seen: set[str] = set()
    for index, item in enumerate(value):
        item_location = f"{location}[{index}]"
        fields = ("id", "status", "digest")
        if not _strict_keys(item, set(fields), errors, item_location):
            continue
        assert isinstance(item, dict)
        _require(item, fields, errors, item_location)
        _opaque(item.get("id"), errors, f"{item_location}.id")
        if isinstance(item.get("id"), str) and item["id"] in seen:
            _append(errors, "duplicate_id", "Bundle entry IDs must be unique per category.", f"{item_location}.id")
        if isinstance(item.get("id"), str):
            seen.add(item["id"])
        if item.get("status") not in _BUNDLE_STATUS_VALUES:
            _append(errors, "status", "Bundle entry status is not part of the fixed allowlist.", f"{item_location}.status")
        _digest(item.get("digest"), errors, f"{item_location}.digest")


def validate_support_bundle_manifest(value: object) -> dict[str, Any]:
    errors, candidate = _base_validation(value, "manifest", "manifest")
    if candidate is None:
        return {"valid": False, "errors": errors, "manifest": None}
    fields = ("schema_version", "id", "label", "policy_id", "snapshot_id", "source", "ledger", "consent", "retention", "findings", "components", "configs", "tools", "contracts", "summary", "execution")
    _strict_keys(candidate, set(fields), errors, "manifest")
    _require(candidate, fields, errors, "manifest")
    if candidate.get("schema_version") != SUPPORT_BUNDLE_MANIFEST_SCHEMA_VERSION:
        _append(errors, "schema_version", "Manifest must declare support-bundle-manifest.v1.", "manifest.schema_version")
    _opaque(candidate.get("id"), errors, "manifest.id")
    _safe_text(candidate.get("label"), errors, "manifest.label")
    _opaque(candidate.get("policy_id"), errors, "manifest.policy_id")
    _opaque(candidate.get("snapshot_id"), errors, "manifest.snapshot_id")
    _validate_source(candidate.get("source"), errors, "manifest.source", "server-owned")
    _validate_ledger(candidate.get("ledger"), errors, "manifest.ledger")
    _validate_consent(candidate.get("consent"), errors, "manifest.consent")
    _validate_retention(candidate.get("retention"), errors, "manifest.retention")
    _validate_ledger_links(candidate, errors, "manifest", retention_required=True)
    findings = candidate.get("findings")
    if not isinstance(findings, list):
        _append(errors, "array_required", "Manifest findings must be an array.", "manifest.findings")
        findings = []
    if len(findings) > MAX_BUNDLE_ENTRIES:
        _append(errors, "entry_limit", "Manifest finding count exceeds the static bound.", "manifest.findings")
    seen_findings: set[str] = set()
    for index, item in enumerate(findings):
        location = f"manifest.findings[{index}]"
        fields_finding = ("id", "severity", "availability", "digest")
        if not _strict_keys(item, set(fields_finding), errors, location):
            continue
        assert isinstance(item, dict)
        _require(item, fields_finding, errors, location)
        _opaque(item.get("id"), errors, f"{location}.id")
        if isinstance(item.get("id"), str) and item["id"] in seen_findings:
            _append(errors, "duplicate_id", "Manifest finding IDs must be unique.", f"{location}.id")
        if isinstance(item.get("id"), str):
            seen_findings.add(item["id"])
        if item.get("severity") not in FINDING_SEVERITIES:
            _append(errors, "severity", "Manifest finding severity is not part of the fixed allowlist.", f"{location}.severity")
        if item.get("availability") not in FINDING_AVAILABILITY:
            _append(errors, "availability", "Manifest finding availability is not part of the fixed allowlist.", f"{location}.availability")
        _digest(item.get("digest"), errors, f"{location}.digest")
    for key in ("components", "configs", "tools", "contracts"):
        _validate_bundle_status_array(candidate.get(key), errors, f"manifest.{key}")
    summary = candidate.get("summary")
    summary_fields = ("findings", "components", "configs", "tools", "contracts")
    if _strict_keys(summary, set(summary_fields), errors, "manifest.summary"):
        assert isinstance(summary, dict)
        _require(summary, summary_fields, errors, "manifest.summary")
        for key in summary_fields:
            _bounded_int(summary.get(key), errors, f"manifest.summary.{key}", MAX_BUNDLE_ENTRIES)
    if candidate.get("execution") != "not_run":
        _append(errors, "execution", "Support bundle projection is always not_run.", "manifest.execution")
    return _finish(candidate, "manifest", errors, canonical_support_bundle_manifest_json)


def validate_remediation_plan(value: object) -> dict[str, Any]:
    errors, candidate = _base_validation(value, "plan", "plan")
    if candidate is None:
        return {"valid": False, "errors": errors, "plan": None}
    fields = ("schema_version", "id", "label", "policy_id", "snapshot_id", "source", "ledger", "consent", "retention", "dry_run", "status", "actions", "execution")
    _strict_keys(candidate, set(fields), errors, "plan")
    _require(candidate, fields, errors, "plan")
    if candidate.get("schema_version") != REMEDIATION_PLAN_SCHEMA_VERSION:
        _append(errors, "schema_version", "Plan must declare remediation-plan.v1.", "plan.schema_version")
    _opaque(candidate.get("id"), errors, "plan.id")
    _safe_text(candidate.get("label"), errors, "plan.label")
    _opaque(candidate.get("policy_id"), errors, "plan.policy_id")
    _opaque(candidate.get("snapshot_id"), errors, "plan.snapshot_id")
    _validate_source(candidate.get("source"), errors, "plan.source", "server-owned")
    _validate_ledger(candidate.get("ledger"), errors, "plan.ledger")
    _validate_consent(candidate.get("consent"), errors, "plan.consent")
    _validate_retention(candidate.get("retention"), errors, "plan.retention")
    _validate_ledger_links(candidate, errors, "plan", retention_required=True)
    if candidate.get("dry_run") is not True:
        _append(errors, "dry_run", "Remediation plans must be dry_run true.", "plan.dry_run")
    if candidate.get("status") not in REMEDIATION_STATUSES:
        _append(errors, "plan_status", "Plan status is not part of the fixed allowlist.", "plan.status")
    actions = candidate.get("actions")
    if not isinstance(actions, list):
        _append(errors, "array_required", "Plan actions must be an array.", "plan.actions")
        actions = []
    if len(actions) > MAX_REMEDIATION_ACTIONS:
        _append(errors, "action_limit", "Plan action count exceeds the static bound.", "plan.actions")
    seen: set[str] = set()
    for index, action in enumerate(actions):
        location = f"plan.actions[{index}]"
        action_fields = ("id", "finding_id", "action_code", "risk", "owner", "prerequisites", "rollback_code", "rollback_notes", "status")
        if not _strict_keys(action, set(action_fields), errors, location):
            continue
        assert isinstance(action, dict)
        _require(action, action_fields, errors, location)
        _opaque(action.get("id"), errors, f"{location}.id")
        if isinstance(action.get("id"), str) and action["id"] in seen:
            _append(errors, "duplicate_id", "Plan action IDs must be unique.", f"{location}.id")
        if isinstance(action.get("id"), str):
            seen.add(action["id"])
        _opaque(action.get("finding_id"), errors, f"{location}.finding_id")
        if action.get("action_code") not in ACTION_CODES:
            _append(errors, "action_code", "Plan action is not part of the fixed allowlist.", f"{location}.action_code")
        if action.get("risk") not in REMEDIATION_RISKS:
            _append(errors, "risk", "Plan risk is not part of the fixed allowlist.", f"{location}.risk")
        if action.get("owner") not in COMPONENT_IDS:
            _append(errors, "owner", "Plan owner is not part of the fixed component allowlist.", f"{location}.owner")
        _allowlisted_array(action.get("prerequisites"), PREREQUISITE_CODES, errors, f"{location}.prerequisites", maximum=8)
        if action.get("rollback_code") not in ROLLBACK_CODES:
            _append(errors, "rollback_code", "Rollback code is not part of the fixed allowlist.", f"{location}.rollback_code")
        _safe_text(action.get("rollback_notes"), errors, f"{location}.rollback_notes")
        if action.get("status") not in REMEDIATION_STATUSES:
            _append(errors, "action_status", "Plan action status is not part of the fixed allowlist.", f"{location}.status")
    if candidate.get("execution") != "not_run":
        _append(errors, "execution", "Remediation plans are always not_run.", "plan.execution")
    return _finish(candidate, "plan", errors, canonical_remediation_plan_json)


def _sort_records(value: list[dict[str, Any]], key: str = "id") -> list[dict[str, Any]]:
    return [copy.deepcopy(item) for item in sorted(value, key=lambda item: str(item[key]))]


def canonical_privacy_policy_json(value: dict[str, Any]) -> str:
    normalized = copy.deepcopy(value)
    for key in ("components", "config_keys", "tool_ids", "contract_ids"):
        normalized[key] = sorted(normalized[key])
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def canonical_diagnostic_snapshot_json(value: dict[str, Any]) -> str:
    normalized = copy.deepcopy(value)
    for key in ("components", "configs", "tools", "contracts"):
        normalized[key] = _sort_records(normalized[key])
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def canonical_diagnostic_finding_json(value: dict[str, Any]) -> str:
    return json.dumps(copy.deepcopy(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def canonical_support_bundle_manifest_json(value: dict[str, Any]) -> str:
    normalized = copy.deepcopy(value)
    normalized["findings"] = _sort_records(normalized["findings"])
    for key in ("components", "configs", "tools", "contracts"):
        normalized[key] = _sort_records(normalized[key])
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def canonical_remediation_plan_json(value: dict[str, Any]) -> str:
    normalized = copy.deepcopy(value)
    normalized["actions"] = _sort_records(normalized["actions"])
    for action in normalized["actions"]:
        action["prerequisites"] = sorted(action["prerequisites"])
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
