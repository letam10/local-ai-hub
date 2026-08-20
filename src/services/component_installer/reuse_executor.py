"""Explicit receipt reconstruction for an already-managed installation.

Reuse never copies or downloads bytes.  It only records a catalog-bound
receipt after every expected leaf is revalidated under the fixed managed root.
This keeps existing owner installations usable when their upstream source is
gone, while leaving malformed, partial or reparse-backed state for review.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import os
from pathlib import Path
import stat
import time
from typing import Any

from src.platform.paths import HubPaths
from .receipts import source_identity, write_component_receipt


def _is_reparse(path: Path) -> bool:
    try:
        if stat.S_ISLNK(path.lstat().st_mode):
            return True
    except FileNotFoundError:
        return False
    except OSError:
        return True
    if os.name == "nt":
        try:
            import ctypes
            attrs = int(ctypes.windll.kernel32.GetFileAttributesW(str(path))) & 0xFFFFFFFF
            return attrs != 0xFFFFFFFF and bool(attrs & 0x400)
        except (AttributeError, OSError):
            return True
    return False


def _safe_leaf(root: Path, relative: str) -> Path | None:
    try:
        lexical_root = root.absolute()
        candidate = (lexical_root / Path(relative)).absolute()
        candidate.relative_to(lexical_root)
    except (OSError, ValueError):
        return None
    current = candidate
    while True:
        if _is_reparse(current):
            return None
        if current == lexical_root:
            break
        if current.parent == current:
            return None
        current = current.parent
    try:
        candidate.resolve().relative_to(lexical_root.resolve())
    except (OSError, ValueError):
        return None
    return candidate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ExistingInstallReuseExecutor:
    """Reconstruct a receipt only for a complete exact managed install."""

    def __init__(self, *, paths: HubPaths, manager: Any) -> None:
        self.paths = paths
        self.manager = manager

    def _record(self, component_id: str, component_type: str) -> Mapping[str, Any] | None:
        try:
            return self.manager._catalog_record(component_id, component_type)
        except Exception:
            return None

    def _root(self, component_id: str, component_type: str, record: Mapping[str, Any]) -> Path:
        if component_type == "model":
            return self.paths.models_root / component_id
        if record.get("root_class") == "environments_root":
            return self.paths.environments_root
        return self.paths.runtime_root

    def apply(self, plan: Mapping[str, Any], *, confirmed: bool) -> dict[str, Any]:
        if not confirmed:
            return {"status": "waiting_confirmation", "execution": "not_run", "dry_run": True}
        component_id = plan.get("component_id")
        component_type = plan.get("component_type")
        if not isinstance(component_id, str) or component_type not in {"model", "runtime"}:
            return {"status": "error", "code": "reuse_plan_invalid", "execution": "not_run"}
        record = self._record(component_id, str(component_type))
        if not isinstance(record, Mapping):
            return {"status": "error", "code": "unknown_component", "execution": "not_run"}
        root = self._root(component_id, str(component_type), record)
        leaves = record.get("files") if component_type == "model" else record.get("required_leaves")
        if not isinstance(leaves, list) or not leaves:
            return {"status": "unavailable", "code": "catalog_leaves_missing", "execution": "not_run"}
        verified: list[dict[str, Any]] = []
        for item in leaves:
            if component_type == "model":
                if not isinstance(item, Mapping):
                    return {"status": "failed", "code": "catalog_leaf_invalid", "execution": "not_run"}
                relative = item.get("relative_path")
                expected_size = item.get("size_bytes")
                expected_hash = item.get("sha256")
            else:
                relative = item
                expected_size = None
                expected_hash = None
            if not isinstance(relative, str):
                return {"status": "failed", "code": "catalog_leaf_invalid", "execution": "not_run"}
            target = _safe_leaf(root, relative)
            if target is None or not target.is_file() or _is_reparse(target):
                return {"status": "unavailable", "code": "existing_install_incomplete", "execution": "not_run"}
            actual_size = int(target.stat().st_size)
            if isinstance(expected_size, int) and expected_size > 0 and actual_size != expected_size:
                return {"status": "conflict", "code": "existing_install_size_mismatch", "execution": "not_run"}
            digest = _sha256(target) if actual_size <= 64 * 1024 * 1024 else None
            if isinstance(expected_hash, str) and digest != expected_hash.lower():
                return {"status": "conflict", "code": "existing_install_checksum_mismatch", "execution": "not_run"}
            verified.append({"relative_leaf": relative.replace("\\", "/"), "size_bytes": actual_size, "sha256": digest, "size_verified": bool(isinstance(expected_size, int) and expected_size > 0 and actual_size == expected_size) or digest is not None})
        receipt = {
            "component_id": component_id,
            "component_type": component_type,
            "bundle_revision": str(record.get("revision") or "unknown"),
            "source": "existing_install_reuse",
            "source_identity": source_identity(record),
            "files": verified,
            "installed_size_bytes": sum(item["size_bytes"] for item in verified),
            "installed_at": int(time.time()),
            "verified_at": int(time.time()),
            "state": "INSTALLED_UNVERIFIED",
            "operational": False,
            "catalog_fingerprint": str(plan.get("catalog_fingerprint") or "")[:128],
        }
        try:
            write_component_receipt(self.paths.config_root, component_id, receipt)
        except OSError:
            return {"status": "failed", "code": "receipt_write_failed", "execution": "not_run"}
        return {"status": "completed", "execution": "completed", "dry_run": False, "component_id": component_id, "state": "INSTALLED_UNVERIFIED", "source": "existing_install_reuse", "installed_size_bytes": receipt["installed_size_bytes"], "next_action": "Run the component-specific bounded verification before operational promotion."}


__all__ = ["ExistingInstallReuseExecutor"]
