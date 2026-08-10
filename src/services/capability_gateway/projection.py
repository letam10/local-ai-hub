"""Allowlisted projections for server-owned static section results."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any

from src.shared.schemas.capability_gateway import (
    EXECUTION_NOT_RUN,
    SECTION_SET,
    STATUS_ALLOWLIST,
    gateway_fingerprint,
)


_STATUS_RANK = {"operational": 0, "planned": 1, "partial": 2, "unavailable": 3}
_DEFAULT_REASON = "static_metadata_only"
_DEFAULT_ACTION = "review_runtime_evidence"
_REASON_TEXT = {
    "static_metadata_only": "Validated static metadata is available; runtime evidence is absent.",
    "static_summary_ready": "Validated static metadata is available without runtime execution.",
    "section_unavailable": "The requested static section is unavailable or ambiguous.",
    "server_result_rejected": "The server result failed the closed projection contract.",
    "selector_not_found": "No uniquely validated managed identity matched the selector.",
    "loader_unavailable": "No trusted server loader is available for the requested section.",
    "runtime_not_run": "Runtime execution was not requested or performed.",
}
_ACTION_TEXT = {
    "review_runtime_evidence": "Keep the result static and obtain separately authorized runtime evidence before execution claims.",
    "fix_managed_descriptor": "Correct the managed descriptor or identity and retry static validation.",
    "authorize_bounded_smoke": "Request a separately authorized bounded smoke before claiming runtime readiness.",
    "restore_managed_source": "Restore a unique validated managed source under the fixed server-owned root.",
}
_UNSAFE_TYPE_TOKENS = frozenset(
    {
        "path",
        "secret",
        "command",
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
        "url",
        "token",
        "credential",
    }
)


class CapabilityProjectionError(ValueError):
    """Raised by strict callers; public projections normally fail closed instead."""


def _safe_code(value: object, fallback: str) -> str:
    if isinstance(value, str) and value.isascii() and 1 <= len(value) <= 64 and value[0].islower() and all(char.islower() or char.isdigit() or char == "_" for char in value):
        return value
    return fallback


def _safe_status(value: object) -> str:
    return value if isinstance(value, str) and value in STATUS_ALLOWLIST else "unavailable"


def _safe_id(value: object) -> bool:
    if not isinstance(value, str) or not 1 <= len(value) <= 120:
        return False
    if not (value[0].islower() or value[0].isdigit()) or ".." in value:
        return False
    return all(char.islower() or char.isdigit() or char in "._@-" for char in value)


def _safe_version(value: object) -> bool:
    return value is None or _safe_id(value)


def _safe_count_map(value: object) -> dict[str, int] | None:
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > 32:
        return None
    result: dict[str, int] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key or len(key) > 64 or not all(char.islower() or char.isdigit() or char == "_" for char in key):
            return None
        if not isinstance(item, int) or isinstance(item, bool) or not 0 <= item <= 256:
            return None
        result[key] = item
    return {key: result[key] for key in sorted(result)}


def _safe_types(value: object) -> list[str] | None:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 32:
        return None
    if any(
        not isinstance(item, str)
        or not item
        or len(item) > 64
        or not all(char.islower() or char.isdigit() or char == "_" for char in item)
        or any(token in item.split("_") for token in _UNSAFE_TYPE_TOKENS)
        for item in value
    ):
        return None
    if len(value) != len(set(value)):
        return None
    return sorted(value)


def _record(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    allowed = {"id", "version", "status", "reason_code", "action_code", "type_summary", "counts"}
    if set(value) - allowed:
        return None
    identifier = value.get("id")
    version = value.get("version")
    if not _safe_id(identifier) or not _safe_version(version):
        return None
    types = _safe_types(value.get("type_summary"))
    counts = _safe_count_map(value.get("counts"))
    if types is None or counts is None:
        return None
    status = _safe_status(value.get("status", "partial"))
    return {
        "id": identifier,
        "version": version,
        "status": status,
        "reason_code": _safe_code(value.get("reason_code"), _DEFAULT_REASON),
        "action_code": _safe_code(value.get("action_code"), _DEFAULT_ACTION),
        "type_summary": types,
        "counts": counts,
    }


def _section_error(section: str, reason_code: str, action_code: str, *, dry_run: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "unavailable",
        "reason_code": reason_code,
        "action_code": action_code,
        "records": [],
        "counts": {},
        "type_summaries": [],
        "execution": EXECUTION_NOT_RUN,
        "dry_run": bool(dry_run),
    }
    result["fingerprint"] = gateway_fingerprint({"section": section, **result})
    return result


def project_section(section: str, value: object, *, selectors: tuple[str, ...] = (), include_plans: bool = False) -> dict[str, Any]:
    """Project a trusted loader result to the closed section DTO.

    The function intentionally accepts no filesystem path, report mapping or
    runtime handle.  Unknown loader fields cause an unavailable projection;
    the offending value is never copied into the returned object.
    """

    if section not in SECTION_SET:
        return _section_error("unknown", "section_unavailable", "fix_managed_descriptor", dry_run=include_plans)
    if isinstance(value, list):
        source: dict[str, Any] = {"records": value}
    elif isinstance(value, dict):
        source = value
    else:
        return _section_error(section, "server_result_rejected", "fix_managed_descriptor", dry_run=include_plans)
    allowed = {"status", "reason_code", "action_code", "records", "items", "entries", "counts", "type_summaries", "execution", "dry_run", "fingerprint"}
    if set(source) - allowed:
        return _section_error(section, "server_result_rejected", "fix_managed_descriptor", dry_run=include_plans)
    if source.get("execution", EXECUTION_NOT_RUN) != EXECUTION_NOT_RUN:
        return _section_error(section, "server_result_rejected", "authorize_bounded_smoke", dry_run=include_plans)
    if "dry_run" in source and not isinstance(source["dry_run"], bool):
        return _section_error(section, "server_result_rejected", "fix_managed_descriptor", dry_run=include_plans)
    raw_records = source.get("records", source.get("items", source.get("entries", [])))
    if not isinstance(raw_records, list) or len(raw_records) > 256:
        return _section_error(section, "server_result_rejected", "fix_managed_descriptor", dry_run=include_plans)
    records: list[dict[str, Any]] = []
    for item in raw_records:
        projected = _record(item)
        if projected is None:
            return _section_error(section, "server_result_rejected", "fix_managed_descriptor", dry_run=include_plans)
        if selectors and projected["id"] not in selectors:
            continue
        records.append(projected)
    identities = [(item["id"], item["version"]) for item in records]
    if len(identities) != len(set(identities)):
        return _section_error(section, "section_unavailable", "restore_managed_source", dry_run=include_plans)
    if selectors and not records:
        return _section_error(section, "selector_not_found", "restore_managed_source", dry_run=include_plans)
    counts = _safe_count_map(source.get("counts"))
    types = _safe_types(source.get("type_summaries"))
    if counts is None or types is None:
        return _section_error(section, "server_result_rejected", "fix_managed_descriptor", dry_run=include_plans)
    statuses = [item["status"] for item in records]
    status = _safe_status(source.get("status", "partial"))
    if statuses:
        status = max([status, *statuses], key=lambda item: _STATUS_RANK[item])
    dry_run = bool(include_plans or source.get("dry_run", False))
    reason_code = _safe_code(source.get("reason_code"), _DEFAULT_REASON)
    action_code = _safe_code(source.get("action_code"), _DEFAULT_ACTION)
    result: dict[str, Any] = {
        "status": status,
        "reason_code": reason_code,
        "action_code": action_code,
        "records": sorted(records, key=lambda item: (item["id"], item["version"] or "")),
        "counts": counts,
        "type_summaries": types,
        "execution": EXECUTION_NOT_RUN,
        "dry_run": dry_run,
    }
    result["fingerprint"] = gateway_fingerprint({"section": section, **result})
    return deepcopy(result)


def project_invalid_response(*, reason_code: str = "server_result_rejected", action_code: str = "fix_managed_descriptor") -> dict[str, Any]:
    """Return a fixed safe response fragment for a rejected loader result."""

    return _section_error("invalid", reason_code, action_code)


def reason_text(code: str) -> str:
    return _REASON_TEXT.get(code, _REASON_TEXT["static_metadata_only"])


def action_text(code: str) -> str:
    return _ACTION_TEXT.get(code, _ACTION_TEXT["review_runtime_evidence"])


__all__ = [
    "CapabilityProjectionError",
    "project_section",
    "project_invalid_response",
    "reason_text",
    "action_text",
]
