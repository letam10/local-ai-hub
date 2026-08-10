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
    validate_support_bundle_manifest,
)

from .diagnostics import diagnose_snapshot
from .provenance import _owned_payload


_STATIC_REASON = "Support projection uses server-owned diagnostic metadata only. No file, log, attachment, or runtime payload was collected."
_STATIC_ACTION = "Review the detached manifest and authorize any separate bounded support export if required."


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


def _canonical_findings(value: object) -> str | None:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        return None
    try:
        normalized = sorted((copy.deepcopy(item) for item in value), key=lambda item: str(item.get("id", "")))
        return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        return None


def _trusted_inputs(policy_value: object, snapshot_value: object) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None, str | None]:
    policy_owned = _owned_payload(policy_value, "privacy-policy")
    snapshot_owned = _owned_payload(snapshot_value, "diagnostic-snapshot")
    if policy_owned is None or snapshot_owned is None:
        return None, None, None, "provenance_required"
    policy, _policy_fingerprint = policy_owned
    snapshot, _snapshot_fingerprint = snapshot_owned
    diagnosis = diagnose_snapshot(policy_value, snapshot_value)
    if not diagnosis.get("valid"):
        return None, None, None, "diagnostics_invalid"
    if snapshot.get("policy_id") != policy.get("id"):
        return None, None, None, "policy_snapshot_mismatch"
    return copy.deepcopy(policy), copy.deepcopy(snapshot), diagnosis, None


def build_support_bundle_manifest(policy_value: object, snapshot_value: object, findings_value: object | None = None) -> dict[str, Any]:
    """Build a detached manifest from trusted inputs and freshly derived findings."""

    policy, snapshot, diagnosis, error_code = _trusted_inputs(policy_value, snapshot_value)
    if error_code is not None or policy is None or snapshot is None or diagnosis is None:
        return _failure(error_code or "input_invalid", "Support projection requires trusted server-owned policy and snapshot carriers.", "Load both descriptors through the fixed-root trusted loaders and retry.")
    derived_findings = diagnosis["findings"]
    if findings_value is not None and _canonical_findings(findings_value) != _canonical_findings(derived_findings):
        return _failure("finding_provenance_mismatch", "Supplied findings do not exactly match freshly derived server-owned diagnostics.", "Omit the findings argument or use the unchanged result from deterministic diagnostics.")
    findings = [{"id": item["id"], "severity": item["severity"], "availability": item["availability"], "digest": item["evidence_digest"]} for item in derived_findings]

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
