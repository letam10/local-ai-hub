"""Deterministic V7 installer/Core packager with detached provenance."""

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

from scripts.build_core_release import _write_normalized_entry
from scripts.verify_release_provenance import (
    APPROVED_RELEASE_VERSIONS,
    RAW_JSON_FRAMING,
    RELEASE_BRANCH,
    RELEASE_ROOT_FILES,
    RELEASE_ROOTS,
    REVIEWED_INTENDED_TAG,
    REVIEWED_RELEASE_VERSION,
    SOURCE_DATE_EPOCH,
    ZIP_ENTRY_TIMESTAMP,
    ProvenanceRefusal,
    audit_installer_selection,
    canonical_json,
    compute_build_input_fingerprint,
    installer_selection_fingerprint,
    manifest_self_hash,
    release_file_names,
    require_source_identity,
    selection_fingerprint,
    selection_records,
    validate_release_manifest,
)
from src.shared.version import PRODUCT_VERSION

DIST_DIR = ROOT / "dist"
STAGING_DIR = DIST_DIR / ".v7-release-staging"
HISTORICAL_MANIFEST_PATH = ROOT / "distribution" / "release_manifest.json"
MANIFEST_PATH = HISTORICAL_MANIFEST_PATH  # read-only compatibility name
ISS_PATH = ROOT / "distribution" / "installer.iss"


class ReleaseBuildRefusal(ValueError):
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
    ]
    return next((candidate for candidate in candidates if candidate and os.path.isfile(candidate)), None)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except (OSError, ValueError):
        _refuse("ARTIFACT_READ_FAILED")
    return digest.hexdigest()


def compile_installer(version: str = PRODUCT_VERSION) -> tuple[Path | None, str | None]:
    if version != REVIEWED_RELEASE_VERSION or version not in APPROVED_RELEASE_VERSIONS:
        return None, "UNREVIEWED_VERSION"
    iscc = find_iscc()
    if not iscc:
        return None, "INSTALLER_COMPILER_UNAVAILABLE"
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    exe_path = DIST_DIR / f"LocalAIHub-Setup-Win64-v{version}.exe"
    if exe_path.exists() or exe_path.is_symlink():
        return None, "ARTIFACT_OVERWRITE_FORBIDDEN"
    try:
        result = subprocess.run([iscc, f"/DMyAppVersion={version}", str(ISS_PATH)], cwd=ROOT, capture_output=True, check=False, timeout=300)
    except (OSError, subprocess.SubprocessError):
        if exe_path.exists():
            exe_path.unlink()
        return None, "INSTALLER_COMPILE_FAILED"
    if result.returncode != 0 or not exe_path.is_file():
        if exe_path.exists():
            exe_path.unlink()
        return None, "INSTALLER_COMPILE_FAILED"
    return exe_path, None


def _ensure_under(path: Path, parent: Path, code: str) -> None:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        _refuse(code)
    if path.is_symlink():
        _refuse(code)


def _write_zip(entries: list[tuple[Path, str]], output: Path) -> None:
    if output.exists() or output.is_symlink():
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
    except (OSError, ValueError, zipfile.BadZipFile):
        if temporary.exists():
            temporary.unlink()
        _refuse("CORE_ARCHIVE_WRITE_FAILED")


def _zip_content_fingerprint(path: Path) -> str:
    rows: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(path, "r") as archive:
            for info in sorted(archive.infolist(), key=lambda item: item.filename):
                digest = hashlib.sha256()
                with archive.open(info, "r") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                rows.append({"filename": info.filename, "size_bytes": info.file_size, "sha256": digest.hexdigest()})
    except (OSError, ValueError, zipfile.BadZipFile):
        _refuse("ARCHIVE_READ_FAILED")
    return hashlib.sha256(canonical_json(rows)).hexdigest()


def _write_sidecar(path: Path, manifest: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        _refuse("MANIFEST_OUTPUT_OCCUPIED")
    temporary = path.with_name(path.name + ".part")
    if temporary.exists():
        _refuse("MANIFEST_TEMPORARY_OCCUPIED")
    payload = canonical_json(manifest) + b"\n"
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


def _git_text(args: list[str], *, required: bool = True) -> str:
    try:
        result = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, check=False, timeout=10)
    except (OSError, subprocess.SubprocessError):
        _refuse("GIT_QUERY_FAILED")
    if result.returncode != 0:
        if not required:
            return ""
        _refuse("GIT_QUERY_FAILED")
    try:
        return result.stdout.decode("utf-8").strip()
    except UnicodeDecodeError:
        _refuse("GIT_QUERY_FAILED")


def get_git_info() -> tuple[str, str, str, str | None]:
    commit = _git_text(["rev-parse", "HEAD"])
    branch = _git_text(["symbolic-ref", "--quiet", "--short", "HEAD"])
    tag_commit = _git_text(["rev-parse", f"refs/tags/{REVIEWED_INTENDED_TAG}^{{commit}}"], required=False) or None
    return commit, branch, REVIEWED_INTENDED_TAG, tag_commit


