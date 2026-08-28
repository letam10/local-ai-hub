"""Server-owned resource scheduling with no hardware probe or workload start.

The scheduler works from a bounded hardware snapshot supplied by server code.
It is intentionally a pure in-process coordinator in Phase 4: it can reserve
declared capacity and enforce identity/state rules, but it cannot launch a
worker, query a GPU, or dispatch a provider.  Phase 5 will bind durable jobs
to these reservations only after its execution-state contract is complete.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from functools import wraps
import re
import secrets
import threading
import time
from typing import Any


RESOURCE_SCHEDULER_SCHEMA_VERSION = "resource-scheduler.v3"
RESOURCE_SCHEDULER_STATES = (
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
_STATE_SET = frozenset(RESOURCE_SCHEDULER_STATES)
_ACTIVE_STATES = frozenset({"PREPARING", "RUNNING", "PAUSED", "CANCELLING"})
_TERMINAL_STATES = frozenset({"CANCELLED", "SUCCEEDED", "FAILED"})
_JOB_ID = re.compile(r"^(?:jobv5_[a-f0-9]{32}|jobv2_[a-f0-9]{32}|job_[0-9]{8}_[0-9]{6}_[a-f0-9]{8})$")
_WORKER_ID = re.compile(r"^[a-z][a-z0-9._:-]{1,95}$")
_PROFILE_ID = re.compile(r"^[a-z][a-z0-9._-]{1,63}$")
_GPU_ID = re.compile(r"^[a-z][a-z0-9._:-]{1,63}$")
_RESERVATION_ID = re.compile(r"^resv_[a-f0-9]{32}$")
_LEASE_ID = re.compile(r"^lease_[a-f0-9]{32}$")
_SLOT_ID = re.compile(r"^[a-z][a-z0-9._-]{1,63}$")
_MAX_INT = (1 << 31) - 1
_RESERVATION_TTL_SECONDS = 120
_LEASE_TTL_SECONDS = 90
_HEAVY_VRAM_MB = 2048
_MAX_JOBS = 512
_MAX_TERMINAL_JOBS = 128
_DEFAULT_GPU_SAFETY_MARGIN_MB = 512


class ResourceSchedulerError(ValueError):
    """Fixed error when a scheduler contract is malformed."""


def _synchronized(method):
    """Serialize all process-local scheduler state transitions."""

    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapped


def _now() -> float:
    return time.monotonic()


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp_after(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _bounded_int(value: object, *, minimum: int = 0, maximum: int = _MAX_INT) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= maximum else None


def _safe_id(value: object, pattern: re.Pattern[str]) -> str | None:
    return value if isinstance(value, str) and pattern.fullmatch(value) else None


def _copy_profile(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "profile_id": value["profile_id"],
        "estimated_vram_mb": value["estimated_vram_mb"],
        "estimated_ram_mb": value["estimated_ram_mb"],
        "gpu_required": value["gpu_required"],
        "cpu_fallback": value["cpu_fallback"],
        "exclusive": value["exclusive"],
        "priority": value["priority"],
        "interruptible": value["interruptible"],
        "batchable": value["batchable"],
        "cpu_slots": value["cpu_slots"],
        "disk_mb": value["disk_mb"],
        "runtime_slot": value["runtime_slot"],
        "provider_slot": value["provider_slot"],
    }


def _profile(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    allowed = {
        "profile_id", "estimated_vram_mb", "estimated_ram_mb", "gpu_required", "cpu_fallback",
        "exclusive", "priority", "interruptible", "batchable", "cpu_slots", "disk_mb",
        "runtime_slot", "provider_slot",
    }
    if set(value) != allowed:
        return None
    profile_id = _safe_id(value.get("profile_id"), _PROFILE_ID)
    runtime_slot = value.get("runtime_slot")
    provider_slot = value.get("provider_slot")
    numbers = {
        "estimated_vram_mb": _bounded_int(value.get("estimated_vram_mb")),
        "estimated_ram_mb": _bounded_int(value.get("estimated_ram_mb")),
        "priority": _bounded_int(value.get("priority"), maximum=100),
        "cpu_slots": _bounded_int(value.get("cpu_slots"), minimum=1, maximum=64),
        "disk_mb": _bounded_int(value.get("disk_mb")),
    }
    if (
        profile_id is None
        or any(item is None for item in numbers.values())
        or not all(type(value.get(name)) is bool for name in ("gpu_required", "cpu_fallback", "exclusive", "interruptible", "batchable"))
        or (runtime_slot is not None and _safe_id(runtime_slot, _SLOT_ID) is None)
        or (provider_slot is not None and _safe_id(provider_slot, _SLOT_ID) is None)
        or (value["gpu_required"] is False and numbers["estimated_vram_mb"] != 0)
        or (value["gpu_required"] is True and numbers["estimated_vram_mb"] < 1)
    ):
        return None
    return {
        "profile_id": profile_id,
        **{key: int(item) for key, item in numbers.items()},
        "gpu_required": value["gpu_required"],
        "cpu_fallback": value["cpu_fallback"],
        "exclusive": value["exclusive"],
        "interruptible": value["interruptible"],
        "batchable": value["batchable"],
        "runtime_slot": runtime_slot,
        "provider_slot": provider_slot,
    }


def server_owned_resource_profiles() -> dict[str, dict[str, Any]]:
    """Return a small fixed set of code-owned scheduler profiles.

    These are capacity declarations only. They are deliberately not provider
    launch configurations and cannot make a tool operational.
    """

    profiles = [
        {"profile_id": "cpu_light", "estimated_vram_mb": 0, "estimated_ram_mb": 512, "gpu_required": False, "cpu_fallback": True, "exclusive": False, "priority": 50, "interruptible": True, "batchable": True, "cpu_slots": 1, "disk_mb": 128, "runtime_slot": None, "provider_slot": None},
        {"profile_id": "ffmpeg_probe", "estimated_vram_mb": 0, "estimated_ram_mb": 256, "gpu_required": False, "cpu_fallback": True, "exclusive": False, "priority": 60, "interruptible": True, "batchable": True, "cpu_slots": 1, "disk_mb": 64, "runtime_slot": "ffmpeg", "provider_slot": None},
        {"profile_id": "vision_gpu_2gb", "estimated_vram_mb": 2048, "estimated_ram_mb": 2048, "gpu_required": True, "cpu_fallback": False, "exclusive": True, "priority": 70, "interruptible": False, "batchable": False, "cpu_slots": 2, "disk_mb": 256, "runtime_slot": "vision", "provider_slot": "vision"},
        {"profile_id": "whisper_gpu_2gb", "estimated_vram_mb": 2048, "estimated_ram_mb": 2048, "gpu_required": True, "cpu_fallback": False, "exclusive": True, "priority": 70, "interruptible": False, "batchable": False, "cpu_slots": 2, "disk_mb": 256, "runtime_slot": "faster-whisper", "provider_slot": "whisper"},
        {"profile_id": "video_gpu_4gb", "estimated_vram_mb": 4096, "estimated_ram_mb": 4096, "gpu_required": True, "cpu_fallback": False, "exclusive": True, "priority": 65, "interruptible": False, "batchable": False, "cpu_slots": 2, "disk_mb": 1024, "runtime_slot": "animesr", "provider_slot": "video"},
    ]
    result: dict[str, dict[str, Any]] = {}
    for raw in profiles:
        normalized = _profile(raw)
        if normalized is None:
            raise RuntimeError("resource_profile_contract_invalid")
        result[normalized["profile_id"]] = normalized
    return result


def _inventory(value: object) -> dict[str, Any]:
    """Normalize a bounded server-owned hardware snapshot, never probe it.

    ``free_vram_mb`` is intentionally optional because it is collected by a
    separately-owned bounded host snapshotter.  When it is present, admission
    uses it conservatively together with Hub reservations and a policy margin;
    no browser supplied estimate or live GPU probe participates here.
    """

    if value is None:
        return {
            "status": "unknown",
            "cpu_slots": None,
            "ram_mb": None,
            "disk_mb": None,
            "gpus": [],
            "runtime_slots": {},
            "provider_slots": {},
            "gpu_safety_margin_mb": _DEFAULT_GPU_SAFETY_MARGIN_MB,
            "observed_at": None,
            "source_fingerprint": None,
        }
    if not isinstance(value, Mapping) or set(value) - {
        "cpu_slots", "cpu_cores", "ram_mb", "disk_mb", "gpus", "runtime_slots", "provider_slots",
        "gpu_safety_margin_mb", "observed_at", "source_fingerprint",
    }:
        raise ResourceSchedulerError("resource_inventory_invalid")
    cpu_slots = value.get("cpu_slots", value.get("cpu_cores"))
    normalized_cpu = _bounded_int(cpu_slots, minimum=1, maximum=256) if cpu_slots is not None else None
    ram_mb = _bounded_int(value.get("ram_mb")) if value.get("ram_mb") is not None else None
    disk_mb = _bounded_int(value.get("disk_mb")) if value.get("disk_mb") is not None else None
    raw_gpus = value.get("gpus", [])
    if not isinstance(raw_gpus, list) or len(raw_gpus) > 16:
        raise ResourceSchedulerError("resource_inventory_invalid")
    gpus: list[dict[str, Any]] = []
    for raw in raw_gpus:
        if not isinstance(raw, Mapping) or set(raw) - {
            "id", "vendor", "device_class", "model", "vram_mb", "free_vram_mb", "observed_at", "source_fingerprint",
        }:
            raise ResourceSchedulerError("resource_inventory_invalid")
        gpu_id = _safe_id(raw.get("id"), _GPU_ID)
        vram = _bounded_int(raw.get("vram_mb"))
        free_vram = _bounded_int(raw.get("free_vram_mb")) if raw.get("free_vram_mb") is not None else None
        vendor = raw.get("vendor")
        device_class = raw.get("device_class")
        model = raw.get("model", "unknown")
        if (
            gpu_id is None or vram is None or (free_vram is not None and free_vram > vram)
            or vendor not in {"nvidia", "amd", "intel"} or device_class not in {"discrete", "integrated"}
            or not isinstance(model, str) or not 1 <= len(model) <= 96
            or (raw.get("observed_at") is not None and (not isinstance(raw.get("observed_at"), str) or not 1 <= len(str(raw.get("observed_at"))) <= 64))
            or (raw.get("source_fingerprint") is not None and (not isinstance(raw.get("source_fingerprint"), str) or not 1 <= len(str(raw.get("source_fingerprint"))) <= 128))
        ):
            raise ResourceSchedulerError("resource_inventory_invalid")
        gpus.append({
            "id": gpu_id,
            "vendor": vendor,
            "device_class": device_class,
            "model": model,
            "vram_mb": vram,
            "free_vram_mb": free_vram,
            "observed_at": raw.get("observed_at"),
            "source_fingerprint": raw.get("source_fingerprint"),
        })
    if len({item["id"] for item in gpus}) != len(gpus):
        raise ResourceSchedulerError("resource_inventory_invalid")

    def slots(name: str) -> dict[str, int]:
        raw = value.get(name, {})
        if not isinstance(raw, Mapping) or len(raw) > 64:
            raise ResourceSchedulerError("resource_inventory_invalid")
        result: dict[str, int] = {}
        for key, amount in raw.items():
            if _safe_id(key, _SLOT_ID) is None or _bounded_int(amount, minimum=1, maximum=64) is None:
                raise ResourceSchedulerError("resource_inventory_invalid")
            result[key] = int(amount)
        return result

    margin = _bounded_int(value.get("gpu_safety_margin_mb", _DEFAULT_GPU_SAFETY_MARGIN_MB), minimum=0, maximum=32768)
    if margin is None or (value.get("observed_at") is not None and (not isinstance(value.get("observed_at"), str) or not 1 <= len(str(value.get("observed_at"))) <= 64)) or (value.get("source_fingerprint") is not None and (not isinstance(value.get("source_fingerprint"), str) or not 1 <= len(str(value.get("source_fingerprint"))) <= 128)):
        raise ResourceSchedulerError("resource_inventory_invalid")
    return {
        "status": "available",
        "cpu_slots": normalized_cpu,
        "ram_mb": ram_mb,
        "disk_mb": disk_mb,
        "gpus": gpus,
        "runtime_slots": slots("runtime_slots"),
        "provider_slots": slots("provider_slots"),
        "gpu_safety_margin_mb": int(margin),
        "observed_at": value.get("observed_at"),
        "source_fingerprint": value.get("source_fingerprint"),
    }


class ResourceScheduler:
    """Finite resource reservation state machine with exact identity binding."""

    def __init__(
        self,
        *,
        hardware_snapshot: Mapping[str, Any] | None = None,
        profiles: Mapping[str, Mapping[str, Any]] | None = None,
        max_heavy_gpu_jobs: int = 1,
        reservation_ttl_seconds: int = _RESERVATION_TTL_SECONDS,
        lease_ttl_seconds: int = _LEASE_TTL_SECONDS,
    ) -> None:
        if (
            _bounded_int(max_heavy_gpu_jobs, minimum=1, maximum=8) is None
            or _bounded_int(reservation_ttl_seconds, minimum=10, maximum=3600) is None
            or _bounded_int(lease_ttl_seconds, minimum=10, maximum=3600) is None
        ):
            raise ResourceSchedulerError("scheduler_config_invalid")
        self._inventory = _inventory(hardware_snapshot)
        raw_profiles = profiles if profiles is not None else server_owned_resource_profiles()
        normalized_profiles: dict[str, dict[str, Any]] = {}
        for key, raw in raw_profiles.items():
            if _safe_id(key, _PROFILE_ID) is None:
                raise ResourceSchedulerError("resource_profile_invalid")
            profile = _profile(raw)
            if profile is None or profile["profile_id"] != key:
                raise ResourceSchedulerError("resource_profile_invalid")
            normalized_profiles[key] = profile
        if not normalized_profiles:
            raise ResourceSchedulerError("resource_profile_invalid")
        self._profiles = normalized_profiles
        self._max_heavy_gpu_jobs = int(max_heavy_gpu_jobs)
        self._reservation_ttl = int(reservation_ttl_seconds)
        self._lease_ttl = int(lease_ttl_seconds)
        self._jobs: dict[str, dict[str, Any]] = {}
        self._reservations: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._epoch = 0

    @property
    def profiles(self) -> dict[str, dict[str, Any]]:
        return {key: _copy_profile(value) for key, value in self._profiles.items()}

    @contextmanager
    def saga(self):
        """Hold the process-local coordinator across one durable saga.

        This is an internal server contract. It lets the durable engine hold
        the scheduler revision/epoch stable from checkpoint through SQLite
        CAS or compensation. Browser routes do not receive this object.
        """

        with self._lock:
            yield

    def _job(self, job_id: object) -> dict[str, Any] | None:
        identifier = _safe_id(job_id, _JOB_ID)
        value = self._jobs.get(identifier) if identifier else None
        return deepcopy(value) if value is not None else None

    def _reservation(self, reservation_id: object) -> dict[str, Any] | None:
        identifier = _safe_id(reservation_id, _RESERVATION_ID)
        value = self._reservations.get(identifier) if identifier else None
        return deepcopy(value) if value is not None else None

    def _touch(self, job: dict[str, Any], **changes: Any) -> None:
        """Apply one scheduler transition and advance its local revision."""

        current = job.get("revision")
        job.update(changes)
        job["revision"] = int(current) + 1 if isinstance(current, int) and not isinstance(current, bool) and current >= 1 else 1
        job["updated_at"] = _timestamp()
        self._epoch += 1

    @_synchronized
    def checkpoint(self, job_id: object) -> dict[str, Any] | None:
        """Return an internal compensation checkpoint for one exact job.

        The token contains only process-local scheduler metadata and is never
        projected to the browser. It is consumed by the durable saga if the
        subsequent SQLite CAS fails.
        """

        identifier = _safe_id(job_id, _JOB_ID)
        job = self._jobs.get(identifier or "")
        if job is None:
            return None
        reservation_id = job.get("reservation_id")
        reservation = self._reservations.get(reservation_id) if isinstance(reservation_id, str) else None
        return {
            "job_id": str(job["job_id"]),
            "epoch": self._epoch,
            "job": deepcopy(job),
            "reservation": deepcopy(reservation) if reservation is not None else None,
            "jobs": deepcopy(self._jobs),
            "reservations": deepcopy(self._reservations),
        }

    @_synchronized
    def restore_checkpoint(self, checkpoint: object, *, expected_revision: object, expected_epoch: object | None = None) -> bool:
        """Restore exactly one scheduler transition when a durable CAS fails."""

        if not isinstance(checkpoint, Mapping) or not isinstance(checkpoint.get("job_id"), str) or not isinstance(checkpoint.get("job"), Mapping):
            return False
        job_id = _safe_id(checkpoint.get("job_id"), _JOB_ID)
        current = self._jobs.get(job_id or "")
        if (
            job_id is None or current is None or not isinstance(expected_revision, int)
            or current.get("revision") != expected_revision
            or (expected_epoch is not None and (not isinstance(expected_epoch, int) or self._epoch != expected_epoch))
        ):
            return False
        original = deepcopy(dict(checkpoint["job"]))
        if original.get("job_id") != job_id or not isinstance(original.get("revision"), int):
            return False
        before_jobs = checkpoint.get("jobs")
        before_reservations = checkpoint.get("reservations")
        before_epoch = checkpoint.get("epoch")
        if not isinstance(before_jobs, Mapping) or not isinstance(before_reservations, Mapping) or not isinstance(before_epoch, int):
            return False
        self._jobs = deepcopy(dict(before_jobs))
        self._reservations = deepcopy(dict(before_reservations))
        self._epoch = before_epoch
        return True

    @_synchronized
    def discard_unpersisted(self, job_id: object, worker_id: object, *, expected_revision: object) -> bool:
        """Drop a pre-execution scheduler admission that never reached SQLite."""

        identifier = _safe_id(job_id, _JOB_ID)
        worker = _safe_id(worker_id, _WORKER_ID)
        job = self._jobs.get(identifier or "")
        if job is None or worker is None or job.get("worker_id") != worker or job.get("revision") != expected_revision or job.get("state") not in {"QUEUED", "WAITING_RESOURCE", "PREPARING"}:
            return False
        reservation_id = job.get("reservation_id")
        if isinstance(reservation_id, str):
            self._reservations.pop(reservation_id, None)
        self._jobs.pop(str(identifier), None)
        self._epoch += 1
        return True

    def _active_jobs(self) -> list[dict[str, Any]]:
        return [item for item in self._jobs.values() if item["state"] in _ACTIVE_STATES]

    def _prune_terminal_jobs(self) -> int:
        """Retain a bounded recent terminal coordination history only.

        Durable SQLite metadata is the history authority.  This process-local
        scheduler owns only active coordination, so pruning terminal entries
        cannot delete a durable record, artifact reference, or user data.
        """

        terminal = [item for item in self._jobs.values() if item["state"] in _TERMINAL_STATES and item.get("reservation_id") is None]
        if len(terminal) <= _MAX_TERMINAL_JOBS:
            return 0
        terminal.sort(key=lambda item: (str(item.get("terminal_at") or item["updated_at"]), item["job_id"]))
        removed = 0
        for item in terminal[: len(terminal) - _MAX_TERMINAL_JOBS]:
            self._jobs.pop(str(item["job_id"]), None)
            removed += 1
        if removed:
            self._epoch += removed
        return removed

    def _reserved(self, *, key: str | None = None, slot_kind: str | None = None, gpu_id: str | None = None) -> int:
        total = 0
        for reservation in self._reservations.values():
            job = self._jobs.get(reservation["job_id"])
            if job is None or job["state"] not in _ACTIVE_STATES:
                continue
            if gpu_id is not None and reservation.get("gpu_id") != gpu_id:
                continue
            if key is not None:
                if slot_kind == "runtime" and reservation.get("runtime_slot") != key:
                    continue
                if slot_kind == "provider" and reservation.get("provider_slot") != key:
                    continue
                if slot_kind not in {"runtime", "provider"} and key not in {reservation.get("runtime_slot"), reservation.get("provider_slot")}:
                    continue
            total += 1 if key is not None else int(reservation["vram_estimate_mb"])
        return total

    def _resource_usage(self) -> dict[str, int]:
        active = self._active_jobs()
        return {
            "cpu_slots": sum(int(item["profile"]["cpu_slots"]) for item in active),
            "ram_mb": sum(int(item["profile"]["estimated_ram_mb"]) for item in active),
            "disk_mb": sum(int(item["profile"]["disk_mb"]) for item in active),
        }

    @staticmethod
    def _heavy(profile: Mapping[str, Any]) -> bool:
        return profile["gpu_required"] is True and (profile["exclusive"] is True or int(profile["estimated_vram_mb"]) >= _HEAVY_VRAM_MB)

    def _gpu_conflict(self, profile: Mapping[str, Any]) -> bool:
        """Apply GPU exclusivity in both directions, not only for heavy jobs."""

        if profile["gpu_required"] is not True:
            return False
        for reservation in self._reservations.values():
            job = self._jobs.get(str(reservation["job_id"]))
            if job is None or job["state"] not in _ACTIVE_STATES or reservation.get("gpu_id") is None:
                continue
            if profile["exclusive"] is True or job["profile"]["exclusive"] is True:
                return True
        return False

    def _gpu_capacity_mb(self, gpu: Mapping[str, Any]) -> int:
        """Return conservatively available bytes for *new* Hub reservations.

        A server-owned current-free observation is preferable.  The fallback
        is explicit and therefore only useful for declarative/preflight state;
        the public snapshot exposes that no process-free observation was
        available rather than fabricating one.
        """

        reserved = self._reserved(gpu_id=str(gpu["id"]))
        observed_free = gpu.get("free_vram_mb")
        if isinstance(observed_free, int) and not isinstance(observed_free, bool):
            # ``free_vram_mb`` is already host free capacity.  Subtract only
            # reservations that the Hub itself has made since that snapshot;
            # bounding at zero prevents an optimistic result.
            return max(0, int(observed_free) - reserved - int(self._inventory["gpu_safety_margin_mb"]))
        return max(0, int(gpu["vram_mb"]) - reserved - int(self._inventory["gpu_safety_margin_mb"]))

    def _available_gpu(self, profile: Mapping[str, Any]) -> str | None:
        required = int(profile["estimated_vram_mb"])
        candidates = sorted(self._inventory["gpus"], key=lambda item: (self._gpu_capacity_mb(item), item["id"]), reverse=True)
        for gpu in candidates:
            if self._gpu_capacity_mb(gpu) >= required:
                return str(gpu["id"])
        return None

    def _capacity_reason(self, profile: Mapping[str, Any]) -> tuple[str, str]:
        if self._inventory["status"] != "available":
            return "resource_inventory_unavailable", "Publish a bounded server-owned CPU/RAM/disk/GPU inventory before preparing this job."
        usage = self._resource_usage()
        if self._inventory["cpu_slots"] is not None and usage["cpu_slots"] + int(profile["cpu_slots"]) > int(self._inventory["cpu_slots"]):
            return "cpu_slots_unavailable", "Wait for CPU slots to become available before preparing this job."
        if self._inventory["ram_mb"] is not None and usage["ram_mb"] + int(profile["estimated_ram_mb"]) > int(self._inventory["ram_mb"]):
            return "ram_unavailable", "Wait for RAM capacity to become available before preparing this job."
        if self._inventory["disk_mb"] is not None and usage["disk_mb"] + int(profile["disk_mb"]) > int(self._inventory["disk_mb"]):
            return "disk_unavailable", "Free or reserve sufficient managed disk capacity before preparing this job."
        if profile["gpu_required"] is True:
            heavy_active = sum(1 for item in self._active_jobs() if self._heavy(item["profile"]))
            if self._heavy(profile) and heavy_active >= self._max_heavy_gpu_jobs:
                return "heavy_gpu_limit", "Wait for the existing heavy GPU reservation to release; maximum heavy GPU jobs is one."
            if self._gpu_conflict(profile):
                return "gpu_exclusive_conflict", "An existing GPU reservation is exclusive, or this profile requires exclusive GPU ownership."
            if not self._inventory["gpus"]:
                return "gpu_inventory_unavailable", "Publish a server-owned GPU inventory before scheduling this GPU-required job."
            if self._available_gpu(profile) is None:
                return "vram_unavailable", "Wait for compatible GPU VRAM to become available or choose a separately approved lower requirement."
        for field, slots_name in (("runtime_slot", "runtime_slots"), ("provider_slot", "provider_slots")):
            slot = profile.get(field)
            if isinstance(slot, str):
                capacity = self._inventory[slots_name].get(slot)
                slot_kind = "runtime" if field == "runtime_slot" else "provider"
                if capacity is not None and self._reserved(key=slot, slot_kind=slot_kind) >= capacity:
                    return f"{field}_unavailable", "Wait for the exact runtime/provider slot to release before preparing this job."
        return "ready", "Resource requirements fit the current server-owned snapshot."

    def _release(self, reservation_id: str) -> None:
        if self._reservations.pop(reservation_id, None) is not None:
            self._epoch += 1

    def _reschedule_waiting(self, *, now: float, exclude_job_ids: set[str] | None = None) -> int:
        """Prepare waiting jobs by priority after a bounded reservation release.

        This changes only scheduler metadata. A prepared job still needs an
        exact worker claim; this method never dispatches or starts one.
        """

        excluded = exclude_job_ids or set()
        candidates = sorted(
            (item for item in self._jobs.values() if item["state"] in {"QUEUED", "WAITING_RESOURCE"} and item["job_id"] not in excluded),
            key=lambda item: (-int(item["profile"]["priority"]), item["created_at"], item["job_id"]),
        )
        prepared = 0
        for item in candidates:
            before = item["state"]
            updated = self._try_prepare(str(item["job_id"]), now=now)
            if before != "PREPARING" and updated["state"] == "PREPARING":
                prepared += 1
        return prepared

    def _try_prepare(self, job_id: str, *, now: float) -> dict[str, Any]:
        job = self._jobs[job_id]
        profile = job["profile"]
        code, action = self._capacity_reason(profile)
        if code != "ready":
            self._touch(job, state="WAITING_RESOURCE", reason_code=code, next_action=action, reservation_id=None, lease=None)
            return deepcopy(job)
        gpu_id = self._available_gpu(profile) if profile["gpu_required"] is True else None
        reservation_id = f"resv_{secrets.token_hex(16)}"
        reservation = {
            "reservation_id": reservation_id,
            "job_id": job_id,
            "worker_id": job["worker_id"],
            "gpu_id": gpu_id,
            "vram_estimate_mb": int(profile["estimated_vram_mb"]),
            "runtime_slot": profile["runtime_slot"],
            "provider_slot": profile["provider_slot"],
            "expires_at_monotonic": now + self._reservation_ttl,
        }
        self._reservations[reservation_id] = reservation
        self._touch(
            job,
            state="PREPARING",
            reason_code="reservation_created",
            next_action="The exact owned worker must claim this reservation before it can run.",
            reservation_id=reservation_id,
            lease=None,
        )
        return deepcopy(job)

    @_synchronized
    def submit(self, job_id: object, worker_id: object, profile_id: object, *, now: float | None = None) -> dict[str, Any]:
        job = _safe_id(job_id, _JOB_ID)
        worker = _safe_id(worker_id, _WORKER_ID)
        profile = self._profiles.get(profile_id) if isinstance(profile_id, str) else None
        if job is None or worker is None or profile is None:
            return {"status": "invalid", "code": "scheduler_submission_invalid", "execution": "not_run", "dry_run": True}
        if job in self._jobs:
            return {"status": "conflict", "code": "scheduler_job_exists", "execution": "not_run", "dry_run": True}
        self._prune_terminal_jobs()
        if len(self._jobs) >= _MAX_JOBS:
            return {"status": "unavailable", "code": "scheduler_job_limit", "execution": "not_run", "dry_run": True}
        self._jobs[job] = {
            "job_id": job,
            "worker_id": worker,
            "profile": _copy_profile(profile),
            "state": "QUEUED",
            "reservation_id": None,
            "lease": None,
            "progress": 0,
            "revision": 1,
            "created_at": _timestamp(),
            "updated_at": _timestamp(),
            "terminal_at": None,
            "reason_code": "queued",
            "next_action": "Waiting for server-owned resource allocation.",
        }
        return self._public_job(self._try_prepare(job, now=_now() if now is None else now))

    @_synchronized
    def claim_running(self, job_id: object, worker_id: object, reservation_id: object, *, now: float | None = None) -> dict[str, Any]:
        job = self._jobs.get(_safe_id(job_id, _JOB_ID) or "")
        worker = _safe_id(worker_id, _WORKER_ID)
        reservation = self._reservations.get(_safe_id(reservation_id, _RESERVATION_ID) or "")
        current = _now() if now is None else now
        if job is None or worker is None or reservation is None:
            return {"status": "invalid", "code": "reservation_identity_invalid", "execution": "not_run", "dry_run": True}
        if job["state"] != "PREPARING" or reservation["job_id"] != job["job_id"] or reservation["worker_id"] != worker or job.get("reservation_id") != reservation["reservation_id"]:
            return {"status": "conflict", "code": "reservation_identity_mismatch", "execution": "not_run", "dry_run": True}
        if current > float(reservation["expires_at_monotonic"]):
            self._release(reservation["reservation_id"])
            self._touch(job, state="WAITING_RESOURCE", reservation_id=None, lease=None, reason_code="reservation_expired", next_action="Request a fresh resource reservation before starting the worker.")
            return self._public_job(job)
        lease = {
            "lease_id": f"lease_{secrets.token_hex(16)}",
            "worker_id": worker,
            "job_id": job["job_id"],
            "reservation_id": reservation["reservation_id"],
            "heartbeat_at_monotonic": current,
            "expires_at_monotonic": current + self._lease_ttl,
            "heartbeat_at": _timestamp(),
            "expires_at": _timestamp_after(self._lease_ttl),
        }
        self._touch(
            job,
            state="RUNNING",
            lease=lease,
            reason_code="worker_claimed_reservation",
            next_action="The worker may report progress only while this exact reservation and lease remain bound.",
        )
        return self._public_job(job)

    @_synchronized
    def heartbeat(self, job_id: object, worker_id: object, reservation_id: object, lease_id: object, *, now: float | None = None) -> dict[str, Any]:
        job, code = self._bound_active(job_id, worker_id, reservation_id, lease_id, expected={"RUNNING", "PAUSED", "CANCELLING"})
        if job is None:
            return {"status": "conflict", "code": code, "execution": "not_run", "dry_run": True}
        lease = dict(job["lease"])
        current = _now() if now is None else now
        lease["heartbeat_at_monotonic"] = current
        lease["expires_at_monotonic"] = current + self._lease_ttl
        lease["heartbeat_at"] = _timestamp()
        lease["expires_at"] = _timestamp_after(self._lease_ttl)
        self._touch(job, lease=lease, reason_code="worker_heartbeat", next_action="The exact server-owned worker holds a bounded renewable lease.")
        return self._public_job(job)

    @_synchronized
    def progress(self, job_id: object, worker_id: object, reservation_id: object, lease_id: object, value: object, *, now: float | None = None) -> dict[str, Any]:
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 100:
            return {"status": "invalid", "code": "progress_invalid", "execution": "not_run", "dry_run": True}
        heartbeat = self.heartbeat(job_id, worker_id, reservation_id, lease_id, now=now)
        if heartbeat.get("state") != "RUNNING":
            return heartbeat
        job = self._jobs.get(_safe_id(job_id, _JOB_ID) or "")
        if job is None:
            return {"status": "conflict", "code": "scheduler_job_missing", "execution": "not_run", "dry_run": True}
        self._touch(job, progress=value, reason_code="worker_progress", next_action="The exact server-owned worker may finish only with its current lease.")
        return self._public_job(job)

    @_synchronized
    def pause(self, job_id: object, worker_id: object, reservation_id: object, lease_id: object | None = None) -> dict[str, Any]:
        return self._transition_active(job_id, worker_id, reservation_id, lease_id, expected="RUNNING", target="PAUSED", code="paused", action="The reservation remains bound while this job is paused.")

    @_synchronized
    def resume(self, job_id: object, worker_id: object, reservation_id: object, lease_id: object | None = None) -> dict[str, Any]:
        return self._transition_active(job_id, worker_id, reservation_id, lease_id, expected="PAUSED", target="RUNNING", code="resumed", action="The exact worker resumed under its existing reservation.")

    @_synchronized
    def cancel(self, job_id: object, worker_id: object) -> dict[str, Any]:
        job = self._jobs.get(_safe_id(job_id, _JOB_ID) or "")
        worker = _safe_id(worker_id, _WORKER_ID)
        if job is None or worker is None or job["worker_id"] != worker:
            return {"status": "invalid", "code": "scheduler_cancellation_invalid", "execution": "not_run", "dry_run": True}
        if job["state"] in _TERMINAL_STATES:
            return {"status": "conflict", "code": "scheduler_job_terminal", "execution": "not_run", "dry_run": True}
        reservation_id = job.get("reservation_id")
        if job["state"] in {"QUEUED", "WAITING_RESOURCE"}:
            self._touch(job, state="CANCELLED", reason_code="cancelled_before_prepare", next_action="The job did not receive a resource reservation.", reservation_id=None, lease=None, terminal_at=_timestamp())
        else:
            self._touch(job, state="CANCELLING", reason_code="cancellation_requested", next_action="The exact worker must acknowledge cancellation before its reservation is released.")
        return self._public_job(job)

    @_synchronized
    def acknowledge_cancel(self, job_id: object, worker_id: object, reservation_id: object, lease_id: object | None = None) -> dict[str, Any]:
        return self._finish(job_id, worker_id, reservation_id, lease_id, expected_states={"CANCELLING"}, state="CANCELLED", code="cancelled", action="The owned reservation was released after cancellation acknowledgement.")

    @_synchronized
    def finish(self, job_id: object, worker_id: object, reservation_id: object, *, succeeded: bool, lease_id: object | None = None) -> dict[str, Any]:
        target = "SUCCEEDED" if succeeded else "FAILED"
        return self._finish(job_id, worker_id, reservation_id, lease_id, expected_states={"RUNNING", "PAUSED"}, state=target, code=target.casefold(), action="The owned reservation was released after a terminal worker result.")

    def _bound_active(self, job_id: object, worker_id: object, reservation_id: object, lease_id: object | None, *, expected: set[str]) -> tuple[dict[str, Any] | None, str]:
        job = self._jobs.get(_safe_id(job_id, _JOB_ID) or "")
        worker = _safe_id(worker_id, _WORKER_ID)
        reservation = self._reservations.get(_safe_id(reservation_id, _RESERVATION_ID) or "")
        if job is None or worker is None or reservation is None or job["state"] not in expected or job["worker_id"] != worker or job.get("reservation_id") != reservation["reservation_id"] or reservation["job_id"] != job["job_id"] or reservation["worker_id"] != worker:
            return None, "reservation_identity_mismatch"
        lease = job.get("lease")
        # Cancellation can arrive while a reservation is PREPARING, before a
        # worker has ever claimed a lease.  The exact reservation/worker pair
        # remains sufficient to acknowledge that non-executing cancellation.
        if job["state"] == "CANCELLING" and lease is None:
            return job, "ready"
        if lease_id is None:
            return None, "worker_lease_required"
        if not isinstance(lease, Mapping) or lease.get("worker_id") != worker or lease.get("job_id") != job["job_id"] or lease.get("reservation_id") != reservation["reservation_id"]:
            return None, "worker_lease_mismatch"
        if lease_id is not None and _safe_id(lease_id, _LEASE_ID) != lease.get("lease_id"):
            return None, "worker_lease_mismatch"
        if _now() > float(lease.get("expires_at_monotonic", 0)):
            return None, "worker_lease_expired"
        return job, "ready"

    def _transition_active(self, job_id: object, worker_id: object, reservation_id: object, lease_id: object | None, *, expected: str, target: str, code: str, action: str) -> dict[str, Any]:
        job, failure = self._bound_active(job_id, worker_id, reservation_id, lease_id, expected={expected})
        if job is None:
            return {"status": "conflict", "code": failure, "execution": "not_run", "dry_run": True}
        self._touch(job, state=target, reason_code=code, next_action=action)
        return self._public_job(job)

    def _finish(self, job_id: object, worker_id: object, reservation_id: object, lease_id: object | None, *, expected_states: set[str], state: str, code: str, action: str) -> dict[str, Any]:
        job, failure = self._bound_active(job_id, worker_id, reservation_id, lease_id, expected=expected_states)
        if job is None:
            return {"status": "conflict", "code": failure, "execution": "not_run", "dry_run": True}
        self._release(str(reservation_id))
        self._touch(job, state=state, reservation_id=None, lease=None, reason_code=code, next_action=action, terminal_at=_timestamp())
        self._reschedule_waiting(now=_now())
        # Do not prune this just-transitioned record here: the durable engine
        # may still need to compensate a failed SQLite CAS using the exact
        # scheduler checkpoint. Pruning occurs before later admission and in
        # reconciliation after the saga has returned.
        return self._public_job(job)

    @_synchronized
    def reconcile(self, *, now: float | None = None) -> dict[str, int]:
        current = _now() if now is None else now
        expired = 0
        lease_expired = 0
        expired_jobs: set[str] = set()
        for reservation_id, reservation in list(self._reservations.items()):
            job = self._jobs.get(reservation["job_id"])
            if job is None:
                self._release(reservation_id)
                expired += 1
                continue
            if job["state"] == "PREPARING" and current > float(reservation["expires_at_monotonic"]):
                self._release(reservation_id)
                self._touch(job, state="WAITING_RESOURCE", reservation_id=None, lease=None, reason_code="reservation_expired", next_action="Request a fresh reservation before the worker starts.")
                expired_jobs.add(str(job["job_id"]))
                expired += 1
                continue
            lease = job.get("lease")
            if job["state"] in {"RUNNING", "PAUSED", "CANCELLING"} and isinstance(lease, Mapping) and current > float(lease.get("expires_at_monotonic", 0)):
                self._release(reservation_id)
                self._touch(job, state="FAILED", reservation_id=None, lease=None, reason_code="worker_lease_expired", next_action="The worker lease expired and its resource reservation was released; use a trusted readmission/retry path only after preflight.", terminal_at=_timestamp())
                expired_jobs.add(str(job["job_id"]))
                lease_expired += 1
        prepared = self._reschedule_waiting(now=current, exclude_job_ids=expired_jobs)
        pruned = self._prune_terminal_jobs()
        return {"expired": expired, "lease_expired": lease_expired, "prepared": prepared, "pruned": pruned, "jobs": len(self._jobs)}

    def _public_job(self, value: Mapping[str, Any]) -> dict[str, Any]:
        profile = value["profile"]
        reservation = self._reservation(value.get("reservation_id")) if value.get("reservation_id") else None
        return {
            "job_id": value["job_id"],
            "worker_id": value["worker_id"],
            "profile_id": profile["profile_id"],
            "state": value["state"],
            "revision": value["revision"],
            "scheduler_epoch": self._epoch,
            "progress": value.get("progress", 0),
            "reservation": self._public_reservation(reservation) if reservation is not None else None,
            "lease": self._public_lease(value.get("lease")),
            "reason_code": value["reason_code"],
            "next_action": value["next_action"],
            "execution": "not_run",
            "dry_run": True,
        }

    @staticmethod
    def _public_reservation(value: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "reservation_id": value["reservation_id"],
            "job_id": value["job_id"],
            "worker_id": value["worker_id"],
            "gpu_id": value["gpu_id"],
            "vram_estimate_mb": value["vram_estimate_mb"],
            "runtime_slot": value["runtime_slot"],
            "provider_slot": value["provider_slot"],
            "expires_in_seconds": max(0, int(float(value["expires_at_monotonic"]) - _now())),
        }

    @staticmethod
    def _public_lease(value: object) -> dict[str, Any] | None:
        if not isinstance(value, Mapping):
            return None
        lease_id = value.get("lease_id")
        if _safe_id(lease_id, _LEASE_ID) is None:
            return None
        return {
            "lease_id": lease_id,
            "worker_id": value.get("worker_id"),
            "job_id": value.get("job_id"),
            "reservation_id": value.get("reservation_id"),
            "heartbeat_at": value.get("heartbeat_at"),
            "expires_at": value.get("expires_at"),
            "expires_in_seconds": max(0, int(float(value.get("expires_at_monotonic", 0)) - _now())),
        }

    @_synchronized
    def job(self, job_id: object) -> dict[str, Any] | None:
        value = self._job(job_id)
        return self._public_job(value) if value is not None else None

    @_synchronized
    def snapshot(self) -> dict[str, Any]:
        self.reconcile()
        usage = self._resource_usage()
        gpus = []
        for gpu in self._inventory["gpus"]:
            reserved = self._reserved(gpu_id=gpu["id"])
            observed_free = gpu.get("free_vram_mb")
            gpus.append({
                "gpu_id": gpu["id"],
                "vendor": gpu["vendor"],
                "device_class": gpu["device_class"],
                "model": gpu["model"],
                "vram_total_mb": gpu["vram_mb"],
                "vram_reserved_mb": reserved,
                "vram_available_for_reservation_mb": self._gpu_capacity_mb(gpu),
                "vram_free_observed_mb": observed_free,
                "vram_used_by_processes_mb": (max(0, int(gpu["vram_mb"]) - int(observed_free)) if isinstance(observed_free, int) else None),
                "observed_at": gpu.get("observed_at") or self._inventory.get("observed_at"),
                "source_fingerprint": gpu.get("source_fingerprint") or self._inventory.get("source_fingerprint"),
            })
        jobs = [self._public_job(value) for value in sorted(self._jobs.values(), key=lambda item: (item["created_at"], item["job_id"]))]
        counts = {state: sum(1 for item in jobs if item["state"] == state) for state in RESOURCE_SCHEDULER_STATES}
        waiting = [item for item in jobs if item["state"] in {"QUEUED", "WAITING_RESOURCE"}]
        return {
            "schema_version": RESOURCE_SCHEDULER_SCHEMA_VERSION,
            "status": "available" if self._inventory["status"] == "available" else "unavailable",
            "inventory": {
                "status": self._inventory["status"],
                "cpu_slots_total": self._inventory["cpu_slots"],
                "cpu_slots_reserved": usage["cpu_slots"],
                "ram_total_mb": self._inventory["ram_mb"],
                "ram_reserved_mb": usage["ram_mb"],
                "disk_total_mb": self._inventory["disk_mb"],
                "disk_reserved_mb": usage["disk_mb"],
                "observed_at": self._inventory.get("observed_at"),
                "source_fingerprint": self._inventory.get("source_fingerprint"),
                "gpus": gpus,
                "runtime_slots": {
                    key: {"capacity": amount, "reserved": self._reserved(key=key, slot_kind="runtime")}
                    for key, amount in self._inventory["runtime_slots"].items()
                },
                "provider_slots": {
                    key: {"capacity": amount, "reserved": self._reserved(key=key, slot_kind="provider")}
                    for key, amount in self._inventory["provider_slots"].items()
                },
            },
            "policy": {
                "max_heavy_gpu_jobs": self._max_heavy_gpu_jobs,
                "reservation_ttl_seconds": self._reservation_ttl,
                "lease_ttl_seconds": self._lease_ttl,
                "gpu_safety_margin_mb": self._inventory["gpu_safety_margin_mb"],
                "terminal_history_limit": _MAX_TERMINAL_JOBS,
            },
            "profiles": [_copy_profile(self._profiles[key]) for key in sorted(self._profiles)],
            "jobs": jobs,
            "counts": counts,
            "queue": {"waiting": len(waiting), "estimated_next_start": None, "reason": "No start time is fabricated; it depends on exact worker/reservation release events."},
            "reason": "Resource Scheduler V2 uses a server-owned inventory snapshot and reserves capacity only; it does not probe hardware or launch workloads.",
            "next_action": "Bind this contract to the Durable Job Engine only after a job has passed capability and artifact preflight.",
            "execution": "not_run",
            "dry_run": True,
        }


__all__ = [
    "RESOURCE_SCHEDULER_SCHEMA_VERSION",
    "RESOURCE_SCHEDULER_STATES",
    "ResourceScheduler",
    "ResourceSchedulerError",
    "server_owned_resource_profiles",
]
