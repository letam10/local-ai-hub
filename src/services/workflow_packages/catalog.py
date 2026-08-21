"""Repository-managed static package discovery and preflight projections."""

from __future__ import annotations

import copy
import json
import os
import stat
from functools import cmp_to_key
from pathlib import Path
from typing import Any

from src.shared.schemas.workflow_package import (
    IDENTIFIER_RE,
    MAX_PACKAGE_BYTES,
    PACKAGE_ID_RE,
    SEMVER_RE,
    VERSION_CONSTRAINT_RE,
    compare_semver,
    validate_evaluation_scenario,
)

from .io import _DuplicateJsonKey, _duplicate_key_guard, _non_finite_number, safe_import_workflow_package
from .linting import lint_workflow_package


_MAX_WALK_DEPTH = 16
_MAX_WALK_ENTRIES = 4096
_MAX_ANCESTOR_DEPTH = 64
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class _ManagedPathRefused(Exception):
    """Internal fixed-code refusal; never expose filesystem details."""


REPOSITORY_ROOT = Path(os.path.abspath(os.fspath(Path(__file__)))).parents[3]
MANAGED_PACKAGE_ROOT = REPOSITORY_ROOT / "workflow_packages"


def _absolute_without_resolve(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _is_reparse(result: os.stat_result) -> bool:
    return stat.S_ISLNK(result.st_mode) or bool(getattr(result, "st_file_attributes", 0) & _REPARSE_POINT)


def _identity(result: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        int(getattr(result, "st_dev", 0)),
        int(getattr(result, "st_ino", 0)),
        int(result.st_mode),
        int(result.st_size),
        int(getattr(result, "st_mtime_ns", 0)),
        int(getattr(result, "st_ctime_ns", 0)),
        int(getattr(result, "st_file_attributes", 0)),
    )


def _lstat_checked(path: Path, *, kind: str) -> tuple[int, int, int, int, int, int, int]:
    try:
        result = os.lstat(path)
    except (OSError, TypeError, ValueError) as exc:
        raise _ManagedPathRefused from exc
    if _is_reparse(result):
        raise _ManagedPathRefused
    if kind == "directory" and not stat.S_ISDIR(result.st_mode):
        raise _ManagedPathRefused
    if kind == "file" and not stat.S_ISREG(result.st_mode):
        raise _ManagedPathRefused
    return _identity(result)


def _contained(path: Path, root: Path) -> bool:
    try:
        _absolute_without_resolve(path).relative_to(_absolute_without_resolve(root))
        return True
    except (OSError, TypeError, ValueError):
        return False


def _directory_chain(root: Path, target_parent: Path) -> dict[Path, tuple[int, int, int, int, int, int, int]]:
    """Validate the fixed root, its ancestors, and every target-parent directory."""

    root_abs = _absolute_without_resolve(root)
    parent_abs = _absolute_without_resolve(target_parent)
    if not _contained(parent_abs, root_abs):
        raise _ManagedPathRefused
    try:
        parent_abs.relative_to(root_abs)
    except ValueError as exc:
        raise _ManagedPathRefused from exc
    identities: dict[Path, tuple[int, int, int, int, int, int, int]] = {}
    current = parent_abs
    depth = 0
    while True:
        identities[current] = _lstat_checked(current, kind="directory")
        if current == root_abs:
            break
        current = current.parent
        depth += 1
        if depth > _MAX_ANCESTOR_DEPTH:
            raise _ManagedPathRefused
    current = root_abs.parent
    depth = 0
    while current.parent != current:
        identities[current] = _lstat_checked(current, kind="directory")
        current = current.parent
        depth += 1
        if depth > _MAX_ANCESTOR_DEPTH:
            raise _ManagedPathRefused
    return identities


def _read_managed_json(path: Path) -> tuple[bytes | None, str | None]:
    """Read one fixed-root descriptor only after containment and size checks."""

    try:
        path_abs = _absolute_without_resolve(path)
        root_abs = _absolute_without_resolve(MANAGED_PACKAGE_ROOT)
        if path_abs == root_abs or not _contained(path_abs, root_abs):
            raise _ManagedPathRefused
        chain_before = _directory_chain(root_abs, path_abs.parent)
        before = _lstat_checked(path_abs, kind="file")
        size = before[3]
    except (OSError, TypeError, ValueError, _ManagedPathRefused):
        return None, "managed_descriptor_refused"
    if size <= 0 or size > MAX_PACKAGE_BYTES:
        return None, "managed_descriptor_size"
    try:
        payload = path_abs.read_bytes()
    except (OSError, TypeError, ValueError):
        return None, "managed_descriptor_refused"
    try:
        chain_after = _directory_chain(root_abs, path_abs.parent)
        after = _lstat_checked(path_abs, kind="file")
    except (OSError, TypeError, ValueError, _ManagedPathRefused):
        return None, "managed_descriptor_refused"
    # Defend against same-size/same-byte replacement and ancestor replacement.
    if chain_before != chain_after or before != after:
        return None, "managed_descriptor_refused"
    if len(payload) != size or len(payload) > MAX_PACKAGE_BYTES:
        return None, "managed_descriptor_size"
    return payload, None


def _walk_managed_files(root: Path, suffix: str) -> tuple[bool, list[Path], list[str]]:
    """Bounded no-follow walk returning only regular files under a safe root."""

    try:
        root_abs = _absolute_without_resolve(root)
        _directory_chain(root_abs, root_abs)
    except (OSError, TypeError, ValueError, _ManagedPathRefused):
        return False, [], ["managed_root_unavailable"]

    paths: list[Path] = []
    errors: list[str] = []
    stack: list[tuple[Path, int]] = [(root_abs, 0)]
    seen_entries = 0
    while stack:
        directory, depth = stack.pop()
        try:
            chain_before = _directory_chain(root_abs, directory)
            entries = []
            bounded = True
            with os.scandir(directory) as iterator:
                for entry in iterator:
                    if len(entries) >= _MAX_WALK_ENTRIES:
                        bounded = False
                        break
                    entries.append(entry)
        except (OSError, TypeError, ValueError, _ManagedPathRefused):
            errors.append("managed_directory_refused")
            continue
        if not bounded or seen_entries + len(entries) > _MAX_WALK_ENTRIES:
            errors.append("managed_catalog_bounds")
            try:
                _directory_chain(root_abs, directory)
            except (OSError, TypeError, ValueError, _ManagedPathRefused):
                errors.append("managed_directory_refused")
            continue
        seen_entries += len(entries)
        local_paths: list[Path] = []
        local_errors: list[str] = []
        for entry in sorted(entries, key=lambda item: item.name):
            try:
                entry_path = directory / entry.name
                result = os.lstat(entry_path)
            except (OSError, TypeError, ValueError):
                local_errors.append("managed_entry_refused")
                continue
            if _is_reparse(result):
                local_errors.append("managed_entry_refused")
                continue
            if stat.S_ISDIR(result.st_mode):
                if entry.name.endswith(suffix) or depth >= _MAX_WALK_DEPTH:
                    local_errors.append("managed_entry_refused" if entry.name.endswith(suffix) else "managed_catalog_bounds")
                    continue
                stack.append((entry_path, depth + 1))
                continue
            if not stat.S_ISREG(result.st_mode):
                local_errors.append("managed_entry_refused")
                continue
            if entry.name.endswith(suffix):
                local_paths.append(entry_path)
        try:
            chain_after = _directory_chain(root_abs, directory)
            if chain_before != chain_after:
                errors.append("managed_directory_changed")
                continue
        except (OSError, TypeError, ValueError, _ManagedPathRefused):
            errors.append("managed_directory_refused")
            continue
        paths.extend(local_paths)
        errors.extend(local_errors)
    try:
        _directory_chain(root_abs, root_abs)
    except (OSError, TypeError, ValueError, _ManagedPathRefused):
        errors.append("managed_root_changed")
    return True, sorted(paths, key=lambda item: str(item)), errors


def _deduplicated_errors(errors: list[str]) -> list[dict[str, str]]:
    return [{"code": code} for code in sorted(set(errors))]


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
        "catalog_ready": True,
        "integration_contract": {
            "parameter_ids": sorted(item["id"] for item in package["parameters"]),
            "model_requirement_ids": sorted(item["id"] for item in package["requirements"]["models"]),
            "runtime_requirement_ids": sorted(item["id"] for item in package["requirements"]["runtimes"]),
            "exclusive_resource_groups": sorted(package["resource_hints"]["exclusive_groups"]),
            "preview": {
                "id": package["preview"]["id"],
                "content_type": package["preview"]["content_type"],
            },
        },
        "lint_status": lint["status"],
        "availability": {
            "status": "partial",
            "reason": "The managed descriptor passed static validation, but no runtime graph smoke has been authorized.",
            "action": "Use server-owned validated output for a later bounded integration preflight.",
        },
    }


