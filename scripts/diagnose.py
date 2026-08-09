from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "Reports" / "DIAGNOSTIC_REPORT.md"


def get_json(route: str) -> dict:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8765" + route, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"status": "unavailable", "error": str(exc)}


def nvidia() -> str:
    executable = shutil.which("nvidia-smi") or r"C:\Windows\System32\nvidia-smi.exe"
    if not Path(executable).exists():
        return "nvidia-smi not found"
    result = subprocess.run([executable, "--query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu,driver_version", "--format=csv,noheader"], capture_output=True, text=True, timeout=10, check=False)
    return result.stdout.strip() or result.stderr.strip() or "no output"


def main() -> int:
    total, used, free = shutil.disk_usage(ROOT)
    health = get_json("/health")
    components = get_json("/components")
    models = get_json("/models")
    lines = [
        "# Local AI Hub — Diagnostic Report",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Host",
        "",
        f"- OS: {platform.platform()}",
        f"- Python: {platform.python_version()}",
        f"- Workspace disk free: {free / 1_073_741_824:.1f} GB of {total / 1_073_741_824:.1f} GB",
        f"- NVIDIA: `{nvidia()}`",
        "",
        "## Hub API",
        "",
        "```json",
        json.dumps(health, ensure_ascii=False, indent=2),
        "```",
        "",
        "## Components",
        "",
        "```json",
        json.dumps(components, ensure_ascii=False, indent=2),
        "```",
        "",
        "## Models",
        "",
        "```json",
        json.dumps(models, ensure_ascii=False, indent=2),
        "```",
        "",
        "Diagnostics are state checks only; this script does not benchmark or stress CPU/GPU.",
        "",
    ]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(REPORT)
    return 0 if health.get("status") == "healthy" else 1


if __name__ == "__main__":
    raise SystemExit(main())
