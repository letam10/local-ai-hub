"""Strict runtime catalog validation."""

from __future__ import annotations

from collections.abc import Mapping
import json
import re
from pathlib import Path
from typing import Any


RUNTIME_CATALOG_SCHEMA = "runtime-catalog.v1"
_ID = re.compile(r"^[a-z][a-z0-9._-]{1,95}$")


class RuntimeCatalogError(ValueError):
    pass


def _relative(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value.startswith(("/", "\\")) or ":" in value:
        raise RuntimeCatalogError(f"unsafe_{field}")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or any(part in {"", "."} for part in path.parts):
        raise RuntimeCatalogError(f"unsafe_{field}")
    return path.as_posix()


def validate_runtime_entry(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeCatalogError("runtime_not_object")
    allowed = {
        "runtime_id", "display_name", "kind", "version", "revision", "root_class", "required_leaves",
        "modules", "official_source", "install_supported", "environment_class", "architecture",
        "source_type", "sha256", "estimated_download_size", "estimated_disk_size", "install_strategy",
        "notes", "shared_dependency_id",
    }
    if set(value) - allowed:
        raise RuntimeCatalogError("unknown_runtime_field")
    runtime_id = value.get("runtime_id")
    if not isinstance(runtime_id, str) or not _ID.fullmatch(runtime_id):
        raise RuntimeCatalogError("invalid_runtime_id")
    root_class = value.get("root_class")
    if root_class not in {"runtime_root", "environments_root", "external_managed"}:
        raise RuntimeCatalogError("invalid_runtime_root_class")
    leaves = value.get("required_leaves", [])
    if not isinstance(leaves, list) or not leaves or len(leaves) > 256 or not all(isinstance(item, str) for item in leaves):
        raise RuntimeCatalogError("invalid_runtime_leaves")
    normalized = [_relative(item, "runtime_leaf") for item in leaves]
    modules = value.get("modules", [])
    if not isinstance(modules, list) or not all(isinstance(item, str) and _ID.fullmatch(item) for item in modules):
        raise RuntimeCatalogError("invalid_runtime_modules")
    source = value.get("official_source", "local")
    if not isinstance(source, str) or not (source == "local" or source.startswith("https://")):
        raise RuntimeCatalogError("invalid_runtime_source")
    install_strategy = str(value.get("install_strategy", "reference_existing"))
    if install_strategy not in {"reference_existing", "portable_archive", "python_environment", "manual_import"}:
        raise RuntimeCatalogError("invalid_install_strategy")
    digest = value.get("sha256")
    if digest is not None and (not isinstance(digest, str) or len(digest) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in digest)):
        raise RuntimeCatalogError("invalid_runtime_hash")
    estimated_download = int(value.get("estimated_download_size", 0))
    estimated_disk = int(value.get("estimated_disk_size", 0))
    if estimated_download < 0 or estimated_disk < 0:
        raise RuntimeCatalogError("invalid_runtime_size")
    return {
        "runtime_id": runtime_id,
        "display_name": str(value.get("display_name", runtime_id)),
        "kind": str(value.get("kind", "tool")),
        "version": str(value.get("version", "unknown")),
        "revision": str(value.get("revision", value.get("version", "unknown"))),
        "root_class": root_class,
        "required_leaves": normalized,
        "modules": sorted(set(modules)),
        "official_source": source,
        "install_supported": bool(value.get("install_supported", False)),
        "environment_class": str(value.get("environment_class", "managed")),
        "architecture": str(value.get("architecture", "native")),
        "source_type": str(value.get("source_type", "official")),
        "sha256": digest.lower() if isinstance(digest, str) else None,
        "estimated_download_size": estimated_download,
        "estimated_disk_size": estimated_disk,
        "install_strategy": install_strategy,
        "notes": str(value.get("notes", "")),
        "shared_dependency_id": str(value.get("shared_dependency_id", "")) or None,
    }


def load_catalog(path: Path) -> list[dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeCatalogError("runtime_catalog_unreadable") from exc
    if not isinstance(raw, Mapping) or raw.get("schema_version") != RUNTIME_CATALOG_SCHEMA or not isinstance(raw.get("runtimes"), list):
        raise RuntimeCatalogError("unsupported_runtime_catalog")
    records = [validate_runtime_entry(item) for item in raw["runtimes"]]
    if len({item["runtime_id"] for item in records}) != len(records):
        raise RuntimeCatalogError("duplicate_runtime_id")
    return records


__all__ = ["RUNTIME_CATALOG_SCHEMA", "RuntimeCatalogError", "load_catalog", "validate_runtime_entry"]
