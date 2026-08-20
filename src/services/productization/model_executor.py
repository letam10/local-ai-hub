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
from src.services.component_installer.receipts import CatalogBindingContext, ReceiptError, V2_CATALOG_SCHEMA, source_identity, write_component_receipt


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

    @staticmethod
    def _binding_error(
        record: Mapping[str, Any],
        *,
        catalog_binding: CatalogBindingContext | None,
        catalog_fingerprint: str | None,
        catalog_revision: str | None,
    ) -> str | None:
        if not isinstance(record, Mapping):
            return "catalog_binding_stale"
        if not isinstance(catalog_binding, CatalogBindingContext):
            return "catalog_binding_stale" if catalog_binding is None else "catalog_schema_unsupported"
        if not isinstance(catalog_fingerprint, str) or catalog_binding.catalog_fingerprint != catalog_fingerprint.casefold():
            return "catalog_binding_stale"
        if not isinstance(catalog_revision, str) or catalog_binding.catalog_revision != catalog_revision:
            return "catalog_binding_stale"
        if catalog_binding.catalog_schema not in {"model-catalog.v1", V2_CATALOG_SCHEMA}:
            return "catalog_schema_unsupported"
        record_is_v2 = record.get("catalog_schema") == V2_CATALOG_SCHEMA or "source_verification" in record
        if record_is_v2 and catalog_binding.catalog_schema != V2_CATALOG_SCHEMA:
            return "catalog_schema_unsupported"
        try:
            catalog_binding.validate_record(component_type="model", record=record)
        except (ReceiptError, TypeError, ValueError):
            return "catalog_binding_stale"
        if catalog_binding.catalog_schema == V2_CATALOG_SCHEMA and "source_identity" not in record:
            return "catalog_binding_stale"
        return None

    @staticmethod
    def _refusal(code: str) -> dict[str, Any]:
        return {"status": "conflict", "code": code, "execution": "not_run", "dry_run": True}

    @staticmethod
    def _safe_path(root: Path, relative: str) -> Path | None:
        try:
            root = root.absolute()
            candidate = (root / Path(relative)).absolute()
            candidate.relative_to(root)
            candidate.resolve().relative_to(root.resolve())
        except (OSError, ValueError):
            return None
        current = candidate
        while True:
            if _is_reparse(current):
                return None
            if current == root:
                return candidate
            if current.parent == current:
                return None
            current = current.parent

    @staticmethod
    def _cleanup_target(root: Path, target: Path) -> None:
        try:
            if target.is_file() and not _is_reparse(target):
                target.unlink()
            current = target.parent
            while current != root and current.is_dir() and not _is_reparse(current) and not any(current.iterdir()):
                current.rmdir()
                current = current.parent
            if root.is_dir() and not _is_reparse(root) and not any(root.iterdir()):
                root.rmdir()
        except OSError:
            pass

    def apply(
        self,
        record: Mapping[str, Any],
        staged_file: Path,
        *,
        catalog_binding: CatalogBindingContext | None = None,
        catalog_fingerprint: str | None = None,
        catalog_revision: str | None = None,
    ) -> dict[str, Any]:
        binding_error = self._binding_error(
            record,
            catalog_binding=catalog_binding,
            catalog_fingerprint=catalog_fingerprint,
            catalog_revision=catalog_revision,
        )
        if binding_error is not None:
            return self._refusal(binding_error)
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
        target = self._safe_path(root, relative)
        if target is None or _is_reparse(root) or root.exists():
            return {"status": "conflict", "code": "model_target_exists_manual_review", "execution": "not_run"}
        try:
            root.mkdir(parents=True, exist_ok=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(staged_file, target)
            binding_error = self._binding_error(
                record,
                catalog_binding=catalog_binding,
                catalog_fingerprint=catalog_fingerprint,
                catalog_revision=catalog_revision,
            )
            if binding_error is not None:
                self._cleanup_target(root, target)
                return self._refusal(binding_error)
            assert catalog_binding is not None
            receipt_source_identity = catalog_binding.source_identity if catalog_binding.catalog_schema == V2_CATALOG_SCHEMA else source_identity(record)
            receipt = {
                "component_id": model_id,
                "component_type": "model",
                "catalog_schema": catalog_binding.catalog_schema,
                "catalog_revision": catalog_binding.catalog_revision,
                "catalog_fingerprint": catalog_binding.catalog_fingerprint,
                "source_identity": receipt_source_identity,
                "root_class": "models_root",
                "location_class": "models_root",
                "leaves": [{"relative_path": relative, "observed_size_bytes": target.stat().st_size, "observed_mtime_ns": target.stat().st_mtime_ns, "verification_level": "unverified"}],
                "recorded_at": int(time.time()),
                "verified_at": None,
                "state": "INSTALLED_UNVERIFIED",
                "source": "catalog_primary",
                "operational": False,
            }
            write_component_receipt(self.paths.config_root, model_id, receipt, catalog_binding=catalog_binding)
            return {"status": "completed", "model_id": model_id, "state": "INSTALLED_UNVERIFIED", "execution": "completed", "receipt": "written", "next_action": "Run the model-specific bounded adapter smoke before operational promotion."}
        except (OSError, ValueError):
            self._cleanup_target(root, target)
            return {"status": "failed", "code": "model_install_failed", "execution": "not_run"}


__all__ = ["ModelArchiveExecutor"]
