"""Deterministic Local AI Hub V7 release packager.

This packager creates a detached ``release_manifest.v2.json`` sidecar in an
ignored staging directory. It never writes the historical
``distribution/release_manifest.json`` and never embeds the attestation in an
artifact whose digest it records. The current reviewed release remains 7.1.0;
branch/tag identity is checked explicitly and a mismatched checkout is
blocked before any output is created.
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
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DIST_DIR = ROOT / "dist"
STAGING_DIR = DIST_DIR / ".v7-release-staging"
HISTORICAL_MANIFEST_PATH = ROOT / "distribution" / "release_manifest.json"
# Kept as a read-only compatibility name for existing callers.
MANIFEST_PATH = HISTORICAL_MANIFEST_PATH
ISS_PATH = ROOT / "distribution" / "installer.iss"

from scripts.build_core_release import _write_normalized_entry, zip_content_fingerprint
from scripts.verify_release_provenance import (
    APPROVED_RELEASE_VERSIONS,
    INTENDED_TAG,
    RELEASE_BRANCH,
    RELEASE_ROOT_FILES,
    RELEASE_ROOTS,
    REVIEWED_RELEASE_VERSION,
    SOURCE_DATE_EPOCH,
    ZIP_ENTRY_TIMESTAMP,
    ProvenanceRefusal,
    canonical_json,
    compute_build_input_fingerprint,
    installer_selection_fingerprint,
    release_file_names,
    selection_fingerprint,
    selection_records,
    validate_release_manifest,
    manifest_self_hash,
)
from src.shared.version import PRODUCT_VERSION


class ReleaseBuildRefusal(ValueError):
    """Fixed release-build refusal without path, command or tool output echo."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _refuse(code: str) -> None:
    raise ReleaseBuildRefusal(code)


