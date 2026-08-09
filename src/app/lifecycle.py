"""Application lifecycle hooks kept separate from the UI shell."""

from __future__ import annotations

from typing import Callable


def run_with_lifecycle(start: Callable[[], int]) -> int:
    """Run a lightweight entry point with a stable lifecycle boundary."""

    return int(start())
