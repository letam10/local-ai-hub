"""Server-owned component plans and safe selection tokens.

The browser can select a fixed component and confirm an opaque plan.  It cannot
provide a path, URL, command, executable or file list.  Production plans remain
read-only when catalog metadata is incomplete; tests use tiny synthetic
fixtures through explicit backend-only methods.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import os
import secrets
import shutil
import stat
import threading
import time
from pathlib import Path
from typing import Any

from src.platform.paths import ComponentPathError, HubPaths, get_paths, resolve_component_leaf, resolve_component_root
from src.services.model_manager import ModelManager
from src.services.runtime_manager import RuntimeManager

from .policy import (
    INSTALL_JOB_STATES,
    InstallPlanError,
    plan_fingerprint,
    safe_component_projection,
    source_fingerprint,
    trusted_source,
    validate_component_id,
)
from .downloader import DownloadError, TrustedDownloader
from .import_executor import ManualImportExecutor
from .maintenance_executor import MaintenanceExecutor
from .reuse_executor import ExistingInstallReuseExecutor
from .receipts import CatalogBindingContext, ReceiptError, write_component_receipt


class SelectionError(ValueError):
    pass


def _is_reparse(path: Path) -> bool:
    try:
        if stat.S_ISLNK(path.lstat().st_mode):
            return True
    except FileNotFoundError:
        return False
    except OSError:
        return True
    if os.name == "nt":
        try:
            import ctypes

            attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
            attrs = int(attrs) & 0xFFFFFFFF
            return attrs != 0xFFFFFFFF and bool(attrs & 0x400)
        except (AttributeError, OSError):
            return False
    return False


def _state_fingerprint(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _binding_context(component_type: str, record: Mapping[str, Any], catalog_fingerprint: str, supplied: CatalogBindingContext | Mapping[str, Any] | None = None) -> CatalogBindingContext:
    try:
        if supplied is None:
            return CatalogBindingContext.for_v1(component_type=component_type, record=record, catalog_fingerprint=catalog_fingerprint)
        binding = supplied if isinstance(supplied, CatalogBindingContext) else CatalogBindingContext.from_mapping(supplied)
        binding.validate_record(component_type=component_type, record=record)
        return binding
    except (ReceiptError, TypeError, ValueError) as exc:
        code = getattr(exc, "code", "catalog_binding_invalid")
        raise InstallPlanError(str(code)) from None


class ComponentInstaller:
    """Compose, validate and execute only server-owned component plans."""

    def __init__(self, *, paths: HubPaths | None = None, model_manager: ModelManager | None = None, runtime_manager: RuntimeManager | None = None, catalog_binding_provider: Any | None = None) -> None:
        self.paths = paths or get_paths()
        self.model_manager = model_manager or ModelManager(paths=self.paths)
        self.runtime_manager = runtime_manager or RuntimeManager(paths=self.paths)
        self._catalog_binding_provider = catalog_binding_provider if callable(catalog_binding_provider) else None
        self._plans: dict[str, dict[str, Any]] = {}
        self._selections: dict[str, dict[str, Any]] = {}
        self._jobs: dict[str, dict[str, Any]] = {}
        self._cancel_events: dict[str, threading.Event] = {}

    def _model_record(self, component_id: str) -> dict[str, Any] | None:
        try:
            return self.model_manager._record(component_id)  # server-owned catalog lookup
        except Exception:
            return None

    def _runtime_record(self, component_id: str) -> dict[str, Any] | None:
        return next((item for item in self.runtime_manager._records if item["runtime_id"] == component_id), None)

    def _inspect(self, component_id: str, component_type: str, *, catalog_binding: CatalogBindingContext | Mapping[str, Any] | None = None) -> dict[str, Any]:
        if component_type == "model":
            return self.model_manager.inspect(component_id, catalog_binding=catalog_binding)
        if component_type == "runtime":
            return self.runtime_manager.inspect(component_id, catalog_binding=catalog_binding)
        raise InstallPlanError("invalid_component_type")

    @staticmethod
    def _auto_install_ready(record: Mapping[str, Any]) -> bool:
        source = record.get("official_source")
        files = record.get("files") or record.get("required_leaves")
        if not trusted_source(source, fixture_mode=False):
            return False
        if not isinstance(files, list) or len(files) != 1:
            return False
        if record.get("estimated_download_size", 0) <= 0 or record.get("estimated_disk_size", 0) <= 0:
            return False
        if record.get("sha256"):
            return True
        file_item = files[0] if isinstance(files[0], Mapping) else {}
        return bool(file_item.get("sha256") and file_item.get("size_bytes", 0) > 0)

    def _catalog_record(self, component_id: str, component_type: str) -> dict[str, Any]:
        record = self._model_record(component_id) if component_type == "model" else self._runtime_record(component_id)
        if record is None:
            raise InstallPlanError("unknown_component")
        return record

    @staticmethod
    def _same_catalog_binding(left: CatalogBindingContext, right: CatalogBindingContext) -> bool:
        return left.as_record_fields() == right.as_record_fields()

    def _current_catalog_binding_source(self, component_type: str, record: Mapping[str, Any]) -> CatalogBindingContext | Mapping[str, Any] | None:
        provider = self._catalog_binding_provider
        if callable(provider):
            try:
                supplied = provider(component_type, record)
            except Exception:
                return None
            if isinstance(supplied, (CatalogBindingContext, Mapping)):
                return supplied
            return None
        keys = ("catalog_schema", "catalog_revision", "catalog_fingerprint", "source_identity")
        if all(key in record for key in keys):
            return {key: record.get(key) for key in keys}
        return None

    def _fresh_binding_for_plan(self, plan: Mapping[str, Any], *, catalog_binding: CatalogBindingContext | Mapping[str, Any] | None = None) -> tuple[CatalogBindingContext | None, str | None]:
        """Rebuild and compare the current server-owned binding at a boundary."""

        if not isinstance(plan, Mapping):
            return None, "stale_binding"
        component_id = plan.get("component_id")
        component_type = plan.get("component_type")
        if not isinstance(component_id, str) or component_type not in {"model", "runtime"}:
            return None, "stale_binding"
        try:
            record = self._catalog_record(component_id, str(component_type))
            planned_value = plan.get("_catalog_binding")
            if isinstance(planned_value, CatalogBindingContext):
                planned = planned_value
            elif isinstance(planned_value, Mapping):
                planned = CatalogBindingContext.from_mapping(planned_value)
            else:
                return None, "stale_binding"
            current_fingerprint = self.model_manager.catalog_fingerprint if component_type == "model" else self.runtime_manager._catalog_fingerprint()
            if callable(self._catalog_binding_provider):
                source = self._current_catalog_binding_source(str(component_type), record)
                if source is None:
                    return None, "stale_binding"
                current = _binding_context(str(component_type), record, current_fingerprint, source)
            elif catalog_binding is not None:
                current = _binding_context(str(component_type), record, current_fingerprint, catalog_binding)
            elif planned.catalog_schema in {"model-catalog.v1", "runtime-catalog.v1"}:
                current = _binding_context(str(component_type), record, current_fingerprint)
            else:
                source = self._current_catalog_binding_source(str(component_type), record)
                if source is None:
                    return None, "stale_binding"
                current = _binding_context(str(component_type), record, current_fingerprint, source)
            if not self._same_catalog_binding(planned, current):
                return None, "stale_binding"
            planned_fingerprint = plan.get("catalog_fingerprint")
            if planned_fingerprint is not None and planned_fingerprint != current.catalog_fingerprint:
                return None, "stale_binding"
            return current, None
        except (InstallPlanError, ReceiptError, TypeError, ValueError):
            return None, "stale_binding"

    @staticmethod
    def _binding_refusal(plan_id: object, code: str = "stale_binding") -> dict[str, Any]:
        return {
            "status": "conflict",
            "code": code,
            "plan_id": plan_id,
            "execution": "not_run",
            "dry_run": True,
            "next_action": "Create a fresh server-owned component plan.",
        }

    def _dependency_steps(self, component_id: str, component_type: str, record: Mapping[str, Any], *, catalog_binding: CatalogBindingContext | Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        """Build a fixed server-owned dependency graph for one plan."""

        if component_type != "model":
            return []
        runtime_id = record.get("runtime_id")
        if not isinstance(runtime_id, str) or not runtime_id:
            return []
        runtime = self._runtime_record(runtime_id)
        if runtime is None:
            return [{"kind": "runtime", "component_id": runtime_id, "status": "UNAVAILABLE", "disposition": "UNSUPPORTED"}]
        dependency_binding = catalog_binding
        if catalog_binding is not None:
            try:
                context = catalog_binding if isinstance(catalog_binding, CatalogBindingContext) else CatalogBindingContext.from_mapping(catalog_binding)
                if context.catalog_schema == "v7-production-catalog.v2":
                    dependency_binding = CatalogBindingContext.for_v2(
                        catalog_version=context.catalog_revision,
                        catalog_fingerprint=context.catalog_fingerprint,
                        source_identity=runtime.get("source_identity") if "source_identity" in runtime else None,
                    )
                elif context.catalog_schema in {"model-catalog.v1", "runtime-catalog.v1"}:
                    dependency_binding = CatalogBindingContext.for_v1(
                        component_type="runtime",
                        record=runtime,
                        catalog_fingerprint=self.runtime_manager._catalog_fingerprint(),
                    )
            except (ReceiptError, TypeError, ValueError):
                dependency_binding = None
        state = self.runtime_manager.inspect(runtime_id, catalog_binding=dependency_binding)
        return [{"kind": "runtime", "component_id": runtime_id, "status": state.get("status", "UNAVAILABLE"), "disposition": runtime.get("disposition", "MANUAL_INSTALL"), "install_strategy": runtime.get("install_strategy", "reference_existing"), "shared_dependency_id": runtime.get("shared_dependency_id")}]

    def _history_path(self) -> Path:
        return self.paths.config_root / "component_install_history.json"

    def _record_history(self, job_id: str) -> None:
        """Persist bounded, path-free install history for the Jobs/Components UI."""

        job = self._jobs.get(job_id)
        if not isinstance(job, Mapping):
            return
        try:
            current = json.loads(self._history_path().read_text(encoding="utf-8")) if self._history_path().is_file() else {"schema_version": "component-install-history.v1", "records": []}
        except (OSError, UnicodeError, json.JSONDecodeError):
            current = {"schema_version": "component-install-history.v1", "records": []}
        records = current.get("records") if isinstance(current, Mapping) else None
        if not isinstance(records, list):
            records = []
        safe = {key: job.get(key) for key in ("job_id", "plan_id", "component_id", "component_type", "category", "state", "execution", "error") if key in job}
        records = [item for item in records if isinstance(item, Mapping) and item.get("job_id") != job_id]
        records.append(safe)
        records = records[-100:]
        target = self._history_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump({"schema_version": "component-install-history.v1", "records": records}, handle, ensure_ascii=True, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)

    def inspect(self) -> dict[str, Any]:
        models = {item["model_id"]: self.model_manager.inspect(item["model_id"]) for item in self.model_manager._records}
        runtimes = {item["runtime_id"]: self.runtime_manager.inspect(item["runtime_id"]) for item in self.runtime_manager._records}
        records: list[dict[str, Any]] = []
        for item in self.model_manager._records:
            state = models[item["model_id"]]
            records.append({
                "component_id": item["model_id"], "component_type": "model", "display_name": item["display_name"],
                "status": state["status"], "runtime_status": None, "model_status": state["status"],
                "execution": "not_run", "location_class": "models_root", "plan_available": True,
                "reason": state["reason"], "next_action": state["next_action"],
            })
        for item in self.runtime_manager._records:
            state = runtimes[item["runtime_id"]]
            records.append({
                "component_id": item["runtime_id"], "component_type": "runtime", "display_name": item["display_name"],
                "status": state["status"], "runtime_status": state["status"], "model_status": None,
                "execution": "not_run", "location_class": item["root_class"], "plan_available": True,
                "reason": state["reason"], "next_action": state["next_action"],
            })
        graph: dict[str, dict[str, list[str]]] = {}
        for item in self.model_manager._records:
            for module_id in item.get("modules_using_model", []):
                graph.setdefault(str(module_id), {"models": [], "runtimes": []})["models"].append(item["model_id"])
        for item in self.runtime_manager._records:
            for module_id in item.get("modules", []):
                graph.setdefault(str(module_id), {"models": [], "runtimes": []})["runtimes"].append(item["runtime_id"])
        for value in graph.values():
            value["models"] = sorted(set(value["models"]))
            value["runtimes"] = sorted(set(value["runtimes"]))
        return {
            "schema_version": "component-manager.v1", "status": "completed", "execution": "not_run", "dry_run": True,
            "records": sorted(records, key=lambda value: (str(value["component_type"]), str(value["component_id"]))),
            "dependency_graph": graph,
            "jobs": [self.lookup_job(job_id) for job_id in sorted(self._jobs) if self.lookup_job(job_id) is not None],
            "download_history": self.download_history(),
            "reason": "Components are discovered from bounded catalogs; no startup installation or inference occurred.",
            "next_action": "Select one component to create an explicit server-owned install plan.",
        }

    def download_history(self) -> list[dict[str, Any]]:
        try:
            value = json.loads(self._history_path().read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return []
        records = value.get("records") if isinstance(value, Mapping) else None
        return [dict(item) for item in records[-100:] if isinstance(item, Mapping)] if isinstance(records, list) else []

    def snapshot(self) -> dict[str, Any]:
        return self.inspect()

    def detail(self, component_id: str) -> dict[str, Any]:
        """Return one browser-safe component record by fixed ID."""

        component_id = validate_component_id(component_id)
        for item in self.inspect()["records"]:
            if item.get("component_id") == component_id:
                return {"schema_version": "component-detail.v1", "status": "completed", "execution": "not_run", "dry_run": True, "component": item}
        raise InstallPlanError("unknown_component")

    def plan_install(self, component_id: str, *, component_type: str = "model", variant: str | None = None, catalog_binding: CatalogBindingContext | Mapping[str, Any] | None = None) -> dict[str, Any]:
        component_id = validate_component_id(component_id)
        if component_type not in {"model", "runtime"}:
            raise InstallPlanError("invalid_component_type")
        record = self._catalog_record(component_id, component_type)
        default_fingerprint = self.model_manager.catalog_fingerprint if component_type == "model" else self.runtime_manager._catalog_fingerprint()
        binding = _binding_context(component_type, record, default_fingerprint, catalog_binding)
        state = self._inspect(component_id, component_type, catalog_binding=binding)
        dependencies = self._dependency_steps(component_id, component_type, record, catalog_binding=binding)
        projection = safe_component_projection({**record, "component_id": component_id, "component_type": component_type, "location_class": "models_root" if component_type == "model" else record.get("root_class")})
        if variant is not None and variant not in {"default", "low_vram", "portable"}:
            raise InstallPlanError("invalid_component_variant")
        plan_body: dict[str, Any] = {
            "schema_version": "component-install-plan.v1",
            "component_id": component_id,
            "component_type": component_type,
            "variant": variant or "default",
            "catalog_fingerprint": binding.catalog_fingerprint,
            "expected_state_fingerprint": _state_fingerprint(state),
            "existing_status": state["status"],
            "auto_install_supported": self._auto_install_ready(record),
            "location_class": projection["location_class"],
            "source_policy": projection["source_policy"],
            "dependencies": dependencies,
            "bundle_strategy": "composite_server_owned" if dependencies else "single_component",
            "preserve_existing_dependencies": True,
        }
        plan_id = f"install_plan_{secrets.token_hex(16)}"
        fingerprint = plan_fingerprint(plan_body)
        internal = {**plan_body, "plan_id": plan_id, "plan_fingerprint": fingerprint, "record": record, "_catalog_binding": binding, "created_at": time.time()}
        self._plans[plan_id] = internal
        return {
            "schema_version": "component-install-plan.v1", "plan_id": plan_id, "plan_fingerprint": fingerprint,
            "status": "planned", "execution": "not_run", "dry_run": True,
            "component": projection, "existing_status": state["status"],
            "source_policy": projection["source_policy"], "auto_install_supported": bool(plan_body["auto_install_supported"]),
            "download_bytes": int(record.get("estimated_download_size", 0)),
            "estimated_disk_bytes": int(record.get("estimated_disk_size", 0)),
            "dependencies": dependencies,
            "warnings": ["Installation requires explicit confirmation and a fresh stale-plan check."],
            "reason": "The browser supplies only a component ID; destination, source and files remain server-owned.",
            "next_action": "Confirm this exact plan or choose a managed manual-import flow.",
        }

    def confirm_plan(self, plan_id: str, *, confirmed: bool = False, catalog_binding: CatalogBindingContext | Mapping[str, Any] | None = None) -> dict[str, Any]:
        internal = self._plans.get(plan_id)
        if not internal:
            return {"status": "error", "code": "unknown_install_plan"}
        if not confirmed:
            return {"status": "waiting_confirmation", "plan_id": plan_id, "execution": "not_run"}
        current_binding, binding_error = self._fresh_binding_for_plan(internal, catalog_binding=catalog_binding)
        if current_binding is None:
            return self._binding_refusal(plan_id, binding_error or "stale_binding")
        current = self._inspect(internal["component_id"], internal["component_type"], catalog_binding=current_binding)
        if _state_fingerprint(current) != internal["expected_state_fingerprint"]:
            return {"status": "conflict", "code": "stale_install_plan", "plan_id": plan_id, "next_action": "Create a fresh plan."}
        if not internal["auto_install_supported"]:
            return {"status": "unavailable", "code": "trusted_source_metadata_required", "plan_id": plan_id, "execution": "not_run", "next_action": "Use a server-owned native manual import selection or complete trusted catalog metadata."}
        dependency_block = next((item for item in internal.get("dependencies", []) if item.get("status") not in {"INSTALLED", "INSTALLED_UNVERIFIED", "OPERATIONAL"}), None)
        if dependency_block is not None:
            return {"status": "unavailable", "code": "dependency_unavailable", "plan_id": plan_id, "execution": "not_run", "dependency": {key: dependency_block.get(key) for key in ("kind", "component_id", "status", "disposition")}, "next_action": "Review or install the exact server-owned dependency before confirming this bundle."}
        return self.apply_plan(plan_id, confirmed=True, catalog_binding=current_binding)

    def apply_plan(self, plan_id: str, *, confirmed: bool = False, cancel_event: Any | None = None, catalog_binding: CatalogBindingContext | Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Execute a complete trusted one-leaf plan through bounded staging.

        The public API can only reach this method after an opaque plan and an
        explicit confirmation.  Current tracked catalogs intentionally do not
        satisfy the metadata gate, so normal startup and current examples
        never open a network connection.
        """

        plan = self._plans.get(plan_id)
        if not plan or plan.get("schema_version") != "component-install-plan.v1":
            return {"status": "error", "code": "unknown_install_plan", "execution": "not_run"}
        if not confirmed:
            return {"status": "waiting_confirmation", "plan_id": plan_id, "execution": "not_run"}
        current_binding, binding_error = self._fresh_binding_for_plan(plan, catalog_binding=catalog_binding)
        if current_binding is None:
            return self._binding_refusal(plan_id, binding_error or "stale_binding")
        current = self._inspect(plan["component_id"], plan["component_type"], catalog_binding=current_binding)
        if _state_fingerprint(current) != plan["expected_state_fingerprint"]:
            return {"status": "conflict", "code": "stale_install_plan", "plan_id": plan_id, "next_action": "Create a fresh plan."}
        if not plan.get("auto_install_supported"):
            return {"status": "unavailable", "code": "trusted_source_metadata_required", "plan_id": plan_id, "execution": "not_run", "next_action": "Use a server-owned native manual import selection or complete trusted catalog metadata."}
        dependency_block = next((item for item in plan.get("dependencies", []) if item.get("status") not in {"INSTALLED", "INSTALLED_UNVERIFIED", "OPERATIONAL"}), None)
        if dependency_block is not None:
            return {"status": "unavailable", "code": "dependency_unavailable", "plan_id": plan_id, "execution": "not_run", "dependency": {key: dependency_block.get(key) for key in ("kind", "component_id", "status", "disposition")}, "next_action": "Install or reuse the dependency before applying this bundle."}
        try:
            record = self._catalog_record(str(plan["component_id"]), str(plan["component_type"]))
        except InstallPlanError:
            return {"status": "unavailable", "code": "catalog_record_unavailable", "plan_id": plan_id, "execution": "not_run", "dry_run": True}
        source = record.get("official_source")
        if not trusted_source(source, fixture_mode=False):
            return {"status": "unavailable", "code": "source_not_trusted", "plan_id": plan_id, "execution": "not_run"}
        try:
            free = int(shutil.disk_usage(self.paths.data_root).free)
        except OSError:
            free = 0
        required = int(record.get("estimated_download_size", 0)) + int(record.get("estimated_disk_size", 0)) + 256 * 1024 * 1024
        if free < required:
            return {"status": "unavailable", "code": "insufficient_disk", "plan_id": plan_id, "execution": "not_run", "next_action": "Free the required staging and installation margin before retrying."}
        job_id = f"component_job_{secrets.token_hex(12)}"
        self._jobs[job_id] = {"job_id": job_id, "plan_id": plan_id, "component_id": plan["component_id"], "component_type": plan["component_type"], "category": "component_install", "state": "DOWNLOADING", "execution": "running"}
        event = cancel_event if isinstance(cancel_event, threading.Event) else threading.Event()
        self._cancel_events[job_id] = event
        stage = self.paths.temp_root / "component-install" / plan_id
        try:
            stage.mkdir(parents=True, exist_ok=True)
            files = record.get("files") if isinstance(record.get("files"), list) else []
            if plan["component_type"] == "model" and len(files) == 1 and isinstance(files[0], Mapping):
                destination_name = Path(str(files[0]["relative_path"])).name
                expected_size = int(files[0].get("size_bytes", 0))
                expected_hash = files[0].get("sha256") or record.get("sha256")
            else:
                destination_name = f"{plan['component_id']}.package"
                expected_size = int(record.get("estimated_download_size", 0))
                expected_hash = record.get("sha256")
            result = TrustedDownloader(staging_root=stage, max_bytes=max(required, 1)).download(
                str(source), destination_name, expected_sha256=expected_hash, expected_size=expected_size,
                disk_free_bytes=free, disk_safety_bytes=256 * 1024 * 1024, cancel_event=event,
            )
            self._jobs[job_id]["state"] = "VERIFYING"
            if plan["component_type"] == "model":
                finalized = self.model_manager.install_staged_file(plan["component_id"], result.staged_path)
            else:
                finalized = self.runtime_manager.install_staged_archive(plan["component_id"], result.staged_path)
            if finalized.get("status") != "completed":
                self._jobs[job_id].update({"state": "FAILED", "execution": "not_run", "error": str(finalized.get("code") or "finalization_failed")})
                self._record_history(job_id)
                self._cancel_events.pop(job_id, None)
                return {"status": "failed", "code": "finalization_failed", "job_id": job_id, "plan_id": plan_id, "execution": "not_run"}
            try:
                binding = current_binding
                root_class = "models_root" if plan["component_type"] == "model" else record.get("root_class", "runtime_root")
                managed_root = resolve_component_root(self.paths, plan["component_id"], plan["component_type"], root_class, require_exists=True)
                if plan["component_type"] == "model":
                    receipt_leaves = [
                        {"relative_leaf": item.get("relative_path"), "observed_size_bytes": resolve_component_leaf(managed_root, item.get("relative_path"), require_exists=True).stat().st_size, "observed_mtime_ns": resolve_component_leaf(managed_root, item.get("relative_path"), require_exists=True).stat().st_mtime_ns, "verification_level": "unverified"}
                        for item in record.get("files", []) if isinstance(item, Mapping)
                    ]
                else:
                    receipt_leaves = [
                        {"relative_leaf": item, "observed_size_bytes": resolve_component_leaf(managed_root, item, require_exists=True).stat().st_size, "observed_mtime_ns": resolve_component_leaf(managed_root, item, require_exists=True).stat().st_mtime_ns, "verification_level": "unverified"}
                        for item in record.get("required_leaves", []) if isinstance(item, str)
                    ]
                write_component_receipt(self.paths.config_root, plan["component_id"], {
                    "component_id": plan["component_id"],
                    "component_type": plan["component_type"],
                    "bundle_revision": str(record.get("revision") or "unknown"),
                    "source": "catalog_primary",
                    "source_identity": binding.source_identity,
                    "root_class": "models_root" if plan["component_type"] == "model" else record.get("root_class", "runtime_root"),
                    "location_class": "models_root" if plan["component_type"] == "model" else record.get("root_class", "runtime_root"),
                    "files": receipt_leaves,
                    "installed_at": int(time.time()),
                    "verified_at": None,
                    "state": "INSTALLED_UNVERIFIED",
                    "operational": False,
                    "previous_version": None,
                    "rollback_candidate": None,
                    **binding.as_record_fields(),
                }, catalog_binding=binding)
            except (OSError, ComponentPathError, ValueError, TypeError):
                self._jobs[job_id].update({"state": "FAILED", "execution": "not_run", "error": "receipt_write_failed"})
                self._record_history(job_id)
                self._cancel_events.pop(job_id, None)
                return {"status": "failed", "code": "receipt_write_failed", "job_id": job_id, "plan_id": plan_id, "execution": "not_run"}
            self._jobs[job_id].update({"state": "COMPLETED", "execution": "completed"})
            self._record_history(job_id)
            self._cancel_events.pop(job_id, None)
            return {"status": "completed", "job_id": job_id, "plan_id": plan_id, "component_id": plan["component_id"], "state": "INSTALLED_UNVERIFIED", "execution": "completed", "receipt": "written", "next_action": "Refresh the component snapshot; bounded runtime evidence is still required."}
        except DownloadError as exc:
            state = "CANCELLED" if exc.code == "cancelled" else "FAILED"
            self._jobs[job_id].update({"state": state, "execution": "not_run", "error": exc.code})
            self._record_history(job_id)
            self._cancel_events.pop(job_id, None)
            return {"status": "cancelled" if state == "CANCELLED" else "failed", "code": exc.code, "job_id": job_id, "plan_id": plan_id, "execution": "not_run"}
        except (OSError, ValueError):
            self._jobs[job_id].update({"state": "FAILED", "execution": "not_run", "error": "component_install_failed"})
            self._record_history(job_id)
            self._cancel_events.pop(job_id, None)
            return {"status": "failed", "code": "component_install_failed", "job_id": job_id, "plan_id": plan_id, "execution": "not_run"}

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        job = self._jobs.get(job_id)
        if not isinstance(job, Mapping):
            return {"status": "error", "code": "unknown_component_job", "job_id": job_id}
        if job.get("state") in {"COMPLETED", "FAILED", "CANCELLED"}:
            return {"status": "conflict", "code": "component_job_terminal", "job_id": job_id, "state": job.get("state")}
        event = self._cancel_events.get(job_id)
        if event is None:
            return {"status": "unavailable", "code": "component_job_not_running", "job_id": job_id, "execution": "not_run"}
        event.set()
        job["state"] = "CANCELLING"
        return {"status": "cancelling", "job_id": job_id, "execution": "running", "next_action": "The downloader will stop at its next bounded chunk and preserve valid partial staging."}

    def lookup_plan(self, plan_id: str) -> dict[str, Any] | None:
        """Return only the safe public projection of an in-memory plan."""

        if not isinstance(plan_id, str) or not plan_id or len(plan_id) > 120:
            return None
        plan = self._plans.get(plan_id)
        if not plan:
            return None
        result = {
            key: plan.get(key)
            for key in (
                "schema_version", "plan_id", "plan_fingerprint", "component_id",
                "component_type", "variant", "status", "execution", "dry_run",
                "expected_state_fingerprint", "action", "current_status",
                "dependencies", "bundle_strategy", "preserve_existing_dependencies",
            )
            if key in plan
        }
        if plan.get("schema_version") == "component-install-plan.v1":
            result["status"] = "planned"
        result.setdefault("execution", "not_run")
        result.setdefault("dry_run", True)
        return result

    def lookup_job(self, job_id: str) -> dict[str, Any] | None:
        if not isinstance(job_id, str) or not job_id or len(job_id) > 120:
            return None
        job = self._jobs.get(job_id)
        if not isinstance(job, Mapping):
            return None
        return {key: job.get(key) for key in ("job_id", "plan_id", "component_id", "component_type", "category", "state", "execution", "error") if key in job}

    def plan_verify(self, component_id: str, *, component_type: str, catalog_binding: CatalogBindingContext | Mapping[str, Any] | None = None) -> dict[str, Any]:
        component_id = validate_component_id(component_id)
        if component_type not in {"model", "runtime"}:
            raise InstallPlanError("invalid_component_type")
        record = self._catalog_record(component_id, component_type)
        default_fingerprint = self.model_manager.catalog_fingerprint if component_type == "model" else self.runtime_manager._catalog_fingerprint()
        binding = _binding_context(component_type, record, default_fingerprint, catalog_binding)
        state = self._inspect(component_id, component_type, catalog_binding=binding)
        body = {
            "schema_version": "component-verify-plan.v1",
            "component_id": component_id,
            "component_type": component_type,
            "expected_state_fingerprint": _state_fingerprint(state),
        }
        plan_id = f"verify_plan_{secrets.token_hex(16)}"
        fingerprint = plan_fingerprint(body)
        self._plans[plan_id] = {**body, "plan_id": plan_id, "plan_fingerprint": fingerprint, "_catalog_binding": binding, "created_at": time.time()}
        return {
            **body,
            "plan_id": plan_id,
            "plan_fingerprint": fingerprint,
            "status": "planned",
            "execution": "not_run",
            "dry_run": True,
            "current_status": state["status"],
            "reason": "Verification plan is read-only and does not execute a model, runtime or smoke.",
            "next_action": "Confirm a separately authorized bounded verification operation.",
        }

    def confirm_verify(self, plan_id: str, *, confirmed: bool = False, catalog_binding: CatalogBindingContext | Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Run the explicit receipt-only deep verifier after confirmation."""

        plan = self._plans.get(plan_id)
        if not isinstance(plan, Mapping) or plan.get("schema_version") != "component-verify-plan.v1":
            return {"status": "error", "code": "unknown_verify_plan", "execution": "not_run", "dry_run": True}
        if not confirmed:
            return {"status": "waiting_confirmation", "plan_id": plan_id, "execution": "not_run", "dry_run": True}
        component_id = plan.get("component_id")
        component_type = plan.get("component_type")
        if not isinstance(component_id, str) or not isinstance(component_type, str) or component_type not in {"model", "runtime"}:
            return {"status": "error", "code": "verify_plan_invalid", "plan_id": plan_id, "execution": "not_run", "dry_run": True}
        current_binding, binding_error = self._fresh_binding_for_plan(plan, catalog_binding=catalog_binding)
        if current_binding is None:
            return self._binding_refusal(plan_id, binding_error or "stale_binding")
        current = self._inspect(component_id, str(component_type), catalog_binding=current_binding)
        if _state_fingerprint(current) != plan.get("expected_state_fingerprint"):
            return {"status": "conflict", "code": "stale_verify_plan", "plan_id": plan_id, "execution": "not_run", "dry_run": True, "next_action": "Create a fresh verification plan."}
        if component_type == "model":
            result = self.model_manager.verify(component_id, catalog_binding=current_binding)
        else:
            result = self.runtime_manager.verify(component_id, catalog_binding=current_binding)
        result["plan_id"] = plan_id
        result.setdefault("execution", "not_run")
        result.setdefault("dry_run", True)
        return result

    def issue_selection(self, component_id: str, selected: Path, *, component_type: str = "model") -> dict[str, Any]:
        component_id = validate_component_id(component_id)
        if component_type != "model" or self._model_record(component_id) is None:
            raise SelectionError("selection_component_not_allowed")
        candidate = Path(selected).absolute()
        if not candidate.exists() or _is_reparse(candidate) or candidate.is_file() and candidate.stat().st_size <= 0:
            raise SelectionError("selection_unavailable")
        current = candidate
        while True:
            if current.exists() and _is_reparse(current):
                raise SelectionError("selection_reparse_unsupported")
            if current.parent == current:
                break
            current = current.parent
        selection_id = f"selection_{secrets.token_hex(16)}"
        self._selections[selection_id] = {"selection_id": selection_id, "component_id": component_id, "component_type": component_type, "path": candidate, "expires_at": time.time() + 300}
        return {"status": "ready", "selection_id": selection_id, "component_id": component_id, "location_class": "native_selection", "expires_in_seconds": 300}

    def plan_import(self, selection_id: str, *, mode: str = "COPY_INTO_MANAGED_MODELS") -> dict[str, Any]:
        selection = self._selections.get(selection_id)
        if not selection or selection["expires_at"] < time.time():
            raise SelectionError("selection_expired")
        if mode not in {"COPY_INTO_MANAGED_MODELS", "REFERENCE_EXISTING_MANAGED_LOCATION"}:
            raise SelectionError("invalid_import_mode")
        record = self._model_record(selection["component_id"])
        assert record is not None
        plan_body = {"schema_version": "model-import-plan.v1", "component_id": selection["component_id"], "component_type": "model", "mode": mode, "catalog_fingerprint": self.model_manager.catalog_fingerprint, "selection_id": selection_id}
        plan_id = f"import_plan_{secrets.token_hex(16)}"
        fingerprint = plan_fingerprint(plan_body)
        self._plans[plan_id] = {**plan_body, "plan_id": plan_id, "plan_fingerprint": fingerprint, "selection": selection}
        return {"schema_version": "model-import-plan.v1", "plan_id": plan_id, "plan_fingerprint": fingerprint, "status": "planned", "execution": "not_run", "dry_run": True, "component": safe_component_projection({**record, "component_id": selection["component_id"], "component_type": "model", "location_class": "models_root"}), "mode": mode, "selection_id": selection_id, "next_action": "Confirm the exact import plan."}

    def confirm_import(self, plan_id: str, *, confirmed: bool = False) -> dict[str, Any]:
        plan = self._plans.get(plan_id)
        if not plan or plan.get("schema_version") != "model-import-plan.v1":
            return {"status": "error", "code": "unknown_import_plan"}
        if not confirmed:
            return {"status": "waiting_confirmation", "plan_id": plan_id, "execution": "not_run"}
        selection = plan.get("selection") if isinstance(plan.get("selection"), Mapping) else {}
        if not selection or float(selection.get("expires_at", 0)) < time.time():
            return {"status": "conflict", "code": "selection_expired", "plan_id": plan_id, "execution": "not_run"}
        result = ManualImportExecutor(paths=self.paths, model_manager=self.model_manager, runtime_manager=self.runtime_manager).apply(plan, confirmed=True)
        result["plan_id"] = plan_id
        if result.get("status") == "completed":
            self._selections.pop(str(selection.get("selection_id")), None)
        return result

    def plan_reuse(self, component_id: str, *, component_type: str = "model", catalog_binding: CatalogBindingContext | Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Plan a receipt-only reuse of a complete managed installation."""

        component_id = validate_component_id(component_id)
        if component_type not in {"model", "runtime"}:
            raise InstallPlanError("invalid_component_type")
        record = self._catalog_record(component_id, component_type)
        default_fingerprint = self.model_manager.catalog_fingerprint if component_type == "model" else self.runtime_manager._catalog_fingerprint()
        binding = _binding_context(component_type, record, default_fingerprint, catalog_binding)
        state = self._inspect(component_id, component_type, catalog_binding=binding)
        body = {
            "schema_version": "component-reuse-plan.v1",
            "component_id": component_id,
            "component_type": component_type,
            "catalog_fingerprint": binding.catalog_fingerprint,
            "expected_state_fingerprint": _state_fingerprint(state),
            "current_status": state.get("status"),
            "source": "existing_install_reuse",
            "execution": "not_run",
            "dry_run": True,
        }
        plan_id = f"reuse_plan_{secrets.token_hex(16)}"
        fingerprint = plan_fingerprint(body)
        self._plans[plan_id] = {**body, "plan_id": plan_id, "plan_fingerprint": fingerprint, "record": record, "_catalog_binding": binding}
        return {**body, "plan_id": plan_id, "plan_fingerprint": fingerprint, "status": "planned", "next_action": "Confirm only if every catalog leaf is already present under the managed root."}

    def confirm_reuse(self, plan_id: str, *, confirmed: bool = False, catalog_binding: CatalogBindingContext | Mapping[str, Any] | None = None) -> dict[str, Any]:
        plan = self._plans.get(plan_id)
        if not isinstance(plan, Mapping) or plan.get("schema_version") != "component-reuse-plan.v1":
            return {"status": "error", "code": "unknown_reuse_plan", "execution": "not_run"}
        if not confirmed:
            return {"status": "waiting_confirmation", "plan_id": plan_id, "execution": "not_run", "dry_run": True}
        current_binding, binding_error = self._fresh_binding_for_plan(plan, catalog_binding=catalog_binding)
        if current_binding is None:
            return self._binding_refusal(plan_id, binding_error or "stale_binding")
        current = self._inspect(str(plan["component_id"]), str(plan["component_type"]), catalog_binding=current_binding)
        if _state_fingerprint(current) != plan.get("expected_state_fingerprint"):
            return {"status": "conflict", "code": "stale_reuse_plan", "plan_id": plan_id, "execution": "not_run", "next_action": "Create a fresh existing-install reuse plan."}
        result = ExistingInstallReuseExecutor(paths=self.paths, manager=self).apply(plan, confirmed=True, catalog_binding=current_binding)
        result["plan_id"] = plan_id
        return result

    def plan_maintenance(self, component_id: str, *, action: str, catalog_binding: CatalogBindingContext | Mapping[str, Any] | None = None) -> dict[str, Any]:
        component_id = validate_component_id(component_id)
        if action not in {"repair", "update", "uninstall"}:
            raise InstallPlanError("invalid_maintenance_action")
        kind = "model" if self._model_record(component_id) else "runtime" if self._runtime_record(component_id) else None
        if kind is None:
            raise InstallPlanError("unknown_component")
        record = self._catalog_record(component_id, kind)
        default_fingerprint = self.model_manager.catalog_fingerprint if kind == "model" else self.runtime_manager._catalog_fingerprint()
        binding = _binding_context(kind, record, default_fingerprint, catalog_binding)
        state = self._inspect(component_id, kind, catalog_binding=binding)
        body = {"schema_version": "component-maintenance-plan.v1", "component_id": component_id, "component_type": kind, "action": action, "expected_state_fingerprint": _state_fingerprint(state), "catalog_fingerprint": binding.catalog_fingerprint, "shared_dependency_policy": "preserve_referenced_assets"}
        plan_id = f"maintenance_plan_{secrets.token_hex(16)}"
        fingerprint = plan_fingerprint(body)
        self._plans[plan_id] = {**body, "plan_id": plan_id, "plan_fingerprint": fingerprint, "_catalog_binding": binding}
        return {**body, "plan_id": plan_id, "plan_fingerprint": fingerprint, "status": "planned", "execution": "not_run", "dry_run": True, "current_status": state["status"], "next_action": "Review the plan and explicit destructive confirmation policy."}

    def confirm_maintenance(self, plan_id: str, *, confirmed: bool = False, catalog_binding: CatalogBindingContext | Mapping[str, Any] | None = None) -> dict[str, Any]:
        plan = self._plans.get(plan_id)
        if not plan or not plan.get("action"):
            return {"status": "error", "code": "unknown_maintenance_plan"}
        if not confirmed:
            return {"status": "waiting_confirmation", "plan_id": plan_id, "execution": "not_run"}
        current_binding, binding_error = self._fresh_binding_for_plan(plan, catalog_binding=catalog_binding)
        if current_binding is None:
            return self._binding_refusal(plan_id, binding_error or "stale_binding")
        current = self._inspect(plan["component_id"], plan["component_type"], catalog_binding=current_binding)
        if _state_fingerprint(current) != plan["expected_state_fingerprint"]:
            return {"status": "conflict", "code": "stale_maintenance_plan", "plan_id": plan_id}
        if plan.get("action") == "repair":
            from .receipts import ReceiptError, write_component_receipt
            from .verification import DeepComponentVerifier

            record = self._catalog_record(plan["component_id"], plan["component_type"])
            catalog_fingerprint = self.model_manager.catalog_fingerprint if plan["component_type"] == "model" else self.runtime_manager._catalog_fingerprint()
            verified = DeepComponentVerifier().verify(
                paths=self.paths,
                component_id=plan["component_id"],
                component_type=plan["component_type"],
                record=record,
                catalog_fingerprint=catalog_fingerprint,
                catalog_binding=current_binding,
                source="existing_install_reuse",
            )
            if verified.get("status") != "completed":
                return {"status": verified.get("status", "unavailable"), "code": verified.get("code", "repair_unavailable"), "plan_id": plan_id, "execution": "not_run", "dry_run": True, "next_action": verified.get("next_action", "Review the managed installation.")}
            try:
                write_component_receipt(self.paths.config_root, plan["component_id"], verified["receipt"], catalog_binding=current_binding)
            except (OSError, ReceiptError, KeyError, TypeError):
                return {"status": "unavailable", "code": "receipt_write_failed", "plan_id": plan_id, "execution": "not_run", "dry_run": True, "next_action": "Retry the explicit receipt-only repair after reviewing receipt storage."}
            return {"status": "completed", "action": "repair", "component_id": plan["component_id"], "state": verified["state"], "execution": "not_run", "dry_run": True, "verified": verified["state"] == "INSTALLED_VERIFIED", "operational": False, "next_action": verified["next_action"]}
        result = MaintenanceExecutor(paths=self.paths, catalog=self._catalog_adapter()).apply(plan, confirmed=True)
        result["plan_id"] = plan_id
        return result

    def _catalog_adapter(self) -> Any:
        """Expose the fixed model/runtime records to the maintenance executor."""

        class _Catalog:
            models = {item["model_id"]: item for item in self.model_manager._records}
            runtimes = {item["runtime_id"]: item for item in self.runtime_manager._records}

        return _Catalog()

    def install_fixture(self, component_id: str, source_root: Path) -> dict[str, Any]:
        """Backend-only synthetic acceptance hook; never exposed as an API route."""

        if self._runtime_record(component_id) is not None:
            return self.runtime_manager.install_fixture(component_id, source_root)
        return self.model_manager.install_fixture(component_id, source_root)


__all__ = ["ComponentInstaller", "SelectionError", "INSTALL_JOB_STATES"]
