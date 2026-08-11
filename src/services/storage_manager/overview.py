"""Read-only storage and model overview for the unified UI."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

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


_CACHE_SECONDS = 120.0
_OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"
_OLLAMA_TIMEOUT_SECONDS = 0.5
_cache_lock = threading.Lock()
_size_cache: tuple[float, dict[str, Any]] | None = None
_model_cache: tuple[float, list[dict[str, Any]]] | None = None


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


def storage_summary(*, force: bool = False) -> dict[str, Any]:
    global _size_cache
    now = time.monotonic()
    with _cache_lock:
        if not force and _size_cache and now - _size_cache[0] < _CACHE_SECONDS:
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


def model_summary(*, force: bool = False) -> list[dict[str, Any]]:
    global _model_cache
    now = time.monotonic()
    with _cache_lock:
        if not force and _model_cache and now - _model_cache[0] < _CACHE_SECONDS:
            return [dict(item) for item in _model_cache[1]]
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
                location = "managed model store" if path.is_relative_to(MODEL_ROOT) else "external managed"
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
