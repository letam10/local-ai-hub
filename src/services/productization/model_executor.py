"""Production direct-file model archive/weight executor."""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import shutil
import stat
import time
from typing import Any

from src.platform.paths import HubPaths
from src.services.component_installer.receipts import source_identity, write_component_receipt


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


class ModelArchiveExecutor:
    """Publish one catalog-bound model leaf only when its target is absent."""

    def __init__(self, *, paths: HubPaths) -> None:
        self.paths = paths

    def apply(self, record: Mapping[str, Any], staged_file: Path, *, catalog_fingerprint: str | None = None) -> dict[str, Any]:
        model_id = record.get("model_id")
        files = record.get("files")
        if not isinstance(model_id, str) or not isinstance(files, list) or len(files) != 1 or not isinstance(files[0], Mapping):
            return {"status": "unavailable", "code": "model_executor_requires_single_leaf", "execution": "not_run"}
        expected = files[0]
        relative = expected.get("relative_path")
        if not isinstance(relative, str) or not staged_file.is_file() or _is_reparse(staged_file):
            return {"status": "failed", "code": "model_staged_payload_unavailable", "execution": "not_run"}
        expected_size = expected.get("size_bytes")
        if not isinstance(expected_size, int) or expected_size <= 0 or staged_file.stat().st_size != expected_size:
            return {"status": "failed", "code": "model_staged_size_mismatch", "execution": "not_run"}
        root = self.paths.models_root / model_id
        target = root / Path(relative)
        if _is_reparse(root) or _is_reparse(target) or root.exists():
            return {"status": "conflict", "code": "model_target_exists_manual_review", "execution": "not_run"}
        try:
            root.mkdir(parents=True, exist_ok=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(staged_file, target)
            receipt = {
                "component_id": model_id,
                "component_type": "model",
                "bundle_revision": str(record.get("revision") or "unknown"),
                "source": "catalog_primary",
                "source_identity": source_identity(record),
                "installed_at": int(time.time()),
                "verified_at": None,
                "state": "INSTALLED_UNVERIFIED",
                "operational": False,
                "catalog_fingerprint": catalog_fingerprint,
                "files": [{"relative_leaf": relative, "size_bytes": target.stat().st_size, "sha256": expected.get("sha256")}],
            }
            write_component_receipt(self.paths.config_root, model_id, receipt)
            return {"status": "completed", "model_id": model_id, "state": "INSTALLED_UNVERIFIED", "execution": "completed", "receipt": "written", "next_action": "Run the model-specific bounded adapter smoke before operational promotion."}
        except (OSError, ValueError):
            try:
                if target.is_file() and not _is_reparse(target):
                    target.unlink()
                if root.is_dir() and not _is_reparse(root) and not any(root.iterdir()):
                    root.rmdir()
            except OSError:
                pass
            return {"status": "failed", "code": "model_install_failed", "execution": "not_run"}


__all__ = ["ModelArchiveExecutor"]