def _scan_managed_packages() -> dict[str, Any]:
    """Collect unique, catalog-ready managed packages without using filenames as IDs."""

    root_available, package_paths, walk_errors = _walk_managed_files(MANAGED_PACKAGE_ROOT, ".workflow-package.json")
    if not root_available:
        return {
            "root_available": False,
            "packages": {},
            "ambiguous": set(),
            "not_ready": set(),
            "errors": walk_errors or ["managed_root_unavailable"],
        }
    errors: list[str] = list(walk_errors)
    candidates: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for path in package_paths:
        payload, error = _read_managed_json(path)
        if error is not None:
            errors.append(error)
            continue
        assert payload is not None
        imported = safe_import_workflow_package(payload)
        if not imported["accepted"]:
            errors.append("managed_package_invalid")
            continue
        package = imported["package"]
        assert isinstance(package, dict)
        identity = (package["id"], package["version"])
        candidates.setdefault(identity, []).append(imported)
    packages: dict[tuple[str, str], dict[str, Any]] = {}
    ambiguous: set[tuple[str, str]] = set()
    not_ready: set[tuple[str, str]] = set()
    for identity in sorted(candidates):
        values = candidates[identity]
        if len(values) != 1:
            ambiguous.add(identity)
            errors.append("managed_package_identity_ambiguous")
            continue
        imported = values[0]
        package = imported["package"]
        assert isinstance(package, dict)
        if package["catalog_ready"] is not True:
            not_ready.add(identity)
            errors.append("managed_package_not_catalog_ready")
            continue
        packages[identity] = imported
    return {
        "root_available": True,
        "packages": packages,
        "ambiguous": ambiguous,
        "not_ready": not_ready,
        "errors": errors,
    }


