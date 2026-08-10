"""Static privacy-policy diff and dry-run migration reports."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from src.shared.schemas.privacy_diagnostics import OPAQUE_ID_RE, SHA256_RE, canonical_privacy_policy_json, validate_privacy_policy


_STATIC_REASON = "The report is derived from validated managed policy metadata only. No filesystem, host, process, or runtime state was inspected."
_STATIC_ACTION = "Review the opaque change kinds and authorize any separate policy update through its owner."
_CHANGE_FIELDS = (
    ("label", "policy_metadata_changed"),
    ("revision", "policy_revision_changed"),
    ("source", "policy_source_changed"),
    ("ledger", "policy_ledger_changed"),
    ("consent", "policy_consent_changed"),
    ("retention", "policy_retention_changed"),
    ("redaction_profile", "policy_redaction_changed"),
    ("components", "policy_component_allowlist_changed"),
    ("config_keys", "policy_config_allowlist_changed"),
    ("tool_ids", "policy_tool_allowlist_changed"),
    ("contract_ids", "policy_contract_allowlist_changed"),
)
_CHANGE_KINDS = {"policy_identity_changed", "policy_contract_changed"} | {kind for _, kind in _CHANGE_FIELDS}


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _failure(code: str, reason: str, action: str) -> dict[str, Any]:
    return {"valid": False, "status": "unavailable", "reason": reason, "action": action, "errors": [{"code": code}], "changes": [], "execution": "not_run"}


def _policy(value: object) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    candidate = value
    if isinstance(value, dict) and value.get("valid") is True and isinstance(value.get("policy"), dict):
        candidate = value["policy"]
    elif isinstance(value, dict) and value.get("accepted") is True and isinstance(value.get("policy"), dict):
        candidate = value["policy"]
    validation = validate_privacy_policy(candidate)
    if not validation["valid"]:
        return None, validation
    policy = validation["policy"]
    assert isinstance(policy, dict)
    return copy.deepcopy(policy), validation


def _ref(policy: dict[str, Any], fingerprint: str) -> dict[str, str]:
    return {"id": policy["id"], "fingerprint": fingerprint}


def diff_privacy_policies(source_value: object, target_value: object) -> dict[str, Any]:
    """Compare every v1 contract field without echoing labels or values."""

    source, source_validation = _policy(source_value)
    target, target_validation = _policy(target_value)
    if source is None or target is None or source_validation is None or target_validation is None:
        return _failure("policy_invalid", "Both managed policies must pass closed static validation.", "Correct the policy descriptor and retry the static diff.")
    source_fp = source_validation["fingerprint"]
    target_fp = target_validation["fingerprint"]
    changes: list[dict[str, str]] = []
    if source["id"] != target["id"]:
        changes.append({"kind": "policy_identity_changed"})
    for field, kind in _CHANGE_FIELDS:
        source_field = source.get(field)
        target_field = target.get(field)
        if field in {"components", "config_keys", "tool_ids", "contract_ids"}:
            source_field = sorted(source_field) if isinstance(source_field, list) else source_field
            target_field = sorted(target_field) if isinstance(target_field, list) else target_field
        if source_field != target_field:
            changes.append({"kind": kind})
    if source_fp != target_fp and not changes:
        # Future closed-field additions must never silently become unchanged.
        changes.append({"kind": "policy_contract_changed"})
    changes = sorted(changes, key=lambda item: item["kind"])
    status = "unchanged" if source_fp == target_fp else "changed"
    if status == "unchanged" and changes:
        return _failure("diff_invariant", "The canonical policy diff invariant failed.", "Retry with validated canonical policy values.")
    return {
        "valid": True,
        "contract": "privacy-policy-diff.v1",
        "status": status,
        "from": _ref(source, source_fp),
        "to": _ref(target, target_fp),
        "changes": changes,
        "reason": "Managed policy canonical content is unchanged." if status == "unchanged" else _STATIC_REASON,
        "action": "No migration is required." if status == "unchanged" else _STATIC_ACTION,
        "execution": "not_run",
    }


def plan_privacy_policy_migration(source_value: object, target_value: object) -> dict[str, Any]:
    """Plan policy migration without writing, replacing, or guessing versions."""

    diff = diff_privacy_policies(source_value, target_value)
    if not diff.get("valid"):
        return {**diff, "contract": "privacy-policy-migration.v1", "dry_run": True, "steps": []}
    source_ref = diff["from"]
    target_ref = diff["to"]
    changes = diff["changes"]
    if not changes:
        return {
            "valid": True,
            "contract": "privacy-policy-migration.v1",
            "status": "not_required",
            "dry_run": True,
            "from": source_ref,
            "to": target_ref,
            "steps": [],
            "reason": "Managed policy canonical content is unchanged.",
            "action": "No migration is required.",
            "execution": "not_run",
        }
    if any(change["kind"] == "policy_identity_changed" for change in changes):
        status = "unavailable"
        reason = "Policy identity changes require an explicitly authorized replacement, not an inferred migration."
        action = "Keep both opaque policy identities and request an authorized replacement review."
    else:
        restrictive_kinds = {
            "policy_consent_changed",
            "policy_retention_changed",
            "policy_redaction_changed",
            "policy_component_allowlist_changed",
            "policy_config_allowlist_changed",
            "policy_tool_allowlist_changed",
            "policy_contract_allowlist_changed",
            "policy_contract_changed",
            "policy_source_changed",
            "policy_ledger_changed",
        }
        status = "manual_review" if any(change["kind"] in restrictive_kinds for change in changes) else "planned"
        reason = "Policy changes are classified for static review. No policy file or ledger was modified."
        action = "Review each opaque change kind before an authorized policy update."
    steps = [
        {
            "id": f"review-{change['kind']}",
            "kind": change["kind"],
            "status": "manual_review" if status in {"manual_review", "unavailable"} else "planned",
        }
        for change in changes
    ]
    return {
        "valid": True,
        "contract": "privacy-policy-migration.v1",
        "status": status,
        "dry_run": True,
        "from": source_ref,
        "to": target_ref,
        "steps": steps,
        "reason": reason,
        "action": action,
        "execution": "not_run",
    }


def build_policy_diff_markdown(value: object) -> dict[str, Any]:
    """Render a diff using only fixed change kinds and opaque references."""

    diff = value if isinstance(value, dict) else {}
    if not _valid_diff_projection(diff):
        return {"ready": False, "status": "unavailable", "reason": "Only a valid policy diff can be rendered.", "action": "Run a validated static policy diff first.", "content": None, "execution": "not_run"}
    lines = [
        "# Local AI Hub privacy policy diff",
        "",
        f"- Status: `{diff['status']}`",
        f"- From: `{diff['from']['id']}` (`{diff['from']['fingerprint']}`)",
        f"- To: `{diff['to']['id']}` (`{diff['to']['fingerprint']}`)",
        "- Execution: `not_run`",
        "",
        "| Change kind |",
        "| --- |",
    ]
    for change in diff.get("changes", []):
        if isinstance(change, dict) and isinstance(change.get("kind"), str):
            safe = change["kind"].replace("\\", "\\\\").replace("|", "\\|").replace("`", "\\`")
            lines.append(f"| `{safe}` |")
    return {"ready": True, "status": "partial", "reason": _STATIC_REASON, "action": _STATIC_ACTION, "content": "\n".join(lines) + "\n", "media_type": "text/markdown", "execution": "not_run"}


def _valid_diff_projection(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    allowed = {"valid", "contract", "status", "from", "to", "changes", "reason", "action", "execution"}
    if set(value) - allowed or value.get("valid") is not True or value.get("contract") != "privacy-policy-diff.v1" or value.get("execution") != "not_run":
        return False
    if value.get("status") not in {"unchanged", "changed"} or not isinstance(value.get("from"), dict) or not isinstance(value.get("to"), dict):
        return False
    for reference in (value["from"], value["to"]):
        if set(reference) != {"id", "fingerprint"} or not isinstance(reference.get("id"), str) or not OPAQUE_ID_RE.fullmatch(reference["id"]):
            return False
        if not isinstance(reference.get("fingerprint"), str) or not SHA256_RE.fullmatch(reference["fingerprint"]):
            return False
    changes = value.get("changes")
    if not isinstance(changes, list):
        return False
    seen: set[str] = set()
    for change in changes:
        if not isinstance(change, dict) or set(change) != {"kind"} or not isinstance(change.get("kind"), str) or change["kind"] not in _CHANGE_KINDS or change["kind"] in seen:
            return False
        seen.add(change["kind"])
    if value["status"] == "unchanged":
        return value["from"]["fingerprint"] == value["to"]["fingerprint"] and not changes
    return value["from"]["fingerprint"] != value["to"]["fingerprint"] and bool(changes)


diff_policies = diff_privacy_policies
plan_policy_migration = plan_privacy_policy_migration

__all__ = ["build_policy_diff_markdown", "diff_policies", "diff_privacy_policies", "plan_policy_migration", "plan_privacy_policy_migration"]
