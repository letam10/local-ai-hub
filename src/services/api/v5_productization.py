"""Read-only V5-D product projections across the existing server-owned stores.

This adapter composes already validated capability, job, workflow-library, and
health snapshots.  It never accepts a client manifest, resolves a local path,
starts a worker, or changes durable state.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import math
from typing import Any

from src.services.api.config import CONFIG_ROOT
from src.services.api.jobs import DurableJobStore, DurableStoreHealthError
from src.services.job_manager import DurableWorkEngine, ServerOwnedAdapterRegistry


PRODUCT_SURFACE_SCHEMA_VERSION = "v5-product-surface.v1"
_STATUS_ALLOWLIST = frozenset({
    "healthy", "operational", "installed", "available", "partial", "planned",
    "not_published", "unavailable", "error", "interrupted", "failed", "cancelled",
    "queued", "starting", "running", "cancelling", "completed", "ready", "clean",
    "recovery_required", "not_run",
})
_ACTIVE_JOB_STATES = frozenset({"queued", "starting", "running", "cancelling"})
_ATTENTION_JOB_STATES = frozenset({"failed", "unavailable", "cancelled", "interrupted"})
_MAX_MODULES = 256
_MAX_JOBS = 500
_MAX_VOLUMES = 2
_MAX_TEXT = 240
_LOW_SPACE_BYTES = 20 * 1024**3
_VOLUME_LABELS = {"c": "C:", "d": "D:"}
DURABLE_JOBS_PATH = CONFIG_ROOT / "durable_jobs.v5.json"


def _status(value: object, fallback: str = "partial") -> str:
    candidate = str(value or fallback)
    return candidate if candidate in _STATUS_ALLOWLIST else fallback


def _text(value: object, fallback: str = "") -> str:
    if not isinstance(value, str):
        return fallback
    return value.strip()[:_MAX_TEXT]


def _safe_id(value: object, fallback: str = "unknown") -> str:
    candidate = _text(value, fallback)
    return candidate if candidate and "\\" not in candidate and "/" not in candidate and ".." not in candidate else fallback


def _volume_numbers(value: Mapping[str, Any]) -> tuple[int, int, int] | None:
    values = (value.get("total_bytes"), value.get("free_bytes"), value.get("used_bytes"))
    if any(type(item) is not int or item < 0 for item in values):
        return None
    total, free, used = values
    if free > total or used > total:
        return None
    return total, free, used


def _unavailable_volume_projection(volume_id: str) -> dict[str, Any]:
    return {
        "id": volume_id,
        "label": _VOLUME_LABELS[volume_id],
        "total_bytes": None,
        "free_bytes": None,
        "used_bytes": None,
        "total_gb": None,
        "free_gb": None,
        "used_gb": None,
        "low_space": None,
        "status": "unavailable",
        "availability": "unknown",
        "reason": "Volume statistics are unavailable; no figures are shown.",
        "next_action": "Verify that the volume is mounted and readable, then refresh storage.",
    }


def project_storage_projection(value: object) -> dict[str, Any]:
    """Project only the server-owned C:/D: storage summary for the Dashboard."""

    source = value if isinstance(value, Mapping) else {}
    raw_values = source.get("volumes")
    if not isinstance(raw_values, list):
        nested = source.get("volume_projection")
        raw_values = nested.get("volumes") if isinstance(nested, Mapping) else []
    by_id: dict[str, Mapping[str, Any]] = {}
    for item in raw_values[:_MAX_VOLUMES] if isinstance(raw_values, list) else []:
        if not isinstance(item, Mapping):
            continue
        volume_id = str(item.get("id") or "").casefold()
        if volume_id in _VOLUME_LABELS and volume_id not in by_id:
            by_id[volume_id] = item

    projected: list[dict[str, Any]] = []
    for volume_id in ("c", "d"):
        item = by_id.get(volume_id)
        numbers = _volume_numbers(item) if item is not None else None
        status = str(item.get("status") or "") if item is not None else ""
        if numbers is None or status != "available":
            projected.append(_unavailable_volume_projection(volume_id))
            continue
        total, free, used = numbers
        low_space = free < _LOW_SPACE_BYTES
        projected.append({
            "id": volume_id,
            "label": _VOLUME_LABELS[volume_id],
            "total_bytes": total,
            "free_bytes": free,
            "used_bytes": used,
            "total_gb": round(total / (1024**3), 3),
            "free_gb": round(free / (1024**3), 3),
            "used_gb": round(used / (1024**3), 3),
            "low_space": low_space,
            "status": "available",
            "availability": "available",
            "reason": "Free space is below the 20 GiB low-space threshold." if low_space else "Volume statistics are available from the server-owned allowlist.",
            "next_action": "Review output, cache, and temporary data before new writes." if low_space else "No action is required; refresh after external storage changes.",
        })
    available = sum(1 for item in projected if item["status"] == "available")
    status = "ready" if available == len(projected) else "partial" if available else "unavailable"
    return {
        "status": status,
        "execution": "not_run",
        "dry_run": True,
        "allowlist": ["c", "d"],
        "volumes": projected,
        "low_space_volumes": [item["id"] for item in projected if item["low_space"] is True],
    }


def project_capability_modules(control_plane: object) -> list[dict[str, Any]]:
    """Return only opaque, server-owned module readiness fields."""

    source = control_plane if isinstance(control_plane, Mapping) else {}
    registry = source.get("registry") if isinstance(source.get("registry"), Mapping) else {}
    records = registry.get("records") if isinstance(registry.get("records"), list) else []
    projected: list[dict[str, Any]] = []
    for record in records[:_MAX_MODULES]:
        if not isinstance(record, Mapping):
            continue
        identifier = _safe_id(record.get("id"), "module")
        projected.append({
            "id": identifier,
            "provider": _safe_id(record.get("provider"), "server_owned"),
            "component": _safe_id(record.get("component"), identifier),
            "status": _status(record.get("status")),
            "version": _text(record.get("version")) or None,
            "reason": _text(record.get("reason"), "Static capability evidence is bounded."),
            "next_action": _text(record.get("next_action"), "Review the server-owned evidence before runtime work."),
        })
    projected.sort(key=lambda item: (item["status"], item["provider"], item["component"], item["id"]))
    return projected


def project_job_recovery(jobs: object) -> dict[str, Any]:
    """Project recovery truth without inputs, descriptors, callables, or paths."""

    values = jobs if isinstance(jobs, list) else []
    records: list[dict[str, Any]] = []
    counts = {"active": 0, "attention": 0, "interrupted": 0, "recoverable": 0, "total": 0}
    for item in values[:_MAX_JOBS]:
        if not isinstance(item, Mapping):
            continue
        status = _status(item.get("status"), "unavailable")
        resumable = bool(item.get("resumable") is True or item.get("retry_available") is True)
        raw_progress = item.get("progress", 0)
        progress = max(0, min(100, int(raw_progress))) if type(raw_progress) in {int, float} and math.isfinite(raw_progress) else 0
        record = {
            "id": _safe_id(item.get("id"), "job"),
            "tool": _safe_id(item.get("tool"), "job"),
            "source": _safe_id(item.get("source"), "legacy"),
            "status": status,
            "progress": progress,
            "resumable": resumable and status in _ATTENTION_JOB_STATES,
            "next_action": _text(item.get("next_action") or item.get("action_code"), "Review the job state and create a new task when recovery is unavailable."),
        }
        records.append(record)
        counts["total"] += 1
        if status in _ACTIVE_JOB_STATES:
            counts["active"] += 1
        if status in _ATTENTION_JOB_STATES:
            counts["attention"] += 1
        if status == "interrupted":
            counts["interrupted"] += 1
        if record["resumable"]:
            counts["recoverable"] += 1
    records.sort(key=lambda item: (item["status"], item["id"]))
    return {
        "status": "partial" if counts["attention"] else "ready",
        "execution": "not_run",
        "dry_run": True,
        "counts": counts,
        "records": records,
    }


def durable_jobs_snapshot(path: Path = DURABLE_JOBS_PATH) -> dict[str, Any]:
    """Read the V5-A durable store through its public projection only."""

    try:
        store = DurableJobStore(path)
        try:
            records = []
            for item in store.records():
                detached = dict(item)
                detached["retry_available"] = False
                records.append(DurableWorkEngine.public_job(detached))
            health = store.health()
        finally:
            store.close()
    except DurableStoreHealthError as exc:
        return {
            "status": "unavailable",
            "execution": "not_run",
            "dry_run": True,
            "recovery": {"status": "unavailable", "code": exc.code, "next_action": exc.action},
            "records": [],
        }
    projected = project_job_recovery(records)
    projected["records"] = [{**item, "source": "durable"} for item in projected["records"]]
    projected["store_health"] = health
    return projected


def reconcile_durable_jobs(path: Path = DURABLE_JOBS_PATH) -> dict[str, Any]:
    """Reconcile pre-crash V5-A records without registering runtime adapters."""

    store = DurableJobStore(path)
    engine = DurableWorkEngine(store, ServerOwnedAdapterRegistry())
    try:
        report = engine.reconcile_startup()
        return {"status": "ready", "execution": "not_run", "dry_run": True, **report}
    finally:
        engine.close()


def resume_durable_job(job_id: str, path: Path = DURABLE_JOBS_PATH) -> dict[str, Any]:
    """Attempt V5-A resume only through the server-owned adapter registry."""

    if not isinstance(job_id, str) or not job_id.startswith("jobv5_") or len(job_id) != 38:
        return {"status": "invalid", "execution": "not_run", "dry_run": True, "next_action": "Use the opaque durable job ID returned by Hub."}
    store = DurableJobStore(path)
    engine = DurableWorkEngine(store, ServerOwnedAdapterRegistry())
    try:
        record = engine.get(job_id)
        if record is None:
            return {"status": "not_found", "execution": "not_run", "dry_run": True, "next_action": "Create a new allowlisted job descriptor."}
        if record.get("retry_available") is not True:
            return {
                "status": "unavailable",
                "execution": "not_run",
                "dry_run": True,
                "job": record,
                "next_action": "Create a new allowlisted descriptor; no server adapter is currently reconstructable.",
            }
        try:
            resumed = engine.resume(job_id)
        except Exception:
            return {
                "status": "unavailable",
                "execution": "not_run",
                "dry_run": True,
                "job": record,
                "next_action": "Create a new allowlisted descriptor after server adapter review.",
            }
        return {"status": "queued", "execution": "not_run", "dry_run": True, "job": resumed}
    finally:
        engine.close()


def project_workflow_library(value: object) -> dict[str, Any]:
    """Expose validated Workflow Library metadata without raw graph details."""

    source = value if isinstance(value, Mapping) else {}
    recovery = source.get("recovery") if isinstance(source.get("recovery"), Mapping) else {}
    workflows = source.get("workflows") if isinstance(source.get("workflows"), list) else []
    summaries: list[dict[str, Any]] = []
    for workflow in workflows[:_MAX_MODULES]:
        if not isinstance(workflow, Mapping):
            continue
        summaries.append({
            "id": _safe_id(workflow.get("id"), "workflow"),
            "title": _text(workflow.get("title"), "Untitled workflow"),
            "scope": _safe_id(workflow.get("scope"), "general"),
            "revision": workflow.get("revision") if type(workflow.get("revision")) is int else 0,
            "status": _status(workflow.get("status"), "partial"),
            "source": _safe_id(workflow.get("source"), "local"),
        })
    summaries.sort(key=lambda item: item["id"])
    recovery_status = _status(recovery.get("status"), "partial")
    status = _status(source.get("status"), "partial")
    if recovery_status == "recovery_required":
        status = "recovery_required"
    return {
        "status": status,
        "schema_version": _text(source.get("schema_version"), "workflow-library.v1"),
        "library_revision": source.get("library_revision") if type(source.get("library_revision")) is int else 0,
        "workflow_count": len(summaries),
        "workflows": summaries,
        "recovery": {
            "status": recovery_status,
            "reason": _text(recovery.get("reason"), "Workflow Library recovery state is not available."),
            "next_action": _text(recovery.get("action"), "Review the validated local library before writing."),
        },
    }


def project_product_surface(
    *,
    control_plane: object,
    health: object,
    jobs: object,
    workflow_library: object,
    storage: object = None,
) -> dict[str, Any]:
    """Compose the deterministic Dashboard/API product surface."""

    control = control_plane if isinstance(control_plane, Mapping) else {}
    health_value = health if isinstance(health, Mapping) else {}
    registry = control.get("registry") if isinstance(control.get("registry"), Mapping) else {}
    plan = control.get("module_manager") if isinstance(control.get("module_manager"), Mapping) else {}
    readiness = _status(control.get("status") or health_value.get("status"), "partial")
    recovery = project_job_recovery(jobs)
    library = project_workflow_library(workflow_library)
    storage_projection = project_storage_projection(storage)
    disk = health_value.get("disk") if isinstance(health_value.get("disk"), Mapping) else {}
    gpu = health_value.get("gpu") if isinstance(health_value.get("gpu"), Mapping) else {}
    warnings: list[dict[str, str]] = []
    gpu_status = "available" if gpu.get("available") is True else _status(gpu.get("status"), "unavailable")
    if gpu_status not in {"healthy", "operational", "available"}:
        warnings.append({"id": "gpu", "status": gpu_status, "reason": "GPU snapshot is unavailable or has no runtime proof."})
    if not isinstance(disk.get("free_bytes"), int) or disk.get("free_bytes", 0) < 0:
        warnings.append({"id": "disk", "status": "unavailable", "reason": "Storage snapshot is unavailable; no write operation was attempted."})
    for volume in storage_projection["volumes"]:
        if volume["low_space"] is True:
            warnings.append({
                "id": f"volume-{volume['id']}",
                "status": "partial",
                "reason": f"{volume['label']} is below the low-space threshold; review storage before new writes.",
            })
        elif volume["status"] != "available":
            warnings.append({
                "id": f"volume-{volume['id']}",
                "status": "unavailable",
                "reason": f"{volume['label']} statistics are unavailable; no figures were fabricated.",
            })
    return {
        "schema_version": PRODUCT_SURFACE_SCHEMA_VERSION,
        "status": readiness,
        "execution": "not_run",
        "dry_run": True,
        "readiness": {
            "status": readiness,
            "reason": _text(control.get("reason"), "Readiness is derived from server-owned static capability evidence."),
            "next_action": _text(control.get("next_action"), "Review the module plan before requesting runtime work."),
        },
        "capabilities": {
            "status": _status(registry.get("status"), "partial"),
            "registry_status": _status(registry.get("status"), "partial"),
            "module_plan_status": _status(plan.get("status"), "partial"),
            "reason": _text(plan.get("reason"), "Module preflight is server-owned static metadata."),
            "next_action": _text(plan.get("next_action"), "Review the plan before any separately authorized operation."),
            "modules": project_capability_modules(control),
        },
        "jobs": recovery,
        "workflow_library": library,
        "storage": storage_projection,
        "warnings": warnings,
    }


__all__ = [
    "DURABLE_JOBS_PATH",
    "PRODUCT_SURFACE_SCHEMA_VERSION",
    "durable_jobs_snapshot",
    "reconcile_durable_jobs",
    "project_capability_modules",
    "project_job_recovery",
    "project_product_surface",
    "project_storage_projection",
    "project_workflow_library",
    "resume_durable_job",
]
