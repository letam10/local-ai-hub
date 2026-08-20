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
        self._plans: dict[str, dict[str, Any]] = {}

    def _record(self, component_id: str) -> tuple[str, Mapping[str, Any]]:
        component_id = _safe_id(component_id)
        if component_id in self.catalog.models:
            return "model", self.catalog.models[component_id]
        if component_id in self.catalog.runtimes:
            return "runtime", self.catalog.runtimes[component_id]
        raise ValueError("unknown_component_id")

    def _local(self, component_id: str, kind: str) -> Mapping[str, Any]:
        return self.catalog.inspect_model(component_id) if kind == "model" else self.catalog.inspect_runtime(component_id)

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

    def _source_for(self, component_id: str, record: Mapping[str, Any], *, force: bool) -> dict[str, Any]:
        return self.sources.check(component_id, record, force=force)

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
        source = self._source_for(component_id, record, force=force_source_check)
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
            "source": source_status_projection(source),
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
        report = self.check_component(component_id, force_source_check=False)
        if report["status"] != "UPDATE_AVAILABLE":
            return {"status": "unavailable", "code": report["status"].lower(), "report": report, "execution": "not_run", "dry_run": True}
        body = {key: report[key] for key in ("component_id", "component_type", "installed_revision", "latest_supported_revision", "changed_parts", "download_required")}
        plan_id = "update_plan_" + _fingerprint({**body, "time": int(time.time())})[:24]
        fingerprint = _fingerprint(body)
        plan = {"schema_version": "component-update-plan.v1", "plan_id": plan_id, "plan_fingerprint": fingerprint, **body, "component_type": kind, "catalog_fingerprint": getattr(self.catalog, "fingerprint", None), "update_candidate": dict(record.get("update_candidate")) if isinstance(record.get("update_candidate"), Mapping) else None}
        self._plans[plan_id] = plan
        return {**plan, "status": "planned", "execution": "not_run", "dry_run": True, "rollback_available": report["rollback_available"], "next_action": "Confirm a fresh plan through the explicit component update executor."}

    def apply_update(self, plan_id: str, *, confirmed: bool = False) -> dict[str, Any]:
        plan = self._plans.get(plan_id)
        if not confirmed:
            return {"status": "waiting_confirmation", "plan_id": plan_id, "execution": "not_run", "dry_run": True}
        if not isinstance(plan, Mapping):
            return {"status": "error", "code": "unknown_update_plan", "execution": "not_run"}
        from .update_executor import ComponentUpdateExecutor
        return ComponentUpdateExecutor(paths=self.paths).apply(plan, confirmed=True)

    def rollback(self, component_id: str) -> dict[str, Any]:
        from .update_executor import ComponentUpdateExecutor
        return ComponentUpdateExecutor(paths=self.paths).rollback(_safe_id(component_id))


__all__ = ["UPDATE_STATUSES", "SCHEDULE_POLICIES", "UpdateResolver", "UpdateSchedule"]
