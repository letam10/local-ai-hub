"""Server-owned resource scheduling with no hardware probe or workload start.

The scheduler works from a bounded hardware snapshot supplied by server code.
It is intentionally a pure in-process coordinator in Phase 4: it can reserve
declared capacity and enforce identity/state rules, but it cannot launch a
worker, query a GPU, or dispatch a provider.  Phase 5 will bind durable jobs
to these reservations only after its execution-state contract is complete.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
import re
import secrets
import time
from typing import Any


RESOURCE_SCHEDULER_SCHEMA_VERSION = "resource-scheduler.v2"
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
_SLOT_ID = re.compile(r"^[a-z][a-z0-9._-]{1,63}$")
_MAX_INT = (1 << 31) - 1
_RESERVATION_TTL_SECONDS = 120
_HEAVY_VRAM_MB = 2048
_MAX_JOBS = 512


class ResourceSchedulerError(ValueError):
    """Fixed error when a scheduler contract is malformed."""


def _now() -> float:
    return time.monotonic()


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        {"profile_id": "image_gpu_8gb", "estimated_vram_mb": 8192, "estimated_ram_mb": 8192, "gpu_required": True, "cpu_fallback": False, "exclusive": True, "priority": 65, "interruptible": False, "batchable": False, "cpu_slots": 2, "disk_mb": 1024, "runtime_slot": "comfyui", "provider_slot": "image"},
    ]
    result: dict[str, dict[str, Any]] = {}
    for raw in profiles:
        normalized = _profile(raw)
        if normalized is None:
            raise RuntimeError("resource_profile_contract_invalid")
        result[normalized["profile_id"]] = normalized
    return result


def _inventory(value: object) -> dict[str, Any]:
    """Normalize a bounded server-owned hardware snapshot, never probe it."""

    if value is None:
        return {
            "status": "unknown",
            "cpu_slots": None,
            "ram_mb": None,
            "disk_mb": None,
            "gpus": [],
            "runtime_slots": {},
            "provider_slots": {},
        }
    if not isinstance(value, Mapping) or set(value) - {"cpu_slots", "cpu_cores", "ram_mb", "disk_mb", "gpus", "runtime_slots", "provider_slots"}:
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
        if not isinstance(raw, Mapping) or set(raw) - {"id", "vendor", "device_class", "model", "vram_mb"}:
            raise ResourceSchedulerError("resource_inventory_invalid")
        gpu_id = _safe_id(raw.get("id"), _GPU_ID)
        vram = _bounded_int(raw.get("vram_mb"))
        vendor = raw.get("vendor")
        device_class = raw.get("device_class")
        model = raw.get("model", "unknown")
        if gpu_id is None or vram is None or vendor not in {"nvidia", "amd", "intel"} or device_class not in {"discrete", "integrated"} or not isinstance(model, str) or not 1 <= len(model) <= 96:
            raise ResourceSchedulerError("resource_inventory_invalid")
        gpus.append({"id": gpu_id, "vendor": vendor, "device_class": device_class, "model": model, "vram_mb": vram})
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

    return {
        "status": "available",
        "cpu_slots": normalized_cpu,
        "ram_mb": ram_mb,
        "disk_mb": disk_mb,
        "gpus": gpus,
        "runtime_slots": slots("runtime_slots"),
        "provider_slots": slots("provider_slots"),
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
    ) -> None:
        if _bounded_int(max_heavy_gpu_jobs, minimum=1, maximum=8) is None or _bounded_int(reservation_ttl_seconds, minimum=10, maximum=3600) is None:
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
        self._jobs: dict[str, dict[str, Any]] = {}
        self._reservations: dict[str, dict[str, Any]] = {}

    @property
    def profiles(self) -> dict[str, dict[str, Any]]:
        return {key: _copy_profile(value) for key, value in self._profiles.items()}

    def _job(self, job_id: object) -> dict[str, Any] | None:
        identifier = _safe_id(job_id, _JOB_ID)
        value = self._jobs.get(identifier) if identifier else None
        return deepcopy(value) if value is not None else None

    def _reservation(self, reservation_id: object) -> dict[str, Any] | None:
        identifier = _safe_id(reservation_id, _RESERVATION_ID)
        value = self._reservations.get(identifier) if identifier else None
        return deepcopy(value) if value is not None else None

    def _active_jobs(self) -> list[dict[str, Any]]:
        return [item for item in self._jobs.values() if item["state"] in _ACTIVE_STATES]

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

    def _available_gpu(self, profile: Mapping[str, Any]) -> str | None:
        required = int(profile["estimated_vram_mb"])
        candidates = sorted(self._inventory["gpus"], key=lambda item: (int(item["vram_mb"]) - self._reserved(gpu_id=item["id"]), item["id"]), reverse=True)
        for gpu in candidates:
            if int(gpu["vram_mb"]) - self._reserved(gpu_id=gpu["id"]) >= required:
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
        self._reservations.pop(reservation_id, None)

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
            job.update({"state": "WAITING_RESOURCE", "reason_code": code, "next_action": action, "updated_at": _timestamp(), "reservation_id": None})
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
        job.update({"state": "PREPARING", "reason_code": "reservation_created", "next_action": "The exact owned worker must claim this reservation before it can run.", "updated_at": _timestamp(), "reservation_id": reservation_id})
        return deepcopy(job)

    def submit(self, job_id: object, worker_id: object, profile_id: object, *, now: float | None = None) -> dict[str, Any]:
        job = _safe_id(job_id, _JOB_ID)
        worker = _safe_id(worker_id, _WORKER_ID)
        profile = self._profiles.get(profile_id) if isinstance(profile_id, str) else None
        if job is None or worker is None or profile is None:
            return {"status": "invalid", "code": "scheduler_submission_invalid", "execution": "not_run", "dry_run": True}
        if job in self._jobs:
            return {"status": "conflict", "code": "scheduler_job_exists", "execution": "not_run", "dry_run": True}
        if len(self._jobs) >= _MAX_JOBS:
            return {"status": "unavailable", "code": "scheduler_job_limit", "execution": "not_run", "dry_run": True}
        self._jobs[job] = {
            "job_id": job,
            "worker_id": worker,
            "profile": _copy_profile(profile),
            "state": "QUEUED",
            "reservation_id": None,
            "created_at": _timestamp(),
            "updated_at": _timestamp(),
            "reason_code": "queued",
            "next_action": "Waiting for server-owned resource allocation.",
        }
        return self._public_job(self._try_prepare(job, now=_now() if now is None else now))

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
            job.update({"state": "WAITING_RESOURCE", "reservation_id": None, "reason_code": "reservation_expired", "next_action": "Request a fresh resource reservation before starting the worker.", "updated_at": _timestamp()})
            return self._public_job(job)
        job.update({"state": "RUNNING", "reason_code": "worker_claimed_reservation", "next_action": "The worker may report progress only while this exact reservation remains bound.", "updated_at": _timestamp()})
        return self._public_job(job)

    def pause(self, job_id: object, worker_id: object, reservation_id: object) -> dict[str, Any]:
        return self._transition_active(job_id, worker_id, reservation_id, expected="RUNNING", target="PAUSED", code="paused", action="The reservation remains bound while this job is paused.")

    def resume(self, job_id: object, worker_id: object, reservation_id: object) -> dict[str, Any]:
        return self._transition_active(job_id, worker_id, reservation_id, expected="PAUSED", target="RUNNING", code="resumed", action="The exact worker resumed under its existing reservation.")

    def cancel(self, job_id: object, worker_id: object) -> dict[str, Any]:
        job = self._jobs.get(_safe_id(job_id, _JOB_ID) or "")
        worker = _safe_id(worker_id, _WORKER_ID)
        if job is None or worker is None or job["worker_id"] != worker:
            return {"status": "invalid", "code": "scheduler_cancellation_invalid", "execution": "not_run", "dry_run": True}
        if job["state"] in _TERMINAL_STATES:
            return {"status": "conflict", "code": "scheduler_job_terminal", "execution": "not_run", "dry_run": True}
        reservation_id = job.get("reservation_id")
        if job["state"] in {"QUEUED", "WAITING_RESOURCE"}:
            job.update({"state": "CANCELLED", "reason_code": "cancelled_before_prepare", "next_action": "The job did not receive a resource reservation.", "updated_at": _timestamp(), "reservation_id": None})
        else:
            job.update({"state": "CANCELLING", "reason_code": "cancellation_requested", "next_action": "The exact worker must acknowledge cancellation before its reservation is released.", "updated_at": _timestamp()})
        return self._public_job(job)

    def acknowledge_cancel(self, job_id: object, worker_id: object, reservation_id: object) -> dict[str, Any]:
        return self._finish(job_id, worker_id, reservation_id, state="CANCELLED", code="cancelled", action="The owned reservation was released after cancellation acknowledgement.")

    def finish(self, job_id: object, worker_id: object, reservation_id: object, *, succeeded: bool) -> dict[str, Any]:
        target = "SUCCEEDED" if succeeded else "FAILED"
        return self._finish(job_id, worker_id, reservation_id, state=target, code=target.casefold(), action="The owned reservation was released after a terminal worker result.")

    def _transition_active(self, job_id: object, worker_id: object, reservation_id: object, *, expected: str, target: str, code: str, action: str) -> dict[str, Any]:
        job = self._jobs.get(_safe_id(job_id, _JOB_ID) or "")
        worker = _safe_id(worker_id, _WORKER_ID)
        reservation = self._reservations.get(_safe_id(reservation_id, _RESERVATION_ID) or "")
        if job is None or worker is None or reservation is None or job["state"] != expected or job["worker_id"] != worker or job.get("reservation_id") != reservation["reservation_id"] or reservation["job_id"] != job["job_id"] or reservation["worker_id"] != worker:
            return {"status": "conflict", "code": "reservation_identity_mismatch", "execution": "not_run", "dry_run": True}
        job.update({"state": target, "reason_code": code, "next_action": action, "updated_at": _timestamp()})
        return self._public_job(job)

    def _finish(self, job_id: object, worker_id: object, reservation_id: object, *, state: str, code: str, action: str) -> dict[str, Any]:
        job = self._jobs.get(_safe_id(job_id, _JOB_ID) or "")
        worker = _safe_id(worker_id, _WORKER_ID)
        reservation = self._reservations.get(_safe_id(reservation_id, _RESERVATION_ID) or "")
        if job is None or worker is None or reservation is None or job["worker_id"] != worker or job.get("reservation_id") != reservation["reservation_id"] or reservation["job_id"] != job["job_id"] or reservation["worker_id"] != worker or job["state"] not in {"RUNNING", "PAUSED", "CANCELLING"}:
            return {"status": "conflict", "code": "reservation_identity_mismatch", "execution": "not_run", "dry_run": True}
        self._release(reservation["reservation_id"])
        job.update({"state": state, "reservation_id": None, "reason_code": code, "next_action": action, "updated_at": _timestamp()})
        self._reschedule_waiting(now=_now())
        return self._public_job(job)

    def reconcile(self, *, now: float | None = None) -> dict[str, int]:
        current = _now() if now is None else now
        expired = 0
        expired_jobs: set[str] = set()
        for reservation_id, reservation in list(self._reservations.items()):
            job = self._jobs.get(reservation["job_id"])
            if job is None:
                self._release(reservation_id)
                expired += 1
                continue
            if job["state"] == "PREPARING" and current > float(reservation["expires_at_monotonic"]):
                self._release(reservation_id)
                job.update({"state": "WAITING_RESOURCE", "reservation_id": None, "reason_code": "reservation_expired", "next_action": "Request a fresh reservation before the worker starts.", "updated_at": _timestamp()})
                expired_jobs.add(str(job["job_id"]))
                expired += 1
        prepared = self._reschedule_waiting(now=current, exclude_job_ids=expired_jobs)
        return {"expired": expired, "prepared": prepared, "jobs": len(self._jobs)}

    def _public_job(self, value: Mapping[str, Any]) -> dict[str, Any]:
        profile = value["profile"]
        reservation = self._reservation(value.get("reservation_id")) if value.get("reservation_id") else None
        return {
            "job_id": value["job_id"],
            "worker_id": value["worker_id"],
            "profile_id": profile["profile_id"],
            "state": value["state"],
            "reservation": self._public_reservation(reservation) if reservation is not None else None,
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

    def job(self, job_id: object) -> dict[str, Any] | None:
        value = self._job(job_id)
        return self._public_job(value) if value is not None else None

    def snapshot(self) -> dict[str, Any]:
        self.reconcile()
        usage = self._resource_usage()
        gpus = []
        for gpu in self._inventory["gpus"]:
            reserved = self._reserved(gpu_id=gpu["id"])
            gpus.append({
                "gpu_id": gpu["id"],
                "vendor": gpu["vendor"],
                "device_class": gpu["device_class"],
                "model": gpu["model"],
                "vram_total_mb": gpu["vram_mb"],
                "vram_reserved_mb": reserved,
                "vram_available_for_reservation_mb": max(0, int(gpu["vram_mb"]) - reserved),
                "vram_used_by_processes_mb": None,
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
            "policy": {"max_heavy_gpu_jobs": self._max_heavy_gpu_jobs, "reservation_ttl_seconds": self._reservation_ttl},
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
