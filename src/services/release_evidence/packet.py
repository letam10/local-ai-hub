"""Pure static construction and parsing for release-evidence-packet.v1.

All inputs are bounded JSON-compatible values. This module has no Git,
subprocess, filesystem, network, provider, device, model, or media behavior.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from src.shared.schemas.release_evidence_packet import (
    MAX_PACKET_BYTES,
    RELEASE_EVIDENCE_PACKET_SCHEMA_VERSION,
    REJECTION_CODES,
    canonical_release_evidence_packet_json,
    is_plain_static_value,
    release_evidence_packet_fingerprint,
    validate_release_evidence_packet,
)


def _result_error(code: str) -> dict[str, Any]:
    return {
        "valid": False,
        "errors": [{"code": code, "location": "packet"}],
        "packet": None,
        "fingerprint": None,
        "execution": "not_run",
    }


def _default_verdict() -> dict[str, str]:
    return {
        "static": "pass",
        "operational": "not_run",
        "provenance": "server_owned_validated",
        "runtime_smoke": "not_run",
    }


def _default_admission() -> dict[str, Any]:
    return {"status": "pending_manager_qa", "rejection_codes": [], "manager_qa_id": None}


def _finalize(packet: dict[str, Any]) -> dict[str, Any]:
    packet["fingerprint"] = {
        "algorithm": "sha256",
        "value": "0" * 64,
        "projection": "canonical_redacted_v1",
    }
    packet["fingerprint"]["value"] = release_evidence_packet_fingerprint(packet)
    return validate_release_evidence_packet(packet)


def build_release_evidence_packet(
    identity: object,
    commands: object,
    verdict: object | None = None,
    *,
    admission: object | None = None,
    runtime_evidence: object | None = None,
) -> dict[str, Any]:
    """Build a detached packet result from typed/static JSON-compatible inputs."""

    values = (identity, commands, verdict, admission, runtime_evidence)
    if any(not is_plain_static_value(value) for value in values):
        return _result_error("unsafe_value")
    packet = {
        "contract": RELEASE_EVIDENCE_PACKET_SCHEMA_VERSION,
        "identity": copy.deepcopy(identity),
        "commands": copy.deepcopy(commands),
        "verdict": copy.deepcopy(verdict) if verdict is not None else _default_verdict(),
        "runtime_evidence": copy.deepcopy(runtime_evidence),
        "admission": copy.deepcopy(admission) if admission is not None else _default_admission(),
        "fingerprint": {
            "algorithm": "sha256",
            "value": "0" * 64,
            "projection": "canonical_redacted_v1",
        },
    }
    if type(packet["identity"]) is not dict or type(packet["commands"]) is not list:
        return _result_error("object_required")
    return _finalize(packet)


create_release_evidence_packet = build_release_evidence_packet
create_packet = build_release_evidence_packet


def validate_packet(value: object) -> dict[str, Any]:
    return validate_release_evidence_packet(value)


def admit_release_evidence_packet(value: object, manager_qa_id: object) -> dict[str, Any]:
    """Return a detached manager-admitted packet without performing QA itself."""

    validation = validate_release_evidence_packet(value)
    if not validation.get("valid"):
        return validation
    if not is_plain_static_value(manager_qa_id):
        return _result_error("unsafe_value")
    candidate = copy.deepcopy(validation["packet"])
    assert type(candidate) is dict
    candidate["admission"] = {
        "status": "admitted",
        "rejection_codes": [],
        "manager_qa_id": copy.deepcopy(manager_qa_id),
    }
    return _finalize(candidate)


def reject_release_evidence_packet(value: object, rejection_code: object) -> dict[str, Any]:
    """Return a detached rejected packet using one fixed safe rejection code."""

    validation = validate_release_evidence_packet(value)
    if not validation.get("valid"):
        return validation
    if type(rejection_code) is not str or rejection_code not in REJECTION_CODES:
        return _result_error("invalid_enum")
    candidate = copy.deepcopy(validation["packet"])
    assert type(candidate) is dict
    candidate["admission"] = {
        "status": "rejected",
        "rejection_codes": [rejection_code],
        "manager_qa_id": None,
    }
    return _finalize(candidate)


admit_packet = admit_release_evidence_packet
reject_packet = reject_release_evidence_packet


class _DuplicateKey(ValueError):
    pass


class _NonFinite(ValueError):
    pass


def _duplicate_key_guard(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey
        result[key] = value
    return result


def _reject_nonfinite(_: str) -> None:
    raise _NonFinite


def parse_release_evidence_packet(payload: object) -> dict[str, Any]:
    """Parse bounded UTF-8 JSON or validate a detached plain object."""

    if type(payload) is dict:
        if not is_plain_static_value(payload):
            return _result_error("unsafe_value")
        return validate_release_evidence_packet(copy.deepcopy(payload))
    if type(payload) is str:
        try:
            raw = payload.encode("utf-8")
        except UnicodeEncodeError:
            return _result_error("invalid_utf8")
    elif type(payload) is bytes:
        raw = payload
    else:
        return _result_error("invalid_json")
    if len(raw) > MAX_PACKET_BYTES:
        return _result_error("packet_size")
    try:
        text = raw.decode("utf-8", errors="strict")
        if text.startswith("\ufeff"):
            return _result_error("invalid_utf8")
        value = json.loads(text, object_pairs_hook=_duplicate_key_guard, parse_constant=_reject_nonfinite)
    except _DuplicateKey:
        return _result_error("duplicate_json_key")
    except _NonFinite:
        return _result_error("nonfinite_json")
    except UnicodeDecodeError:
        return _result_error("invalid_utf8")
    except (TypeError, ValueError, json.JSONDecodeError):
        return _result_error("invalid_json")
    if type(value) is not dict:
        return _result_error("object_required")
    return validate_release_evidence_packet(value)


canonical_packet_json = canonical_release_evidence_packet_json
packet_fingerprint = release_evidence_packet_fingerprint


__all__ = [
    "admit_packet",
    "admit_release_evidence_packet",
    "build_release_evidence_packet",
    "canonical_packet_json",
    "create_packet",
    "create_release_evidence_packet",
    "packet_fingerprint",
    "parse_release_evidence_packet",
    "reject_packet",
    "reject_release_evidence_packet",
    "validate_packet",
]
