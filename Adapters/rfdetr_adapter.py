from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from .common import unavailable


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / "Environments" / "vision-torch" / "Scripts" / "python.exe"
HELPER = ROOT / "Services" / "RF-DETR" / "detect_cli.py"


def detect(path: str, threshold: float = 0.5) -> dict:
    source = Path(os.path.expandvars(path)).expanduser()
    if not source.is_file():
        return {"status": "error", "error": f"Input file does not exist: {source}"}
    if not PYTHON.exists() or not HELPER.exists():
        return unavailable("rfdetr", "RF-DETR helper environment is incomplete.")
    payload = {"path": str(source), "threshold": float(threshold)}
    try:
        result = subprocess.run(
            [str(PYTHON), str(HELPER)],
            input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            capture_output=True,
            timeout=180,
            check=False,
            env={**os.environ, "RF_HOME": str(ROOT / "Models" / "Vision" / "RF-DETR"), "PYTHONIOENCODING": "utf-8"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "error", "error": str(exc)}
    if result.returncode != 0:
        return {"status": "error", "error": result.stderr.decode("utf-8", errors="replace")[-4000:], "returncode": result.returncode}
    stdout = result.stdout.decode("utf-8", errors="replace")
    json_line = next((line for line in reversed(stdout.splitlines()) if line.lstrip().startswith("{")), "")
    try:
        value = json.loads(json_line)
    except json.JSONDecodeError as exc:
        return {"status": "error", "error": f"RF-DETR helper returned invalid JSON: {exc}", "stderr": result.stderr.decode("utf-8", errors="replace")[-2000:]}
    return value


def capability() -> dict:
    return {"component": "rfdetr", "status": "installed", "helper": str(HELPER), "environment": str(PYTHON)}
