"""Deterministic reviewed Core archive builder.

The ``--build`` path invokes the same clean source/branch/tag/build-input
identity gate as the installer builder before it creates an output directory,
staging file, or ZIP. Source selection and Inno Setup selection come from the
single audited contract in ``verify_release_provenance``.
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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.verify_release_provenance import (
    INSTALLER_SOURCE_RULES,
    RELEASE_BRANCH,
    RELEASE_SELECTION_CONTRACT,
    REVIEWED_INTENDED_TAG,
    SOURCE_DATE_EPOCH,
    ProvenanceRefusal,
    audit_installer_selection,
    require_source_identity,
    release_file_names,
)

MANIFEST_PATH = ROOT / "distribution" / "core.manifest.json"
DEFAULT_OUTPUT = ROOT / "distribution" / "output" / "LocalAIHub-Core-Win64.zip"
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
    result = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=False, capture_output=True)
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


def selected_source_files(_manifest: dict[str, Any] | None = None) -> list[Path]:
    """Return the shared source contract used by the Core and installer."""

    return [ROOT / name for name in release_file_names(ROOT)]


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
    runtime_policy = manifest.get("portable_runtime")
    wheelhouse_policy = manifest.get("wheelhouse")
    if not isinstance(runtime_policy, dict) or not isinstance(wheelhouse_policy, dict):
        raise ValueError("CORE_MANIFEST_STAGING_POLICY_INVALID")
    runtime_destination = runtime_policy.get("destination")
    wheelhouse_destination = wheelhouse_policy.get("destination")
    if not isinstance(runtime_destination, str) or not isinstance(wheelhouse_destination, str):
        raise ValueError("CORE_MANIFEST_STAGING_POLICY_INVALID")
    payload = [
        *source,
        *_runtime_files(runtime_dir, runtime_destination.strip("/"), forbidden_tokens, forbidden_suffixes),
        *_runtime_files(wheelhouse_dir, wheelhouse_destination.strip("/"), forbidden_tokens, forbidden_suffixes),
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
    if overwrite or output.exists():
        raise ValueError("ARTIFACT_OVERWRITE_FORBIDDEN")
    temporary = output.with_name(output.name + ".part")
    if temporary.exists():
        raise ValueError("TEMPORARY_OUTPUT_OCCUPIED")
    ordered = sorted(entries, key=lambda item: item[1])
    destinations = [destination for _source, destination in ordered]
    if len(destinations) != len(set(destinations)) or any(not _safe_archive_name(item) for item in destinations):
        raise ValueError("ARCHIVE_SELECTION_INVALID")
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for source, destination in ordered:
                _write_normalized_entry(archive, source, destination)
        target = manifest.get("target_size_mib")
        if not isinstance(target, dict):
            raise ValueError("CORE_MANIFEST_SIZE_POLICY_INVALID")
        try:
            minimum = float(target.get("minimum"))
            maximum = float(target.get("maximum"))
        except (TypeError, ValueError):
            raise ValueError("CORE_MANIFEST_SIZE_POLICY_INVALID") from None
        size_mib = temporary.stat().st_size / (1024 * 1024)
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
    return {"asset": output.name, "sha256": sha256(output), "size_bytes": output.stat().st_size, "entries": len(ordered)}


def _safe_archive_name(value: Any) -> bool:
    if not isinstance(value, str) or not value or value.startswith(("/", "\\")) or "\\" in value or ":" in value:
        return False
    return ".." not in PurePosixPath(value).parts


def _ensure_output_under(path: Path) -> None:
    allowed = (ROOT / "distribution" / "output").resolve()
    try:
        path.resolve().relative_to(allowed)
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
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    try:
        if args.build:
            # This is intentionally before manifest loading, selection expansion,
            # output parent creation and every archive write.
            require_source_identity(ROOT, intended_tag=REVIEWED_INTENDED_TAG, phase="tagged")
            source_names = release_file_names(ROOT)
            installer_names = audit_installer_selection(ROOT, ROOT / "distribution" / "installer.iss")
            if source_names != installer_names:
                raise ValueError("CORE_INSTALLER_SELECTION_MISMATCH")
            _ensure_output_under(args.output)
        manifest = load_manifest(args.manifest)
        entries, notices = describe(manifest, args.runtime_dir, args.wheelhouse_dir)
        result: dict[str, Any] = {
            "asset_name": manifest.get("asset_name", "LocalAIHub-Core-Win64.zip"),
            "entries": len(entries),
            "input_bytes": sum(path.stat().st_size for path, _name in entries),
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
            result["archive"] = archive_payload(entries, args.output, manifest, False)
        print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
        return 0
    except (OSError, RuntimeError, ValueError, ProvenanceRefusal, json.JSONDecodeError) as exc:
        code = str(exc) if str(exc).isidentifier() and len(str(exc)) < 80 else "CORE_RELEASE_BLOCKED"
        print(json.dumps({"ok": False, "code": code, "execution": "not_run", "dry_run": True}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_OUTPUT",
    "MANIFEST_PATH",
    "RELEASE_SELECTION_CONTRACT",
    "ROOT",
    "SOURCE_DATE_EPOCH",
    "archive_payload",
    "describe",
    "git_tracked_files",
    "load_manifest",
    "relative",
    "selected_source_files",
    "sha256",
]
