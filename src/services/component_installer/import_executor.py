"""Native/server-owned manual import executor.

The browser never supplies a filesystem path.  ``ComponentInstaller`` creates a
short-lived selection token from a desktop/native chooser and passes the
private selection record here.  The executor validates catalog leaves before
any copy, refuses existing/reparse destinations, publishes only into the
managed Models root, and writes a path-free receipt atomically.
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
from .receipts import write_component_receipt


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


def _safe_chain(path: Path, root: Path) -> bool:
    """Validate lexical and resolved containment with reparse rejection."""

    try:
        lexical_root = root.absolute()
        lexical = path.absolute()
        lexical.relative_to(lexical_root)
    except (OSError, ValueError):
        return False
    current = lexical
    while True:
        if _is_reparse(current):
            return False
        if current == lexical_root:
            break
        if current.parent == current:
            return False
        current = current.parent
    try:
        lexical.resolve().relative_to(lexical_root.resolve())
    except (OSError, ValueError):
        return False
    return True


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        handle, name = tempfile.mkstemp(prefix=".component-receipt-", suffix=".tmp", dir=path.parent)
        os.close(handle)
        temporary = Path(name)
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=True, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def _receipt_fingerprint(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


class ManualImportExecutor:
    """Apply one catalog-bound manual import; no overwrite or arbitrary paths."""

    def __init__(self, *, paths: HubPaths, model_manager: Any, runtime_manager: Any | None = None) -> None:
        self.paths = paths
        self.model_manager = model_manager
        self.runtime_manager = runtime_manager

    def _record(self, component_id: str, component_type: str) -> Mapping[str, Any] | None:
        if component_type == "model":
            try:
                return self.model_manager._record(component_id)
            except Exception:
                return None
        if component_type == "runtime" and self.runtime_manager is not None:
            return next((item for item in self.runtime_manager._records if item.get("runtime_id") == component_id), None)
        return None

    @staticmethod
    def _source_leaf(source: Path, relative: str, expected_count: int) -> Path | None:
        if source.is_file() and expected_count == 1:
            return source
        if not source.is_dir():
            return None
        candidates = [source / Path(relative), source / Path(relative).name]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return None

    def apply(self, plan: Mapping[str, Any], *, confirmed: bool) -> dict[str, Any]:
        if not confirmed:
            return {"status": "waiting_confirmation", "execution": "not_run", "dry_run": True}
        component_id = plan.get("component_id")
        component_type = plan.get("component_type")
        selection = plan.get("selection")
        if not isinstance(component_id, str) or component_type not in {"model", "runtime"} or not isinstance(selection, Mapping):
            return {"status": "error", "code": "import_plan_invalid", "execution": "not_run"}
        if float(selection.get("expires_at", 0) or 0) < time.time():
            return {"status": "conflict", "code": "selection_expired", "execution": "not_run"}
        record = self._record(component_id, str(component_type))
        if not isinstance(record, Mapping):
            return {"status": "error", "code": "unknown_component", "execution": "not_run"}
        source_raw = selection.get("path")
        if not isinstance(source_raw, Path):
            return {"status": "invalid", "code": "selection_not_native", "execution": "not_run"}
        source = source_raw.absolute()
        if not source.exists() or _is_reparse(source):
            return {"status": "unavailable", "code": "selection_unavailable", "execution": "not_run"}
        # Native selection is allowed to originate outside Hub, but every
        # source ancestor must be ordinary; an external junction is rejected.
        current = source
        while True:
            if _is_reparse(current):
                return {"status": "unavailable", "code": "selection_reparse_unsupported", "execution": "not_run"}
            if current.parent == current:
                break
            current = current.parent

        if component_type != "model":
            return {"status": "unavailable", "code": "runtime_manual_import_requires_review", "execution": "not_run"}
        files = record.get("files")
        if not isinstance(files, list) or not files:
            return {"status": "unavailable", "code": "catalog_files_missing", "execution": "not_run"}
        destination = (self.paths.models_root / component_id).absolute()
        if not _safe_chain(destination.parent, self.paths.models_root) or _is_reparse(destination.parent):
            return {"status": "failed", "code": "unsafe_managed_root", "execution": "not_run"}
        if destination.exists() or destination.is_symlink() or _is_reparse(destination):
            return {"status": "conflict", "code": "target_exists_manual_review", "execution": "not_run"}

        validated: list[tuple[Path, str, int, str | None]] = []
        for raw in files:
            if not isinstance(raw, Mapping) or not isinstance(raw.get("relative_path"), str):
                return {"status": "failed", "code": "catalog_file_invalid", "execution": "not_run"}
            relative = str(raw["relative_path"])
            candidate = self._source_leaf(source, relative, len(files))
            if candidate is None or not candidate.is_file() or _is_reparse(candidate):
                return {"status": "failed", "code": "import_leaf_missing", "execution": "not_run"}
            expected_size = raw.get("size_bytes")
            expected_size = int(expected_size) if isinstance(expected_size, int) and expected_size > 0 else None
            actual_size = candidate.stat().st_size
            if actual_size <= 0 or expected_size is not None and actual_size != expected_size:
                return {"status": "failed", "code": "import_size_mismatch", "execution": "not_run"}
            expected_hash = raw.get("sha256")
            if expected_hash is not None and (not isinstance(expected_hash, str) or _sha256(candidate) != expected_hash.lower()):
                return {"status": "failed", "code": "import_checksum_mismatch", "execution": "not_run"}
            validated.append((candidate, relative.replace("\\", "/"), actual_size, expected_hash.lower() if isinstance(expected_hash, str) else None))

        mode = plan.get("mode")
        if mode == "REFERENCE_EXISTING_MANAGED_LOCATION":
            if not _safe_chain(source, self.paths.models_root):
                return {"status": "failed", "code": "reference_requires_managed_location", "execution": "not_run"}
            # A managed existing location is already the destination; only the
            # receipt is reconstructed and no binary is copied or replaced.
            installed_root = source
        elif mode == "COPY_INTO_MANAGED_MODELS":
            stage_parent = self.paths.temp_root / "component-import"
            if not _safe_chain(stage_parent, self.paths.temp_root):
                return {"status": "failed", "code": "unsafe_import_stage", "execution": "not_run"}
            stage_parent.mkdir(parents=True, exist_ok=True)
            self.paths.models_root.mkdir(parents=True, exist_ok=True)
            stage = Path(tempfile.mkdtemp(prefix=f"{component_id}-", dir=stage_parent))
            installed_root = destination
            try:
                for candidate, relative, _size, _digest in validated:
                    target = stage / Path(relative)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(candidate, target)
                if destination.exists() or destination.is_symlink() or _is_reparse(destination):
                    return {"status": "conflict", "code": "intervening_target", "execution": "not_run"}
                # os.rename fails on Windows when the destination appeared;
                # unlike replace it never intentionally overwrites a target.
                os.rename(stage, destination)
                stage = None  # type: ignore[assignment]
            except (OSError, ValueError):
                return {"status": "failed", "code": "import_publish_failed", "execution": "not_run"}
            finally:
                if isinstance(stage, Path) and stage.exists():
                    shutil.rmtree(stage, ignore_errors=True)
        else:
            return {"status": "invalid", "code": "invalid_import_mode", "execution": "not_run"}

        receipt = {
            "component_id": component_id,
            "component_type": "model",
            "bundle_revision": str(record.get("revision") or "unknown"),
            "source": "manual_import",
            "source_identity": str(record.get("source_identity") or record.get("revision") or "manual")[:256],
            "files": [{"relative_leaf": relative, "size_bytes": size, "sha256": digest, "size_verified": digest is not None or size > 0} for _candidate, relative, size, digest in validated],
            "installed_size_bytes": sum(item[2] for item in validated),
            "location_class": "models_root" if mode == "COPY_INTO_MANAGED_MODELS" else "managed_existing",
            "installed_at": int(time.time()),
            "verified_at": None,
            "state": "INSTALLED_UNVERIFIED",
            "operational": False,
            "catalog_fingerprint": str(plan.get("catalog_fingerprint") or "")[:128],
        }
        try:
            write_component_receipt(self.paths.config_root, component_id, receipt)
        except OSError:
            return {"status": "failed", "code": "receipt_write_failed", "execution": "not_run"}
        return {
            "status": "completed",
            "execution": "completed",
            "dry_run": False,
            "component_id": component_id,
            "state": "INSTALLED_UNVERIFIED",
            "receipt": "written",
            "receipt_fingerprint": _receipt_fingerprint(receipt),
            "source": "manual_import",
            "next_action": "Run the component-specific bounded verification before operational promotion.",
        }


__all__ = ["ManualImportExecutor"]