def find_iscc() -> str | None:
    candidates = [
        shutil.which("ISCC.exe"),
        shutil.which("iscc"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"),
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
        r"C:\Program Files (x86)\Inno Setup 5\ISCC.exe",
        r"C:\Program Files\Inno Setup 5\ISCC.exe",
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except (OSError, ValueError):
        _refuse("ARTIFACT_READ_FAILED")
    return digest.hexdigest()


def _git_text(args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), *args],
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        _refuse("GIT_QUERY_FAILED")
    if result.returncode != 0 or len(result.stdout) > 8 * 1024 * 1024:
        _refuse("GIT_QUERY_FAILED")
    try:
        return result.stdout.decode("utf-8").strip()
    except UnicodeDecodeError:
        _refuse("GIT_QUERY_FAILED")


def get_git_info() -> tuple[str, str, str, str]:
    """Read explicit HEAD/branch/intended-tag identity; no tag description lookup."""

    commit = _git_text(["rev-parse", "HEAD"])
    branch = _git_text(["symbolic-ref", "--quiet", "--short", "HEAD"])
    tag_commit = _git_text(["rev-parse", f"refs/tags/{INTENDED_TAG}^{{commit}}"])
    return commit, branch, INTENDED_TAG, tag_commit


def _assert_reviewed_version() -> None:
    if PRODUCT_VERSION != REVIEWED_RELEASE_VERSION or PRODUCT_VERSION not in APPROVED_RELEASE_VERSIONS:
        _refuse("UNREVIEWED_VERSION")


def _assert_release_identity(commit: str, branch: str, tag_commit: str) -> None:
    _assert_reviewed_version()
    if branch != RELEASE_BRANCH:
        _refuse("SOURCE_BRANCH_MISMATCH")
    if _git_text(["status", "--porcelain=v1"]):
        _refuse("DIRTY_SOURCE")
    if len(commit) != 64 or any(character not in "0123456789abcdef" for character in commit):
        _refuse("SOURCE_COMMIT_INVALID")
    if tag_commit != commit:
        _refuse("TAG_SOURCE_MISMATCH")
    required = (
        "scripts/build_installer.py",
        "scripts/build_core_release.py",
        "scripts/verify_release_provenance.py",
        "distribution/release_manifest.v2.schema.json",
    )
    for relative in required:
        try:
            _git_text(["cat-file", "-e", f"{commit}:{relative}"])
        except ReleaseBuildRefusal:
            _refuse("BUILD_INPUT_NOT_COMMITTED")


def _required_installer_markers() -> tuple[str, ...]:
    roots = tuple(f'Source: "..\\{name}\\*"' for name in RELEASE_ROOTS)
    root_files = tuple(f'Source: "..\\{name}"' for name in RELEASE_ROOT_FILES)
    return (*roots, *root_files, 'Source: "..\\Config\\*.example.json"', 'Excludes: "release_manifest.json"')


def installer_selection_names(repo_root: Path = ROOT) -> list[str]:
    """Return installer source names after checking the reviewed ISS contract."""

    try:
        text = ISS_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        _refuse("INSTALLER_SPEC_UNREADABLE")
    if any(marker not in text for marker in _required_installer_markers()):
        _refuse("INSTALLER_SELECTION_UNREVIEWED")
    return release_file_names(repo_root)


def _assert_selection_parity(source_names: list[str], installer_names: list[str]) -> None:
    if source_names != installer_names:
        _refuse("CORE_INSTALLER_SELECTION_MISMATCH")


def _ensure_under(path: Path, parent: Path, code: str) -> None:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        _refuse(code)
    if path.is_symlink():
        _refuse(code)


def _write_deterministic_zip(entries: list[tuple[Path, str]], output: Path) -> None:
    if output.exists():
        _refuse("ARTIFACT_OVERWRITE_FORBIDDEN")
    temporary = output.with_name(output.name + ".part")
    if temporary.exists():
        _refuse("TEMPORARY_OUTPUT_OCCUPIED")
    ordered = sorted(entries, key=lambda item: item[1])
    destinations = [destination for _source, destination in ordered]
    if len(destinations) != len(set(destinations)):
        _refuse("CORE_SELECTION_DUPLICATE")
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for source, destination in ordered:
                _write_normalized_entry(archive, source, destination)
        os.replace(temporary, output)
    except ReleaseBuildRefusal:
        if temporary.exists():
            temporary.unlink()
        raise
    except (OSError, zipfile.BadZipFile, ValueError):
        if temporary.exists():
            temporary.unlink()
        _refuse("CORE_ARCHIVE_WRITE_FAILED")


def _write_manifest_sidecar(path: Path, manifest: dict[str, Any]) -> None:
    if path.exists():
        _refuse("MANIFEST_OUTPUT_OCCUPIED")
    payload = canonical_json(manifest) + b"\n"
    temporary = path.with_name(path.name + ".part")
    if temporary.exists():
        _refuse("MANIFEST_TEMPORARY_OCCUPIED")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except (OSError, ValueError):
        if temporary.exists():
            temporary.unlink()
        _refuse("MANIFEST_WRITE_FAILED")


def compile_installer(version: str = PRODUCT_VERSION) -> tuple[Path | None, str | None]:
    """Compile one installer with the reviewed version supplied explicitly."""

    if version != REVIEWED_RELEASE_VERSION:
        return None, "UNREVIEWED_VERSION"
    iscc = find_iscc()
    if not iscc:
        return None, "INSTALLER_COMPILER_UNAVAILABLE"
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    exe_path = DIST_DIR / f"LocalAIHub-Setup-Win64-v{version}.exe"
    if exe_path.exists():
        return None, "ARTIFACT_OVERWRITE_FORBIDDEN"
    command = [iscc, f"/DMyAppVersion={version}", str(ISS_PATH)]
    try:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, check=False, timeout=300)
    except (OSError, subprocess.SubprocessError):
        if exe_path.exists():
            exe_path.unlink()
        return None, "INSTALLER_COMPILE_FAILED"
    if result.returncode != 0 or not exe_path.is_file():
        if exe_path.exists():
            exe_path.unlink()
        return None, "INSTALLER_COMPILE_FAILED"
    return exe_path, None


def _artifact_record(artifact_id: str, path: Path, *, content_fingerprint: str) -> dict[str, Any]:
    filename = path.name
    return {
        "id": artifact_id,
        "filename": filename,
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "content_fingerprint": content_fingerprint,
    }


