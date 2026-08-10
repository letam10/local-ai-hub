"""Dependency and capability preflight with no process or workload execution."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from src.services.capability_planner.planner import plan_resources
from src.shared.schemas.extension_manifest import (
    AVAILABILITY_STATUSES,
    ManifestValidationError,
    STATIC_METADATA_CAPABILITIES,
    compare_versions,
    is_semver,
    validate_extension_manifest,
    version_satisfies,
)


PREFLIGHT_VERSION = "extension-preflight.v1"
_STATUS_RANK = {"operational": 0, "planned": 1, "partial": 2, "unavailable": 3}
_STATUS_ALIASES = {
    "configured": "partial",
    "external_managed": "partial",
    "installed": "partial",
    "installed_or_configured": "partial",
    "not_installed": "unavailable",
    "reused": "partial",
}


def _combine(current: str, candidate: str) -> str:
    return candidate if _STATUS_RANK[candidate] > _STATUS_RANK[current] else current


def _inventory_index(value: Any) -> dict[str, dict[str, str]]:
    """Normalize inventory records and deliberately discard unknown local fields."""

    records: Iterable[Any]
    if isinstance(value, Mapping):
        records = [{**item, "id": identifier} if isinstance(item, Mapping) else {"id": identifier, "status": item} for identifier, item in value.items()]
    elif isinstance(value, list):
        records = value
    else:
        records = []
    result: dict[str, dict[str, str]] = {}
    for item in records:
        if not isinstance(item, Mapping) or not isinstance(item.get("id"), str) or not item["id"]:
            continue
        raw_status = item.get("status")
        status = raw_status if raw_status in AVAILABILITY_STATUSES else _STATUS_ALIASES.get(raw_status, "unavailable")
        normalized = {"status": status}
        if is_semver(item.get("version")):
            normalized["version"] = item["version"]
        result[item["id"]] = normalized
    return result


def _input_manifest(value: Mapping[str, Any]) -> tuple[dict[str, Any], Mapping[str, Any] | None]:
    record = value if isinstance(value, Mapping) and isinstance(value.get("manifest"), Mapping) else None
    candidate = record["manifest"] if record is not None else value
    return validate_extension_manifest(candidate), record


def _dependency_check(
    requirement: Mapping[str, Any],
    inventory: Mapping[str, Mapping[str, str]],
    *,
    kind: str,
) -> dict[str, Any]:
    identifier = requirement["id"]
    optional = bool(requirement.get("optional", False))
    record = inventory.get(identifier)
    if record is None:
        return {
            "kind": kind,
            "id": identifier,
            "optional": optional,
            "status": "partial" if optional else "unavailable",
            "reason": f"The declared {kind} is not present in the supplied inventory.",
            "action": f"Register or configure the required {kind} before executing this extension.",
        }
    status = record["status"]
    version = requirement.get("version")
    if version:
        actual = record.get("version")
        if actual is None:
            status = _combine(status, "partial")
            reason = f"The declared {kind} has no semantic version in the supplied inventory."
            action = f"Record and verify a compatible {kind} version before executing this extension."
        elif not version_satisfies(actual, version):
            status = "partial" if optional else "unavailable"
            reason = f"The declared {kind} does not meet its version constraint."
            action = f"Install or select a compatible {kind} version before executing this extension."
        else:
            reason = f"The declared {kind} satisfies its requested version constraint."
            action = "No version remediation is required."
    else:
        reason = f"The declared {kind} is listed in the supplied inventory."
        action = "No metadata remediation is required."
    if status != "operational" and "version" not in requirement:
        reason = f"The declared {kind} is present but its inventory status is {status}."
        action = f"Resolve the {kind} status before executing this extension."
    return {"kind": kind, "id": identifier, "optional": optional, "status": status, "reason": reason, "action": action}


def _compatibility_checks(manifest: Mapping[str, Any], hub_version: str | None, platform: str | None) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    hub = manifest["compatibility"]["hub"]
    if not is_semver(hub_version):
        checks.append(
            {
                "kind": "hub",
                "id": "local-ai-hub",
                "status": "partial",
                "reason": "No semantic Hub version was supplied to dry-run preflight.",
                "action": "Set hub_version in the extension configuration before asserting compatibility.",
            }
        )
    elif compare_versions(hub_version, hub["min_version"]) < 0 or (hub.get("max_version") and compare_versions(hub_version, hub["max_version"]) > 0):
        checks.append(
            {
                "kind": "hub",
                "id": "local-ai-hub",
                "status": "unavailable",
                "reason": "The supplied Hub version is outside the extension's declared compatibility range.",
                "action": "Use a compatible Hub release or update the extension manifest after validation.",
            }
        )
    else:
        checks.append(
            {"kind": "hub", "id": "local-ai-hub", "status": "operational", "reason": "The supplied Hub version is in the declared compatibility range.", "action": "No version remediation is required."}
        )
    if platform is None:
        checks.append(
            {
                "kind": "platform",
                "id": "host-platform",
                "status": "partial",
                "reason": "No host platform was supplied to dry-run preflight.",
                "action": "Set platform in the extension configuration before asserting compatibility.",
            }
        )
    elif platform not in manifest["compatibility"]["platforms"]:
        checks.append(
            {
                "kind": "platform",
                "id": "host-platform",
                "status": "unavailable",
                "reason": "The supplied host platform is not in the extension's declared compatibility list.",
                "action": "Use a supported platform or choose a compatible extension.",
            }
        )
    else:
        checks.append(
            {"kind": "platform", "id": "host-platform", "status": "operational", "reason": "The supplied host platform is declared compatible.", "action": "No platform remediation is required."}
        )
    return checks


def preflight_extension(
    value: Mapping[str, Any],
    *,
    components: Any = None,
    models: Any = None,
    hub_version: str | None = None,
    platform: str | None = None,
) -> dict[str, Any]:
    """Check declarative requirements without importing or starting an extension."""

    try:
        manifest, discovery_record = _input_manifest(value)
    except (ManifestValidationError, TypeError, AttributeError):
        return {
            "contract_version": PREFLIGHT_VERSION,
            "dry_run": True,
            "extension_id": None,
            "status": "unavailable",
            "reason": "Preflight requires a valid extension-manifest.v1 descriptor.",
            "action": "Correct the manifest and run static discovery before dependency preflight.",
            "checks": [],
            "actions": ["Correct the manifest and run static discovery before dependency preflight."],
        }
    component_inventory = _inventory_index(components)
    model_inventory = _inventory_index(models)
    checks: list[dict[str, Any]] = _compatibility_checks(manifest, hub_version, platform)
    checks.extend(_dependency_check(item, component_inventory, kind="component") for item in manifest["required_components"])
    checks.extend(_dependency_check(item, model_inventory, kind="model") for item in manifest["required_models"])
    status = manifest["availability"]["status"]
    reason = manifest["availability"]["reason"]
    action = manifest["availability"]["action"]
    if discovery_record is not None and discovery_record.get("status") in AVAILABILITY_STATUSES:
        status = _combine(status, discovery_record["status"])
        if discovery_record["status"] != "operational":
            reason = "Static discovery has not established an operational extension state."
            action = "Resolve the static discovery findings before attempting a runtime capability."
    for check in checks:
        status = _combine(status, check["status"])
    runtime_capability_declared = bool(set(manifest["capabilities"]) - STATIC_METADATA_CAPABILITIES)
    if status == "operational" and (manifest["required_components"] or manifest["required_models"] or runtime_capability_declared):
        status = "partial"
        reason = "Dry-run preflight verified dependency metadata only; it did not execute or import a runtime capability."
        action = "Run a bounded, separately authorized functional smoke before claiming operational status."
    actions = sorted({action, *(check["action"] for check in checks if check["status"] != "operational")})
    return {
        "contract_version": PREFLIGHT_VERSION,
        "dry_run": True,
        "extension_id": manifest["id"],
        "status": status,
        "reason": reason,
        "action": action,
        "checks": checks,
        "actions": actions,
        "capabilities": list(manifest["capabilities"]),
        "resource_profile": manifest["resource_profile"],
    }


def preflight_extensions(
    discovery: Mapping[str, Any] | Iterable[Mapping[str, Any]],
    *,
    configuration: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Preflight discovered extensions and attach a dry-run mutual-exclusion plan."""

    records = discovery.get("extensions", []) if isinstance(discovery, Mapping) else discovery
    config = configuration if isinstance(configuration, Mapping) else {}
    components = config.get("components")
    models = config.get("models")
    hub_version = config.get("hub_version") if isinstance(config.get("hub_version"), str) else None
    platform = config.get("platform") if isinstance(config.get("platform"), str) else None
    enabled_setting = config.get("enabled_extensions")
    # An omitted setting means the caller intentionally wants a report/plan for
    # every discovered descriptor.  A supplied list is an explicit allowlist;
    # therefore an explicit empty list enables no extension and consumes no
    # resource capacity.
    enabled = {item for item in enabled_setting if isinstance(item, str)} if isinstance(enabled_setting, list) else None
    results: list[dict[str, Any]] = []
    planning_manifests: list[Mapping[str, Any]] = []
    eligible_results: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, Mapping):
            continue
        result = preflight_extension(record, components=components, models=models, hub_version=hub_version, platform=platform)
        planning_eligible = enabled is None or result["extension_id"] in enabled
        if not planning_eligible:
            result = dict(result)
            result["status"] = "planned"
            result["reason"] = "The extension is not enabled in the supplied extension configuration."
            result["action"] = "Add the extension id to enabled_extensions after reviewing its dry-run report."
            result["actions"] = sorted(set([*result["actions"], result["action"]]))
        result["planning_eligible"] = planning_eligible
        results.append(result)
        if planning_eligible:
            eligible_results.append(result)
        if planning_eligible and isinstance(record.get("manifest"), Mapping):
            try:
                planning_manifests.append(validate_extension_manifest(record["manifest"]))
            except ManifestValidationError:
                pass
    status = "planned" if enabled is not None and not eligible_results else "operational"
    for result in eligible_results:
        status = _combine(status, result["status"])
    resource_plan = plan_resources(planning_manifests, hardware=config.get("hardware") if isinstance(config, Mapping) else None)
    if eligible_results:
        status = _combine(status, resource_plan["status"])
    counts = {item: sum(1 for result in results if result["status"] == item) for item in ("operational", "partial", "unavailable", "planned")}
    return {
        "contract_version": PREFLIGHT_VERSION,
        "dry_run": True,
        "status": status,
        "planning_scope": "enabled_extensions" if enabled is not None else "all_discovered_extensions",
        "extensions": results,
        "counts": counts,
        "resource_plan": resource_plan,
    }