def _reference_state(identity: tuple[str, str], scan: dict[str, Any]) -> str:
    if identity in scan["ambiguous"]:
        return "ambiguous"
    if identity in scan["not_ready"]:
        return "not_ready"
    if identity in scan["packages"]:
        return "available"
    return "missing"


def discover_managed_packages() -> dict[str, Any]:
    """Read only unambiguous catalog-ready descriptors below the fixed root."""

    scan = _scan_managed_packages()
    if not scan["root_available"]:
        return {
            "status": "unavailable",
            "reason": "The managed workflow package directory is unavailable.",
            "action": "Restore the repository-managed package directory and run static validation.",
            "records": [],
            "scenarios": [],
            "errors": _deduplicated_errors(scan["errors"]),
            "execution": "not_run",
        }
    records = [_package_record(imported) for _identity, imported in sorted(scan["packages"].items())]
    scenarios: list[dict[str, Any]] = []
    errors: list[str] = list(scan["errors"])
    scenario_root_available, scenario_paths, scenario_errors = _walk_managed_files(MANAGED_PACKAGE_ROOT, ".evaluation-scenario.json")
    errors.extend(scenario_errors)
    if not scenario_root_available:
        errors.append("managed_scenario_refused")
    for path in scenario_paths:
        payload, error = _read_managed_json(path)
        if error is not None:
            errors.append("managed_scenario_size" if error == "managed_descriptor_size" else "managed_scenario_refused")
            continue
        assert payload is not None
        try:
            scenario = json.loads(payload.decode("utf-8", errors="strict"), object_pairs_hook=_duplicate_key_guard, parse_constant=_non_finite_number)
        except (_DuplicateJsonKey, UnicodeDecodeError, ValueError):
            errors.append("managed_scenario_invalid")
            continue
        validation = validate_evaluation_scenario(scenario)
        if not validation["valid"]:
            errors.append("managed_scenario_invalid")
            continue
        normalized = validation["scenario"]
        assert isinstance(normalized, dict)
        references = [(normalized["package"]["id"], normalized["package"]["version"])]
        references.extend((normalized["package"]["id"], candidate["package_version"]) for candidate in normalized["candidates"])
        states = {_reference_state(reference, scan) for reference in references}
        if "ambiguous" in states:
            errors.append("managed_scenario_reference_ambiguous")
            continue
        if states != {"available"}:
            errors.append("managed_scenario_reference_unavailable")
            continue
        scenarios.append(
            {
                "id": normalized["id"],
                "package": copy.deepcopy(normalized["package"]),
                "candidate_versions": [candidate["package_version"] for candidate in sorted(normalized["candidates"], key=lambda item: item["id"])],
                "fingerprint": validation["fingerprint"],
                "execution": "not_run",
            }
        )
    records.sort(key=lambda item: (item["id"], cmp_to_key(compare_semver)(item["version"])))
    scenarios.sort(key=lambda item: item["id"])
    return {
        "status": "partial" if records else "unavailable",
        "reason": "Catalog output is static only and does not execute imported graphs.",
        "action": "Run the static package CLI; reserve runtime checks for a separately authorized bounded smoke.",
        "records": records,
        "scenarios": scenarios,
        "errors": _deduplicated_errors(errors),
        "execution": "not_run",
    }


