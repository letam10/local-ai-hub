"""Safe scaffolding for a new repository-managed declarative extension."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.shared.schemas.extension_manifest import EXTENSION_ID_PATTERN, MANIFEST_SCHEMA_VERSION, validate_extension_manifest

from .capability_pack import validate_capability_pack
from .config import project_root


def _display_name(extension_id: str) -> str:
    return " ".join(part.capitalize() for part in extension_id.split("-"))


def _safe_root(root: Path | None) -> Path:
    return (root or project_root()).resolve()


def generate_extension_scaffold(extension_id: str, *, root: Path | None = None, display_name: str | None = None) -> dict[str, Any]:
    """Create a minimal static descriptor tree under ``<root>/extensions`` only.

    It never writes executable code, runs a package manager, downloads a model,
    or overwrites an existing extension.  The caller gets only relative file
    names in the result, not a machine path.
    """

    if not isinstance(extension_id, str) or not EXTENSION_ID_PATTERN.fullmatch(extension_id):
        raise ValueError("extension_id must be a safe lowercase hyphenated identifier")
    title = display_name or _display_name(extension_id)
    if not isinstance(title, str) or not title.strip() or "\n" in title or "\r" in title or "\x00" in title or len(title) > 120:
        raise ValueError("display_name must be concise single-line text")
    base = _safe_root(root) / "extensions"
    destination = base / extension_id
    try:
        destination.resolve().relative_to(base.resolve())
    except (OSError, ValueError) as exc:
        raise ValueError("extension destination is outside the managed extensions root") from exc
    if destination.exists():
        raise FileExistsError("a managed extension already uses this extension_id")
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "id": extension_id,
        "version": "0.1.0",
        "display_name": title,
        "description": "A static capability-pack scaffold. Add validated descriptors before enabling it.",
        "author": {"name": "Extension author"},
        "license": "Specify a license before distribution",
        "source": "https://example.invalid/replace-with-source",
        "capabilities": ["capability_pack", "metadata_catalog", "resource_planning"],
        "compatibility": {"hub": {"min_version": "4.0.0"}, "platforms": ["windows"]},
        "required_components": [],
        "required_models": [],
        "permissions": ["read_extension_metadata", "plan_resources", "render_compatibility_report"],
        "entrypoints": [
            {"kind": "capability_pack", "path": "capability-pack.json"},
            {"kind": "documentation", "path": "README.md"},
        ],
        "resource_profile": {
            "cpu": {"class": "light", "threads": 1},
            "gpu": {"required": False, "vendor": "none", "device_class": "none"},
            "vram_gb": 0,
            "ram_gb": 0.25,
            "disk_gb": 0.01,
            "exclusive_resource_groups": [],
        },
        "availability": {
            "status": "planned",
            "reason": "This scaffold has not yet been reviewed or enabled.",
            "action": "Fill in provenance and descriptor metadata, then run static validation.",
        },
    }
    capability_pack = {
        "schema_version": "capability-pack.v1",
        "id": f"{extension_id}-pack",
        "display_name": f"{title} capability pack",
        "description": "Declarative capability metadata only.",
        "capabilities": ["capability_pack", "metadata_catalog", "resource_planning"],
        "model_card_ids": [],
        "runtime_card_ids": [],
    }
    validate_extension_manifest(manifest)
    validate_capability_pack(capability_pack)
    base.mkdir(parents=True, exist_ok=True)
    destination.mkdir()
    files = {
        "extension.json": json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        "capability-pack.json": json.dumps(capability_pack, ensure_ascii=False, indent=2) + "\n",
        "README.md": f"# {title}\n\nThis is a declarative Local AI Hub extension scaffold. It contains no executable code.\n",
    }
    for relative_name, contents in files.items():
        (destination / relative_name).write_text(contents, encoding="utf-8")
    return {"status": "created", "extension_id": extension_id, "files": sorted(files)}

