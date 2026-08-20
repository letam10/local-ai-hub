"""Read-only model discovery and explicit, fixture-friendly installation.

The manager never downloads, runs inference, or treats file presence as
operational evidence. Installation is an explicit method for a future
authorized UI/desktop chooser and is intentionally absent from startup.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any

from src.platform.paths import HubPaths, get_paths

from .catalog import ModelCatalogError, catalog_fingerprint, load_catalog


def _safe_child(root: Path, relative: str) -> Path | None:
    candidate = root / Path(relative)
    try:
        resolved_root = root.resolve()
        resolved = candidate.resolve()
        resolved.relative_to(resolved_root)
    except (OSError, ValueError):
        return None
    current = candidate
    while True:
        if current.exists() and current.is_symlink():
            return None
        if current == root:
            break
        if current.parent == current:
            return None
        current = current.parent
    return candidate


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
            "source": record["official_source"],
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
        source = source_root.resolve()
        if not source.is_dir() or source.is_symlink():
            return {"status": "failed", "code": "source_unavailable", "execution": "not_run"}
        destination = self.paths.models_root / model_id
        if destination.exists():
            return {"status": "failed", "code": "target_exists_manual_review", "execution": "not_run"}
        stage_parent = self.paths.temp_root / "v7-model-install"
        stage_parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=f"{model_id}-", dir=stage_parent))
        try:
            for expected in record["files"]:
                source_file = _safe_child(source, expected["relative_path"])
                if source_file is None or not source_file.is_file() or source_file.stat().st_size != expected["size_bytes"]:
                    return {"status": "failed", "code": "source_mismatch", "execution": "not_run"}
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
            receipt.write_text(json.dumps({"schema_version": "model-install-receipts.v1", "records": records}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            return {"status": "completed", "model_id": model_id, "state": "INSTALLED_UNVERIFIED", "location_class": "models_root", "execution": "not_run"}
        except (OSError, ValueError, json.JSONDecodeError):
            return {"status": "failed", "code": "atomic_install_failed", "execution": "not_run"}
        finally:
            if stage.exists():
                shutil.rmtree(stage, ignore_errors=True)


__all__ = ["ModelManager"]
