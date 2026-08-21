"""Safe scaffolding for a new repository-managed declarative extension."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from src.shared.schemas.extension_manifest import EXTENSION_ID_PATTERN, MANIFEST_SCHEMA_VERSION, validate_extension_manifest

from .capability_pack import validate_capability_pack
from .config import (
    ExtensionStorageError,
    StorageGuard,
    _lexical_path,
    bounded_directory_entries,
    ensure_directory,
    guard_is_current,
    guarded_location,
    guarded_read_bytes,
    project_root,
)


def _display_name(extension_id: str) -> str:
    return " ".join(part.capitalize() for part in extension_id.split("-"))


def _safe_root(root: Path | None) -> Path:
    return _lexical_path(root or project_root())


def _capability_pack_id(extension_id: str) -> str:
    """Keep the generated companion identifier within the shared 64-char bound."""

    if len(extension_id) <= 59:
        return f"{extension_id}-pack"
    return f"{extension_id[:59].rstrip('-')}-pack"


def _same_identity(left: Any, right: Any) -> bool:
    return bool(left is not None and right is not None and left.device == right.device and left.inode == right.inode)


def _cleanup_owned_directory(
    root: Path,
    directory: Path,
    *,
    expected_identity: Any = None,
    expected_files: dict[str, Any] | None = None,
) -> bool:
    """Remove only a still-contained task directory with the expected identity."""

    try:
        guard = guarded_location(root, directory, kind="directory")
        if expected_identity is not None and not _same_identity(guard.target, expected_identity):
            return False
        if not guard_is_current(guard):
            return False
        entries = bounded_directory_entries(guard, maximum=8)
        for path, kind, _entry_signature in entries:
            if kind != "file" or (expected_files is not None and path.name not in expected_files):
                return False
            file_guard = guarded_location(root, path, kind="file")
            if expected_files is not None and not _same_identity(file_guard.target, expected_files[path.name]):
                return False
            if not guard_is_current(file_guard):
                return False
            path.unlink()
            if guarded_location(root, path, kind="file", allow_missing=True).target is not None:
                return False
        if not guard_is_current(guard):
            return False
        directory.rmdir()
        return guarded_location(root, directory, kind="directory", allow_missing=True).target is None
    except (ExtensionStorageError, OSError):
        return False


def _write_staged_file(root: Path, stage: Path, relative_name: str, contents: str) -> None:
    path = stage / relative_name
    stage_guard = guarded_location(root, stage, kind="directory")
    if not guard_is_current(stage_guard):
        raise ExtensionStorageError("scaffold_stage_changed")
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        raise
    except (OSError, UnicodeError) as exc:
        raise ExtensionStorageError("scaffold_write_failed") from exc
    file_guard = guarded_location(root, path, kind="file")
    if not guard_is_current(file_guard):
        raise ExtensionStorageError("scaffold_file_changed")


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
    repository_root = _safe_root(root)
    repository_guard = guarded_location(repository_root, repository_root, kind="directory")
    base = repository_root / "extensions"
    base_guard = ensure_directory(repository_root, base)
    repository_guard = guarded_location(repository_root, repository_root, kind="directory")
    if not guard_is_current(repository_guard) or not guard_is_current(base_guard):
        raise ValueError("extension_scaffold_unavailable")
    destination = base / extension_id
    destination_guard = guarded_location(repository_root, destination, kind="directory", allow_missing=True)
    if destination_guard.target is not None:
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
        "id": _capability_pack_id(extension_id),
        "display_name": f"{title} capability pack",
        "description": "Declarative capability metadata only.",
        "capabilities": ["capability_pack", "metadata_catalog", "resource_planning"],
        "model_card_ids": [],
        "runtime_card_ids": [],
    }
    validate_extension_manifest(manifest)
    validate_capability_pack(capability_pack)
    files = {
        "extension.json": json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        "capability-pack.json": json.dumps(capability_pack, ensure_ascii=False, indent=2) + "\n",
        "README.md": f"# {title}\n\nThis is a declarative Local AI Hub extension scaffold. It contains no executable code.\n",
    }
    stage: Path | None = None
    stage_guard: StorageGuard | None = None
    stage_identity: Any = None
    staged_file_identities: dict[str, Any] = {}
    moved = False
    try:
        try:
            stage = Path(tempfile.mkdtemp(prefix=".extension-staging-", dir=base))
        except (OSError, ValueError) as exc:
            raise ExtensionStorageError("scaffold_stage_unavailable") from exc
        stage_guard = guarded_location(repository_root, stage, kind="directory")
        stage_identity = stage_guard.target
        if not guard_is_current(stage_guard) or not guard_is_current(base_guard):
            raise ExtensionStorageError("scaffold_stage_changed")
        for relative_name in ("extension.json", "capability-pack.json", "README.md"):
            _write_staged_file(repository_root, stage, relative_name, files[relative_name])
            staged_file_identities[relative_name] = guarded_location(
                repository_root, stage / relative_name, kind="file"
            ).target
        if not guard_is_current(stage_guard) or not guard_is_current(base_guard):
            raise ExtensionStorageError("scaffold_stage_changed")
        destination_guard = guarded_location(repository_root, destination, kind="directory", allow_missing=True)
        if destination_guard.target is not None:
            raise FileExistsError("a managed extension already uses this extension_id")
        try:
            os.rename(stage, destination)
        except FileExistsError:
            raise
        except OSError as exc:
            raise ExtensionStorageError("scaffold_publish_failed") from exc
        moved = True
        published_guard = guarded_location(repository_root, destination, kind="directory")
        if not _same_identity(published_guard.target, stage_identity) or not guard_is_current(published_guard):
            raise ExtensionStorageError("scaffold_publish_changed")
        entries = bounded_directory_entries(published_guard, maximum=8)
        names = {path.name for path, kind, _entry_signature in entries if kind == "file"}
        if names != set(files):
            raise ExtensionStorageError("scaffold_publish_incomplete")
        for relative_name, contents in files.items():
            actual = guarded_read_bytes(repository_root, destination / relative_name, maximum=len(contents.encode("utf-8")) + 1)
            if actual != contents.encode("utf-8"):
                raise ExtensionStorageError("scaffold_publish_changed")
        if not guard_is_current(base_guard):
            raise ExtensionStorageError("scaffold_parent_changed")
        return {"status": "created", "extension_id": extension_id, "files": sorted(files)}
    except FileExistsError:
        if moved:
            _cleanup_owned_directory(
                repository_root,
                destination,
                expected_identity=stage_identity,
                expected_files=staged_file_identities,
            )
        elif stage is not None:
            _cleanup_owned_directory(
                repository_root,
                stage,
                expected_identity=stage_identity,
                expected_files=staged_file_identities,
            )
        raise
    except ExtensionStorageError as exc:
        if moved:
            if not _cleanup_owned_directory(
                repository_root,
                destination,
                expected_identity=stage_identity,
                expected_files=staged_file_identities,
            ):
                raise ValueError("extension_scaffold_manual_review") from None
        elif stage is not None and not _cleanup_owned_directory(
            repository_root,
            stage,
            expected_identity=stage_identity,
            expected_files=staged_file_identities,
        ):
            raise ValueError("extension_scaffold_manual_review") from None
        raise ValueError("extension_scaffold_unavailable") from None
    except OSError:
        if moved:
            _cleanup_owned_directory(
                repository_root,
                destination,
                expected_identity=stage_identity,
                expected_files=staged_file_identities,
            )
        elif stage is not None:
            _cleanup_owned_directory(
                repository_root,
                stage,
                expected_identity=stage_identity,
                expected_files=staged_file_identities,
            )
        raise OSError("extension_scaffold_unavailable") from None
