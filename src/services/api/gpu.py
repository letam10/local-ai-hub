from __future__ import annotations

import csv
import io
import shutil
import subprocess
import threading
import time
from typing import Any

from src.services.process_manager.windows import run_hidden
from .jobs import active_heavy_jobs


_GPU_CACHE_SECONDS = 2.0
_gpu_cache: tuple[float, dict[str, Any]] | None = None
_gpu_lock = threading.RLock()


def _nvidia_smi() -> str | None:
    return shutil.which("nvidia-smi") or r"C:\Windows\System32\nvidia-smi.exe"


def query_gpu(*, force: bool = False) -> dict[str, Any]:
    """Return a short-lived cached GPU snapshot without spawning visible cmd windows."""

    global _gpu_cache
    now = time.monotonic()
    with _gpu_lock:
        if not force and _gpu_cache and now - _gpu_cache[0] < _GPU_CACHE_SECONDS:
            return dict(_gpu_cache[1])

    executable = _nvidia_smi()
    if not executable:
        value = {"available": False, "reason": "nvidia-smi not found"}
        with _gpu_lock:
            _gpu_cache = (now, value)
        return dict(value)
    command = [
        executable,
        "--query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu,driver_version",
        "--format=csv,noheader,nounits",
    ]
    try:
        result = run_hidden(command, capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        value = {"available": False, "reason": str(exc)}
        with _gpu_lock:
            _gpu_cache = (now, value)
        return dict(value)
    if result.returncode != 0:
        value = {"available": False, "reason": result.stderr.strip() or "nvidia-smi failed"}
        with _gpu_lock:
            _gpu_cache = (now, value)
        return dict(value)
    rows = list(csv.reader(io.StringIO(result.stdout)))
    if not rows:
        value = {"available": False, "reason": "no GPU rows returned"}
        with _gpu_lock:
            _gpu_cache = (now, value)
        return dict(value)
    fields = ["name", "memory_total_mib", "memory_used_mib", "memory_free_mib", "utilization_percent", "temperature_c", "driver_version"]
    values = [item.strip() for item in rows[0]]
    data: dict[str, Any] = {"available": True}
    for index, field in enumerate(fields):
        data[field] = values[index] if index < len(values) else None
    with _gpu_lock:
        _gpu_cache = (now, dict(data))
    return data


def gpu_policy(config: dict[str, Any]) -> dict[str, Any]:
    active = active_heavy_jobs()
    limit = int(config.get("max_heavy_gpu_jobs", 1))
    return {
        "max_heavy_gpu_jobs": limit,
        "active_heavy_jobs": len(active),
        "can_start_heavy_job": len(active) < limit,
        "idle_unload_seconds": config.get("idle_unload_seconds", 600),
        "model_load_policy": config.get("model_load_policy", "on_demand"),
    }
