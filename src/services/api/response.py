"""Small transport response value used by the explicit API router."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ApiResponse:
    """A JSON response before the HTTP handler serializes it."""

    status: int
    payload: Any
    headers: dict[str, str] = field(default_factory=dict)


__all__ = ["ApiResponse"]
