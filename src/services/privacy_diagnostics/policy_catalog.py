"""Fixed-root managed privacy-policy discovery with bounded reads."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import stat
from typing import Any

from src.shared.schemas.privacy_diagnostics import MAX_DESCRIPTOR_BYTES, OPAQUE_ID_RE, validate_privacy_policy

from .io import _DuplicateJsonKey, _duplicate_key_guard, _non_finite_number, safe_import_privacy_policy
from .provenance import _issue_server_owned


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MANAGED_PRIVACY_POLICY_ROOT = REPOSITORY_ROOT / "privacy_diagnostics" / "policies"


def _contained(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _managed_path_safe(path: Path, root: Path | None = None) -> bool:
    root = MANAGED_PRIVACY_POLICY_ROOT if root is None else root
    if root.is_symlink() or path.is_symlink() or not _contained(path, root):
        return False
    parent = path.parent
    while parent != root:
        if parent == parent.parent or parent.is_symlink():
            return False
        parent = parent.parent
    return True


def _stat_snapshot(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns)


def _read_managed_json(path: Path, *, root: Path | None = None) -> tuple[bytes | None, str | None]:
    """Open once and read at most MAX_DESCRIPTOR_BYTES + 1 bytes."""

    managed_root = MANAGED_PRIVACY_POLICY_ROOT if root is None else root
    if not _managed_path_safe(path, managed_root):
        return None, "managed_policy_refused"
    try:
        before = path.lstat()
    except OSError:
        return None, "managed_policy_refused"
    if not stat.S_ISREG(before.st_mode):
        return None, "managed_policy_refused"
    if before.st_size <= 0 or before.st_size > MAX_DESCRIPTOR_BYTES:
        return None, "managed_policy_size"

    descriptor_fd: int | None = None
    payload: bytes | None = None
    opened: os.stat_result | None = None
    after_descriptor: os.stat_result | None = None
    error: str | None = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor_fd = os.open(path, flags)
        opened = os.fstat(descriptor_fd)
        if not stat.S_ISREG(opened.st_mode):
            error = "managed_policy_refused"
        elif opened.st_size <= 0 or opened.st_size > MAX_DESCRIPTOR_BYTES:
            error = "managed_policy_size"
        elif _stat_snapshot(before) != _stat_snapshot(opened) or not _managed_path_safe(path, managed_root):
            error = "managed_policy_refused"
        else:
            current = path.lstat()
            if _stat_snapshot(opened) != _stat_snapshot(current):
                error = "managed_policy_refused"
            else:
                payload = os.read(descriptor_fd, MAX_DESCRIPTOR_BYTES + 1)
                after_descriptor = os.fstat(descriptor_fd)
    except OSError:
        error = "managed_policy_refused"
    finally:
        if descriptor_fd is not None:
            try:
                os.close(descriptor_fd)
            except OSError:
                error = "managed_policy_refused"

    if error is not None:
        return None, error
    if payload is None or opened is None or after_descriptor is None:
        return None, "managed_policy_refused"
    if len(payload) > MAX_DESCRIPTOR_BYTES:
        return None, "managed_policy_size"
    if len(payload) != opened.st_size or _stat_snapshot(opened) != _stat_snapshot(after_descriptor):
        return None, "managed_policy_refused"
    if not _managed_path_safe(path, managed_root):
        return None, "managed_policy_refused"
    try:
        after_path = path.lstat()
    except OSError:
        return None, "managed_policy_refused"
    if _stat_snapshot(opened) != _stat_snapshot(after_path):
        return None, "managed_policy_refused"
    return payload, None


def _safe_json_validation(payload: bytes) -> tuple[dict[str, Any] | None, str | None]:
    try:
        text = payload.decode("utf-8", errors="strict")
        if text.startswith("\ufeff"):
            return None, "managed_policy_invalid"
        value = json.loads(text, object_pairs_hook=_duplicate_key_guard, parse_constant=_non_finite_number)
    except (_DuplicateJsonKey, UnicodeDecodeError, ValueError):
        return None, "managed_policy_invalid"
    validation = validate_privacy_policy(value)
    if not validation["valid"]:
        return None, "managed_policy_invalid"
    return validation, None


def _summary(imported: dict[str, Any]) -> dict[str, Any]:
    policy = imported["policy"]
    assert isinstance(policy, dict)
    return {
        "id": policy["id"],
        "fingerprint": imported["fingerprint"],
        "revision": policy["revision"],
        "redaction_profile": policy["redaction_profile"],
        "allowlists": {"components": len(policy["components"]), "config_keys": len(policy["config_keys"]), "tool_ids": len(policy["tool_ids"]), "contract_ids": len(policy["contract_ids"])},
        "availability": {"status": "partial", "reason": "Managed privacy policy passed static validation. No OS, process, device, or runtime probe was performed.", "action": "Use the server-owned policy result for static diagnostics only."},
    }


def _discover(*, include_validated: bool) -> dict[str, Any]:
    root = MANAGED_PRIVACY_POLICY_ROOT
    if not root.is_dir() or root.is_symlink():
        result: dict[str, Any] = {"status": "unavailable", "reason": "The managed privacy-policy directory is unavailable.", "action": "Restore the repository-managed policy directory and rerun static validation.", "policies": [], "errors": [{"code": "managed_policy_root_unavailable"}], "execution": "not_run"}
        if include_validated:
            result["_validated_policies"] = {}
        return result
    imports: dict[str, list[dict[str, Any]]] = {}
    errors: list[str] = []
    for path in sorted(root.rglob("*.privacy-policy.json")):
        payload, read_error = _read_managed_json(path)
        if read_error is not None:
            errors.append(read_error)
            continue
        assert payload is not None
        imported = safe_import_privacy_policy(payload)
        if not imported["accepted"]:
            errors.append("managed_policy_invalid")
            continue
        policy = imported["policy"]
        assert isinstance(policy, dict)
        imports.setdefault(policy["id"], []).append(imported)
    records: list[dict[str, Any]] = []
    unique: dict[str, dict[str, Any]] = {}
    for policy_id in sorted(imports):
        candidates = imports[policy_id]
        if len(candidates) != 1:
            errors.append("managed_policy_identity_ambiguous")
            continue
        unique[policy_id] = candidates[0]
        records.append(_summary(candidates[0]))
    records.sort(key=lambda item: item["id"])
    result = {"status": "partial" if records else "unavailable", "reason": "Managed policy discovery is static only. No machine or runtime state was accessed.", "action": "Use only the server-owned policy summaries for diagnostics planning.", "policies": records, "errors": [{"code": code} for code in sorted(set(errors))], "execution": "not_run"}
    if include_validated:
        result["_validated_policies"] = unique
    return result


def discover_managed_privacy_policies() -> dict[str, Any]:
    return _discover(include_validated=False)


def load_managed_privacy_policy(policy_id: object) -> dict[str, Any]:
    if not isinstance(policy_id, str) or not OPAQUE_ID_RE.fullmatch(policy_id):
        return {"found": False, "status": "unavailable", "reason": "Policy ID is invalid.", "action": "Use a declared managed policy ID.", "execution": "not_run"}
    discovery = _discover(include_validated=True)
    imported = discovery["_validated_policies"].get(policy_id)
    if imported is None:
        return {"found": False, "status": "unavailable", "reason": "No unique managed privacy policy passed static validation.", "action": "Check the policy ID and remove any duplicate or invalid descriptor.", "execution": "not_run"}
    return {"found": True, "status": "partial", "reason": "Policy is detached server-owned metadata. No machine or runtime state was accessed.", "action": "Use this policy only for static diagnostics planning.", "policy": copy.deepcopy(imported["policy"]), "fingerprint": imported["fingerprint"], "execution": "not_run"}


def load_server_owned_policy(policy_id: object) -> dict[str, Any]:
    """Load a policy carrier exclusively from the fixed managed policy root."""

    loaded = load_managed_privacy_policy(policy_id)
    if not loaded.get("found"):
        return {"found": False, "status": "unavailable", "reason": "No unique managed privacy policy can establish server provenance.", "action": "Use a fixed-root managed policy ID.", "execution": "not_run"}
    policy = loaded.get("policy")
    if not isinstance(policy, dict) or not isinstance(loaded.get("fingerprint"), str):
        return {"found": False, "status": "unavailable", "reason": "Managed policy provenance could not be established.", "action": "Reload the managed policy through the trusted loader.", "execution": "not_run"}
    return {"found": True, "status": "partial", "reason": "Policy provenance was established by the fixed-root loader. No machine or runtime state was accessed.", "action": "Use the opaque server-owned policy carrier for static diagnostics.", "policy": _issue_server_owned("privacy-policy", policy, loaded["fingerprint"]), "fingerprint": loaded["fingerprint"], "execution": "not_run"}


# Explicit aliases keep the service discoverable without introducing a second
# loader or a second (potentially less safe) policy root.
discover_privacy_policies = discover_managed_privacy_policies
load_privacy_policy = load_managed_privacy_policy
