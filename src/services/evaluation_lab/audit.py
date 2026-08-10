"""Scrubbed provenance reports built only from revalidated package contracts."""

from __future__ import annotations

import copy
from typing import Any

from src.services.workflow_packages.diffing import plan_workflow_migration
from src.services.workflow_packages.io import validated_package_result

from .scenario import _validated_scenario


def _blueprint_summary(blueprint: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": blueprint["id"],
        "nodes": len(blueprint["nodes"]),
        "edges": len(blueprint["edges"]),
        "input_types": sorted({item["type"] for item in blueprint["inputs"]}),
        "output_types": sorted({item["type"] for item in blueprint["outputs"]}),
    }


def build_package_audit(package_value: object, *, scenario_value: object = None, previous_package_value: object = None) -> dict[str, Any]:
    """Build scrubbed JSON provenance without accepting raw report mappings.

    The only optional inputs are package/scenario descriptors, which are always
    revalidated before projection.  No host path, user identity, raw payload,
    command, artifact, benchmark result, or runtime claim enters the output.
    """

    package_validation = validated_package_result(package_value)
    if not package_validation["valid"]:
        return {
            "valid": False,
            "status": "unavailable",
            "reason": "Package failed static validation and no provenance was projected.",
            "action": "Correct the static package contract before producing audit output.",
            "errors": [{"code": item["code"], "location": item["location"]} for item in package_validation["errors"]],
        }
    package = package_validation["package"]
    assert isinstance(package, dict)
    audit: dict[str, Any] = {
        "contract": "workflow-package-audit.v1",
        "valid": True,
        "status": "partial",
        "reason": "Static descriptor provenance is available; runtime execution has not been performed.",
        "action": "Use this report for review and run a separately authorized bounded smoke before claiming runtime support.",
        "package": {
            "id": package["id"],
            "version": package["version"],
            "fingerprint": package_validation["fingerprint"],
            "capabilities": sorted(package["capabilities"]),
            "hub_compatibility": package["compatibility"]["hub_version"],
            "node_contract": package["compatibility"]["node_contract"],
            "required_node_types": sorted(package["compatibility"]["required_node_types"]),
            "catalog_ready": package["catalog_ready"],
        },
        "integration_contract": {
            "parameters": [{"id": item["id"], "type": item["type"]} for item in sorted(package["parameters"], key=lambda item: item["id"])],
            "model_requirement_ids": [item["id"] for item in sorted(package["requirements"]["models"], key=lambda item: item["id"])],
            "runtime_requirement_ids": [item["id"] for item in sorted(package["requirements"]["runtimes"], key=lambda item: item["id"])],
            "resource_hints": {
                "cpu": {
                    "minimum_cores": package["resource_hints"]["cpu"]["minimum_cores"],
                    "recommended_cores": package["resource_hints"]["cpu"]["recommended_cores"],
                },
                "gpu": {
                    "required": package["resource_hints"]["gpu"]["required"],
                    "vendor": package["resource_hints"]["gpu"]["vendor"],
                    "minimum_vram_mb": package["resource_hints"]["gpu"]["minimum_vram_mb"],
                },
                "ram": copy.deepcopy(package["resource_hints"]["ram"]),
                "disk": copy.deepcopy(package["resource_hints"]["disk"]),
                "exclusive_groups": sorted(package["resource_hints"]["exclusive_groups"]),
            },
            "preview": {
                "id": package["preview"]["id"],
                "content_type": package["preview"]["content_type"],
            },
        },
        "blueprints": {
            "workflow": _blueprint_summary(package["workflow"]),
            "subgraphs": [_blueprint_summary(item) for item in sorted(package["subgraphs"], key=lambda item: item["id"])],
        },
        "provenance": [
            {"kind": "static_validation", "status": "valid"},
            {"kind": "descriptor_fingerprint", "sha256": package_validation["fingerprint"]},
            {"kind": "execution", "status": "not_run"},
        ],
        "execution": "not_run",
    }
    if scenario_value is not None:
        scenario_validation = _validated_scenario(scenario_value)
        if scenario_validation["valid"]:
            scenario = scenario_validation["scenario"]
            assert isinstance(scenario, dict)
            if scenario["package"]["id"] == package["id"]:
                audit["evaluation"] = {
                    "status": "planned" if scenario["package"]["version"] == package["version"] else "partial",
                    "scenario_id": scenario["id"],
                    "fingerprint": scenario_validation["fingerprint"],
                    "criteria_count": len(scenario["rubric"]),
                    "human_review_required": True,
                    "execution": "not_run",
                }
            else:
                audit["evaluation"] = {"status": "unavailable", "error_codes": ["scenario_package_mismatch"], "execution": "not_run"}
        else:
            audit["evaluation"] = {"status": "unavailable", "error_codes": sorted({item["code"] for item in scenario_validation["errors"]}), "execution": "not_run"}
    if previous_package_value is not None:
        migration = plan_workflow_migration(previous_package_value, package)
        audit["migration"] = {
            "status": migration["status"],
            "dry_run": bool(migration["dry_run"]),
            "step_ids": [item["id"] for item in migration["steps"]],
            "diff_digest": migration.get("diff_digest"),
            "plan_digest": migration.get("plan_digest"),
            "execution": "not_run",
        }
    return copy.deepcopy(audit)


def build_package_audit_markdown(package_value: object, *, scenario_value: object = None, previous_package_value: object = None) -> str:
    """Render only fixed labels and schema-validated identifiers into Markdown."""

    audit = build_package_audit(package_value, scenario_value=scenario_value, previous_package_value=previous_package_value)
    if not audit["valid"]:
        codes = ", ".join(sorted(item["code"] for item in audit["errors"])) or "validation_failed"
        return "# Workflow Package Audit\n\nStatus: unavailable\n\nStatic validation codes: " + codes + "\n"
    package = audit["package"]
    workflow = audit["blueprints"]["workflow"]
    lines = [
        "# Workflow Package Audit",
        "",
        f"Status: {audit['status']}",
        "",
        "## Package",
        "",
        f"- ID: `{package['id']}`",
        f"- Version: `{package['version']}`",
        f"- Descriptor SHA-256: `{package['fingerprint']}`",
        f"- Catalog ready: `{str(package['catalog_ready']).lower()}`",
        f"- Execution: `{audit['execution']}`",
        "",
        "## Typed blueprint summary",
        "",
        f"- Workflow: `{workflow['id']}` ({workflow['nodes']} nodes, {workflow['edges']} edges)",
        f"- Subgraphs: {len(audit['blueprints']['subgraphs'])}",
        "",
        "## Static integration contract",
        "",
        f"- Parameters: {len(audit['integration_contract']['parameters'])}",
        f"- Model requirements: {len(audit['integration_contract']['model_requirement_ids'])}",
        f"- Runtime requirements: {len(audit['integration_contract']['runtime_requirement_ids'])}",
        f"- GPU required: `{str(audit['integration_contract']['resource_hints']['gpu']['required']).lower()}`",
        f"- Exclusive resource groups: {len(audit['integration_contract']['resource_hints']['exclusive_groups'])}",
    ]
    if "evaluation" in audit:
        evaluation = audit["evaluation"]
        lines.extend(["", "## Evaluation", "", f"- Status: `{evaluation['status']}`", f"- Execution: `{evaluation['execution']}`"])
    if "migration" in audit:
        migration = audit["migration"]
        lines.extend(["", "## Migration dry-run", "", f"- Status: `{migration['status']}`", f"- Dry run: `{str(migration['dry_run']).lower()}`"])
    return "\n".join(lines) + "\n"
