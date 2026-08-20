"""Production portable-runtime archive executor for catalog-bound runtimes."""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import shutil
import stat
from typing import Any

from src.platform.paths import HubPaths
from src.services.component_installer.archive import ArchiveSafetyError, safe_extract_archive
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


def _safe_path(root: Path, relative: str) -> Path | None:
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


class RuntimeArchiveExecutor:
    """Install an absent portable archive into the managed runtime root."""

    def __init__(self, *, paths: HubPaths) -> None:
        self.paths = paths

    def apply(self, record: Mapping[str, Any], archive: Path, *, catalog_fingerprint: str | None = None) -> dict[str, Any]:
        runtime_id = record.get("runtime_id")
        if not isinstance(runtime_id, str):
            return {"status": "error", "code": "runtime_record_invalid", "execution": "not_run"}
        if record.get("install_strategy") != "portable_archive" or record.get("disposition") != "AUTO_INSTALL_READY":
            return {"status": "unavailable", "code": "runtime_not_auto_install_ready", "execution": "not_run"}
        archive_prefix = str(record.get("archive_prefix") or "")
        archive_leaves = record.get("archive_leaves") if isinstance(record.get("archive_leaves"), Mapping) else {}
        if not archive_prefix or not archive_leaves:
            return {"status": "unavailable", "code": "runtime_archive_mapping_missing", "execution": "not_run"}
        runtime_root = self.paths.runtime_root
        stage = self.paths.temp_root / "component-install" / f"runtime-{runtime_id}-extracted"
        destination_targets: list[tuple[Path, Path]] = []
        published: list[Path] = []
        try:
            if not archive.is_file() or _is_reparse(archive):
                return {"status": "unavailable", "code": "runtime_archive_unavailable", "execution": "not_run"}
            if not _safe_path(self.paths.temp_root, stage.relative_to(self.paths.temp_root).as_posix()):
                return {"status": "failed", "code": "unsafe_runtime_stage", "execution": "not_run"}
            safe_extract_archive(archive, stage, max_bytes=max(int(record.get("estimated_disk_size", 0)), 1))
            for target_relative in record.get("required_leaves", []):
                if not isinstance(target_relative, str):
                    return {"status": "failed", "code": "runtime_leaf_invalid", "execution": "not_run"}
                archive_leaf = archive_leaves.get(target_relative)
                if not isinstance(archive_leaf, str):
                    return {"status": "failed", "code": "runtime_archive_leaf_missing", "execution": "not_run"}
                source = _safe_path(stage, f"{archive_prefix}/{archive_leaf}")
                target = _safe_path(runtime_root, target_relative)
                if source is None or target is None or not source.is_file() or _is_reparse(source):
                    return {"status": "failed", "code": "runtime_archive_leaf_invalid", "execution": "not_run"}
                if target.exists() or target.is_symlink() or _is_reparse(target):
                    return {"status": "conflict", "code": "runtime_target_exists_manual_review", "execution": "not_run"}
                destination_targets.append((source, target))
            runtime_root.mkdir(parents=True, exist_ok=True)
            for source, target in destination_targets:
                if target.exists() or target.is_symlink() or _is_reparse(target):
                    return {"status": "conflict", "code": "runtime_intervening_target", "execution": "not_run"}
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                published.append(target)
            receipt = {
                "component_id": runtime_id,
                "component_type": "runtime",
                "bundle_revision": str(record.get("revision") or "unknown"),
                "source": "catalog_primary",
                "source_identity": source_identity(record),
                "installed_at": __import__("time").time_ns() // 1_000_000_000,
                "verified_at": None,
                "state": "INSTALLED_UNVERIFIED",
                "operational": False,
                "catalog_fingerprint": catalog_fingerprint,
                "files": [{"relative_leaf": target.relative_to(runtime_root).as_posix(), "size_bytes": target.stat().st_size} for target in published],
            }
            write_component_receipt(self.paths.config_root, runtime_id, receipt)
            return {"status": "completed", "runtime_id": runtime_id, "state": "INSTALLED_UNVERIFIED", "execution": "completed", "receipt": "written", "next_action": "Probe the runtime and run the bounded capability smoke before operational promotion."}
        except (OSError, ValueError, ArchiveSafetyError):
            for target in reversed(published):
                try:
                    if target.is_file() and not _is_reparse(target):
                        target.unlink()
                except OSError:
                    pass
            return {"status": "failed", "code": "runtime_install_failed", "execution": "not_run"}
        finally:
            if stage.exists():
                shutil.rmtree(stage, ignore_errors=True)


__all__ = ["RuntimeArchiveExecutor"]
