"""Read the constrained extension-platform configuration without leaking local data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from src.shared.schemas.extension_manifest import AVAILABILITY_STATUSES, EXTENSION_ID_PATTERN, PLATFORMS, is_semver


EXTENSION_CONFIG_SCHEMA_VERSION = "extensions-config.v1"
_MAX_CONFIG_BYTES = 1_000_000


def project_root() -> Path:
    """Return the repository root without depending on a machine-local setting."""

    return Path(__file__).resolve().parents[3]


def _safe_id(value: Any) -> bool:
    return isinstance(value, str) and bool(EXTENSION_ID_PATTERN.fullmatch(value))


def _normalize_inventory(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping) or not _safe_id(item.get("id")) or item["id"] in seen:
            continue
        status = item.get("status")
        if not isinstance(status, str) or status not in AVAILABILITY_STATUSES:
            continue
        normalized = {"id": item["id"], "status": status}
        if is_semver(item.get("version")):
            normalized["version"] = item["version"]
        result.append(normalized)
        seen.add(item["id"])
    return result


def _normalize_hardware(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    result: dict[str, Any] = {}
    for field in ("cpu_threads",):
        if isinstance(value.get(field), int) and not isinstance(value[field], bool) and value[field] > 0:
            result[field] = value[field]
    for field in ("ram_gb", "disk_gb"):
        if isinstance(value.get(field), (int, float)) and not isinstance(value[field], bool) and value[field] >= 0:
            result[field] = float(value[field])
    gpus: list[dict[str, Any]] = []
    if isinstance(value.get("gpus"), list):
        for index, gpu in enumerate(value["gpus"]):
            if not isinstance(gpu, Mapping):
                continue
            vendor = gpu.get("vendor")
            device_class = gpu.get("device_class")
            vram = gpu.get("vram_gb")
            if vendor not in {"nvidia", "amd", "intel"} or device_class not in {"integrated", "discrete"}:
                continue
            if not isinstance(vram, (int, float)) or isinstance(vram, bool) or vram < 0:
                continue
            identifier = gpu.get("id")
            if not isinstance(identifier, str) or not identifier or len(identifier) > 64:
                identifier = f"gpu-{index + 1}"
            gpus.append({"id": identifier, "vendor": vendor, "device_class": device_class, "vram_gb": float(vram)})
    result["gpus"] = gpus
    return result


def _normalize_config(value: Any, *, source: str) -> dict[str, Any] | None:
    if not isinstance(value, Mapping) or value.get("schema_version") != EXTENSION_CONFIG_SCHEMA_VERSION:
        return None
    hub_version = value.get("hub_version")
    platform = value.get("platform")
    if not is_semver(hub_version) or platform not in PLATFORMS:
        return None
    enabled = value.get("enabled_extensions", [])
    if not isinstance(enabled, list) or len(enabled) != len(set(enabled)) or any(not _safe_id(item) for item in enabled):
        return None
    return {
        "schema_version": EXTENSION_CONFIG_SCHEMA_VERSION,
        "source": source,
        "hub_version": hub_version,
        "platform": platform,
        "enabled_extensions": list(enabled),
        "components": _normalize_inventory(value.get("components")),
        "models": _normalize_inventory(value.get("models")),
        "hardware": _normalize_hardware(value.get("hardware")),
    }


def load_extension_config(root: Path | None = None) -> dict[str, Any]:
    """Load local configuration when valid, then fall back to the tracked example.

    Unknown fields are intentionally discarded; this permits a machine-local
    file to contain unrelated settings without surfacing secrets in a report.
    """

    resolved_root = (root or project_root()).resolve()
    for filename, source in (("extensions.local.json", "local"), ("extensions.example.json", "example")):
        candidate = resolved_root / "Config" / filename
        try:
            if candidate.is_symlink() or candidate.stat().st_size > _MAX_CONFIG_BYTES:
                continue
            value = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        normalized = _normalize_config(value, source=source)
        if normalized is not None:
            return normalized
    return {
        "schema_version": EXTENSION_CONFIG_SCHEMA_VERSION,
        "source": "defaults",
        "hub_version": None,
        "platform": None,
        "enabled_extensions": [],
        "components": [],
        "models": [],
        "hardware": None,
    }
