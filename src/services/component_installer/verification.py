"""Bounded fast inspection and explicit streaming component verification.

This module is the only component verifier used by startup inspection and
existing-install reuse.  Fast inspection performs fixed-leaf ``stat`` calls
only.  Deep verification is explicit, hashes every selected leaf in bounded
chunks, and checks the file identity before and after the read.  Neither path
ever downloads, copies, overwrites, deletes, imports, starts a process or
loads a model/runtime.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import os
from pathlib import Path
import re
import time
from typing import Any

from src.platform.paths import ComponentPathError, HubPaths, is_reparse_point, resolve_component_leaf, resolve_component_root

from .receipts import CatalogBindingContext, RECEIPT_SCHEMA, ReceiptError, read_receipts


HASH_CHUNK_SIZE = 1024 * 1024
MAX_CATALOG_LEAVES = 512
_SHA256_SIZE = 64
_COMPONENT_TYPES = frozenset({"model", "runtime"})
_ROOT_CLASSES = frozenset({"models_root", "runtime_root", "environments_root", "external_managed"})
_STATES = frozenset({"DISCOVERED", "INSTALLED_UNVERIFIED", "INSTALLED_VERIFIED", "PARTIAL", "NOT_INSTALLED", "UNAVAILABLE"})
_SAFE_ID = re.compile(r"^[a-z][a-z0-9._-]{1,95}$")


def _fixed_result(component_id: str, component_type: str, *, status: str, code: str | None = None, reason: str, next_action: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": status,
        "component_id": component_id if isinstance(component_id, str) and _SAFE_ID.fullmatch(component_id) else "unknown",
        "component_type": component_type if isinstance(component_type, str) and component_type in _COMPONENT_TYPES else "unknown",
        "state": status if status in _STATES else "UNAVAILABLE",
        "execution": "not_run",
        "dry_run": True,
        "leaves": [],
        "reason": reason,
        "next_action": next_action,
        "operational": False,
    }
    if code is not None:
        result["code"] = code
    return result


def _catalog_leaves(record: Mapping[str, Any], component_type: str) -> tuple[list[dict[str, Any]] | None, str | None]:
    if not isinstance(component_type, str) or component_type not in _COMPONENT_TYPES or not isinstance(record, Mapping):
        return None, "catalog_record_invalid"
    raw = record.get("files") if component_type == "model" else record.get("required_leaves")
    if not isinstance(raw, list) or not raw or len(raw) > MAX_CATALOG_LEAVES:
        return None, "catalog_leaves_missing"
    leaves: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if component_type == "model":
            if not isinstance(item, Mapping):
                return None, "catalog_leaf_invalid"
            relative = item.get("relative_path")
            expected_size = item.get("size_bytes")
            expected_sha = item.get("sha256")
        else:
            if isinstance(item, Mapping):
                relative = item.get("relative_path")
                expected_size = item.get("size_bytes")
                expected_sha = item.get("sha256")
            else:
                relative = item
                expected_size = None
                expected_sha = record.get("sha256") if len(raw) == 1 else None
        if not isinstance(relative, str) or not relative or len(relative) > 512:
            return None, "catalog_leaf_invalid"
        normalized = relative.replace("\\", "/")
        if normalized in seen:
            return None, "catalog_duplicate_leaf"
        seen.add(normalized)
        if expected_size is not None and (isinstance(expected_size, bool) or not isinstance(expected_size, int) or expected_size < 0):
            return None, "catalog_leaf_size_invalid"
        if expected_size == 0 and expected_sha is None:
            expected_size = None
        if expected_sha is not None:
            if not isinstance(expected_sha, str) or len(expected_sha) != _SHA256_SIZE or any(char not in "0123456789abcdefABCDEF" for char in expected_sha):
                return None, "catalog_leaf_hash_invalid"
            expected_sha = expected_sha.casefold()
        leaves.append({"relative_path": normalized, "expected_size": expected_size, "expected_sha256": expected_sha})
    return leaves, None


def _catalog_binding(
    record: Mapping[str, Any],
    component_type: str,
    catalog_fingerprint: str | None,
    catalog_binding: CatalogBindingContext | Mapping[str, Any] | None,
) -> CatalogBindingContext:
    if catalog_binding is None:
        if not isinstance(catalog_fingerprint, str) or len(catalog_fingerprint) != 64:
            raise ReceiptError("catalog_binding_missing")
        binding = CatalogBindingContext.for_v1(component_type=component_type, record=record, catalog_fingerprint=catalog_fingerprint)
    elif isinstance(catalog_binding, CatalogBindingContext):
        binding = catalog_binding
    elif isinstance(catalog_binding, Mapping):
        binding = CatalogBindingContext.from_mapping(catalog_binding)
    else:
        raise ReceiptError("catalog_binding_invalid")
    binding.validate_record(component_type=component_type, record=record)
    return binding


def _root_class(record: Mapping[str, Any], component_type: str) -> str | None:
    if component_type == "model":
        return "models_root"
    value = record.get("root_class")
    return value if isinstance(value, str) and value in _ROOT_CLASSES else None


def _stat_signature(path: Path) -> tuple[int, int, int, int] | None:
    try:
        value = path.stat()
        return (int(value.st_size), int(value.st_mtime_ns), int(getattr(value, "st_dev", 0)), int(getattr(value, "st_ino", 0)))
    except (OSError, ValueError, TypeError):
        return None


def stream_sha256(path: Path, *, chunk_size: int = HASH_CHUNK_SIZE) -> str:
    """Hash a selected leaf without a whole-file buffer."""

    if not isinstance(chunk_size, int) or chunk_size <= 0 or chunk_size > HASH_CHUNK_SIZE:
        raise ValueError("invalid_hash_chunk_size")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _safe_root(paths: HubPaths, component_id: str, component_type: str, root_class: str | None, *, require_exists: bool) -> tuple[Path | None, str | None]:
    try:
        return resolve_component_root(paths, component_id, component_type, root_class, require_exists=require_exists), None
    except ComponentPathError as exc:
        return None, exc.code
    except (OSError, ValueError):
        return None, "component_root_unavailable"


def _receipt_binding_matches(receipt: Mapping[str, Any], component_id: str, component_type: str, record: Mapping[str, Any], binding: CatalogBindingContext, root_class: str, observed: Sequence[Mapping[str, Any]]) -> bool:
    if receipt.get("schema_version") != RECEIPT_SCHEMA or not isinstance(receipt.get("records"), Mapping):
        return False
    value = receipt["records"].get(component_id)
    if not isinstance(value, Mapping):
        return False
    binding_fields = binding.as_record_fields()
    if value.get("component_id") != component_id or value.get("component_type") != component_type:
        return False
    if value.get("catalog_schema") != binding_fields["catalog_schema"] or value.get("catalog_revision") != binding_fields["catalog_revision"]:
        return False
    if value.get("catalog_fingerprint") != binding_fields["catalog_fingerprint"] or value.get("source_identity") != binding_fields["source_identity"]:
        return False
    if value.get("root_class") != root_class or value.get("location_class") != root_class:
        return False
    receipt_leaves = value.get("leaves")
    if not isinstance(receipt_leaves, list) or len(receipt_leaves) != len(observed):
        return False
    by_path = {item.get("relative_path"): item for item in receipt_leaves if isinstance(item, Mapping)}
    catalog_leaves, catalog_error = _catalog_leaves(record, component_type)
    if catalog_leaves is None:
        return False
    expected_by_path = {item["relative_path"]: item for item in catalog_leaves}
    receipt_state = value.get("state")
    for item in observed:
        relative = item.get("relative_path")
        previous = by_path.get(relative)
        if not isinstance(previous, Mapping) or previous.get("observed_size_bytes") != item.get("observed_size_bytes") or previous.get("observed_mtime_ns") != item.get("observed_mtime_ns"):
            return False
        expected = expected_by_path.get(relative)
        if not isinstance(expected, Mapping):
            return False
        if receipt_state == "INSTALLED_VERIFIED":
            if expected.get("expected_size") is None or expected.get("expected_sha256") is None:
                return False
            if previous.get("verification_level") != "verified" or previous.get("verified_size_bytes") != expected.get("expected_size") or previous.get("verified_sha256") != expected.get("expected_sha256"):
                return False
    state = receipt_state
    return isinstance(state, str) and state in {"INSTALLED_UNVERIFIED", "INSTALLED_VERIFIED", "DISCOVERED"}


class FastComponentInspector:
    """Startup-safe fixed-leaf inspection; never invokes the deep hasher."""

    def inspect(self, *, paths: HubPaths, component_id: str, component_type: str, record: Mapping[str, Any], catalog_fingerprint: str | None = None, catalog_binding: CatalogBindingContext | Mapping[str, Any] | None = None, receipts: Mapping[str, Any] | None = None) -> dict[str, Any]:
        leaves, error = _catalog_leaves(record, component_type)
        if leaves is None:
            return _fixed_result(component_id, component_type, status="UNAVAILABLE", code=error, reason="The server-owned component catalog is invalid or incomplete.", next_action="Review the fixed catalog metadata before verification.")
        try:
            binding = _catalog_binding(record, component_type, catalog_fingerprint, catalog_binding)
        except ReceiptError as exc:
            return _fixed_result(component_id, component_type, status="UNAVAILABLE", code=exc.code, reason="The server-owned catalog binding is unavailable or mismatched.", next_action="Refresh the server-owned catalog context before verification.")
        root_class = _root_class(record, component_type)
        if root_class is None:
            return _fixed_result(component_id, component_type, status="UNAVAILABLE", code="catalog_root_class_invalid", reason="The server-owned component root class is unavailable.", next_action="Review the fixed managed-root contract.")
        root, root_error = _safe_root(paths, component_id, component_type, root_class, require_exists=False)
        if root is None:
            return _fixed_result(component_id, component_type, status="UNAVAILABLE", code=root_error or "component_root_unavailable", reason="The managed component root is unavailable or unsafe.", next_action="Review the server-owned managed installation.")
        observed: list[dict[str, Any]] = []
        present_count = 0
        legacy_record = False
        for item in leaves:
            try:
                target = resolve_component_leaf(root, item["relative_path"], require_exists=False)
            except ComponentPathError:
                target = None
            signature = _stat_signature(target) if target is not None and target.is_file() and not is_reparse_point(target) else None
            if signature is not None:
                present_count += 1
            observed.append({
                "relative_path": item["relative_path"],
                "present": signature is not None,
                "observed_size_bytes": signature[0] if signature else 0,
                "observed_mtime_ns": signature[1] if signature else 0,
                "verification_level": "unverified",
            })
        receipt_doc = receipts if isinstance(receipts, Mapping) else read_receipts(paths.config_root)
        if receipt_doc.get("schema_version") == "component-install-receipts.v2":
            legacy_record = isinstance(receipt_doc.get("records"), Mapping) and component_id in receipt_doc["records"]
        bound = _receipt_binding_matches(receipt_doc, component_id, component_type, record, binding, root_class, observed)
        receipt_state = None
        if bound and isinstance(receipt_doc.get("records"), Mapping) and isinstance(receipt_doc["records"].get(component_id), Mapping):
            candidate_state = receipt_doc["records"][component_id].get("state")
            receipt_state = candidate_state if isinstance(candidate_state, str) else None
            for item in observed:
                item["verification_level"] = "verified" if receipt_state == "INSTALLED_VERIFIED" else "unverified"
        if present_count == 0:
            status = "NOT_INSTALLED"
            reason = "No fixed catalog leaf is present under the managed root."
            action = "Review an explicit component import or installation plan."
        elif present_count < len(observed):
            status = "PARTIAL"
            reason = "Only part of the fixed catalog selection is present."
            action = "Review the incomplete installation without replacing existing bytes."
        elif bound and receipt_state in {"INSTALLED_VERIFIED", "INSTALLED_UNVERIFIED"}:
            status = receipt_state
            reason = "The fixed leaves match the bound server-owned receipt; runtime evidence is still separate."
            action = "Run a separately authorized component capability check before operational use."
        elif legacy_record:
            status = "INSTALLED_UNVERIFIED"
            reason = "A legacy receipt is readable but cannot establish explicit V3 verification."
            action = "Run explicit deep verification to create a V3 receipt."
        else:
            status = "DISCOVERED"
            reason = "All fixed catalog leaves are present, but no current bound receipt was found."
            action = "Confirm an explicit Verify Installation or Reuse Existing action."
        return {
            "status": status,
            "component_id": component_id,
            "component_type": component_type,
            "state": status,
            "location_class": root_class,
            "execution": "not_run",
            "dry_run": True,
            "leaves": observed,
            "catalog_schema": binding.catalog_schema,
            "catalog_fingerprint": binding.catalog_fingerprint,
            "catalog_revision": binding.catalog_revision,
            "source_identity": binding.source_identity,
            "verification_source": "receipt_v3" if bound else "legacy_receipt" if legacy_record else "fixed_leaf_inspection",
            "operational": False,
            "reason": reason,
            "next_action": action,
        }


class DeepComponentVerifier:
    """Explicit streaming verifier for user-confirmed reuse/repair."""

    def verify(self, *, paths: HubPaths, component_id: str, component_type: str, record: Mapping[str, Any], catalog_fingerprint: str | None = None, catalog_binding: CatalogBindingContext | Mapping[str, Any] | None = None, source: str = "existing_install_reuse") -> dict[str, Any]:
        leaves, error = _catalog_leaves(record, component_type)
        if leaves is None:
            return _fixed_result(component_id, component_type, status="UNAVAILABLE", code=error, reason="The server-owned component catalog is invalid or incomplete.", next_action="Review fixed catalog metadata before retrying verification.")
        try:
            binding = _catalog_binding(record, component_type, catalog_fingerprint, catalog_binding)
        except ReceiptError as exc:
            return _fixed_result(component_id, component_type, status="UNAVAILABLE", code=exc.code, reason="The server-owned catalog binding is unavailable or mismatched.", next_action="Refresh the server-owned catalog context before verification.")
        root_class = _root_class(record, component_type)
        root, root_error = _safe_root(paths, component_id, component_type, root_class, require_exists=True) if root_class else (None, "catalog_root_class_invalid")
        if root is None:
            return _fixed_result(component_id, component_type, status="UNAVAILABLE", code=root_error or "component_root_unavailable", reason="The managed component root is unavailable or unsafe.", next_action="Review the existing managed installation without changing its bytes.")
        output_leaves: list[dict[str, Any]] = []
        all_verified = True
        for item in leaves:
            try:
                target = resolve_component_leaf(root, item["relative_path"], require_exists=True)
            except ComponentPathError:
                return _fixed_result(component_id, component_type, status="UNAVAILABLE", code="component_leaf_unavailable", reason="A fixed catalog leaf is missing or unsafe.", next_action="Review the installation manually; no bytes were changed.")
            before = _stat_signature(target)
            if before is None:
                return _fixed_result(component_id, component_type, status="UNAVAILABLE", code="component_leaf_unavailable", reason="A fixed catalog leaf is missing or unsafe.", next_action="Review the installation manually; no bytes were changed.")
            try:
                measured_hash = stream_sha256(target)
            except (OSError, ValueError):
                return _fixed_result(component_id, component_type, status="UNAVAILABLE", code="component_leaf_unreadable", reason="A fixed catalog leaf could not be read safely.", next_action="Review the installation manually; no bytes were changed.")
            after = _stat_signature(target)
            if after is None or after != before:
                return _fixed_result(component_id, component_type, status="CONFLICT", code="component_leaf_changed_during_verification", reason="A fixed catalog leaf changed during verification.", next_action="Recreate an explicit verification plan after the installation is stable.")
            expected_size = item.get("expected_size")
            expected_hash = item.get("expected_sha256")
            if expected_size is not None and before[0] != expected_size:
                return _fixed_result(component_id, component_type, status="CONFLICT", code="component_size_mismatch", reason="A fixed catalog size does not match the existing leaf.", next_action="Review the catalog and installation without overwriting bytes.")
            if expected_hash is not None and measured_hash != expected_hash:
                return _fixed_result(component_id, component_type, status="CONFLICT", code="component_checksum_mismatch", reason="A fixed catalog digest does not match the existing leaf.", next_action="Review the installation and catalog binding without overwriting bytes.")
            verified = expected_size is not None and expected_hash is not None
            all_verified = all_verified and verified
            leaf = {
                "relative_path": item["relative_path"],
                "observed_size_bytes": before[0],
                "observed_mtime_ns": before[1],
                "verification_level": "verified" if verified else "measured_only",
            }
            if verified:
                leaf.update({"verified_size_bytes": before[0], "verified_sha256": measured_hash, "hash_algorithm": "sha256", "verified_at": int(time.time())})
            output_leaves.append(leaf)
        state = "INSTALLED_VERIFIED" if all_verified else "INSTALLED_UNVERIFIED"
        safe_source = source if isinstance(source, str) and source in {"catalog_primary", "existing_install_reuse", "manual_import", "legacy", "unknown"} else "unknown"
        receipt = {
            "component_id": component_id,
            "component_type": component_type,
            "catalog_schema": binding.catalog_schema,
            "catalog_revision": binding.catalog_revision,
            "catalog_fingerprint": binding.catalog_fingerprint,
            "source_identity": binding.source_identity,
            "root_class": root_class,
            "location_class": root_class,
            "leaves": output_leaves,
            "recorded_at": int(time.time()),
            "verified_at": int(time.time()) if all_verified else None,
            "state": state,
            "source": safe_source,
            "operational": False,
        }
        return {
            "status": "completed",
            "component_id": component_id,
            "component_type": component_type,
            "catalog_schema": binding.catalog_schema,
            "catalog_revision": binding.catalog_revision,
            "catalog_fingerprint": binding.catalog_fingerprint,
            "source_identity": binding.source_identity,
            "state": state,
            "location_class": root_class,
            "execution": "not_run",
            "dry_run": True,
            "leaves": output_leaves,
            "receipt": receipt,
            "operational": False,
            "source": safe_source,
            "next_action": "Run a separately authorized bounded capability check before operational promotion." if state == "INSTALLED_VERIFIED" else "The measured installation remains unverified until catalog digests are bound.",
        }


def fast_inspect(**kwargs: Any) -> dict[str, Any]:
    return FastComponentInspector().inspect(**kwargs)


def deep_verify(**kwargs: Any) -> dict[str, Any]:
    return DeepComponentVerifier().verify(**kwargs)


def verify_installation(**kwargs: Any) -> dict[str, Any]:
    return deep_verify(**kwargs)


__all__ = [
    "DeepComponentVerifier", "FastComponentInspector", "HASH_CHUNK_SIZE", "deep_verify",
    "fast_inspect", "stream_sha256", "verify_installation",
]
