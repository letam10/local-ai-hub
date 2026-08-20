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

from src.platform.paths import HubPaths, resolve_component_root
from .receipts import CatalogBindingContext, ReceiptError, write_component_receipt
from .verification import DeepComponentVerifier, stream_sha256


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
    """Compatibility wrapper; deep verification always streams every size."""

    return stream_sha256(path)


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
        root_class = "models_root" if component_type == "model" else record.get("root_class")
        return resolve_component_root(self.paths, component_id, component_type, root_class, require_exists=False)

    def apply(self, plan: Mapping[str, Any], *, confirmed: bool, catalog_binding: CatalogBindingContext | Mapping[str, Any] | None = None) -> dict[str, Any]:
        if not confirmed:
            return {"status": "waiting_confirmation", "execution": "not_run", "dry_run": True}
        component_id = plan.get("component_id")
        component_type = plan.get("component_type")
        if not isinstance(component_id, str) or component_type not in {"model", "runtime"}:
            return {"status": "error", "code": "reuse_plan_invalid", "execution": "not_run"}
        record = self._record(component_id, str(component_type))
        if not isinstance(record, Mapping):
            return {"status": "error", "code": "unknown_component", "execution": "not_run"}
        resolver = getattr(self.manager, "_fresh_binding_for_plan", None)
        if not callable(resolver):
            return {"status": "unavailable", "code": "stale_binding", "execution": "not_run", "dry_run": True, "next_action": "Create a fresh server-owned component plan."}
        current_binding, binding_error = resolver(plan, catalog_binding=catalog_binding)
        if current_binding is None:
            return {"status": "conflict", "code": binding_error or "stale_binding", "execution": "not_run", "dry_run": True, "next_action": "Create a fresh server-owned component plan."}
        result = DeepComponentVerifier().verify(
            paths=self.paths,
            component_id=component_id,
            component_type=str(component_type),
            record=record,
            catalog_fingerprint=plan.get("catalog_fingerprint") if isinstance(plan.get("catalog_fingerprint"), str) else None,
            catalog_binding=current_binding,
            source="existing_install_reuse",
        )
        if result.get("status") != "completed":
            failure = {key: result[key] for key in ("status", "code", "execution", "dry_run", "reason", "next_action") if key in result}
            if failure.get("code") == "component_leaf_unavailable":
                failure["code"] = "existing_install_incomplete"
            return failure
        try:
            write_component_receipt(
                self.paths.config_root,
                component_id,
                result["receipt"],
                catalog_binding=current_binding,
            )
        except (OSError, ReceiptError, KeyError, TypeError):
            return {"status": "failed", "code": "receipt_write_failed", "execution": "not_run"}
        return {
            "status": "completed", "execution": "not_run", "dry_run": True,
            "component_id": component_id, "state": result.get("state", "INSTALLED_UNVERIFIED"),
            "source": "existing_install_reuse",
            "leaves": result.get("leaves", []),
            "next_action": result.get("next_action", "Run the component-specific bounded verification before operational promotion."),
            "operational": False,
        }


__all__ = ["ExistingInstallReuseExecutor"]
