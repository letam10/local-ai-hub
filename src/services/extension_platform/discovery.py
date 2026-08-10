"""Safe static discovery for repository-managed extension descriptors only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from src.services.capability_planner.cards import (
    CardValidationError,
    validate_model_card_collection,
    validate_runtime_card_collection,
)
from src.shared.schemas.extension_manifest import (
    EXTENSION_ID_PATTERN,
    ManifestValidationError,
    STATIC_METADATA_CAPABILITIES,
    validate_extension_manifest,
)

from .capability_pack import CapabilityPackValidationError, validate_capability_pack
from .config import project_root


DISCOVERY_VERSION = "extension-discovery.v1"
_MAX_DESCRIPTOR_BYTES = 1_000_000


def _issue(code: str, message: str, *, extension_id: str | None = None) -> dict[str, str | None]:
    # The issue content is fixed text: descriptor values, paths, and parse errors
    # are never reflected into reports.
    return {"extension_id": extension_id, "code": code, "message": message}


def _read_json_descriptor(path: Path) -> Any:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_DESCRIPTOR_BYTES:
            raise ValueError("descriptor_not_readable")
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("descriptor_not_json") from exc


def _resolve_descriptor(extension_dir: Path, relative_path: str) -> Path | None:
    try:
        unresolved = extension_dir / relative_path
        current = extension_dir
        for part in Path(relative_path).parts:
            current = current / part
            if current.is_symlink():
                return None
        candidate = unresolved.resolve()
        candidate.relative_to(extension_dir.resolve())
    except (OSError, ValueError):
        return None
    return candidate


def _descriptor_error(kind: str) -> tuple[str, str]:
    labels = {
        "capability_pack": "capability pack",
        "model_cards": "model card collection",
        "runtime_cards": "runtime card collection",
        "documentation": "documentation descriptor",
    }
    label = labels.get(kind, "static descriptor")
    return "invalid_descriptor", f"The {label} is missing, unsafe, or outside its static schema allowlist."


def _load_descriptor(extension_dir: Path, entrypoint: Mapping[str, str]) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    kind = entrypoint["kind"]
    candidate = _resolve_descriptor(extension_dir, entrypoint["path"])
    if candidate is None:
        code, message = _descriptor_error(kind)
        return None, {"code": code, "message": message}
    if kind == "documentation":
        try:
            if candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_size > _MAX_DESCRIPTOR_BYTES:
                raise OSError
        except OSError:
            code, message = _descriptor_error(kind)
            return None, {"code": code, "message": message}
        return {"kind": kind, "documentation": True}, None
    try:
        raw = _read_json_descriptor(candidate)
        if kind == "capability_pack":
            return {"kind": kind, "value": validate_capability_pack(raw)}, None
        if kind == "model_cards":
            return {"kind": kind, "value": validate_model_card_collection(raw)}, None
        if kind == "runtime_cards":
            return {"kind": kind, "value": validate_runtime_card_collection(raw)}, None
    except (ValueError, CapabilityPackValidationError, CardValidationError):
        pass
    code, message = _descriptor_error(kind)
    return None, {"code": code, "message": message}


def _runtime_required(manifest: Mapping[str, Any]) -> bool:
    if manifest["required_components"] or manifest["required_models"]:
        return True
    return bool(set(manifest["capabilities"]) - STATIC_METADATA_CAPABILITIES)


def _honest_status(manifest: Mapping[str, Any], descriptor_errors: list[dict[str, str]]) -> tuple[str, str, str]:
    if descriptor_errors:
        return (
            "unavailable",
            "Static discovery could not validate every allowlisted descriptor.",
            "Correct the descriptor schema and rerun static validation; no extension code was executed.",
        )
    availability = manifest["availability"]
    if availability["status"] != "operational":
        return availability["status"], availability["reason"], availability["action"]
    if _runtime_required(manifest):
        return (
            "partial",
            "Static discovery validated descriptors but does not execute or import the required runtime capability.",
            "Run dependency preflight and a bounded, separately authorized functional smoke before claiming operational status.",
        )
    return (
        "operational",
        "Static descriptors were validated and this extension declares no runtime workload.",
        "Use the compatibility report or resource planner; no code has been loaded.",
    )


def _validate_descriptor_relationships(
    manifest: Mapping[str, Any],
    capability_packs: list[dict[str, Any]],
    model_cards: list[dict[str, Any]],
    runtime_cards: list[dict[str, Any]],
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    model_ids = {card["id"] for card in model_cards}
    runtime_ids = {card["id"] for card in runtime_cards}
    for pack in capability_packs:
        if not set(pack["capabilities"]).issubset(manifest["capabilities"]):
            errors.append({"code": "capability_mismatch", "message": "A capability pack declares a capability absent from its extension manifest."})
        if not set(pack.get("model_card_ids", [])).issubset(model_ids):
            errors.append({"code": "unknown_model_card", "message": "A capability pack references a model card not present in its static collection."})
        if not set(pack.get("runtime_card_ids", [])).issubset(runtime_ids):
            errors.append({"code": "unknown_runtime_card", "message": "A capability pack references a runtime card not present in its static collection."})
    declared = set(manifest["capabilities"])
    expected = {
        "capability_pack": capability_packs,
        "model_cards": model_cards,
        "runtime_cards": runtime_cards,
    }
    for capability, values in expected.items():
        if capability in declared and not values:
            errors.append({"code": "missing_descriptor", "message": "A manifest capability has no corresponding static descriptor."})
    return errors


def _discover_extension(extension_dir: Path) -> tuple[dict[str, Any], list[dict[str, str | None]]]:
    issues: list[dict[str, str | None]] = []
    manifest_path = extension_dir / "extension.json"
    try:
        raw_manifest = _read_json_descriptor(manifest_path)
        manifest = validate_extension_manifest(raw_manifest)
    except (ValueError, ManifestValidationError):
        return (
            {
                "extension_id": None,
                "display_name": "Unidentified extension",
                "status": "unavailable",
                "reason": "The extension manifest is missing, unsafe, or outside extension-manifest.v1.",
                "action": "Correct extension.json under the managed extensions directory and rerun static validation.",
                "capabilities": [],
                "permissions": [],
                "entrypoints": [],
                "required_components": [],
                "required_models": [],
                "resource_profile": None,
                "manifest": None,
                "descriptors": {"capability_packs": [], "model_cards": [], "runtime_cards": [], "documentation": 0},
            },
            [_issue("invalid_manifest", "An extension manifest could not be validated without executing code.")],
        )
    if extension_dir.name != manifest["id"]:
        return (
            {
                "extension_id": manifest["id"],
                "display_name": manifest["display_name"],
                "status": "unavailable",
                "reason": "The managed directory identifier does not match the extension manifest identifier.",
                "action": "Rename the managed extension directory to match its manifest id.",
                "capabilities": list(manifest["capabilities"]),
                "permissions": list(manifest["permissions"]),
                "entrypoints": [{"kind": item["kind"]} for item in manifest["entrypoints"]],
                "required_components": list(manifest["required_components"]),
                "required_models": list(manifest["required_models"]),
                "resource_profile": manifest["resource_profile"],
                "manifest": manifest,
                "descriptors": {"capability_packs": [], "model_cards": [], "runtime_cards": [], "documentation": 0},
            },
            [_issue("directory_id_mismatch", "A managed extension directory does not match its declared id.", extension_id=manifest["id"])],
        )

    descriptor_errors: list[dict[str, str]] = []
    capability_packs: list[dict[str, Any]] = []
    model_cards: list[dict[str, Any]] = []
    runtime_cards: list[dict[str, Any]] = []
    documentation = 0
    for entrypoint in manifest["entrypoints"]:
        descriptor, error = _load_descriptor(extension_dir, entrypoint)
        if error is not None:
            descriptor_errors.append(error)
            continue
        assert descriptor is not None
        if descriptor["kind"] == "capability_pack":
            capability_packs.append(descriptor["value"])
        elif descriptor["kind"] == "model_cards":
            model_cards.extend(descriptor["value"])
        elif descriptor["kind"] == "runtime_cards":
            runtime_cards.extend(descriptor["value"])
        else:
            documentation += 1
    descriptor_errors.extend(_validate_descriptor_relationships(manifest, capability_packs, model_cards, runtime_cards))
    status, reason, action = _honest_status(manifest, descriptor_errors)
    for error in descriptor_errors:
        issues.append(_issue(error["code"], error["message"], extension_id=manifest["id"]))
    return (
        {
            "extension_id": manifest["id"],
            "display_name": manifest["display_name"],
            "status": status,
            "reason": reason,
            "action": action,
            "capabilities": list(manifest["capabilities"]),
            "permissions": list(manifest["permissions"]),
            "entrypoints": [{"kind": item["kind"]} for item in manifest["entrypoints"]],
            "required_components": list(manifest["required_components"]),
            "required_models": list(manifest["required_models"]),
            "resource_profile": manifest["resource_profile"],
            "manifest": manifest,
            "descriptors": {
                "capability_packs": capability_packs,
                "model_cards": model_cards,
                "runtime_cards": runtime_cards,
                "documentation": documentation,
            },
        },
        issues,
    )


def discover_extensions(root: Path | None = None) -> dict[str, Any]:
    """Discover descriptors only from ``<repository>/extensions``.

    The routine reads JSON and Markdown metadata from immediate managed
    subdirectories.  It never recursively imports Python, executes an
    entrypoint, launches a shell, follows an extension symlink, or accepts an
    arbitrary discovery directory.
    """

    managed_root = (root or project_root()).resolve() / "extensions"
    records: list[dict[str, Any]] = []
    issues: list[dict[str, str | None]] = []
    if not managed_root.exists() or not managed_root.is_dir() or managed_root.is_symlink():
        return {
            "contract_version": DISCOVERY_VERSION,
            "managed_root": "extensions",
            "extensions": records,
            "issues": [_issue("managed_root_missing", "The repository-managed extensions directory is unavailable.")],
            "counts": {status: 0 for status in ("operational", "partial", "unavailable", "planned")},
        }
    seen_ids: set[str] = set()
    for entry in sorted(managed_root.iterdir(), key=lambda item: item.name.casefold()):
        if entry.is_symlink():
            issues.append(_issue("symlink_ignored", "A linked entry was ignored; extensions must be repository-managed directories."))
            continue
        if not entry.is_dir():
            issues.append(_issue("non_directory_ignored", "A non-directory entry was ignored under the managed extensions root."))
            continue
        if not EXTENSION_ID_PATTERN.fullmatch(entry.name):
            issues.append(_issue("unsafe_directory_ignored", "A managed extension directory has an unsafe identifier and was ignored."))
            continue
        record, record_issues = _discover_extension(entry)
        extension_id = record["extension_id"]
        if extension_id is not None and extension_id in seen_ids:
            record["status"] = "unavailable"
            record["reason"] = "More than one managed descriptor declares the same extension identifier."
            record["action"] = "Keep one descriptor for each extension identifier."
            record_issues.append(_issue("duplicate_extension_id", "Duplicate extension identifiers are not discoverable.", extension_id=extension_id))
        elif extension_id is not None:
            seen_ids.add(extension_id)
        records.append(record)
        issues.extend(record_issues)
    counts = {status: sum(1 for record in records if record["status"] == status) for status in ("operational", "partial", "unavailable", "planned")}
    return {
        "contract_version": DISCOVERY_VERSION,
        "managed_root": "extensions",
        "extensions": records,
        "issues": issues,
        "counts": counts,
    }


class ExtensionDiscovery:
    """State-free façade around repository-managed static discovery."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root

    def discover(self) -> dict[str, Any]:
        return discover_extensions(self._root)
