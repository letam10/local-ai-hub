"""Dry-run resource planning for declarative extension manifests.

This service performs arithmetic on user-provided resource profiles only.  It
does not probe hardware, import extensions, allocate GPU memory, or start any
workload.  A caller may optionally provide a sanitized hardware inventory to
obtain deterministic capacity checks.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from src.shared.schemas.extension_manifest import AVAILABILITY_STATUSES, ManifestValidationError, validate_extension_manifest


RESOURCE_PLANNER_VERSION = "resource-planner.v1"
_STATUS_RANK = {"operational": 0, "planned": 1, "partial": 2, "unavailable": 3}


@dataclass(frozen=True)
class _HardwareGpu:
    identifier: str
    vendor: str
    device_class: str
    vram_gb: float


@dataclass(frozen=True)
class _Hardware:
    cpu_threads: int | None
    ram_gb: float | None
    disk_gb: float | None
    gpus: tuple[_HardwareGpu, ...]
    explicit: bool


def _safe_hardware(value: Mapping[str, Any] | None) -> _Hardware:
    if value is None:
        return _Hardware(None, None, None, (), False)
    if not isinstance(value, Mapping):
        return _Hardware(None, None, None, (), True)

    def positive_number(item: Any) -> float | None:
        return float(item) if isinstance(item, (int, float)) and not isinstance(item, bool) and float(item) >= 0 else None

    cpu = value.get("cpu_threads")
    cpu_threads = int(cpu) if isinstance(cpu, int) and not isinstance(cpu, bool) and cpu >= 1 else None
    gpus: list[_HardwareGpu] = []
    raw_gpus = value.get("gpus", [])
    if isinstance(raw_gpus, list):
        for index, item in enumerate(raw_gpus):
            if not isinstance(item, Mapping):
                continue
            identifier = item.get("id")
            if not isinstance(identifier, str) or not identifier or len(identifier) > 64:
                identifier = f"gpu-{index + 1}"
            vendor = item.get("vendor") if item.get("vendor") in {"nvidia", "amd", "intel"} else "unknown"
            device_class = item.get("device_class") if item.get("device_class") in {"integrated", "discrete"} else "unknown"
            vram = positive_number(item.get("vram_gb"))
            if vram is not None:
                gpus.append(_HardwareGpu(identifier, vendor, device_class, vram))
    return _Hardware(cpu_threads, positive_number(value.get("ram_gb")), positive_number(value.get("disk_gb")), tuple(gpus), True)


def summarize_resource_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    """Return the public, no-path summary used in plans and reports."""

    cpu = profile["cpu"]
    gpu = profile["gpu"]
    return {
        "cpu": {"class": cpu["class"], "threads": cpu["threads"]},
        "gpu": {"required": gpu["required"], "vendor": gpu["vendor"], "device_class": gpu["device_class"]},
        "vram_gb": float(profile["vram_gb"]),
        "ram_gb": float(profile["ram_gb"]),
        "disk_gb": float(profile["disk_gb"]),
        "exclusive_resource_groups": list(profile["exclusive_resource_groups"]),
    }


def _combine_status(current: str, candidate: str) -> str:
    return candidate if _STATUS_RANK[candidate] > _STATUS_RANK[current] else current


def _request_manifest(value: Mapping[str, Any]) -> Mapping[str, Any]:
    candidate = value.get("manifest") if isinstance(value.get("manifest"), Mapping) else value
    return validate_extension_manifest(candidate)


def _matches_gpu(profile: Mapping[str, Any], gpu: _HardwareGpu) -> bool:
    requirement = profile["gpu"]
    return (requirement["vendor"] in {"any", gpu.vendor}) and (requirement["device_class"] in {"any", gpu.device_class})


def _capacity_action(resource: str) -> str:
    return f"Run requests serially or provide additional {resource} capacity before executing a workload."


def plan_resources(
    requests: Iterable[Mapping[str, Any]],
    *,
    hardware: Mapping[str, Any] | None = None,
    mode: str = "parallel",
) -> dict[str, Any]:
    """Create a deterministic plan for declarative extension requests.

    ``mode`` is either ``parallel`` or ``serial``.  Both modes are always dry
    runs: this planner has no side effects and never starts a process.
    """

    if mode not in {"parallel", "serial"}:
        raise ValueError("mode must be parallel or serial")
    environment = _safe_hardware(hardware)
    plan_status = "operational"
    actions: list[str] = []
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    gpu_usage: dict[str, float] = defaultdict(float)

    for index, value in enumerate(requests):
        try:
            manifest = _request_manifest(value)
        except (ManifestValidationError, TypeError, AttributeError):
            plan_status = _combine_status(plan_status, "unavailable")
            rejected.append(
                {
                    "request_index": index,
                    "status": "unavailable",
                    "reason": "The request does not contain a valid extension-manifest.v1 descriptor.",
                    "action": "Validate the descriptor before asking the planner to schedule it.",
                }
            )
            continue

        availability = manifest["availability"]
        item_status = availability["status"]
        item_actions: list[str] = []
        profile = manifest["resource_profile"]
        assignment: str | None = None
        if profile["gpu"]["required"]:
            compatible = [gpu for gpu in environment.gpus if _matches_gpu(profile, gpu)]
            if not environment.explicit:
                item_status = _combine_status(item_status, "partial")
                item_actions.append("Provide a sanitized hardware inventory to verify the required GPU type and VRAM.")
            elif not compatible:
                item_status = _combine_status(item_status, "unavailable")
                item_actions.append("Provide a compatible GPU or select an extension profile that does not require one.")
            else:
                selected = next((gpu for gpu in compatible if gpu_usage[gpu.identifier] + float(profile["vram_gb"]) <= gpu.vram_gb), None)
                if selected is None:
                    item_status = _combine_status(item_status, "partial")
                    item_actions.append(_capacity_action("GPU VRAM"))
                else:
                    assignment = selected.identifier
                    gpu_usage[selected.identifier] += float(profile["vram_gb"])

        accepted.append(
            {
                "extension_id": manifest["id"],
                "display_name": manifest["display_name"],
                "status": item_status,
                "reason": availability["reason"],
                "action": availability["action"],
                "additional_actions": item_actions,
                "resource_profile": summarize_resource_profile(profile),
                "gpu_assignment": assignment,
            }
        )
        plan_status = _combine_status(plan_status, item_status)
        actions.extend(item_actions)

    totals = {
        "cpu_threads": sum(item["resource_profile"]["cpu"]["threads"] for item in accepted),
        "vram_gb": sum(item["resource_profile"]["vram_gb"] for item in accepted),
        "ram_gb": sum(item["resource_profile"]["ram_gb"] for item in accepted),
        "disk_gb": sum(item["resource_profile"]["disk_gb"] for item in accepted),
    }
    capacity = {
        "cpu_threads": environment.cpu_threads,
        "ram_gb": environment.ram_gb,
        "disk_gb": environment.disk_gb,
        "gpus": [
            {"id": gpu.identifier, "vendor": gpu.vendor, "device_class": gpu.device_class, "vram_gb": gpu.vram_gb}
            for gpu in environment.gpus
        ],
    }
    for total_key, capacity_value in (("cpu_threads", environment.cpu_threads), ("ram_gb", environment.ram_gb), ("disk_gb", environment.disk_gb)):
        if mode == "parallel" and capacity_value is not None and totals[total_key] > capacity_value:
            plan_status = _combine_status(plan_status, "partial")
            resource_name = total_key.replace("_", " ").upper() if total_key == "ram_gb" else total_key.replace("_", " ")
            actions.append(_capacity_action(resource_name))

    group_members: dict[str, list[str]] = defaultdict(list)
    for item in accepted:
        for group in item["resource_profile"]["exclusive_resource_groups"]:
            group_members[group].append(item["extension_id"])
    conflicts: list[dict[str, Any]] = []
    if mode == "parallel":
        for group, members in sorted(group_members.items()):
            if len(members) > 1:
                conflicts.append(
                    {
                        "kind": "exclusive_resource_group",
                        "group": group,
                        "extension_ids": members,
                        "reason": "These profiles declare mutual exclusion for the same resource group.",
                        "action": "Use serial mode or schedule one extension at a time for this group.",
                    }
                )
                plan_status = _combine_status(plan_status, "partial")
                actions.append("Serialize extensions that share an exclusive resource group.")

    return {
        "planner_version": RESOURCE_PLANNER_VERSION,
        "dry_run": True,
        "mode": mode,
        "status": plan_status,
        "requests": accepted,
        "rejected_requests": rejected,
        "totals": totals,
        "capacity": capacity,
        "conflicts": conflicts,
        "actions": sorted(set(actions)),
    }


class ResourcePlanner:
    """Small state-free façade useful to application services and tests."""

    def plan(
        self,
        requests: Iterable[Mapping[str, Any]],
        *,
        hardware: Mapping[str, Any] | None = None,
        mode: str = "parallel",
    ) -> dict[str, Any]:
        return plan_resources(requests, hardware=hardware, mode=mode)
