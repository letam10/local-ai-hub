"""Runtime discovery and receipt composition; no startup installation."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from src.platform.paths import HubPaths, get_paths

from .catalog import RuntimeCatalogError, load_catalog


def _safe_target(root: Path, relative: str) -> Path | None:
    candidate = root / relative
    try:
        candidate.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    current = candidate
    while True:
        if current.exists() and current.is_symlink():
            return None
        if current == root:
            break
        if current.parent == current:
            return None
        current = current.parent
    return candidate


class RuntimeManager:
    def __init__(self, *, paths: HubPaths | None = None, catalog_path: Path | None = None) -> None:
        self.paths = paths or get_paths()
        self.catalog_path = catalog_path or (self.paths.app_root / "Config" / "runtime_catalog.example.json")
        self._records = load_catalog(self.catalog_path)

    def _root(self, record: Mapping[str, Any]) -> Path:
        if record["root_class"] == "environments_root":
            return self.paths.environments_root
        if record["root_class"] == "external_managed":
            return self.paths.runtime_root / "external"
        return self.paths.runtime_root

    def inspect(self, runtime_id: str) -> dict[str, Any]:
        record = next((item for item in self._records if item["runtime_id"] == runtime_id), None)
        if record is None:
            raise RuntimeCatalogError("unknown_runtime_id")
        root = self._root(record)
        leaves = []
        for relative in record["required_leaves"]:
            target = _safe_target(root, relative)
            leaves.append({"relative_path": relative, "present": bool(target and target.exists() and target.is_file())})
        if leaves and all(item["present"] for item in leaves):
            status = "INSTALLED_UNVERIFIED"
            reason = "Required runtime leaves are present; import/pip and bounded smoke evidence are still required."
            action = "Run the component-specific verification under an explicit authorization."
        elif any(item["present"] for item in leaves):
            status = "PARTIAL"
            reason = "Some required runtime leaves are present, but the runtime is incomplete."
            action = "Review the runtime install plan; do not replace an existing environment automatically."
        else:
            status = "NOT_INSTALLED"
            reason = "No required runtime leaves were observed at the managed root."
            action = "Create a separate pinned runtime installation plan."
        return {"runtime_id": runtime_id, "status": status, "execution": "not_run", "leaves": leaves, "reason": reason, "next_action": action}

    def snapshot(self) -> dict[str, Any]:
        records = [self.inspect(item["runtime_id"]) for item in self._records]
        return {"schema_version": "runtime-manager.v1", "status": "partial" if any(item["status"] == "PARTIAL" for item in records) else "available", "execution": "not_run", "dry_run": True, "records": records, "reason": "Runtime Manager performs bounded leaf inspection only; it does not install, execute or alter environments at startup.", "next_action": "Review runtime plans separately from model installation plans."}

    def plan_install(self, runtime_id: str, *, free_bytes: int | None = None) -> dict[str, Any]:
        record = next((item for item in self._records if item["runtime_id"] == runtime_id), None)
        if record is None:
            raise RuntimeCatalogError("unknown_runtime_id")
        return {"schema_version": "runtime-install-plan.v1", "runtime_id": runtime_id, "status": "planned", "execution": "not_run", "dry_run": True, "destination": record["root_class"], "free_bytes": free_bytes, "source": record["official_source"], "reason": "No download, process execution, dependency installation or environment mutation occurred.", "next_action": "Review pinned requirements, wheels/checksums and disk/process gates before explicit installation."}


__all__ = ["RuntimeManager"]
