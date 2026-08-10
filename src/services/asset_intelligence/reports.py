"""Static QA reports, deterministic catalog diffs, and dry-run migration plans."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from src.shared.schemas.asset_intelligence import canonical_asset_catalog_json, canonical_asset_record_json

from .catalog import build_exact_duplicate_groups
from .io import validated_asset_catalog_result
from .providers import provider_capability_cards


def build_asset_qa_report(catalog_value: object) -> dict[str, Any]:
    """Project safe static counts only; no client report mapping is accepted."""

    validation = validated_asset_catalog_result(catalog_value)
    if not validation["valid"]:
        return {
            "valid": False,
            "status": "unavailable",
            "reason": "Catalog failed static validation and no QA report was projected.",
            "action": "Correct catalog contract errors before requesting a static QA report.",
            "errors": [{"code": item["code"], "location": item["location"]} for item in validation["errors"]],
            "execution": "not_run",
        }
    catalog = validation["catalog"]
    assert isinstance(catalog, dict)
    duplicates = build_exact_duplicate_groups(catalog)
    kind_counts: dict[str, int] = {}
    media_counts: dict[str, int] = {}
    retention_counts: dict[str, int] = {}
    perceptual_count = 0
    embedding_count = 0
    for asset in catalog["assets"]:
        kind_counts[asset["asset"]["kind"]] = kind_counts.get(asset["asset"]["kind"], 0) + 1
        media_counts[asset["asset"]["media_type"]] = media_counts.get(asset["asset"]["media_type"], 0) + 1
        retention_counts[asset["retention"]["classification"]] = retention_counts.get(asset["retention"]["classification"], 0) + 1
        perceptual_count += len(asset["fingerprints"]["perceptual"])
        embedding_count += len(asset["fingerprints"]["embedding"])
    return {
        "contract": "asset-catalog-static-qa.v1",
        "valid": True,
        "status": "partial",
        "reason": "Static metadata QA is available; filesystem, provider, workflow, and retention actions were not performed.",
        "action": "Use this report for review and request a separately bounded smoke for operational capabilities.",
        "catalog": {"id": catalog["id"], "fingerprint": validation["fingerprint"]},
        "counts": {
            "assets": len(catalog["assets"]),
            "asset_kinds": dict(sorted(kind_counts.items())),
            "media_types": dict(sorted(media_counts.items())),
            "retention_classes": dict(sorted(retention_counts.items())),
            "perceptual_metadata": perceptual_count,
            "embedding_metadata": embedding_count,
            "exact_duplicate_groups": len(duplicates["groups"]),
        },
        "providers": provider_capability_cards()["cards"],
        "execution": "not_run",
    }


def build_asset_qa_markdown(catalog_value: object) -> str:
    report = build_asset_qa_report(catalog_value)
    if not report["valid"]:
        codes = ", ".join(sorted(item["code"] for item in report["errors"])) or "validation_failed"
        return "# Asset Catalog Static QA\n\nStatus: unavailable\n\nValidation codes: " + codes + "\n"
    counts = report["counts"]
    lines = [
        "# Asset Catalog Static QA",
        "",
        f"Status: `{report['status']}`",
        "",
        "Execution: `not_run`",
        "",
        "## Static summary",
        "",
        f"- Catalog: `{report['catalog']['id']}`",
        f"- Assets: {counts['assets']}",
        f"- Exact SHA-256 duplicate groups: {counts['exact_duplicate_groups']}",
        f"- Perceptual metadata records: {counts['perceptual_metadata']} (not run)",
        f"- Embedding metadata records: {counts['embedding_metadata']} (not run)",
        "",
        "## Provider capability cards",
        "",
    ]
    for card in report["providers"]:
        lines.append(f"- `{card['id']}`: `{card['status']}`; execution `{card['execution']}`")
    lines.append("")
    return "\n".join(lines)


def _assets_by_id(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {asset["id"]: asset for asset in catalog["assets"]}


def diff_asset_catalogs(before: object, after: object) -> dict[str, Any]:
    """Compare canonical catalogs without echoing labels, paths, or unsafe values."""

    source = validated_asset_catalog_result(before)
    target = validated_asset_catalog_result(after)
    if not source["valid"] or not target["valid"]:
        return {
            "valid": False,
            "status": "unavailable",
            "reason": "Both source and target catalogs must pass static validation.",
            "action": "Correct catalog contract errors before requesting a diff.",
            "source_errors": [{"code": item["code"], "location": item["location"]} for item in source["errors"]],
            "target_errors": [{"code": item["code"], "location": item["location"]} for item in target["errors"]],
            "execution": "not_run",
        }
    source_catalog = source["catalog"]
    target_catalog = target["catalog"]
    assert isinstance(source_catalog, dict) and isinstance(target_catalog, dict)
    changes: list[dict[str, str]] = []
    if source_catalog["id"] != target_catalog["id"]:
        changes.append({"kind": "catalog_id_changed"})
    source_assets = _assets_by_id(source_catalog)
    target_assets = _assets_by_id(target_catalog)
    for asset_id in sorted(set(target_assets) - set(source_assets)):
        changes.append({"kind": "asset_added", "asset_id": asset_id})
    for asset_id in sorted(set(source_assets) - set(target_assets)):
        changes.append({"kind": "asset_removed", "asset_id": asset_id})
    for asset_id in sorted(set(source_assets) & set(target_assets)):
        source_item = canonical_asset_record_json(source_assets[asset_id])
        target_item = canonical_asset_record_json(target_assets[asset_id])
        if source_item != target_item:
            changes.append({"kind": "asset_changed", "asset_id": asset_id})
    source_groups = {(group["sha256"], tuple(group["asset_ids"])) for group in build_exact_duplicate_groups(source_catalog)["groups"]}
    target_groups = {(group["sha256"], tuple(group["asset_ids"])) for group in build_exact_duplicate_groups(target_catalog)["groups"]}
    for _digest, assets in sorted(target_groups - source_groups):
        changes.append({"kind": "exact_duplicate_group_added", "asset_id": assets[0]})
    for _digest, assets in sorted(source_groups - target_groups):
        changes.append({"kind": "exact_duplicate_group_removed", "asset_id": assets[0]})
    changes.sort(key=lambda item: (item["kind"], item.get("asset_id", "")))
    return {
        "valid": True,
        "status": "unchanged" if not changes else "changed",
        "from": {"id": source_catalog["id"], "fingerprint": source["fingerprint"]},
        "to": {"id": target_catalog["id"], "fingerprint": target["fingerprint"]},
        "changes": changes,
        "deterministic_digest": hashlib.sha256((canonical_asset_catalog_json(source_catalog) + "\n" + canonical_asset_catalog_json(target_catalog)).encode("utf-8")).hexdigest(),
        "execution": "not_run",
    }


def plan_asset_catalog_migration(before: object, after: object) -> dict[str, Any]:
    """Describe a deterministic dry-run migration that never writes a catalog."""

    diff = diff_asset_catalogs(before, after)
    if not diff["valid"]:
        return {
            "valid": False,
            "status": "unavailable",
            "dry_run": True,
            "reason": diff["reason"],
            "action": diff["action"],
            "steps": [],
            "execution": "not_run",
        }
    if diff["from"]["id"] != diff["to"]["id"]:
        return {
            "valid": True,
            "status": "unavailable",
            "dry_run": True,
            "reason": "Catalog IDs differ, so a migration cannot be inferred safely.",
            "action": "Treat the target as a separate catalog and request explicit integration review.",
            "steps": [],
            "diff_digest": diff["deterministic_digest"],
            "execution": "not_run",
        }
    changes = diff["changes"]
    if not changes:
        status = "not_required"
        reason = "Catalogs are canonically unchanged."
        action = "No migration action is required."
        steps: list[dict[str, str]] = []
    else:
        risky = any(change["kind"] in {"asset_removed", "asset_changed", "exact_duplicate_group_removed"} for change in changes)
        status = "manual_review" if risky else "planned"
        reason = "The dry-run found removed or changed asset metadata requiring human review." if risky else "The dry-run found additive static catalog metadata."
        action = "Review changed asset records manually; this plan does not change any asset." if risky else "Revalidate the target catalog before managed adoption."
        steps = [{"id": "validate-target-static", "action": "Revalidate the target using asset-catalog.v1."}]
        if risky:
            steps.append({"id": "review-asset-contract", "action": "Review removed or changed asset/duplicate metadata manually."})
        steps.append({"id": "apply-reference-manually", "action": "Apply any approved consumer reference change outside this dry-run plan."})
    result = {
        "valid": True,
        "status": status,
        "dry_run": True,
        "reason": reason,
        "action": action,
        "from": diff["from"],
        "to": diff["to"],
        "changes": changes,
        "steps": steps,
        "diff_digest": diff["deterministic_digest"],
        "execution": "not_run",
    }
    result["plan_digest"] = hashlib.sha256(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return copy.deepcopy(result)
