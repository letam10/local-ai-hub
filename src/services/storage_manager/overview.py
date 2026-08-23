"""Read-only storage and model overview for the unified UI."""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from src.services.api.config import load_json
from src.platform.paths import get_paths
from src.shared.paths.registry import (
    CACHE_ROOT,
    LOG_ROOT,
    MODEL_ROOT,
    OUTPUT_ROOT,
    ROOT,
    RUNTIME_ROOT,
    TEMP_ROOT,
)


_CACHE_SECONDS = 120.0
_LOW_SPACE_BYTES = 20 * 1024**3
_OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"
_OLLAMA_TIMEOUT_SECONDS = 0.5
_VOLUME_ALLOWLIST = (
    ("c", "C:", Path("C:/")),
    ("d", "D:", Path("D:/")),
)
_cache_lock = threading.Lock()
_size_cache: tuple[float, dict[str, Any]] | None = None
_volume_snapshot_cache: tuple[float, dict[str, Any]] | None = None
_model_cache: tuple[float, list[dict[str, Any]]] | None = None


def _managed_roots() -> tuple[Path, Path, Path, Path, Path, Path, Path, Path]:
    """Resolve the installed DATA_ROOT authority at call time.

    The registry constants are retained for legacy imports, but a split
    installation may select its data root through installation.json or the
    stable launch environment. Reading that authority here prevents a source
    checkout or historical Temp install from producing a false zero-sized
    storage card.
    """

    paths = get_paths()
    return (
        paths.data_root,
        paths.models_root,
        paths.environments_root,
        paths.runtime_root,
        paths.cache_root,
        paths.output_root,
        paths.temp_root,
        paths.log_root,
    )


