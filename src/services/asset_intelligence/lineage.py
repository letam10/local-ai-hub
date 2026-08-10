"""Static provenance linkage and retention planning contracts."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from .io import validated_asset_catalog_result, validated_provenance_lineage_result


def preflight_provenance_lineage(lineage_value: object, catalog_value: object = None) -> dict[str, Any]:
    """Check opaque asset references only; no recipe, package, run, or asset is opened."""

    lineage_validation = validated_provenance_lineage_result(lineage_value)
    if not lineage_validation["valid"]:
        return {
            "valid": False,
            "status": "unavailable",
            "reason": "Lineage failed static validation.",
            "action": "Correct the lineage contract errors before integration.",
            "errors": [{"code": item["code"], "location": item["location"]} for item in lineage_validation["errors"]],
            "missing_asset_references": [],
            "execution": "not_run",
        }
    lineage = lineage_validation["lineage"]
    assert isinstance(lineage, dict)
    if catalog_value is None:
        return {
            "valid": True,
            "status": "partial",
            "reason": "Lineage is structurally valid, but no server-owned asset catalog was supplied for reference checking.",
            "action": "Supply a validated server-owned catalog to check asset-node references statically.",
            "lineage": {"id": lineage["id"], "fingerprint": lineage_validation["fingerprint"]},
            "missing_asset_references": [],
            "execution": "not_run",
        }
    catalog_validation = validated_asset_catalog_result(catalog_value)
    if not catalog_validation["valid"]:
        return {
            "valid": False,
            "status": "unavailable",
            "reason": "Asset catalog failed static validation.",
            "action": "Pass a validated server-owned catalog to provenance preflight.",
            "errors": [{"code": item["code"], "location": item["location"]} for item in catalog_validation["errors"]],
            "missing_asset_references": [],
            "execution": "not_run",
        }
    catalog = catalog_validation["catalog"]
    assert isinstance(catalog, dict)
    catalog_assets = {asset["id"]: asset for asset in catalog["assets"]}
    asset_nodes = [node for node in lineage["nodes"] if node["kind"] == "asset"]
    missing = sorted({node["reference"] for node in asset_nodes if node["reference"] not in catalog_assets})
    retention_mismatches = sorted(
        {
            node["reference"]
            for node in asset_nodes
            if node["reference"] in catalog_assets
            and node["retention_days"] != catalog_assets[node["reference"]]["retention"]["days"]
        }
    )
    unavailable = bool(missing or retention_mismatches)
    if missing:
        reason = "One or more asset references are absent from the supplied static catalog."
        action = "Add the missing opaque asset records before integration."
    elif retention_mismatches:
        reason = "One or more lineage asset nodes disagree with the supplied catalog retention metadata."
        action = "Align the lineage asset retention values with the validated catalog before integration."
    else:
        reason = "Lineage asset references and retention metadata match the supplied catalog; recipes, packages, runs, and derivatives remain unexecuted."
        action = "Authorize a separately bounded smoke before claiming any runtime lineage behavior."
    return {
        "valid": True,
        "status": "unavailable" if unavailable else "partial",
        "reason": reason,
        "action": action,
        "lineage": {"id": lineage["id"], "fingerprint": lineage_validation["fingerprint"]},
        "catalog": {"id": catalog["id"], "fingerprint": catalog_validation["fingerprint"]},
        "missing_asset_references": missing,
        "retention_mismatches": retention_mismatches,
        "execution": "not_run",
    }


def plan_asset_retention(catalog_value: object, *, maximum_retention_days: object = None) -> dict[str, Any]:
    """Produce an immutable dry-run retention plan without inspecting or deleting assets."""

    validation = validated_asset_catalog_result(catalog_value)
    if not validation["valid"]:
        return {
            "valid": False,
            "status": "unavailable",
            "dry_run": True,
            "reason": "Catalog failed static validation.",
            "action": "Correct catalog errors before creating a retention plan.",
            "steps": [],
            "execution": "not_run",
        }
    if maximum_retention_days is not None and (isinstance(maximum_retention_days, bool) or not isinstance(maximum_retention_days, int) or not 0 <= maximum_retention_days <= 3650):
        return {
            "valid": False,
            "status": "unavailable",
            "dry_run": True,
            "reason": "Retention override is not a bounded integer.",
            "action": "Use a static retention maximum between zero and 3650 days.",
            "steps": [],
            "execution": "not_run",
        }
    catalog = validation["catalog"]
    assert isinstance(catalog, dict)
    steps: list[dict[str, Any]] = []
    for asset in sorted(catalog["assets"], key=lambda item: item["id"]):
        retention = asset["retention"]
        review_required = maximum_retention_days is not None and retention["days"] > maximum_retention_days
        steps.append(
            {
                "asset_id": asset["id"],
                "classification": retention["classification"],
                "declared_days": retention["days"],
                "status": "manual_review" if review_required else "recorded",
            }
        )
    status = "manual_review" if any(step["status"] == "manual_review" for step in steps) else "planned"
    result = {
        "valid": True,
        "status": status,
        "dry_run": True,
        "reason": "A retention override would shorten one or more declared records." if status == "manual_review" else "Static retention metadata was recorded without changing any asset.",
        "action": "Review shortened retention records manually; this plan never deletes or moves assets." if status == "manual_review" else "Use the static plan for a separately approved retention workflow.",
        "catalog": {"id": catalog["id"], "fingerprint": validation["fingerprint"]},
        "steps": steps,
        "execution": "not_run",
    }
    result["plan_digest"] = hashlib.sha256(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return copy.deepcopy(result)
