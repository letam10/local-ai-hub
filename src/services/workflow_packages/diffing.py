"""Deterministic descriptor comparison and dry-run migration planning."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from src.shared.schemas.workflow_package import canonical_workflow_package_json, compare_semver

from .io import validated_package_result


def _change(kind: str, *, blueprint: str | None = None, entity_id: str | None = None) -> dict[str, str]:
    value = {"kind": kind}
    if blueprint is not None:
        value["blueprint"] = blueprint
    if entity_id is not None:
        value["entity_id"] = entity_id
    return value


def _blueprints(package: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {"workflow": package["workflow"], **{item["id"]: item for item in package["subgraphs"]}}


def _entities(blueprint: dict[str, Any], key: str) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in blueprint[key]}


def _canonical(value: object) -> str:
    def normalize(item: object) -> object:
        if isinstance(item, dict):
            return {key: normalize(child) for key, child in item.items()}
        if isinstance(item, list):
            normalized = [normalize(child) for child in item]
            if all(isinstance(child, dict) and isinstance(child.get("id"), str) for child in normalized):
                return sorted(normalized, key=lambda child: str(child["id"]))
            if all(child is None or isinstance(child, (bool, int, float, str)) for child in normalized):
                return sorted(normalized, key=lambda child: json.dumps(child, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False))
            return normalized
        return item

    return json.dumps(normalize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _append_entity_changes(changes: list[dict[str, str]], name: str, before: dict[str, Any], after: dict[str, Any], key: str) -> None:
    before_entities = _entities(before, key)
    after_entities = _entities(after, key)
    singular = key[:-1]
    for entity_id in sorted(set(after_entities) - set(before_entities)):
        changes.append(_change(f"{singular}_added", blueprint=name, entity_id=entity_id))
    for entity_id in sorted(set(before_entities) - set(after_entities)):
        changes.append(_change(f"{singular}_removed", blueprint=name, entity_id=entity_id))
    for entity_id in sorted(set(before_entities) & set(after_entities)):
        if _canonical(before_entities[entity_id]) != _canonical(after_entities[entity_id]):
            changes.append(_change(f"{singular}_changed", blueprint=name, entity_id=entity_id))


def _append_contract_entity_changes(changes: list[dict[str, str]], before: list[dict[str, Any]], after: list[dict[str, Any]], prefix: str) -> None:
    before_items = {item["id"]: item for item in before}
    after_items = {item["id"]: item for item in after}
    for entity_id in sorted(set(after_items) - set(before_items)):
        changes.append(_change(f"{prefix}_added", entity_id=entity_id))
    for entity_id in sorted(set(before_items) - set(after_items)):
        changes.append(_change(f"{prefix}_removed", entity_id=entity_id))
    for entity_id in sorted(set(before_items) & set(after_items)):
        if _canonical(before_items[entity_id]) != _canonical(after_items[entity_id]):
            changes.append(_change(f"{prefix}_changed", entity_id=entity_id))


def diff_workflow_packages(before: object, after: object) -> dict[str, Any]:
    """Compare every safe integration contract without projecting prior values."""

    source = validated_package_result(before)
    target = validated_package_result(after)
    if not source["valid"] or not target["valid"]:
        return {
            "valid": False,
            "status": "unavailable",
            "reason": "Both source and target descriptors must pass static validation.",
            "action": "Correct the package contract errors before requesting a diff.",
            "source_errors": [{"code": item["code"], "location": item["location"]} for item in source["errors"]],
            "target_errors": [{"code": item["code"], "location": item["location"]} for item in target["errors"]],
        }
    source_package = source["package"]
    target_package = target["package"]
    assert isinstance(source_package, dict) and isinstance(target_package, dict)
    changes: list[dict[str, str]] = []
    if source_package["id"] != target_package["id"]:
        changes.append(_change("package_id_changed"))
    if source_package["version"] != target_package["version"]:
        changes.append(_change("package_version_changed"))
    for capability in sorted(set(target_package["capabilities"]) - set(source_package["capabilities"])):
        changes.append(_change("capability_added", entity_id=capability))
    for capability in sorted(set(source_package["capabilities"]) - set(target_package["capabilities"])):
        changes.append(_change("capability_removed", entity_id=capability))
    source_compatibility = source_package["compatibility"]
    target_compatibility = target_package["compatibility"]
    if source_compatibility["hub_version"] != target_compatibility["hub_version"]:
        changes.append(_change("hub_compatibility_changed"))
    if source_compatibility["node_contract"] != target_compatibility["node_contract"]:
        changes.append(_change("node_contract_changed"))
    source_operations = set(source_compatibility["required_node_types"])
    target_operations = set(target_compatibility["required_node_types"])
    for operation in sorted(target_operations - source_operations):
        changes.append(_change("required_node_type_added", entity_id=operation))
    for operation in sorted(source_operations - target_operations):
        changes.append(_change("required_node_type_removed", entity_id=operation))
    if source_package["catalog_ready"] != target_package["catalog_ready"]:
        changes.append(_change("catalog_ready_changed"))
    _append_contract_entity_changes(changes, source_package["parameters"], target_package["parameters"], "parameter")
    _append_contract_entity_changes(changes, source_package["requirements"]["models"], target_package["requirements"]["models"], "model_requirement")
    _append_contract_entity_changes(changes, source_package["requirements"]["runtimes"], target_package["requirements"]["runtimes"], "runtime_requirement")
    if _canonical(source_package["resource_hints"]) != _canonical(target_package["resource_hints"]):
        changes.append(_change("resource_hints_changed"))
    if _canonical(source_package["preview"]) != _canonical(target_package["preview"]):
        changes.append(_change("preview_metadata_changed"))
    source_blueprints = _blueprints(source_package)
    target_blueprints = _blueprints(target_package)
    for blueprint_id in sorted(set(target_blueprints) - set(source_blueprints)):
        changes.append(_change("blueprint_added", blueprint=blueprint_id))
    for blueprint_id in sorted(set(source_blueprints) - set(target_blueprints)):
        changes.append(_change("blueprint_removed", blueprint=blueprint_id))
    for blueprint_id in sorted(set(source_blueprints) & set(target_blueprints)):
        _append_entity_changes(changes, blueprint_id, source_blueprints[blueprint_id], target_blueprints[blueprint_id], "inputs")
        _append_entity_changes(changes, blueprint_id, source_blueprints[blueprint_id], target_blueprints[blueprint_id], "outputs")
        _append_entity_changes(changes, blueprint_id, source_blueprints[blueprint_id], target_blueprints[blueprint_id], "nodes")
        _append_entity_changes(changes, blueprint_id, source_blueprints[blueprint_id], target_blueprints[blueprint_id], "edges")
    changes.sort(key=lambda item: (item["kind"], item.get("blueprint", ""), item.get("entity_id", "")))
    return {
        "valid": True,
        "status": "unchanged" if not changes else "changed",
        "from": {"id": source_package["id"], "version": source_package["version"], "fingerprint": source["fingerprint"]},
        "to": {"id": target_package["id"], "version": target_package["version"], "fingerprint": target["fingerprint"]},
        "changes": changes,
        "deterministic_digest": hashlib.sha256((canonical_workflow_package_json(source_package) + "\n" + canonical_workflow_package_json(target_package)).encode("utf-8")).hexdigest(),
        "execution": "not_run",
    }


_MANUAL_REVIEW_CHANGES = {
    "blueprint_removed",
    "catalog_ready_changed",
    "capability_removed",
    "hub_compatibility_changed",
    "input_removed",
    "input_changed",
    "model_requirement_added",
    "model_requirement_removed",
    "model_requirement_changed",
    "node_contract_changed",
    "node_removed",
    "node_changed",
    "output_removed",
    "output_changed",
    "parameter_removed",
    "parameter_changed",
    "required_node_type_added",
    "required_node_type_removed",
    "resource_hints_changed",
    "runtime_requirement_added",
    "runtime_requirement_removed",
    "runtime_requirement_changed",
}


def plan_workflow_migration(before: object, after: object) -> dict[str, Any]:
    """Return a deterministic dry-run plan; it never changes either package."""

    diff = diff_workflow_packages(before, after)
    if not diff["valid"]:
        return {
            "valid": False,
            "status": "unavailable",
            "dry_run": True,
            "reason": diff["reason"],
            "action": diff["action"],
            "steps": [],
        }
    source = diff["from"]
    target = diff["to"]
    if source["id"] != target["id"]:
        return {
            "valid": True,
            "status": "unavailable",
            "dry_run": True,
            "reason": "Package IDs differ, so a migration cannot be inferred safely.",
            "action": "Treat the target as a separate package and request an explicit integration design.",
            "steps": [],
            "diff_digest": diff["deterministic_digest"],
            "execution": "not_run",
        }
    if compare_semver(target["version"], source["version"]) < 0:
        return {
            "valid": True,
            "status": "unavailable",
            "dry_run": True,
            "reason": "Downgrade migrations are not inferred by the static planner.",
            "action": "Keep the newer package unchanged or supply a separately reviewed rollback contract.",
            "steps": [],
            "diff_digest": diff["deterministic_digest"],
            "execution": "not_run",
        }
    changes = diff["changes"]
    if not changes:
        status = "not_required"
        reason = "Descriptors are semantically unchanged."
        action = "No migration action is required."
        steps: list[dict[str, str]] = []
    else:
        risky = any(item["kind"] in _MANUAL_REVIEW_CHANGES for item in changes)
        status = "manual_review" if risky else "planned"
        reason = "The dry-run found restrictive or changed integration contracts that require a human review." if risky else "The dry-run found additive static descriptor changes."
        action = "Review the listed contract changes before applying any integration update." if risky else "Revalidate the target package at the integration boundary before adoption."
        steps = [{"id": "validate-target-static", "action": "Revalidate the target descriptor using workflow-package.v1."}]
        if risky:
            steps.append({"id": "review-breaking-contract", "action": "Review restrictive typed, compatibility, requirement, parameter, or resource contracts manually."})
        steps.append({"id": "update-reference-manually", "action": "Apply any consumer reference change outside this dry-run planner."})
    result = {
        "valid": True,
        "status": status,
        "dry_run": True,
        "reason": reason,
        "action": action,
        "from": source,
        "to": target,
        "changes": changes,
        "steps": steps,
        "diff_digest": diff["deterministic_digest"],
        "execution": "not_run",
    }
    result["plan_digest"] = hashlib.sha256(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return result
