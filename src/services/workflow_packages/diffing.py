"""Deterministic descriptor comparison and dry-run migration planning."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from src.shared.schemas.workflow_package import canonical_workflow_package_json

from .io import validated_package_result


_SEMVER_PARTS = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


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


def diff_workflow_packages(before: object, after: object) -> dict[str, Any]:
    """Compare two static packages without exposing old/new free-text values."""

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
    source_operations = set(source_package["compatibility"]["required_node_types"])
    target_operations = set(target_package["compatibility"]["required_node_types"])
    for operation in sorted(target_operations - source_operations):
        changes.append(_change("required_node_type_added", entity_id=operation))
    for operation in sorted(source_operations - target_operations):
        changes.append(_change("required_node_type_removed", entity_id=operation))
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


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = _SEMVER_PARTS.match(value)
    if match is None:
        return (0, 0, 0)
    return tuple(int(item) for item in match.groups())


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
        }
    if _version_tuple(target["version"]) < _version_tuple(source["version"]):
        return {
            "valid": True,
            "status": "unavailable",
            "dry_run": True,
            "reason": "Downgrade migrations are not inferred by the static planner.",
            "action": "Keep the newer package unchanged or supply a separately reviewed rollback contract.",
            "steps": [],
            "diff_digest": diff["deterministic_digest"],
        }
    changes = diff["changes"]
    if not changes:
        status = "not_required"
        reason = "Descriptors are semantically unchanged."
        action = "No migration action is required."
        steps: list[dict[str, str]] = []
    else:
        risky_prefixes = ("blueprint_removed", "input_removed", "output_removed", "node_removed", "node_changed", "required_node_type_removed", "capability_removed")
        risky = any(item["kind"].startswith(risky_prefixes) for item in changes)
        status = "manual_review" if risky else "planned"
        reason = "The dry-run found contract changes that require a human review." if risky else "The dry-run found additive static descriptor changes."
        action = "Review the listed contract changes before applying any integration update." if risky else "Revalidate the target package at the integration boundary before adoption."
        steps = [{"id": "validate-target-static", "action": "Revalidate the target descriptor using workflow-package.v1."}]
        if risky:
            steps.append({"id": "review-breaking-contract", "action": "Review removed or changed typed interfaces manually."})
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
