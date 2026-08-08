from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from .common import configured_path, unavailable


ROOT = Path(__file__).resolve().parents[1]


def _runtime() -> tuple[Path, Path, Path]:
    service = configured_path("groundingdino", "path", "GROUNDINGDINO_HOME") or ROOT / "Services" / "GroundingDINO"
    python = configured_path("groundingdino", "executable", "GROUNDINGDINO_PYTHON") or ROOT / "Environments" / "groundingdino" / "Scripts" / "python.exe"
    return python, service / "ground_cli.py", service


def ground(path: str, prompt: str, box_threshold: float = 0.35, text_threshold: float = 0.25) -> dict:
    python, helper, service = _runtime()
    source = Path(os.path.expandvars(path)).expanduser()
    if not source.is_file():
        return {"status": "error", "error": f"Input file does not exist: {source}"}
    if not python.exists() or not helper.exists():
        return unavailable("groundingdino", "Grounding DINO helper environment is incomplete.")
    payload = {"path": str(source), "prompt": prompt, "box_threshold": float(box_threshold), "text_threshold": float(text_threshold)}
    try:
        result = subprocess.run(
            [str(python), str(helper)],
            input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            capture_output=True,
            timeout=240,
            check=False,
            env={**os.environ, "LOCALAIHUB_ROOT": str(ROOT), "GROUNDINGDINO_HOME": str(service), "PYTHONIOENCODING": "utf-8"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "error", "error": str(exc)}
    if result.returncode != 0:
        return {"status": "error", "error": result.stderr.decode("utf-8", errors="replace")[-4000:], "returncode": result.returncode}
    stdout = result.stdout.decode("utf-8", errors="replace")
    json_line = next((line for line in reversed(stdout.splitlines()) if line.lstrip().startswith("{")), "")
    try:
        return json.loads(json_line)
    except json.JSONDecodeError as exc:
        return {"status": "error", "error": f"Grounding DINO helper returned invalid JSON: {exc}", "stdout": stdout[-2000:]}


def capability() -> dict:
    python, helper, _ = _runtime()
    return {"component": "groundingdino", "status": "installed", "helper": str(helper), "environment": str(python)}
