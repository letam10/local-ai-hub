"""Production model/runtime catalog and bounded owner-install discovery.

This is the final V7 catalog boundary.  Catalog metadata is tracked and may
describe a real upstream source, but a source is never treated as a usable
download until its disposition says so.  Discovery is fixed-root and leaf
bounded; it does not scan drives, load models, run Python, or launch tools.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any

from src.platform.paths import HubPaths, get_paths
from src.services.operational_closure.source_availability import SourceAvailabilityService


SCHEMA = "v7-production-catalog.v1"
MODEL_DISPOSITIONS = frozenset({"AUTO_INSTALL_READY", "AUTH_REQUIRED", "LICENSE_REQUIRED", "MANUAL_IMPORT_ONLY", "UNSUPPORTED_SOURCE"})
RUNTIME_DISPOSITIONS = frozenset({"AUTO_INSTALL_READY", "REFERENCE_EXISTING", "MANUAL_INSTALL", "UNSUPPORTED"})
MODEL_STATES = frozenset({"INSTALLED", "NOT_INSTALLED", "PARTIAL", "UNAVAILABLE", "OPERATIONAL"})
RUNTIME_STATES = frozenset({"INSTALLED", "NOT_INSTALLED", "PARTIAL", "UNAVAILABLE", "OPERATIONAL"})


class ProductionCatalogError(ValueError):
    """Raised when a production catalog is malformed or unsafe."""


def _safe_id(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 96 or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789._-" for ch in value) or not value[0].isalpha():
        raise ProductionCatalogError(f"invalid_{field}")
    return value


def _safe_relative(value: object) -> str:
    if not isinstance(value, str) or not value or value.startswith(("/", "\\")) or ":" in value:
        raise ProductionCatalogError("unsafe_catalog_leaf")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or any(part in {"", "."} for part in path.parts):
        raise ProductionCatalogError("unsafe_catalog_leaf")
    return path.as_posix()


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
    lexical_root = root.absolute()
    candidate = lexical_root / Path(relative)
    try:
        candidate.absolute().relative_to(lexical_root)
    except (OSError, ValueError):
        return None
    current = candidate.absolute()
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


def _json_object(path: Path, fallback: object) -> object:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value
    except (OSError, UnicodeError, json.JSONDecodeError):
        return fallback


def _fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _validate_model(item: Mapping[str, Any]) -> dict[str, Any]:
    model_id = _safe_id(item.get("model_id"), "model_id")
    disposition = str(item.get("disposition", "MANUAL_IMPORT_ONLY"))
    if disposition not in MODEL_DISPOSITIONS:
        raise ProductionCatalogError("invalid_model_disposition")
    files_raw = item.get("files")
    if not isinstance(files_raw, list) or not files_raw:
        raise ProductionCatalogError("invalid_model_files")
    files: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in files_raw:
        if not isinstance(raw, Mapping):
            raise ProductionCatalogError("invalid_model_file")
        relative = _safe_relative(raw.get("relative_path"))
        if relative in seen:
            raise ProductionCatalogError("duplicate_model_file")
        seen.add(relative)
        size = raw.get("size_bytes")
        if not isinstance(size, int) or size < 0:
            raise ProductionCatalogError("invalid_model_file_size")
        digest = raw.get("sha256")
        if digest is not None and (not isinstance(digest, str) or len(digest) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in digest)):
            raise ProductionCatalogError("invalid_model_file_hash")
        files.append({"relative_path": relative, "size_bytes": size, "sha256": digest.lower() if isinstance(digest, str) else None})
    official_source = item.get("official_source")
    if isinstance(official_source, Mapping):
        official_source_value: Any = {
            key: value for key, value in official_source.items()
            if key in {"provider", "kind", "url", "https_url", "canonical_identity", "artifact_identity", "revision", "release", "priority", "authentication_required", "auth_required", "license_required"}
        }
    else:
        official_source_value = str(official_source or "local")[:2048]
    primary_source = item.get("primary_source")
    if primary_source is not None and not isinstance(primary_source, (str, Mapping)):
        primary_source = None
    fallback_sources = item.get("trusted_fallback_sources", [])
    if not isinstance(fallback_sources, list):
        fallback_sources = []
    return {
        "model_id": model_id,
        "display_name": str(item.get("display_name") or model_id)[:160],
        "category": str(item.get("category") or "Other")[:48],
        "provider": str(item.get("provider") or "unknown")[:96],
        "version": str(item.get("version") or "unknown")[:96],
        "revision": str(item.get("revision") or item.get("version") or "unknown")[:128],
        "official_source": official_source_value,
        "primary_source": primary_source,
        "trusted_fallback_sources": [dict(value) for value in fallback_sources[:4] if isinstance(value, Mapping)],
        "source_identity": str(item.get("source_identity") or item.get("canonical_identity") or item.get("revision") or "unknown")[:256],
        "latest_upstream_revision": str(item.get("latest_upstream_revision") or item.get("revision") or "unknown")[:128],
        "latest_supported_revision": str(item.get("latest_supported_revision") or item.get("revision") or "unknown")[:128],
        "update_parts": [str(value) for value in item.get("update_parts", []) if value in {"backend", "runtime", "dependencies", "model"}],
        "install_strategy": str(item.get("install_strategy") or "manual_import"),
        "compatibility": dict(item.get("compatibility")) if isinstance(item.get("compatibility"), Mapping) else {},
        "update_candidate": dict(item.get("update_candidate")) if isinstance(item.get("update_candidate"), Mapping) else None,
        "source_type": str(item.get("source_type") or "metadata_only")[:64],
        "disposition": disposition,
        "license": str(item.get("license") or "review_required")[:160],
        "license_url": str(item.get("license_url") or "")[:2048],
        "authentication_required": bool(item.get("authentication_required", False)),
        "modules": sorted({_safe_id(value, "module_id") for value in item.get("modules", []) if isinstance(value, str)}),
        "runtime_id": str(item.get("runtime_id") or "")[:96] or None,
        "files": files,
        "estimated_download_size": max(0, int(item.get("estimated_download_size", 0) or 0)),
        "estimated_disk_size": max(0, int(item.get("estimated_disk_size", 0) or 0)),
        "minimum_vram_mb": max(0, int(item.get("minimum_vram_mb", 0) or 0)),
        "recommended_vram_mb": max(0, int(item.get("recommended_vram_mb", 0) or 0)),
        "notes": str(item.get("notes") or "")[:500],
    }


def _validate_runtime(item: Mapping[str, Any]) -> dict[str, Any]:
    runtime_id = _safe_id(item.get("runtime_id"), "runtime_id")
    disposition = str(item.get("disposition", "REFERENCE_EXISTING"))
    if disposition not in RUNTIME_DISPOSITIONS:
        raise ProductionCatalogError("invalid_runtime_disposition")
    leaves = item.get("required_leaves")
    if not isinstance(leaves, list) or not leaves:
        raise ProductionCatalogError("invalid_runtime_leaves")
    normalized = [_safe_relative(value) for value in leaves]
    official_source = item.get("official_source")
    if isinstance(official_source, Mapping):
        official_source_value: Any = {
            key: value for key, value in official_source.items()
            if key in {"provider", "kind", "url", "https_url", "canonical_identity", "artifact_identity", "revision", "release", "priority", "authentication_required", "auth_required", "license_required"}
        }
    else:
        official_source_value = str(official_source or "local")[:2048]
    primary_source = item.get("primary_source")
    if primary_source is not None and not isinstance(primary_source, (str, Mapping)):
        primary_source = None
    fallback_sources = item.get("trusted_fallback_sources", [])
    if not isinstance(fallback_sources, list):
        fallback_sources = []
    return {
        "runtime_id": runtime_id,
        "display_name": str(item.get("display_name") or runtime_id)[:160],
        "kind": str(item.get("kind") or "tool")[:48],
        "version": str(item.get("version") or "unknown")[:96],
        "revision": str(item.get("revision") or item.get("version") or "unknown")[:128],
        "root_class": str(item.get("root_class") or "runtime_root"),
        "required_leaves": normalized,
        "modules": sorted({_safe_id(value, "module_id") for value in item.get("modules", []) if isinstance(value, str)}),
        "official_source": official_source_value,
        "primary_source": primary_source,
        "trusted_fallback_sources": [dict(value) for value in fallback_sources[:4] if isinstance(value, Mapping)],
        "source_identity": str(item.get("source_identity") or item.get("canonical_identity") or item.get("revision") or "unknown")[:256],
        "latest_upstream_revision": str(item.get("latest_upstream_revision") or item.get("revision") or "unknown")[:128],
        "latest_supported_revision": str(item.get("latest_supported_revision") or item.get("revision") or "unknown")[:128],
        "update_parts": [str(value) for value in item.get("update_parts", []) if value in {"backend", "runtime", "dependencies", "model"}],
        "compatibility": dict(item.get("compatibility")) if isinstance(item.get("compatibility"), Mapping) else {},
        "update_candidate": dict(item.get("update_candidate")) if isinstance(item.get("update_candidate"), Mapping) else None,
        "source_type": str(item.get("source_type") or "metadata_only")[:64],
        "disposition": disposition,
        "install_strategy": str(item.get("install_strategy") or "reference_existing"),
        "estimated_download_size": max(0, int(item.get("estimated_download_size", 0) or 0)),
        "estimated_disk_size": max(0, int(item.get("estimated_disk_size", 0) or 0)),
        "notes": str(item.get("notes") or "")[:500],
    }


def load_production_catalog(path: Path) -> dict[str, list[dict[str, Any]]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProductionCatalogError("catalog_unreadable") from exc
    if not isinstance(raw, Mapping) or raw.get("schema_version") != SCHEMA:
        raise ProductionCatalogError("unsupported_catalog_schema")
    models_raw = raw.get("models")
    runtimes_raw = raw.get("runtimes")
    if not isinstance(models_raw, list) or not isinstance(runtimes_raw, list):
        raise ProductionCatalogError("catalog_sections_missing")
    models = [_validate_model(item) for item in models_raw if isinstance(item, Mapping)]
    runtimes = [_validate_runtime(item) for item in runtimes_raw if isinstance(item, Mapping)]
    if len({item["model_id"] for item in models}) != len(models) or len({item["runtime_id"] for item in runtimes}) != len(runtimes):
        raise ProductionCatalogError("duplicate_catalog_id")
    return {"models": models, "runtimes": runtimes}


class ProductionCatalog:
    """Read-only production catalog plus explicit, bounded size refresh."""

    def __init__(self, *, paths: HubPaths | None = None, catalog_path: Path | None = None) -> None:
        self.paths = paths or get_paths()
        self.catalog_path = catalog_path or self.paths.app_root / "Config" / "v7_production_catalog.example.json"
        loaded = load_production_catalog(self.catalog_path)
        self.models = {item["model_id"]: item for item in loaded["models"]}
        self.runtimes = {item["runtime_id"]: item for item in loaded["runtimes"]}
        self.fingerprint = _fingerprint(loaded)
        self.source_availability = SourceAvailabilityService(paths=self.paths)

    def _root_for_runtime(self, record: Mapping[str, Any]) -> Path:
        if record["root_class"] == "environments_root":
            return self.paths.environments_root
        if record["root_class"] == "external_managed":
            return self.paths.runtime_root / "external"
        return self.paths.runtime_root

    def _size_cache_path(self) -> Path:
        return self.paths.config_root / "model_size_cache.json"

    def _size_cache(self) -> dict[str, Any]:
        value = _json_object(self._size_cache_path(), {})
        return value if isinstance(value, dict) else {}

    def _receipt_size(self, model_id: str) -> int | None:
        value = _json_object(self.paths.config_root / "model_install_receipts.json", {})
        records = value.get("records") if isinstance(value, Mapping) else None
        record = records.get(model_id) if isinstance(records, Mapping) else None
        size = record.get("installed_size_bytes") if isinstance(record, Mapping) else None
        return int(size) if isinstance(size, int) and size >= 0 else None

    def inspect_model(self, model_id: str) -> dict[str, Any]:
        record = self.models.get(model_id)
        if record is None:
            raise ProductionCatalogError("unknown_model_id")
        root = self.paths.models_root / model_id
        leaves = []
        for expected in record["files"]:
            target = _safe_leaf(root, expected["relative_path"])
            present = bool(target and target.is_file())
            size = int(target.stat().st_size) if present else 0
            expected_size = int(expected["size_bytes"])
            leaves.append({"relative_leaf": expected["relative_path"], "present": present, "size_bytes": size if present else None, "size_matches": bool(present and (expected_size == 0 or size == expected_size))})
        if leaves and all(item["present"] and item["size_matches"] for item in leaves):
            status = "INSTALLED"
            reason = "All catalog leaves are present; bounded smoke evidence is still required."
        elif any(item["present"] for item in leaves):
            status = "PARTIAL"
            reason = "Some catalog leaves are present but the managed installation is incomplete."
        else:
            status = "NOT_INSTALLED"
            reason = "No catalog leaf is present under the managed Models root."
        if record["disposition"] == "UNSUPPORTED_SOURCE":
            action = "Manual review is required; no trusted installation action is available."
        elif record["disposition"] == "AUTH_REQUIRED":
            action = "Authorize the official provider before installation."
        elif record["disposition"] == "LICENSE_REQUIRED":
            action = "Review and accept the upstream license before installation."
        elif record["disposition"] == "AUTO_INSTALL_READY":
            action = "Review the server-owned plan, then choose Download & Install."
        else:
            action = "Use Import Model after verifying the official source and license."
        cache = self._size_cache().get(model_id) if status == "INSTALLED" else None
        installed_size = cache.get("size_bytes") if isinstance(cache, Mapping) and isinstance(cache.get("size_bytes"), int) else self._receipt_size(model_id) if status == "INSTALLED" else None
        projected = {key: value for key, value in record.items() if key not in {"official_source", "license_url", "files"}}
        projected.update({"status": status, "execution": "not_run", "operational": False, "leaves": leaves, "installed_size_bytes": installed_size, "expected_download_size_bytes": record["estimated_download_size"] or None, "expected_disk_size_bytes": record["estimated_disk_size"] or None, "source_availability": self.source_availability.cached(model_id), "reason": reason, "next_action": action})
        if installed_size is None and status == "INSTALLED":
            projected["size_label"] = "Size unavailable"
        elif installed_size is not None:
            projected["size_label"] = f"{installed_size} bytes"
        elif status == "NOT_INSTALLED" and not record["estimated_download_size"]:
            projected["size_label"] = "Size unavailable"
        return projected

    def inspect_runtime(self, runtime_id: str) -> dict[str, Any]:
        record = self.runtimes.get(runtime_id)
        if record is None:
            raise ProductionCatalogError("unknown_runtime_id")
        root = self._root_for_runtime(record)
        leaves = []
        for relative in record["required_leaves"]:
            target = _safe_leaf(root, relative)
            leaves.append({"relative_leaf": relative, "present": bool(target and target.is_file())})
        if leaves and all(item["present"] for item in leaves):
            status = "INSTALLED"
            reason = "Required runtime leaves are present; import/package and smoke evidence are still required."
        elif any(item["present"] for item in leaves):
            status = "PARTIAL"
            reason = "Some runtime leaves are present but the runtime is incomplete."
        else:
            status = "NOT_INSTALLED"
            reason = "No required runtime leaf was observed at the managed root."
        projected = {key: value for key, value in record.items() if key != "official_source"}
        projected.update({"status": status, "execution": "not_run", "operational": False, "leaves": leaves, "source_availability": self.source_availability.cached(runtime_id), "reason": reason, "next_action": "Review the pinned runtime plan; existing environments are never overwritten automatically."})
        return projected

    def snapshot(self, *, query: str = "", category: str = "", installed: bool | None = None) -> dict[str, Any]:
        models = [self.inspect_model(model_id) for model_id in sorted(self.models)]
        runtimes = [self.inspect_runtime(runtime_id) for runtime_id in sorted(self.runtimes)]
        needle = query.strip().casefold()
        if needle:
            models = [item for item in models if needle in str(item.get("model_id", "")).casefold() or needle in str(item.get("display_name", "")).casefold()]
            runtimes = [item for item in runtimes if needle in str(item.get("runtime_id", "")).casefold() or needle in str(item.get("display_name", "")).casefold()]
        if category:
            models = [item for item in models if str(item.get("category", "")).casefold() == category.casefold()]
        if installed is not None:
            models = [item for item in models if (item["status"] == "INSTALLED") is installed]
        return {"schema_version": "v7-production-catalog-snapshot.v1", "status": "completed", "execution": "not_run", "dry_run": True, "catalog_fingerprint": self.fingerprint, "models": models, "runtimes": runtimes, "counts": {"models": len(models), "runtimes": len(runtimes), "installed_models": sum(item["status"] == "INSTALLED" for item in models), "install_ready": sum(item["disposition"] == "AUTO_INSTALL_READY" for item in models)}, "reason": "Catalog and fixed-leaf discovery only; no model/runtime process or network action ran.", "next_action": "Select a server-owned component plan before any installation."}

    def refresh_model_size(self, model_id: str, *, max_files: int = 10000) -> dict[str, Any]:
        """Explicit user-requested bounded size refresh; never runs on every UI refresh."""

        record = self.models.get(model_id)
        if record is None:
            raise ProductionCatalogError("unknown_model_id")
        root = (self.paths.models_root / model_id).absolute()
        if not root.is_dir() or _is_reparse(root):
            return {"status": "unavailable", "code": "model_root_missing", "execution": "not_run"}
        total = 0
        count = 0
        for path in root.rglob("*"):
            if count >= max_files or _is_reparse(path):
                if count >= max_files:
                    return {"status": "unavailable", "code": "size_scan_limit", "execution": "not_run"}
                continue
            if path.is_file():
                total += path.stat().st_size
                count += 1
        cache = self._size_cache()
        cache[model_id] = {"size_bytes": total, "file_count": count, "catalog_fingerprint": self.fingerprint}
        self.paths.config_root.mkdir(parents=True, exist_ok=True)
        target = self._size_cache_path()
        temporary = Path(tempfile.mkstemp(prefix=".model-size-", suffix=".tmp", dir=self.paths.config_root)[1])
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(cache, handle, ensure_ascii=True, sort_keys=True, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return {"status": "completed", "model_id": model_id, "size_bytes": total, "file_count": count, "execution": "not_run"}


def catalog_snapshot(*, paths: HubPaths | None = None, query: str = "", category: str = "", installed: bool | None = None) -> dict[str, Any]:
    return ProductionCatalog(paths=paths).snapshot(query=query, category=category, installed=installed)


__all__ = ["MODEL_DISPOSITIONS", "ProductionCatalog", "ProductionCatalogError", "RUNTIME_DISPOSITIONS", "SCHEMA", "catalog_snapshot", "load_production_catalog"]
