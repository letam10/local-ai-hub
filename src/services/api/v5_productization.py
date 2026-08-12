"""Read-only V5-D product projections across the existing server-owned stores.

This adapter composes already validated capability, job, workflow-library, and
health snapshots.  It never accepts a client manifest, resolves a local path,
starts a worker, or changes durable state.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
import math
import re
from typing import Any

from src.services.api.config import CONFIG_ROOT
from src.services.api.jobs import DurableJobStore, DurableStoreHealthError
from src.services.job_manager import DurableWorkEngine, ServerOwnedAdapterRegistry
from src.services.job_manager.contracts import JOB_STATES, TERMINAL_JOB_STATES, is_artifact_id
from src.services.job_manager.durable import (
    RECOVERY_NEXT_CREATE,
    RECOVERY_NEXT_RETRY,
    RECOVERY_REASON_ACTIVE,
    RECOVERY_REASON_ADAPTER_UNAVAILABLE,
    RECOVERY_REASON_AVAILABLE,
    RECOVERY_REASON_INVALID,
    RECOVERY_REASON_NOT_RECONSTRUCTABLE,
    public_recovery_decision,
)


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
_RUNTIME_EVIDENCE_CLASSES = frozenset({
    "filter_graph", "image_decode", "stream_mapping", "encoder_or_mux", "filesystem", "timeout", "unknown",
})
_RECOVERY_KEYS = frozenset({"status", "action", "action_available", "reason", "next_action"})
_RECOVERY_REASONS = frozenset({
    RECOVERY_REASON_INVALID,
    RECOVERY_REASON_ACTIVE,
    RECOVERY_REASON_NOT_RECONSTRUCTABLE,
    RECOVERY_REASON_ADAPTER_UNAVAILABLE,
    RECOVERY_REASON_AVAILABLE,
})
_LIFECYCLE_STATES = frozenset(JOB_STATES)
_LIFECYCLE_TERMINAL = frozenset(TERMINAL_JOB_STATES)
_LIFECYCLE_JOB_ID = re.compile(r"jobv5_[a-f0-9]{32}")
_LIFECYCLE_FINGERPRINT = re.compile(r"[a-f0-9]{64}")


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


def _runtime_evidence_fallback() -> dict[str, Any]:
    return {
        "schema_version": "runtime-evidence-projection.v1",
        "subject": "media_overlay_cpu_acceptance",
        "status": "unavailable",
        "outcome": "not_run",
        "execution": "not_run",
        "failure_class": None,
        "invocation_count": 0,
        "cleanup": {"processes_remaining": 0, "temp_cleaned": True},
        "artifact_published": False,
        "source_overwrite_checked": False,
        "source_overwritten": None,
        "reason": "No bounded media acceptance evidence is available.",
        "next_action": "Keep media operations partial until a separately authorized bounded acceptance completes.",
    }


def project_runtime_evidence(value: object) -> dict[str, Any]:
    """Copy only safe runtime-evidence semantics into the product surface."""

    if not isinstance(value, Mapping) or value.get("subject") != "media_overlay_cpu_acceptance":
        return _runtime_evidence_fallback()
    cleanup = value.get("cleanup")
    cleanup_ok = isinstance(cleanup, Mapping) and cleanup.get("processes_remaining") == 0 and cleanup.get("temp_cleaned") is True
    safe_cleanup = {"processes_remaining": 0 if cleanup_ok else 1, "temp_cleaned": cleanup_ok}
    source_overwrite_checked = value.get("source_overwrite_checked")
    source_overwritten = value.get("source_overwritten")
    if source_overwrite_checked is True and type(source_overwritten) is bool:
        safe_overwrite = (True, source_overwritten)
    elif source_overwrite_checked is False and source_overwritten is None:
        safe_overwrite = (False, None)
    else:
        safe_overwrite = None
    failure_class = value.get("failure_class")
    if (
        value.get("status") in {"partial", "unavailable"}
        and value.get("outcome") == "error"
        and value.get("execution") == "attempted"
        and value.get("invocation_count") == 1
        and failure_class in _RUNTIME_EVIDENCE_CLASSES
        and value.get("artifact_published") is False
        and safe_overwrite is not None
    ):
        return {
            "schema_version": "runtime-evidence-projection.v1",
            "subject": "media_overlay_cpu_acceptance",
            "status": "unavailable",
            "outcome": "error",
            "execution": "attempted",
            "failure_class": failure_class,
            "invocation_count": 1,
            "cleanup": safe_cleanup,
            "artifact_published": False,
            "source_overwrite_checked": safe_overwrite[0],
            "source_overwritten": safe_overwrite[1],
            "reason": "The last bounded media acceptance stopped before a publishable output.",
            "next_action": "Keep media operations partial; request a new exact-source approval before any future attempt.",
        }
    if (
        value.get("status") in {"partial", "unavailable"}
        and value.get("outcome") in {"blocked", "not_run"}
        and value.get("execution") == "not_run"
        and value.get("invocation_count") == 0
        and value.get("failure_class") is None
        and value.get("artifact_published") is False
        and safe_overwrite is not None
    ):
        if value.get("outcome") == "blocked":
            reason = "The bounded media acceptance was blocked before execution."
            next_action = "Keep media operations partial; obtain a fresh exact-source approval before any attempt."
        else:
            reason = "No bounded media acceptance invocation was recorded."
            next_action = "Keep media operations partial until a separately authorized bounded acceptance is recorded."
        return {
            "schema_version": "runtime-evidence-projection.v1",
            "subject": "media_overlay_cpu_acceptance",
            "status": "unavailable",
            "outcome": value.get("outcome"),
            "execution": "not_run",
            "failure_class": None,
            "invocation_count": 0,
            "cleanup": safe_cleanup,
            "artifact_published": False,
            "source_overwrite_checked": safe_overwrite[0],
            "source_overwritten": safe_overwrite[1],
            "reason": reason,
            "next_action": next_action,
        }
    if (
        value.get("status") == "operational"
        and value.get("outcome") == "completed"
        and value.get("execution") == "completed"
        and value.get("invocation_count") == 1
        and value.get("failure_class") is None
        and cleanup_ok
        and value.get("artifact_published") is True
        and safe_overwrite == (True, False)
    ):
        return {
            "schema_version": "runtime-evidence-projection.v1",
            "subject": "media_overlay_cpu_acceptance",
            "status": "operational",
            "outcome": "completed",
            "execution": "completed",
            "failure_class": None,
            "invocation_count": 1,
            "cleanup": safe_cleanup,
            "artifact_published": True,
            "source_overwrite_checked": True,
            "source_overwritten": False,
            "reason": "A bounded media acceptance completed for the approved source.",
            "next_action": "Use the existing allowlisted media operations with opaque artifacts.",
        }
    return _runtime_evidence_fallback()


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


def _recovery_fallback() -> dict[str, Any]:
    return {
        "status": "unavailable",
        "action": "CREATE_NEW_JOB",
        "action_available": False,
        "reason": RECOVERY_REASON_INVALID,
        "next_action": RECOVERY_NEXT_CREATE,
    }


def _project_recovery(value: object) -> dict[str, Any]:
    """Copy only the fixed server-owned recovery decision."""

    if not isinstance(value, Mapping) or set(value) != _RECOVERY_KEYS:
        return _recovery_fallback()
    status = value.get("status")
    action = value.get("action")
    available = value.get("action_available")
    reason = value.get("reason")
    next_action = value.get("next_action")
    if type(available) is not bool:
        return _recovery_fallback()
    if status == "available":
        if (
            action != "RETRY_IF_RECONSTRUCTABLE"
            or available is not True
            or reason != RECOVERY_REASON_AVAILABLE
            or next_action != RECOVERY_NEXT_RETRY
        ):
            return _recovery_fallback()
    elif status == "unavailable":
        if (
            action != "CREATE_NEW_JOB"
            or available is not False
            or reason not in _RECOVERY_REASONS - {RECOVERY_REASON_AVAILABLE}
            or next_action != RECOVERY_NEXT_CREATE
        ):
            return _recovery_fallback()
    else:
        return _recovery_fallback()
    return {
        "status": status,
        "action": action,
        "action_available": available,
        "reason": reason,
        "next_action": next_action,
    }


def _safe_lifecycle_timestamp(value: object) -> str | None:
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.isoformat()


def _project_lifecycle(value: object, fallback_state: str) -> dict[str, Any]:
    """Keep a finite lifecycle projection and stop history after a terminal."""

    source = value if isinstance(value, Mapping) else {}
    state = source.get("state") if source.get("state") in _LIFECYCLE_STATES else fallback_state
    raw_history = source.get("history")
    history = [item for item in raw_history if isinstance(item, str) and item in _LIFECYCLE_STATES] if isinstance(raw_history, list) else []
    history = history[:32]
    terminal_index = next((index for index, item in enumerate(history) if item in _LIFECYCLE_TERMINAL), None)
    if terminal_index is not None:
        history = history[: terminal_index + 1]
        state = history[-1]
    elif state in _LIFECYCLE_TERMINAL:
        history.append(state)
    elif not history or history[-1] != state:
        history.append(state)
    timestamps = source.get("timestamps") if isinstance(source.get("timestamps"), Mapping) else {}
    attempt = source.get("attempt")
    safe_attempt = attempt if isinstance(attempt, int) and not isinstance(attempt, bool) and 1 <= attempt <= 10_000 else 1
    retry_of = source.get("retry_of")
    safe_retry_of = retry_of if isinstance(retry_of, str) and _LIFECYCLE_JOB_ID.fullmatch(retry_of) else None
    return {
        "state": state,
        "history": history[-32:],
        "attempt": safe_attempt,
        "retry_of": safe_retry_of,
        "timestamps": {
            key: _safe_lifecycle_timestamp(timestamps.get(key))
            for key in ("created_at", "updated_at", "started_at", "finished_at")
        },
    }


def _artifact_unavailable_projection(artifact_id: object = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "unavailable",
        "preview_available": False,
        "reason": "Artifact metadata is unavailable or its durable provenance does not match.",
        "next_action": "Refresh the durable job after server-owned artifact validation.",
    }
    if isinstance(artifact_id, str) and is_artifact_id(artifact_id):
        result["id"] = artifact_id
    return result


def _project_artifacts(value: object) -> list[dict[str, Any]]:
    values = value if isinstance(value, list) else []
    projected: list[dict[str, Any]] = []
    for item in values[:64]:
        if not isinstance(item, Mapping):
            projected.append(_artifact_unavailable_projection())
            continue
        artifact_id = item.get("id")
        if item.get("status") != "available" or item.get("preview_available") is not True or not is_artifact_id(artifact_id):
            projected.append(_artifact_unavailable_projection(artifact_id))
            continue
        name = item.get("name")
        media_type = item.get("media_type")
        size = item.get("size_bytes")
        digest = item.get("sha256")
        if (
            not isinstance(name, str)
            or not 1 <= len(name) <= 160
            or any(char in name for char in ("\x00", "\r", "\n", "/", "\\"))
            or ".." in name
            or not isinstance(media_type, str)
            or not re.fullmatch(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", media_type)
            or isinstance(size, bool)
            or not isinstance(size, int)
            or not 0 <= size <= 8 * 1024**3
            or not isinstance(digest, str)
            or not _LIFECYCLE_FINGERPRINT.fullmatch(digest)
            or item.get("url") != f"/api/artifacts/{artifact_id}"
        ):
            projected.append(_artifact_unavailable_projection(artifact_id))
            continue
        provenance = item.get("provenance")
        if not isinstance(provenance, Mapping) or set(provenance) != {"job_id", "job_spec_fingerprint", "adapter_id", "attempt", "status"}:
            projected.append(_artifact_unavailable_projection(artifact_id))
            continue
        if (
            not isinstance(provenance.get("job_id"), str)
            or not _LIFECYCLE_JOB_ID.fullmatch(provenance["job_id"])
            or not isinstance(provenance.get("job_spec_fingerprint"), str)
            or not _LIFECYCLE_FINGERPRINT.fullmatch(provenance["job_spec_fingerprint"])
            or not isinstance(provenance.get("adapter_id"), str)
            or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", provenance["adapter_id"])
            or isinstance(provenance.get("attempt"), bool)
            or not isinstance(provenance.get("attempt"), int)
            or not 1 <= provenance["attempt"] <= 10_000
            or provenance.get("status") not in _LIFECYCLE_STATES
        ):
            projected.append(_artifact_unavailable_projection(artifact_id))
            continue
        projected.append({
            "id": artifact_id,
            "name": name,
            "media_type": media_type,
            "size_bytes": size,
            "sha256": digest,
            "url": f"/api/artifacts/{artifact_id}",
            "status": "available",
            "preview_available": True,
            "provenance": {
                "job_id": provenance["job_id"],
                "job_spec_fingerprint": provenance["job_spec_fingerprint"],
                "adapter_id": provenance["adapter_id"],
                "attempt": provenance["attempt"],
                "status": provenance["status"],
            },
        })
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
        durable_record = item.get("contract_version") == "durable-job.v1" and isinstance(item.get("job_spec"), Mapping)
        if durable_record:
            item = DurableWorkEngine.public_job(dict(item))
            status = _status(item.get("status"), "unavailable")
        durable = durable_record or item.get("source") == "durable" or item.get("contract_version") == "durable-job.v1"
        recovery = _project_recovery(item.get("recovery")) if durable else None
        resumable = recovery["action_available"] if recovery is not None else bool(item.get("resumable") is True or item.get("retry_available") is True)
        raw_progress = item.get("progress", 0)
        progress = max(0, min(100, int(raw_progress))) if type(raw_progress) in {int, float} and math.isfinite(raw_progress) else 0
        job_id = item.get("id")
        safe_job_id = job_id if isinstance(job_id, str) and _LIFECYCLE_JOB_ID.fullmatch(job_id) else _safe_id(job_id, "job")
        record: dict[str, Any] = {
            "id": safe_job_id,
            "tool": _safe_id(item.get("tool"), "job"),
            "source": "durable" if durable else _safe_id(item.get("source"), "legacy"),
            "status": status,
            "progress": progress,
            "resumable": bool(resumable) and status in _ATTENTION_JOB_STATES,
            "next_action": recovery["next_action"] if recovery is not None else _text(
                item.get("next_action") or item.get("action_code"),
                "Review the job state and create a new task when recovery is unavailable.",
            ),
        }
        if recovery is not None:
            record["recovery"] = recovery
            record["lifecycle"] = _project_lifecycle(item.get("lifecycle"), status)
            record["artifacts"] = _project_artifacts(item.get("artifacts"))
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


def durable_jobs_snapshot(
    path: Path = DURABLE_JOBS_PATH,
    *,
    registry: ServerOwnedAdapterRegistry | None = None,
) -> dict[str, Any]:
    """Read the V5-A durable store through its public projection only."""

    active_registry = registry if isinstance(registry, ServerOwnedAdapterRegistry) else ServerOwnedAdapterRegistry()
    try:
        store = DurableJobStore(path)
        try:
            records = []
            for item in store.records():
                records.append(DurableWorkEngine.public_job(item, registry=active_registry))
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


def resume_durable_job(
    job_id: str,
    path: Path = DURABLE_JOBS_PATH,
    *,
    registry: ServerOwnedAdapterRegistry | None = None,
) -> dict[str, Any]:
    """Attempt V5-A resume only through the server-owned adapter registry."""

    if not isinstance(job_id, str) or not _LIFECYCLE_JOB_ID.fullmatch(job_id):
        return {"status": "invalid", "execution": "not_run", "dry_run": True, "next_action": "Use the opaque durable job ID returned by Hub."}
    active_registry = registry if isinstance(registry, ServerOwnedAdapterRegistry) else ServerOwnedAdapterRegistry()
    try:
        store = DurableJobStore(path)
    except DurableStoreHealthError as exc:
        return {
            "status": "unavailable",
            "execution": "not_run",
            "dry_run": True,
            "recovery": {"status": "unavailable", "action": "CREATE_NEW_JOB", "action_available": False, "reason": "Durable job state is unavailable.", "next_action": RECOVERY_NEXT_CREATE},
            "next_action": RECOVERY_NEXT_CREATE,
            "reason": exc.code,
        }
    engine = DurableWorkEngine(store, active_registry)
    try:
        try:
            raw_record = store.get(job_id)
        except DurableStoreHealthError as exc:
            return {
                "status": "unavailable",
                "execution": "not_run",
                "dry_run": True,
                "recovery": _recovery_fallback(),
                "next_action": RECOVERY_NEXT_CREATE,
                "reason": exc.code,
            }
        if raw_record is None:
            return {"status": "not_found", "execution": "not_run", "dry_run": True, "next_action": "Create a new allowlisted job descriptor."}
        decision = public_recovery_decision(raw_record, active_registry)
        record = DurableWorkEngine.public_job(raw_record, registry=active_registry)
        if decision["action_available"] is not True:
            return {
                "status": "unavailable",
                "execution": "not_run",
                "dry_run": True,
                "job": record,
                "recovery": decision,
                "next_action": decision["next_action"],
            }
        try:
            resumed = engine.resume(job_id, start=False)
        except Exception:
            return {
                "status": "unavailable",
                "execution": "not_run",
                "dry_run": True,
                "job": record,
                "recovery": {**decision, "status": "unavailable", "action": "CREATE_NEW_JOB", "action_available": False, "reason": RECOVERY_REASON_INVALID, "next_action": RECOVERY_NEXT_CREATE},
                "next_action": RECOVERY_NEXT_CREATE,
            }
        return {"status": "queued", "execution": "not_run", "dry_run": True, "job": resumed, "recovery": decision}
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
            "runtime_evidence": project_runtime_evidence(control.get("runtime_evidence")),
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
    "project_runtime_evidence",
    "project_storage_projection",
    "project_workflow_library",
    "resume_durable_job",
]
