"""Bounded identity contract for one Local AI Hub desktop/API installation."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any


PRODUCT_ID = "LocalAIHub"
APP_USER_MODEL_ID = "LocalAIHub.Desktop"
API_PROTOCOL_VERSION = "v8-api.v1"
PROCESS_OWNER = "localaihub.api"
MAX_IDENTITY_LENGTH = 64


def _normalized_scope(value: str | os.PathLike[str] | None) -> str:
    if value is None:
        return "<unset>"
    try:
        return os.path.normcase(str(Path(value).absolute()))
    except (OSError, TypeError, ValueError):
        return "<invalid>"


def installation_id(
    *,
    installation_root: str | os.PathLike[str] | None = None,
    app_root: str | os.PathLike[str] | None = None,
    data_root: str | os.PathLike[str] | None = None,
) -> str:
    """Return an opaque stable installation identity without exposing paths."""

    install = installation_root if installation_root is not None else os.environ.get("LOCALAIHUB_INSTALL_ROOT")
    app = install if install is not None else (app_root if app_root is not None else os.environ.get("LOCALAIHUB_APP_ROOT"))
    data = data_root if data_root is not None else os.environ.get("LOCALAIHUB_DATA_ROOT")
    payload = f"{PRODUCT_ID}\0{_normalized_scope(install or app)}\0{_normalized_scope(data)}".encode("utf-8", "replace")
    return hashlib.sha256(payload).hexdigest()[:32]


def api_identity(
    *,
    product_version: str,
    installation_root: str | os.PathLike[str] | None = None,
    app_root: str | os.PathLike[str] | None = None,
    data_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Build the finite public handshake used by desktop/API ownership checks."""

    return {
        "product_id": PRODUCT_ID,
        "product_version": str(product_version)[:32],
        "api_protocol_version": API_PROTOCOL_VERSION,
        "installation_id": installation_id(installation_root=installation_root, app_root=app_root, data_root=data_root),
        "app_user_model_id": APP_USER_MODEL_ID,
        "process_owner": PROCESS_OWNER,
    }


__all__ = ["API_PROTOCOL_VERSION", "APP_USER_MODEL_ID", "PROCESS_OWNER", "PRODUCT_ID", "api_identity", "installation_id"]
