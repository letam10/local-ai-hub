"""Private provenance carriers for server-owned static metadata.

Raw JSON and validator result mappings are intentionally not accepted by the
diagnostic boundary.  Only trusted fixed-root loaders in this package can
create the carrier, using the private token below.  The carrier exposes no
public payload accessor and its representation contains only a kind and
fingerprint.
"""

from __future__ import annotations

import copy
import re
from typing import Any


_PROVENANCE_TOKEN = object()
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")


class _ServerOwnedValue:
    __slots__ = ("_kind", "_payload", "_fingerprint", "_token")

    def __init__(self, kind: str, payload: dict[str, Any], fingerprint: str, token: object) -> None:
        if token is not _PROVENANCE_TOKEN:
            raise TypeError("server-owned values are created by trusted loaders only")
        self._kind = kind
        self._payload = copy.deepcopy(payload)
        self._fingerprint = fingerprint
        self._token = token

    def __repr__(self) -> str:
        return f"_ServerOwnedValue(kind={self._kind!r}, fingerprint={self._fingerprint!r})"


def _issue_server_owned(kind: str, payload: dict[str, Any], fingerprint: str) -> _ServerOwnedValue:
    if not isinstance(kind, str) or not kind or not isinstance(payload, dict) or not isinstance(fingerprint, str) or _FINGERPRINT_RE.fullmatch(fingerprint) is None:
        raise TypeError("invalid server-owned carrier")
    return _ServerOwnedValue(kind, payload, fingerprint, _PROVENANCE_TOKEN)


def _owned_payload(value: object, kind: str) -> tuple[dict[str, Any], str] | None:
    if type(value) is not _ServerOwnedValue:
        return None
    if value._token is not _PROVENANCE_TOKEN or value._kind != kind or _FINGERPRINT_RE.fullmatch(value._fingerprint) is None:
        return None
    try:
        payload = copy.deepcopy(value._payload)
    except (copy.Error, TypeError, RecursionError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload, value._fingerprint


def _is_owned(value: object, kind: str) -> bool:
    return _owned_payload(value, kind) is not None


__all__ = []
