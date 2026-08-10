"""Dry-run-only remediation planning for static diagnostic findings."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from src.shared.schemas.privacy_diagnostics import (
    ACTION_CODES,
    canonical_remediation_plan_json,
    validate_remediation_plan,
)

from .diagnostics import diagnose_snapshot
from .provenance import _owned_payload


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


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _derived_id(prefix: str, value: str) -> str:
    candidate = f"{prefix}-{value}"
    return candidate if len(candidate) <= 120 else f"{prefix}-{_digest(value)[:32]}"


def _canonical_findings(value: object) -> str | None:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        return None
    try:
        normalized = sorted((copy.deepcopy(item) for item in value), key=lambda item: str(item.get("id", "")))
        return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        return None


def _failure(code: str, reason: str, action: str) -> dict[str, Any]:
    return {"valid": False, "ready": False, "status": "unavailable", "reason": reason, "action": action, "errors": [{"code": code}], "plan": None, "execution": "not_run"}


def plan_remediation(policy_value: object, snapshot_value: object, findings_value: object | None = None) -> dict[str, Any]:
    """Create a deterministic dry-run plan from freshly derived findings."""

    policy_owned = _owned_payload(policy_value, "privacy-policy")
    snapshot_owned = _owned_payload(snapshot_value, "diagnostic-snapshot")
    if policy_owned is None or snapshot_owned is None:
        return _failure("provenance_required", "Remediation planning requires trusted server-owned policy and snapshot carriers.", "Load both descriptors through the fixed-root trusted loaders and retry.")
    policy = policy_owned[0]
    snapshot = snapshot_owned[0]
    diagnosis = diagnose_snapshot(policy_value, snapshot_value)
    if not diagnosis.get("valid"):
        return _failure("diagnostics_invalid", "Remediation planning requires a valid freshly derived diagnostic set.", "Use matching trusted policy and snapshot carriers.")
    if findings_value is not None and _canonical_findings(findings_value) != _canonical_findings(diagnosis["findings"]):
        return _failure("finding_provenance_mismatch", "Supplied findings do not exactly match freshly derived server-owned diagnostics.", "Omit the findings argument or use the unchanged result from deterministic diagnostics.")
    normalized = copy.deepcopy(diagnosis["findings"])

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
