"""Dry-run-only remediation planning for static diagnostic findings."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from src.shared.schemas.privacy_diagnostics import (
    ACTION_CODES,
    canonical_remediation_plan_json,
    validate_diagnostic_finding,
    validate_diagnostic_snapshot,
    validate_privacy_policy,
    validate_remediation_plan,
)


_STATIC_REASON = "Remediation is a detached dry-run plan. No configuration, file, process, provider, or runtime action was performed."
_STATIC_ACTION = "Review prerequisites and rollback notes, then authorize any separate bounded change through its owning service."
_ACTION_META: dict[str, dict[str, Any]] = {
    "collect_missing_evidence": {"risk": "medium", "owner": "privacy-diagnostics", "prerequisites": ["policy-loaded", "server-owned-snapshot", "static-review"], "rollback_code": "discard-dry-run", "rollback_notes": "Discard the dry-run evidence request without changing server state."},
    "record_consent": {"risk": "high", "owner": "privacy-diagnostics", "prerequisites": ["policy-loaded", "static-review"], "rollback_code": "restore-declared-metadata", "rollback_notes": "Restore the prior declared consent metadata through the authorized ledger owner."},
    "refresh_contract_evidence": {"risk": "medium", "owner": "privacy-diagnostics", "prerequisites": ["policy-loaded", "server-owned-snapshot", "static-review"], "rollback_code": "discard-dry-run", "rollback_notes": "Discard the dry-run contract review without changing the contract or runtime."},
    "review_component_status": {"risk": "medium", "owner": "privacy-diagnostics", "prerequisites": ["policy-loaded", "server-owned-snapshot", "static-review"], "rollback_code": "no-op-review-only", "rollback_notes": "Review-only planning has no state to roll back."},
    "review_config_drift": {"risk": "high", "owner": "privacy-diagnostics", "prerequisites": ["policy-loaded", "server-owned-snapshot", "static-review"], "rollback_code": "restore-declared-metadata", "rollback_notes": "Restore the prior declared configuration metadata through its authorized owner. This plan does not edit it."},
    "review_tool_status": {"risk": "medium", "owner": "privacy-diagnostics", "prerequisites": ["policy-loaded", "server-owned-snapshot", "static-review"], "rollback_code": "no-op-review-only", "rollback_notes": "Review-only planning has no runtime or tool state to roll back."},
}


def _unwrap(value: object, key: str) -> object:
    if isinstance(value, dict) and value.get("valid") is True and isinstance(value.get(key), dict):
        return value[key]
    if isinstance(value, dict) and value.get("accepted") is True and isinstance(value.get(key), dict):
        return value[key]
    return value


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _derived_id(prefix: str, value: str) -> str:
    candidate = f"{prefix}-{value}"
    return candidate if len(candidate) <= 120 else f"{prefix}-{_digest(value)[:32]}"


def _failure(code: str, reason: str, action: str) -> dict[str, Any]:
    return {"valid": False, "ready": False, "status": "unavailable", "reason": reason, "action": action, "errors": [{"code": code}], "plan": None, "execution": "not_run"}


def plan_remediation(policy_value: object, snapshot_value: object, findings_value: object) -> dict[str, Any]:
    """Create a deterministic dry-run plan from normalized finding objects."""

    policy_result = validate_privacy_policy(_unwrap(policy_value, "policy"))
    snapshot_result = validate_diagnostic_snapshot(_unwrap(snapshot_value, "snapshot"))
    if not policy_result["valid"] or not snapshot_result["valid"]:
        return _failure("input_invalid", "Policy or snapshot failed closed static validation.", "Supply server-owned validated metadata and retry.")
    policy = policy_result["policy"]
    snapshot = snapshot_result["snapshot"]
    assert isinstance(policy, dict) and isinstance(snapshot, dict)
    if snapshot["policy_id"] != policy["id"]:
        return _failure("policy_snapshot_mismatch", "Policy and snapshot identities do not match.", "Use one server-owned policy and its matching snapshot.")
    if not isinstance(findings_value, list):
        return _failure("findings_type", "Remediation planning accepts only a bounded finding array.", "Pass deterministic server-owned findings, not a client-built report mapping.")
    if len(findings_value) > 256:
        return _failure("findings_limit", "Finding count exceeds the static remediation bound.", "Reduce the finding set before planning.")

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in findings_value:
        validation = validate_diagnostic_finding(item)
        if not validation["valid"]:
            return _failure("finding_invalid", "A finding failed closed validation.", "Use only normalized findings from deterministic diagnostics.")
        finding = validation["finding"]
        assert isinstance(finding, dict)
        if finding["id"] in seen:
            return _failure("finding_duplicate", "Finding IDs must be unique in a remediation plan.", "Deduplicate the server-owned findings and retry.")
        seen.add(finding["id"])
        normalized.append(finding)

    actions: list[dict[str, Any]] = []
    for finding in sorted(normalized, key=lambda item: item["id"]):
        action_code = finding["action_code"]
        meta = _ACTION_META.get(action_code)
        if meta is None or action_code not in ACTION_CODES:
            return _failure("action_not_allowlisted", "Finding action is not part of the fixed remediation allowlist.", "Use a fixed diagnostic action code.")
        action_id = f"remediation-{finding['id']}"
        if len(action_id) > 120:
            action_id = f"remediation-{_digest(finding['id'])[:16]}"
        actions.append(
            {
                "id": action_id,
                "finding_id": finding["id"],
                "action_code": action_code,
                "risk": meta["risk"],
                "owner": meta["owner"],
                "prerequisites": list(meta["prerequisites"]),
                "rollback_code": meta["rollback_code"],
                "rollback_notes": meta["rollback_notes"],
                "status": "manual_review" if meta["risk"] == "high" else "planned",
            }
        )
    plan_status = "manual_review" if any(action["status"] == "manual_review" for action in actions) else "planned"
    plan = {
        "schema_version": "remediation-plan.v1",
        "id": _derived_id("remediation-plan", snapshot["id"]),
        "label": "Static diagnostic remediation plan",
        "policy_id": policy["id"],
        "snapshot_id": snapshot["id"],
        "source": {"kind": "server-owned", "reference": _derived_id("remediation", snapshot["id"])},
        "ledger": copy.deepcopy(snapshot["ledger"]),
        "consent": copy.deepcopy(snapshot["consent"]),
        "retention": copy.deepcopy(policy["retention"]),
        "dry_run": True,
        "status": plan_status,
        "actions": actions,
        "execution": "not_run",
    }
    validation = validate_remediation_plan(plan)
    if not validation["valid"]:
        return _failure("plan_invariant", "The deterministic remediation plan failed its closed contract invariant.", "Keep the dry-run output within the published bounded schema.")
    detached = copy.deepcopy(validation["plan"])
    assert isinstance(detached, dict)
    return {
        "valid": True,
        "ready": True,
        "status": plan_status,
        "reason": _STATIC_REASON,
        "action": _STATIC_ACTION,
        "errors": [],
        "dry_run": True,
        "plan": detached,
        "fingerprint": _digest(json.loads(canonical_remediation_plan_json(detached))),
        "execution": "not_run",
    }


build_remediation_plan = plan_remediation

__all__ = ["build_remediation_plan", "plan_remediation"]