def _is_reparse_point(entry: os.DirEntry[str]) -> bool:
    """Do not count a Windows junction target again through an alias."""

    try:
        if entry.is_symlink():
            return True
        attributes = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
        return bool(attributes & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT
    except OSError:
        return True


def _directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if _is_reparse_point(entry):
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def _bytes_record(value: int) -> dict[str, Any]:
    return {"bytes": value, "gb": round(value / (1024**3), 3)}


def _unavailable_volume(*, volume_id: str, label: str, reason: str, next_action: str) -> dict[str, Any]:
    """Return a truthful volume record without exposing a workstation path."""

    return {
        "id": volume_id,
        "label": label,
        "total_bytes": None,
        "free_bytes": None,
        "used_bytes": None,
        "total_gb": None,
        "free_gb": None,
        "used_gb": None,
        "low_space": None,
        "status": "unavailable",
        "availability": "unknown",
        "reason": reason,
        "next_action": next_action,
    }


def _volume_record(volume_id: str, label: str, root: Path) -> dict[str, Any]:
    """Project one fixed allowlisted volume using server-owned disk usage."""

    try:
        usage = shutil.disk_usage(root)
    except FileNotFoundError:
        return _unavailable_volume(
            volume_id=volume_id,
            label=label,
            reason="Volume is not mounted or is unavailable.",
            next_action="Connect or mount the volume, then refresh storage.",
        )
    except PermissionError:
        return _unavailable_volume(
            volume_id=volume_id,
            label=label,
            reason="Volume is present but its statistics cannot be read.",
            next_action="Check volume permissions, then refresh storage.",
        )
    except OSError:
        return _unavailable_volume(
            volume_id=volume_id,
            label=label,
            reason="Volume statistics are unavailable.",
            next_action="Verify the volume is readable, then refresh storage.",
        )

    total, used, free = usage
    if any(type(value) is not int or value < 0 for value in (total, used, free)):
        return _unavailable_volume(
            volume_id=volume_id,
            label=label,
            reason="Volume statistics are invalid and were not displayed.",
            next_action="Refresh storage after the volume reports valid statistics.",
        )
    if free > total or used > total:
        return _unavailable_volume(
            volume_id=volume_id,
            label=label,
            reason="Volume statistics are inconsistent and were not displayed.",
            next_action="Refresh storage after the volume reports consistent statistics.",
        )

    low_space = free < _LOW_SPACE_BYTES
    reason = "Free space is below the 20 GiB low-space threshold." if low_space else "Volume statistics are available from the server-owned allowlist."
    next_action = "Review output, cache, and temporary data before new writes." if low_space else "No action is required; refresh after external storage changes."
    return {
        "id": volume_id,
        "label": label,
        "total_bytes": total,
        "free_bytes": free,
        "used_bytes": used,
        "total_gb": round(total / (1024**3), 3),
        "free_gb": round(free / (1024**3), 3),
        "used_gb": round(used / (1024**3), 3),
        "low_space": low_space,
        "status": "available",
        "availability": "available",
        "reason": reason,
        "next_action": next_action,
    }


def _volume_projection() -> list[dict[str, Any]]:
    """Inspect only the fixed C:/D: volume allowlist; never accept client input."""

    return [_volume_record(volume_id, label, root) for volume_id, label, root in _VOLUME_ALLOWLIST]


def _volume_projection_payload(volumes: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": "completed" if all(item["status"] == "available" for item in volumes) else "partial",
        "execution": "not_run",
        "allowlist": [item["id"] for item in volumes],
        "volumes": volumes,
    }


def dashboard_volume_snapshot(*, force: bool = False) -> dict[str, Any]:
    """Return a cached, metadata-only C:/D: snapshot for Dashboard bootstrap.

    This path deliberately does not read managed directories, legacy metadata, or
    model/provider endpoints.  Full areas/legacy storage remains owned by
    ``storage_summary`` and its existing route.
    """

    global _volume_snapshot_cache
    now = time.monotonic()
    with _cache_lock:
        if not force and _volume_snapshot_cache and now - _volume_snapshot_cache[0] < _CACHE_SECONDS:
            return _volume_snapshot_cache[1]
    result = _volume_projection_payload(_volume_projection())
    with _cache_lock:
        _volume_snapshot_cache = (now, result)
    return result


def _is_ollama_model(item: dict[str, Any]) -> bool:
    return str(item.get("engine") or "").casefold() == "ollama"


def _ollama_tag_sizes() -> dict[str, int]:
    """Return per-tag byte sizes from Ollama's fixed loopback tags endpoint.

    Ollama owns manifests and blobs, so scanning its model-store directory would
    over-count shared blobs and make every registry row look like the entire
    store.  The local tags API supplies the model/tag size that Ollama itself
    reports.  A missing or unavailable service is intentionally represented by
    an empty result rather than a guessed filesystem total.
    """

    request = Request(_OLLAMA_TAGS_URL, method="GET")
    try:
        with urlopen(request, timeout=_OLLAMA_TIMEOUT_SECONDS) as response:  # noqa: S310 - fixed loopback endpoint
            payload = json.load(response)
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return {}
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return {}
    result: dict[str, int] = {}
    for model in models:
        if not isinstance(model, dict):
            continue
        name = str(model.get("name") or model.get("model") or "").strip()
        size = model.get("size")
        if not name or isinstance(size, bool):
            continue
        try:
            size_bytes = int(size)
        except (TypeError, ValueError):
            continue
        if size_bytes >= 0:
            result[name] = size_bytes
    return result


def invalidate_model_cache() -> None:
    """Discard the read-only model summary cache after an external model change."""

    global _model_cache
    with _cache_lock:
        _model_cache = None


def _legacy_records() -> list[dict[str, Any]]:
    data_root, _models, _environments, _runtime, _cache, _output, _temp, _logs = _managed_roots()
    path = data_root / "Config" / "layout_migration.local.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return []
    records: list[dict[str, Any]] = []
    for entry in value.get("entries", []):
        if not isinstance(entry, dict):
            continue
        records.append({
            "component": entry.get("component"),
            "classification": entry.get("classification") or entry.get("type") or "UNKNOWN",
            "status": entry.get("status", "unknown"),
            "confidence": entry.get("confidence", "unknown"),
            "cleanup_allowed": bool(entry.get("cleanup_allowed", False)),
            "managed": entry.get("status") in {"verified", "external_managed", "external_system_app", "not_installed"},
        })
    return records


def storage_summary(*, force: bool = False) -> dict[str, Any]:
    global _size_cache
    now = time.monotonic()
    with _cache_lock:
        if not force and _size_cache and now - _size_cache[0] < _CACHE_SECONDS:
            return _size_cache[1]

    data_root, model_root, environments_root, runtime_root, cache_root, output_root, temp_root, log_root = _managed_roots()
    usage = os.statvfs(data_root) if hasattr(os, "statvfs") else None
    if usage is not None:
        total = usage.f_blocks * usage.f_frsize
        free = usage.f_bavail * usage.f_frsize
    else:
        total, _used, free = shutil.disk_usage(data_root)
    roots = {
        "Models": model_root,
        "Environments": environments_root,
        "Runtime": runtime_root,
        "Cache": cache_root,
        "Output": output_root,
        "Temp": temp_root,
        "Logs": log_root,
    }
    areas = {name: _bytes_record(_directory_size(path)) for name, path in roots.items()}
    legacy = _legacy_records()
    volumes = _volume_projection()
    result = {
        "status": "completed",
        "disk": {
            "total_bytes": total,
            "free_bytes": free,
            "used_bytes": max(0, total - free),
            "free_gb": round(free / (1024**3), 3),
            "low_space": free < _LOW_SPACE_BYTES,
        },
        "volumes": volumes,
        "volume_projection": _volume_projection_payload(volumes),
        "areas": areas,
        "legacy": legacy,
        "legacy_counts": {
            "total": len(legacy),
            "cleanup_candidates": sum(1 for item in legacy if item["cleanup_allowed"]),
            "unverified": sum(1 for item in legacy if not item["managed"]),
        },
        "canonical_root": "LocalAIHub",
        "data_location_class": "persistent_configured" if data_root != get_paths().app_root else "app_root",
    }
    with _cache_lock:
        _size_cache = (now, result)
    return result


def model_summary(*, force: bool = False) -> list[dict[str, Any]]:
    global _model_cache
    now = time.monotonic()
    with _cache_lock:
        if not force and _model_cache and now - _model_cache[0] < _CACHE_SECONDS:
            return [dict(item) for item in _model_cache[1]]
    _data_root, model_root, _environments, _runtime, _cache, _output, _temp, _logs = _managed_roots()
    value = load_json("model_registry.json", {})
    items = [item for item in value.get("models", []) if isinstance(item, dict)]
    ollama_sizes = _ollama_tag_sizes() if any(_is_ollama_model(item) for item in items) else {}
    result: list[dict[str, Any]] = []
    for item in items:
        is_ollama = _is_ollama_model(item)
        model_name = str(item.get("model_name") or item.get("id") or "")
        if is_ollama:
            metadata_size = ollama_sizes.get(model_name)
            installed = metadata_size is not None
            location = "Ollama-managed model store"
            size = metadata_size or 0
            size_source = "ollama_api_tags" if installed else "ollama_api_tags_unavailable"
        else:
            local_path = str(item.get("local_path") or "")
            if local_path.startswith("${"):
                location = "not configured"
                installed = False
                size = 0
            else:
                path = Path(os.path.expandvars(local_path))
                installed = path.exists()
                location = "managed model store" if path.is_relative_to(model_root) else "external managed"
                size = _directory_size(path) if installed else 0
            size_source = "filesystem"
        result.append({
            "id": item.get("id"),
            "model_name": model_name,
            "engine": item.get("engine"),
            "version": item.get("version"),
            "installed": installed,
            "location": location,
            "size": _bytes_record(size),
            "size_source": size_source,
            "load_policy": "on_demand",
        })
    with _cache_lock:
        _model_cache = (now, [dict(item) for item in result])
    return result
