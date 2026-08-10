"""Deterministic diagnostics over server-owned bounded metadata.

This module deliberately does not discover components, inspect the operating
system, launch a tool, or read a path.  Callers supply policy and snapshot
objects that have already crossed the managed/server-owned validation
boundary; every public function still revalidates and returns detached data.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Callable

from src.shared.schemas.privacy_diagnostics import (
    ACTION_CODES,
    ACTION_TEXT,
    COMPONENT_IDS,
    COMPONENT_STATUSES,
    CONFIG_KEYS,
    CONFIG_STATUSES,
    CONTRACT_IDS,
    CONTRACT_STATUSES,
    FINDING_RULE_IDS,
    FINDING_SEVERITIES,
    REASON_TEXT,
    TOOL_IDS,
    TOOL_STATUSES,
    canonical_diagnostic_finding_json,
    validate_diagnostic_finding,
    validate_diagnostic_snapshot,
    validate_privacy_policy,
)


_STATIC_REASON = "Diagnostics are derived from server-owned metadata only. No OS, process, device, or runtime probe was performed."
_STATIC_ACTION = "Use the detached findings for static review or a separately authorized bounded smoke."


def _unwrap(value: object, key: str) -> object:
    """Accept a validator result without accepting arbitrary report mappings."""

    if isinstance(value, dict) and value.get("valid") is True and isinstance(value.get(key), dict):
        return value[key]
    if isinstance(value, dict) and value.get("accepted") is True and isinstance(value.get(key), dict):
        return value[key]
    return value


def _invalid_result(errors: list[dict[str, str]], *, reason: str = "Static diagnostics inputs failed validation.") -> dict[str, Any]:
    return {
        "valid": False,
        "status": "unavailable",
        "reason": reason,
        "action": "Provide validated server-owned policy and snapshot metadata. No runtime probe is available here.",
        "errors": [{"code": item.get("code", "invalid"), "location": item.get("location", "input")} for item in errors],
        "findings": [],
        "summary": {"findings": 0, "errors": 0, "warnings": 0},
        "execution": "not_run",
    }


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _finding(
    *,
    rule_id: str,
    severity: str,
    subject_kind: str,
    subject_id: str,
    availability: str,
    action_code: str,
    evidence_count: int,
    evidence: object,
) -> dict[str, Any]:
    # All parts of this identifier come from fixed allowlists or validated
    # opaque IDs.  No source labels, paths, or values are copied into output.
    finding_id = f"finding-{rule_id}-{subject_kind}-{subject_id}"
    if len(finding_id) > 120:
        finding_id = f"finding-{rule_id}-{subject_kind}-{_digest(subject_id)[:16]}"
    result = {
        "schema_version": "diagnostic-finding.v1",
        "id": finding_id,
        "rule_id": rule_id,
        "severity": severity,
        "subject": {"kind": subject_kind, "id": subject_id},
        "availability": availability,
        "reason_code": rule_id,
        "reason": REASON_TEXT[rule_id],
        "action_code": action_code,
        "action": ACTION_TEXT[action_code],
        "evidence_count": max(0, min(int(evidence_count), 1_000_000)),
        "evidence_digest": _digest(evidence),
        "execution": "not_run",
    }
    validation = validate_diagnostic_finding(result)
    if not validation["valid"]:
        # This is an internal invariant failure.  Do not expose the candidate
        # or its validation text at a public boundary.
        raise ValueError("diagnostic finding invariant failed")
    return copy.deepcopy(validation["finding"])


def _severity_for(status: str, *, missing: bool = False) -> tuple[str, str]:
    if missing or status in {"unavailable", "missing", "drifted"}:
        return "error", "unavailable"
    if status in {"partial", "planned", "not_run", "unknown", "stale"}:
        return "warning", "planned" if status in {"planned", "not_run"} else "partial"
    return "warning", "partial"


def _records(value: object) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list):
        return {}
    return {record["id"]: record for record in value if isinstance(record, dict) and isinstance(record.get("id"), str)}


def _append_expected(
    findings: list[dict[str, Any]],
    *,
    expected: list[str],
    records: dict[str, dict[str, Any]],
    subject_kind: str,
    missing_rule: str,
    status_rule: str,
    good_status: str,
    action_code: str,
) -> None:
    for subject_id in expected:
        record = records.get(subject_id)
        if record is None:
            findings.append(
                _finding(
                    rule_id=missing_rule,
                    severity="error",
                    subject_kind=subject_kind,
                    subject_id=subject_id,
                    availability="unavailable",
                    action_code="collect_missing_evidence" if subject_kind != "contract" else action_code,
                    evidence_count=0,
                    evidence={"subject": subject_id, "state": "missing"},
                )
            )
            continue
        status = str(record.get("status"))
        if status == good_status:
            continue
        severity, availability = _severity_for(status)
        findings.append(
            _finding(
                rule_id=status_rule,
                severity=severity,
                subject_kind=subject_kind,
                subject_id=subject_id,
                availability=availability,
                action_code=action_code,
                evidence_count=int(record.get("evidence_count", 0)),
                evidence={"subject": subject_id, "status": status, "evidence_count": int(record.get("evidence_count", 0))},
            )
        )


def diagnose_snapshot(policy_value: object, snapshot_value: object) -> dict[str, Any]:
    """Return deterministic findings for one validated policy/snapshot pair."""

    policy_result = validate_privacy_policy(_unwrap(policy_value, "policy"))
    snapshot_result = validate_diagnostic_snapshot(_unwrap(snapshot_value, "snapshot"))
    errors = list(policy_result.get("errors", [])) + list(snapshot_result.get("errors", []))
    if errors:
        return _invalid_result(errors)
    policy = policy_result["policy"]
    snapshot = snapshot_result["snapshot"]
    assert isinstance(policy, dict) and isinstance(snapshot, dict)
    if snapshot.get("policy_id") != policy.get("id"):
        return _invalid_result([{"code": "policy_snapshot_mismatch", "location": "snapshot.policy_id"}], reason="Policy and snapshot identities do not match.")

    findings: list[dict[str, Any]] = []
    consent = snapshot["consent"]
    policy_consent = policy["consent"]
    if any(item.get("state") != "granted" or item.get("scope") not in {"diagnostics", "diagnostics-and-support"} for item in (policy_consent, consent)):
        findings.append(
            _finding(
                rule_id="consent_required",
                severity="error",
                subject_kind="policy",
                subject_id=policy["id"],
                availability="unavailable",
                action_code="record_consent",
                evidence_count=0,
                evidence={"policy_state": policy_consent.get("state"), "snapshot_state": consent.get("state"), "policy_scope": policy_consent.get("scope"), "snapshot_scope": consent.get("scope")},
            )
        )

    _append_expected(
        findings,
        expected=policy["components"],
        records=_records(snapshot["components"]),
        subject_kind="component",
        missing_rule="missing_evidence",
        status_rule="component_status",
        good_status="operational",
        action_code="review_component_status",
    )
    # A config is healthy only when it is explicitly in sync.  Expected and
    # observed digests remain inside server-owned input and are never echoed.
    config_records = _records(snapshot["configs"])
    for config_id in policy["config_keys"]:
        record = config_records.get(config_id)
        if record is None or record.get("status") in {"missing", "unknown", "not_run"}:
            findings.append(
                _finding(
                    rule_id="missing_evidence",
                    severity="error",
                    subject_kind="config",
                    subject_id=config_id,
                    availability="unavailable",
                    action_code="collect_missing_evidence",
                    evidence_count=0 if record is None else int(record.get("evidence_count", 0)),
                    evidence={"subject": config_id, "state": "missing" if record is None else str(record.get("status"))},
                )
            )
        elif record.get("status") == "drifted":
            findings.append(
                _finding(
                    rule_id="config_drift",
                    severity="error",
                    subject_kind="config",
                    subject_id=config_id,
                    availability="unavailable",
                    action_code="review_config_drift",
                    evidence_count=int(record.get("evidence_count", 0)),
                    evidence={"subject": config_id, "status": "drifted", "expected": record.get("expected_digest"), "observed": record.get("observed_digest")},
                )
            )

    _append_expected(
        findings,
        expected=policy["tool_ids"],
        records=_records(snapshot["tools"]),
        subject_kind="tool",
        missing_rule="missing_evidence",
        status_rule="tool_status",
        good_status="operational",
        action_code="review_tool_status",
    )
    _append_expected(
        findings,
        expected=policy["contract_ids"],
        records=_records(snapshot["contracts"]),
        subject_kind="contract",
        missing_rule="contract_stale",
        status_rule="contract_stale",
        good_status="current",
        action_code="refresh_contract_evidence",
    )
    evidence = snapshot["evidence"]
    expected_counts = {"components": len(policy["components"]), "configs": len(policy["config_keys"]), "tools": len(policy["tool_ids"]), "contracts": len(policy["contract_ids"])}
    if evidence.get("status") != "complete" or any(int(evidence.get(key, 0)) < count for key, count in expected_counts.items()):
        findings.append(
            _finding(
                rule_id="missing_evidence",
                severity="error",
                subject_kind="policy",
                subject_id=policy["id"],
                availability="unavailable",
                action_code="collect_missing_evidence",
                evidence_count=sum(int(evidence.get(key, 0)) for key in expected_counts),
                evidence={"state": evidence.get("status"), "counts": {key: int(evidence.get(key, 0)) for key in expected_counts}},
            )
        )

    # Stable ordering and de-duplication make repeated planning idempotent.
    unique = {item["id"]: item for item in findings}
    findings = [unique[key] for key in sorted(unique)]
    summary = {
        "findings": len(findings),
        "errors": sum(1 for item in findings if item["severity"] == "error"),
        "warnings": sum(1 for item in findings if item["severity"] == "warning"),
    }
    return {
        "valid": True,
        "status": "partial",
        "reason": _STATIC_REASON,
        "action": _STATIC_ACTION,
        "policy_id": policy["id"],
        "snapshot_id": snapshot["id"],
        "policy_fingerprint": policy_result["fingerprint"],
        "snapshot_fingerprint": snapshot_result["fingerprint"],
        "findings": copy.deepcopy(findings),
        "summary": summary,
        "execution": "not_run",
    }


def build_diagnostic_report(policy_value: object, snapshot_value: object) -> dict[str, Any]:
    """Project diagnostics to opaque IDs, codes, counts, and digests only."""

    result = diagnose_snapshot(policy_value, snapshot_value)
    if not result.get("valid"):
        return {key: copy.deepcopy(value) for key, value in result.items() if key not in {"findings"}}
    findings = result["findings"]
    return {
        "contract": "diagnostic-report.v1",
        "status": result["status"],
        "policy_id": result["policy_id"],
        "snapshot_id": result["snapshot_id"],
        "policy_fingerprint": result["policy_fingerprint"],
        "snapshot_fingerprint": result["snapshot_fingerprint"],
        "summary": copy.deepcopy(result["summary"]),
        "finding_codes": [{"id": item["id"], "rule_id": item["rule_id"], "severity": item["severity"], "availability": item["availability"], "digest": item["evidence_digest"]} for item in findings],
        "reason": _STATIC_REASON,
        "action": _STATIC_ACTION,
        "execution": "not_run",
    }


def build_diagnostic_markdown(policy_value: object, snapshot_value: object) -> dict[str, Any]:
    """Render a diagnostic report directly from validated server-owned inputs."""

    report = build_diagnostic_report(policy_value, snapshot_value)
    if report.get("valid") is False or "finding_codes" not in report:
        return {"ready": False, "status": "unavailable", "reason": "Only a valid static diagnostic report can be rendered.", "action": "Provide validated server-owned policy and snapshot metadata.", "content": None, "execution": "not_run"}
    lines = [
        "# Local AI Hub diagnostic report",
        "",
        f"- Status: `{report['status']}`",
        f"- Policy: `{report['policy_id']}`",
        f"- Snapshot: `{report['snapshot_id']}`",
        f"- Findings: {int(report['summary']['findings'])}",
        f"- Errors: {int(report['summary']['errors'])}",
        f"- Warnings: {int(report['summary']['warnings'])}",
        "- Execution: `not_run`",
        "",
        "| Finding | Rule | Severity | Availability | Digest |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in report["finding_codes"]:
        lines.append(f"| `{item['id']}` | `{item['rule_id']}` | `{item['severity']}` | `{item['availability']}` | `{item['digest']}` |")
    return {"ready": True, "status": report["status"], "reason": report["reason"], "action": report["action"], "content": "\n".join(lines) + "\n", "media_type": "text/markdown", "execution": "not_run"}


preflight_diagnostics = diagnose_snapshot
evaluate_diagnostics = diagnose_snapshot
