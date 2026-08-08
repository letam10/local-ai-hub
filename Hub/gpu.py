from __future__ import annotations

import csv
import io
import shutil
import subprocess
from typing import Any

from .jobs import active_heavy_jobs


def _nvidia_smi() -> str | None:
    return shutil.which("nvidia-smi") or r"C:\Windows\System32\nvidia-smi.exe"


def query_gpu() -> dict[str, Any]:
    executable = _nvidia_smi()
    if not executable:
        return {"available": False, "reason": "nvidia-smi not found"}
    command = [
        executable,
        "--query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu,driver_version",
        "--format=csv,noheader,nounits",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"available": False, "reason": str(exc)}
    if result.returncode != 0:
        return {"available": False, "reason": result.stderr.strip() or "nvidia-smi failed"}
    rows = list(csv.reader(io.StringIO(result.stdout)))
    if not rows:
        return {"available": False, "reason": "no GPU rows returned"}
    fields = ["name", "memory_total_mib", "memory_used_mib", "memory_free_mib", "utilization_percent", "temperature_c", "driver_version"]
    values = [item.strip() for item in rows[0]]
    data: dict[str, Any] = {"available": True}
    for index, field in enumerate(fields):
        data[field] = values[index] if index < len(values) else None
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
