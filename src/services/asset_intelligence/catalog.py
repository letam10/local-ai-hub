"""Static discovery and exact SHA-256 duplicate projection for managed assets."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import stat
from typing import Any

from src.shared.schemas.asset_intelligence import MAX_DESCRIPTOR_BYTES, OPAQUE_ID_RE, validate_provenance_lineage, validate_smart_collection

from .io import (
    _DuplicateJsonKey,
    _duplicate_key_guard,
    _non_finite_number,
    safe_import_asset_catalog,
    validated_asset_catalog_result,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MANAGED_ASSET_ROOT = REPOSITORY_ROOT / "asset_catalog"


def _contained(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _managed_descriptor_path_safe(path: Path) -> bool:
    """Confirm the fixed managed root and every descriptor parent stay non-symlinked."""

    root = MANAGED_ASSET_ROOT
    if root.is_symlink() or path.is_symlink() or not _contained(path, root):
        return False
    parent = path.parent
    while parent != root:
        if parent == parent.parent or parent.is_symlink():
            return False
        parent = parent.parent
    return True


def _stat_snapshot(value: os.stat_result) -> tuple[int, int, int, int, int]:
    """Return stable descriptor identity and content metadata across lstat/fstat on Windows."""

    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
    )


def _read_managed_json(path: Path) -> tuple[bytes | None, str | None]:
    """Read one fixed-root descriptor with bounded allocation and mutation checks."""

    if not _managed_descriptor_path_safe(path):
        return None, "managed_descriptor_refused"
    try:
        before = path.lstat()
    except OSError:
        return None, "managed_descriptor_refused"
    if not stat.S_ISREG(before.st_mode):
        return None, "managed_descriptor_refused"
    if before.st_size <= 0 or before.st_size > MAX_DESCRIPTOR_BYTES:
        return None, "managed_descriptor_size"

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
            error = "managed_descriptor_refused"
        elif opened.st_size <= 0 or opened.st_size > MAX_DESCRIPTOR_BYTES:
            error = "managed_descriptor_size"
        elif _stat_snapshot(before) != _stat_snapshot(opened) or not _managed_descriptor_path_safe(path):
            error = "managed_descriptor_refused"
        else:
            current = path.lstat()
            if _stat_snapshot(opened) != _stat_snapshot(current):
                error = "managed_descriptor_refused"
            else:
                payload = os.read(descriptor_fd, MAX_DESCRIPTOR_BYTES + 1)
                after_descriptor = os.fstat(descriptor_fd)
    except OSError:
        error = "managed_descriptor_refused"
    finally:
        if descriptor_fd is not None:
            try:
                os.close(descriptor_fd)
            except OSError:
                error = "managed_descriptor_refused"

    if error is not None:
        return None, error
    if payload is None or opened is None or after_descriptor is None:
        return None, "managed_descriptor_refused"
    if len(payload) > MAX_DESCRIPTOR_BYTES:
        return None, "managed_descriptor_size"
    if len(payload) != opened.st_size or _stat_snapshot(opened) != _stat_snapshot(after_descriptor):
        return None, "managed_descriptor_refused"
    if not _managed_descriptor_path_safe(path):
        return None, "managed_descriptor_refused"
    try:
        after_path = path.lstat()
    except OSError:
        return None, "managed_descriptor_refused"
    if _stat_snapshot(opened) != _stat_snapshot(after_path):
        return None, "managed_descriptor_refused"
    return payload, None


def _static_catalog_record(imported: dict[str, Any]) -> dict[str, Any]:
    catalog = imported["catalog"]
    assert isinstance(catalog, dict)
    assets = catalog["assets"]
    return {
        "id": catalog["id"],
        "fingerprint": imported["fingerprint"],
        "asset_count": len(assets),
        "asset_kinds": sorted({asset["asset"]["kind"] for asset in assets}),
        "availability": {
            "status": "partial",
            "reason": "The catalog passed static contract checks, but no asset filesystem or provider operation was performed.",
            "action": "Use the server-owned result for static QA, collection evaluation, or later authorized integration planning.",
        },
    }


def _safe_json_validation(payload: bytes, validator: Any) -> tuple[dict[str, Any] | None, str | None]:
    try:
        value = json.loads(payload.decode("utf-8", errors="strict"), object_pairs_hook=_duplicate_key_guard, parse_constant=_non_finite_number)
    except (_DuplicateJsonKey, UnicodeDecodeError, ValueError):
        return None, "managed_descriptor_invalid"
    validation = validator(value)
    if not validation["valid"]:
        return None, "managed_descriptor_invalid"
    return validation, None


def _discover_managed_asset_catalogs(*, include_validated: bool) -> dict[str, Any]:
    """Discover only fixed-root descriptors; validated payloads stay private by default."""

    if not MANAGED_ASSET_ROOT.is_dir() or MANAGED_ASSET_ROOT.is_symlink():
        result: dict[str, Any] = {
            "status": "unavailable",
            "reason": "The managed asset catalog directory is unavailable.",
            "action": "Restore the repository-managed asset_catalog directory and rerun static validation.",
            "catalogs": [],
            "lineages": [],
            "collections": [],
            "errors": [{"code": "managed_root_unavailable"}],
            "execution": "not_run",
        }
        if include_validated:
            result["_validated_catalogs"] = {}
        return result
    errors: list[str] = []
    catalog_imports: dict[str, list[dict[str, Any]]] = {}
    lineage_imports: dict[str, list[dict[str, Any]]] = {}
    collection_imports: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(MANAGED_ASSET_ROOT.rglob("*.asset-catalog.json")):
        payload, error = _read_managed_json(path)
        if error is not None:
            errors.append(error)
            continue
        assert payload is not None
        imported = safe_import_asset_catalog(payload)
        if not imported["accepted"]:
            errors.append("managed_catalog_invalid")
            continue
        catalog = imported["catalog"]
        assert isinstance(catalog, dict)
        catalog_imports.setdefault(catalog["id"], []).append(imported)
    catalog_records: list[dict[str, Any]] = []
    unique_catalogs: dict[str, dict[str, Any]] = {}
    for catalog_id in sorted(catalog_imports):
        imports = catalog_imports[catalog_id]
        if len(imports) != 1:
            errors.append("managed_catalog_identity_ambiguous")
            continue
        unique_catalogs[catalog_id] = imports[0]
        catalog_records.append(_static_catalog_record(imports[0]))
    for path in sorted(MANAGED_ASSET_ROOT.rglob("*.provenance-lineage.json")):
        payload, error = _read_managed_json(path)
        if error is not None:
            errors.append("managed_lineage_size" if error == "managed_descriptor_size" else "managed_lineage_refused")
            continue
        assert payload is not None
        validation, invalid = _safe_json_validation(payload, validate_provenance_lineage)
        if invalid is not None or validation is None:
            errors.append("managed_lineage_invalid")
            continue
        lineage = validation["lineage"]
        assert isinstance(lineage, dict)
        lineage_imports.setdefault(lineage["id"], []).append({"id": lineage["id"], "fingerprint": validation["fingerprint"], "node_count": len(lineage["nodes"]), "edge_count": len(lineage["edges"]), "execution": "not_run"})
    for path in sorted(MANAGED_ASSET_ROOT.rglob("*.smart-collection.json")):
        payload, error = _read_managed_json(path)
        if error is not None:
            errors.append("managed_collection_size" if error == "managed_descriptor_size" else "managed_collection_refused")
            continue
        assert payload is not None
        validation, invalid = _safe_json_validation(payload, validate_smart_collection)
        if invalid is not None or validation is None:
            errors.append("managed_collection_invalid")
            continue
        collection = validation["collection"]
        assert isinstance(collection, dict)
        collection_imports.setdefault(collection["id"], []).append({"id": collection["id"], "fingerprint": validation["fingerprint"], "execution": "not_run"})
    lineages: list[dict[str, Any]] = []
    for lineage_id in sorted(lineage_imports):
        records = lineage_imports[lineage_id]
        if len(records) != 1:
            errors.append("managed_lineage_identity_ambiguous")
            continue
        lineages.append(records[0])
    collections: list[dict[str, Any]] = []
    for collection_id in sorted(collection_imports):
        records = collection_imports[collection_id]
        if len(records) != 1:
            errors.append("managed_collection_identity_ambiguous")
            continue
        collections.append(records[0])
    catalog_records.sort(key=lambda item: item["id"])
    lineages.sort(key=lambda item: item["id"])
    collections.sort(key=lambda item: item["id"])
    result = {
        "status": "partial" if catalog_records else "unavailable",
        "reason": "Managed asset discovery is static only; no files, providers, or workflows were accessed.",
        "action": "Use the static CLI output for review and reserve runtime verification for a separately authorized smoke.",
        "catalogs": catalog_records,
        "lineages": lineages,
        "collections": collections,
        "errors": [{"code": code} for code in sorted(set(errors))],
        "execution": "not_run",
    }
    if include_validated:
        result["_validated_catalogs"] = unique_catalogs
    return result


def discover_managed_asset_catalogs() -> dict[str, Any]:
    """Discover only fixed-root descriptors; paths and payloads never leave this layer."""

    return _discover_managed_asset_catalogs(include_validated=False)


def load_managed_asset_catalog(catalog_id: object) -> dict[str, Any]:
    if not isinstance(catalog_id, str) or not OPAQUE_ID_RE.fullmatch(catalog_id):
        return {"found": False, "status": "unavailable", "reason": "Catalog ID is invalid.", "action": "Use a declared managed catalog ID.", "execution": "not_run"}
    discovery = _discover_managed_asset_catalogs(include_validated=True)
    if discovery["status"] == "unavailable" and not discovery["catalogs"]:
        return {"found": False, "status": "unavailable", "reason": "The managed asset catalog is unavailable.", "action": "Restore a unique valid managed catalog before loading it.", "execution": "not_run"}
    imports = discovery["_validated_catalogs"]
    imported = imports.get(catalog_id)
    if imported is None:
        return {"found": False, "status": "unavailable", "reason": "No matching unique managed catalog passed static validation.", "action": "Check the managed catalog ID and remove any duplicate descriptor identity.", "execution": "not_run"}
    return {
        "found": True,
        "status": "partial",
        "reason": "The catalog is valid static metadata; no asset contents or runtime providers were accessed.",
        "action": "Use only the returned detached server-owned catalog for static planning.",
        "catalog": copy.deepcopy(imported["catalog"]),
        "fingerprint": imported["fingerprint"],
        "execution": "not_run",
    }


def build_exact_duplicate_groups(value: object) -> dict[str, Any]:
    """Group only validated SHA-256 metadata; no asset content is read or hashed."""

    validation = validated_asset_catalog_result(value)
    if not validation["valid"]:
        return {
            "valid": False,
            "status": "unavailable",
            "reason": "Catalog did not pass static validation.",
            "action": "Correct asset catalog contract errors before requesting duplicate groups.",
            "errors": [{"code": item["code"], "location": item["location"]} for item in validation["errors"]],
            "groups": [],
            "execution": "not_run",
        }
    catalog = validation["catalog"]
    assert isinstance(catalog, dict)
    by_sha256: dict[str, list[str]] = {}
    for asset in catalog["assets"]:
        by_sha256.setdefault(asset["asset"]["sha256"], []).append(asset["id"])
    groups = [
        {"sha256": digest, "asset_ids": sorted(asset_ids), "count": len(asset_ids), "match": "declared_sha256_match"}
        for digest, asset_ids in sorted(by_sha256.items())
        if len(asset_ids) > 1
    ]
    return {
        "valid": True,
        "status": "partial",
        "reason": "Groups use only validated server-owned SHA-256 metadata; perceptual and embedding similarity were not computed.",
        "action": "Review exact metadata groups manually; authorize a separate bounded provider smoke before any similarity claim.",
        "catalog": {"id": catalog["id"], "fingerprint": validation["fingerprint"]},
        "groups": groups,
        "perceptual_similarity": {"status": "not_run", "reason": "Perceptual metadata is descriptive only.", "action": "Do not infer perceptual duplicates from this static report."},
        "embedding_similarity": {"status": "not_run", "reason": "Embedding metadata is descriptive only.", "action": "Do not infer embedding similarity from this static report."},
        "execution": "not_run",
    }
