"""Strict model catalog and installation-record primitives."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import re
from pathlib import Path
from typing import Any


MODEL_CATALOG_SCHEMA = "model-catalog.v1"
MODEL_STATES = frozenset({"NOT_INSTALLED", "INSTALLING", "INSTALLED_UNVERIFIED", "PARTIAL", "OPERATIONAL", "UNAVAILABLE", "BROKEN", "UPDATE_AVAILABLE"})
_ID = re.compile(r"^[a-z][a-z0-9._-]{1,95}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ModelCatalogError(ValueError):
    """Raised for an unsafe or incomplete model catalog."""


def _identifier(value: object, field: str = "model_id") -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ModelCatalogError(f"invalid_{field}")
    return value


def _relative_file(value: object) -> str:
    if not isinstance(value, str) or not value or value.startswith(("/", "\\")) or ":" in value:
        raise ModelCatalogError("unsafe_model_relative_path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or any(part in {"", "."} for part in path.parts):
        raise ModelCatalogError("unsafe_model_relative_path")
    return path.as_posix()


def validate_model_entry(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ModelCatalogError("model_not_object")
    allowed = {
        "model_id", "display_name", "provider", "family", "version", "files",
        "estimated_download_size", "estimated_disk_size", "sha256",
        "official_source", "license", "authentication_required", "modules_using_model",
        "minimum_vram", "recommended_vram", "used_by", "revision", "source_type",
        "license_url", "notes", "install_supported", "shared_dependency_id",
    }
    if set(value) - allowed:
        raise ModelCatalogError("unknown_model_field")
    identifier = _identifier(value.get("model_id"))
    display_name = value.get("display_name")
    if not isinstance(display_name, str) or not display_name.strip():
        raise ModelCatalogError("invalid_display_name")
    files = value.get("files")
    if not isinstance(files, list) or not files:
        raise ModelCatalogError("invalid_model_files")
    normalized_files: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in files:
        if not isinstance(item, Mapping):
            raise ModelCatalogError("invalid_model_file")
        if set(item) - {"relative_path", "size_bytes", "sha256"}:
            raise ModelCatalogError("unknown_model_file_field")
        relative = _relative_file(item.get("relative_path"))
        if relative in seen:
            raise ModelCatalogError("duplicate_model_file")
        seen.add(relative)
        size = item.get("size_bytes")
        if not isinstance(size, int) or size < 0:
            raise ModelCatalogError("invalid_model_file_size")
        digest = item.get("sha256")
        if digest is not None and (not isinstance(digest, str) or not _SHA256.fullmatch(digest.lower())):
            raise ModelCatalogError("invalid_model_file_hash")
        normalized_files.append({"relative_path": relative, "size_bytes": size, "sha256": digest.lower() if isinstance(digest, str) else None})
    modules = value.get("modules_using_model", value.get("used_by", []))
    if not isinstance(modules, list) or not all(isinstance(item, str) and _ID.fullmatch(item) for item in modules):
        raise ModelCatalogError("invalid_model_modules")
    source = value.get("official_source")
    if not isinstance(source, str) or not (source.startswith("https://") or source == "local"):
        raise ModelCatalogError("invalid_official_source")
    overall_hash = value.get("sha256")
    if overall_hash is not None and (not isinstance(overall_hash, str) or not _SHA256.fullmatch(overall_hash.lower())):
        raise ModelCatalogError("invalid_model_hash")
    download_size = int(value.get("estimated_download_size", sum(item["size_bytes"] for item in normalized_files)))
    disk_size = int(value.get("estimated_disk_size", sum(item["size_bytes"] for item in normalized_files)))
    if download_size < 0 or disk_size < 0:
        raise ModelCatalogError("invalid_model_size")
    return {
        "model_id": identifier,
        "display_name": display_name.strip(),
        "provider": str(value.get("provider", "unknown")),
        "family": str(value.get("family", "unknown")),
        "version": str(value.get("version", "unknown")),
        "revision": str(value.get("revision", value.get("version", "unknown"))),
        "source_type": str(value.get("source_type", "official")),
        "files": normalized_files,
        "estimated_download_size": download_size,
        "estimated_disk_size": disk_size,
        "sha256": overall_hash.lower() if isinstance(overall_hash, str) else None,
        "official_source": source,
        "license": str(value.get("license", "verify upstream")),
        "license_url": str(value.get("license_url", "")),
        "notes": str(value.get("notes", "")),
        "install_supported": bool(value.get("install_supported", False)),
        "shared_dependency_id": str(value.get("shared_dependency_id", "")) or None,
        "authentication_required": bool(value.get("authentication_required", False)),
        "modules_using_model": sorted(set(modules)),
        "minimum_vram": int(value.get("minimum_vram", 0)),
        "recommended_vram": int(value.get("recommended_vram", 0)),
    }


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ModelCatalogError("duplicate_json_key")
        result[key] = value
    return result


def load_catalog(path: Path) -> list[dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_pairs)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ModelCatalogError("catalog_unreadable") from exc
    if not isinstance(value, Mapping) or value.get("schema_version") != MODEL_CATALOG_SCHEMA or not isinstance(value.get("models"), list):
        raise ModelCatalogError("unsupported_catalog_schema")
    records = [validate_model_entry(item) for item in value["models"]]
    if len({item["model_id"] for item in records}) != len(records):
        raise ModelCatalogError("duplicate_model_id")
    return records


def catalog_fingerprint(records: list[Mapping[str, Any]]) -> str:
    payload = json.dumps(records, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


__all__ = ["MODEL_CATALOG_SCHEMA", "MODEL_STATES", "ModelCatalogError", "catalog_fingerprint", "load_catalog", "validate_model_entry"]
