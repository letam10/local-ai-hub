"""Durable Job Engine V2 with truthful dispatch/retry/recovery semantics.

The engine persists only bounded metadata and has no default execution owner.
It can coordinate an actual future server-owned owner, but does not create a
thread, launch a worker, or claim runtime execution by itself.  Thus a
reconstruct-only retry is always public as a newly created, not-yet-executed
record rather than a running job.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from datetime import datetime, timezone
import re
import secrets
from typing import Any

from src.services.resource_scheduler import ResourceScheduler

from .store import DurableJobStoreV2, DurableJobStoreV2Error


DURABLE_JOB_ENGINE_V2_SCHEMA_VERSION = "durable-job-engine.v2"
DURABLE_JOB_V2_STATES = (
    "QUEUED",
    "WAITING_RESOURCE",
    "PREPARING",
    "RUNNING",
    "PAUSED",
    "CANCELLING",
    "CANCELLED",
    "SUCCEEDED",
    "FAILED",
)
_STATE_SET = frozenset(DURABLE_JOB_V2_STATES)
_TERMINAL = frozenset({"CANCELLED", "SUCCEEDED", "FAILED"})
_ACTIVE = frozenset({"QUEUED", "WAITING_RESOURCE", "PREPARING", "RUNNING", "PAUSED", "CANCELLING"})
_JOB_ID = re.compile(r"^jobv2_[a-f0-9]{32}$")
_WORKFLOW_ID = re.compile(r"^[a-z][a-z0-9._-]{1,95}$")
_OWNER_ID = re.compile(r"^[a-z][a-z0-9._:-]{1,95}$")
_WORKER_ID = re.compile(r"^[a-z][a-z0-9._:-]{1,95}$")
_PROFILE_ID = re.compile(r"^[a-z][a-z0-9._-]{1,63}$")
_ARTIFACT_ID = re.compile(r"^artifact_[a-f0-9]{32}$")
_RESERVATION_ID = re.compile(r"^resv_[a-f0-9]{32}$")
_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_UNSAFE_TEXT = re.compile(
    r"(?i)(?:[a-z]:[\\/]|\\\\|(?:https?|file|data):|bearer\s|\b(?:api[_-]?key|secret|password|token)\b|\.\.[\\/])"
)
_MAX_ARTIFACTS = 64
_MAX_QUERY = 80


class DurableJobEngineV2Error(ValueError):
    """Fixed error when a V2 durable job request is invalid."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_id(value: object, pattern: re.Pattern[str]) -> str | None:
    return value if isinstance(value, str) and pattern.fullmatch(value) else None


def _safe_text(value: object, fallback: str, *, maximum: int = 320) -> str:
    if not isinstance(value, str):
        return fallback
    candidate = value.strip()
    if not 1 <= len(candidate) <= maximum or any(ord(char) < 32 for char in candidate):
        return fallback
    return fallback if _UNSAFE_TEXT.search(candidate) else candidate


def _artifact_ids(value: object) -> list[str] | None:
    if not isinstance(value, list) or len(value) > _MAX_ARTIFACTS:
        return None
    items = [item for item in value if isinstance(item, str) and _ARTIFACT_ID.fullmatch(item)]
    return items if len(items) == len(value) and len(set(items)) == len(items) else None


