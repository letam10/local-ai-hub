"""Server-owned one-click component lifecycle plans.

The public contract is inspect -> plan -> confirm -> apply.  Real downloads
remain subject to the catalog disposition and trusted downloader policy.  The
fixture apply path exists solely for clean-machine acceptance tests; it uses
the same ModelManager/RuntimeManager and receipt flow as an owner install.
Legacy unsupported component types remain fail-closed rather than pretending
to execute; historical callers may still recognize ``manager_executor_required``
as a compatibility refusal, while supported runtime candidates use the bounded
executor below.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
import secrets
import shutil
import time
from typing import Any

from src.platform.paths import ComponentPathError, HubPaths, get_paths, resolve_component_root

from .catalog import ProductionCatalog, ProductionCatalogError
from .runtime_executor import RuntimeArchiveExecutor
from .model_executor import ModelArchiveExecutor
from src.services.component_installer.downloader import DownloadError, TrustedDownloader
from src.services.component_installer.policy import trusted_source


class LifecycleError(ValueError):
    pass


def _fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


class ComponentLifecycle:
    def __init__(self, *, paths: HubPaths | None = None, catalog: ProductionCatalog | None = None) -> None:
        self.paths = paths or get_paths()
        self.catalog = catalog or ProductionCatalog(paths=self.paths)
        self._plans: dict[str, dict[str, Any]] = {}
        self._jobs: dict[str, dict[str, Any]] = {}

    def _record(self, component_id: str) -> tuple[str, dict[str, Any]]:
        if component_id in self.catalog.models:
            return "model", self.catalog.models[component_id]
        if component_id in self.catalog.runtimes:
            return "runtime", self.catalog.runtimes[component_id]
        raise LifecycleError("unknown_component_id")

    def _inspect_component(self, component_id: str, component_type: str, record: Mapping[str, Any]) -> dict[str, Any]:
        """Use the shared fixed-leaf fast inspector for lifecycle planning."""

        from src.services.component_installer.verification import FastComponentInspector

        return FastComponentInspector().inspect(
            paths=self.paths,
            component_id=component_id,
            component_type=component_type,
            record=record,
            catalog_fingerprint=self.catalog.fingerprint,
        )

    def snapshot(self) -> dict[str, Any]:
        value = self.catalog.snapshot()
        value["lifecycle_schema"] = "component-lifecycle.v1"
        value["maintenance_actions"] = ["repair", "update", "uninstall"]
        return value

    def plan_one_click(self, component_id: str) -> dict[str, Any]:
        kind, record = self._record(component_id)
        if kind == "model":
            current = self._inspect_component(component_id, "model", record)
            runtime_id = record.get("runtime_id")
            runtime = self._inspect_component(runtime_id, "runtime", self.catalog.runtimes[runtime_id]) if runtime_id and runtime_id in self.catalog.runtimes else None
        else:
            current = self._inspect_component(component_id, "runtime", record)
            runtime = current
            runtime_id = component_id
        dependencies = []
        if kind == "model" and runtime_id:
            dependencies.append({"kind": "runtime", "id": runtime_id, "status": runtime["status"] if runtime else "UNAVAILABLE"})
        dependencies.append({"kind": kind, "id": component_id, "status": current["status"]})
        can_install = record.get("disposition") == "AUTO_INSTALL_READY"
        action = "Download & Install" if can_install else ("Authorize & Install" if record.get("disposition") == "AUTH_REQUIRED" else "Review License" if record.get("disposition") == "LICENSE_REQUIRED" else "Import Model" if kind == "model" else "Review Runtime")
        body = {"schema_version": "v7-component-install-plan.v1", "component_id": component_id, "component_type": kind, "catalog_fingerprint": self.catalog.fingerprint, "dependencies": dependencies, "expected_state": current["status"], "action": action, "disposition": record.get("disposition"), "estimated_download_size_bytes": int(record.get("estimated_download_size", 0)), "estimated_disk_size_bytes": int(record.get("estimated_disk_size", 0)), "preserve_existing": True, "execution": "not_run", "dry_run": True}
        plan_id = "v7_plan_" + secrets.token_hex(16)
        # Keep the normalized catalog record server-side only; the public plan
        # projection contains IDs/fingerprints and bounded estimates.
        plan = {**body, "record": record, "plan_id": plan_id, "plan_fingerprint": _fingerprint(body), "created_at": int(time.time())}
        self._plans[plan_id] = plan
        status = "planned" if can_install and current["status"] != "INSTALLED" else "manual_review" if current["status"] != "INSTALLED" else "already_installed"
        return {**body, "plan_id": plan_id, "plan_fingerprint": plan["plan_fingerprint"], "status": status, "reason": "Server-owned dependency plan; no download or write occurred." if status == "planned" else "The catalog or existing owner state requires explicit review.", "next_action": action}

    def lookup_plan(self, plan_id: str) -> dict[str, Any] | None:
        value = self._plans.get(plan_id)
        if not value:
            return None
        return {key: value[key] for key in ("schema_version", "plan_id", "plan_fingerprint", "component_id", "component_type", "dependencies", "expected_state", "action", "disposition", "execution", "dry_run") if key in value}

    def confirm(self, plan_id: str, *, confirmed: bool = False) -> dict[str, Any]:
        plan = self._plans.get(plan_id)
        if plan is None:
            return {"status": "error", "code": "unknown_plan", "execution": "not_run"}
        if not confirmed:
            return {"status": "waiting_confirmation", "plan_id": plan_id, "execution": "not_run", "dry_run": True}
        if plan.get("catalog_fingerprint") != self.catalog.fingerprint:
            return {"status": "conflict", "code": "catalog_changed", "plan_id": plan_id, "execution": "not_run"}
        if plan.get("disposition") != "AUTO_INSTALL_READY":
            return {"status": "unavailable", "code": "manual_review_required", "plan_id": plan_id, "execution": "not_run", "dry_run": True, "next_action": "Use the explicitly documented import/license/authentication flow."}
        record = plan.get("record") if isinstance(plan.get("record"), Mapping) else None
        if not isinstance(record, Mapping):
            return {"status": "unavailable", "code": "catalog_record_unavailable", "plan_id": plan_id, "execution": "not_run", "dry_run": True}
        dependency_block = next((item for item in plan.get("dependencies", []) if item.get("kind") == "runtime" and item.get("id") != plan.get("component_id") and item.get("status") not in {"INSTALLED", "INSTALLED_UNVERIFIED", "OPERATIONAL"}), None)
        if dependency_block is not None:
            return {"status": "unavailable", "code": "dependency_unavailable", "plan_id": plan_id, "execution": "not_run", "dry_run": True, "dependency": {key: dependency_block.get(key) for key in ("kind", "id", "status")}, "next_action": "Install or reuse the server-owned runtime dependency before this component."}
        component_type = plan.get("component_type")
        if component_type == "runtime" and record.get("install_strategy") == "portable_archive":
            executor_kind = "runtime"
        elif component_type == "model" and record.get("install_strategy") == "direct_file_download":
            executor_kind = "model"
        else:
            return {"status": "unavailable", "code": "component_executor_unavailable", "plan_id": plan_id, "execution": "not_run", "dry_run": True, "next_action": "Use a reviewed component executor for this bundle type."}
        source = record.get("official_source")
        if not trusted_source(source, fixture_mode=False):
            return {"status": "unavailable", "code": "source_not_trusted", "plan_id": plan_id, "execution": "not_run", "dry_run": True}
        expected_size = int(record.get("estimated_download_size", 0) or 0)
        expected_hash = record.get("sha256")
        if executor_kind == "model":
            files = record.get("files") if isinstance(record.get("files"), list) else []
            leaf = files[0] if files and isinstance(files[0], Mapping) else {}
            expected_hash = leaf.get("sha256") or expected_hash
        if expected_size <= 0 or not isinstance(expected_hash, str) or len(expected_hash) != 64:
            return {"status": "unavailable", "code": "trusted_source_metadata_required", "plan_id": plan_id, "execution": "not_run", "dry_run": True}
        required = expected_size + int(record.get("estimated_disk_size", 0) or 0) + 256 * 1024 * 1024
        try:
            free = int(shutil.disk_usage(self.paths.data_root).free)
        except OSError:
            free = 0
        if free < required:
            return {"status": "unavailable", "code": "insufficient_disk", "plan_id": plan_id, "execution": "not_run", "dry_run": True}
        stage = self.paths.temp_root / "component-install" / plan_id
        try:
            stage.mkdir(parents=True, exist_ok=True)
            filename = f"{plan['component_id']}.zip" if executor_kind == "runtime" else f"{plan['component_id']}.payload"
            downloaded = TrustedDownloader(staging_root=stage, max_bytes=required).download(
                str(source), filename, expected_sha256=expected_hash, expected_size=expected_size,
                disk_free_bytes=free, disk_safety_bytes=256 * 1024 * 1024,
            )
            result = RuntimeArchiveExecutor(paths=self.paths).apply(record, downloaded.staged_path, catalog_fingerprint=self.catalog.fingerprint) if executor_kind == "runtime" else ModelArchiveExecutor(paths=self.paths).apply(record, downloaded.staged_path, catalog_fingerprint=self.catalog.fingerprint)
            return {"status": result.get("status"), "plan_id": plan_id, "component_id": plan["component_id"], "execution": result.get("execution", "not_run"), "dry_run": False, "result": {key: result.get(key) for key in ("state", "receipt", "code", "next_action") if key in result}, "next_action": result.get("next_action")}
        except DownloadError as exc:
            return {"status": "failed", "code": exc.code, "plan_id": plan_id, "execution": "not_run", "dry_run": False}
        except (OSError, ValueError):
            return {"status": "failed", "code": "component_install_failed", "plan_id": plan_id, "execution": "not_run", "dry_run": False}

    def plan_maintenance(self, component_id: str, action: str) -> dict[str, Any]:
        if action not in {"repair", "update", "uninstall"}:
            raise LifecycleError("invalid_maintenance_action")
        kind, record = self._record(component_id)
        current = self._inspect_component(component_id, kind, record)
        body = {"schema_version": "v7-component-maintenance-plan.v1", "component_id": component_id, "component_type": kind, "action": action, "catalog_fingerprint": self.catalog.fingerprint, "expected_state": current["status"], "preserve_existing": True, "execution": "not_run", "dry_run": True}
        plan_id = "v7_maintenance_" + secrets.token_hex(16)
        plan = {**body, "plan_id": plan_id, "plan_fingerprint": _fingerprint(body)}
        self._plans[plan_id] = plan
        return {**body, "plan_id": plan_id, "plan_fingerprint": plan["plan_fingerprint"], "status": "planned", "reason": "Maintenance is plan-only until a separately confirmed manager executor is available.", "next_action": "Review the exact existing receipt and dependency impact."}

    def apply_fixture(self, plan_id: str, *, model_source: Path | None = None, runtime_source: Path | None = None, confirmed: bool = False) -> dict[str, Any]:
        """Apply a synthetic clean-machine plan using the normal managers.

        This method is intentionally explicit and only accepts a source under
        the caller's test-owned temporary root.  Production HTTP callers never
        receive this path-bearing method.
        """
        plan = self._plans.get(plan_id)
        if not confirmed or not isinstance(plan, Mapping):
            return {"status": "waiting_confirmation", "execution": "not_run", "dry_run": True}
        if plan.get("disposition") != "AUTO_INSTALL_READY":
            return {"status": "unavailable", "code": "manual_review_required", "execution": "not_run"}
        from src.services.model_manager import ModelManager
        from src.services.runtime_manager import RuntimeManager
        model = ModelManager(paths=self.paths, catalog_path=self.paths.app_root / "Config" / "model_catalog.example.json")
        runtime = RuntimeManager(paths=self.paths, catalog_path=self.paths.app_root / "Config" / "runtime_catalog.example.json")
        component_id = str(plan["component_id"])
        if plan["component_type"] == "model":
            result = model.install_fixture(component_id, model_source) if model_source else {"status": "failed", "code": "fixture_source_required"}
        else:
            result = runtime.install_fixture(component_id, runtime_source) if runtime_source else {"status": "failed", "code": "fixture_source_required"}
        output = {"schema_version": "v7-component-install-result.v1", "plan_id": plan_id, "status": "completed" if result.get("status") == "completed" else "failed", "execution": "completed" if result.get("status") == "completed" else "not_run", "dry_run": False, "component_id": component_id, "result": {key: result.get(key) for key in ("status", "state", "location_class", "code") if key in result}, "next_action": "Run one bounded component verification before operational promotion."}
        if output["status"] == "completed":
            # This receipt is only used by the synthetic acceptance harness to
            # prove repair/update/uninstall transaction semantics.  Production
            # HTTP callers cannot supply fixture roots or reach this method.
            marker = self.paths.config_root / "v7_fixture_receipts.json"
            current = marker.read_text(encoding="utf-8") if marker.is_file() else "{}"
            try:
                records = json.loads(current)
            except (UnicodeError, json.JSONDecodeError):
                records = {}
            if not isinstance(records, dict):
                records = {}
            records[component_id] = {"component_type": plan["component_type"], "fixture_owned": True, "catalog_fingerprint": self.catalog.fingerprint}
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(json.dumps(records, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return output

    def apply_fixture_maintenance(self, plan_id: str, *, confirmed: bool = False) -> dict[str, Any]:
        """Exercise repair/update/uninstall only for fixture-owned leaves."""

        plan = self._plans.get(plan_id)
        if not confirmed or not isinstance(plan, Mapping) or plan.get("schema_version") != "v7-component-maintenance-plan.v1":
            return {"status": "waiting_confirmation", "execution": "not_run", "dry_run": True}
        marker = self.paths.config_root / "v7_fixture_receipts.json"
        try:
            records = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            records = {}
        owned = records.get(plan.get("component_id")) if isinstance(records, Mapping) else None
        if not isinstance(owned, Mapping) or owned.get("fixture_owned") is not True or owned.get("catalog_fingerprint") != self.catalog.fingerprint:
            return {"status": "unavailable", "code": "fixture_ownership_required", "execution": "not_run"}
        component_id = str(plan["component_id"])
        kind = str(plan["component_type"])
        current = self.catalog.inspect_model(component_id) if kind == "model" else self.catalog.inspect_runtime(component_id)
        if current["status"] != "INSTALLED":
            return {"status": "conflict", "code": "fixture_state_changed", "execution": "not_run"}
        if plan["action"] in {"repair", "update"}:
            return {"status": "completed", "action": plan["action"], "component_id": component_id, "execution": "completed", "dry_run": False, "state": "INSTALLED", "next_action": "Run bounded runtime verification before operational promotion."}
        if plan["action"] == "uninstall":
            # Remove only known leaves under the managed root, never a caller
            # path or an unknown directory.  This branch is test-only and
            # requires the explicit fixture-owned receipt above.
            record = self.catalog.models.get(component_id) if kind == "model" else self.catalog.runtimes.get(component_id)
            try:
                root = resolve_component_root(self.paths, component_id, kind, "models_root" if kind == "model" else record.get("root_class") if record else None, require_exists=False)
            except ComponentPathError:
                return {"status": "failed", "code": "unsafe_fixture_target", "execution": "not_run"}
            leaves = record.get("files", []) if kind == "model" and record else [{"relative_path": item} for item in (record.get("required_leaves", []) if record else [])]
            removed = 0
            for leaf in leaves:
                target = root / str(leaf.get("relative_path"))
                try:
                    target.resolve().relative_to(root.resolve())
                    if target.is_file() and not target.is_symlink():
                        target.unlink()
                        removed += 1
                except (OSError, ValueError):
                    return {"status": "failed", "code": "unsafe_fixture_target", "execution": "not_run"}
            records.pop(component_id, None)
            marker.write_text(json.dumps(records, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            return {"status": "completed", "action": "uninstall", "component_id": component_id, "removed_known_leaves": removed, "execution": "completed", "dry_run": False}
        return {"status": "error", "code": "invalid_maintenance_action", "execution": "not_run"}


def lifecycle_for_paths(paths: HubPaths | None = None) -> ComponentLifecycle:
    return ComponentLifecycle(paths=paths)


__all__ = ["ComponentLifecycle", "LifecycleError", "lifecycle_for_paths"]
