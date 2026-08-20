"""Runtime discovery and receipt composition; no startup installation."""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import stat
from typing import Any

from src.platform.paths import HubPaths, get_paths, resolve_component_root

from .catalog import RuntimeCatalogError, load_catalog


def _safe_target(root: Path, relative: str) -> Path | None:
    candidate = root / relative
    try:
        lexical_root = root.absolute()
        lexical = candidate.absolute()
        lexical.relative_to(lexical_root)
    except (OSError, ValueError):
        return None
    current = lexical
    while current.parent != current:
        if _is_reparse(current):
            return None
        current = current.parent
    if _is_reparse(current):
        return None
    try:
        candidate.resolve().relative_to(lexical_root.resolve())
    except (OSError, ValueError):
        return None
    return candidate


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

            attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
            attrs = int(attrs) & 0xFFFFFFFF
            return attrs != 0xFFFFFFFF and bool(attrs & 0x400)
        except (AttributeError, OSError):
            return False
    return False


def _safe_directory(path: Path) -> bool:
    current = path.absolute()
    while current.parent != current:
        if _is_reparse(current):
            return False
        current = current.parent
    return not _is_reparse(current)


class RuntimeManager:
    def __init__(self, *, paths: HubPaths | None = None, catalog_path: Path | None = None) -> None:
        self.paths = paths or get_paths()
        self.catalog_path = catalog_path or (self.paths.app_root / "Config" / "runtime_catalog.example.json")
        self._records = load_catalog(self.catalog_path)

    def _root(self, record: Mapping[str, Any]) -> Path:
        return resolve_component_root(self.paths, str(record.get("runtime_id", "runtime")), "runtime", record.get("root_class"), require_exists=False)

    def _catalog_fingerprint(self) -> str:
        import hashlib

        payload = json.dumps(self._records, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def inspect(self, runtime_id: str) -> dict[str, Any]:
        from src.services.component_installer.verification import FastComponentInspector

        record = next((item for item in self._records if item["runtime_id"] == runtime_id), None)
        if record is None:
            raise RuntimeCatalogError("unknown_runtime_id")
        result = FastComponentInspector().inspect(
            paths=self.paths,
            component_id=runtime_id,
            component_type="runtime",
            record=record,
            catalog_fingerprint=self._catalog_fingerprint(),
        )
        result["runtime_id"] = runtime_id
        return result

    def snapshot(self) -> dict[str, Any]:
        records = [self.inspect(item["runtime_id"]) for item in self._records]
        return {"schema_version": "runtime-manager.v1", "status": "partial" if any(item["status"] == "PARTIAL" for item in records) else "available", "execution": "not_run", "dry_run": True, "records": records, "reason": "Runtime Manager performs bounded leaf inspection only; it does not install, execute or alter environments at startup.", "next_action": "Review runtime plans separately from model installation plans."}

    def plan_install(self, runtime_id: str, *, free_bytes: int | None = None) -> dict[str, Any]:
        record = next((item for item in self._records if item["runtime_id"] == runtime_id), None)
        if record is None:
            raise RuntimeCatalogError("unknown_runtime_id")
        required = int(record.get("estimated_disk_size", 0))
        if free_bytes is None:
            try:
                free_bytes = int(shutil.disk_usage(self._root(record)).free)
            except OSError:
                free_bytes = 0
        enough = free_bytes >= required
        return {
            "schema_version": "runtime-install-plan.v1", "runtime_id": runtime_id,
            "status": "planned" if enough else "unavailable", "execution": "not_run", "dry_run": True,
            "destination": record["root_class"], "install_strategy": record.get("install_strategy"),
            "free_bytes": free_bytes, "estimated_download_size": int(record.get("estimated_download_size", 0)),
            "estimated_disk_size": required, "source_policy": "trusted_catalog" if record.get("install_supported") else "manual_import_or_review",
            "reason": "No download, process execution, dependency installation or environment mutation occurred." if enough else "Insufficient free space for the catalog estimate.",
            "next_action": "Review pinned requirements, wheels/checksums and disk/process gates before explicit installation." if enough else "Increase free space or choose a smaller runtime.",
        }

    def verify(self, runtime_id: str) -> dict[str, Any]:
        from src.services.component_installer.receipts import ReceiptError, write_component_receipt
        from src.services.component_installer.verification import DeepComponentVerifier

        record = next((item for item in self._records if item["runtime_id"] == runtime_id), None)
        if record is None:
            raise RuntimeCatalogError("unknown_runtime_id")
        result = DeepComponentVerifier().verify(
            paths=self.paths,
            component_id=runtime_id,
            component_type="runtime",
            record=record,
            catalog_fingerprint=self._catalog_fingerprint(),
            source="catalog_primary",
        )
        if result.get("status") != "completed":
            return {
                "schema_version": "runtime-verify.v1", "status": result.get("status", "unavailable"),
                "execution": "not_run", "dry_run": True, "runtime_id": runtime_id,
                "state": result.get("state", "UNAVAILABLE"), "leaves": result.get("leaves", []),
                "verified": False, "code": result.get("code", "verification_unavailable"),
                "reason": result.get("reason", "Bounded verification was unavailable."),
                "next_action": result.get("next_action", "Review the managed runtime."),
            }
        try:
            write_component_receipt(self.paths.config_root, runtime_id, result["receipt"])
        except (OSError, ReceiptError, KeyError, TypeError):
            return {"schema_version": "runtime-verify.v1", "status": "unavailable", "execution": "not_run", "dry_run": True, "runtime_id": runtime_id, "state": "UNAVAILABLE", "verified": False, "code": "receipt_write_failed", "reason": "Verification completed but its receipt could not be stored safely.", "next_action": "Retry explicit verification after reviewing receipt storage."}
        return {
            "schema_version": "runtime-verify.v1", "status": "completed", "execution": "not_run", "dry_run": True,
            "runtime_id": runtime_id, "state": result["state"], "leaves": result["leaves"],
            "verified": result["state"] == "INSTALLED_VERIFIED", "operational": False,
            "reason": "Bounded streaming runtime-leaf verification only; no process or environment import ran.",
            "next_action": result["next_action"],
        }

    def install_fixture(self, runtime_id: str, source_root: Path) -> dict[str, Any]:
        """Install a tiny synthetic runtime fixture without executing it."""

        record = next((item for item in self._records if item["runtime_id"] == runtime_id), None)
        if record is None:
            return {"status": "failed", "code": "unknown_runtime_id", "execution": "not_run"}
        source = Path(source_root).absolute()
        if not source.is_dir() or _is_reparse(source):
            return {"status": "failed", "code": "source_unavailable", "execution": "not_run"}
        root = self._root(record)
        stage_parent = self.paths.temp_root / "v7-runtime-install"
        if not _safe_directory(stage_parent) or not _safe_directory(root) or not _safe_directory(self.paths.config_root):
            return {"status": "failed", "code": "unsafe_install_root", "execution": "not_run"}
        stage_parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=f"{runtime_id}-", dir=stage_parent))
        published: list[Path] = []
        def undo_published() -> None:
            for target in reversed(published):
                try:
                    if target.is_file() and not _is_reparse(target):
                        target.unlink()
                except OSError:
                    pass
        try:
            staged_targets: list[tuple[Path, Path]] = []
            for relative in record["required_leaves"]:
                source_file = _safe_target(source, relative)
                if source_file is None or not source_file.is_file():
                    return {"status": "failed", "code": "source_mismatch", "execution": "not_run"}
                staged = stage / relative
                staged.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_file, staged)
                target = _safe_target(root, relative)
                if target is None:
                    undo_published()
                    return {"status": "failed", "code": "unsafe_target", "execution": "not_run"}
                if target.exists() or target.is_symlink():
                    undo_published()
                    return {"status": "failed", "code": "target_exists_manual_review", "execution": "not_run"}
                staged_targets.append((staged, target))
            for staged, target in staged_targets:
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists() or target.is_symlink():
                    undo_published()
                    return {"status": "failed", "code": "intervening_target", "execution": "not_run"}
                os.replace(staged, target)
                published.append(target)
            receipt_path = self.paths.config_root / "runtime_install_receipts.json"
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                current = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.is_file() else {"schema_version": "runtime-install-receipts.v1", "records": {}}
            except (OSError, UnicodeError, json.JSONDecodeError):
                current = {"schema_version": "runtime-install-receipts.v1", "records": {}}
            records = current.get("records") if isinstance(current, Mapping) else None
            if not isinstance(records, dict):
                records = {}
            installed_size = sum((root / relative).stat().st_size for relative in record["required_leaves"] if (root / relative).is_file() and not _is_reparse(root / relative))
            records[runtime_id] = {"status": "INSTALLED_UNVERIFIED", "installed_at": int(time.time()), "location_class": record["root_class"], "required_leaves": list(record["required_leaves"]), "installed_size_bytes": installed_size, "size_source": "installation_receipt"}
            temporary = receipt_path.with_suffix(".tmp")
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps({"schema_version": "runtime-install-receipts.v1", "records": records}, ensure_ascii=True, sort_keys=True, indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, receipt_path)
            return {"status": "completed", "runtime_id": runtime_id, "state": "INSTALLED_UNVERIFIED", "location_class": record["root_class"], "execution": "not_run"}
        except (OSError, ValueError, json.JSONDecodeError):
            # Only remove leaves created by this attempt, and only while they
            # remain ordinary files.  We never roll back or overwrite an
            # intervening/reparse target.
            undo_published()
            return {"status": "failed", "code": "atomic_install_failed", "execution": "not_run"}
        finally:
            if stage.exists():
                shutil.rmtree(stage, ignore_errors=True)

    def install_staged_archive(self, runtime_id: str, staged_archive: Path) -> dict[str, Any]:
        """Extract a trusted staged archive into a synthetic managed runtime flow."""

        record = next((item for item in self._records if item["runtime_id"] == runtime_id), None)
        if record is None:
            return {"status": "failed", "code": "unknown_runtime_id", "execution": "not_run"}
        archive = Path(staged_archive).absolute()
        if not archive.is_file() or _is_reparse(archive):
            return {"status": "failed", "code": "staged_payload_unavailable", "execution": "not_run"}
        extraction = self.paths.temp_root / "component-install" / f"runtime-source-{runtime_id}-{int(time.time() * 1000)}"
        try:
            from src.services.component_installer.archive import safe_extract_archive

            extraction.mkdir(parents=True, exist_ok=True)
            safe_extract_archive(archive, extraction, max_bytes=max(int(record.get("estimated_disk_size", 0)), 1))
            return self.install_fixture(runtime_id, extraction)
        except (OSError, ValueError):
            return {"status": "failed", "code": "runtime_archive_invalid", "execution": "not_run"}
        finally:
            if extraction.exists():
                shutil.rmtree(extraction, ignore_errors=True)


__all__ = ["RuntimeManager"]