def load_managed_package(package_id: object, version: object = None) -> dict[str, Any]:
    """Return a detached validated package by an unambiguous ID/version identity."""

    if not isinstance(package_id, str) or not PACKAGE_ID_RE.fullmatch(package_id):
        return {"found": False, "status": "unavailable", "reason": "Package ID is invalid.", "action": "Use a declared managed package ID."}
    if version is not None and (not isinstance(version, str) or not SEMVER_RE.fullmatch(version)):
        return {"found": False, "status": "unavailable", "reason": "Package version is invalid.", "action": "Use an exact declared package version."}
    scan = _scan_managed_packages()
    if not scan["root_available"]:
        return {"found": False, "status": "unavailable", "reason": "The managed package catalog is unavailable.", "action": "Restore the repository-managed catalog."}
    if version is not None:
        identity = (package_id, version)
        if identity in scan["ambiguous"]:
            return {"found": False, "status": "unavailable", "reason": "The requested package identity is ambiguous in the managed catalog.", "action": "Remove duplicate managed descriptors with the same package ID and version."}
        if identity in scan["not_ready"]:
            return {"found": False, "status": "unavailable", "reason": "The requested package is not catalog-ready.", "action": "Complete its public port bindings before catalog integration."}
        selected = scan["packages"].get(identity)
    else:
        if any(candidate_id == package_id for candidate_id, _candidate_version in scan["ambiguous"]):
            return {"found": False, "status": "unavailable", "reason": "Matching managed package versions include an ambiguous identity.", "action": "Remove duplicate managed descriptors before selecting a latest package version."}
        matching = [imported for (candidate_id, _candidate_version), imported in scan["packages"].items() if candidate_id == package_id]
        if not matching:
            return {"found": False, "status": "unavailable", "reason": "No matching managed package passed static validation.", "action": "Check the managed catalog ID and version."}
        selected = sorted(
            matching,
            key=cmp_to_key(lambda left, right: compare_semver(str(left["package"]["version"]), str(right["package"]["version"]))),
        )[-1]
    if selected is None:
        return {"found": False, "status": "unavailable", "reason": "No matching managed package passed static validation.", "action": "Check the managed catalog ID and version."}
    return {
        "found": True,
        "status": "partial",
        "reason": "The descriptor is valid statically; runtime execution remains unverified.",
        "action": "Use the returned server-owned package only for static planning or an authorized preflight.",
        "package": copy.deepcopy(selected["package"]),
        "fingerprint": selected["fingerprint"],
    }


