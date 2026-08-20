"""Allowlisted, data-only module manifest discovery."""

from __future__ import annotations

from collections.abc import Iterable
import json
from pathlib import Path
from typing import Any

from src.shared.schemas.module_manifest import ManifestError, validate_manifest


DEFAULT_MODULES = (
    "animesr", "image_generation", "media_editor", "ocr", "practical_rife",
    "real_esrgan", "sam2", "vision", "voice", "whisper",
)


def load_module_manifest(path: Path) -> dict[str, Any]:
    if path.name != "module.json":
        raise ManifestError("manifest_name_not_allowed")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError("manifest_unreadable") from exc
    return validate_manifest(value)


def discover_manifests(root: Path, *, module_ids: Iterable[str] = DEFAULT_MODULES) -> dict[str, Any]:
    """Discover only fixed source directories; never import a discovered module."""

    result: dict[str, Any] = {}
    errors: list[dict[str, str]] = []
    source_root = root.resolve()
    for identifier in module_ids:
        if not isinstance(identifier, str) or "/" in identifier or "\\" in identifier:
            errors.append({"code": "invalid_module_id"})
            continue
        candidate = (source_root / identifier / "module.json")
        try:
            candidate.resolve().relative_to(source_root)
            result[identifier] = load_module_manifest(candidate)
        except (OSError, ValueError, ManifestError):
            errors.append({"code": "manifest_unavailable", "module": identifier})
    return {"schema_version": "module-registry.v1", "records": result, "errors": errors}


__all__ = ["DEFAULT_MODULES", "discover_manifests", "load_module_manifest"]
