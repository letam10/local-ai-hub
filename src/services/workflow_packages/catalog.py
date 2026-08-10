"""Repository-managed static package discovery and preflight projections."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from src.shared.schemas.workflow_package import IDENTIFIER_RE, PACKAGE_ID_RE, SEMVER_RE, validate_evaluation_scenario

from .io import safe_import_workflow_package
from .linting import lint_workflow_package


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MANAGED_PACKAGE_ROOT = REPOSITORY_ROOT / "workflow_packages"


def _contained(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _read_managed_json(path: Path) -> bytes | None:
    if path.is_symlink() or not _contained(path, MANAGED_PACKAGE_ROOT):
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def _package_record(imported: dict[str, Any]) -> dict[str, Any]:
    package = imported["package"]
    assert isinstance(package, dict)
    lint = lint_workflow_package(package)
    return {
        "id": package["id"],
        "version": package["version"],
        "title": package["title"],
        "capabilities": list(package["capabilities"]),
        "required_node_types": list(package["compatibility"]["required_node_types"]),
        "fingerprint": imported["fingerprint"],
        "lint_status": lint["status"],
        "availability": {
            "status": "partial",
            "reason": "The managed descriptor passed static validation, but no runtime graph smoke has been authorized.",
            "action": "Use server-owned validated output for a later bounded integration preflight.",
        },
    }


def discover_managed_packages() -> dict[str, Any]:
    """Read only descriptors tracked below the fixed repository package root."""

    records: list[dict[str, Any]] = []
    scenarios: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    if not MANAGED_PACKAGE_ROOT.is_dir():
        return {
            "status": "unavailable",
            "reason": "The managed workflow package directory is unavailable.",
            "action": "Restore the repository-managed package directory and run static validation.",
            "records": [],
            "scenarios": [],
            "errors": [{"code": "managed_root_unavailable"}],
        }
    for path in sorted(MANAGED_PACKAGE_ROOT.rglob("*.workflow-package.json")):
        payload = _read_managed_json(path)
        if payload is None:
            errors.append({"code": "managed_descriptor_refused"})
            continue
        imported = safe_import_workflow_package(payload)
        if not imported["accepted"]:
            errors.append({"code": "managed_package_invalid"})
            continue
        records.append(_package_record(imported))
    for path in sorted(MANAGED_PACKAGE_ROOT.rglob("*.evaluation-scenario.json")):
        payload = _read_managed_json(path)
        if payload is None:
            errors.append({"code": "managed_scenario_refused"})
            continue
        try:
            scenario = json.loads(payload.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, ValueError):
            errors.append({"code": "managed_scenario_invalid"})
            continue
        validation = validate_evaluation_scenario(scenario)
        if not validation["valid"]:
            errors.append({"code": "managed_scenario_invalid"})
            continue
        normalized = validation["scenario"]
        assert isinstance(normalized, dict)
        scenarios.append({"id": normalized["id"], "package": copy.deepcopy(normalized["package"]), "fingerprint": validation["fingerprint"], "execution": "not_run"})
    records.sort(key=lambda item: (item["id"], item["version"]))
    scenarios.sort(key=lambda item: item["id"])
    return {
        "status": "partial" if records else "unavailable",
        "reason": "Catalog output is static only and does not execute imported graphs.",
        "action": "Run the static package CLI; reserve runtime checks for a separately authorized bounded smoke.",
        "records": records,
        "scenarios": scenarios,
        "errors": sorted(errors, key=lambda item: item["code"]),
    }


def load_managed_package(package_id: object, version: object = None) -> dict[str, Any]:
    """Return a detached validated package selected by opaque ID/version only."""

    if not isinstance(package_id, str) or not PACKAGE_ID_RE.fullmatch(package_id):
        return {"found": False, "status": "unavailable", "reason": "Package ID is invalid.", "action": "Use a declared managed package ID."}
    if version is not None and (not isinstance(version, str) or not SEMVER_RE.fullmatch(version)):
        return {"found": False, "status": "unavailable", "reason": "Package version is invalid.", "action": "Use an exact declared package version."}
    if not MANAGED_PACKAGE_ROOT.is_dir():
        return {"found": False, "status": "unavailable", "reason": "The managed package catalog is unavailable.", "action": "Restore the repository-managed catalog."}
    candidates: list[dict[str, Any]] = []
    for path in sorted(MANAGED_PACKAGE_ROOT.rglob("*.workflow-package.json")):
        payload = _read_managed_json(path)
        if payload is None:
            continue
        imported = safe_import_workflow_package(payload)
        package = imported.get("package")
        if imported.get("accepted") and isinstance(package, dict) and package["id"] == package_id and (version is None or package["version"] == version):
            candidates.append(imported)
    if not candidates:
        return {"found": False, "status": "unavailable", "reason": "No matching managed package passed static validation.", "action": "Check the managed catalog ID and version."}
    selected = sorted(candidates, key=lambda item: str(item["package"]["version"]))[-1]
    return {
        "found": True,
        "status": "partial",
        "reason": "The descriptor is valid statically; runtime execution remains unverified.",
        "action": "Use the returned server-owned package only for static planning or an authorized preflight.",
        "package": copy.deepcopy(selected["package"]),
        "fingerprint": selected["fingerprint"],
    }


def preflight_workflow_package(value: object, supported_node_types: object = None) -> dict[str, Any]:
    """Evaluate declared node requirements only; this never runs a workflow."""

    from .io import validated_package_result

    validation = validated_package_result(value)
    if not validation["valid"]:
        return {
            "status": "unavailable",
            "reason": "Package did not pass static validation.",
            "action": "Correct the static contract errors before integration.",
            "missing_node_types": [],
        }
    package = validation["package"]
    assert isinstance(package, dict)
    declared = set(package["compatibility"]["required_node_types"])
    if supported_node_types is None:
        return {
            "status": "partial",
            "reason": "No host node capability snapshot was supplied, so only static package validity is known.",
            "action": "Provide a server-owned host capability snapshot for a static requirement check.",
            "missing_node_types": [],
            "execution": "not_run",
        }
    if not isinstance(supported_node_types, (list, tuple, set)) or not all(isinstance(item, str) and IDENTIFIER_RE.fullmatch(item) for item in supported_node_types):
        return {
            "status": "unavailable",
            "reason": "Host node capability input is not a bounded typed snapshot.",
            "action": "Pass only server-owned node type identifiers to preflight.",
            "missing_node_types": [],
            "execution": "not_run",
        }
    missing = sorted(declared - set(supported_node_types))
    return {
        "status": "unavailable" if missing else "partial",
        "reason": "Required typed node declarations are absent from the host snapshot." if missing else "Static requirements match the host snapshot; runtime graph execution remains unverified.",
        "action": "Install or expose the missing typed node contracts before integration." if missing else "Run a separately authorized bounded smoke before claiming operational runtime support.",
        "missing_node_types": missing,
        "execution": "not_run",
    }
