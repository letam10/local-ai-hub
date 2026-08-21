"""Server-owned, bounded Config backup and restore storage boundary.

The backup surface is intentionally narrow: fixed Config JSON leaves and
bounded Node Studio draft leaves are copied to an opaque ZIP backup and can
be restored only through a server-created, identity-bound plan. Filesystem
authority is established with no-follow lstat evidence before any resolved
containment check. Public results contain fixed categories and opaque IDs;
paths, archive names, exceptions and client values never cross the boundary.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import stat
import tempfile
import threading
import zipfile
from pathlib import Path
from typing import Any

from src.shared.paths.registry import CONFIG_ROOT


BACKUP_SCHEMA_VERSION = 1
DATA_CLASSES = ("settings", "creative_workspace", "workflow_library", "node_studio_drafts")

_MAX_BACKUP_SIZE_BYTES = 50 * 1024 * 1024
_MAX_MEMBER_COUNT = 200
_MAX_DECOMPRESSED_SIZE_BYTES = 100 * 1024 * 1024
_MAX_MEMBER_SIZE_BYTES = 8 * 1024 * 1024
_MAX_MANIFEST_SIZE_BYTES = 256 * 1024
_MAX_SOURCE_FILE_BYTES = 8 * 1024 * 1024
_MAX_PLAN_ENTRIES = 64
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

_FIXED_MEMBERS = frozenset({"settings.json", "creative_workspace.json", "workflow_library.json"})
_DRAFT_MEMBER_RE = re.compile(r"^drafts/(?:node_studio_draft_|draft_)[A-Za-z0-9_.-]{1,96}\.json$")
_BACKUP_ID_RE = re.compile(r"^backup_[A-Za-z0-9_-]{1,160}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_KEYS = frozenset({"schema_version", "created_at", "hub_backup_version", "included_data_classes", "files"})
_META_KEYS = frozenset({"sha256", "size_bytes"})
_SECRET_KEY_RE = re.compile(
    r"api[_-]?key|token|password|secret|credential|private[_-]?key|access[_-]?key|refresh[_-]?token",
    re.IGNORECASE,
)

_RESTORE_PLANS: dict[str, dict[str, Any]] = {}
_PLANS_LOCK = threading.RLock()


class _StorageUnsafe(Exception):
    """Private refusal signal. Its text is never returned to callers."""


class _ManifestInvalid(Exception):
    """Private archive schema refusal."""


@dataclass(frozen=True)
class _Identity:
    device: int
    inode: int
    mode: int
    size: int
    mtime_ns: int
    ctime_ns: int
    attributes: int


@dataclass(frozen=True)
class _Guard:
    path: Path
    kind: str
    anchor: Path | None
    target_identity: _Identity | None
    chain: tuple[tuple[str, _Identity], ...]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): ("[REDACTED]" if _SECRET_KEY_RE.search(str(key)) else _scrub(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


def _lexical(path: Path) -> Path:
    """Normalize without following a link or reparse point."""

    return Path(os.path.normpath(os.path.abspath(os.fspath(path))))


def _is_reparse(value: os.stat_result) -> bool:
    return stat.S_ISLNK(value.st_mode) or bool(int(getattr(value, "st_file_attributes", 0)) & _REPARSE_POINT)


def _identity(value: os.stat_result, *, directory: bool) -> _Identity:
    return _Identity(
        device=int(getattr(value, "st_dev", 0)),
        inode=int(getattr(value, "st_ino", 0)),
        mode=int(value.st_mode),
        size=0 if directory else int(value.st_size),
        mtime_ns=0 if directory else int(getattr(value, "st_mtime_ns", 0)),
        # Rename updates POSIX ctime even though the file identity and bytes
        # remain the same. The stable identity tuple therefore uses device,
        # inode, mode, size, mtime and platform attributes; ctime is omitted
        # from authority decisions to avoid rejecting our own atomic rename.
        ctime_ns=0,
        attributes=int(getattr(value, "st_file_attributes", 0)),
    )


def _safe_lstat(path: Path, *, kind: str | None = None) -> _Identity | None:
    try:
        value = os.lstat(path)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raise _StorageUnsafe from None
    if _is_reparse(value):
        raise _StorageUnsafe
    is_dir = stat.S_ISDIR(value.st_mode)
    is_file = stat.S_ISREG(value.st_mode)
    if kind == "dir" and not is_dir:
        raise _StorageUnsafe
    if kind == "file" and not is_file:
        raise _StorageUnsafe
    if kind is None and not (is_dir or is_file):
        raise _StorageUnsafe
    return _identity(value, directory=is_dir)


def _existing_ancestor(path: Path) -> Path:
    current = _lexical(path)
    while True:
        identity = _safe_lstat(current)
        if identity is not None:
            return current
        parent = current.parent
        if parent == current:
            raise _StorageUnsafe
        current = parent


def _ancestors(path: Path) -> list[Path]:
    result: list[Path] = []
    current = _lexical(path)
    while True:
        result.append(current)
        parent = current.parent
        if parent == current:
            return result
        current = parent


def _contained(anchor: Path, target: Path) -> bool:
    try:
        anchor_resolved = os.path.normcase(os.path.realpath(os.fspath(anchor)))
        target_resolved = os.path.normcase(os.path.realpath(os.fspath(target)))
        return os.path.commonpath((anchor_resolved, target_resolved)) == anchor_resolved
    except (OSError, ValueError):
        return False


def _guard(path: Path, *, kind: str, anchor: Path | None = None, allow_missing: bool = False) -> _Guard:
    path = _lexical(path)
    target_identity = _safe_lstat(path, kind=kind)
    if target_identity is None and not allow_missing:
        raise _StorageUnsafe
    active = path if target_identity is not None else _existing_ancestor(path.parent)
    chain: list[tuple[str, _Identity]] = []
    for ancestor in _ancestors(active):
        value = _safe_lstat(ancestor)
        if value is not None:
            chain.append((os.path.normcase(os.fspath(ancestor)), value))
    if not chain:
        raise _StorageUnsafe
    anchor_value = _lexical(anchor) if anchor is not None else None
    if anchor_value is not None:
        anchor_identity = _safe_lstat(anchor_value, kind="dir")
        if anchor_identity is None or not _contained(anchor_value, path):
            raise _StorageUnsafe
    return _Guard(path, kind, anchor_value, target_identity, tuple(chain))


def _guard_same(expected: _Guard) -> bool:
    try:
        current = _guard(
            expected.path,
            kind=expected.kind,
            anchor=expected.anchor,
            allow_missing=expected.target_identity is None,
        )
    except _StorageUnsafe:
        return False
    return current.target_identity == expected.target_identity and current.chain == expected.chain


def _strict_json(payload: bytes) -> Any:
    class _Duplicate(ValueError):
        pass

    class _NonFinite(ValueError):
        pass

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in items:
            if key in result:
                raise _Duplicate
            result[key] = item
        return result

    try:
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(_NonFinite()),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _Duplicate, _NonFinite, TypeError, ValueError, OverflowError, RecursionError):
        raise _ManifestInvalid from None


def _read_bounded(path: Path, guard: _Guard, limit: int) -> bytes:
    if not _guard_same(guard):
        raise _StorageUnsafe
    try:
        with path.open("rb") as stream:
            payload = stream.read(limit + 1)
    except (OSError, ValueError):
        raise _StorageUnsafe from None
    if len(payload) > limit or not _guard_same(guard):
        raise _StorageUnsafe
    return payload


def _sha256_file(path: Path, guard: _Guard, limit: int = _MAX_BACKUP_SIZE_BYTES) -> str:
    if not _guard_same(guard):
        raise _StorageUnsafe
    digest = hashlib.sha256()
    total = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                total += len(chunk)
                if total > limit:
                    raise _StorageUnsafe
                digest.update(chunk)
    except _StorageUnsafe:
        raise
    except (OSError, ValueError):
        raise _StorageUnsafe from None
    if not _guard_same(guard):
        raise _StorageUnsafe
    return digest.hexdigest()


def _fsync(path: Path) -> None:
    flags = os.O_RDWR | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _safe_member(member: object) -> bool:
    if not isinstance(member, str) or not member or len(member) > 160:
        return False
    if "\\" in member or member.startswith(("/", "\\")) or ":" in member:
        return False
    parts = member.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return False
    return member in _FIXED_MEMBERS or _DRAFT_MEMBER_RE.fullmatch(member) is not None


def _public_member(member: str) -> str:
    return member if member in _FIXED_MEMBERS else "drafts"


def _public_category(member: str) -> str:
    return {
        "settings.json": "settings",
        "creative_workspace.json": "creative_workspace",
        "workflow_library.json": "workflow_library",
    }.get(member, "drafts")


def _public_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    files = manifest.get("files")
    members = files.keys() if isinstance(files, Mapping) else []
    return {
        "schema_version": manifest.get("schema_version"),
        "hub_backup_version": manifest.get("hub_backup_version"),
        "included_data_classes": list(DATA_CLASSES),
        "member_count": len(files) if isinstance(files, Mapping) else 0,
        "categories": sorted({_public_category(str(member)) for member in members}),
    }


def _failure(code: str = "storage_unavailable", *, status: str = "unavailable") -> dict[str, Any]:
    reasons = {
        "storage_unavailable": "Local backup storage is unavailable; no data was changed.",
        "invalid_archive": "The backup archive is not a valid server-owned backup.",
        "restore_conflict": "Local data changed after the restore plan was created.",
        "restore_manual_review": "Restore state requires manual review; no unsafe cleanup was attempted.",
        "restore_transaction_failed": "Restore could not complete atomically; prior data was restored or manual review is required.",
        "backup_create_failed": "Backup creation could not complete; no public backup was published.",
    }
    return {
        "accepted": False,
        "status": status,
        "code": code,
        "reason": reasons.get(code, reasons["storage_unavailable"]),
        "execution": "not_run",
        "dry_run": True,
        "verified": False,
    }


def _safe_json(path: Path, guard: _Guard | None = None) -> bytes:
    current = guard or _guard(path, kind="file", allow_missing=False)
    try:
        raw = _strict_json(_read_bounded(path, current, _MAX_SOURCE_FILE_BYTES))
    except _ManifestInvalid:
        return b'{"status":"unreadable"}'
    cleaned = _scrub(raw)
    try:
        result = json.dumps(cleaned, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError):
        return b'{"status":"unreadable"}'
    if len(result) > _MAX_MEMBER_SIZE_BYTES or not _guard_same(current):
        raise _StorageUnsafe
    return result


def _collect_files_guarded(config_guard: _Guard) -> dict[str, tuple[Path, _Guard]]:
    if not _guard_same(config_guard):
        raise _StorageUnsafe
    root = config_guard.path
    result: dict[str, tuple[Path, _Guard]] = {}
    for member in sorted(_FIXED_MEMBERS):
        path = root / member
        guard = _guard(path, kind="file", anchor=root, allow_missing=True)
        if guard.target_identity is not None:
            result[member] = (path, guard)
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                name = entry.name
                if not (name.startswith("node_studio_draft_") or name.startswith("draft_")) or not name.endswith(".json"):
                    continue
                member = f"drafts/{name}"
                if not _safe_member(member) or entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    raise _StorageUnsafe
                path = root / name
                result[member] = (path, _guard(path, kind="file", anchor=root, allow_missing=False))
    except _StorageUnsafe:
        raise
    except OSError:
        raise _StorageUnsafe from None
    return result


def _compute_state_fingerprint() -> str:
    try:
        root_guard = _guard(CONFIG_ROOT, kind="dir", allow_missing=False)
        files = _collect_files_guarded(root_guard)
        digest = hashlib.sha256()
        for member in sorted(files):
            path, guard = files[member]
            content = _read_bounded(path, guard, _MAX_SOURCE_FILE_BYTES)
            digest.update(member.encode("utf-8"))
            digest.update(_sha256_bytes(content).encode("ascii"))
        return digest.hexdigest()
    except _StorageUnsafe:
        return ""


def _remove_temp_file(path: Path, guard: _Guard | None, parent_guard: _Guard | None = None) -> None:
    try:
        if guard is not None:
            if not _guard_same(guard):
                return
        elif parent_guard is not None:
            if not _guard_same(parent_guard):
                return
            guard = _guard(path, kind="file", anchor=parent_guard.path, allow_missing=False)
        else:
            return
        path.unlink()
    except (_StorageUnsafe, OSError, ValueError):
        return


def _remove_temp_dir(path: Path, guard: _Guard | None) -> None:
    if guard is None:
        return
    try:
        if not _guard_same(guard):
            return
        children = list(os.scandir(path))
        for entry in children:
            if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                return
        for entry in children:
            child = Path(entry.path)
            child_guard = _guard(child, kind="file", anchor=path, allow_missing=False)
            if not _guard_same(child_guard):
                return
            child.unlink()
        path.rmdir()
    except (_StorageUnsafe, OSError, ValueError):
        return


def _zip_info_is_safe(info: zipfile.ZipInfo) -> bool:
    if info.is_dir() or info.filename.endswith("/"):
        return False
    mode = (info.external_attr >> 16) & 0o170000
    return mode not in {stat.S_IFLNK, stat.S_IFDIR}


def _parse_archive_manifest(archive: zipfile.ZipFile) -> dict[str, Any]:
    infos = archive.infolist()
    if len(infos) > _MAX_MEMBER_COUNT or len({info.filename for info in infos}) != len(infos):
        raise _ManifestInvalid
    total = 0
    names: list[str] = []
    for info in infos:
        name = info.filename
        if name != "manifest.json" and not _safe_member(name):
            raise _ManifestInvalid
        if not _zip_info_is_safe(info) or info.file_size > _MAX_MEMBER_SIZE_BYTES:
            raise _ManifestInvalid
        total += int(info.file_size)
        if total > _MAX_DECOMPRESSED_SIZE_BYTES:
            raise _ManifestInvalid
        names.append(name)
    if "manifest.json" not in names:
        raise _ManifestInvalid
    manifest_info = next(info for info in infos if info.filename == "manifest.json")
    if manifest_info.file_size > _MAX_MANIFEST_SIZE_BYTES:
        raise _ManifestInvalid
    try:
        manifest = _strict_json(archive.read("manifest.json"))
    except (KeyError, OSError, RuntimeError, ValueError, TypeError, _ManifestInvalid):
        raise _ManifestInvalid from None
    if type(manifest) is not dict or set(manifest) != _MANIFEST_KEYS:
        raise _ManifestInvalid
    schema_version = manifest.get("schema_version")
    if type(schema_version) is not int or isinstance(schema_version, bool) or schema_version != BACKUP_SCHEMA_VERSION:
        raise _ManifestInvalid
    created_at = manifest.get("created_at")
    if type(created_at) is not str or len(created_at) > 80:
        raise _ManifestInvalid
    try:
        datetime.fromisoformat(created_at)
    except (TypeError, ValueError, OverflowError):
        raise _ManifestInvalid from None
    version = manifest.get("hub_backup_version")
    if type(version) is not str or not re.fullmatch(r"[0-9]+\.[0-9]+", version):
        raise _ManifestInvalid
    included = manifest.get("included_data_classes")
    if type(included) is not list or included != list(DATA_CLASSES):
        raise _ManifestInvalid
    files = manifest.get("files")
    if type(files) is not dict or len(files) > _MAX_PLAN_ENTRIES:
        raise _ManifestInvalid
    expected_names = {"manifest.json"}
    for member, meta in files.items():
        if not _safe_member(member) or type(meta) is not dict or set(meta) != _META_KEYS:
            raise _ManifestInvalid
        sha256 = meta.get("sha256")
        size_bytes = meta.get("size_bytes")
        if type(sha256) is not str or _SHA256_RE.fullmatch(sha256) is None:
            raise _ManifestInvalid
        if type(size_bytes) is not int or isinstance(size_bytes, bool) or not 0 <= size_bytes <= _MAX_MEMBER_SIZE_BYTES:
            raise _ManifestInvalid
        expected_names.add(member)
    if set(names) != expected_names:
        raise _ManifestInvalid
    for member, meta in files.items():
        content = archive.read(member)
        if len(content) != meta["size_bytes"] or _sha256_bytes(content) != meta["sha256"]:
            raise _ManifestInvalid
    return manifest


class BackupManager:
    """Create, inspect, plan and apply bounded server-owned backups."""

    def __init__(self, backup_dir: Path | None = None) -> None:
        self._backup_dir = Path(backup_dir) if backup_dir is not None else CONFIG_ROOT / "backups"
        self._lock = threading.RLock()

    def _config_guard(self) -> _Guard:
        return _guard(CONFIG_ROOT, kind="dir", allow_missing=False)

    def _backup_dir_guard(self, config_guard: _Guard | None = None, *, allow_missing: bool = False) -> _Guard:
        anchor = config_guard.path if config_guard is not None else CONFIG_ROOT
        return _guard(self._backup_dir, kind="dir", anchor=anchor, allow_missing=allow_missing)

    def _ensure_backup_dir(self, config_guard: _Guard) -> _Guard:
        guard = self._backup_dir_guard(config_guard, allow_missing=True)
        if guard.target_identity is not None:
            return guard
        if _lexical(self._backup_dir).parent != config_guard.path or not _guard_same(config_guard):
            raise _StorageUnsafe
        self._backup_dir.mkdir(parents=False, exist_ok=True)
        return self._backup_dir_guard(config_guard, allow_missing=False)

    def _backup_id_for_file(self, path: Path) -> str:
        clean = re.sub(r"[^A-Za-z0-9_-]", "_", path.stem)
        return f"backup_{clean}"

    def _resolve_backup_target(self, backup_id: str) -> Path | None:
        if not isinstance(backup_id, str) or _BACKUP_ID_RE.fullmatch(backup_id) is None:
            return None
        try:
            backup_guard = self._backup_dir_guard(allow_missing=False)
            matches: list[Path] = []
            with os.scandir(backup_guard.path) as entries:
                for entry in entries:
                    if not entry.name.endswith(".zip") or entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                        continue
                    candidate = Path(entry.path)
                    if self._backup_id_for_file(candidate) == backup_id:
                        _guard(candidate, kind="file", anchor=backup_guard.path, allow_missing=False)
                        matches.append(candidate)
            if len(matches) != 1:
                return None
            return _lexical(matches[0])
        except (_StorageUnsafe, OSError, ValueError):
            return None

    def _archive_guard(self, path: Path) -> _Guard:
        backup_guard = self._backup_dir_guard(allow_missing=False)
        return _guard(path, kind="file", anchor=backup_guard.path, allow_missing=False)

    def list_backups(self) -> list[dict[str, Any]]:
        try:
            backup_guard = self._backup_dir_guard(allow_missing=False)
            result: list[dict[str, Any]] = []
            with os.scandir(backup_guard.path) as entries:
                for entry in entries:
                    if not entry.name.endswith(".zip") or entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                        continue
                    path = Path(entry.path)
                    guard = _guard(path, kind="file", anchor=backup_guard.path, allow_missing=False)
                    identity = guard.target_identity
                    if identity is None or identity.size > _MAX_BACKUP_SIZE_BYTES:
                        continue
                    result.append({
                        "backup_id": self._backup_id_for_file(path),
                        "created_at": datetime.fromtimestamp(identity.mtime_ns / 1_000_000_000, tz=timezone.utc).isoformat(),
                        "size_bytes": identity.size,
                    })
            return sorted(result, key=lambda item: str(item["created_at"]), reverse=True)
        except (_StorageUnsafe, OSError, ValueError, OverflowError):
            return []

    def create_backup(self) -> dict[str, Any]:
        temporary: Path | None = None
        temporary_guard: _Guard | None = None
        backup_guard: _Guard | None = None
        try:
            with self._lock:
                config_guard = self._config_guard()
                backup_guard = self._ensure_backup_dir(config_guard)
                files = _collect_files_guarded(config_guard)
                manifest: dict[str, Any] = {
                    "schema_version": BACKUP_SCHEMA_VERSION,
                    "created_at": _now_iso(),
                    "hub_backup_version": "1.0",
                    "included_data_classes": list(DATA_CLASSES),
                    "files": {},
                }
                with tempfile.NamedTemporaryFile(dir=backup_guard.path, prefix=".backup-", suffix=".zip.tmp", delete=False) as handle:
                    temporary = Path(handle.name)
                with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    for member in sorted(files):
                        path, guard = files[member]
                        content = _safe_json(path, guard)
                        manifest["files"][member] = {"sha256": _sha256_bytes(content), "size_bytes": len(content)}
                        archive.writestr(member, content)
                    archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
                _fsync(temporary)
                temporary_guard = _guard(temporary, kind="file", anchor=backup_guard.path, allow_missing=False)
                target_name = f"hub-backup-{hashlib.sha256(manifest['created_at'].encode('ascii')).hexdigest()[:24]}.zip"
                target = backup_guard.path / target_name
                target_guard = _guard(target, kind="file", anchor=backup_guard.path, allow_missing=True)
                if target_guard.target_identity is not None or not _guard_same(config_guard) or not _guard_same(backup_guard) or not _guard_same(temporary_guard):
                    raise _StorageUnsafe
                os.replace(temporary, target)
                temporary = None
                published = _guard(target, kind="file", anchor=backup_guard.path, allow_missing=False)
                if not _guard_same(config_guard) or not _guard_same(backup_guard) or published.target_identity is None:
                    raise _StorageUnsafe
                return {"accepted": True, "backup_id": self._backup_id_for_file(target), "manifest": _public_manifest(manifest)}
        except (_StorageUnsafe, OSError, ValueError, TypeError, zipfile.BadZipFile, OverflowError):
            return _failure("backup_create_failed")
        finally:
            if temporary is not None:
                _remove_temp_file(temporary, temporary_guard, backup_guard)

    def _inspect_backup_internal(self, backup_id: str | Path) -> dict[str, Any]:
        try:
            if isinstance(backup_id, Path):
                backup_guard = self._backup_dir_guard(allow_missing=False)
                candidate = _lexical(backup_id)
                if not _contained(backup_guard.path, candidate):
                    raise _StorageUnsafe
                path = candidate
            else:
                path = self._resolve_backup_target(backup_id)
                if path is None:
                    raise _StorageUnsafe
            guard = self._archive_guard(path)
            identity = guard.target_identity
            if identity is None or identity.size > _MAX_BACKUP_SIZE_BYTES:
                raise _StorageUnsafe
            digest = _sha256_file(path, guard)
            with zipfile.ZipFile(path, "r") as archive:
                manifest = _parse_archive_manifest(archive)
            if not _guard_same(guard):
                raise _StorageUnsafe
            return {"valid": True, "manifest": manifest, "errors": [], "sha256": digest}
        except (_StorageUnsafe, OSError, ValueError, TypeError, zipfile.BadZipFile, RuntimeError, EOFError, KeyError, UnicodeError, _ManifestInvalid):
            return {"valid": False, "manifest": None, "errors": [_failure("invalid_archive")["reason"]]}

    def inspect_backup(self, backup_id: str | Path) -> dict[str, Any]:
        result = self._inspect_backup_internal(backup_id)
        if result.get("valid") and isinstance(result.get("manifest"), Mapping):
            result["manifest"] = _public_manifest(result["manifest"])
        return result

    def _member_target_guard(self, member: str, config_guard: _Guard) -> tuple[Path, _Guard]:
        if not _safe_member(member):
            raise _StorageUnsafe
        if member in _FIXED_MEMBERS:
            target_name = member
        elif _DRAFT_MEMBER_RE.fullmatch(member) is not None:
            target_name = member.removeprefix("drafts/")
        else:
            raise _StorageUnsafe
        target = _lexical(config_guard.path / target_name)
        return target, _guard(target, kind="file", anchor=config_guard.path, allow_missing=True)

    def _state_fingerprint(self, config_guard: _Guard) -> str:
        if not _guard_same(config_guard):
            raise _StorageUnsafe
        value = _compute_state_fingerprint()
        if not value:
            raise _StorageUnsafe
        return value

    def plan_restore(self, backup_id: str | Path) -> dict[str, Any]:
        actual_id = self._backup_id_for_file(backup_id) if isinstance(backup_id, Path) else backup_id
        inspected = self._inspect_backup_internal(backup_id)
        if not inspected.get("valid"):
            return _failure("invalid_archive")
        try:
            config_guard = self._config_guard()
            state_fingerprint = self._state_fingerprint(config_guard)
            manifest = inspected["manifest"]
            entries: list[dict[str, Any]] = []
            changes: list[dict[str, Any]] = []
            category_counts = {"settings": 0, "creative_workspace": 0, "workflow_library": 0, "drafts": 0}
            try:
                backup_epoch = datetime.fromisoformat(str(manifest["created_at"])).timestamp()
            except (TypeError, ValueError, OverflowError):
                raise _StorageUnsafe
            for member, meta in manifest["files"].items():
                target, target_guard = self._member_target_guard(member, config_guard)
                prior_identity = target_guard.target_identity
                prior_bytes = _read_bounded(target, target_guard, _MAX_MEMBER_SIZE_BYTES) if prior_identity is not None else None
                action = "create" if prior_identity is None else "overwrite"
                if prior_identity is not None and prior_identity.mtime_ns / 1_000_000_000 > backup_epoch + 5:
                    action = "skip_newer"
                category_counts[_public_category(member)] += 1
                changes.append({"member": _public_member(member), "action": action, "size_bytes": int(meta["size_bytes"]), "reason": "Review the fixed Config member before confirmation."})
                entries.append({"member": member, "action": action, "target": target, "prior_identity": prior_identity, "prior_bytes": prior_bytes})
            if len(entries) > _MAX_PLAN_ENTRIES or not _guard_same(config_guard):
                raise _StorageUnsafe
            plan_id = f"plan_{hashlib.sha256(os.urandom(16)).hexdigest()[:24]}"
            public = {
                "plan_id": plan_id,
                "backup_id": actual_id,
                "backup_sha256": inspected["sha256"],
                "state_fingerprint": state_fingerprint,
                "created_at": _now_iso(),
                "changes": changes,
                "newer_protected": [_public_member(item["member"]) for item in entries if item["action"] == "skip_newer"],
                "preview": {
                    "total": len(changes),
                    "overwrite": sum(item["action"] == "overwrite" for item in changes),
                    "create": sum(item["action"] == "create" for item in changes),
                    "skip_newer": sum(item["action"] == "skip_newer" for item in changes),
                },
                "categories": {key: value for key, value in category_counts.items() if value},
            }
            with _PLANS_LOCK:
                _RESTORE_PLANS[plan_id] = {**public, "_entries": entries}
            return {"accepted": True, **public}
        except (_StorageUnsafe, OSError, ValueError, TypeError):
            return _failure("storage_unavailable")

    def _ensure_parent(self, target: Path, config_guard: _Guard) -> _Guard:
        parent = _lexical(target.parent)
        if parent != config_guard.path:
            raise _StorageUnsafe
        return _guard(parent, kind="dir", anchor=config_guard.path, allow_missing=False)

    def _atomic_restore_bytes(self, target: Path, content: bytes, expected: _Identity, config_guard: _Guard) -> bool:
        temporary: Path | None = None
        temporary_guard: _Guard | None = None
        parent_guard: _Guard | None = None
        try:
            parent_guard = self._ensure_parent(target, config_guard)
            target_guard = _guard(target, kind="file", anchor=config_guard.path, allow_missing=False)
            if target_guard.target_identity != expected:
                return False
            with tempfile.NamedTemporaryFile(dir=parent_guard.path, prefix=".restore-", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            temporary_guard = _guard(temporary, kind="file", anchor=parent_guard.path, allow_missing=False)
            if not _guard_same(config_guard) or not _guard_same(target_guard) or not _guard_same(temporary_guard):
                return False
            os.replace(temporary, target)
            temporary = None
            final = _guard(target, kind="file", anchor=config_guard.path, allow_missing=False)
            return final.target_identity is not None
        except (_StorageUnsafe, OSError, ValueError):
            return False
        finally:
            if temporary is not None:
                _remove_temp_file(temporary, temporary_guard, parent_guard)

    def _rollback(self, applied: list[dict[str, Any]], config_guard: _Guard) -> bool:
        for item in reversed(applied):
            target = item["target"]
            try:
                current = _guard(target, kind="file", anchor=config_guard.path, allow_missing=False)
                if current.target_identity != item["applied_identity"]:
                    return False
                prior_bytes = item["prior_bytes"]
                if prior_bytes is None:
                    if not _guard_same(current):
                        return False
                    target.unlink()
                    if _guard(target, kind="file", anchor=config_guard.path, allow_missing=True).target_identity is not None:
                        return False
                elif not self._atomic_restore_bytes(target, prior_bytes, item["applied_identity"], config_guard):
                    return False
            except (_StorageUnsafe, OSError, ValueError):
                return False
        return True

    def apply_restore(self, plan_id: str, *, confirmed: bool) -> dict[str, Any]:
        if not confirmed:
            return {"accepted": False, "status": "unconfirmed", "reason": "Restore requires explicit confirmation.", "execution": "not_run", "dry_run": True}
        with _PLANS_LOCK:
            plan = _RESTORE_PLANS.get(plan_id)
        if not isinstance(plan, dict):
            return {"accepted": False, "status": "not_found", "reason": "Restore plan is unavailable.", "execution": "not_run", "dry_run": True}
        applied: list[dict[str, Any]] = []
        stage_root: Path | None = None
        stage_guard: _Guard | None = None
        try:
            with self._lock:
                backup_path = self._resolve_backup_target(plan.get("backup_id"))
                if backup_path is None:
                    return _failure("invalid_archive")
                inspected = self._inspect_backup_internal(plan.get("backup_id"))
                if not inspected.get("valid") or inspected.get("sha256") != plan.get("backup_sha256"):
                    return _failure("invalid_archive")
                config_guard = self._config_guard()
                if self._state_fingerprint(config_guard) != plan.get("state_fingerprint"):
                    return {**_failure("restore_conflict", status="conflict"), "code": 409}
                archive_guard = self._archive_guard(backup_path)
                manifest = inspected["manifest"]
                entries = plan.get("_entries")
                if not isinstance(entries, list) or len(entries) > _MAX_PLAN_ENTRIES:
                    return _failure("restore_manual_review")
                stage_root = Path(tempfile.mkdtemp(prefix=".backup-restore-", dir=config_guard.path))
                stage_guard = _guard(stage_root, kind="dir", anchor=config_guard.path, allow_missing=False)
                staged: list[dict[str, Any]] = []
                with zipfile.ZipFile(backup_path, "r") as archive:
                    for entry in entries:
                        member = entry.get("member")
                        if entry.get("action") == "skip_newer":
                            continue
                        if not _safe_member(member) or member not in manifest["files"]:
                            raise _StorageUnsafe
                        target = entry.get("target")
                        if not isinstance(target, Path):
                            raise _StorageUnsafe
                        target_guard = _guard(target, kind="file", anchor=config_guard.path, allow_missing=True)
                        expected_identity = entry.get("prior_identity")
                        expected_bytes = entry.get("prior_bytes")
                        if target_guard.target_identity != expected_identity:
                            raise _StorageUnsafe
                        if expected_identity is not None and _read_bounded(target, target_guard, _MAX_MEMBER_SIZE_BYTES) != expected_bytes:
                            raise _StorageUnsafe
                        self._ensure_parent(target, config_guard)
                        content = archive.read(member)
                        meta = manifest["files"][member]
                        if len(content) != meta["size_bytes"] or _sha256_bytes(content) != meta["sha256"]:
                            raise _StorageUnsafe
                        stage = stage_root / f"{_sha256_bytes(member.encode('utf-8'))[:24]}.tmp"
                        with stage.open("wb") as stream:
                            stream.write(content)
                            stream.flush()
                            os.fsync(stream.fileno())
                        staged_guard = _guard(stage, kind="file", anchor=stage_root, allow_missing=False)
                        staged.append({"entry": entry, "target_guard": target_guard, "stage": stage, "stage_guard": staged_guard})
                if not _guard_same(config_guard) or not _guard_same(archive_guard) or not _guard_same(stage_guard):
                    raise _StorageUnsafe
                for item in staged:
                    entry = item["entry"]
                    target = entry["target"]
                    current = _guard(target, kind="file", anchor=config_guard.path, allow_missing=True)
                    if current.target_identity != entry.get("prior_identity") or not _guard_same(item["stage_guard"]):
                        raise _StorageUnsafe
                    if not _guard_same(config_guard) or not _guard_same(archive_guard):
                        raise _StorageUnsafe
                    os.replace(item["stage"], target)
                    item["stage"] = None
                    applied.append({"target": target, "prior_bytes": entry.get("prior_bytes"), "applied_identity": item["stage_guard"].target_identity})
                    if not _guard_same(config_guard) or not _guard_same(archive_guard):
                        raise _StorageUnsafe
                    final = _guard(target, kind="file", anchor=config_guard.path, allow_missing=False)
                    if final.target_identity != item["stage_guard"].target_identity:
                        raise _StorageUnsafe
                applied_members = [item["entry"]["member"] for item in staged]
                result = {"accepted": True, "applied": [_public_member(member) for member in applied_members], "skipped": [item["member"] for item in plan.get("changes", []) if item.get("action") == "skip_newer"], "verified": True}
                with _PLANS_LOCK:
                    _RESTORE_PLANS.pop(plan_id, None)
                return result
        except (_StorageUnsafe, OSError, ValueError, TypeError, zipfile.BadZipFile, RuntimeError, EOFError, KeyError):
            if applied:
                try:
                    rollback_guard = self._config_guard()
                except _StorageUnsafe:
                    rollback_guard = None
                rolled_back = rollback_guard is not None and self._rollback(applied, rollback_guard)
            else:
                rolled_back = True
            return _failure("restore_transaction_failed" if rolled_back else "restore_manual_review")
        finally:
            if stage_root is not None:
                _remove_temp_dir(stage_root, stage_guard)

    def verify_restore(self, result: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(result, dict) or not result.get("accepted"):
            return {"valid": False, "errors": ["restore_not_accepted"]}
        try:
            config_guard = self._config_guard()
            members = result.get("applied")
            if not isinstance(members, list) or any(member == "drafts" or not _safe_member(member) for member in members):
                raise _StorageUnsafe
            for member in members:
                target, guard = self._member_target_guard(member, config_guard)
                if guard.target_identity is None or not _guard_same(guard):
                    raise _StorageUnsafe
                _strict_json(_read_bounded(target, guard, _MAX_MEMBER_SIZE_BYTES))
            return {"valid": True, "errors": []}
        except (_StorageUnsafe, OSError, ValueError, TypeError, _ManifestInvalid):
            return {"valid": False, "errors": ["restore_verification_unavailable"]}
