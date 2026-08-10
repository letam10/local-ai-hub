"""Scrubbed, deterministic support-bundle projection.

The result is a manifest, not a bundle writer.  It contains only opaque IDs,
fixed statuses, counts, and metadata digests.  No log, attachment, path,
command, host value, or client-built report is accepted or emitted.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from src.shared.schemas.privacy_diagnostics import (
    canonical_support_bundle_manifest_json,
    validate_diagnostic_finding,
    validate_diagnostic_snapshot,
    validate_privacy_policy,
    validate_support_bundle_manifest,
)


_STATIC_REASON = "Support projection uses server-owned diagnostic metadata only. No file, log, attachment, or runtime payload was collected."
_STATIC_ACTION = "Review the detached manifest and authorize any separate bounded support export if required."


def _unwrap(value: object, key: str) -> object:
    if isinstance(value, dict) and value.get("valid") is True and isinstance(value.get(key), dict):
        return value[key]
    if isinstance(value, dict) and value.get("accepted") is True and isinstance(value.get(key), dict):
        return value[key]
    return value


def _failure(code: str, reason: str, action: str) -> dict[str, Any]:
    return {
        "valid": False,
        "ready": False,
        "status": "unavailable",
        "reason": reason,
        "action": action,
        "errors": [{"code": code}],
        "manifest": None,
        "execution": "not_run",
    }


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _derived_id(prefix: str, value: str) -> str:
    candidate = f"{prefix}-{value}"
    return candidate if len(candidate) <= 120 else f"{prefix}-{_digest(value)[:32]}"


def _entry(record: dict[str, Any]) -> dict[str, Any]:
    return {"id": record["id"], "status": record["status"], "digest": _digest(record)}


def _validated_inputs(policy_value: object, snapshot_value: object) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[dict[str, str]]]:
    policy_result = validate_privacy_policy(_unwrap(policy_value, "policy"))
    snapshot_result = validate_diagnostic_snapshot(_unwrap(snapshot_value, "snapshot"))
    errors = list(policy_result.get("errors", [])) + list(snapshot_result.get("errors", []))
    if errors:
        return None, None, errors
    policy = policy_result["policy"]
    snapshot = snapshot_result["snapshot"]
    assert isinstance(policy, dict) and isinstance(snapshot, dict)
    if snapshot.get("policy_id") != policy.get("id"):
        errors.append({"code": "policy_snapshot_mismatch", "location": "snapshot.policy_id"})
        return None, None, errors
    return copy.deepcopy(policy), copy.deepcopy(snapshot), []


def build_support_bundle_manifest(policy_value: object, snapshot_value: object, findings_value: object) -> dict[str, Any]:
    """Build a detached manifest from validated server-owned findings."""

    policy, snapshot, errors = _validated_inputs(policy_value, snapshot_value)
    if errors:
        return _failure("input_invalid", "Policy or snapshot failed closed static validation.", "Supply server-owned validated metadata and retry.")
    if not isinstance(findings_value, list):
        return _failure("findings_type", "Support projection accepts only a bounded finding array.", "Pass the findings returned by deterministic diagnostics. Do not construct a report mapping.")
    if len(findings_value) > 256:
        return _failure("findings_limit", "Finding count exceeds the static support bound.", "Reduce the server-owned finding set before projection.")
    findings: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in findings_value:
        validation = validate_diagnostic_finding(item)
        if not validation["valid"]:
            return _failure("finding_invalid", "A finding failed its closed contract validation.", "Use only normalized findings produced by deterministic diagnostics.")
        finding = validation["finding"]
        assert isinstance(finding, dict)
        if finding["id"] in seen:
            return _failure("finding_duplicate", "Finding IDs must be unique in a support projection.", "Deduplicate the server-owned finding set and retry.")
        seen.add(finding["id"])
        findings.append({"id": finding["id"], "severity": finding["severity"], "availability": finding["availability"], "digest": finding["evidence_digest"]})

    assert policy is not None and snapshot is not None
    consent = snapshot["consent"]
    policy_consent = policy["consent"]
    if any(item.get("state") != "granted" or item.get("scope") not in {"support-bundle", "diagnostics-and-support"} for item in (policy_consent, consent)):
        return _failure("consent_required", "Support-bundle consent is not granted in the server-owned ledger.", "Record support-bundle consent through an authorized ledger process before projection.")

    manifest = {
        "schema_version": "support-bundle-manifest.v1",
        "id": _derived_id("support-bundle", snapshot["id"]),
        "label": "Static diagnostic support bundle",
        "policy_id": policy["id"],
        "snapshot_id": snapshot["id"],
        "source": {"kind": "server-owned", "reference": _derived_id("support-manifest", snapshot["id"])},
        "ledger": copy.deepcopy(snapshot["ledger"]),
        "consent": copy.deepcopy(snapshot["consent"]),
        "retention": copy.deepcopy(policy["retention"]),
        "findings": sorted(findings, key=lambda item: item["id"]),
        "components": sorted((_entry(record) for record in snapshot["components"]), key=lambda item: item["id"]),
        "configs": sorted((_entry(record) for record in snapshot["configs"]), key=lambda item: item["id"]),
        "tools": sorted((_entry(record) for record in snapshot["tools"]), key=lambda item: item["id"]),
        "contracts": sorted((_entry(record) for record in snapshot["contracts"]), key=lambda item: item["id"]),
        "summary": {
            "findings": len(findings),
            "components": len(snapshot["components"]),
            "configs": len(snapshot["configs"]),
            "tools": len(snapshot["tools"]),
            "contracts": len(snapshot["contracts"]),
        },
        "execution": "not_run",
    }
    validation = validate_support_bundle_manifest(manifest)
    if not validation["valid"]:
        return _failure("manifest_invariant", "The deterministic support manifest failed its closed contract invariant.", "Keep the projection within the published bounded schema.")
    detached = copy.deepcopy(validation["manifest"])
    assert isinstance(detached, dict)
    return {
        "valid": True,
        "ready": True,
        "status": "partial",
        "reason": _STATIC_REASON,
        "action": _STATIC_ACTION,
        "errors": [],
        "manifest": detached,
        "fingerprint": _digest(json.loads(canonical_support_bundle_manifest_json(detached))),
        "execution": "not_run",
    }


def _md_safe(value: object) -> str:
    text = str(value)
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ").replace("[", "\\[").replace("]", "\\]").replace("<", "&lt;").replace(">", "&gt;").replace("`", "\\`")


def build_support_bundle_markdown(value: object) -> dict[str, Any]:
    """Render only safe manifest IDs, statuses, counts, and digests."""

    candidate = value.get("manifest") if isinstance(value, dict) and isinstance(value.get("manifest"), dict) else value
    validation = validate_support_bundle_manifest(candidate)
    if not validation["valid"]:
        return {"ready": False, "status": "unavailable", "reason": "Only a valid support manifest can be rendered.", "action": "Correct the closed manifest contract before Markdown projection.", "errors": [{"code": item["code"]} for item in validation["errors"]], "content": None, "execution": "not_run"}
    manifest = validation["manifest"]
    assert isinstance(manifest, dict)
    lines = [
        "# Local AI Hub diagnostic support manifest",
        "",
        f"- Contract: `{_md_safe(manifest['schema_version'])}`",
        f"- Manifest: `{_md_safe(manifest['id'])}`",
        f"- Policy: `{_md_safe(manifest['policy_id'])}`",
        f"- Snapshot: `{_md_safe(manifest['snapshot_id'])}`",
        "- Execution: `not_run`",
        "",
        "| Category | Count |",
        "| --- | ---: |",
    ]
    for key in ("findings", "components", "configs", "tools", "contracts"):
        lines.append(f"| {_md_safe(key)} | {int(manifest['summary'][key])} |")
    lines.extend(["", "| Finding | Severity | Availability | Digest |", "| --- | --- | --- | --- |"])
    for item in manifest["findings"]:
        lines.append(f"| `{_md_safe(item['id'])}` | {_md_safe(item['severity'])} | {_md_safe(item['availability'])} | `{_md_safe(item['digest'])}` |")
    content = "\n".join(lines) + "\n"
    return {"ready": True, "status": "partial", "reason": _STATIC_REASON, "action": _STATIC_ACTION, "content": content, "media_type": "text/markdown", "execution": "not_run"}


build_support_bundle = build_support_bundle_manifest

__all__ = ["build_support_bundle", "build_support_bundle_manifest", "build_support_bundle_markdown"]