def build_release_package(output_zip: Path | None = None, compile_exe: bool = True, staging_dir: Path | None = None) -> dict[str, Any]:
    # The identity gate must happen before output/staging creation.
    identity = require_source_identity(ROOT, intended_tag=REVIEWED_INTENDED_TAG, phase="tagged")
    if PRODUCT_VERSION != REVIEWED_RELEASE_VERSION or PRODUCT_VERSION not in APPROVED_RELEASE_VERSIONS:
        _refuse("UNREVIEWED_VERSION")
    output = output_zip or (DIST_DIR / f"LocalAIHub-Core-Win64-v{REVIEWED_RELEASE_VERSION}.zip")
    staging = staging_dir or STAGING_DIR
    if output.name != f"LocalAIHub-Core-Win64-v{REVIEWED_RELEASE_VERSION}.zip":
        _refuse("OUTPUT_NAME_MISMATCH")
    _ensure_under(output, DIST_DIR, "OUTPUT_PATH_OUTSIDE_DIST")
    _ensure_under(staging, DIST_DIR, "STAGING_PATH_OUTSIDE_DIST")
    if output.exists() or staging.exists() and any(staging.iterdir()):
        _refuse("ARTIFACT_OVERWRITE_FORBIDDEN" if output.exists() else "STAGING_OCCUPIED")

    source_names = release_file_names(ROOT)
    installer_names = audit_installer_selection(ROOT, ISS_PATH)
    if source_names != installer_names:
        _refuse("CORE_INSTALLER_SELECTION_MISMATCH")
    before = selection_records(ROOT, source_names)
    selection_digest = hashlib.sha256(canonical_json(before)).hexdigest()
    installer_digest = installer_selection_fingerprint(installer_names)
    source_commit = _current_head()
    input_fingerprint = compute_build_input_fingerprint(source_commit, "tagged", REVIEWED_INTENDED_TAG, selection_digest, installer_digest)
    _write_zip([(ROOT / name, name) for name in source_names], output)
    if before != selection_records(ROOT, source_names):
        output.unlink(missing_ok=True)
        _refuse("SOURCE_CHANGED_DURING_BUILD")
    artifacts: list[dict[str, Any]] = [{
        "id": "core_zip",
        "filename": output.name,
        "size_bytes": output.stat().st_size,
        "sha256": sha256_file(output),
        "content_fingerprint": _zip_content_fingerprint(output),
    }]
    if compile_exe:
        exe_path, error = compile_installer(REVIEWED_RELEASE_VERSION)
        if exe_path is None:
            output.unlink(missing_ok=True)
            _refuse(error or "INSTALLER_COMPILE_FAILED")
        artifacts.append({
            "id": "setup_exe",
            "filename": exe_path.name,
            "size_bytes": exe_path.stat().st_size,
            "sha256": sha256_file(exe_path),
            "content_fingerprint": sha256_file(exe_path),
        })
    tag_commit = identity.get("tag_commit") or source_commit
    manifest: dict[str, Any] = {
        "schema_version": "release-provenance.v2",
        "application_name": "Local AI Hub",
        "version": REVIEWED_RELEASE_VERSION,
        "phase": "tagged",
        "source_commit": source_commit,
        "build_commit": source_commit,
        "source_branch": RELEASE_BRANCH,
        "intended_tag": REVIEWED_INTENDED_TAG,
        "tag_commit": tag_commit,
        "build_input_fingerprint": input_fingerprint,
        "build_parameters": {
            "source_date_epoch": SOURCE_DATE_EPOCH,
            "zip_compression": "deflate",
            "zip_compression_level": 9,
            "zip_entry_timestamp": ZIP_ENTRY_TIMESTAMP,
            "json_framing": RAW_JSON_FRAMING,
            "selection_fingerprint": selection_digest,
            "installer_selection_fingerprint": installer_digest,
            "reproducibility": "not_claimed",
        },
        "artifacts": artifacts,
        "manifest_sha256": "0" * 64,
    }
    manifest["manifest_sha256"] = manifest_self_hash(manifest)
    if not validate_release_manifest(manifest, oid_width=identity.get("oid_width"))["valid"]:
        output.unlink(missing_ok=True)
        _refuse("GENERATED_MANIFEST_INVALID")
    try:
        _write_sidecar(staging / "release_manifest.v2.json", manifest)
    except ReleaseBuildRefusal:
        output.unlink(missing_ok=True)
        raise
    return manifest


def _current_head() -> str:
    try:
        result = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, check=False, timeout=10)
    except (OSError, subprocess.SubprocessError):
        _refuse("GIT_QUERY_FAILED")
    if result.returncode != 0:
        _refuse("GIT_QUERY_FAILED")
    return result.stdout.decode("utf-8").strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a detached Local AI Hub release provenance v2 sidecar.")
    parser.add_argument("--output-zip", type=Path, default=None)
    parser.add_argument("--staging-dir", type=Path, default=None)
    parser.add_argument("--no-exe", action="store_true")
    args = parser.parse_args()
    try:
        result = build_release_package(output_zip=args.output_zip, compile_exe=not args.no_exe, staging_dir=args.staging_dir)
        print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
        return 0
    except (OSError, ValueError, ProvenanceRefusal) as exc:
        code = str(exc) if str(exc).isidentifier() and len(str(exc)) < 80 else "RELEASE_BUILD_BLOCKED"
        print(json.dumps({"ok": False, "code": code, "execution": "not_run", "dry_run": True}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DIST_DIR",
    "HISTORICAL_MANIFEST_PATH",
    "ISS_PATH",
    "MANIFEST_PATH",
    "PRODUCT_VERSION",
    "ReleaseBuildRefusal",
    "build_release_package",
    "compile_installer",
    "find_iscc",
    "get_git_info",
    "sha256_file",
]
