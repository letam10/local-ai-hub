"""Lightweight application bootstrap helpers.

Heavy model runtimes are intentionally not imported during startup.
"""

from __future__ import annotations

from src.shared.paths.registry import ensure_managed_directories


def bootstrap() -> None:
    """Create only safe, ignored working directories for the Hub."""

    ensure_managed_directories()
