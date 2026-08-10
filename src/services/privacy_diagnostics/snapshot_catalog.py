"""Trusted fixed-root loader for server-owned diagnostic snapshots."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.shared.schemas.privacy_diagnostics import OPAQUE_ID_RE

from .io import safe_import_diagnostic_snapshot
from .policy_catalog import REPOSITORY_ROOT, _read_managed_json
from .provenance import _issue_server_owned


# The sample descriptor is deliberately inside a fixed repository-owned root.
# No caller-provided path or payload can change this root.
MANAGED_DIAGNOSTIC_SNAPSHOT_ROOT = REPOSITORY_ROOT / "privacy_diagnostics" / "samples"


def _summary(imported: dict[str, Any]) -> dict[str, Any]:
    snapshot = imported["snapshot"]
    assert isinstance(snapshot, dict)
    return {
        "id": snapshot["id"],
        "policy_id": snapshot["policy_id"],
        "fingerprint": imported["fingerprint"],
        "availability": {"status": "partial", "reason": "Managed snapshot provenance was established statically. No OS, process, device, or runtime probe was performed.", "action": "Use the trusted carrier for deterministic diagnostics only."},
    }


def _discover(*, include_owned: bool) -> dict[str, Any]:
    root = MANAGED_DIAGNOSTIC_SNAPSHOT_ROOT
    if not root.is_dir() or root.is_symlink():
        result: dict[str, Any] = {"status": "unavailable", "reason": "The managed diagnostic snapshot directory is unavailable.", "action": "Restore the repository-managed snapshot directory and retry static validation.", "snapshots": [], "errors": [{"code": "managed_snapshot_root_unavailable"}], "execution": "not_run"}
        if include_owned:
            result["_owned_snapshots"] = {}
        return result
    imports: dict[str, list[dict[str, Any]]] = {}
    errors: list[str] = []
    for path in sorted(root.rglob("*.diagnostic-snapshot.json")):
        payload, read_error = _read_managed_json(path, root=root)
        if read_error is not None:
            errors.append(read_error)
            continue
        assert payload is not None
        imported = safe_import_diagnostic_snapshot(payload)
        if not imported["accepted"]:
            errors.append("managed_snapshot_invalid")
            continue
        snapshot = imported["snapshot"]
        assert isinstance(snapshot, dict)
        imports.setdefault(snapshot["id"], []).append(imported)
    records: list[dict[str, Any]] = []
    owned: dict[str, Any] = {}
    for snapshot_id in sorted(imports):
        candidates = imports[snapshot_id]
        if len(candidates) != 1:
            errors.append("managed_snapshot_identity_ambiguous")
            continue
        imported = candidates[0]
        records.append(_summary(imported))
        if include_owned:
            snapshot = imported["snapshot"]
            owned[snapshot_id] = _issue_server_owned("diagnostic-snapshot", snapshot, imported["fingerprint"])
    result = {"status": "partial" if records else "unavailable", "reason": "Managed snapshot discovery is fixed-root and static only.", "action": "Use only trusted snapshot carriers for diagnostics planning.", "snapshots": sorted(records, key=lambda item: item["id"]), "errors": [{"code": code} for code in sorted(set(errors))], "execution": "not_run"}
    if include_owned:
        result["_owned_snapshots"] = owned
    return result


def discover_managed_diagnostic_snapshots() -> dict[str, Any]:
    return _discover(include_owned=False)


def load_server_owned_snapshot(snapshot_id: object) -> dict[str, Any]:
    if not isinstance(snapshot_id, str) or not OPAQUE_ID_RE.fullmatch(snapshot_id):
        return {"found": False, "status": "unavailable", "reason": "Snapshot ID is invalid.", "action": "Use a declared fixed-root snapshot ID.", "execution": "not_run"}
    discovered = _discover(include_owned=True)
    owned = discovered["_owned_snapshots"].get(snapshot_id)
    if owned is None:
        return {"found": False, "status": "unavailable", "reason": "No unique managed snapshot passed static validation.", "action": "Check the snapshot ID and remove duplicate or invalid descriptors.", "execution": "not_run"}
    summary = next(item for item in discovered["snapshots"] if item["id"] == snapshot_id)
    return {"found": True, "status": "partial", "reason": "Snapshot provenance was established by the fixed-root loader. No machine or runtime state was accessed.", "action": "Use the opaque server-owned snapshot carrier for static diagnostics.", "snapshot": owned, "fingerprint": summary["fingerprint"], "execution": "not_run"}


__all__ = ["discover_managed_diagnostic_snapshots", "load_server_owned_snapshot"]