def _safe_requirement_snapshot(value: object, *, runtime: bool) -> tuple[dict[str, str] | None, str | None]:
    if not isinstance(value, list) or len(value) > 64:
        return None, "host_requirement_snapshot"
    result: dict[str, str] = {}
    for item in value:
        if not isinstance(item, dict) or set(item) != {"id", "version"}:
            return None, "host_requirement_snapshot"
        item_id = item.get("id")
        item_version = item.get("version")
        if not isinstance(item_id, str) or not IDENTIFIER_RE.fullmatch(item_id) or not isinstance(item_version, str) or not SEMVER_RE.fullmatch(item_version):
            return None, "host_requirement_snapshot"
        if item_id in result:
            return None, "host_requirement_snapshot"
        result[item_id] = item_version
    return result, None


def _version_satisfies(installed: str, constraint: str) -> bool:
    if not VERSION_CONSTRAINT_RE.fullmatch(constraint):
        return False
    if constraint.startswith(">="):
        return compare_semver(installed, constraint[2:]) >= 0
    if constraint.startswith(">"):
        return compare_semver(installed, constraint[1:]) > 0
    if constraint.startswith("="):
        return compare_semver(installed, constraint[1:]) == 0
    return compare_semver(installed, constraint) == 0


def _resource_snapshot_gaps(hints: dict[str, Any], snapshot: object) -> tuple[list[str] | None, str | None]:
    if not isinstance(snapshot, dict) or set(snapshot) != {"cpu_cores", "gpu", "ram_mb", "disk_mb", "exclusive_groups"}:
        return None, "host_resource_snapshot"
    numeric_fields = ("cpu_cores", "ram_mb", "disk_mb")
    if any(isinstance(snapshot.get(field), bool) or not isinstance(snapshot.get(field), int) or snapshot[field] < 0 for field in numeric_fields):
        return None, "host_resource_snapshot"
    groups = snapshot.get("exclusive_groups")
    if not isinstance(groups, list) or len(groups) > 64 or not all(isinstance(group, str) and IDENTIFIER_RE.fullmatch(group) for group in groups) or len(set(groups)) != len(groups):
        return None, "host_resource_snapshot"
    gpu = snapshot.get("gpu")
    if not isinstance(gpu, dict) or set(gpu) != {"available", "vendor", "vram_mb"}:
        return None, "host_resource_snapshot"
    if not isinstance(gpu.get("available"), bool) or gpu.get("vendor") not in {"any", "none", "nvidia"} or isinstance(gpu.get("vram_mb"), bool) or not isinstance(gpu.get("vram_mb"), int) or gpu["vram_mb"] < 0:
        return None, "host_resource_snapshot"
    gaps: list[str] = []
    if snapshot["cpu_cores"] < hints["cpu"]["minimum_cores"]:
        gaps.append("cpu_cores")
    if snapshot["ram_mb"] < hints["ram"]["minimum_mb"]:
        gaps.append("ram_mb")
    if snapshot["disk_mb"] < hints["disk"]["minimum_mb"]:
        gaps.append("disk_mb")
    required_gpu = hints["gpu"]
    if required_gpu["required"]:
        if not gpu["available"]:
            gaps.append("gpu")
        elif required_gpu["vendor"] != "any" and gpu["vendor"] != required_gpu["vendor"]:
            gaps.append("gpu_vendor")
        elif gpu["vram_mb"] < required_gpu["minimum_vram_mb"]:
            gaps.append("gpu_vram_mb")
    if set(hints["exclusive_groups"]) & set(groups):
        gaps.append("exclusive_resource_group")
    return gaps, None


