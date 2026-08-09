from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from .common import configured_path, local_cache_root, unavailable


ROOT = Path(__file__).resolve().parents[1]


def _runtime() -> tuple[Path, Path, Path]:
    service = configured_path("paddleocr_vl", "path", "PADDLEOCR_HOME") or ROOT / "Services" / "PaddleOCR"
    python = configured_path("paddleocr_vl", "executable", "PADDLEOCR_PYTHON") or ROOT / "Environments" / "paddle-ocr" / "Scripts" / "python.exe"
    return python, service / "paddle_cli.py", service


def parse(path: str) -> dict:
    python, helper, service = _runtime()
    source = Path(os.path.expandvars(path)).expanduser()
    if not source.is_file():
        return {"status": "error", "error": f"Input file does not exist: {source}"}
    if not python.exists() or not helper.exists():
        return unavailable("paddleocr_vl", "PaddleOCR-VL helper environment is incomplete.")
    try:
        result = subprocess.run(
            [str(python), str(helper)],
            input=json.dumps({"path": str(source)}, ensure_ascii=False).encode("utf-8"),
            capture_output=True,
            timeout=600,
            check=False,
            env={
                **os.environ,
                "LOCALAIHUB_ROOT": str(ROOT),
                "PADDLEOCR_HOME": str(service),
                "PADDLE_PDX_CACHE_HOME": str(local_cache_root() / "PaddleX"),
                "FLAGS_use_cuda": "1",
                "PYTHONIOENCODING": "utf-8",
            },
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "error", "error": str(exc)}
    if result.returncode != 0:
        return {"status": "error", "error": result.stderr.decode("utf-8", errors="replace")[-6000:], "returncode": result.returncode}
    stdout = result.stdout.decode("utf-8", errors="replace")
    json_line = next((line for line in reversed(stdout.splitlines()) if line.lstrip().startswith("{")), "")
    try:
        return json.loads(json_line)
    except json.JSONDecodeError as exc:
        return {"status": "error", "error": f"PaddleOCR-VL helper returned invalid JSON: {exc}", "stdout": stdout[-2000:]}


def capability() -> dict:
    python, helper, _ = _runtime()
    return {"component": "paddleocr_vl", "status": "installed", "helper": str(helper), "environment": str(python)}
