"""Read-only model discovery and explicit, fixture-friendly installation.

The manager never downloads, runs inference, or treats file presence as
operational evidence. Installation is an explicit method for a future
authorized UI/desktop chooser and is intentionally absent from startup.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import os
from pathlib import Path
import stat
import shutil
import tempfile
import time
from typing import Any

from src.platform.paths import HubPaths, get_paths

from .catalog import ModelCatalogError, catalog_fingerprint, load_catalog


def _safe_child(root: Path, relative: str) -> Path | None:
    candidate = root / Path(relative)
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


class ModelManager:
    def __init__(self, *, paths: HubPaths | None = None, catalog_path: Path | None = None) -> None:
        self.paths = paths or get_paths()
        self.catalog_path = catalog_path or (self.paths.app_root / "Config" / "model_catalog.example.json")
        self._records = load_catalog(self.catalog_path)
        self._by_id = {item["model_id"]: item for item in self._records}

    @property
    def catalog_fingerprint(self) -> str:
        return catalog_fingerprint(self._records)

    def _record(self, model_id: str) -> dict[str, Any]:
        try:
            return self._by_id[model_id]
        except KeyError as exc:
            raise ModelCatalogError("unknown_model_id") from exc

    def inspect(self, model_id: str) -> dict[str, Any]:
        record = self._record(model_id)
        model_root = self.paths.models_root / model_id
        files: list[dict[str, Any]] = []
        for expected in record["files"]:
            target = _safe_child(model_root, expected["relative_path"])
            present = bool(target and target.is_file())
            size_ok = bool(present and target.stat().st_size == expected["size_bytes"])
            files.append({"relative_path": expected["relative_path"], "present": present, "size_match": size_ok, "size_bytes": target.stat().st_size if present else 0})
        if all(item["present"] and item["size_match"] for item in files):
            status = "INSTALLED_UNVERIFIED"
            reason = "All catalog leaves are present and sized; bounded smoke evidence is still required."
            action = "Run an authorized bounded module verification before claiming operational status."
        elif any(item["present"] for item in files):
            status = "PARTIAL"
            reason = "Some catalog leaves are present but the installation is incomplete or differs from the catalog."
            action = "Review the installation plan; do not overwrite existing files automatically."
        else:
            status = "NOT_INSTALLED"
            reason = "No catalog leaf is present under the managed Models root."
            action = "Review the model install plan and explicitly authorize a trusted source or manual import."
        return {"model_id": model_id, "status": status, "execution": "not_run", "files": files, "reason": reason, "next_action": action}

    def snapshot(self) -> dict[str, Any]:
        records = [self.inspect(model_id) for model_id in sorted(self._by_id)]
        return {
            "schema_version": "model-manager.v1",
            "status": "partial" if any(item["status"] == "PARTIAL" for item in records) else "available",
            "execution": "not_run",
            "dry_run": True,
            "catalog_fingerprint": self.catalog_fingerprint,
            "records": records,
            "reason": "Model Manager reads bounded catalog leaves only; it does not download or run inference at startup.",
            "next_action": "Choose an explicit model plan from the Module/Model Manager.",
        }

    def verify(self, model_id: str) -> dict[str, Any]:
        state = self.inspect(model_id)
        return {
            "schema_version": "model-verify.v1", "status": "completed", "execution": "not_run", "dry_run": True,
            "model_id": model_id, "state": state["status"], "files": state["files"],
            "verified": state["status"] == "INSTALLED_UNVERIFIED",
            "reason": "Bounded file existence/size verification only; no model load or inference ran.",
            "next_action": "Run the module-specific bounded verification under explicit authorization.",
        }

    def plan_install(self, model_id: str, *, free_bytes: int | None = None) -> dict[str, Any]:
        record = self._record(model_id)
        required = int(record["estimated_disk_size"])
        if free_bytes is None:
            try:
                free_bytes = shutil.disk_usage(self.paths.models_root).free
            except OSError:
                free_bytes = 0
        enough = free_bytes >= required
        return {
            "schema_version": "model-install-plan.v1",
            "model_id": model_id,
            "status": "planned" if enough else "unavailable",
            "execution": "not_run",
            "dry_run": True,
            "source_policy": "trusted_catalog" if record.get("install_supported") else "manual_import_or_review",
            "source_fingerprint": __import__("src.services.component_installer.policy", fromlist=["source_fingerprint"]).source_fingerprint(record["official_source"]),
            "license": record["license"],
            "estimated_download_size": record["estimated_download_size"],
            "estimated_disk_size": required,
            "free_bytes": free_bytes,
            "destination": "models_root",
            "reason": "Free-space and catalog preflight only; no network or write occurred." if enough else "Insufficient free space for the catalog estimate.",
            "next_action": "Request explicit confirmation and a trusted source before installation." if enough else "Increase free space or choose a smaller model.",
        }

    def install_fixture(self, model_id: str, source_root: Path) -> dict[str, Any]:
        """Install a complete tiny fixture atomically; intended for tests/QA only."""

        record = self._record(model_id)
        source = Path(source_root).absolute()
        if not source.is_dir() or _is_reparse(source):
            return {"status": "failed", "code": "source_unavailable", "execution": "not_run"}
        destination = self.paths.models_root / model_id
        if destination.exists():
            return {"status": "failed", "code": "target_exists_manual_review", "execution": "not_run"}
        stage_parent = self.paths.temp_root / "v7-model-install"
        if not _safe_directory(stage_parent) or not _safe_directory(destination.parent) or not _safe_directory(self.paths.config_root):
            return {"status": "failed", "code": "unsafe_install_root", "execution": "not_run"}
        stage_parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=f"{model_id}-", dir=stage_parent))
        try:
            for expected in record["files"]:
                source_file = _safe_child(source, expected["relative_path"])
                if source_file is None or not source_file.is_file() or source_file.stat().st_size != expected["size_bytes"]:
                    return {"status": "failed", "code": "source_mismatch", "execution": "not_run"}
                expected_hash = expected.get("sha256")
                if expected_hash:
                    digest = hashlib.sha256()
                    with source_file.open("rb") as handle:
                        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                            digest.update(chunk)
                    if digest.hexdigest() != expected_hash:
                        return {"status": "failed", "code": "source_checksum_mismatch", "execution": "not_run"}
                staged_file = stage / expected["relative_path"]
                staged_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_file, staged_file)
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(stage, destination)
            receipt = self.paths.config_root / "model_install_receipts.json"
            receipt.parent.mkdir(parents=True, exist_ok=True)
            existing = json.loads(receipt.read_text(encoding="utf-8")) if receipt.exists() else {"schema_version": "model-install-receipts.v1", "records": {}}
            records = existing.get("records") if isinstance(existing, Mapping) else None
            if not isinstance(records, dict):
                records = {}
            records[model_id] = {"status": "INSTALLED_UNVERIFIED", "catalog_fingerprint": self.catalog_fingerprint, "installed_at": int(time.time()), "location_class": "models_root"}
            temporary = receipt.with_suffix(".tmp")
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps({"schema_version": "model-install-receipts.v1", "records": records}, indent=2, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, receipt)
            return {"status": "completed", "model_id": model_id, "state": "INSTALLED_UNVERIFIED", "location_class": "models_root", "execution": "not_run"}
        except (OSError, ValueError, json.JSONDecodeError):
            return {"status": "failed", "code": "atomic_install_failed", "execution": "not_run"}
        finally:
            if stage.exists():
                shutil.rmtree(stage, ignore_errors=True)

    def install_staged_file(self, model_id: str, staged_path: Path) -> dict[str, Any]:
        """Finalize one server-downloaded catalog leaf through the fixture-safe path."""

        record = self._record(model_id)
        if len(record["files"]) != 1:
            return {"status": "failed", "code": "multi_file_recipe_requires_executor", "execution": "not_run"}
        staged = Path(staged_path).absolute()
        if not staged.is_file() or _is_reparse(staged):
            return {"status": "failed", "code": "staged_payload_unavailable", "execution": "not_run"}
        source_root = self.paths.temp_root / "component-install" / f"source-{model_id}-{int(time.time() * 1000)}"
        try:
            leaf = source_root / record["files"][0]["relative_path"]
            leaf.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(staged, leaf)
            return self.install_fixture(model_id, source_root)
        except OSError:
            return {"status": "failed", "code": "staged_payload_copy_failed", "execution": "not_run"}
        finally:
            if source_root.exists():
                shutil.rmtree(source_root, ignore_errors=True)


__all__ = ["ModelManager"]
