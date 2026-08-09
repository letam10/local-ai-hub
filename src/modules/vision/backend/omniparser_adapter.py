from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from src.shared.utils.adapter_common import configured_path, local_cache_root, unavailable


ROOT = Path(__file__).resolve().parents[1]


def _runtime() -> tuple[Path, Path, Path]:
    service = configured_path("omniparser", "path", "OMNIPARSER_HOME") or ROOT / "Services" / "OmniParser"
    python = configured_path("omniparser", "executable", "OMNIPARSER_PYTHON") or ROOT / "Environments" / "omniparser" / "Scripts" / "python.exe"
    return python, service / "omni_cli.py", service


def parse(path: str, box_threshold: float = 0.05) -> dict:
    python, helper, service = _runtime()
    source = Path(os.path.expandvars(path)).expanduser()
    if not source.is_file():
        return {"status": "error", "error": f"Input file does not exist: {source}"}
    if not python.exists() or not helper.exists():
        return unavailable("omniparser", "OmniParser helper environment is incomplete.")
    payload = {"path": str(source), "box_threshold": float(box_threshold)}
    try:
        result = subprocess.run(
            [str(python), str(helper)],
            input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            capture_output=True,
            timeout=300,
            check=False,
            env={
                **os.environ,
                "LOCALAIHUB_ROOT": str(ROOT),
                "OMNIPARSER_HOME": str(service),
                "HF_HOME": str(local_cache_root() / "HuggingFace"),
                "HF_HUB_CACHE": str(local_cache_root() / "HuggingFace" / "hub"),
                "EASYOCR_MODULE_PATH": str(local_cache_root() / "EasyOCR"),
                "PYTHONIOENCODING": "utf-8",
            },
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "error", "error": str(exc)}
    if result.returncode != 0:
        return {"status": "error", "error": result.stderr.decode("utf-8", errors="replace")[-5000:], "returncode": result.returncode}
    stdout = result.stdout.decode("utf-8", errors="replace")
    json_line = next((line for line in reversed(stdout.splitlines()) if line.lstrip().startswith("{")), "")
    try:
        return json.loads(json_line)
    except json.JSONDecodeError as exc:
        return {"status": "error", "error": f"OmniParser helper returned invalid JSON: {exc}", "stdout": stdout[-2000:]}


def capability() -> dict:
    python, helper, _ = _runtime()
    return {"component": "omniparser", "status": "installed", "helper": str(helper), "environment": str(python)}