def _progress(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 100 else 0


def _history(value: object, state: str) -> list[str]:
    prior = [item for item in value if isinstance(item, str) and item in _STATE_SET] if isinstance(value, list) else []
    return [*prior[-31:], state]


class ExecutionOwnerRegistry:
    """Server-owned capabilities for a future actual execution bridge.

    An owner record does not contain a callable, command, path, or provider
    secret. It only lets the engine know whether the trusted server integration
    has separately registered the capability to dispatch, pause, or resume.
    """

    def __init__(self, owners: Mapping[str, Mapping[str, Any]] | None = None) -> None:
        self._owners: dict[str, dict[str, bool]] = {}
        for owner_id, capability in (owners or {}).items():
            self.register(owner_id, capability)

    def register(self, owner_id: str, capability: Mapping[str, Any]) -> None:
        if _safe_id(owner_id, _OWNER_ID) is None or not isinstance(capability, Mapping) or set(capability) != {"dispatch", "pause", "resume"} or not all(type(capability.get(name)) is bool for name in ("dispatch", "pause", "resume")):
            raise DurableJobEngineV2Error("execution_owner_invalid")
        self._owners[owner_id] = {name: bool(capability[name]) for name in ("dispatch", "pause", "resume")}

    def capability(self, owner_id: object) -> dict[str, bool] | None:
        identifier = _safe_id(owner_id, _OWNER_ID)
        value = self._owners.get(identifier) if identifier else None
        return dict(value) if value is not None else None

    def snapshot(self) -> list[dict[str, object]]:
        return [{"owner_id": key, **self._owners[key]} for key in sorted(self._owners)]


class DurableJobEngineV2:
    """Persist/reconcile job metadata and bind dispatchable jobs to scheduler."""

    def __init__(self, *, store: DurableJobStoreV2, scheduler: ResourceScheduler, owners: ExecutionOwnerRegistry | None = None) -> None:
        if not isinstance(store, DurableJobStoreV2) or not isinstance(scheduler, ResourceScheduler):
            raise DurableJobEngineV2Error("durable_job_v2_dependencies_invalid")
        self.store = store
        self.scheduler = scheduler
        self.owners = owners or ExecutionOwnerRegistry()

    def _validate_record(self, value: object) -> dict[str, Any] | None:
        if not isinstance(value, Mapping) or value.get("schema_version") != DURABLE_JOB_ENGINE_V2_SCHEMA_VERSION:
            return None
        job_id = _safe_id(value.get("job_id"), _JOB_ID)
        workflow_id = _safe_id(value.get("workflow_id"), _WORKFLOW_ID)
        owner = _safe_id(value.get("execution_owner"), _OWNER_ID)
        worker = _safe_id(value.get("worker_id"), _WORKER_ID)
        profile = _safe_id(value.get("resource_profile_id"), _PROFILE_ID)
        state = value.get("state")
        inputs = _artifact_ids(value.get("input_artifact_ids"))
        artifacts = _artifact_ids(value.get("artifact_refs"))
        retry_of = value.get("retry_of")
        retry_mode = value.get("retry_mode")
        timestamps = value.get("timestamps")
        if (
            job_id is None or workflow_id is None or owner is None or worker is None or profile is None or state not in _STATE_SET
            or inputs is None or artifacts is None or retry_mode not in {"new", "retry_execution", "reconstruct_only"}
            or (retry_of is not None and _safe_id(retry_of, _JOB_ID) is None)
            or not isinstance(timestamps, Mapping) or set(timestamps) != {"created_at", "updated_at", "started_at", "completed_at"}
            or not all(value is None or isinstance(value, str) and 1 <= len(value) <= 64 for value in timestamps.values())
            or type(value.get("dispatchable")) is not bool or type(value.get("actual_execution")) is not bool or type(value.get("archived")) is not bool
        ):
            return None
        reservation_id = value.get("reservation_id")
        if reservation_id is not None and _safe_id(reservation_id, _RESERVATION_ID) is None:
            return None
        return {
            "schema_version": DURABLE_JOB_ENGINE_V2_SCHEMA_VERSION,
            "job_id": job_id,
            "workflow_id": workflow_id,
            "input_artifact_ids": inputs,
            "artifact_refs": artifacts,
            "execution_owner": owner,
            "worker_id": worker,
            "resource_profile_id": profile,
            "state": state,
            "progress": _progress(value.get("progress")),
            "retry_of": retry_of,
            "retry_mode": retry_mode,
            "dispatchable": value["dispatchable"],
            "actual_execution": value["actual_execution"],
            "reservation_id": reservation_id,
            "timestamps": dict(timestamps),
            "error_code": value.get("error_code") if isinstance(value.get("error_code"), str) and _SAFE_CODE.fullmatch(value["error_code"]) else None,
            "reason": _safe_text(value.get("reason"), "Durable job metadata is awaiting a server-owned state transition."),
            "next_action": _safe_text(value.get("next_action"), "Inspect the durable job record before requesting any execution."),
            "state_history": _history(value.get("state_history"), state),
            "archived": value["archived"],
        }

    def _persist(self, record: Mapping[str, Any]) -> dict[str, Any]:
        valid = self._validate_record(record)
        if valid is None:
            raise DurableJobEngineV2Error("durable_job_v2_record_invalid")
        try:
            return self.store.put(valid)
        except DurableJobStoreV2Error as exc:
            raise DurableJobEngineV2Error(exc.code) from exc

    def _get(self, job_id: object) -> dict[str, Any] | None:
        if _safe_id(job_id, _JOB_ID) is None:
            return None
        try:
            stored = self.store.get(job_id)
        except DurableJobStoreV2Error as exc:
            raise DurableJobEngineV2Error(exc.code) from exc
        valid = self._validate_record(stored)
        if stored is not None and valid is None:
            raise DurableJobEngineV2Error("durable_job_v2_record_invalid")
        return valid

    def _transition(
        self,
        record: Mapping[str, Any],
        *,
        state: str,
        reason: str,
        next_action: str,
        error_code: str | None = None,
        progress: int | None = None,
        reservation_id: str | None = None,
        actual_execution: bool | None = None,
        dispatchable: bool | None = None,
    ) -> dict[str, Any]:
        if state not in _STATE_SET:
            raise DurableJobEngineV2Error("durable_job_v2_transition_invalid")
        current = dict(record)
        timestamps = dict(current["timestamps"])
        now = _now()
        timestamps["updated_at"] = now
        if state == "RUNNING" and timestamps["started_at"] is None:
            timestamps["started_at"] = now
        if state in _TERMINAL and timestamps["completed_at"] is None:
            timestamps["completed_at"] = now
        current.update({
            "state": state,
            "timestamps": timestamps,
            "reason": reason,
            "next_action": next_action,
            "error_code": error_code,
            "progress": _progress(progress) if progress is not None else _progress(current.get("progress")),
            "reservation_id": reservation_id,
            "state_history": _history(current.get("state_history"), state),
        })
        if actual_execution is not None:
            current["actual_execution"] = actual_execution
        if dispatchable is not None:
            current["dispatchable"] = dispatchable
        return self._persist(current)

    @staticmethod
    def _is_reconstruct_only(record: Mapping[str, Any]) -> bool:
        return record.get("retry_mode") == "reconstruct_only" and record.get("actual_execution") is False and record.get("state") == "QUEUED"

    def _public(self, record: Mapping[str, Any]) -> dict[str, Any]:
        reconstruct = self._is_reconstruct_only(record)
        state = record["state"]
        dispatchable = record["dispatchable"] is True
        actual_execution = record["actual_execution"] is True
        owner_capability = self.owners.capability(record["execution_owner"]) or {"dispatch": False, "pause": False, "resume": False}
        active = state in _ACTIVE and not reconstruct and (dispatchable or actual_execution)
        if reconstruct:
            lifecycle = "Đã tạo · chưa thực thi"
        elif state in {"QUEUED", "WAITING_RESOURCE", "PREPARING"} and not actual_execution:
            lifecycle = "Đã được lập lịch · chờ worker" if dispatchable else "Đã lập kế hoạch · chưa thực thi"
        elif state == "RUNNING":
            lifecycle = "Đang thực thi bởi worker đã xác nhận"
        elif state == "PAUSED":
            lifecycle = "Đã tạm dừng bởi worker đã xác nhận"
        elif state == "CANCELLING":
            lifecycle = "Đang chờ worker xác nhận hủy"
        else:
            lifecycle = state
        execution = "not_run"
        dry_run = True
        if actual_execution:
            execution = {
                "RUNNING": "running",
                "PAUSED": "paused",
                "CANCELLING": "cancelling",
                "CANCELLED": "cancelled",
                "SUCCEEDED": "completed",
                "FAILED": "failed",
            }.get(state, "not_run")
            dry_run = execution == "not_run"
        return {
            "job_id": record["job_id"],
            "workflow_id": record["workflow_id"],
            "input_artifact_ids": list(record["input_artifact_ids"]),
            "artifact_refs": list(record["artifact_refs"]),
            "retry_of": record["retry_of"],
            "retry_mode": record["retry_mode"],
            "dispatchable": dispatchable,
            "actual_execution": actual_execution,
            "execution_owner": record["execution_owner"],
            "worker_id": record["worker_id"],
            "resource_profile_id": record["resource_profile_id"],
            "state": record["state"],
            "lifecycle": lifecycle,
            "active": active,
            "can_pause": actual_execution and state == "RUNNING" and owner_capability["pause"] is True,
            "can_resume": actual_execution and state == "PAUSED" and owner_capability["resume"] is True,
            "progress": record["progress"],
            "timestamps": dict(record["timestamps"]),
            "error_code": record["error_code"],
            "reason": record["reason"],
            "next_action": record["next_action"],
            "archived": record["archived"],
            "reservation_id": record["reservation_id"],
            "execution": execution,
            "dry_run": dry_run,
        }

    def _result_for_job(self, status: str, record: Mapping[str, Any], **extra: Any) -> dict[str, Any]:
        """Return one public job projection with truthful execution fields."""

        job = self._public(record)
        return {"status": status, "job": job, "execution": job["execution"], "dry_run": job["dry_run"], **extra}

    def _release_unpersisted_admission(self, job_id: str, worker_id: str, reservation_id: object) -> None:
        """Release only this in-memory scheduler admission after a store failure.

        No worker can have started before the durable record was persisted. The
        bounded scheduler entry is therefore safe to cancel/acknowledge here;
        this prevents a metadata write failure from consuming capacity for a
        job that the durable engine cannot recover or expose.
        """

        try:
            cancelled = self.scheduler.cancel(job_id, worker_id)
            if cancelled.get("state") == "CANCELLING" and isinstance(reservation_id, str):
                self.scheduler.acknowledge_cancel(job_id, worker_id, reservation_id)
        except Exception:
            # The original durable-store failure remains authoritative. The
            # scheduler is in-process-only and never represents an executing
            # worker at this point, so no exception is exposed from cleanup.
            pass

    def admit(self, request: object) -> dict[str, Any]:
        if not isinstance(request, Mapping) or set(request) != {"workflow_id", "input_artifact_ids", "artifact_refs", "execution_owner", "worker_id", "resource_profile_id"}:
            return {"status": "invalid", "code": "durable_job_v2_request_invalid", "execution": "not_run", "dry_run": True}
        workflow = _safe_id(request.get("workflow_id"), _WORKFLOW_ID)
        inputs = _artifact_ids(request.get("input_artifact_ids"))
        artifacts = _artifact_ids(request.get("artifact_refs"))
        owner = _safe_id(request.get("execution_owner"), _OWNER_ID)
        worker = _safe_id(request.get("worker_id"), _WORKER_ID)
        profile = _safe_id(request.get("resource_profile_id"), _PROFILE_ID)
        capability = self.owners.capability(owner)
        if workflow is None or inputs is None or artifacts is None or owner is None or worker is None or profile is None:
            return {"status": "invalid", "code": "durable_job_v2_request_invalid", "execution": "not_run", "dry_run": True}
        if capability is None or capability.get("dispatch") is not True:
            return {"status": "unavailable", "code": "execution_owner_unavailable", "reason": "No current server-owned execution owner can dispatch this durable job.", "next_action": "Use a separately registered execution owner; no job record was created.", "execution": "not_run", "dry_run": True}
        job_id = f"jobv2_{secrets.token_hex(16)}"
        scheduler_result = self.scheduler.submit(job_id, worker, profile)
        if scheduler_result.get("status") in {"invalid", "unavailable", "conflict"}:
            return {"status": "unavailable", "code": "scheduler_admission_unavailable", "reason": "The server-owned resource scheduler did not accept this job.", "next_action": "Review the selected scheduler profile and inventory before trying again.", "execution": "not_run", "dry_run": True}
        state = scheduler_result.get("state")
        if state not in _STATE_SET:
            return {"status": "unavailable", "code": "scheduler_state_invalid", "execution": "not_run", "dry_run": True}
        now = _now()
        record = {
            "schema_version": DURABLE_JOB_ENGINE_V2_SCHEMA_VERSION,
            "job_id": job_id,
            "workflow_id": workflow,
            "input_artifact_ids": inputs,
            "artifact_refs": artifacts,
            "execution_owner": owner,
            "worker_id": worker,
            "resource_profile_id": profile,
            "state": state,
            "progress": 0,
            "retry_of": None,
            "retry_mode": "new",
            "dispatchable": True,
            "actual_execution": False,
            "reservation_id": scheduler_result.get("reservation", {}).get("reservation_id") if isinstance(scheduler_result.get("reservation"), Mapping) else None,
            "timestamps": {"created_at": now, "updated_at": now, "started_at": None, "completed_at": None},
            "error_code": None,
            "reason": "A durable job was admitted to the server-owned scheduler; no worker has started yet.",
            "next_action": "The registered execution owner must claim the exact reservation before marking this job running.",
            "state_history": [state],
            "archived": False,
        }
        try:
            persisted = self._persist(record)
        except DurableJobEngineV2Error as exc:
            self._release_unpersisted_admission(job_id, worker, record["reservation_id"])
            return {
                "status": "unavailable",
                "code": exc.args[0] if exc.args else "durable_job_v2_store_unavailable",
                "reason": "Durable metadata could not be persisted, so the in-memory scheduler admission was released before any worker started.",
                "next_action": "Inspect the durable metadata store and retry server-owned admission only after it is available.",
                "execution": "not_run",
                "dry_run": True,
            }
        return self._result_for_job("accepted", persisted)

    def claim_running(self, job_id: object, worker_id: object, reservation_id: object) -> dict[str, Any] | None:
        record = self._get(job_id)
        if record is None:
            return None
        if record["dispatchable"] is not True or record["actual_execution"] is not False or record["state"] != "PREPARING":
            return {"status": "conflict", "code": "durable_job_not_preparing", "execution": "not_run", "dry_run": True}
        scheduler = self.scheduler.claim_running(job_id, worker_id, reservation_id)
        if scheduler.get("state") != "RUNNING":
            return {"status": "conflict", "code": scheduler.get("code", "reservation_identity_mismatch"), "execution": "not_run", "dry_run": True}
        updated = self._transition(
            record,
            state="RUNNING",
            reason="The exact server-owned worker claimed its matching scheduler reservation and reported that execution started.",
            next_action="The worker may report bounded progress and a terminal result.",
            reservation_id=str(reservation_id),
            actual_execution=True,
        )
        return self._result_for_job("running", updated)

    def progress(self, job_id: object, worker_id: object, reservation_id: object, value: object) -> dict[str, Any] | None:
        record = self._get(job_id)
        if record is None:
            return None
        if record["actual_execution"] is not True or record["state"] != "RUNNING" or record.get("reservation_id") != reservation_id or record.get("worker_id") != worker_id or not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 100:
            return {"status": "conflict", "code": "durable_job_progress_rejected", "execution": "not_run", "dry_run": True}
        updated = self._transition(record, state="RUNNING", reason="The exact server-owned worker reported bounded progress.", next_action="Continue only while the matching scheduler reservation remains bound.", progress=value, reservation_id=str(reservation_id))
        return self._result_for_job("completed", updated)

    def pause(self, job_id: object, worker_id: object, reservation_id: object) -> dict[str, Any] | None:
        """Record an already-confirmed worker pause; this method never pauses a process."""

        record = self._get(job_id)
        if record is None:
            return None
        capability = self.owners.capability(record["execution_owner"])
        if capability is None or capability.get("pause") is not True:
            return {"status": "unavailable", "code": "durable_job_pause_unavailable", "execution": "not_run", "dry_run": True}
        if record["actual_execution"] is not True or record["state"] != "RUNNING":
            return {"status": "conflict", "code": "durable_job_not_running", "execution": "not_run", "dry_run": True}
        scheduler = self.scheduler.pause(job_id, worker_id, reservation_id)
        if scheduler.get("state") != "PAUSED":
            return {"status": "conflict", "code": scheduler.get("code", "reservation_identity_mismatch"), "execution": "not_run", "dry_run": True}
        updated = self._transition(
            record,
            state="PAUSED",
            reason="The exact server-owned worker confirmed that it paused under its existing reservation.",
            next_action="Resume is available only when the same execution owner explicitly supports it.",
            reservation_id=str(reservation_id),
        )
        return self._result_for_job("paused", updated)

    def resume(self, job_id: object, worker_id: object, reservation_id: object) -> dict[str, Any] | None:
        """Record an already-confirmed worker resume; this method never starts a worker."""

        record = self._get(job_id)
        if record is None:
            return None
        capability = self.owners.capability(record["execution_owner"])
        if capability is None or capability.get("resume") is not True:
            return {"status": "unavailable", "code": "durable_job_resume_unavailable", "execution": "not_run", "dry_run": True}
        if record["actual_execution"] is not True or record["state"] != "PAUSED":
            return {"status": "conflict", "code": "durable_job_not_paused", "execution": "not_run", "dry_run": True}
        scheduler = self.scheduler.resume(job_id, worker_id, reservation_id)
        if scheduler.get("state") != "RUNNING":
            return {"status": "conflict", "code": scheduler.get("code", "reservation_identity_mismatch"), "execution": "not_run", "dry_run": True}
        updated = self._transition(
            record,
            state="RUNNING",
            reason="The exact server-owned worker confirmed that it resumed under its existing reservation.",
            next_action="Continue only while the matching scheduler reservation remains bound.",
            reservation_id=str(reservation_id),
        )
        return self._result_for_job("resumed", updated)

    def finish(self, job_id: object, worker_id: object, reservation_id: object, *, succeeded: bool, artifact_refs: object = None) -> dict[str, Any] | None:
        record = self._get(job_id)
        if record is None:
            return None
        artifacts = _artifact_ids(artifact_refs) if artifact_refs is not None else list(record["artifact_refs"])
        if artifacts is None:
            return {"status": "invalid", "code": "durable_job_artifacts_invalid", "execution": "not_run", "dry_run": True}
        scheduler = self.scheduler.finish(job_id, worker_id, reservation_id, succeeded=succeeded)
        target = "SUCCEEDED" if succeeded else "FAILED"
        if scheduler.get("state") != target:
            return {"status": "conflict", "code": scheduler.get("code", "reservation_identity_mismatch"), "execution": "not_run", "dry_run": True}
        changed = dict(record)
        changed["artifact_refs"] = artifacts
        updated = self._transition(changed, state=target, reason="The exact worker published a terminal result; artifacts remain referenced and are not deleted by the job engine.", next_action="Inspect artifacts or archive/delete only the metadata history when appropriate.", progress=100 if succeeded else record["progress"], reservation_id=None)
        return self._result_for_job("completed", updated)

    def cancel(self, job_id: object) -> dict[str, Any] | None:
        record = self._get(job_id)
        if record is None:
            return None
        if self._is_reconstruct_only(record):
            updated = self._transition(record, state="CANCELLED", reason="The reconstruct-only record was cancelled before any execution was possible.", next_action="The historical job and its artifacts remain unchanged.", reservation_id=None)
            return self._result_for_job("cancelled", updated)
        if record["state"] not in _ACTIVE:
            return {"status": "conflict", "code": "durable_job_terminal", "execution": "not_run", "dry_run": True}
        scheduler = self.scheduler.cancel(record["job_id"], record["worker_id"])
        if scheduler.get("state") == "CANCELLED":
            updated = self._transition(record, state="CANCELLED", reason="The job was cancelled before a worker started; no artifact was deleted.", next_action="The job metadata can be archived or removed from history without deleting artifacts.", reservation_id=None)
            return self._result_for_job("cancelled", updated)
        if scheduler.get("state") != "CANCELLING":
            return {"status": "conflict", "code": scheduler.get("code", "scheduler_cancellation_invalid"), "execution": "not_run", "dry_run": True}
        updated = self._transition(record, state="CANCELLING", reason="Cancellation was requested from the exact scheduler worker binding.", next_action="Wait for the worker to acknowledge cancellation; artifacts remain preserved.", reservation_id=record["reservation_id"])
        return self._result_for_job("cancelling", updated)

    def acknowledge_cancel(self, job_id: object, worker_id: object, reservation_id: object) -> dict[str, Any] | None:
        record = self._get(job_id)
        if record is None:
            return None
        scheduler = self.scheduler.acknowledge_cancel(job_id, worker_id, reservation_id)
        if scheduler.get("state") != "CANCELLED":
            return {"status": "conflict", "code": scheduler.get("code", "reservation_identity_mismatch"), "execution": "not_run", "dry_run": True}
        updated = self._transition(record, state="CANCELLED", reason="The exact worker acknowledged cancellation and its reservation was released.", next_action="Artifacts are retained; archive or remove only this terminal metadata record if desired.", reservation_id=None)
        return self._result_for_job("cancelled", updated)

    def retry(self, job_id: object, *, mode: object) -> dict[str, Any] | None:
        record = self._get(job_id)
        if record is None:
            return None
        if record["state"] not in _TERMINAL:
            return {"status": "conflict", "code": "durable_job_retry_not_terminal", "execution": "not_run", "dry_run": True}
        if mode == "RETRY_EXECUTION":
            return {"status": "unavailable", "code": "actual_retry_execution_not_available", "reason": "No registered V2 owner dispatch bridge can truthfully retry execution in this product state.", "next_action": "Use Tạo lại tác vụ to create a reconstruct-only record, or wait for a separately registered execution owner.", "execution": "not_run", "dry_run": True}
        if mode != "RECONSTRUCT_ONLY":
            return {"status": "invalid", "code": "durable_job_retry_mode_invalid", "execution": "not_run", "dry_run": True}
        now = _now()
        new_record = {
            "schema_version": DURABLE_JOB_ENGINE_V2_SCHEMA_VERSION,
            "job_id": f"jobv2_{secrets.token_hex(16)}",
            "workflow_id": record["workflow_id"],
            "input_artifact_ids": list(record["input_artifact_ids"]),
            "artifact_refs": list(record["artifact_refs"]),
            "execution_owner": record["execution_owner"],
            "worker_id": record["worker_id"],
            "resource_profile_id": record["resource_profile_id"],
            "state": "QUEUED",
            "progress": 0,
            "retry_of": record["job_id"],
            "retry_mode": "reconstruct_only",
            "dispatchable": False,
            "actual_execution": False,
            "reservation_id": None,
            "timestamps": {"created_at": now, "updated_at": now, "started_at": None, "completed_at": None},
            "error_code": None,
            "reason": "A new durable record was reconstructed from sanitized reproducible metadata; no execution owner was started.",
            "next_action": "Đã tạo tác vụ mới nhưng chưa thực thi. Review/dispatch requires a separately available execution owner.",
            "state_history": ["QUEUED"],
            "archived": False,
        }
        stored = self._persist(new_record)
        return self._result_for_job("queued", stored, actual_retry_execution=False)

    def archive(self, job_id: object) -> dict[str, Any] | None:
        record = self._get(job_id)
        if record is None:
            return None
        if record["state"] not in _TERMINAL:
            return {"status": "conflict", "code": "durable_job_archive_not_terminal", "execution": "not_run", "dry_run": True}
        updated = dict(record)
        updated["archived"] = True
        updated["timestamps"] = {**updated["timestamps"], "updated_at": _now()}
        stored = self._persist(updated)
        return self._result_for_job("completed", stored, artifacts_preserved=True)

    def delete_history(self, job_ids: object) -> dict[str, Any]:
        if not isinstance(job_ids, list) or not 1 <= len(job_ids) <= 100 or not all(_safe_id(item, _JOB_ID) is not None for item in job_ids) or len(set(job_ids)) != len(job_ids):
            return {"status": "invalid", "code": "durable_job_history_selection_invalid", "execution": "not_run", "dry_run": True}
        records: list[dict[str, Any]] = []
        for job_id in job_ids:
            record = self._get(job_id)
            if record is None:
                return {"status": "not_found", "code": "durable_job_history_not_found", "execution": "not_run", "dry_run": True}
            if record["state"] not in _TERMINAL:
                return {"status": "conflict", "code": "durable_job_history_not_terminal", "execution": "not_run", "dry_run": True}
            records.append(record)
        try:
            removed = self.store.delete_many([record["job_id"] for record in records])
        except DurableJobStoreV2Error as exc:
            if exc.code == "durable_job_v2_record_missing":
                return {"status": "not_found", "code": "durable_job_history_not_found", "execution": "not_run", "dry_run": True}
            raise DurableJobEngineV2Error(exc.code) from exc
        return {"status": "completed", "removed_count": removed, "artifacts_preserved": True, "execution": "not_run", "dry_run": True}

    def reconcile_startup(
        self,
        *,
        worker_alive: Callable[[str, str], bool] | None = None,
        artifact_complete: Callable[[list[str]], bool] | None = None,
    ) -> dict[str, int]:
        """Reconcile stale active records without guessing an active worker."""

        try:
            records = self.store.list()
        except DurableJobStoreV2Error as exc:
            raise DurableJobEngineV2Error(exc.code) from exc
        reconciled = {"waiting_resource": 0, "failed": 0, "succeeded": 0, "retained": 0}
        for raw in records:
            record = self._validate_record(raw)
            if record is None or record["state"] in _TERMINAL or self._is_reconstruct_only(record):
                reconciled["retained"] += 1
                continue
            if record["state"] in {"QUEUED", "WAITING_RESOURCE", "PREPARING"}:
                updated = self._transition(
                    record,
                    state="WAITING_RESOURCE",
                    reason="A pre-start scheduler reservation is not durable across process restart and was released for fresh admission.",
                    next_action="Re-run server-owned resource admission before any worker can start.",
                    error_code="reservation_stale",
                    reservation_id=None,
                    dispatchable=False,
                )
                del updated
                reconciled["waiting_resource"] += 1
                continue
            artifacts = list(record["artifact_refs"])
            complete = bool(callable(artifact_complete) and artifact_complete(artifacts))
            liveness_available = callable(worker_alive)
            alive = bool(liveness_available and worker_alive(record["job_id"], record["worker_id"]))
            if complete:
                self._transition(record, state="SUCCEEDED", reason="Restart reconciliation found a complete server-owned artifact set; no artifact was deleted.", next_action="Inspect the preserved artifacts and terminal job metadata.", reservation_id=None, progress=100)
                reconciled["succeeded"] += 1
            elif not liveness_available:
                self._transition(record, state="FAILED", reason="Restart reconciliation has no trusted worker liveness probe, so it cannot adopt or claim the prior execution.", next_action="Inspect the prior owner and create a fresh durable job only if the request is still reproducible.", error_code="worker_liveness_unavailable_after_restart", reservation_id=None)
                reconciled["failed"] += 1
            elif alive:
                # A live worker cannot be blindly adopted without a durable
                # session/lease protocol. Keep a truthful terminal failure
                # rather than claiming this new engine owns it.
                self._transition(record, state="FAILED", reason="A worker appears live after restart but lacks a durable V2 ownership lease; execution was not adopted.", next_action="Inspect the worker through its owner and create a fresh durable job if appropriate.", error_code="worker_ownership_unproven", reservation_id=None)
                reconciled["failed"] += 1
            else:
                self._transition(record, state="FAILED", reason="The previous worker is unavailable after restart and no complete artifact set was recorded.", next_action="Use Tạo lại tác vụ only if the sanitized reproducible request remains valid.", error_code="worker_gone_after_restart", reservation_id=None)
                reconciled["failed"] += 1
        return reconciled

    def get(self, job_id: object) -> dict[str, Any] | None:
        record = self._get(job_id)
        return self._public(record) if record is not None else None

    def snapshot(self, *, status: object = None, query: object = None, archived: object = False) -> dict[str, Any]:
        wanted_status = status if isinstance(status, str) and status in _STATE_SET else None
        needle = query.strip().casefold()[:_MAX_QUERY] if isinstance(query, str) and not _UNSAFE_TEXT.search(query) else ""
        include_archived = archived is True
        try:
            raw_records = self.store.list()
        except DurableJobStoreV2Error as exc:
            return {"schema_version": DURABLE_JOB_ENGINE_V2_SCHEMA_VERSION, "status": "unavailable", "records": [], "counts": {}, "reason": "Durable Job Engine V2 metadata store is unavailable.", "next_action": "Inspect the local durable metadata store before retrying.", "code": exc.code, "execution": "not_run", "dry_run": True}
        records = [self._validate_record(item) for item in raw_records]
        if any(item is None for item in records):
            return {"schema_version": DURABLE_JOB_ENGINE_V2_SCHEMA_VERSION, "status": "unavailable", "records": [], "counts": {}, "reason": "Durable Job Engine V2 found an invalid persisted metadata record.", "next_action": "Preserve the store and inspect it through Diagnostics before recovery.", "code": "durable_job_v2_record_invalid", "execution": "not_run", "dry_run": True}
        public = [self._public(item) for item in records if item is not None]
        if not include_archived:
            public = [item for item in public if item["archived"] is False]
        if wanted_status is not None:
            public = [item for item in public if item["state"] == wanted_status]
        if needle:
            public = [item for item in public if needle in f"{item['job_id']} {item['workflow_id']} {item['execution_owner']} {item['resource_profile_id']} {item['state']}".casefold()]
        counts = {state: sum(1 for item in public if item["state"] == state) for state in DURABLE_JOB_V2_STATES}
        active = sum(1 for item in public if item["active"] is True)
        reconstruct_only = sum(1 for item in public if item["retry_mode"] == "reconstruct_only" and item["state"] == "QUEUED")
        return {
            "schema_version": DURABLE_JOB_ENGINE_V2_SCHEMA_VERSION,
            "status": "completed",
            "records": public,
            "counts": {**counts, "active": active, "reconstruct_only_pending": reconstruct_only},
            "execution_owners": self.owners.snapshot(),
            "reason": "Durable Job Engine V2 lists persisted metadata; it does not claim a worker is running without an exact owned reservation/session.",
            "next_action": "Use the registered scheduler/owner path for actual execution, or keep reconstruct-only records visibly not executed.",
            "execution": "not_run",
            "dry_run": True,
        }


__all__ = [
    "DURABLE_JOB_ENGINE_V2_SCHEMA_VERSION",
    "DURABLE_JOB_V2_STATES",
    "DurableJobEngineV2",
    "DurableJobEngineV2Error",
    "ExecutionOwnerRegistry",
]
