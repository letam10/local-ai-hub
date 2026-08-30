"""Small in-process cache for immutable, read-only metadata projections.

The cache never stores job, scheduler, worker, or execution projections.  Its
caller supplies an input fingerprint on every lookup, so a new catalog or
server-owned metadata snapshot invalidates the entry immediately even inside
the short time-to-live window.
"""

from __future__ import annotations

from collections.abc import Callable
import threading
import time
from typing import Any, TypeVar


T = TypeVar("T")


class BoundedProjectionCache:
    """Revision/fingerprint-aware cache with a deliberately tiny lifetime."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: dict[str, tuple[str, float, Any]] = {}

    def get_or_build(
        self,
        key: str,
        fingerprint: str,
        builder: Callable[[], T],
        *,
        ttl_seconds: float = 1.0,
        cacheable: bool = True,
    ) -> T:
        now = time.monotonic()
        if cacheable:
            with self._lock:
                cached = self._entries.get(key)
                if cached is not None and cached[0] == fingerprint and now - cached[1] <= ttl_seconds:
                    return cached[2]
        value = builder()
        if cacheable:
            with self._lock:
                self._entries[key] = (fingerprint, now, value)
        return value

    def invalidate(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._entries.clear()
            else:
                self._entries.pop(key, None)


__all__ = ["BoundedProjectionCache"]
