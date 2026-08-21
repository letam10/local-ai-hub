"""Read the constrained extension-platform configuration without leaking local data."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from src.shared.schemas.extension_manifest import AVAILABILITY_STATUSES, EXTENSION_ID_PATTERN, PLATFORMS, is_semver


EXTENSION_CONFIG_SCHEMA_VERSION = "extensions-config.v1"
_MAX_CONFIG_BYTES = 1_000_000
_REPARSE_POINT = 0x400
_MAX_DIRECTORY_ENTRIES = 256
_MAX_PATH_PARTS = 24


class ExtensionStorageError(ValueError):
    """Fixed internal failure for an unsafe or changing managed location."""


@dataclass(frozen=True)
class _StorageSignature:
    mode: int
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int
    attributes: int


@dataclass(frozen=True)
class StorageGuard:
    """Bounded no-follow evidence for one managed location."""

    root: Path
    path: Path
    kind: str
    chain: tuple[tuple[Path, _StorageSignature], ...]
    target: _StorageSignature | None


def _lexical_path(value: Path | str) -> Path:
    """Make an absolute lexical path without resolving links or reparse points."""

    return Path(os.path.abspath(os.fspath(value)))


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.normpath(os.fspath(left))) == os.path.normcase(os.path.normpath(os.fspath(right)))


def _lexically_contained(root: Path, candidate: Path) -> bool:
    try:
        common = os.path.commonpath((os.fspath(root), os.fspath(candidate)))
        return os.path.normcase(os.path.normpath(common)) == os.path.normcase(os.path.normpath(os.fspath(root)))
    except (OSError, ValueError):
        return False


def _is_reparse(stat_result: os.stat_result) -> bool:
    attributes = int(getattr(stat_result, "st_file_attributes", 0) or 0)
    return stat.S_ISLNK(stat_result.st_mode) or bool(attributes & _REPARSE_POINT)


def _signature(path: Path, *, kind: str, allow_missing: bool = False) -> _StorageSignature | None:
    try:
        stat_result = os.lstat(path)
    except FileNotFoundError:
        if allow_missing:
            return None
        raise ExtensionStorageError("managed_location_unavailable")
    except OSError as exc:
        raise ExtensionStorageError("managed_location_unavailable") from exc
    if _is_reparse(stat_result):
        raise ExtensionStorageError("managed_location_reparse")
    if kind == "directory" and not stat.S_ISDIR(stat_result.st_mode):
        raise ExtensionStorageError("managed_directory_invalid")
    if kind == "file" and not stat.S_ISREG(stat_result.st_mode):
        raise ExtensionStorageError("managed_file_invalid")
    return _StorageSignature(
        mode=int(stat_result.st_mode),
        device=int(getattr(stat_result, "st_dev", 0)),
        inode=int(getattr(stat_result, "st_ino", 0)),
        size=int(getattr(stat_result, "st_size", 0)),
        mtime_ns=int(getattr(stat_result, "st_mtime_ns", 0)),
        ctime_ns=int(getattr(stat_result, "st_ctime_ns", 0)),
        attributes=int(getattr(stat_result, "st_file_attributes", 0) or 0),
    )


def _directory_chain(root: Path, parent: Path) -> tuple[tuple[Path, _StorageSignature], ...]:
    if not _lexically_contained(root, parent):
        raise ExtensionStorageError("managed_location_escape")
    chain: dict[str, tuple[Path, _StorageSignature]] = {}

    current = root
    while True:
        current_signature = _signature(current, kind="directory")
        assert current_signature is not None
        chain[os.path.normcase(os.path.normpath(os.fspath(current)))] = (current, current_signature)
        parent_of_current = current.parent
        if _same_path(parent_of_current, current):
            break
        current = parent_of_current

    current = parent
    while not _same_path(current, root):
        if not _lexically_contained(root, current):
            raise ExtensionStorageError("managed_location_escape")
        current_signature = _signature(current, kind="directory")
        assert current_signature is not None
        chain[os.path.normcase(os.path.normpath(os.fspath(current)))] = (current, current_signature)
        parent_of_current = current.parent
        if _same_path(parent_of_current, current):
            raise ExtensionStorageError("managed_location_escape")
        current = parent_of_current
    return tuple(chain.values())


def guarded_location(root: Path | str, path: Path | str, *, kind: str, allow_missing: bool = False) -> StorageGuard:
    """Capture no-follow root, ancestor, parent, and target evidence."""

    if kind not in {"directory", "file"}:
        raise ExtensionStorageError("managed_location_invalid")
    lexical_root = _lexical_path(root)
    lexical_path = _lexical_path(path)
    if not _lexically_contained(lexical_root, lexical_path):
        raise ExtensionStorageError("managed_location_escape")
    relative_parts = lexical_path.relative_to(lexical_root).parts
    if len(relative_parts) > _MAX_PATH_PARTS:
        raise ExtensionStorageError("managed_location_too_deep")
    parent = lexical_path if _same_path(lexical_path, lexical_root) else lexical_path.parent
    chain = _directory_chain(lexical_root, parent)
    target = _signature(lexical_path, kind=kind, allow_missing=allow_missing)
    return StorageGuard(root=lexical_root, path=lexical_path, kind=kind, chain=chain, target=target)


def guard_is_current(guard: StorageGuard) -> bool:
    """Revalidate bounded chain and target identity without following links."""

    try:
        for path, expected in guard.chain:
            current = _signature(path, kind="directory")
            if current is None or current.device != expected.device or current.inode != expected.inode:
                return False
        current_target = _signature(guard.path, kind=guard.kind, allow_missing=guard.target is None)
        if guard.target is None:
            return current_target is None
        if current_target is None or current_target.device != guard.target.device or current_target.inode != guard.target.inode:
            return False
        return guard.kind == "directory" or current_target == guard.target
    except ExtensionStorageError:
        return False


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ExtensionStorageError("managed_json_duplicate_key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ExtensionStorageError("managed_json_nonfinite")


def strict_json_bytes(data: bytes, *, maximum: int) -> Any:
    """Decode bounded UTF-8 JSON with duplicate and nonfinite rejection."""

    if not isinstance(data, bytes) or len(data) > maximum:
        raise ExtensionStorageError("managed_json_oversized")
    try:
        text = data.decode("utf-8", errors="strict")
        return json.loads(text, object_pairs_hook=_strict_pairs, parse_constant=_reject_constant)
    except ExtensionStorageError:
        raise
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ExtensionStorageError("managed_json_invalid") from exc


def guarded_read_bytes(root: Path | str, path: Path | str, *, maximum: int) -> bytes:
    """Read a small regular file twice with pre/post identity and byte checks."""

    guard = guarded_location(root, path, kind="file")
    if not guard_is_current(guard):
        raise ExtensionStorageError("managed_file_changed")
    try:
        with guard.path.open("rb") as handle:
            first = handle.read(maximum + 1)
        if len(first) > maximum:
            raise ExtensionStorageError("managed_file_oversized")
        if not guard_is_current(guard):
            raise ExtensionStorageError("managed_file_changed")
        with guard.path.open("rb") as handle:
            second = handle.read(maximum + 1)
    except ExtensionStorageError:
        raise
    except (OSError, UnicodeError) as exc:
        raise ExtensionStorageError("managed_file_unavailable") from exc
    if len(second) > maximum or first != second or not guard_is_current(guard):
        raise ExtensionStorageError("managed_file_changed")
    return first


def guarded_read_json(root: Path | str, path: Path | str, *, maximum: int) -> Any:
    return strict_json_bytes(guarded_read_bytes(root, path, maximum=maximum), maximum=maximum)


def bounded_directory_entries(guard: StorageGuard, *, maximum: int = _MAX_DIRECTORY_ENTRIES) -> list[tuple[Path, str, _StorageSignature]]:
    """Enumerate one managed directory with bounded no-follow evidence."""

    if guard.kind != "directory" or guard.target is None or not guard_is_current(guard):
        raise ExtensionStorageError("managed_directory_changed")
    entries: list[tuple[Path, str, _StorageSignature]] = []
    try:
        with os.scandir(guard.path) as iterator:
            for index, entry in enumerate(iterator):
                if index >= maximum:
                    raise ExtensionStorageError("managed_directory_oversized")
                child = _lexical_path(entry.path)
                if not _lexically_contained(guard.root, child):
                    raise ExtensionStorageError("managed_location_escape")
                stat_result = os.lstat(child)
                if _is_reparse(stat_result):
                    raise ExtensionStorageError("managed_location_reparse")
                if stat.S_ISDIR(stat_result.st_mode):
                    kind = "directory"
                elif stat.S_ISREG(stat_result.st_mode):
                    kind = "file"
                else:
                    kind = "other"
                entries.append((child, kind, _StorageSignature(
                    mode=int(stat_result.st_mode),
                    device=int(getattr(stat_result, "st_dev", 0)),
                    inode=int(getattr(stat_result, "st_ino", 0)),
                    size=int(getattr(stat_result, "st_size", 0)),
                    mtime_ns=int(getattr(stat_result, "st_mtime_ns", 0)),
                    ctime_ns=int(getattr(stat_result, "st_ctime_ns", 0)),
                    attributes=int(getattr(stat_result, "st_file_attributes", 0) or 0),
                )))
    except ExtensionStorageError:
        raise
    except OSError as exc:
        raise ExtensionStorageError("managed_directory_unavailable") from exc
    if not guard_is_current(guard):
        raise ExtensionStorageError("managed_directory_changed")
    return entries


def ensure_directory(root: Path | str, path: Path | str) -> StorageGuard:
    """Create missing directory components one at a time after no-follow checks."""

    lexical_root = _lexical_path(root)
    lexical_target = _lexical_path(path)
    if not _lexically_contained(lexical_root, lexical_target):
        raise ExtensionStorageError("managed_location_escape")
    if not _signature(lexical_root, kind="directory"):
        raise ExtensionStorageError("managed_root_unavailable")
    current = lexical_root
    for part in lexical_target.relative_to(lexical_root).parts:
        child = current / part
        guarded_location(lexical_root, current, kind="directory")
        existing = guarded_location(lexical_root, child, kind="directory", allow_missing=True)
        if existing.target is None:
            try:
                child.mkdir()
            except FileExistsError:
                pass
            guarded_location(lexical_root, child, kind="directory")
        current = child
    return guarded_location(lexical_root, lexical_target, kind="directory")


def project_root() -> Path:
    """Return the repository root without depending on a machine-local setting."""

    return _lexical_path(__file__).parents[3]


def _safe_id(value: Any) -> bool:
    return isinstance(value, str) and bool(EXTENSION_ID_PATTERN.fullmatch(value))


def _normalize_inventory(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping) or not _safe_id(item.get("id")) or item["id"] in seen:
            continue
        status = item.get("status")
        if not isinstance(status, str) or status not in AVAILABILITY_STATUSES:
            continue
        normalized = {"id": item["id"], "status": status}
        if is_semver(item.get("version")):
            normalized["version"] = item["version"]
        result.append(normalized)
        seen.add(item["id"])
    return result


def _normalize_hardware(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    result: dict[str, Any] = {}
    for field in ("cpu_threads",):
        if isinstance(value.get(field), int) and not isinstance(value[field], bool) and value[field] > 0:
            result[field] = value[field]
    for field in ("ram_gb", "disk_gb"):
        if isinstance(value.get(field), (int, float)) and not isinstance(value[field], bool) and value[field] >= 0:
            result[field] = float(value[field])
    gpus: list[dict[str, Any]] = []
    if isinstance(value.get("gpus"), list):
        for index, gpu in enumerate(value["gpus"]):
            if not isinstance(gpu, Mapping):
                continue
            vendor = gpu.get("vendor")
            device_class = gpu.get("device_class")
            vram = gpu.get("vram_gb")
            if vendor not in {"nvidia", "amd", "intel"} or device_class not in {"integrated", "discrete"}:
                continue
            if not isinstance(vram, (int, float)) or isinstance(vram, bool) or vram < 0:
                continue
            identifier = gpu.get("id")
            if not isinstance(identifier, str) or not identifier or len(identifier) > 64:
                identifier = f"gpu-{index + 1}"
            gpus.append({"id": identifier, "vendor": vendor, "device_class": device_class, "vram_gb": float(vram)})
    result["gpus"] = gpus
    return result


def _normalize_config(value: Any, *, source: str) -> dict[str, Any] | None:
    if not isinstance(value, Mapping) or value.get("schema_version") != EXTENSION_CONFIG_SCHEMA_VERSION:
        return None
    hub_version = value.get("hub_version")
    platform = value.get("platform")
    if not is_semver(hub_version) or platform not in PLATFORMS:
        return None
    enabled = value.get("enabled_extensions", [])
    if not isinstance(enabled, list) or any(not _safe_id(item) for item in enabled) or len(enabled) != len(set(enabled)):
        return None
    return {
        "schema_version": EXTENSION_CONFIG_SCHEMA_VERSION,
        "source": source,
        "hub_version": hub_version,
        "platform": platform,
        "enabled_extensions": list(enabled),
        "components": _normalize_inventory(value.get("components")),
        "models": _normalize_inventory(value.get("models")),
        "hardware": _normalize_hardware(value.get("hardware")),
    }


def load_extension_config(root: Path | None = None) -> dict[str, Any]:
    """Load local configuration when valid, then fall back to the tracked example.

    Unknown fields are intentionally discarded; this permits a machine-local
    file to contain unrelated settings without surfacing secrets in a report.
    """

    resolved_root = _lexical_path(root or project_root())
    try:
        guarded_location(resolved_root, resolved_root, kind="directory")
    except ExtensionStorageError:
        return {
            "schema_version": EXTENSION_CONFIG_SCHEMA_VERSION,
            "source": "defaults",
            "hub_version": None,
            "platform": None,
            "enabled_extensions": [],
            "components": [],
            "models": [],
            "hardware": None,
        }
    for filename, source in (("extensions.local.json", "local"), ("extensions.example.json", "example")):
        candidate = resolved_root / "Config" / filename
        try:
            value = guarded_read_json(resolved_root, candidate, maximum=_MAX_CONFIG_BYTES)
        except ExtensionStorageError:
            continue
        normalized = _normalize_config(value, source=source)
        if normalized is not None:
            return normalized
    return {
        "schema_version": EXTENSION_CONFIG_SCHEMA_VERSION,
        "source": "defaults",
        "hub_version": None,
        "platform": None,
        "enabled_extensions": [],
        "components": [],
        "models": [],
        "hardware": None,
    }
