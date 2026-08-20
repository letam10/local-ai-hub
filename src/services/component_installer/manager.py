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

from src.platform.paths import HubPaths, get_paths
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


class ComponentInstaller:
    """Compose, validate and execute only server-owned component plans."""

    def __init__(self, *, paths: HubPaths | None = None, model_manager: ModelManager | None = None, runtime_manager: RuntimeManager | None = None) -> None:
        self.paths = paths or get_paths()
        self.model_manager = model_manager or ModelManager(paths=self.paths)
        self.runtime_manager = runtime_manager or RuntimeManager(paths=self.paths)
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

    def _inspect(self, component_id: str, component_type: str) -> dict[str, Any]:
        if component_type == "model":
            return self.model_manager.inspect(component_id)
        if component_type == "runtime":
            return self.runtime_manager.inspect(component_id)
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
            "reason": "Components are discovered from bounded catalogs; no startup installation or inference occurred.",
            "next_action": "Select one component to create an explicit server-owned install plan.",
        }

    def snapshot(self) -> dict[str, Any]:
        return self.inspect()

    def detail(self, component_id: str) -> dict[str, Any]:
        """Return one browser-safe component record by fixed ID."""

        component_id = validate_component_id(component_id)
        for item in self.inspect()["records"]:
            if item.get("component_id") == component_id:
                return {"schema_version": "component-detail.v1", "status": "completed", "execution": "not_run", "dry_run": True, "component": item}
        raise InstallPlanError("unknown_component")

    def plan_install(self, component_id: str, *, component_type: str = "model", variant: str | None = None) -> dict[str, Any]:
        component_id = validate_component_id(component_id)
        if component_type not in {"model", "runtime"}:
            raise InstallPlanError("invalid_component_type")
        record = self._catalog_record(component_id, component_type)
        state = self._inspect(component_id, component_type)
        projection = safe_component_projection({**record, "component_id": component_id, "component_type": component_type, "location_class": "models_root" if component_type == "model" else record.get("root_class")})
        if variant is not None and variant not in {"default", "low_vram", "portable"}:
            raise InstallPlanError("invalid_component_variant")
        plan_body: dict[str, Any] = {
            "schema_version": "component-install-plan.v1",
            "component_id": component_id,
            "component_type": component_type,
            "variant": variant or "default",
            "catalog_fingerprint": self.model_manager.catalog_fingerprint if component_type == "model" else _state_fingerprint({"runtimes": self.runtime_manager._records}),
            "expected_state_fingerprint": _state_fingerprint(state),
            "existing_status": state["status"],
            "auto_install_supported": self._auto_install_ready(record),
            "location_class": projection["location_class"],
            "source_policy": projection["source_policy"],
        }
        plan_id = f"install_plan_{secrets.token_hex(16)}"
        fingerprint = plan_fingerprint(plan_body)
        internal = {**plan_body, "plan_id": plan_id, "plan_fingerprint": fingerprint, "record": record, "created_at": time.time()}
        self._plans[plan_id] = internal
        return {
            "schema_version": "component-install-plan.v1", "plan_id": plan_id, "plan_fingerprint": fingerprint,
            "status": "planned", "execution": "not_run", "dry_run": True,
            "component": projection, "existing_status": state["status"],
            "source_policy": projection["source_policy"], "auto_install_supported": bool(plan_body["auto_install_supported"]),
            "download_bytes": int(record.get("estimated_download_size", 0)),
            "estimated_disk_bytes": int(record.get("estimated_disk_size", 0)),
            "warnings": ["Installation requires explicit confirmation and a fresh stale-plan check."],
            "reason": "The browser supplies only a component ID; destination, source and files remain server-owned.",
            "next_action": "Confirm this exact plan or choose a managed manual-import flow.",
        }

    def confirm_plan(self, plan_id: str, *, confirmed: bool = False) -> dict[str, Any]:
        internal = self._plans.get(plan_id)
        if not internal:
            return {"status": "error", "code": "unknown_install_plan"}
        if not confirmed:
            return {"status": "waiting_confirmation", "plan_id": plan_id, "execution": "not_run"}
        current = self._inspect(internal["component_id"], internal["component_type"])
        if _state_fingerprint(current) != internal["expected_state_fingerprint"]:
            return {"status": "conflict", "code": "stale_install_plan", "plan_id": plan_id, "next_action": "Create a fresh plan."}
        if not internal["auto_install_supported"]:
            return {"status": "unavailable", "code": "trusted_source_metadata_required", "plan_id": plan_id, "execution": "not_run", "next_action": "Use a server-owned native manual import selection or complete trusted catalog metadata."}
        return self.apply_plan(plan_id, confirmed=True)

    def apply_plan(self, plan_id: str, *, confirmed: bool = False, cancel_event: Any | None = None) -> dict[str, Any]:
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
        current = self._inspect(plan["component_id"], plan["component_type"])
        if _state_fingerprint(current) != plan["expected_state_fingerprint"]:
            return {"status": "conflict", "code": "stale_install_plan", "plan_id": plan_id, "next_action": "Create a fresh plan."}
        if not plan.get("auto_install_supported"):
            return {"status": "unavailable", "code": "trusted_source_metadata_required", "plan_id": plan_id, "execution": "not_run", "next_action": "Use a server-owned native manual import selection or complete trusted catalog metadata."}
        record = plan.get("record") if isinstance(plan.get("record"), Mapping) else {}
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
                self._cancel_events.pop(job_id, None)
                return {"status": "failed", "code": "finalization_failed", "job_id": job_id, "plan_id": plan_id, "execution": "not_run"}
            self._jobs[job_id].update({"state": "COMPLETED", "execution": "completed"})
            self._cancel_events.pop(job_id, None)
            return {"status": "completed", "job_id": job_id, "plan_id": plan_id, "component_id": plan["component_id"], "state": "INSTALLED_UNVERIFIED", "execution": "completed", "receipt": "written", "next_action": "Refresh the component snapshot; bounded runtime evidence is still required."}
        except DownloadError as exc:
            state = "CANCELLED" if exc.code == "cancelled" else "FAILED"
            self._jobs[job_id].update({"state": state, "execution": "not_run", "error": exc.code})
            self._cancel_events.pop(job_id, None)
            return {"status": "cancelled" if state == "CANCELLED" else "failed", "code": exc.code, "job_id": job_id, "plan_id": plan_id, "execution": "not_run"}
        except (OSError, ValueError):
            self._jobs[job_id].update({"state": "FAILED", "execution": "not_run", "error": "component_install_failed"})
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

    def plan_verify(self, component_id: str, *, component_type: str) -> dict[str, Any]:
        component_id = validate_component_id(component_id)
        if component_type not in {"model", "runtime"}:
            raise InstallPlanError("invalid_component_type")
        state = self._inspect(component_id, component_type)
        body = {
            "schema_version": "component-verify-plan.v1",
            "component_id": component_id,
            "component_type": component_type,
            "expected_state_fingerprint": _state_fingerprint(state),
        }
        plan_id = f"verify_plan_{secrets.token_hex(16)}"
        fingerprint = plan_fingerprint(body)
        self._plans[plan_id] = {**body, "plan_id": plan_id, "plan_fingerprint": fingerprint, "created_at": time.time()}
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
        self._selections[selection_id] = {"component_id": component_id, "component_type": component_type, "path": candidate, "expires_at": time.time() + 300}
        return {"status": "ready", "selection_id": selection_id, "component_id": component_id, "location_class": "native_selection", "expires_in_seconds": 300}

    def plan_import(self, selection_id: str, *, mode: str = "COPY_INTO_MANAGED_MODELS") -> dict[str, Any]:
        selection = self._selections.get(selection_id)
        if not selection or selection["expires_at"] < time.time():
            raise SelectionError("selection_expired")
        if mode not in {"COPY_INTO_MANAGED_MODELS", "REFERENCE_EXISTING_MANAGED_LOCATION"}:
            raise SelectionError("invalid_import_mode")
        record = self._model_record(selection["component_id"])
        assert record is not None
        plan_body = {"schema_version": "model-import-plan.v1", "component_id": selection["component_id"], "mode": mode, "catalog_fingerprint": self.model_manager.catalog_fingerprint, "selection_id": selection_id}
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
        return {
            "status": "unavailable", "code": "manager_executor_required", "plan_id": plan_id,
            "execution": "not_run", "next_action": "A native manager executor must revalidate the selection and exact import plan.",
        }

    def plan_maintenance(self, component_id: str, *, action: str) -> dict[str, Any]:
        component_id = validate_component_id(component_id)
        if action not in {"repair", "update", "uninstall"}:
            raise InstallPlanError("invalid_maintenance_action")
        kind = "model" if self._model_record(component_id) else "runtime" if self._runtime_record(component_id) else None
        if kind is None:
            raise InstallPlanError("unknown_component")
        state = self._inspect(component_id, kind)
        body = {"schema_version": "component-maintenance-plan.v1", "component_id": component_id, "component_type": kind, "action": action, "expected_state_fingerprint": _state_fingerprint(state), "shared_dependency_policy": "preserve_referenced_assets"}
        plan_id = f"maintenance_plan_{secrets.token_hex(16)}"
        fingerprint = plan_fingerprint(body)
        self._plans[plan_id] = {**body, "plan_id": plan_id, "plan_fingerprint": fingerprint}
        return {**body, "plan_id": plan_id, "plan_fingerprint": fingerprint, "status": "planned", "execution": "not_run", "dry_run": True, "current_status": state["status"], "next_action": "Review the plan and explicit destructive confirmation policy."}

    def confirm_maintenance(self, plan_id: str, *, confirmed: bool = False) -> dict[str, Any]:
        plan = self._plans.get(plan_id)
        if not plan or not plan.get("action"):
            return {"status": "error", "code": "unknown_maintenance_plan"}
        if not confirmed:
            return {"status": "waiting_confirmation", "plan_id": plan_id, "execution": "not_run"}
        current = self._inspect(plan["component_id"], plan["component_type"])
        if _state_fingerprint(current) != plan["expected_state_fingerprint"]:
            return {"status": "conflict", "code": "stale_maintenance_plan", "plan_id": plan_id}
        return {"status": "unavailable", "code": "manager_executor_required", "plan_id": plan_id, "execution": "not_run", "next_action": "A separately authorized native executor must perform the reviewed maintenance plan."}

    def install_fixture(self, component_id: str, source_root: Path) -> dict[str, Any]:
        """Backend-only synthetic acceptance hook; never exposed as an API route."""

        if self._runtime_record(component_id) is not None:
            return self.runtime_manager.install_fixture(component_id, source_root)
        return self.model_manager.install_fixture(component_id, source_root)


__all__ = ["ComponentInstaller", "SelectionError", "INSTALL_JOB_STATES"]
