"""Read-only physical and concurrent resource planning for Module Manager."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any


TARGET_GPU = {"vendor": "nvidia", "device_class": "discrete", "model": "RTX 4060", "vram_mb": 8192}
_SAFE_GPU_VENDORS = {"any", "nvidia", "amd", "intel"}
_SAFE_DEVICE_CLASSES = {"any", "discrete", "integrated"}


def _invalid(code: str, action: str) -> dict[str, Any]:
    return {"status": "unavailable", "execution": "not_run", "dry_run": True, "physical": [], "concurrent": [], "errors": [{"code": code}], "actions": [action], "target_gpu": deepcopy(TARGET_GPU)}


def _gpu(value: object) -> tuple[dict[str, Any] | None, str | None]:
    if value is None:
        return {"required": False, "vendor": "any", "device_class": "any", "vram_mb": 0, "target": None}, None
    if not isinstance(value, Mapping) or set(value) - {"required", "vendor", "device_class", "vram_mb", "target"}:
        return None, "gpu_hint_shape"
    required = value.get("required", True)
    vendor = value.get("vendor", "any")
    device_class = value.get("device_class", "any")
    vram = value.get("vram_mb", 0)
    if type(required) is not bool or vendor not in _SAFE_GPU_VENDORS or device_class not in _SAFE_DEVICE_CLASSES or type(vram) is not int or vram < 0 or vram > 1024 * 1024:
        return None, "gpu_hint_invalid"
    target = value.get("target")
    if target is not None and (not isinstance(target, str) or not 1 <= len(target) <= 80 or any(token in target.lower() for token in ("path", "command", "shell"))):
        return None, "gpu_target_invalid"
    return {"required": required, "vendor": vendor, "device_class": device_class, "vram_mb": vram, "target": target}, None


def _request(item: object, index: int) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(item, Mapping) or not isinstance(item.get("id"), str) or not item["id"] or ".." in item["id"]:
        return None, "module_request_invalid"
    allowed = {"id", "resource_hints", "concurrent_slot", "source_manifest"}
    if set(item) - allowed:
        return None, "module_request_unknown_field"
    hints = item.get("resource_hints") if isinstance(item.get("resource_hints"), Mapping) else {}
    if set(hints) - {"cpu_cores", "ram_mb", "disk_mb", "gpu"}:
        return None, "resource_hint_shape"
    numeric = {field: hints.get(field, 0) for field in ("cpu_cores", "ram_mb", "disk_mb")}
    if any(type(value) is not int or value < 0 for value in numeric.values()):
        return None, "resource_hint_invalid"
    gpu, error = _gpu(hints.get("gpu"))
    if error:
        return None, error
    slot = item.get("concurrent_slot", item["id"])
    if not isinstance(slot, str) or not slot or ".." in slot:
        return None, "concurrent_slot_invalid"
    manifest = item.get("source_manifest")
    if manifest is not None:
        if not isinstance(manifest, Mapping) or set(manifest) - {"url", "sha256", "license", "version"}:
            return None, "source_manifest_shape"
        url = manifest.get("url")
        digest = manifest.get("sha256")
        if not isinstance(url, str) or not url.startswith("https://") or any(token in url.lower() for token in ("@", "file:", "?", "#", "\\", "..")):
            return None, "source_url_policy"
        if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            return None, "source_sha256_required"
        if manifest.get("license") is not None and not isinstance(manifest["license"], str):
            return None, "source_license_invalid"
        if manifest.get("version") is not None and not isinstance(manifest["version"], str):
            return None, "source_version_invalid"
    return {"id": item["id"], "cpu_cores": numeric["cpu_cores"], "ram_mb": numeric["ram_mb"], "disk_mb": numeric["disk_mb"], "gpu": gpu, "slot": slot, "source_manifest": deepcopy(manifest)}, None


def _hardware(value: object) -> tuple[dict[str, Any] | None, str | None]:
    if value is None:
        return {"cpu_cores": None, "ram_mb": None, "disk_mb": None, "gpus": []}, None
    if not isinstance(value, Mapping) or set(value) - {"cpu_cores", "ram_mb", "disk_mb", "gpus"}:
        return None, "hardware_shape"
    for field in ("cpu_cores", "ram_mb", "disk_mb"):
        if value.get(field) is not None and (type(value[field]) is not int or value[field] < 0):
            return None, "hardware_numeric_invalid"
    gpus: list[dict[str, Any]] = []
    raw_gpus = value.get("gpus", [])
    if not isinstance(raw_gpus, list) or len(raw_gpus) > 16:
        return None, "hardware_gpu_invalid"
    for index, item in enumerate(raw_gpus):
        if not isinstance(item, Mapping) or set(item) - {"id", "vendor", "device_class", "model", "vram_mb"}:
            return None, "hardware_gpu_shape"
        if not isinstance(item.get("id", f"gpu-{index + 1}"), str) or not item.get("id", f"gpu-{index + 1}"):
            return None, "hardware_gpu_id"
        if item.get("vendor") not in {"nvidia", "amd", "intel"} or item.get("device_class") not in {"discrete", "integrated"} or type(item.get("vram_mb")) is not int or item["vram_mb"] < 0:
            return None, "hardware_gpu_invalid"
        gpus.append({"id": item.get("id", f"gpu-{index + 1}"), "vendor": item["vendor"], "device_class": item["device_class"], "model": item.get("model", "unknown"), "vram_mb": item["vram_mb"]})
    return {"cpu_cores": value.get("cpu_cores"), "ram_mb": value.get("ram_mb"), "disk_mb": value.get("disk_mb"), "gpus": gpus}, None


def _matches(request: Mapping[str, Any], gpu: Mapping[str, Any]) -> bool:
    hint = request["gpu"]
    return (hint["vendor"] in {"any", gpu["vendor"]}) and (hint["device_class"] in {"any", gpu["device_class"]})


def plan_module_resources(requests: Iterable[Mapping[str, Any]], *, hardware: Mapping[str, Any] | None = None, mode: str = "parallel") -> dict[str, Any]:
    """Return physical-fit and concurrent-allocation results without probing hardware."""

    if mode not in {"parallel", "serial"}:
        return _invalid("mode_invalid", "Choose parallel or serial planning mode.")
    environment, error = _hardware(hardware)
    if error or environment is None:
        return _invalid(error or "hardware_invalid", "Supply a bounded sanitized hardware snapshot.")
    parsed: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for index, item in enumerate(requests):
        request, request_error = _request(item, index)
        if request_error or request is None:
            errors.append({"code": request_error or "module_request_invalid", "index": str(index)})
        else:
            parsed.append(request)
    physical: list[dict[str, Any]] = []
    concurrent: list[dict[str, Any]] = []
    usage: dict[str, int] = {}
    actions: list[str] = []
    status = "operational"
    for request in parsed:
        hint = request["gpu"]
        matching = [gpu for gpu in environment["gpus"] if _matches(request, gpu)] if hint["required"] else []
        physical_fit = next((gpu for gpu in matching if gpu["vram_mb"] >= hint["vram_mb"]), None) if hint["required"] else None
        physical_ok = not hint["required"] or physical_fit is not None
        if not physical_ok:
            status = "unavailable"
            actions.append(f"Provide a compatible GPU with at least {hint['vram_mb']} MB VRAM for {request['id']}.")
        physical.append({"id": request["id"], "status": "available" if physical_ok else "unavailable", "gpu": physical_fit["id"] if physical_fit else None, "physical_fit": physical_ok})
        concurrent_ok = physical_ok
        assigned = None
        if physical_ok and hint["required"]:
            candidates = [gpu for gpu in matching if gpu["vram_mb"] >= hint["vram_mb"]]
            if mode == "serial":
                assigned = candidates[0]["id"] if candidates else None
            else:
                selected = next((gpu for gpu in candidates if usage.get(gpu["id"], 0) + hint["vram_mb"] <= gpu["vram_mb"]), None)
                if selected is None:
                    concurrent_ok = False
                    status = "partial" if status != "unavailable" else status
                    actions.append(f"Run {request['id']} serially or provide additional concurrent GPU VRAM.")
                else:
                    assigned = selected["id"]
                    usage[selected["id"]] = usage.get(selected["id"], 0) + hint["vram_mb"]
        concurrent.append({"id": request["id"], "status": "available" if concurrent_ok else "partial", "gpu": assigned, "concurrent_fit": concurrent_ok})
    if errors and status == "operational":
        status = "unavailable"
    return {"status": status, "execution": "not_run", "dry_run": True, "mode": mode, "target_gpu": deepcopy(TARGET_GPU), "physical": physical, "concurrent": concurrent, "errors": errors, "actions": sorted(set(actions))}


__all__ = ["TARGET_GPU", "plan_module_resources"]
