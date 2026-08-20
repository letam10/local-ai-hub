"""Build the reviewed Local AI Hub Core release from explicit safe inputs.

The Core archive writer is deterministic: source entries are sorted, ZIP
timestamps and permissions are fixed, and source bytes are copied in bounded
chunks. It refuses existing outputs and never pads an archive. Runtime, model,
cache and user-data payloads remain outside the release boundary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "distribution" / "core.manifest.json"
DEFAULT_OUTPUT = ROOT / "distribution" / "output" / "LocalAIHub-Core-Win64.zip"
SOURCE_DATE_EPOCH = 315532800
ZIP_ENTRY_DATE_TIME = (1980, 1, 1, 0, 0, 0)


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        raise ValueError("CORE_MANIFEST_UNREADABLE") from None
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("CORE_MANIFEST_SCHEMA_INVALID")
    return value


def git_tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    if result.returncode:
        raise RuntimeError("GIT_TRACKED_FILES_UNAVAILABLE")
    try:
        names = [part.decode("utf-8") for part in result.stdout.split(b"\0") if part]
    except UnicodeDecodeError:
        raise RuntimeError("GIT_TRACKED_FILES_UNAVAILABLE") from None
    return [ROOT / part for part in sorted(set(names))]


def relative(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        raise ValueError("SOURCE_PATH_OUTSIDE_REPOSITORY") from None


def selected_source_files(manifest: dict[str, Any]) -> list[Path]:
    roots_value = manifest.get("tracked_source_roots", [])
    files_value = manifest.get("tracked_source_files", [])
    excluded_value = manifest.get("exclude_roots", [])
    suffix_value = manifest.get("forbidden_suffixes", [])
    if not all(isinstance(value, list) for value in (roots_value, files_value, excluded_value, suffix_value)):
        raise ValueError("CORE_MANIFEST_SELECTION_INVALID")
    roots = {str(item).strip("/") for item in roots_value if isinstance(item, str)}
    files = {str(item) for item in files_value if isinstance(item, str)}
    excluded = {str(item).casefold() for item in excluded_value if isinstance(item, str)}
    suffixes = {str(item).casefold() for item in suffix_value if isinstance(item, str)}
    selected: list[Path] = []
    for path in git_tracked_files():
        rel = relative(path)
        parts = PurePosixPath(rel).parts
        if not parts or parts[0].casefold() in excluded:
            continue
        if rel == "distribution/release_manifest.json":
            continue
        if path.suffix.casefold() in suffixes:
            raise ValueError("FORBIDDEN_FILE_SELECTED")
        if rel in files or parts[0] in roots:
            if path.is_symlink() or not path.is_file():
                raise ValueError("SOURCE_FILE_UNAVAILABLE")
            selected.append(path)
    return sorted(selected, key=relative)


def _runtime_files(path: Path, namespace: str, forbidden_tokens: set[str], forbidden_suffixes: set[str]) -> list[tuple[Path, str]]:
    if not path.is_dir() or path.is_symlink():
        raise ValueError("STAGING_DIRECTORY_UNAVAILABLE")
    selected: list[tuple[Path, str]] = []
    for candidate in path.rglob("*"):
        if not candidate.is_file() or candidate.is_symlink():
            continue
        rel = candidate.relative_to(path)
        parts = {part.casefold() for part in rel.parts}
        if parts & forbidden_tokens or candidate.suffix.casefold() in forbidden_suffixes:
            raise ValueError("FORBIDDEN_RUNTIME_PAYLOAD")
        selected.append((candidate, f"{namespace}/{rel.as_posix()}"))
    if not selected:
        raise ValueError("STAGING_DIRECTORY_EMPTY")
    return sorted(selected, key=lambda item: item[1])


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except (OSError, ValueError):
        raise ValueError("ARTIFACT_READ_FAILED") from None
    return digest.hexdigest()


def describe(manifest: dict[str, Any], runtime_dir: Path | None, wheelhouse_dir: Path | None) -> tuple[list[tuple[Path, str]], list[str]]:
    source = [(path, relative(path)) for path in selected_source_files(manifest)]
    notices: list[str] = []
    if runtime_dir is None:
        notices.append("RUNTIME_STAGING_REQUIRED")
    if wheelhouse_dir is None:
        notices.append("WHEELHOUSE_STAGING_REQUIRED")
    if runtime_dir is None or wheelhouse_dir is None:
        return source, notices
    forbidden_tokens = {str(item).casefold() for item in manifest.get("forbidden_runtime_path_tokens", []) if isinstance(item, str)}
    forbidden_suffixes = {str(item).casefold() for item in manifest.get("forbidden_suffixes", []) if isinstance(item, str)}
    portable_runtime = manifest.get("portable_runtime")
    wheelhouse = manifest.get("wheelhouse")
    if not isinstance(portable_runtime, dict) or not isinstance(wheelhouse, dict):
        raise ValueError("CORE_MANIFEST_STAGING_POLICY_INVALID")
    if not isinstance(portable_runtime.get("destination"), str) or not isinstance(wheelhouse.get("destination"), str):
        raise ValueError("CORE_MANIFEST_STAGING_POLICY_INVALID")
    runtime_destination = portable_runtime["destination"].strip("/")
    wheelhouse_destination = wheelhouse["destination"].strip("/")
    payload = [
        *source,
        *_runtime_files(runtime_dir, runtime_destination, forbidden_tokens, forbidden_suffixes),
        *_runtime_files(wheelhouse_dir, wheelhouse_destination, forbidden_tokens, forbidden_suffixes),
    ]
    return payload, notices


def _write_normalized_entry(archive: zipfile.ZipFile, source: Path, destination: str) -> None:
    info = zipfile.ZipInfo(destination, date_time=ZIP_ENTRY_DATE_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 0
    info.external_attr = 0o100644 << 16
    info.flag_bits = 0
    try:
        with source.open("rb") as reader, archive.open(info, "w") as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
    except (OSError, ValueError, zipfile.BadZipFile):
        raise ValueError("ARCHIVE_ENTRY_READ_FAILED") from None


def archive_payload(entries: list[tuple[Path, str]], output: Path, manifest: dict[str, Any], overwrite: bool) -> dict[str, Any]:
    """Write one deterministic archive; replacement is always refused."""

    if overwrite or output.exists():
        raise ValueError("ARTIFACT_OVERWRITE_FORBIDDEN")
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise ValueError("OUTPUT_DIRECTORY_UNAVAILABLE") from None
    temporary = output.with_name(output.name + ".part")
    if temporary.exists():
        raise ValueError("TEMPORARY_OUTPUT_OCCUPIED")
    ordered = sorted(entries, key=lambda item: item[1])
    destinations = [destination for _source, destination in ordered]
    if len(destinations) != len(set(destinations)) or any(not _safe_archive_name(name) for name in destinations):
        raise ValueError("ARCHIVE_SELECTION_INVALID")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for source, destination in ordered:
                _write_normalized_entry(archive, source, destination)
        size_mib = temporary.stat().st_size / (1024 * 1024)
        target = manifest.get("target_size_mib")
        if not isinstance(target, dict):
            raise ValueError("CORE_MANIFEST_SIZE_POLICY_INVALID")
        try:
            minimum = float(target.get("minimum"))
            maximum = float(target.get("maximum"))
        except (TypeError, ValueError):
            raise ValueError("CORE_MANIFEST_SIZE_POLICY_INVALID") from None
        if not minimum <= size_mib <= maximum:
            raise ValueError("ARCHIVE_SIZE_OUTSIDE_REVIEWED_RANGE")
        os.replace(temporary, output)
    except ValueError:
        if temporary.exists():
            temporary.unlink()
        raise
    except (OSError, zipfile.BadZipFile):
        if temporary.exists():
            temporary.unlink()
        raise ValueError("ARCHIVE_WRITE_FAILED") from None
    return {
        "asset": output.name,
        "sha256": sha256(output),
        "size_bytes": output.stat().st_size,
        "size_mib": round(output.stat().st_size / (1024 * 1024), 3),
        "entries": len(ordered),
    }


def zip_content_fingerprint(path: Path) -> str:
    """Fingerprint sorted entry names, sizes and bounded per-entry digests."""

    records: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(path, "r") as archive:
            for info in sorted(archive.infolist(), key=lambda item: item.filename):
                if not _safe_archive_name(info.filename):
                    raise ValueError("ARCHIVE_ENTRY_NAME_INVALID")
                digest = hashlib.sha256()
                with archive.open(info, "r") as reader:
                    for chunk in iter(lambda: reader.read(1024 * 1024), b""):
                        digest.update(chunk)
                records.append({"filename": info.filename, "size_bytes": info.file_size, "sha256": digest.hexdigest()})
    except (OSError, ValueError, zipfile.BadZipFile):
        raise ValueError("ARCHIVE_READ_FAILED") from None
    encoded = json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_archive_name(value: Any) -> bool:
    if not isinstance(value, str) or not value or value.startswith(("/", "\\")) or "\\" in value or ":" in value:
        return False
    parts = PurePosixPath(value).parts
    return ".." not in parts and all(part not in {"", "."} for part in parts)


def _ensure_output_under_root(path: Path) -> None:
    allowed_root = ROOT / "distribution" / "output"
    try:
        path.resolve().relative_to(allowed_root.resolve())
    except ValueError:
        raise ValueError("OUTPUT_PATH_OUTSIDE_RELEASE_ROOT") from None
    if path.is_symlink():
        raise ValueError("OUTPUT_PATH_OUTSIDE_RELEASE_ROOT")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a reviewed Local AI Hub Core release archive.")
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--runtime-dir", type=Path, default=None)
    parser.add_argument("--wheelhouse-dir", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--build", action="store_true", help="Write the archive after all validation succeeds.")
    parser.add_argument("--overwrite", action="store_true", help="Rejected; release outputs are immutable.")
    args = parser.parse_args()

    try:
        manifest = load_manifest(args.manifest)
        entries, notices = describe(manifest, args.runtime_dir, args.wheelhouse_dir)
        source_bytes = sum(path.stat().st_size for path, _name in entries)
        result: dict[str, Any] = {
            "asset_name": manifest.get("asset_name", "LocalAIHub-Core-Win64.zip"),
            "entries": len(entries),
            "input_bytes": source_bytes,
            "input_mib": round(source_bytes / (1024 * 1024), 3),
            "build_requested": bool(args.build),
            "release_ready": not notices,
            "notices": notices,
            "execution": "not_run",
            "dry_run": not bool(args.build),
        }
        if args.build:
            if args.overwrite:
                raise ValueError("ARTIFACT_OVERWRITE_FORBIDDEN")
            if notices:
                raise ValueError("RELEASE_STAGING_REQUIRED")
            _ensure_output_under_root(args.output)
            result["archive"] = archive_payload(entries, args.output, manifest, False)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        code = str(exc) if str(exc).isidentifier() and len(str(exc)) < 80 else "CORE_RELEASE_BLOCKED"
        print(json.dumps({"ok": False, "code": code, "execution": "not_run", "dry_run": True}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_OUTPUT",
    "MANIFEST_PATH",
    "ROOT",
    "SOURCE_DATE_EPOCH",
    "archive_payload",
    "describe",
    "git_tracked_files",
    "load_manifest",
    "relative",
    "selected_source_files",
    "sha256",
    "zip_content_fingerprint",
]
