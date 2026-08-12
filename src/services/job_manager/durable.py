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
from src.services import artifact_store
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
_JOB_ID = re.compile(r"jobv5_[a-f0-9]{32}")
_FINGERPRINT = re.compile(r"[a-f0-9]{64}")
_SAFE_REASON_CODES = frozenset({
    "GPU_SLOT_UNAVAILABLE", "RUNNER_STATE_LIMIT", "CONCURRENCY_LIMIT", "GPU_SLOT_BUSY",
    "EXCLUSIVE_GROUP_BUSY", "CANCEL_REQUESTED", "CANCELLED", "INVALID_ADAPTER_RESULT",
    "ADAPTER_UNAVAILABLE", "INVALID_PERSISTED_DESCRIPTOR", "RESTART_INTERRUPTED", "ADAPTER_FAILED",
})
_SAFE_ACTION_CODES = frozenset({
    "CONFIGURE_GPU_SLOT", "RETRY_LATER", "WAIT_FOR_STOP", "RETRY_IF_RECONSTRUCTABLE",
    "CREATE_NEW_JOB", "CHECK_SERVER_ADAPTER",
})
_RESULT_STATUSES = frozenset({"completed", "failed", "unavailable"})
_RECOVERY_ACTION_CREATE = "CREATE_NEW_JOB"
_RECOVERY_ACTION_RETRY = "RETRY_IF_RECONSTRUCTABLE"
_RECOVERY_STATUS_AVAILABLE = "available"
_RECOVERY_STATUS_UNAVAILABLE = "unavailable"
RECOVERY_REASON_INVALID = "The persisted job descriptor is not safely reconstructable."
RECOVERY_REASON_ACTIVE = "The job is active or terminal; automatic recovery is unavailable."
RECOVERY_REASON_NOT_RECONSTRUCTABLE = "The persisted descriptor is not marked reconstructable."
RECOVERY_REASON_ADAPTER_UNAVAILABLE = "No current server-owned adapter is registered for this job."
RECOVERY_REASON_AVAILABLE = "A current server-owned adapter can reconstruct this job."
RECOVERY_NEXT_CREATE = "Create a new allowlisted job descriptor."
RECOVERY_NEXT_RETRY = "An explicit server-owned retry may be queued without starting runtime work."
_SAFE_ARTIFACT_MEDIA = re.compile(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+")
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


def _recovery_unavailable(reason: str = RECOVERY_REASON_INVALID) -> dict[str, Any]:
    """Return the fixed fail-closed recovery contract."""

    return {
        "status": _RECOVERY_STATUS_UNAVAILABLE,
        "action": _RECOVERY_ACTION_CREATE,
        "action_available": False,
        "reason": reason,
        "next_action": RECOVERY_NEXT_CREATE,
    }


def public_recovery_decision(record: object, registry: "ServerOwnedAdapterRegistry") -> dict[str, Any]:
    """Decide recovery from validated durable data and server-owned code only.

    Persisted retry flags, reason/action codes, and free-form values are never
    consulted. A positive decision only permits a new queued attempt; it never
    means runtime has started.
    """

    if not isinstance(record, dict) or not isinstance(registry, ServerOwnedAdapterRegistry):
        return _recovery_unavailable()
    if not _JOB_ID.fullmatch(str(record.get("id") or "")):
        return _recovery_unavailable()
    if record.get("contract_version") != JOB_RECORD_VERSION:
        return _recovery_unavailable()
    status = record.get("status")
    if not isinstance(status, str) or status not in JOB_STATES:
        return _recovery_unavailable()
    try:
        spec = validate_job_spec(record.get("job_spec"))
    except JobContractError:
        return _recovery_unavailable(RECOVERY_REASON_INVALID)
    if record.get("job_spec_fingerprint") != spec.fingerprint:
        return _recovery_unavailable(RECOVERY_REASON_INVALID)
    if record.get("descriptor_summary") != spec.descriptor.summary():
        return _recovery_unavailable(RECOVERY_REASON_INVALID)
    attempt = record.get("attempt")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or not 1 <= attempt <= 10_000:
        return _recovery_unavailable(RECOVERY_REASON_INVALID)
    retry_of = record.get("retry_of")
    if retry_of is not None and (not isinstance(retry_of, str) or not _JOB_ID.fullmatch(retry_of)):
        return _recovery_unavailable(RECOVERY_REASON_INVALID)
    if status not in RETRYABLE_JOB_STATES:
        return _recovery_unavailable(RECOVERY_REASON_ACTIVE)
    if spec.descriptor.reconstructable is not True:
        return _recovery_unavailable(RECOVERY_REASON_NOT_RECONSTRUCTABLE)
    if not registry.contains(spec.descriptor.adapter_id):
        return _recovery_unavailable(RECOVERY_REASON_ADAPTER_UNAVAILABLE)
    return {
        "status": _RECOVERY_STATUS_AVAILABLE,
        "action": _RECOVERY_ACTION_RETRY,
        "action_available": True,
        "reason": RECOVERY_REASON_AVAILABLE,
        "next_action": RECOVERY_NEXT_RETRY,
    }


def _safe_timestamp(value: object) -> str | None:
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.isoformat()


def public_lifecycle(record: object) -> dict[str, Any]:
    """Project bounded state history without allowing terminal regression."""

    source = record if isinstance(record, dict) else {}
    raw_current = source.get("status")
    current = raw_current if isinstance(raw_current, str) and raw_current in JOB_STATES else "unavailable"
    raw_history = source.get("state_history")
    history = [item for item in raw_history if isinstance(item, str) and item in JOB_STATES] if isinstance(raw_history, list) else []
    history = history[:MAX_STATE_HISTORY]
    terminal_index = next((index for index, item in enumerate(history) if item in TERMINAL_JOB_STATES), None)
    if terminal_index is not None:
        history = history[: terminal_index + 1]
        current = history[-1]
    elif current in TERMINAL_JOB_STATES:
        history.append(current)
    elif not history or history[-1] != current:
        history.append(current)
    history = history[-MAX_STATE_HISTORY:]
    timestamps = {
        key: _safe_timestamp(source.get(key))
        for key in ("created_at", "updated_at", "started_at", "finished_at")
    }
    attempt = source.get("attempt")
    safe_attempt = attempt if isinstance(attempt, int) and not isinstance(attempt, bool) and 1 <= attempt <= 10_000 else 1
    retry_of = source.get("retry_of")
    safe_retry_of = retry_of if isinstance(retry_of, str) and _JOB_ID.fullmatch(retry_of) else None
    return {
        "state": current,
        "history": history,
        "attempt": safe_attempt,
        "retry_of": safe_retry_of,
        "timestamps": timestamps,
    }


def _artifact_unavailable(artifact_id: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "unavailable",
        "preview_available": False,
        "reason": "Artifact metadata is unavailable or its durable provenance does not match.",
        "next_action": "Refresh the durable job after server-owned artifact validation.",
    }
    if artifact_id is not None and is_artifact_id(artifact_id):
        result["id"] = artifact_id
    return result


def _safe_artifact_metadata(value: object, record: dict[str, Any], artifact_id: str) -> dict[str, Any] | None:
    if not isinstance(value, dict) or value.get("id") != artifact_id:
        return None
    name = value.get("name")
    if (
        not isinstance(name, str)
        or not 1 <= len(name) <= 160
        or any(char in name for char in ("\x00", "\r", "\n", "/", "\\"))
        or ".." in name
    ):
        return None
    size = value.get("size_bytes")
    media_type = value.get("media_type")
    digest = value.get("sha256")
    if isinstance(size, bool) or not isinstance(size, int) or not 0 <= size <= 8 * 1024**3:
        return None
    if not isinstance(media_type, str) or not 1 <= len(media_type) <= 128 or not _SAFE_ARTIFACT_MEDIA.fullmatch(media_type):
        return None
    if not isinstance(digest, str) or not _FINGERPRINT.fullmatch(digest):
        return None
    if value.get("url") != f"/api/artifacts/{artifact_id}":
        return None
    provenance = value.get("provenance")
    if not isinstance(provenance, dict) or set(provenance) != {"job_id", "job_spec_fingerprint", "adapter_id", "attempt", "status"}:
        return None
    expected_adapter = (record.get("descriptor_summary") or {}).get("adapter_id")
    expected = {
        "job_id": record.get("id"),
        "job_spec_fingerprint": record.get("job_spec_fingerprint"),
        "adapter_id": expected_adapter,
        "attempt": record.get("attempt"),
        "status": record.get("status"),
    }
    if provenance != expected:
        return None
    if not _JOB_ID.fullmatch(str(expected["job_id"] or "")) or not _FINGERPRINT.fullmatch(str(expected["job_spec_fingerprint"] or "")):
        return None
    if not isinstance(expected_adapter, str) or not _ADAPTER_ID.fullmatch(expected_adapter):
        return None
    if isinstance(expected["attempt"], bool) or not isinstance(expected["attempt"], int) or not 1 <= expected["attempt"] <= 10_000:
        return None
    if not isinstance(expected["status"], str) or expected["status"] not in {"queued", "starting", "running", "cancelling", "completed", "failed", "unavailable", "interrupted"}:
        return None
    return {
        "id": artifact_id,
        "name": name,
        "media_type": media_type,
        "size_bytes": size,
        "sha256": digest,
        "url": f"/api/artifacts/{artifact_id}",
        "status": "available",
        "preview_available": True,
        "provenance": dict(expected),
    }


def public_durable_artifacts(record: object) -> list[dict[str, Any]]:
    """Resolve only opaque artifact IDs and validate durable lineage."""

    if not isinstance(record, dict):
        return []
    values = record.get("artifacts") if isinstance(record.get("artifacts"), list) else []
    try:
        spec = validate_job_spec(record.get("job_spec"))
    except JobContractError:
        return [_artifact_unavailable() for _ in values[:64]]
    if record.get("job_spec_fingerprint") != spec.fingerprint or record.get("descriptor_summary") != spec.descriptor.summary():
        return [_artifact_unavailable() for _ in values[:64]]
    result: list[dict[str, Any]] = []
    for value in values[:64]:
        if not is_artifact_id(value):
            result.append(_artifact_unavailable())
            continue
        try:
            resolved = artifact_store.resolve(value)
            described = artifact_store.describe(value) if resolved is not None else None
        except Exception:
            resolved = None
            described = None
        if resolved is None:
            result.append(_artifact_unavailable(value))
            continue
        safe = _safe_artifact_metadata(described, record, value)
        result.append(safe if safe is not None else _artifact_unavailable(value))
    return result


def _public_result_summary(value: object) -> dict[str, Any] | None:
    """Keep adapter summaries bounded and free of persisted free-form data."""

    if not isinstance(value, dict) or set(value) - {"count", "artifact_ids", "result_type"}:
        return None
    result: dict[str, Any] = {}
    count = value.get("count")
    if count is not None:
        if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= 1_000_000:
            return None
        result["count"] = count
    artifact_ids = value.get("artifact_ids")
    if artifact_ids is not None:
        if type(artifact_ids) is not list or len(artifact_ids) > 64:
            return None
        result["artifact_ids"] = [item for item in artifact_ids if is_artifact_id(item)]
    result_type = value.get("result_type")
    if result_type is not None:
        if not isinstance(result_type, str) or not _ADAPTER_ID.fullmatch(result_type):
            return None
        result["result_type"] = result_type
    return result


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
        return self.public_job(stored, registry=self.registry)

    def start(self, job_id: str) -> dict[str, Any] | None:
        """Start a daemon coordinator thread only for a trusted, queued job."""

        try:
            with self._lock:
                record = self.store.get(job_id)
                if record is None or record.get("status") != "queued" or job_id in self._threads:
                    return self.public_job(record, registry=self.registry) if record is not None else None
                if len(self._threads) >= self.max_runner_states:
                    self._update_locked(job_id, {"reason_code": "RUNNER_STATE_LIMIT", "action_code": "RETRY_LATER"})
                    return self.public_job(self.store.get(job_id) or record, registry=self.registry)
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
                return self.public_job(record, registry=self.registry)
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
                    return False, self.public_job(record, registry=self.registry) if record is not None else None
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
        return self.public_job(record, registry=self.registry) if record is not None else None

    @classmethod
    def public_job(
        cls,
        record: dict[str, Any],
        *,
        registry: "ServerOwnedAdapterRegistry | None" = None,
    ) -> dict[str, Any]:
        """Project a durable record without exposing private or stale fields."""

        source = record if isinstance(record, dict) else {}
        active_registry = registry if isinstance(registry, ServerOwnedAdapterRegistry) else ServerOwnedAdapterRegistry()
        decision = public_recovery_decision(source, active_registry)
        lifecycle = public_lifecycle(source)
        result: dict[str, Any] = {}
        job_id = source.get("id")
        if isinstance(job_id, str) and _JOB_ID.fullmatch(job_id):
            result["id"] = job_id
        if source.get("contract_version") == JOB_RECORD_VERSION:
            result["contract_version"] = JOB_RECORD_VERSION
        status = lifecycle["state"]
        result["status"] = status
        for key, value in lifecycle["timestamps"].items():
            if value is not None:
                result[key] = value
        progress = source.get("progress")
        result["progress"] = max(0, min(100, progress)) if isinstance(progress, int) and not isinstance(progress, bool) else 0
        try:
            spec = validate_job_spec(source.get("job_spec"))
        except JobContractError:
            spec = None
        if spec is not None and source.get("job_spec_fingerprint") == spec.fingerprint:
            result["tool"] = spec.tool
            result["job_spec_fingerprint"] = spec.fingerprint
            result["descriptor_summary"] = detached_json(spec.descriptor.summary())
            result["resource_plan"] = cls._resource_plan(spec)
        result["attempt"] = lifecycle["attempt"]
        if lifecycle["retry_of"] is not None:
            result["retry_of"] = lifecycle["retry_of"]
        result["retry_available"] = decision["action_available"] if status in RETRYABLE_JOB_STATES else False
        for key, safe_values in (("reason_code", _SAFE_REASON_CODES), ("action_code", _SAFE_ACTION_CODES)):
            value = source.get(key)
            if isinstance(value, str) and value in safe_values:
                result[key] = value
        summary = _public_result_summary(source.get("result_summary"))
        if summary is not None:
            result["result_summary"] = summary
        result["artifacts"] = public_durable_artifacts(source)
        result["lifecycle"] = lifecycle
        result["recovery"] = decision
        result["execution"] = "not_run"
        result["dry_run"] = True
        result["capability_status"] = "unavailable" if status == "unavailable" else "partial"
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
