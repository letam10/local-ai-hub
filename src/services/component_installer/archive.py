"""Archive extraction with traversal, link and size limits."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
import tarfile
import zipfile
import os
import stat


class ArchiveSafetyError(ValueError):
    pass


def _safe_member(name: str, destination: Path) -> Path:
    if not isinstance(name, str) or not name or "\x00" in name:
        raise ArchiveSafetyError("invalid_archive_member")
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ":" in normalized or ".." in path.parts or any(part in {"", "."} for part in path.parts):
        raise ArchiveSafetyError("archive_path_escape")
    candidate = destination / Path(*path.parts)
    try:
        candidate.resolve().relative_to(destination.resolve())
    except (OSError, ValueError) as exc:
        raise ArchiveSafetyError("archive_path_escape") from exc
    return candidate


def _reparse(path: Path) -> bool:
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

            attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
            attrs = int(attrs) & 0xFFFFFFFF
            return attrs != 0xFFFFFFFF and bool(attrs & 0x400)
        except (AttributeError, OSError):
            return False
    return False


def _validate_destination(destination: Path) -> None:
    current = destination.absolute()
    while current.parent != current:
        if _reparse(current):
            raise ArchiveSafetyError("reparse_staging_root")
        current = current.parent
    if _reparse(current):
        raise ArchiveSafetyError("reparse_staging_root")


def safe_extract_archive(archive: Path, destination: Path, *, max_members: int = 10000, max_bytes: int = 8 * 1024 * 1024 * 1024) -> list[str]:
    """Extract a zip/tar archive into a task-owned staging directory."""

    if not archive.is_file() or archive.is_symlink():
        raise ArchiveSafetyError("archive_unavailable")
    _validate_destination(destination)
    destination.mkdir(parents=True, exist_ok=True)
    _validate_destination(destination)
    names: list[str] = []
    total = 0
    seen: set[str] = set()
    suffix = archive.suffix.casefold()
    if suffix == ".zip":
        opener = zipfile.ZipFile(archive)
        with opener as handle:
            infos = handle.infolist()
            if len(infos) > max_members:
                raise ArchiveSafetyError("archive_member_limit")
            for info in infos:
                target = _safe_member(info.filename, destination)
                if info.filename in seen:
                    raise ArchiveSafetyError("duplicate_archive_member")
                seen.add(info.filename)
                if target.exists() and _reparse(target):
                    raise ArchiveSafetyError("archive_reparse_member")
                is_link = (info.external_attr >> 16) & 0o170000 == 0o120000
                if is_link:
                    raise ArchiveSafetyError("archive_link_member")
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                total += max(0, int(info.file_size))
                if total > max_bytes:
                    raise ArchiveSafetyError("archive_size_limit")
                target.parent.mkdir(parents=True, exist_ok=True)
                _validate_destination(target.parent)
                with handle.open(info, "r") as source, target.open("xb") as output:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                names.append(info.filename)
        return names
    if suffix in {".tar", ".gz", ".tgz", ".bz2", ".xz", ".zst"} or archive.name.casefold().endswith(".tar.gz"):
        with tarfile.open(archive, "r:*") as handle:
            members = handle.getmembers()
            if len(members) > max_members:
                raise ArchiveSafetyError("archive_member_limit")
            for member in members:
                target = _safe_member(member.name, destination)
                if member.name in seen:
                    raise ArchiveSafetyError("duplicate_archive_member")
                seen.add(member.name)
                if target.exists() and _reparse(target):
                    raise ArchiveSafetyError("archive_reparse_member")
                if member.issym() or member.islnk() or member.isdev():
                    raise ArchiveSafetyError("archive_link_member")
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    raise ArchiveSafetyError("archive_member_type")
                total += max(0, int(member.size))
                if total > max_bytes:
                    raise ArchiveSafetyError("archive_size_limit")
                source = handle.extractfile(member)
                if source is None:
                    raise ArchiveSafetyError("archive_member_unreadable")
                target.parent.mkdir(parents=True, exist_ok=True)
                _validate_destination(target.parent)
                with source, target.open("xb") as output:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                names.append(member.name)
        return names
    raise ArchiveSafetyError("unsupported_archive_type")


__all__ = ["ArchiveSafetyError", "safe_extract_archive"]
