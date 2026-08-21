"""Manual update resolver and throttled schedule state for V7.

This module checks metadata only.  It never downloads, activates, replaces or
removes a component.  Applying an update remains owned by a separate explicit
component executor and requires a fresh plan fingerprint.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any

from src.platform.paths import HubPaths, get_paths
from src.services.component_installer.receipts import CatalogBindingContext, ReceiptError

from .source_availability import SOURCE_STATUSES, SourceAvailabilityService, source_status_projection


UPDATE_STATUSES = frozenset({
    "UP_TO_DATE",
    "UPDATE_AVAILABLE",
    "UPSTREAM_NEWER_UNSUPPORTED",
    "SOURCE_UNAVAILABLE",
    "AUTH_REQUIRED",
    "CHECK_FAILED",
    "NOT_INSTALLED",
})
SCHEDULE_POLICIES = frozenset({"manual", "startup_24h", "daily", "weekly"})
_ID_RE = re.compile(r"^[a-z][a-z0-9._-]{0,95}$")


def _safe_id(value: object) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError("invalid_component_id")
    return value


def _fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        handle, name = tempfile.mkstemp(prefix=".update-state-", suffix=".tmp", dir=path.parent)
        os.close(handle)
        temporary = Path(name)
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=True, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


class UpdateSchedule:
    """Persist safe manual/startup/daily/weekly metadata-check policy."""

    def __init__(self, *, paths: HubPaths | None = None) -> None:
        self.paths = paths or get_paths()
        self.path = self.paths.config_root / "update_check_settings.json"

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            value = {}
        policy = value.get("policy") if isinstance(value, Mapping) else None
        if policy not in SCHEDULE_POLICIES:
            policy = "manual"
        last_checked = value.get("last_checked") if isinstance(value, Mapping) and isinstance(value.get("last_checked"), int) else None
        return {"schema_version": "update-check-settings.v1", "policy": policy, "last_checked": last_checked}

    def get(self) -> dict[str, Any]:
        return self._read()

    def set_policy(self, policy: str) -> dict[str, Any]:
        if policy not in SCHEDULE_POLICIES:
            return {"status": "invalid", "code": "invalid_update_schedule", "allowed": sorted(SCHEDULE_POLICIES)}
        value = self._read()
        value["policy"] = policy
        _atomic_write(self.path, value)
        return {"status": "saved", **value}

    def mark_checked(self, timestamp: int | None = None) -> dict[str, Any]:
        value = self._read()
        value["last_checked"] = int(time.time()) if timestamp is None else int(timestamp)
        _atomic_write(self.path, value)
        return value

    def due(self, *, now: int | None = None) -> bool:
        value = self._read()
        policy = value["policy"]
        if policy == "manual":
            return False
        last = value.get("last_checked")
        if not isinstance(last, int):
            return True
        current = int(time.time()) if now is None else int(now)
        interval = {"startup_24h": 24 * 60 * 60, "daily": 24 * 60 * 60, "weekly": 7 * 24 * 60 * 60}[policy]
        return current - last >= interval


class UpdateResolver:
    """Resolve component update metadata without any automatic apply."""

    def __init__(self, *, paths: HubPaths | None = None, catalog: Any | None = None, source_service: SourceAvailabilityService | None = None) -> None:
        self.paths = paths or get_paths()
        if catalog is None:
            from src.services.productization.catalog import ProductionCatalog
            catalog = ProductionCatalog(paths=self.paths)
        self.catalog = catalog
        self.sources = source_service or SourceAvailabilityService(paths=self.paths)
        self.schedule = UpdateSchedule(paths=self.paths)
        # _plans is the read-only public lookup projection used by the API
        # context. Private candidates, records and typed bindings live in a
        # separate server-owned map and never cross that lookup boundary.
        self._plans: dict[str, dict[str, Any]] = {}
        self._private_plans: dict[str, dict[str, Any]] = {}
        self._applied_plans: dict[str, dict[str, Any]] = {}

    def _record(self, component_id: str) -> tuple[str, Mapping[str, Any]]:
        component_id = _safe_id(component_id)
        if component_id in self.catalog.models:
            return "model", self.catalog.models[component_id]
        if component_id in self.catalog.runtimes:
            return "runtime", self.catalog.runtimes[component_id]
        raise ValueError("unknown_component_id")

    def _local(self, component_id: str, kind: str) -> Mapping[str, Any]:
        return self.catalog.inspect_model(component_id) if kind == "model" else self.catalog.inspect_runtime(component_id)

    def _source_binding(self, component_id: str, kind: str, record: Mapping[str, Any]) -> dict[str, Any] | None:
        """Build the current catalog binding; plans never supply this context."""

        catalog_version = getattr(self.catalog, "catalog_version", None)
        catalog_version = catalog_version if isinstance(catalog_version, str) else None
        catalog_schema = getattr(self.catalog, "catalog_schema_version", None)
        # V1 has no versioned catalog context.  Keep its explicit record-bound
        # legacy cache behavior; V2 always carries the complete catalog binding.
        if catalog_schema != "v7-production-catalog.v2":
            return None
        catalog_fingerprint = getattr(self.catalog, "fingerprint", None)
        revision = record.get("revision") if isinstance(record.get("revision"), str) else None
        return {
            "catalog_schema": catalog_schema,
            "catalog_version": catalog_version,
            "catalog_revision": catalog_version or revision,
            "catalog_fingerprint": catalog_fingerprint,
            "source_identity": record.get("source_identity") if isinstance(record.get("source_identity"), str) else None,
            "component_id": component_id,
            "component_type": kind,
            "record_revision": revision,
            "install_strategy": record.get("install_strategy") if isinstance(record.get("install_strategy"), str) else None,
        }

    def _receipt(self, component_id: str) -> Mapping[str, Any] | None:
        for filename in ("component_install_receipts.json", "model_install_receipts.json", "runtime_install_receipts.json"):
            try:
                value = json.loads((self.paths.config_root / filename).read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            records = value.get("records") if isinstance(value, Mapping) else None
            item = records.get(component_id) if isinstance(records, Mapping) else None
            if isinstance(item, Mapping):
                return item
        return None

    def _source_for(self, component_id: str, record: Mapping[str, Any], *, kind: str, force: bool) -> dict[str, Any]:
        return self.sources.check(component_id, record, force=force, binding=self._source_binding(component_id, kind, record))

    def _catalog_binding(self, component_id: str, kind: str, record: Mapping[str, Any]) -> tuple[CatalogBindingContext | None, str | None]:
        """Build the current server-owned typed binding for an update boundary."""

        try:
            schema = getattr(self.catalog, "catalog_schema_version", None)
            fingerprint = getattr(self.catalog, "fingerprint", None)
            if not isinstance(fingerprint, str):
                return None, "catalog_binding_unavailable"
            if schema == "v7-production-catalog.v2":
                version = getattr(self.catalog, "catalog_version", None)
                if not isinstance(version, str) or not version:
                    return None, "catalog_binding_unavailable"
                binding = CatalogBindingContext.for_v2(
                    catalog_version=version,
                    catalog_fingerprint=fingerprint,
                    source_identity=record.get("source_identity") if "source_identity" in record else None,
                )
            elif schema == "v7-production-catalog.v1":
                binding = CatalogBindingContext.for_v1(component_type=kind, record=record, catalog_fingerprint=fingerprint)
            elif schema in {"model-catalog.v1", "runtime-catalog.v1"}:
                expected_schema = "model-catalog.v1" if kind == "model" else "runtime-catalog.v1"
                if schema != expected_schema:
                    return None, "catalog_schema_unsupported"
                binding = CatalogBindingContext.for_v1(component_type=kind, record=record, catalog_fingerprint=fingerprint)
            elif schema is None:
                return None, "catalog_binding_unavailable"
            else:
                return None, "catalog_schema_unsupported"
            binding.validate_record(component_type=kind, record=record)
            return binding, None
        except (ReceiptError, TypeError, ValueError):
            return None, "catalog_binding_unavailable"

    def _current_execution_binding(self, component_id: object, component_type: object) -> tuple[CatalogBindingContext, Mapping[str, Any]] | None:
        """Provider callback used immediately before each mutating boundary."""

        if not isinstance(component_id, str) or component_type not in {"model", "runtime"}:
            return None
        try:
            kind, record = self._record(component_id)
            if kind != component_type:
                return None
            binding, _code = self._catalog_binding(component_id, kind, record)
            return (binding, record) if binding is not None else None
        except (TypeError, ValueError, KeyError):
            return None

    @staticmethod
    def _revisions(record: Mapping[str, Any], receipt: Mapping[str, Any] | None) -> tuple[str, str, str]:
        installed = str((receipt or {}).get("bundle_revision") or (receipt or {}).get("revision") or "unknown")
        upstream = str(record.get("latest_upstream_revision") or record.get("revision") or "unknown")
        supported = str(record.get("latest_supported_revision") or record.get("revision") or upstream)
        return installed, upstream, supported

    @staticmethod
    def _status(local_status: str, source: Mapping[str, Any], installed: str, upstream: str, supported: str) -> str:
        if local_status in {"NOT_INSTALLED", "UNAVAILABLE"}:
            return "NOT_INSTALLED"
        source_status = source.get("status")
        if source_status == "AUTH_REQUIRED":
            return "AUTH_REQUIRED"
        if source_status in {"UNAVAILABLE", "UNKNOWN", "RATE_LIMITED"}:
            return "SOURCE_UNAVAILABLE"
        if upstream != supported and upstream != installed:
            return "UPSTREAM_NEWER_UNSUPPORTED"
        if supported != installed:
            return "UPDATE_AVAILABLE"
        return "UP_TO_DATE"

    def check_component(self, component_id: str, *, force_source_check: bool = False) -> dict[str, Any]:
        kind, record = self._record(component_id)
        local = self._local(component_id, kind)
        receipt = self._receipt(component_id)
        source = self._source_for(component_id, record, kind=kind, force=force_source_check)
        installed, upstream, supported = self._revisions(record, receipt)
        status = self._status(str(local.get("status", "NOT_INSTALLED")), source, installed, upstream, supported)
        update_parts = record.get("update_parts") if isinstance(record.get("update_parts"), list) else []
        if not update_parts and status == "UPDATE_AVAILABLE":
            update_parts = ["model" if kind == "model" else "runtime"]
        return {
            "schema_version": "component-update-report.v1",
            "component_id": component_id,
            "component_type": kind,
            "local_status": str(local.get("status", "NOT_INSTALLED")),
            "status": status if status in UPDATE_STATUSES else "CHECK_FAILED",
            "installed_revision": installed,
            "latest_upstream_revision": upstream,
            "latest_supported_revision": supported,
            "changed_parts": sorted({str(item) for item in update_parts if item in {"backend", "runtime", "dependencies", "model"}}),
            "download_required": status == "UPDATE_AVAILABLE",
            "source": source_status_projection(source, allow_public=True),
            "rollback_available": bool(receipt and receipt.get("previous_version")),
            "execution": "completed" if force_source_check else "not_run",
            "dry_run": not force_source_check,
            "next_action": "Review the update plan before explicit apply." if status == "UPDATE_AVAILABLE" else "No component update apply is selected.",
        }

    def check_all(self, *, force_source_check: bool = False) -> dict[str, Any]:
        # Check only configured/installed catalog components.  A global check
        # must not fan out to every optional model in the catalog or random
        # Internet records that the owner never selected.
        ids: list[str] = []
        for component_id in sorted(set(self.catalog.models) | set(self.catalog.runtimes)):
            kind, _record = self._record(component_id)
            local = self._local(component_id, kind)
            if str(local.get("status")) in {"INSTALLED", "INSTALLED_UNVERIFIED", "OPERATIONAL", "PARTIAL", "UPDATE_AVAILABLE"}:
                ids.append(component_id)
        ids = ids[:32]
        reports = []
        for component_id in ids:
            reports.append(self.check_component(component_id, force_source_check=force_source_check))
        if force_source_check:
            self.schedule.mark_checked()
        return {"schema_version": "component-update-check-all.v1", "status": "completed", "execution": "completed" if force_source_check else "not_run", "dry_run": not force_source_check, "records": reports, "checked_count": len(reports), "auto_apply": False, "next_action": "Review individual update plans; scheduled checks never install automatically."}

    def plan_update(self, component_id: str) -> dict[str, Any]:
        kind, record = self._record(component_id)
        binding, binding_error = self._catalog_binding(component_id, kind, record)
        if binding is None:
            return {"status": "unavailable", "code": binding_error or "catalog_binding_unavailable", "execution": "not_run", "dry_run": True, "next_action": "Refresh the server-owned catalog context before planning an update."}
        report = self.check_component(component_id, force_source_check=False)
        if report["status"] != "UPDATE_AVAILABLE":
            return {"status": "unavailable", "code": report["status"].lower(), "report": report, "execution": "not_run", "dry_run": True}
        body = {key: report[key] for key in ("component_id", "component_type", "installed_revision", "latest_supported_revision", "changed_parts", "download_required")}
        plan_id = "update_plan_" + _fingerprint({**body, "time": int(time.time())})[:24]
        candidate = dict(record.get("update_candidate")) if isinstance(record.get("update_candidate"), Mapping) else None
        candidate_fingerprint = _fingerprint(candidate)
        record_revision = record.get("revision")
        install_strategy = record.get("install_strategy")
        execution_payload = {
            "body": body,
            "catalog_binding": binding.as_record_fields(),
            "record_revision": record_revision,
            "install_strategy": install_strategy,
            "latest_supported_revision": record.get("latest_supported_revision", record_revision),
            "candidate_fingerprint": candidate_fingerprint,
        }
        fingerprint = _fingerprint(execution_payload)
        plan = {
            "schema_version": "component-update-plan.v1",
            "plan_id": plan_id,
            "plan_fingerprint": fingerprint,
            **body,
            "component_type": kind,
            "catalog_fingerprint": binding.catalog_fingerprint,
            "update_candidate": candidate,
            "_catalog_binding": binding,
            "_record": record,
            "_record_revision": record_revision,
            "_install_strategy": install_strategy,
            "_latest_supported_revision": record.get("latest_supported_revision", record_revision),
            "_candidate_fingerprint": candidate_fingerprint,
            "_plan_fingerprint_payload": execution_payload,
        }
        public_plan = {key: value for key, value in plan.items() if not key.startswith("_") and key != "update_candidate"}
        public_value = {**public_plan, "status": "planned", "execution": "not_run", "dry_run": True, "rollback_available": report["rollback_available"], "next_action": "Confirm a fresh plan through the explicit component update executor."}
        self._private_plans[plan_id] = plan
        self._plans[plan_id] = public_value
        return dict(public_value)

    def apply_update(self, plan_id: str, *, confirmed: bool = False) -> dict[str, Any]:
        plan = self._private_plans.get(plan_id)
        if not confirmed:
            return {"status": "waiting_confirmation", "plan_id": plan_id, "execution": "not_run", "dry_run": True}
        if not isinstance(plan, Mapping):
            return {"status": "error", "code": "unknown_update_plan", "execution": "not_run"}
        from .update_executor import ComponentUpdateExecutor
        binding, binding_error = self._catalog_binding(str(plan.get("component_id")), str(plan.get("component_type")), plan.get("_record") if isinstance(plan.get("_record"), Mapping) else {})
        if binding is None:
            return {"status": "unavailable", "code": binding_error or "catalog_binding_unavailable", "execution": "not_run", "dry_run": True, "next_action": "Refresh the server-owned catalog context and create a new update plan."}
        result = ComponentUpdateExecutor(paths=self.paths).apply(
            plan,
            confirmed=True,
            catalog_binding=binding,
            current_record=plan.get("_record") if isinstance(plan.get("_record"), Mapping) else None,
            binding_provider=self._current_execution_binding,
        )
        if result.get("status") == "completed" and isinstance(plan.get("component_id"), str):
            self._applied_plans[plan["component_id"]] = dict(plan)
        return result

    def rollback(self, component_id: str) -> dict[str, Any]:
        from .update_executor import ComponentUpdateExecutor
        component_id = _safe_id(component_id)
        kind, record = self._record(component_id)
        binding, binding_error = self._catalog_binding(component_id, kind, record)
        if binding is None:
            return {"status": "unavailable", "code": binding_error or "catalog_binding_unavailable", "execution": "not_run", "dry_run": True, "next_action": "Refresh the server-owned catalog context before rollback."}
        result = ComponentUpdateExecutor(paths=self.paths).rollback(
            component_id,
            catalog_binding=binding,
            current_record=record,
            binding_provider=self._current_execution_binding,
            plan=self._applied_plans.get(component_id),
        )
        if result.get("status") == "completed":
            self._applied_plans.pop(component_id, None)
        return result


__all__ = ["UPDATE_STATUSES", "SCHEDULE_POLICIES", "UpdateResolver", "UpdateSchedule"]
