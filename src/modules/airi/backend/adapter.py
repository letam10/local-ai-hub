from __future__ import annotations

from pathlib import Path

from src.shared.utils.adapter_common import configured_path, unavailable


def capability() -> dict:
    executable = configured_path("airi", "executable", "AIRI_EXECUTABLE")
    if executable is None or not executable.exists():
        return unavailable("airi", "AIRI executable is missing; configure AIRI_EXECUTABLE or Config/components.json locally.")
    return {"component": "airi", "status": "installed", "executable": str(executable)}