def build_release_package(
    output_zip: Path | None = None,
    compile_exe: bool = True,
    staging_dir: Path | None = None,
) -> dict[str, Any]:
    """Build deterministic artifacts and one detached v2 sidecar.

    A branch/tag mismatch is rejected before ``dist`` or staging creation.
    ``distribution/release_manifest.json`` is never opened for writing.
    """

    commit, branch, _tag, tag_commit = get_git_info()
    _assert_release_identity(commit, branch, tag_commit)
    output = output_zip or (DIST_DIR / f"LocalAIHub-Core-Win64-v{REVIEWED_RELEASE_VERSION}.zip")
    staging = staging_dir or STAGING_DIR
    if output.name != f"LocalAIHub-Core-Win64-v{REVIEWED_RELEASE_VERSION}.zip":
        _refuse("OUTPUT_NAME_MISMATCH")
    _ensure_under(output, DIST_DIR, "OUTPUT_PATH_OUTSIDE_STAGING")
    _ensure_under(staging, DIST_DIR, "STAGING_PATH_OUTSIDE_DIST")
    if output.exists():
        _refuse("ARTIFACT_OVERWRITE_FORBIDDEN")
    if staging.exists() and any(staging.iterdir()):
        _refuse("STAGING_OCCUPIED")

    source_names = release_file_names(ROOT)
    installer_names = installer_selection_names(ROOT)
    _assert_selection_parity(source_names, installer_names)
    records_before = selection_records(ROOT, source_names)
    selection_digest = hashlib.sha256(canonical_json(records_before)).hexdigest()
    installer_selection_digest = installer_selection_fingerprint(installer_names)
    input_fingerprint = compute_build_input_fingerprint(commit, selection_digest, installer_selection_digest)

    entries = [(ROOT / name, name) for name in source_names]
    _write_deterministic_zip(entries, output)
    records_after = selection_records(ROOT, source_names)
    if records_before != records_after:
        if output.exists():
            output.unlink()
        _refuse("SOURCE_CHANGED_DURING_BUILD")
    artifacts: list[dict[str, Any]] = [
        _artifact_record("core_zip", output, content_fingerprint=zip_content_fingerprint(output)),
    ]

    if compile_exe:
        exe_path, error = compile_installer(REVIEWED_RELEASE_VERSION)
        if exe_path is None:
            if output.exists():
                output.unlink()
            _refuse(error or "INSTALLER_COMPILE_FAILED")
        artifacts.append(_artifact_record("setup_exe", exe_path, content_fingerprint=sha256_file(exe_path)))

    manifest: dict[str, Any] = {
        "schema_version": "release-provenance.v2",
        "application_name": "Local AI Hub",
        "version": REVIEWED_RELEASE_VERSION,
        "source_commit": commit,
        "build_commit": commit,
        "source_branch": branch,
        "intended_tag": INTENDED_TAG,
        "tag_commit": tag_commit,
        "build_input_fingerprint": input_fingerprint,
        "build_parameters": {
            "source_date_epoch": SOURCE_DATE_EPOCH,
            "zip_compression": "deflate",
            "zip_compression_level": 9,
            "zip_entry_timestamp": ZIP_ENTRY_TIMESTAMP,
            "selection_fingerprint": selection_digest,
            "installer_selection_fingerprint": installer_selection_digest,
            "reproducibility": "not_claimed",
        },
        "artifacts": artifacts,
        "manifest_sha256": "0" * 64,
    }
    manifest["manifest_sha256"] = manifest_self_hash(manifest)
    validation = validate_release_manifest(manifest)
    if not validation.get("valid"):
        if output.exists():
            output.unlink()
        _refuse("GENERATED_MANIFEST_INVALID")
    try:
        _write_manifest_sidecar(staging / "release_manifest.v2.json", manifest)
    except ReleaseBuildRefusal:
        if output.exists():
            output.unlink()
        raise
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a detached Local AI Hub release provenance v2 sidecar.")
    parser.add_argument("--output-zip", type=Path, default=None)
    parser.add_argument("--staging-dir", type=Path, default=None)
    parser.add_argument("--no-exe", action="store_true", help="Build only the deterministic Core ZIP.")
    args = parser.parse_args()
    try:
        result = build_release_package(
            output_zip=args.output_zip,
            compile_exe=not args.no_exe,
            staging_dir=args.staging_dir,
        )
        print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
        return 0
    except (OSError, ReleaseBuildRefusal, ProvenanceRefusal, ValueError) as exc:
        code = str(exc) if str(exc).isidentifier() and len(str(exc)) < 80 else "RELEASE_BUILD_BLOCKED"
        print(json.dumps({"ok": False, "code": code, "execution": "not_run", "dry_run": True}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DIST_DIR",
    "HISTORICAL_MANIFEST_PATH",
    "INTENDED_TAG",
    "ISS_PATH",
    "MANIFEST_PATH",
    "PRODUCT_VERSION",
    "RELEASE_BRANCH",
    "RELEASE_ROOT_FILES",
    "RELEASE_ROOTS",
    "REVIEWED_RELEASE_VERSION",
    "STAGING_DIR",
    "ReleaseBuildRefusal",
    "build_release_package",
    "compile_installer",
    "find_iscc",
    "get_git_info",
    "installer_selection_names",
    "sha256_file",
]
