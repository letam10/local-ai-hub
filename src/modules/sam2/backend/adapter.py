from __future__ import annotations

from src.shared.utils.adapter_common import unavailable


def capability() -> dict:
    return unavailable("sam2", "Existing SAM 2 is currently exposed as a GUI; direct API/CLI contract is not yet verified.")
