"""Declarative, recoverable work coordination without a runtime integration.

This module owns no subprocesses, sockets, models, media, or hardware.  It
coordinates only adapters explicitly registered by server code and can be
exercised with in-memory adapters in unit tests.  Product wiring belongs to a
future V5-D integration change.
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from src.services.api.jobs import DurableJobStore, DurableStoreHealthError
from src.services.artifact_store import atomic_write_job_output

from .contracts import (
    JOB_RECORD_VERSION,
    JOB_STATES,
    RETRYABLE_JOB_STATES,
    TERMINAL_JOB_STATES,
    ExecutionDescriptor,
    JobContractError,
    JobSpec,
    detached_json,
    is_artifact_id,
    validate_job_spec,
)


MAX_SERVER_ADAPTERS = 64
MAX_RUNNER_STATES = 64
MAX_STATE_HISTORY = 32
_ADAPTER_ID = re.compile(r"[a-z][a-z0-9_.-]{0,63}")
_RESULT_STATUSES = frozenset({"completed", "failed", "unavailable"})
_ALLOWED_TRANSITIONS = {
    "queued": frozenset({"starting", "cancelling", "unavailable", "interrupted"}),
    "starting": frozenset({"running", "cancelling", "failed", "unavailable", "interrupted"}),
    "running": frozenset({"cancelling", "completed", "failed", "unavailable", "interrupted"}),
    "cancelling": frozenset({"interrupted", "failed", "unavailable"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "unavailable": frozenset(),
    "interrupted": frozenset(),
}

Adapter = Callable[[ExecutionDescriptor, "DurableJobContext"], dict[str, Any]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ServerOwnedAdapterRegistry:
    """Code-owned allowlist of in-process adapter functions.

    A client descriptor can name an adapter ID, but this registry is not
    serialized, accepted from a request, or populated through a route.  The
    registration API is deliberately small so a future integration can build
    it from server source only.
    """

    def __init__(self) -> None:
        self._adapters: dict[str, Adapter] = {}
        self._lock = threading.RLock()

    def register(self, adapter_id: str, adapter: Adapter) -> None:
        if not isinstance(adapter_id, str) or not _ADAPTER_ID.fullmatch(adapter_id) or not callable(adapter):
            raise ValueError("Server adapter registration is invalid.")
        with self._lock:
            if adapter_id in self._adapters:
                raise ValueError("Server adapter ID is already registered.")
            if len(self._adapters) >= MAX_SERVER_ADAPTERS:
                raise ValueError("Server adapter registry limit reached.")
            self._adapters[adapter_id] = adapter

    def resolve(self, adapter_id: str) -> Adapter | None:
        with self._lock:
            return self._adapters.get(adapter_id)

    def contains(self, adapter_id: str) -> bool:
        return self.resolve(adapter_id) is not None

    def summaries(self) -> list[dict[str, str]]:
        with self._lock:
            return [{"adapter_id": adapter_id} for adapter_id in sorted(self._adapters)]


class DurableJobContext:
    """Cooperative cancellation and bounded progress for a registered adapter."""

    def __init__(self, job_id: str, progress_callback: Callable[[int], None]) -> None:
        self.job_id = job_id
        self._cancel_event = threading.Event()
        self._progress_callback = progress_callback

    @property
    def cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def cancel(self) -> None:
        self._cancel_event.set()

    def progress(self, value: int) -> None:
        """Persist a numeric checkpoint without accepting free-form messages."""

        if isinstance(value, bool) or not isinstance(value, int):
            return
        self._progress_callback(max(0, min(100, value)))


class DurableWorkEngine:
    """Authoritative state machine for persisted, reconstructable JobSpecs.

    It has no default registry and does not start work unless ``start`` or
    ``run`` is called by trusted server code.  Resource slots are policy
    semaphores only; they do not inspect or reserve a physical GPU.
    """

    def __init__(
        self,
        store: DurableJobStore,
        registry: ServerOwnedAdapterRegistry,
        *,
        max_concurrent: int = 4,
        gpu_slots: int = 1,
        max_runner_states: int = MAX_RUNNER_STATES,
    ) -> None:
        if not isinstance(store, DurableJobStore) or not isinstance(registry, ServerOwnedAdapterRegistry):
            raise TypeError("Durable engine requires server-owned store and adapter registry.")
        if isinstance(max_concurrent, bool) or not isinstance(max_concurrent, int) or not 1 <= max_concurrent <= 32:
            raise ValueError("max_concurrent must be between 1 and 32.")
        if isinstance(gpu_slots, bool) or not isinstance(gpu_slots, int) or not 0 <= gpu_slots <= 8:
            raise ValueError("gpu_slots must be between 0 and 8.")
        self.store = store
        self.registry = registry
        self.max_runner_states = max(1, min(MAX_RUNNER_STATES, int(max_runner_states)))
        self._lock = threading.RLock()
        self._concurrency_slots = threading.BoundedSemaphore(max_concurrent)
        self._gpu_slots = threading.BoundedSemaphore(gpu_slots) if gpu_slots else None
        self._contexts: dict[str, DurableJobContext] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._held_resources: dict[str, tuple[bool, str | None]] = {}
        self._exclusive_groups: set[str] = set()

    @staticmethod
    def _history(record: dict[str, Any], state: str) -> list[str]:
        history = record.get("state_history")
        values = [item for item in history if isinstance(item, str) and item in JOB_STATES] if isinstance(history, list) else []
        values.append(state)
        return values[-MAX_STATE_HISTORY:]

    @staticmethod
    def _resource_plan(spec: JobSpec) -> dict[str, Any]:
        resources = spec.descriptor.resources.to_mapping()
        return {
            "dry_run": True,
            "cpu_slots": resources["cpu_slots"],
            "gpu_slots": resources["gpu_slots"],
            "ram_mb": resources["ram_mb"],
            "disk_mb": resources["disk_mb"],
            "exclusive_group": resources["exclusive_group"],
        }

    @staticmethod
    def _public_status(record: dict[str, Any]) -> str:
        return "unavailable" if record.get("status") == "unavailable" else "partial"

    def _store_unavailable(self, job_id: str | None = None) -> dict[str, Any]:
        """Project an unhealthy durable store without exposing file details."""

        health = self.store.health()
        result: dict[str, Any] = {
            "status": "unavailable",
            "execution": "not_run",
            "dry_run": True,
            "capability_status": "unavailable",
            "reason_code": health.get("code", "DURABLE_STORE_UNAVAILABLE"),
            "action_code": "RESTORE_DURABLE_STATE",
        }
        if isinstance(job_id, str) and re.fullmatch(r"jobv5_[a-f0-9]{32}", job_id):
            result["id"] = job_id
        return result

    def _make_record(self, spec: JobSpec, *, retry_of: str | None = None, attempt: int = 1) -> dict[str, Any]:
        timestamp = _now()
        return {
            "contract_version": JOB_RECORD_VERSION,
            "id": f"jobv5_{uuid.uuid4().hex}",
            "status": "queued",
            "created_at": timestamp,
            "updated_at": timestamp,
            "started_at": None,
            "finished_at": None,
            "progress": 0,
            "execution": "not_run",
            "dry_run": True,
            "job_spec": spec.to_mapping(),
            "job_spec_fingerprint": spec.fingerprint,
            "descriptor_summary": spec.descriptor.summary(),
            "resource_plan": self._resource_plan(spec),
            "attempt": attempt,
            "retry_of": retry_of,
            "retry_available": False,
            "reason_code": None,
            "action_code": None,
            "result_summary": None,
            "artifacts": [],
            "state_history": ["queued"],
        }

    def _update_locked(self, job_id: str, changes: dict[str, Any], *, progress_only: bool = False) -> dict[str, Any] | None:
        changes = dict(changes)
        changes["updated_at"] = _now()
        return self.store.update(job_id, changes, progress_only=progress_only)

    def _transition_locked(self, job_id: str, target: str, **changes: Any) -> dict[str, Any] | None:
        if target not in JOB_STATES:
            raise ValueError("Unknown durable job state.")
        record = self.store.get(job_id)
        if record is None:
            return None
        current = str(record.get("status"))
        if current == target:
            return record
        if target not in _ALLOWED_TRANSITIONS.get(current, frozenset()):
            return None
        changes["status"] = target
        changes["state_history"] = self._history(record, target)
        if target == "starting":
            changes.setdefault("started_at", _now())
        if target in TERMINAL_JOB_STATES:
            changes.setdefault("finished_at", _now())
            changes["execution"] = "not_run"
            changes["dry_run"] = True
        return self._update_locked(job_id, changes)

    def _progress(self, job_id: str, value: int) -> None:
        with self._lock:
            try:
                record = self.store.get(job_id)
            except DurableStoreHealthError:
                return
            if record is None or str(record.get("status")) not in {"starting", "running"}:
                return
            try:
                self._update_locked(job_id, {"progress": value}, progress_only=True)
            except DurableStoreHealthError:
                return

    def _can_retry(self, record: dict[str, Any]) -> bool:
        try:
            spec = validate_job_spec(record.get("job_spec"))
        except JobContractError:
            return False
        return spec.descriptor.reconstructable and self.registry.contains(spec.descriptor.adapter_id)

    def submit(self, value: Any, *, start: bool = False, retry_of: str | None = None, attempt: int = 1) -> dict[str, Any]:
        """Persist a validated job.  It never accepts a callable or loader."""

        spec = validate_job_spec(value)
        if not self.registry.contains(spec.descriptor.adapter_id):
            raise JobContractError("ADAPTER_UNAVAILABLE", "Select a server-supported adapter.")
        record = self._make_record(spec, retry_of=retry_of, attempt=attempt)
        if spec.descriptor.resources.gpu_slots and self._gpu_slots is None:
            record.update(
                {
                    "status": "unavailable",
                    "finished_at": _now(),
                    "reason_code": "GPU_SLOT_UNAVAILABLE",
                    "action_code": "CONFIGURE_GPU_SLOT",
                    "retry_available": spec.descriptor.reconstructable,
                    "state_history": ["queued", "unavailable"],
                }
            )
        stored = self.store.put(record)
        if start and stored.get("status") == "queued":
            self.start(str(stored["id"]))
        return self.public_job(stored)

    def start(self, job_id: str) -> dict[str, Any] | None:
        """Start a daemon coordinator thread only for a trusted, queued job."""

        try:
            with self._lock:
                record = self.store.get(job_id)
                if record is None or record.get("status") != "queued" or job_id in self._threads:
                    return self.public_job(record) if record is not None else None
                if len(self._threads) >= self.max_runner_states:
                    self._update_locked(job_id, {"reason_code": "RUNNER_STATE_LIMIT", "action_code": "RETRY_LATER"})
                    return self.public_job(self.store.get(job_id) or record)
                thread = threading.Thread(target=self.run, args=(job_id,), name=f"LocalAIHub-V5-{job_id[-8:]}", daemon=True)
                self._threads[job_id] = thread
        except DurableStoreHealthError:
            return self._store_unavailable(job_id)
        thread.start()
        return self.get(job_id)

    def _acquire_resources_locked(self, job_id: str, spec: JobSpec) -> bool:
        if not self._concurrency_slots.acquire(blocking=False):
            self._update_locked(job_id, {"reason_code": "CONCURRENCY_LIMIT", "action_code": "RETRY_LATER"})
            return False
        gpu_acquired = False
        group = spec.descriptor.resources.exclusive_group
        if spec.descriptor.resources.gpu_slots:
            if self._gpu_slots is None or not self._gpu_slots.acquire(blocking=False):
                self._concurrency_slots.release()
                self._update_locked(job_id, {"reason_code": "GPU_SLOT_BUSY", "action_code": "RETRY_LATER"})
                return False
            gpu_acquired = True
        if group and group in self._exclusive_groups:
            if gpu_acquired and self._gpu_slots is not None:
                self._gpu_slots.release()
            self._concurrency_slots.release()
            self._update_locked(job_id, {"reason_code": "EXCLUSIVE_GROUP_BUSY", "action_code": "RETRY_LATER"})
            return False
        if group:
            self._exclusive_groups.add(group)
        self._held_resources[job_id] = (gpu_acquired, group)
        return True

    def _release_resources_locked(self, job_id: str) -> None:
        held = self._held_resources.pop(job_id, None)
        if held is None:
            return
        gpu_acquired, group = held
        if group:
            self._exclusive_groups.discard(group)
        if gpu_acquired and self._gpu_slots is not None:
            self._gpu_slots.release()
        self._concurrency_slots.release()

    @staticmethod
    def _result_summary(value: Any) -> tuple[str, dict[str, Any] | None, str | None, str | None]:
        if type(value) is not dict or set(value) - {"status", "summary", "reason_code", "action_code"}:
            return "failed", None, "INVALID_ADAPTER_RESULT", "CHECK_SERVER_ADAPTER"
        status = value.get("status")
        if status not in _RESULT_STATUSES:
            return "failed", None, "INVALID_ADAPTER_RESULT", "CHECK_SERVER_ADAPTER"
        summary_value = value.get("summary")
        summary: dict[str, Any] | None = None
        if summary_value is not None:
            if type(summary_value) is not dict or set(summary_value) - {"count", "artifact_ids", "result_type"}:
                return "failed", None, "INVALID_ADAPTER_RESULT", "CHECK_SERVER_ADAPTER"
            summary = {}
            if "count" in summary_value:
                count = summary_value["count"]
                if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= 1_000_000:
                    return "failed", None, "INVALID_ADAPTER_RESULT", "CHECK_SERVER_ADAPTER"
                summary["count"] = count
            if "artifact_ids" in summary_value:
                artifact_ids = summary_value["artifact_ids"]
                if type(artifact_ids) is not list or len(artifact_ids) > 64 or not all(is_artifact_id(item) for item in artifact_ids):
                    return "failed", None, "INVALID_ADAPTER_RESULT", "CHECK_SERVER_ADAPTER"
                summary["artifact_ids"] = list(artifact_ids)
            if "result_type" in summary_value:
                result_type = summary_value["result_type"]
                if not isinstance(result_type, str) or not _ADAPTER_ID.fullmatch(result_type):
                    return "failed", None, "INVALID_ADAPTER_RESULT", "CHECK_SERVER_ADAPTER"
                summary["result_type"] = result_type
        reason_code = value.get("reason_code")
        action_code = value.get("action_code")
        for candidate in (reason_code, action_code):
            if candidate is not None and (not isinstance(candidate, str) or not _ADAPTER_ID.fullmatch(candidate)):
                return "failed", None, "INVALID_ADAPTER_RESULT", "CHECK_SERVER_ADAPTER"
        return status, detached_json(summary) if summary is not None else None, reason_code, action_code

    def run(self, job_id: str) -> dict[str, Any] | None:
        """Run work while projecting durable-store failure as static unavailable."""

        try:
            return self._run(job_id)
        except DurableStoreHealthError:
            with self._lock:
                self._contexts.pop(job_id, None)
                self._threads.pop(job_id, None)
                self._release_resources_locked(job_id)
            return self._store_unavailable(job_id)

    def _run(self, job_id: str) -> dict[str, Any] | None:
        """Run one queued job synchronously through a server-registered adapter."""

        context: DurableJobContext | None = None
        spec: JobSpec | None = None
        adapter: Adapter | None = None
        with self._lock:
            record = self.store.get(job_id)
            if record is None:
                return None
            if record.get("status") == "cancelling":
                self._transition_locked(job_id, "interrupted", reason_code="CANCELLED", action_code="CREATE_NEW_JOB")
                return self.get(job_id)
            if record.get("status") != "queued":
                return self.public_job(record)
            try:
                spec = validate_job_spec(record.get("job_spec"))
            except JobContractError:
                self._transition_locked(job_id, "unavailable", reason_code="INVALID_PERSISTED_DESCRIPTOR", action_code="CREATE_NEW_JOB")
                return self.get(job_id)
            adapter = self.registry.resolve(spec.descriptor.adapter_id)
            if adapter is None:
                self._transition_locked(job_id, "unavailable", reason_code="ADAPTER_UNAVAILABLE", action_code="CREATE_NEW_JOB")
                return self.get(job_id)
            if len(self._contexts) >= self.max_runner_states:
                self._update_locked(job_id, {"reason_code": "RUNNER_STATE_LIMIT", "action_code": "RETRY_LATER"})
                return self.get(job_id)
            if not self._acquire_resources_locked(job_id, spec):
                return self.get(job_id)
            context = DurableJobContext(job_id, lambda value: self._progress(job_id, value))
            self._contexts[job_id] = context
            self._transition_locked(job_id, "starting", progress=1, reason_code=None, action_code=None)

        try:
            with self._lock:
                live = self.store.get(job_id)
                if context.cancelled or (live is not None and live.get("status") == "cancelling"):
                    self._transition_locked(job_id, "interrupted", reason_code="CANCELLED", action_code="CREATE_NEW_JOB")
                    return self.get(job_id)
                self._transition_locked(job_id, "running", progress=5)
            result = adapter(spec.descriptor, context)
            status, summary, reason_code, action_code = self._result_summary(result)
            with self._lock:
                live = self.store.get(job_id)
                if context.cancelled or (live is not None and live.get("status") == "cancelling"):
                    self._transition_locked(
                        job_id,
                        "interrupted",
                        progress=0,
                        reason_code="CANCELLED",
                        action_code="RETRY_IF_RECONSTRUCTABLE" if spec.descriptor.reconstructable else "CREATE_NEW_JOB",
                        retry_available=spec.descriptor.reconstructable and self.registry.contains(spec.descriptor.adapter_id),
                    )
                elif status == "completed":
                    self._transition_locked(job_id, "completed", progress=100, result_summary=summary, reason_code=reason_code, action_code=action_code)
                elif status == "unavailable":
                    self._transition_locked(
                        job_id,
                        "unavailable",
                        progress=0,
                        result_summary=summary,
                        reason_code=reason_code or "ADAPTER_UNAVAILABLE",
                        action_code=action_code or "CHECK_SERVER_ADAPTER",
                        retry_available=spec.descriptor.reconstructable and self.registry.contains(spec.descriptor.adapter_id),
                    )
                else:
                    self._transition_locked(
                        job_id,
                        "failed",
                        progress=0,
                        result_summary=summary,
                        reason_code=reason_code or "ADAPTER_FAILED",
                        action_code=action_code or "RETRY_IF_RECONSTRUCTABLE",
                        retry_available=spec.descriptor.reconstructable and self.registry.contains(spec.descriptor.adapter_id),
                    )
        except DurableStoreHealthError:
            raise
        except Exception:  # Never serialize an adapter exception or local path.
            with self._lock:
                live = self.store.get(job_id)
                if context is not None and (context.cancelled or (live is not None and live.get("status") == "cancelling")):
                    self._transition_locked(
                        job_id,
                        "interrupted",
                        progress=0,
                        reason_code="CANCELLED",
                        action_code="RETRY_IF_RECONSTRUCTABLE" if spec is not None and spec.descriptor.reconstructable else "CREATE_NEW_JOB",
                        retry_available=bool(spec is not None and spec.descriptor.reconstructable and self.registry.contains(spec.descriptor.adapter_id)),
                    )
                else:
                    self._transition_locked(
                        job_id,
                        "failed",
                        progress=0,
                        reason_code="ADAPTER_FAILED",
                        action_code="CHECK_SERVER_ADAPTER",
                        retry_available=bool(spec is not None and spec.descriptor.reconstructable and self.registry.contains(spec.descriptor.adapter_id)),
                    )
        finally:
            with self._lock:
                self._contexts.pop(job_id, None)
                self._threads.pop(job_id, None)
                self._release_resources_locked(job_id)
        return self.get(job_id)

    def cancel(self, job_id: str) -> tuple[bool, dict[str, Any] | None]:
        """Request cooperative cancellation without regressing terminal state."""

        try:
            with self._lock:
                record = self.store.get(job_id)
                if record is None or str(record.get("status")) in TERMINAL_JOB_STATES:
                    return False, self.public_job(record) if record is not None else None
                context = self._contexts.get(job_id)
                if context is not None:
                    context.cancel()
                transitioned = self._transition_locked(job_id, "cancelling", reason_code="CANCEL_REQUESTED", action_code="WAIT_FOR_STOP")
                if transitioned is None:
                    return False, self.get(job_id)
                if context is None:
                    self._transition_locked(
                        job_id,
                        "interrupted",
                        reason_code="CANCELLED",
                        action_code="RETRY_IF_RECONSTRUCTABLE" if self._can_retry(record) else "CREATE_NEW_JOB",
                        retry_available=self._can_retry(record),
                    )
                return True, self.get(job_id)
        except DurableStoreHealthError:
            return False, self._store_unavailable(job_id)

    def retry(self, job_id: str, *, start: bool = False) -> dict[str, Any]:
        """Create a new attempt only when a persisted descriptor is reconstructable."""

        record = self.store.get(job_id)
        if record is None or str(record.get("status")) not in RETRYABLE_JOB_STATES:
            raise JobContractError("JOB_NOT_RETRYABLE", "Create a new allowlisted job descriptor.")
        try:
            spec = validate_job_spec(record.get("job_spec"))
        except JobContractError as exc:
            raise JobContractError("INVALID_PERSISTED_DESCRIPTOR", "Create a new allowlisted job descriptor.") from exc
        if not spec.descriptor.reconstructable or not self.registry.contains(spec.descriptor.adapter_id):
            raise JobContractError("JOB_NOT_RECONSTRUCTABLE", "Create a new allowlisted job descriptor.")
        attempt = int(record.get("attempt") or 1) + 1
        return self.submit(spec.to_mapping(), start=start, retry_of=job_id, attempt=attempt)

    resume = retry

    def reconcile_startup(self) -> dict[str, int]:
        """Mark pre-crash active work interrupted; never auto-resume it."""

        reconciled = 0
        unavailable = 0
        for record in self.store.records():
            job_id = record.get("id")
            if not isinstance(job_id, str):
                continue
            try:
                spec = validate_job_spec(record.get("job_spec"))
            except JobContractError:
                with self._lock:
                    current = self.store.get(job_id)
                    if current is not None and current.get("status") not in TERMINAL_JOB_STATES:
                        self._transition_locked(job_id, "unavailable", reason_code="INVALID_PERSISTED_DESCRIPTOR", action_code="CREATE_NEW_JOB")
                    elif current is not None:
                        self._update_locked(job_id, {"retry_available": False})
                unavailable += 1
                continue
            retry_available = spec.descriptor.reconstructable and self.registry.contains(spec.descriptor.adapter_id)
            with self._lock:
                current = self.store.get(job_id)
                if current is None:
                    continue
                if str(current.get("status")) in {"queued", "starting", "running", "cancelling"}:
                    self._transition_locked(
                        job_id,
                        "interrupted",
                        progress=0,
                        reason_code="RESTART_INTERRUPTED",
                        action_code="RETRY_IF_RECONSTRUCTABLE" if retry_available else "CREATE_NEW_JOB",
                        retry_available=retry_available,
                    )
                    reconciled += 1
                else:
                    self._update_locked(job_id, {"retry_available": retry_available})
        self.store.flush()
        return {"interrupted": reconciled, "unavailable": unavailable}

    def persist_output(
        self,
        job_id: str,
        content: bytes,
        *,
        name: str = "result.bin",
        media_type: str | None = None,
        disk_safety_bytes: int = 0,
    ) -> dict[str, Any] | None:
        """Atomically create a Hub-owned artifact with opaque job provenance."""

        with self._lock:
            record = self.store.get(job_id)
            if record is None:
                return None
            provenance = {
                "job_id": job_id,
                "job_spec_fingerprint": record.get("job_spec_fingerprint"),
                "adapter_id": (record.get("descriptor_summary") or {}).get("adapter_id"),
                "attempt": record.get("attempt"),
                "status": record.get("status"),
            }
        artifact = atomic_write_job_output(
            job_id,
            content,
            name=name,
            media_type=media_type,
            provenance=provenance,
            disk_safety_bytes=disk_safety_bytes,
        )
        with self._lock:
            current = self.store.get(job_id)
            if current is None:
                return None
            existing = current.get("artifacts") if isinstance(current.get("artifacts"), list) else []
            artifact_ids = [item for item in existing if is_artifact_id(item)]
            artifact_id = artifact.get("id")
            if is_artifact_id(artifact_id) and artifact_id not in artifact_ids:
                artifact_ids.append(artifact_id)
            self._update_locked(job_id, {"artifacts": artifact_ids})
        return detached_json(artifact)

    def get(self, job_id: str) -> dict[str, Any] | None:
        try:
            record = self.store.get(job_id)
        except DurableStoreHealthError:
            return self._store_unavailable(job_id)
        return self.public_job(record) if record is not None else None

    @classmethod
    def public_job(cls, record: dict[str, Any]) -> dict[str, Any]:
        """Project opaque summaries only; private descriptors never cross it."""

        allowed = {
            "id",
            "contract_version",
            "status",
            "created_at",
            "updated_at",
            "started_at",
            "finished_at",
            "progress",
            "execution",
            "dry_run",
            "job_spec_fingerprint",
            "descriptor_summary",
            "resource_plan",
            "attempt",
            "retry_of",
            "retry_available",
            "reason_code",
            "action_code",
            "result_summary",
            "artifacts",
        }
        result = {key: detached_json(value) for key, value in record.items() if key in allowed and value is not None}
        result["execution"] = "not_run"
        result["dry_run"] = True
        result["capability_status"] = cls._public_status(record)
        if str(record.get("status")) not in RETRYABLE_JOB_STATES:
            result["retry_available"] = False
        return result

    def wait_for_idle(self, timeout_seconds: float = 1.0) -> bool:
        """Boundedly join coordinator threads created by this engine only."""

        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        while True:
            with self._lock:
                threads = list(self._threads.values())
            if not threads:
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            for thread in threads:
                thread.join(timeout=min(0.05, remaining))

    def close(self) -> bool:
        """Flush owned state; this never terminates external processes."""

        idle = self.wait_for_idle(1.0)
        self.store.close()
        return idle
