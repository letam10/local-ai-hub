"""Fixed-root, static catalog discovery for Creative Recipe Intelligence."""

from __future__ import annotations

import os
from pathlib import Path
import stat
from typing import Any

from src.shared.schemas.creative_recipes import (
    CATALOG_ID_RE,
    MAX_DESCRIPTOR_BYTES,
    RECIPE_ID_RE,
    SEMVER_RE,
    STYLE_PACK_ID_RE,
    TARGET_ID_RE,
    canonical_recipe_catalog_json,
)

from .io import safe_import_recipe_catalog


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MANAGED_RECIPE_ROOT = REPOSITORY_ROOT / "creative_recipes" / "catalog"
MAX_MANAGED_CATALOG_DESCRIPTORS = 64


def _contained(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _managed_path_safe(path: Path, root: Path) -> bool:
    if root.is_symlink() or path.is_symlink() or not _contained(path, root):
        return False
    parent = path.parent
    resolved_root = root.resolve()
    while parent != root:
        if parent == parent.parent or parent.is_symlink():
            return False
        try:
            parent.resolve().relative_to(resolved_root)
        except (OSError, ValueError):
            return False
        parent = parent.parent
    return True


def _stat_snapshot(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns)


def _read_managed_json(path: Path, *, root: Path | None = None) -> tuple[bytes | None, str | None]:
    """Read one descriptor with a bounded allocation and mutation checks."""

    root = MANAGED_RECIPE_ROOT if root is None else root
    if not _managed_path_safe(path, root):
        return None, "managed_recipe_refused"
    try:
        before = path.lstat()
    except OSError:
        return None, "managed_recipe_refused"
    if not stat.S_ISREG(before.st_mode):
        return None, "managed_recipe_refused"
    if before.st_size <= 0 or before.st_size > MAX_DESCRIPTOR_BYTES:
        return None, "managed_recipe_size"
    descriptor_fd: int | None = None
    payload: bytes | None = None
    opened: os.stat_result | None = None
    after_fd: os.stat_result | None = None
    error: str | None = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor_fd = os.open(path, flags)
        opened = os.fstat(descriptor_fd)
        if not stat.S_ISREG(opened.st_mode):
            error = "managed_recipe_refused"
        elif opened.st_size <= 0 or opened.st_size > MAX_DESCRIPTOR_BYTES:
            error = "managed_recipe_size"
        elif _stat_snapshot(before) != _stat_snapshot(opened) or not _managed_path_safe(path, root):
            error = "managed_recipe_refused"
        else:
            current = path.lstat()
            if _stat_snapshot(opened) != _stat_snapshot(current):
                error = "managed_recipe_refused"
            else:
                payload = os.read(descriptor_fd, MAX_DESCRIPTOR_BYTES + 1)
                after_fd = os.fstat(descriptor_fd)
    except OSError:
        error = "managed_recipe_refused"
    finally:
        if descriptor_fd is not None:
            try:
                os.close(descriptor_fd)
            except OSError:
                error = "managed_recipe_refused"
    if error is not None:
        return None, error
    if payload is None or opened is None or after_fd is None:
        return None, "managed_recipe_refused"
    if len(payload) > MAX_DESCRIPTOR_BYTES:
        return None, "managed_recipe_size"
    if len(payload) != opened.st_size or _stat_snapshot(opened) != _stat_snapshot(after_fd):
        return None, "managed_recipe_refused"
    if not _managed_path_safe(path, root):
        return None, "managed_recipe_refused"
    try:
        after_path = path.lstat()
    except OSError:
        return None, "managed_recipe_refused"
    if _stat_snapshot(opened) != _stat_snapshot(after_path):
        return None, "managed_recipe_refused"
    return payload, None


def _summary(catalog: dict[str, Any], fingerprint: str) -> dict[str, Any]:
    return {
        "id": catalog["id"],
        "version": catalog["version"],
        "fingerprint": fingerprint,
        "recipe_count": len(catalog["recipes"]),
        "style_pack_count": len(catalog["style_packs"]),
        "target_count": len(catalog["targets"]),
        "availability": {"status": "partial", "reason": "Catalog metadata was validated statically; no model, runtime or machine probe was performed.", "action": "Use the catalog only for deterministic composition and preflight planning."},
    }


def _discover() -> dict[str, Any]:
    root = MANAGED_RECIPE_ROOT
    if not root.is_dir() or root.is_symlink():
        return {"status": "unavailable", "reason": "The managed creative recipe root is unavailable.", "action": "Restore the repository-managed catalog root and retry static validation.", "catalogs": [], "recipes": [], "style_packs": [], "targets": [], "errors": [{"code": "managed_recipe_root_unavailable"}], "execution": "not_run", "_catalog_values": {}, "_recipe_values": {}, "_style_values": {}, "_target_values": {}, "_ambiguous": set()}
    try:
        paths = sorted(root.rglob("*.creative-recipe-catalog.json"))
    except OSError:
        return {"status": "unavailable", "reason": "Managed catalog enumeration failed closed.", "action": "Restore the fixed repository-managed catalog root and retry.", "catalogs": [], "recipes": [], "style_packs": [], "targets": [], "errors": [{"code": "managed_recipe_root_unavailable"}], "execution": "not_run", "_catalog_values": {}, "_recipe_values": {}, "_style_values": {}, "_target_values": {}, "_ambiguous": set()}
    if len(paths) > MAX_MANAGED_CATALOG_DESCRIPTORS:
        return {"status": "unavailable", "reason": "The managed catalog descriptor count exceeds the static bound.", "action": "Keep at most the published descriptor limit under the fixed catalog root.", "catalogs": [], "recipes": [], "style_packs": [], "targets": [], "errors": [{"code": "managed_catalog_limit"}], "execution": "not_run", "_catalog_values": {}, "_recipe_values": {}, "_style_values": {}, "_target_values": {}, "_ambiguous": set()}
    catalogs_by_identity: dict[str, list[tuple[dict[str, Any], str]]] = {}
    errors: list[str] = []
    for path in paths:
        payload, read_error = _read_managed_json(path, root=root)
        if read_error is not None:
            errors.append(read_error)
            continue
        assert payload is not None
        imported = safe_import_recipe_catalog(payload)
        if not imported.get("accepted"):
            errors.append("managed_recipe_catalog_invalid")
            continue
        catalog = imported["catalog"]
        assert isinstance(catalog, dict)
        identity = f"{catalog['id']}@{catalog['version']}"
        catalogs_by_identity.setdefault(identity, []).append((catalog, imported["fingerprint"]))
    catalog_values: dict[str, tuple[dict[str, Any], str]] = {}
    summaries: list[dict[str, Any]] = []
    recipes: dict[str, list[tuple[dict[str, Any], str]]] = {}
    styles: dict[str, list[tuple[dict[str, Any], str]]] = {}
    targets: dict[str, list[tuple[dict[str, Any], str]]] = {}
    ambiguous: set[str] = set()
    for identity in sorted(catalogs_by_identity):
        candidates = catalogs_by_identity[identity]
        if len(candidates) != 1:
            errors.append("managed_catalog_identity_ambiguous")
            ambiguous.add(identity)
            continue
        catalog, fingerprint = candidates[0]
        catalog_values[identity] = (catalog, fingerprint)
        summaries.append(_summary(catalog, fingerprint))
        for recipe in catalog["recipes"]:
            recipes.setdefault(f"{recipe['id']}@{recipe['version']}", []).append((recipe, fingerprint))
        for style in catalog["style_packs"]:
            styles.setdefault(f"{style['id']}@{style['version']}", []).append((style, fingerprint))
        for target in catalog["targets"]:
            targets.setdefault(f"{target['id']}@{target['version']}", []).append((target, fingerprint))
    # References in recipes use opaque IDs (without a version).  Keep an
    # explicit version lookup possible, but make an unversioned ID ambiguous
    # whenever more than one managed version exists.  This prevents discovery
    # from selecting a descriptor by filename or version ordering.
    for values, code in (
        (recipes, "managed_recipe_identity_ambiguous"),
        (styles, "managed_style_pack_identity_ambiguous"),
        (targets, "managed_target_identity_ambiguous"),
    ):
        by_id: dict[str, list[str]] = {}
        for identity in values:
            bare_id = identity.rsplit("@", 1)[0]
            by_id.setdefault(bare_id, []).append(identity)
        for bare_id, identities in by_id.items():
            if len(identities) > 1:
                errors.append(code)
                ambiguous.add(bare_id)
    recipe_values: dict[str, tuple[dict[str, Any], str]] = {}
    style_values: dict[str, tuple[dict[str, Any], str]] = {}
    target_values: dict[str, tuple[dict[str, Any], str]] = {}
    recipe_summaries: list[dict[str, Any]] = []
    style_summaries: list[dict[str, Any]] = []
    target_summaries: list[dict[str, Any]] = []
    for identity, candidates in sorted(recipes.items()):
        if len(candidates) != 1:
            errors.append("managed_recipe_identity_ambiguous")
            ambiguous.add(identity)
            continue
        recipe, fingerprint = candidates[0]
        recipe_values[identity] = (recipe, fingerprint)
        recipe_summaries.append({"id": recipe["id"], "version": recipe["version"], "media_kind": recipe["media_kind"], "fingerprint": fingerprint, "availability": {"status": "partial", "reason": "Recipe is static metadata only; execution was not attempted.", "action": "Run deterministic lint and compatibility preflight before any separately authorized execution."}})
    for identity, candidates in sorted(styles.items()):
        if len(candidates) != 1:
            errors.append("managed_style_pack_identity_ambiguous")
            ambiguous.add(identity)
            continue
        style, fingerprint = candidates[0]
        style_values[identity] = (style, fingerprint)
        style_summaries.append({"id": style["id"], "version": style["version"], "fingerprint": fingerprint, "availability": {"status": "partial", "reason": "Style pack is static prompt metadata only.", "action": "Apply only through the deterministic composition planner."}})
    for identity, candidates in sorted(targets.items()):
        if len(candidates) != 1:
            errors.append("managed_target_identity_ambiguous")
            ambiguous.add(identity)
            continue
        target, fingerprint = candidates[0]
        target_values[identity] = (target, fingerprint)
        target_summaries.append({"id": target["id"], "version": target["version"], "media_kind": target["media_kind"], "status": target["status"], "fingerprint": fingerprint})
    summaries.sort(key=lambda item: (item["id"], item["version"]))
    status = "partial" if summaries else "unavailable"
    return {"status": status, "reason": "Managed creative recipe discovery is fixed-root and static only.", "action": "Use only uniquely identified validated catalog records for composition and preflight.", "catalogs": summaries, "recipes": sorted(recipe_summaries, key=lambda item: (item["id"], item["version"])), "style_packs": sorted(style_summaries, key=lambda item: (item["id"], item["version"])), "targets": sorted(target_summaries, key=lambda item: (item["id"], item["version"])), "errors": [{"code": code} for code in sorted(set(errors))[:64]], "execution": "not_run", "_catalog_values": catalog_values, "_recipe_values": recipe_values, "_style_values": style_values, "_target_values": target_values, "_ambiguous": ambiguous}


def discover_managed_recipe_catalogs() -> dict[str, Any]:
    result = _discover()
    return {key: value for key, value in result.items() if not key.startswith("_")}


def _validate_lookup(value: object, pattern: Any, location: str) -> tuple[str | None, dict[str, Any] | None]:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        return None, {"found": False, "status": "unavailable", "reason": "Lookup ID is invalid.", "action": "Use a declared opaque managed identity.", "execution": "not_run"}
    return value, None


def load_managed_recipe_catalog(catalog_id: object, version: object | None = None) -> dict[str, Any]:
    valid_id, error = _validate_lookup(catalog_id, CATALOG_ID_RE, "catalog_id")
    if error is not None:
        return error
    if version is not None and (not isinstance(version, str) or SEMVER_RE.fullmatch(version) is None):
        return {"found": False, "status": "unavailable", "reason": "Catalog version is invalid.", "action": "Use a declared SemVer catalog version.", "execution": "not_run"}
    result = _discover()
    candidates = [(identity, value) for identity, value in result["_catalog_values"].items() if identity.startswith(f"{valid_id}@") and (version is None or identity == f"{valid_id}@{version}")]
    if len(candidates) != 1:
        return {"found": False, "status": "unavailable", "reason": "No unique managed catalog passed static validation.", "action": "Check the catalog identity and remove duplicate or invalid descriptors.", "errors": [{"code": "managed_catalog_identity_ambiguous" if candidates else "managed_catalog_not_found"}], "execution": "not_run"}
    identity, (catalog, fingerprint) = candidates[0]
    if identity in result["_ambiguous"]:
        return {"found": False, "status": "unavailable", "reason": "Managed catalog identity is ambiguous.", "action": "Remove duplicate catalog identities before loading.", "errors": [{"code": "managed_catalog_identity_ambiguous"}], "execution": "not_run"}
    return {"found": True, "status": "partial", "reason": "Catalog was loaded from the fixed repository root; no execution was attempted.", "action": "Use the detached catalog for static recipe planning only.", "catalog": catalog, "fingerprint": fingerprint, "execution": "not_run"}


def _load_entity(identity_value: object, version: object | None, pattern: Any, values_key: str, not_found_code: str, ambiguous_code: str, label: str) -> dict[str, Any]:
    valid_id, error = _validate_lookup(identity_value, pattern, "id")
    if error is not None:
        return error
    if version is not None and (not isinstance(version, str) or SEMVER_RE.fullmatch(version) is None):
        return {"found": False, "status": "unavailable", "reason": f"{label} version is invalid.", "action": "Use a declared SemVer version.", "execution": "not_run"}
    result = _discover()
    candidates = [(identity, value) for identity, value in result[values_key].items() if identity.startswith(f"{valid_id}@") and (version is None or identity == f"{valid_id}@{version}")]
    if len(candidates) != 1:
        code = ambiguous_code if len(candidates) > 1 or any(item.startswith(f"{valid_id}@") for item in result["_ambiguous"]) else not_found_code
        return {"found": False, "status": "unavailable", "reason": f"No unique managed {label} passed static validation.", "action": "Check the identity and remove duplicate or invalid descriptors.", "errors": [{"code": code}], "execution": "not_run"}
    identity, (value, fingerprint) = candidates[0]
    if identity in result["_ambiguous"]:
        return {"found": False, "status": "unavailable", "reason": f"Managed {label} identity is ambiguous.", "action": "Remove duplicate identities before loading.", "errors": [{"code": ambiguous_code}], "execution": "not_run"}
    return {"found": True, "status": "partial", "reason": f"{label.capitalize()} was loaded from the fixed repository root; no execution was attempted.", "action": "Use the detached metadata for static planning only.", label: value, "fingerprint": fingerprint, "execution": "not_run"}


def load_managed_recipe(recipe_id: object, version: object | None = None) -> dict[str, Any]:
    return _load_entity(recipe_id, version, RECIPE_ID_RE, "_recipe_values", "managed_recipe_not_found", "managed_recipe_identity_ambiguous", "recipe")


def load_managed_style_pack(style_id: object, version: object | None = None) -> dict[str, Any]:
    return _load_entity(style_id, version, STYLE_PACK_ID_RE, "_style_values", "managed_style_pack_not_found", "managed_style_pack_identity_ambiguous", "style_pack")


def load_managed_target(target_id: object, version: object | None = None) -> dict[str, Any]:
    return _load_entity(target_id, version, TARGET_ID_RE, "_target_values", "managed_target_not_found", "managed_target_identity_ambiguous", "target")


def load_catalog_index() -> dict[str, Any]:
    """Return a detached static index for the composition planner."""

    result = _discover()
    return {"catalogs": {key: (copy, fingerprint) for key, (copy, fingerprint) in result["_catalog_values"].items() if key not in result["_ambiguous"]}, "recipes": {key: (copy, fingerprint) for key, (copy, fingerprint) in result["_recipe_values"].items() if key not in result["_ambiguous"]}, "style_packs": {key: (copy, fingerprint) for key, (copy, fingerprint) in result["_style_values"].items() if key not in result["_ambiguous"]}, "targets": {key: (copy, fingerprint) for key, (copy, fingerprint) in result["_target_values"].items() if key not in result["_ambiguous"]}, "errors": result["errors"], "execution": "not_run"}


discover_recipe_catalogs = discover_managed_recipe_catalogs
discover_managed_creative_recipe_catalogs = discover_managed_recipe_catalogs
load_managed_creative_recipe_catalog = load_managed_recipe_catalog
load_managed_creative_recipe = load_managed_recipe
load_managed_creative_style_pack = load_managed_style_pack
load_managed_creative_target = load_managed_target


__all__ = ["MANAGED_RECIPE_ROOT", "MAX_DESCRIPTOR_BYTES", "MAX_MANAGED_CATALOG_DESCRIPTORS", "_read_managed_json", "discover_managed_recipe_catalogs", "discover_managed_creative_recipe_catalogs", "discover_recipe_catalogs", "load_managed_recipe_catalog", "load_managed_creative_recipe_catalog", "load_managed_recipe", "load_managed_creative_recipe", "load_managed_style_pack", "load_managed_creative_style_pack", "load_managed_target", "load_managed_creative_target", "load_catalog_index"]
