"""Deterministic typed Smart Collection evaluation with no code execution."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from .catalog import build_exact_duplicate_groups
from .io import validated_asset_catalog_result, validated_smart_collection_result


def _field_value(asset: dict[str, Any], field: str, exact_duplicate_ids: set[str]) -> object:
    if field == "asset.bytes":
        return asset["asset"]["bytes"]
    if field == "asset.kind":
        return asset["asset"]["kind"]
    if field == "asset.media_type":
        return asset["asset"]["media_type"]
    if field == "retention.classification":
        return asset["retention"]["classification"]
    if field == "fingerprints.has_perceptual":
        return bool(asset["fingerprints"]["perceptual"])
    if field == "fingerprints.has_embedding":
        return bool(asset["fingerprints"]["embedding"])
    if field == "exact_duplicate":
        return asset["id"] in exact_duplicate_ids
    raise ValueError("validated smart collection field is unavailable")


def _matches_expression(expression: dict[str, Any], asset: dict[str, Any], exact_duplicate_ids: set[str]) -> bool:
    if "all" in expression:
        return all(_matches_expression(item, asset, exact_duplicate_ids) for item in expression["all"])
    if "any" in expression:
        return any(_matches_expression(item, asset, exact_duplicate_ids) for item in expression["any"])
    actual = _field_value(asset, expression["field"], exact_duplicate_ids)
    operator = expression["operator"]
    expected = expression["value"]
    if operator == "eq":
        return actual == expected
    if operator == "in":
        return actual in expected
    if operator == "gte":
        return isinstance(actual, int) and actual >= expected
    if operator == "lte":
        return isinstance(actual, int) and actual <= expected
    raise ValueError("validated smart collection operator is unavailable")


def evaluate_smart_collection(collection_value: object, catalog_value: object) -> dict[str, Any]:
    """Evaluate a revalidated allowlisted DSL against a detached static catalog."""

    collection_validation = validated_smart_collection_result(collection_value)
    catalog_validation = validated_asset_catalog_result(catalog_value)
    if not collection_validation["valid"] or not catalog_validation["valid"]:
        return {
            "valid": False,
            "status": "unavailable",
            "reason": "Collection and catalog must both pass static validation.",
            "action": "Correct the reported descriptor contract errors before evaluation.",
            "collection_errors": [{"code": item["code"], "location": item["location"]} for item in collection_validation["errors"]],
            "catalog_errors": [{"code": item["code"], "location": item["location"]} for item in catalog_validation["errors"]],
            "asset_ids": [],
            "execution": "not_run",
        }
    collection = collection_validation["collection"]
    catalog = catalog_validation["catalog"]
    assert isinstance(collection, dict) and isinstance(catalog, dict)
    duplicates = build_exact_duplicate_groups(catalog)
    exact_duplicate_ids = {asset_id for group in duplicates["groups"] for asset_id in group["asset_ids"]}
    selected = [asset["id"] for asset in sorted(catalog["assets"], key=lambda item: item["id"]) if _matches_expression(collection["query"], asset, exact_duplicate_ids)]
    return {
        "valid": True,
        "status": "partial",
        "reason": "The typed Smart Collection was evaluated deterministically against static metadata only.",
        "action": "Use selected opaque IDs for review; do not infer filesystem availability or provider output.",
        "collection": {"id": collection["id"], "fingerprint": collection_validation["fingerprint"]},
        "catalog": {"id": catalog["id"], "fingerprint": catalog_validation["fingerprint"]},
        "asset_ids": selected,
        "exact_duplicate_group_count": len(duplicates["groups"]),
        "execution": "not_run",
    }


def export_dataset_manifest(catalog_value: object, collection_value: object = None) -> dict[str, Any]:
    """Build canonical JSON metadata for the whole catalog or one typed collection."""

    catalog_validation = validated_asset_catalog_result(catalog_value)
    if not catalog_validation["valid"]:
        return {
            "ready": False,
            "status": "unavailable",
            "reason": "Catalog failed static validation and no manifest was projected.",
            "action": "Correct catalog contract errors before export.",
            "errors": [{"code": item["code"], "location": item["location"]} for item in catalog_validation["errors"]],
        }
    catalog = catalog_validation["catalog"]
    assert isinstance(catalog, dict)
    asset_ids = [asset["id"] for asset in sorted(catalog["assets"], key=lambda item: item["id"])]
    collection_id = None
    if collection_value is not None:
        selection = evaluate_smart_collection(collection_value, catalog)
        if not selection["valid"]:
            return {
                "ready": False,
                "status": "unavailable",
                "reason": "Smart collection failed static validation and no manifest was projected.",
                "action": "Correct collection contract errors before export.",
                "errors": selection["collection_errors"],
            }
        asset_ids = selection["asset_ids"]
        collection_id = selection["collection"]["id"]
    by_id = {asset["id"]: asset for asset in catalog["assets"]}
    manifest = {
        "schema_version": "asset-dataset-manifest.v1",
        "catalog": {"id": catalog["id"], "fingerprint": catalog_validation["fingerprint"]},
        "collection_id": collection_id,
        "assets": [
            {
                "id": asset_id,
                "kind": by_id[asset_id]["asset"]["kind"],
                "media_type": by_id[asset_id]["asset"]["media_type"],
                "sha256": by_id[asset_id]["asset"]["sha256"],
                "bytes": by_id[asset_id]["asset"]["bytes"],
                "retention": copy.deepcopy(by_id[asset_id]["retention"]),
            }
            for asset_id in asset_ids
        ],
        "execution": "not_run",
    }
    content = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return {
        "ready": True,
        "status": "partial",
        "reason": "Dataset manifest is deterministic static metadata; no files were copied, moved, or opened.",
        "action": "Use it for review or a separately approved managed export boundary.",
        "media_type": "application/json",
        "filename": f"{catalog['id']}{'.' + collection_id if collection_id else ''}.asset-dataset-manifest.json",
        "content": content,
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "asset_count": len(asset_ids),
        "execution": "not_run",
    }
