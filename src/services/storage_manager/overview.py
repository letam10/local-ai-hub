"""Read-only storage and model overview for the unified UI."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from src.services.api.config import load_json
from src.shared.paths.registry import (
    CACHE_ROOT,
    LOG_ROOT,
    MODEL_ROOT,
    OUTPUT_ROOT,
    ROOT,
    RUNTIME_ROOT,
    TEMP_ROOT,
)


_CACHE_SECONDS = 10.0
_cache_lock = threading.Lock()
_size_cache: tuple[float, dict[str, Any]] | None = None


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


def _legacy_records() -> list[dict[str, Any]]:
    path = ROOT / "Config" / "layout_migration.local.json"
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


def storage_summary() -> dict[str, Any]:
    global _size_cache
    now = time.monotonic()
    with _cache_lock:
        if _size_cache and now - _size_cache[0] < _CACHE_SECONDS:
            return _size_cache[1]

    usage = os.statvfs(ROOT) if hasattr(os, "statvfs") else None
    if usage is not None:
        total = usage.f_blocks * usage.f_frsize
        free = usage.f_bavail * usage.f_frsize
    else:
        import shutil

        total, _used, free = shutil.disk_usage(ROOT)
    roots = {
        "Models": MODEL_ROOT,
        "Environments": ROOT / "Environments",
        "Runtime": RUNTIME_ROOT,
        "Cache": CACHE_ROOT,
        "Output": OUTPUT_ROOT,
        "Temp": TEMP_ROOT,
        "Logs": LOG_ROOT,
    }
    areas = {name: _bytes_record(_directory_size(path)) for name, path in roots.items()}
    legacy = _legacy_records()
    result = {
        "status": "completed",
        "disk": {
            "total_bytes": total,
            "free_bytes": free,
            "used_bytes": max(0, total - free),
            "free_gb": round(free / (1024**3), 3),
            "low_space": free < 20 * 1024**3,
        },
        "areas": areas,
        "legacy": legacy,
        "legacy_counts": {
            "total": len(legacy),
            "cleanup_candidates": sum(1 for item in legacy if item["cleanup_allowed"]),
            "unverified": sum(1 for item in legacy if not item["managed"]),
        },
        "canonical_root": "LocalAIHub",
    }
    with _cache_lock:
        _size_cache = (now, result)
    return result


def model_summary() -> list[dict[str, Any]]:
    value = load_json("model_registry.json", {})
    result: list[dict[str, Any]] = []
    for item in value.get("models", []):
        if not isinstance(item, dict):
            continue
        local_path = str(item.get("local_path") or "")
        if local_path.startswith("${"):
            location = "not configured"
            installed = False
            size = 0
        else:
            path = Path(os.path.expandvars(local_path))
            installed = path.exists()
            location = "managed model store" if path.is_relative_to(MODEL_ROOT) else "external managed"
            size = _directory_size(path) if installed else 0
        result.append({
            "id": item.get("id"),
            "model_name": item.get("model_name") or item.get("id"),
            "engine": item.get("engine"),
            "version": item.get("version"),
            "installed": installed,
            "location": location,
            "size": _bytes_record(size),
            "load_policy": "on_demand",
        })
    return result
