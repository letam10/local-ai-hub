"""Dry-run Module Manager dependency, publication, and resource planning."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
import hashlib
import json
from typing import Any

from src.shared.schemas.module_manager import MODULE_MANAGER_SCHEMA_VERSION, STATUS_ALLOWLIST

from .registry import CapabilityRegistry, build_capability_registry
from .resources import plan_module_resources


_STATUS_RANK = {"error": 6, "unavailable": 5, "partial": 4, "planned": 3, "not_published": 3, "available": 2, "installed": 1, "operational": 0}


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def _status(value: object) -> str:
    return value if isinstance(value, str) and value in STATUS_ALLOWLIST else "unavailable"


def _dependency_ids(record: Mapping[str, Any]) -> list[str]:
    return sorted(item["id"] for item in record.get("dependencies", []) if isinstance(item, Mapping) and isinstance(item.get("id"), str))


def _cycle_nodes(graph: Mapping[str, list[str]]) -> set[str]:
    visiting: set[str] = set()
    visited: set[str] = set()
    cycle: set[str] = set()

    def visit(node: str, trail: list[str]) -> None:
        if node in visiting:
            cycle.update(trail[trail.index(node) :])
            return
        if node in visited:
            return
        visiting.add(node)
        for dependency in graph.get(node, []):
            if dependency in graph:
                visit(dependency, [*trail, dependency])
        visiting.remove(node)
        visited.add(node)

    for node in sorted(graph):
        visit(node, [node])
    return cycle


def _source_action(record: Mapping[str, Any]) -> tuple[str, str]:
    manifest = record.get("source_manifest")
    if manifest is None:
        return "not_published", "Publish an HTTPS manifest with a SHA-256 digest before planning installation."
    return "available", "Review the verified manifest and license before requesting a future installation workflow."


def build_module_plan(records: Iterable[Mapping[str, Any]] | None = None, *, registry: CapabilityRegistry | Mapping[str, Any] | None = None, hardware: Mapping[str, Any] | None = None, mode: str = "parallel") -> dict[str, Any]:
    """Create a deterministic plan; this function never downloads or mutates files."""

    if registry is not None:
        if isinstance(registry, CapabilityRegistry):
            source_records = registry.records
        elif isinstance(registry, Mapping) and isinstance(registry.get("records"), list):
            source_records = deepcopy(registry["records"])
        else:
            source_records = []
    else:
        source_records = list(records or [])
    by_id = {item["id"]: deepcopy(dict(item)) for item in source_records if isinstance(item, Mapping) and isinstance(item.get("id"), str)}
    graph = {identifier: _dependency_ids(record) for identifier, record in by_id.items()}
    missing: dict[str, list[str]] = {identifier: [dependency for dependency in dependencies if dependency not in by_id] for identifier, dependencies in graph.items()}
    missing = {identifier: dependencies for identifier, dependencies in missing.items() if dependencies}
    cycle = _cycle_nodes(graph)
    modules: list[dict[str, Any]] = []
    resource_requests: list[dict[str, Any]] = []
    actions: list[str] = []
    overall = "operational"
    for identifier in sorted(by_id):
        record = by_id[identifier]
        status = _status(record.get("status"))
        reason = str(record.get("reason", "Static module metadata is available."))
        action = str(record.get("next_action", "Review static evidence before runtime work."))
        if identifier in missing:
            status = "unavailable"
            reason = "One or more declared module dependencies are missing from the server-owned registry."
            action = "Publish or restore the missing dependency descriptors before planning this module."
        elif identifier in cycle:
            status = "error"
            reason = "The module dependency graph contains a cycle."
            action = "Break the dependency cycle before publishing a module plan."
        elif status == "operational" and (record.get("observed", {}).get("state") != "observed" or record.get("evidence", {}).get("state") not in {"static", "runtime"}):
            status = "partial"
            reason = "Operational status lacks fresh server-owned evidence in this static snapshot."
            action = "Refresh evidence or obtain separately authorized bounded smoke."
        publication_status, publication_action = _source_action(record)
        if publication_status == "not_published" and status in {"operational", "available", "installed"}:
            status = "not_published"
            reason = "No future-install manifest is published for this module; no install action is available."
            action = publication_action
        actions.append(action)
        overall = status if _STATUS_RANK[status] > _STATUS_RANK[overall] else overall
        modules.append({
            "id": identifier,
            "provider": record["provider"],
            "status": status,
            "version": record.get("version"),
            "dependencies": deepcopy(record.get("dependencies", [])),
            "source": record.get("source"),
            "license": record.get("license"),
            "model_requirements": deepcopy(record.get("model_requirements", [])),
            "source_manifest": deepcopy(record.get("source_manifest")),
            "reason": reason,
            "next_action": action,
            "execution": "not_run",
        })
        hints = record.get("resource_hints")
        resource_requests.append({"id": identifier, "resource_hints": hints if isinstance(hints, Mapping) else {}})
    resource_plan = plan_module_resources(resource_requests, hardware=hardware, mode=mode)
    if resource_plan["status"] in {"unavailable", "partial"}:
        overall = resource_plan["status"] if _STATUS_RANK[resource_plan["status"]] > _STATUS_RANK[overall] else overall
        actions.extend(resource_plan.get("actions", []))
    result: dict[str, Any] = {
        "schema_version": MODULE_MANAGER_SCHEMA_VERSION,
        "status": overall,
        "execution": "not_run",
        "dry_run": True,
        "actions": sorted(set(actions)),
        "modules": modules,
        "resource_plan": resource_plan,
        "reason": "Module Manager emits dependency and resource plans only; no download, install, verify, repair, or uninstall operation ran.",
        "next_action": "Review this plan and authorize a future manifest-driven operation separately.",
        "errors": [{"code": "missing_dependency", "module": key} for key in sorted(missing)] + ([{"code": "dependency_cycle"}] if cycle else []),
    }
    result["fingerprint"] = {"algorithm": "sha256", "value": _digest(result)}
    return result


class ModuleManager:
    """Small façade for server-owned registry and dry-run planning."""

    def __init__(self, registry: CapabilityRegistry | Mapping[str, Any] | None = None) -> None:
        self._registry = registry or CapabilityRegistry([])

    @property
    def registry(self) -> CapabilityRegistry | Mapping[str, Any]:
        return self._registry

    def plan(self, *, hardware: Mapping[str, Any] | None = None, mode: str = "parallel") -> dict[str, Any]:
        return build_module_plan(registry=self._registry, hardware=hardware, mode=mode)

    def preflight(self, *, hardware: Mapping[str, Any] | None = None, mode: str = "parallel") -> dict[str, Any]:
        result = self.plan(hardware=hardware, mode=mode)
        result = deepcopy(result)
        result["next_action"] = "Use this read-only preflight to decide whether a separately authorized future operation is appropriate."
        result["fingerprint"] = {"algorithm": "sha256", "value": _digest({key: value for key, value in result.items() if key != "fingerprint"})}
        return result


def preflight_modules(records: Iterable[Mapping[str, Any]] | None = None, *, registry: CapabilityRegistry | Mapping[str, Any] | None = None, hardware: Mapping[str, Any] | None = None, mode: str = "parallel") -> dict[str, Any]:
    return build_module_plan(records, registry=registry, hardware=hardware, mode=mode)


__all__ = ["ModuleManager", "build_module_plan", "preflight_modules"]
