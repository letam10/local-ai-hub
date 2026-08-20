"""Candidate staging/activation and rollback for catalog-owned model updates.

Only server-owned catalog metadata can name a candidate.  Client payloads never
contain paths, URLs or files.  This executor supports a bounded single-model
leaf candidate and refuses when the catalog has not supplied an immutable
candidate graph; runtimes/dependency bundles remain explicit future plans.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import time
from typing import Any

from src.platform.paths import HubPaths
from src.services.component_installer.downloader import DownloadError, TrustedDownloader
from src.services.component_installer.receipts import read_receipts, source_identity, write_component_receipt


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
        root = root.absolute()
        candidate = (root / Path(relative)).absolute()
        candidate.relative_to(root)
    except (OSError, ValueError):
        return None
    current = candidate
    while True:
        if _is_reparse(current):
            return None
        if current == root:
            break
        if current.parent == current:
            return None
        current = current.parent
    try:
        candidate.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return candidate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clone_tree_preserving_files(source_root: Path, destination_root: Path) -> bool:
    """Clone an active component slot without redownloading unchanged bytes.

    Ordinary files are hard-linked where the filesystem permits it; a copy is
    the bounded fallback.  Any reparse entry fails closed instead of following
    an external target.  The helper is only used after an explicit update plan
    and never scans the drive or a user-selected path.
    """

    if not source_root.is_dir() or _is_reparse(source_root):
        return False
    try:
        for item in source_root.rglob("*"):
            relative = item.relative_to(source_root)
            target = destination_root / relative
            if _is_reparse(item):
                return False
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not item.is_file():
                return False
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(item, target)
            except OSError:
                shutil.copy2(item, target)
        return True
    except (OSError, ValueError):
        return False


class ComponentUpdateExecutor:
    """Apply one immutable model candidate and preserve a rollback version."""

    def __init__(self, *, paths: HubPaths) -> None:
        self.paths = paths

    def _candidate_file(self, candidate: Mapping[str, Any]) -> tuple[Path | None, str | None, int | None, str | None]:
        relative = candidate.get("relative_path")
        if not isinstance(relative, str):
            return None, None, None, None
        expected_size = candidate.get("size_bytes") if isinstance(candidate.get("size_bytes"), int) else None
        expected_hash = candidate.get("sha256") if isinstance(candidate.get("sha256"), str) else None
        staged_relative = candidate.get("staged_relative_path")
        if isinstance(staged_relative, str):
            source = _safe_leaf(self.paths.temp_root, staged_relative)
            return source, relative, expected_size, expected_hash
        return None, relative, expected_size, expected_hash

    def apply(self, plan: Mapping[str, Any], *, confirmed: bool) -> dict[str, Any]:
        if not confirmed:
            return {"status": "waiting_confirmation", "execution": "not_run", "dry_run": True}
        if plan.get("component_type") != "model":
            return {"status": "unavailable", "code": "runtime_candidate_executor_required", "execution": "not_run"}
        candidate = plan.get("update_candidate")
        if not isinstance(candidate, Mapping):
            return {"status": "unavailable", "code": "update_candidate_unavailable", "execution": "not_run", "next_action": "Use Manual Import or wait for a catalog-pinned candidate."}
        component_id = plan.get("component_id")
        if not isinstance(component_id, str):
            return {"status": "error", "code": "update_plan_invalid", "execution": "not_run"}
        source, relative, expected_size, expected_hash = self._candidate_file(candidate)
        if source is None or relative is None or not source.is_file() or _is_reparse(source):
            return {"status": "unavailable", "code": "update_candidate_unavailable", "execution": "not_run"}
        if expected_size is None or expected_size <= 0 or source.stat().st_size != expected_size:
            return {"status": "failed", "code": "update_candidate_size_mismatch", "execution": "not_run"}
        actual_hash = _sha256(source)
        if not isinstance(expected_hash, str) or len(expected_hash) != 64 or actual_hash != expected_hash.lower():
            return {"status": "failed", "code": "update_candidate_checksum_mismatch", "execution": "not_run"}
        root = self.paths.models_root / component_id
        target = _safe_leaf(root, relative)
        if target is None:
            return {"status": "failed", "code": "unsafe_update_target", "execution": "not_run"}
        candidate_root = self.paths.temp_root / "component-update" / f"{component_id}-{int(time.time() * 1000)}"
        if not _safe_leaf(self.paths.temp_root, candidate_root.relative_to(self.paths.temp_root).as_posix()):
            return {"status": "failed", "code": "unsafe_update_stage", "execution": "not_run"}
        candidate_root.mkdir(parents=True, exist_ok=True)
        previous_root: Path | None = None
        try:
            if root.exists() and not _clone_tree_preserving_files(root, candidate_root):
                return {"status": "failed", "code": "update_preserve_existing_failed", "execution": "not_run"}
            staged_target = candidate_root / Path(relative)
            staged_target.parent.mkdir(parents=True, exist_ok=True)
            if staged_target.exists() or staged_target.is_symlink():
                if _is_reparse(staged_target):
                    return {"status": "failed", "code": "update_target_reparse", "execution": "not_run"}
                staged_target.unlink()
            shutil.copy2(source, staged_target)
            if _sha256(staged_target) != actual_hash:
                return {"status": "failed", "code": "update_stage_checksum_mismatch", "execution": "not_run"}
            root.parent.mkdir(parents=True, exist_ok=True)
            if root.exists() and _is_reparse(root):
                return {"status": "failed", "code": "update_root_reparse", "execution": "not_run"}
            if root.exists():
                previous_root = self.paths.models_root / ".versions" / component_id / str(plan.get("installed_revision") or "previous")
                if previous_root.exists() or _is_reparse(previous_root):
                    return {"status": "conflict", "code": "rollback_slot_exists", "execution": "not_run"}
                previous_root.parent.mkdir(parents=True, exist_ok=True)
                os.rename(root, previous_root)
            try:
                os.rename(candidate_root, root)
            except OSError:
                if previous_root is not None and previous_root.exists() and not root.exists():
                    os.rename(previous_root, root)
                return {"status": "failed", "code": "update_activation_failed", "execution": "not_run"}
            receipt = {
                "component_id": component_id,
                "component_type": "model",
                "bundle_revision": str(plan.get("latest_supported_revision") or "unknown"),
                "source": "catalog_candidate",
                "source_identity": str(candidate.get("source_identity") or "")[:256],
                "installed_at": int(time.time()),
                "verified_at": None,
                "state": "INSTALLED_UNVERIFIED",
                "operational": False,
                "previous_version": str(plan.get("installed_revision") or "unknown"),
                "rollback_candidate": True,
                "catalog_fingerprint": plan.get("catalog_fingerprint"),
                "files": [{"relative_leaf": relative, "size_bytes": expected_size, "sha256": actual_hash}],
            }
            write_component_receipt(self.paths.config_root, component_id, receipt)
            return {"status": "completed", "execution": "completed", "dry_run": False, "component_id": component_id, "state": "INSTALLED_UNVERIFIED", "rollback_available": previous_root is not None, "next_action": "Run bounded verification before operational promotion."}
        except (OSError, ValueError, DownloadError):
            if previous_root is not None and previous_root.exists() and not root.exists():
                try:
                    os.rename(previous_root, root)
                except OSError:
                    pass
            return {"status": "failed", "code": "update_activation_failed", "execution": "not_run"}
        finally:
            if candidate_root.exists():
                shutil.rmtree(candidate_root, ignore_errors=True)

    def rollback(self, component_id: str) -> dict[str, Any]:
        receipts = read_receipts(self.paths.config_root)
        receipt = receipts["records"].get(component_id)
        if not isinstance(receipt, Mapping) or not receipt.get("rollback_candidate"):
            return {"status": "unavailable", "code": "rollback_unavailable", "execution": "not_run"}
        previous = self.paths.models_root / ".versions" / component_id / str(receipt.get("previous_version") or "previous")
        root = self.paths.models_root / component_id
        if not previous.is_dir() or _is_reparse(previous) or root.exists() and _is_reparse(root):
            return {"status": "unavailable", "code": "rollback_candidate_missing", "execution": "not_run"}
        current_backup = self.paths.models_root / ".versions" / component_id / f"active-{int(time.time())}"
        try:
            if root.exists():
                os.rename(root, current_backup)
            os.rename(previous, root)
            receipt = dict(receipt)
            receipt["rollback_candidate"] = False
            receipt["state"] = "INSTALLED_UNVERIFIED"
            receipt["operational"] = False
            write_component_receipt(self.paths.config_root, component_id, receipt)
            return {"status": "completed", "execution": "completed", "component_id": component_id, "state": "INSTALLED_UNVERIFIED"}
        except OSError:
            if current_backup.exists() and not root.exists():
                try:
                    os.rename(current_backup, root)
                except OSError:
                    pass
            return {"status": "failed", "code": "rollback_activation_failed", "execution": "not_run"}


__all__ = ["ComponentUpdateExecutor"]
