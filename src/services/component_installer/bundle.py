"""Server-owned composite component bundle plans.

The bundle layer is deliberately small: it composes the existing component
installer rather than introducing another download or inference path.  A
browser can select one fixed component ID; the server expands its reviewed
runtime/model dependencies, persists the plan in memory, and applies the
ordered child plans only after an explicit confirmation.  All filesystem
locations, sources and leaf metadata remain private to the server.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import secrets
import time
from typing import Any

from .policy import InstallPlanError, plan_fingerprint, validate_component_id


def _state_fingerprint(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


class ComponentBundleService:
    """Compose and apply a fixed dependency graph through ComponentInstaller."""

    def __init__(self, installer: Any) -> None:
        self.installer = installer
        self._plans: dict[str, dict[str, Any]] = {}

    def _record(self, component_id: str, component_type: str) -> Mapping[str, Any]:
        record = self.installer._catalog_record(component_id, component_type)
        if not isinstance(record, Mapping):
            raise InstallPlanError("unknown_component")
        return record

    def _ordered_nodes(self, component_id: str, component_type: str) -> list[tuple[str, str]]:
        """Return dependencies before the requested component.

        The only dependency edges accepted here are catalog-owned runtime_id
        and an optional fixed ``dependencies`` list.  Client payloads cannot
        add edges or choose a destination.
        """

        ordered: list[tuple[str, str]] = []
        visiting: set[tuple[str, str]] = set()
        visited: set[tuple[str, str]] = set()

        def visit(node_id: str, node_type: str) -> None:
            key = (node_type, node_id)
            if key in visited:
                return
            if key in visiting:
                raise InstallPlanError("component_dependency_cycle")
            visiting.add(key)
            record = self._record(node_id, node_type)
            if node_type == "model":
                runtime_id = record.get("runtime_id")
                if isinstance(runtime_id, str) and runtime_id:
                    validate_component_id(runtime_id)
                    visit(runtime_id, "runtime")
            dependencies = record.get("dependencies")
            if isinstance(dependencies, list):
                for dependency in dependencies[:8]:
                    if not isinstance(dependency, Mapping):
                        raise InstallPlanError("invalid_component_dependency")
                    dep_id = dependency.get("component_id")
                    dep_type = dependency.get("component_type")
                    if not isinstance(dep_id, str) or dep_type not in {"model", "runtime"}:
                        raise InstallPlanError("invalid_component_dependency")
                    visit(validate_component_id(dep_id), str(dep_type))
            visiting.remove(key)
            visited.add(key)
            ordered.append(key)

        visit(validate_component_id(component_id), component_type)
        return ordered

    @staticmethod
    def _step_public(step: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: step[key]
            for key in (
                "step_index", "component_id", "component_type", "status", "disposition",
                "install_strategy", "auto_install_supported", "download_bytes",
                "disk_bytes", "shared_dependency_id", "action", "state_fingerprint",
            )
            if key in step
        }

    def plan(self, component_id: str, *, component_type: str = "model", variant: str = "default") -> dict[str, Any]:
        component_id = validate_component_id(component_id)
        if component_type not in {"model", "runtime"}:
            raise InstallPlanError("invalid_component_type")
        if variant not in {"default", "low_vram", "portable"}:
            raise InstallPlanError("invalid_component_variant")
        nodes = self._ordered_nodes(component_id, component_type)
        steps: list[dict[str, Any]] = []
        total_download = 0
        total_disk = 0
        counted_shared: set[str] = set()
        for index, (node_type, node_id) in enumerate(nodes):
            record = self._record(node_id, node_type)
            state = self.installer._inspect(node_id, node_type)
            status = str(state.get("status") or "UNAVAILABLE")
            disposition = str(record.get("disposition") or "MANUAL_IMPORT_ONLY")
            auto = bool(self.installer._auto_install_ready(record))
            shared = str(record.get("shared_dependency_id") or "") or None
            download = max(0, int(record.get("estimated_download_size", 0) or 0))
            disk = max(0, int(record.get("estimated_disk_size", 0) or 0))
            if shared and shared in counted_shared:
                download = 0
                disk = 0
            elif shared:
                counted_shared.add(shared)
            if status not in {"INSTALLED", "INSTALLED_UNVERIFIED", "OPERATIONAL"}:
                total_download += download
                total_disk += disk
            step = {
                "step_index": index,
                "component_id": node_id,
                "component_type": node_type,
                "status": status,
                "disposition": disposition,
                "install_strategy": str(record.get("install_strategy") or "manual_import"),
                "auto_install_supported": auto,
                "download_bytes": download,
                "disk_bytes": disk,
                "shared_dependency_id": shared,
                "action": "reuse_existing" if status in {"INSTALLED", "INSTALLED_UNVERIFIED", "OPERATIONAL"} else "install",
                "state_fingerprint": _state_fingerprint(state),
            }
            steps.append(step)
        body = {
            "schema_version": "component-bundle-plan.v1",
            "component_id": component_id,
            "component_type": component_type,
            "variant": variant,
            "steps": [self._step_public(step) for step in steps],
            "download_bytes": total_download,
            "estimated_disk_bytes": total_disk,
            "preserve_existing_dependencies": True,
            "shared_dependency_policy": "deduplicate_and_preserve_referenced",
            "execution": "not_run",
            "dry_run": True,
            "created_at": int(time.time()),
        }
        plan_id = f"bundle_plan_{secrets.token_hex(16)}"
        fingerprint = plan_fingerprint(body)
        internal = {**body, "plan_id": plan_id, "plan_fingerprint": fingerprint, "nodes": nodes}
        self._plans[plan_id] = internal
        return {
            **body,
            "plan_id": plan_id,
            "plan_fingerprint": fingerprint,
            "status": "planned",
            "next_action": "Review the ordered dependency bundle and explicitly confirm it.",
        }

    def lookup(self, plan_id: str) -> dict[str, Any] | None:
        plan = self._plans.get(plan_id)
        if not isinstance(plan, Mapping):
            return None
        return {
            key: plan[key]
            for key in (
                "schema_version", "plan_id", "plan_fingerprint", "component_id", "component_type",
                "variant", "steps", "download_bytes", "estimated_disk_bytes",
                "preserve_existing_dependencies", "shared_dependency_policy", "execution", "dry_run",
            )
            if key in plan
        }

    def confirm(self, plan_id: str, *, confirmed: bool = False) -> dict[str, Any]:
        plan = self._plans.get(plan_id)
        if not isinstance(plan, Mapping):
            return {"status": "error", "code": "unknown_component_bundle_plan", "execution": "not_run"}
        if not confirmed:
            return {"status": "waiting_confirmation", "plan_id": plan_id, "execution": "not_run", "dry_run": True}
        results: list[dict[str, Any]] = []
        for node_type, node_id in plan.get("nodes", []):
            current = self.installer._inspect(node_id, node_type)
            step = next((item for item in plan.get("steps", []) if item.get("component_id") == node_id and item.get("component_type") == node_type), None)
            if not isinstance(step, Mapping):
                return {"status": "conflict", "code": "bundle_step_missing", "plan_id": plan_id, "execution": "not_run"}
            if _state_fingerprint(current) != step.get("state_fingerprint"):
                return {"status": "conflict", "code": "stale_component_bundle_plan", "plan_id": plan_id, "component_id": node_id, "execution": "not_run", "next_action": "Create a fresh bundle plan."}
            if current.get("status") in {"INSTALLED", "INSTALLED_UNVERIFIED", "OPERATIONAL"}:
                results.append({"component_id": node_id, "component_type": node_type, "status": "reused", "state": current.get("status"), "execution": "completed"})
                continue
            if not step.get("auto_install_supported"):
                return {"status": "unavailable", "code": "bundle_step_requires_review", "plan_id": plan_id, "component_id": node_id, "component_type": node_type, "execution": "not_run", "steps": results, "next_action": "Use the listed Manual Import, authorization or license flow for this dependency."}
            child = self.installer.plan_install(node_id, component_type=node_type, variant=str(plan.get("variant") or "default"))
            applied = self.installer.confirm_plan(child["plan_id"], confirmed=True)
            results.append({"component_id": node_id, "component_type": node_type, "status": applied.get("status"), "state": applied.get("state"), "code": applied.get("code"), "execution": applied.get("execution", "not_run")})
            if applied.get("status") != "completed":
                return {"status": applied.get("status") or "failed", "code": applied.get("code") or "bundle_step_failed", "plan_id": plan_id, "component_id": node_id, "steps": results, "execution": applied.get("execution", "not_run"), "next_action": "Resolve the failed dependency step and create a fresh bundle plan."}
        return {"status": "completed", "plan_id": plan_id, "component_id": plan["component_id"], "state": "INSTALLED_UNVERIFIED", "execution": "completed", "dry_run": False, "steps": results, "next_action": "Refresh the component snapshot and run the bounded adapter verification."}


__all__ = ["ComponentBundleService"]