def preflight_workflow_package(
    value: object,
    supported_node_types: object = None,
    *,
    host_requirements: object = None,
    resource_snapshot: object = None,
) -> dict[str, Any]:
    """Compare bounded host snapshots only; this never runs a workflow."""

    from .io import validated_package_result

    validation = validated_package_result(value)
    if not validation["valid"]:
        return {
            "status": "unavailable",
            "reason": "Package did not pass static validation.",
            "action": "Correct the static contract errors before integration.",
            "missing_node_types": [],
            "missing_models": [],
            "missing_runtimes": [],
            "resource_gaps": [],
            "execution": "not_run",
        }
    package = validation["package"]
    assert isinstance(package, dict)
    declared_nodes = set(package["compatibility"]["required_node_types"])
    missing_nodes: list[str] = []
    missing_models: list[str] = []
    missing_runtimes: list[str] = []
    resource_gaps: list[str] = []
    snapshot_errors: list[str] = []
    if supported_node_types is not None:
        if not isinstance(supported_node_types, (list, tuple, set)) or len(supported_node_types) > 256 or not all(isinstance(item, str) and IDENTIFIER_RE.fullmatch(item) for item in supported_node_types):
            snapshot_errors.append("host_node_snapshot")
        else:
            missing_nodes = sorted(declared_nodes - set(supported_node_types))
    if host_requirements is not None:
        if not isinstance(host_requirements, dict) or set(host_requirements) != {"models", "runtimes"}:
            snapshot_errors.append("host_requirement_snapshot")
        else:
            models, model_error = _safe_requirement_snapshot(host_requirements.get("models"), runtime=False)
            runtimes, runtime_error = _safe_requirement_snapshot(host_requirements.get("runtimes"), runtime=True)
            if model_error or runtime_error or models is None or runtimes is None:
                snapshot_errors.append("host_requirement_snapshot")
            else:
                missing_models = sorted(item["id"] for item in package["requirements"]["models"] if models.get(item["id"]) != item["version"])
                missing_runtimes = sorted(item["id"] for item in package["requirements"]["runtimes"] if item["id"] not in runtimes or not _version_satisfies(runtimes[item["id"]], item["version"]))
    if resource_snapshot is not None:
        resource_gaps, resource_error = _resource_snapshot_gaps(package["resource_hints"], resource_snapshot)
        if resource_error or resource_gaps is None:
            snapshot_errors.append("host_resource_snapshot")
            resource_gaps = []
    if snapshot_errors:
        return {
            "status": "unavailable",
            "reason": "A host preflight snapshot is not a closed bounded server-owned contract.",
            "action": "Pass only typed server-owned node, requirement, and resource snapshots.",
            "errors": sorted(set(snapshot_errors)),
            "missing_node_types": [],
            "missing_models": [],
            "missing_runtimes": [],
            "resource_gaps": [],
            "execution": "not_run",
        }
    unavailable = bool(missing_nodes or missing_models or missing_runtimes or resource_gaps)
    checked = supported_node_types is not None or host_requirements is not None or resource_snapshot is not None
    return {
        "status": "unavailable" if unavailable else "partial",
        "reason": "One or more declared static requirements are absent from the host snapshot." if unavailable else ("Static requirements match supplied host snapshots; runtime graph execution remains unverified." if checked else "No host capability snapshot was supplied, so only static package validity is known."),
        "action": "Resolve the missing typed requirements or resource capacity before integration." if unavailable else ("Run a separately authorized bounded smoke before claiming operational runtime support." if checked else "Provide server-owned host snapshots for a static requirement check."),
        "missing_node_types": missing_nodes,
        "missing_models": missing_models,
        "missing_runtimes": missing_runtimes,
        "resource_gaps": resource_gaps,
        "resource_hints": copy.deepcopy(package["resource_hints"]),
        "execution": "not_run",
    }
