"""Closed, detached release-provenance v2 verifier.

The verifier is deliberately a control-plane tool.  It reads committed source
identity and detached artifact metadata, but it never creates tags, changes a
checkout, executes a release artifact, or publishes a release.  The historical
v1 manifest is evidence only and is never rewritten.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.shared.version import PRODUCT_VERSION

SCHEMA_PATH = ROOT / "distribution" / "release_manifest.v2.schema.json"
HISTORICAL_MANIFEST_PATH = ROOT / "distribution" / "release_manifest.json"

SCHEMA_VERSION = "release-provenance.v2"
VERIFIER_SCHEMA_VERSION = "release-provenance-verifier.v1"
REVIEWED_RELEASE_VERSION = PRODUCT_VERSION
APPROVED_RELEASE_VERSIONS = frozenset({"7.1.0"})
RELEASE_BRANCH = "feature/v7-operational-closure"
INTENDED_TAG = "v7.1.0"
SOURCE_DATE_EPOCH = 315532800
ZIP_ENTRY_TIMESTAMP = "1980-01-01T00:00:00Z"
MAX_MANIFEST_BYTES = 128 * 1024
MAX_GIT_OUTPUT_BYTES = 8 * 1024 * 1024
HEX64 = re.compile(r"^[a-f0-9]{64}$")
SAFE_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,119}$")
SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")

RELEASE_ROOTS = ("src", "scripts", "distribution", "docs", "workflows", "architecture")
RELEASE_ROOT_FILES = (
    "requirements-hub.txt",
    "dependencies.lock.json",
    "README.md",
    "LICENSES.md",
    "AGENTS.md",
    "LocalAIHub.vbs",
    "LocalAIHub.cmd",
)
RELEASE_EXCLUDED_FILES = frozenset({"distribution/release_manifest.json"})

_ROOT_KEYS = frozenset(
    {
        "schema_version",
        "application_name",
        "version",
        "source_commit",
        "build_commit",
        "source_branch",
        "intended_tag",
        "tag_commit",
        "build_input_fingerprint",
        "build_parameters",
        "artifacts",
        "manifest_sha256",
    }
)
_BUILD_KEYS = frozenset(
    {
        "source_date_epoch",
        "zip_compression",
        "zip_compression_level",
        "zip_entry_timestamp",
        "selection_fingerprint",
        "installer_selection_fingerprint",
        "reproducibility",
    }
)
_ARTIFACT_KEYS = frozenset({"id", "filename", "size_bytes", "sha256", "content_fingerprint"})
_ARTIFACT_IDS = frozenset({"core_zip", "setup_exe"})
_EXPECTED_FILENAMES = {
    "core_zip": f"LocalAIHub-Core-Win64-v{REVIEWED_RELEASE_VERSION}.zip",
    "setup_exe": f"LocalAIHub-Setup-Win64-v{REVIEWED_RELEASE_VERSION}.exe",
}
_REPRODUCIBILITY = frozenset({"not_claimed", "blocked", "reproducibility_failed"})


class ProvenanceRefusal(ValueError):
    """A fixed-code validation refusal with no client-controlled detail."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _refuse(code: str) -> None:
    raise ProvenanceRefusal(code)


def canonical_json(value: Any) -> bytes:
    """Return the only JSON framing accepted for a release record."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        _refuse("CANONICAL_JSON_INVALID")


def _reject_constant(_value: str) -> None:
    _refuse("NONFINITE_JSON_VALUE")


def strict_json_load(raw: bytes, *, max_bytes: int = MAX_MANIFEST_BYTES) -> Any:
    """Parse bounded JSON while rejecting duplicate keys and non-finite values."""

    if not isinstance(raw, bytes) or len(raw) > max_bytes:
        _refuse("MANIFEST_TOO_LARGE")
    try:
        text = raw.decode("utf-8")

        def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
            keys = [key for key, _value in items]
            if len(keys) != len(set(keys)):
                _refuse("DUPLICATE_JSON_KEY")
            return dict(items)

        return json.loads(
            text,
            object_pairs_hook=pairs,
            parse_constant=_reject_constant,
        )
    except ProvenanceRefusal:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        _refuse("MALFORMED_JSON")


def load_manifest(path: Path) -> dict[str, Any]:
    """Load one bounded manifest without echoing its path or contents."""

    try:
        raw = path.read_bytes()
    except (OSError, ValueError):
        _refuse("MANIFEST_UNREADABLE")
    value = strict_json_load(raw)
    if not isinstance(value, dict):
        _refuse("MANIFEST_ROOT_TYPE")
    return value


def manifest_self_hash(manifest: Mapping[str, Any]) -> str:
    """Hash canonical JSON with only the manifest's own hash field nulled."""

    candidate = copy.deepcopy(dict(manifest))
    candidate["manifest_sha256"] = None
    return hashlib.sha256(canonical_json(candidate)).hexdigest()


def _is_hex64(value: Any) -> bool:
    return isinstance(value, str) and HEX64.fullmatch(value) is not None


def _strict_keys(value: Any, expected: frozenset[str], code: str) -> bool:
    return isinstance(value, dict) and frozenset(value.keys()) == expected


def _safe_relative_name(value: Any) -> bool:
    if not isinstance(value, str) or SAFE_FILENAME.fullmatch(value) is None:
        return False
    return "/" not in value and "\\" not in value and value not in {".", ".."}


def _safe_branch_name(value: Any) -> bool:
    if not isinstance(value, str) or SAFE_BRANCH.fullmatch(value) is None:
        return False
    return not value.startswith(("/", "\\")) and ".." not in PurePosixPath(value).parts


def validate_release_manifest(value: Any, *, verify_self_hash: bool = True) -> dict[str, Any]:
    """Validate the closed v2 record and return only finite safe codes."""

    codes: list[str] = []

    def add(code: str) -> None:
        if code not in codes:
            codes.append(code)

    if not isinstance(value, dict):
        return {"valid": False, "codes": ["MANIFEST_ROOT_TYPE"]}
    if frozenset(value.keys()) != _ROOT_KEYS:
        add("UNKNOWN_OR_MISSING_FIELD")
        return {"valid": False, "codes": codes}
    if value.get("schema_version") != SCHEMA_VERSION:
        add("SCHEMA_MISMATCH")
    if value.get("application_name") != "Local AI Hub":
        add("APPLICATION_MISMATCH")
    if value.get("version") != REVIEWED_RELEASE_VERSION or REVIEWED_RELEASE_VERSION not in APPROVED_RELEASE_VERSIONS:
        add("UNREVIEWED_VERSION")
    for field in ("source_commit", "build_commit", "tag_commit", "build_input_fingerprint", "manifest_sha256"):
        if not _is_hex64(value.get(field)):
            add("INVALID_DIGEST")
    if not _safe_branch_name(value.get("source_branch")):
        add("INVALID_BRANCH")
    elif value.get("source_branch") != RELEASE_BRANCH:
        add("SOURCE_BRANCH_MISMATCH")
    if value.get("intended_tag") != INTENDED_TAG:
        add("INTENDED_TAG_MISMATCH")
    if _is_hex64(value.get("source_commit")) and _is_hex64(value.get("build_commit")):
        if value["build_commit"] != value["source_commit"]:
            add("BUILD_COMMIT_NOT_DETACHED")
    if _is_hex64(value.get("source_commit")) and _is_hex64(value.get("tag_commit")):
        if value["tag_commit"] != value["source_commit"]:
            add("TAG_SOURCE_MISMATCH")

    build = value.get("build_parameters")
    if not _strict_keys(build, _BUILD_KEYS, "BUILD_FIELDS"):
        add("BUILD_FIELDS_INVALID")
    else:
        if build.get("source_date_epoch") != SOURCE_DATE_EPOCH:
            add("SOURCE_DATE_EPOCH_MISMATCH")
        if build.get("zip_compression") != "deflate" or build.get("zip_compression_level") != 9:
            add("ZIP_PARAMETERS_MISMATCH")
        if build.get("zip_entry_timestamp") != ZIP_ENTRY_TIMESTAMP:
            add("ZIP_TIMESTAMP_MISMATCH")
        if not _is_hex64(build.get("selection_fingerprint")) or not _is_hex64(build.get("installer_selection_fingerprint")):
            add("INVALID_SELECTION_FINGERPRINT")
        if build.get("reproducibility") not in _REPRODUCIBILITY:
            add("REPRODUCIBILITY_STATUS_INVALID")

    artifacts = value.get("artifacts")
    seen: set[str] = set()
    if not isinstance(artifacts, list) or not 1 <= len(artifacts) <= 2:
        add("ARTIFACT_LIST_INVALID")
    else:
        for item in artifacts:
            if not _strict_keys(item, _ARTIFACT_KEYS, "ARTIFACT_FIELDS"):
                add("ARTIFACT_FIELDS_INVALID")
                continue
            artifact_id = item.get("id")
            if not isinstance(artifact_id, str) or artifact_id not in _ARTIFACT_IDS:
                add("ARTIFACT_ID_INVALID")
            elif artifact_id in seen:
                add("DUPLICATE_ARTIFACT_ID")
            else:
                seen.add(artifact_id)
                if item.get("filename") != _EXPECTED_FILENAMES[artifact_id]:
                    add("ARTIFACT_NAME_MISMATCH")
            if not _safe_relative_name(item.get("filename")):
                add("ARTIFACT_FILENAME_INVALID")
            if not isinstance(item.get("size_bytes"), int) or isinstance(item.get("size_bytes"), bool) or not 0 <= item.get("size_bytes", -1) <= 4 * 1024 * 1024 * 1024:
                add("ARTIFACT_SIZE_INVALID")
            if not _is_hex64(item.get("sha256")) or not _is_hex64(item.get("content_fingerprint")):
                add("ARTIFACT_DIGEST_INVALID")
        if "core_zip" not in seen:
            add("CORE_ARTIFACT_MISSING")
    if verify_self_hash and _is_hex64(value.get("manifest_sha256")):
        if manifest_self_hash(value) != value["manifest_sha256"]:
            add("MANIFEST_SELF_HASH_MISMATCH")
    return {"valid": not codes, "codes": codes}


def _git_text(repo_root: Path, args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        _refuse("GIT_QUERY_FAILED")
    if result.returncode != 0 or len(result.stdout) > MAX_GIT_OUTPUT_BYTES:
        _refuse("GIT_QUERY_FAILED")
    try:
        return result.stdout.decode("utf-8").strip()
    except UnicodeDecodeError:
        _refuse("GIT_QUERY_FAILED")


def tracked_files(repo_root: Path) -> list[str]:
    raw = _git_text(repo_root, ["ls-files", "-z"])
    if "\x00" not in raw and raw:
        # A single path is still valid, but a newline-containing path is not.
        names = [raw]
    else:
        names = [item for item in raw.split("\x00") if item]
    if len(names) > 100_000:
        _refuse("TRACKED_FILE_LIST_TOO_LARGE")
    if any("\x00" in name or name.startswith(("/", "\\")) or ":" in name for name in names):
        _refuse("UNSAFE_TRACKED_PATH")
    return sorted(set(names))


def release_file_names(repo_root: Path) -> list[str]:
    """Return the deterministic source set shared by ZIP and installer."""

    names = tracked_files(repo_root)
    selected: list[str] = []
    for name in names:
        if name in RELEASE_ROOT_FILES:
            selected.append(name)
            continue
        if name.startswith("Config/") and name.endswith(".example.json"):
            selected.append(name)
            continue
        if any(name == root or name.startswith(root + "/") for root in RELEASE_ROOTS):
            if name in RELEASE_EXCLUDED_FILES:
                continue
            selected.append(name)
    return sorted(set(selected))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except (OSError, ValueError):
        _refuse("SOURCE_FILE_UNREADABLE")
    return digest.hexdigest()


def selection_records(repo_root: Path, names: Iterable[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    root = repo_root.resolve()
    for name in sorted(set(names)):
        if not _safe_path_name(name):
            _refuse("UNSAFE_SOURCE_PATH")
        path = (root / PurePosixPath(name)).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            _refuse("SOURCE_PATH_ESCAPE")
        if not path.is_file() or path.is_symlink():
            _refuse("SOURCE_FILE_UNAVAILABLE")
        try:
            size = path.stat().st_size
        except OSError:
            _refuse("SOURCE_FILE_UNAVAILABLE")
        records.append({"path": name, "size_bytes": size, "sha256": _sha256_file(path)})
    return records


def _safe_path_name(name: Any) -> bool:
    if not isinstance(name, str) or not name or "\\" in name or name.startswith(("/", "\\")) or ":" in name:
        return False
    parts = PurePosixPath(name).parts
    return ".." not in parts and all(part not in {"", "."} for part in parts)


def selection_fingerprint(repo_root: Path, names: Iterable[str]) -> str:
    return hashlib.sha256(canonical_json(selection_records(repo_root, names))).hexdigest()


def installer_selection_fingerprint(names: Iterable[str]) -> str:
    """Fingerprint the installer-visible sorted name set without file bytes."""

    return hashlib.sha256(canonical_json(sorted(set(names)))).hexdigest()


def compute_build_input_fingerprint(
    source_commit: str,
    selection_digest: str,
    installer_selection_digest: str,
) -> str:
    payload = {
        "version": REVIEWED_RELEASE_VERSION,
        "source_commit": source_commit,
        "selection_fingerprint": selection_digest,
        "installer_selection_fingerprint": installer_selection_digest,
        "source_date_epoch": SOURCE_DATE_EPOCH,
        "zip_compression": "deflate",
        "zip_compression_level": 9,
        "zip_entry_timestamp": ZIP_ENTRY_TIMESTAMP,
    }
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def _safe_artifact_path(root: Path, filename: str) -> Path:
    if not _safe_relative_name(filename):
        _refuse("ARTIFACT_FILENAME_INVALID")
    path = (root.resolve() / filename).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        _refuse("ARTIFACT_PATH_ESCAPE")
    return path


def verify_artifacts(manifest: Mapping[str, Any], artifact_root: Path) -> list[str]:
    """Rehash detached artifacts without returning paths or raw metadata."""

    codes: list[str] = []
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        return ["ARTIFACT_LIST_INVALID"]
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            codes.append("ARTIFACT_FIELDS_INVALID")
            continue
        filename = artifact.get("filename")
        if not _safe_relative_name(filename):
            codes.append("ARTIFACT_FILENAME_INVALID")
            continue
        try:
            path = _safe_artifact_path(artifact_root, filename)
            if not path.is_file() or path.is_symlink():
                codes.append("ARTIFACT_UNAVAILABLE")
                continue
            if path.stat().st_size != artifact.get("size_bytes"):
                codes.append("ARTIFACT_SIZE_MISMATCH")
                continue
            if _sha256_file(path) != artifact.get("sha256"):
                codes.append("ARTIFACT_HASH_MISMATCH")
                continue
            if artifact.get("id") == "core_zip":
                content_digest = _zip_content_fingerprint(path)
            else:
                content_digest = artifact.get("sha256")
            if content_digest != artifact.get("content_fingerprint"):
                codes.append("ARTIFACT_CONTENT_FINGERPRINT_MISMATCH")
        except ProvenanceRefusal as exc:
            codes.append(exc.code)
    return sorted(set(codes))


def _zip_content_fingerprint(path: Path) -> str:
    records: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(path, "r") as archive:
            for info in sorted(archive.infolist(), key=lambda item: item.filename):
                if not _safe_path_name(info.filename):
                    _refuse("ARCHIVE_ENTRY_NAME_INVALID")
                digest = hashlib.sha256()
                with archive.open(info, "r") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                records.append({"filename": info.filename, "size_bytes": info.file_size, "sha256": digest.hexdigest()})
    except ProvenanceRefusal:
        raise
    except (OSError, ValueError, zipfile.BadZipFile):
        _refuse("ARCHIVE_READ_FAILED")
    return hashlib.sha256(canonical_json(records)).hexdigest()


def _verify_git_binding(manifest: Mapping[str, Any], repo_root: Path) -> list[str]:
    codes: list[str] = []
    try:
        head = _git_text(repo_root, ["rev-parse", "HEAD"])
        branch = _git_text(repo_root, ["symbolic-ref", "--quiet", "--short", "HEAD"])
        dirty = _git_text(repo_root, ["status", "--porcelain=v1"])
        tag_commit = _git_text(repo_root, ["rev-parse", f"refs/tags/{INTENDED_TAG}^{{commit}}"])
    except ProvenanceRefusal as exc:
        return [exc.code]
    if dirty:
        codes.append("DIRTY_SOURCE")
    if head != manifest.get("source_commit"):
        codes.append("SOURCE_COMMIT_MISMATCH")
    if branch != manifest.get("source_branch"):
        codes.append("BRANCH_HEAD_MISMATCH")
    if tag_commit != manifest.get("tag_commit"):
        codes.append("TAG_COMMIT_MISMATCH")
    if tag_commit != manifest.get("source_commit"):
        codes.append("TAG_SOURCE_MISMATCH")
    if manifest.get("build_commit") != manifest.get("source_commit"):
        codes.append("BUILD_COMMIT_NOT_DETACHED")
    for relative in (
        "scripts/build_installer.py",
        "scripts/build_core_release.py",
        "scripts/verify_release_provenance.py",
        "distribution/release_manifest.v2.schema.json",
    ):
        try:
            _git_text(repo_root, ["cat-file", "-e", f"{manifest.get('build_commit')}:{relative}"])
        except ProvenanceRefusal:
            codes.append("BUILD_INPUT_NOT_COMMITTED")
            break
    try:
        names = release_file_names(repo_root)
        selection = selection_fingerprint(repo_root, names)
        installer_selection = installer_selection_fingerprint(names)
        expected_input = compute_build_input_fingerprint(head, selection, installer_selection)
        build = manifest.get("build_parameters")
        if isinstance(build, dict):
            if selection != build.get("selection_fingerprint"):
                codes.append("SELECTION_FINGERPRINT_MISMATCH")
            if installer_selection != build.get("installer_selection_fingerprint"):
                codes.append("INSTALLER_SELECTION_MISMATCH")
        if expected_input != manifest.get("build_input_fingerprint"):
            codes.append("BUILD_INPUT_FINGERPRINT_MISMATCH")
    except ProvenanceRefusal as exc:
        codes.append(exc.code)
    return sorted(set(codes))


def verify_manifest_file(
    manifest_path: Path,
    *,
    repo_root: Path = ROOT,
    artifact_root: Path | None = None,
) -> dict[str, Any]:
    """Verify a v2 sidecar and return sanitized, finite evidence only."""

    try:
        manifest = load_manifest(manifest_path)
        structural = validate_release_manifest(manifest)
    except ProvenanceRefusal as exc:
        return _projection("verify", False, [exc.code])
    codes = list(structural.get("codes", []))
    if structural.get("valid"):
        codes.extend(_verify_git_binding(manifest, repo_root))
        if artifact_root is not None:
            codes.extend(verify_artifacts(manifest, artifact_root))
    codes = sorted(set(codes))
    return _projection("verify", not codes, codes)


def classify_historical_manifest(
    manifest_path: Path = HISTORICAL_MANIFEST_PATH,
    *,
    repo_root: Path = ROOT,
) -> dict[str, Any]:
    """Classify legacy v1 metadata without rewriting or promoting it."""

    try:
        legacy = load_manifest(manifest_path)
    except ProvenanceRefusal as exc:
        return _projection("historical", False, [exc.code], status="HISTORICAL_METADATA")
    if not isinstance(legacy, dict):
        return _projection("historical", False, ["LEGACY_ROOT_TYPE"], status="HISTORICAL_METADATA")
    codes: list[str] = []
    try:
        current_head = _git_text(repo_root, ["rev-parse", "HEAD"])
        current_branch = _git_text(repo_root, ["symbolic-ref", "--quiet", "--short", "HEAD"])
        current_tag = _git_text(repo_root, ["rev-parse", "refs/tags/v7.1.0^{commit}"])
    except ProvenanceRefusal as exc:
        return _projection("historical", False, [exc.code], status="HISTORICAL_METADATA")
    if legacy.get("version") != REVIEWED_RELEASE_VERSION:
        codes.append("VERSION_MISMATCH")
    if legacy.get("tag") != INTENDED_TAG:
        codes.append("VERSION_TAG_MISMATCH")
    if legacy.get("git_commit") != current_head:
        codes.append("SOURCE_COMMIT_MISMATCH")
    if legacy.get("git_branch") != current_branch:
        codes.append("BRANCH_HEAD_MISMATCH")
    if legacy.get("tag_commit") != current_tag:
        codes.append("TAG_COMMIT_MISMATCH")
    if not codes:
        return _projection("historical", True, [], status="HISTORICAL_METADATA")
    return _projection("historical", False, sorted(set(codes)), status="LEGACY_INCONSISTENT")


def _projection(operation: str, ok: bool, codes: Iterable[str], *, status: str | None = None) -> dict[str, Any]:
    safe_codes = sorted({code for code in codes if isinstance(code, str) and re.fullmatch(r"[A-Z0-9_]{2,80}", code)})
    result: dict[str, Any] = {
        "schema_version": VERIFIER_SCHEMA_VERSION,
        "operation": operation,
        "ok": bool(ok),
        "status": status or ("VERIFIED" if ok else "BLOCKED"),
        "codes": safe_codes,
        "execution": "not_run",
        "dry_run": True,
    }
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify detached Local AI Hub release provenance.")
    parser.add_argument("--manifest", type=Path, default=HISTORICAL_MANIFEST_PATH)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--artifact-root", type=Path, default=None)
    parser.add_argument("--historical", action="store_true", help="Classify the byte-preserved v1 historical manifest.")
    args = parser.parse_args(argv)
    if args.historical:
        result = classify_historical_manifest(args.manifest, repo_root=args.repo)
    else:
        result = verify_manifest_file(args.manifest, repo_root=args.repo, artifact_root=args.artifact_root)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "HISTORICAL_MANIFEST_PATH",
    "INTENDED_TAG",
    "APPROVED_RELEASE_VERSIONS",
    "RELEASE_BRANCH",
    "RELEASE_ROOT_FILES",
    "RELEASE_EXCLUDED_FILES",
    "RELEASE_ROOTS",
    "REVIEWED_RELEASE_VERSION",
    "SCHEMA_PATH",
    "SCHEMA_VERSION",
    "SOURCE_DATE_EPOCH",
    "ZIP_ENTRY_TIMESTAMP",
    "ProvenanceRefusal",
    "canonical_json",
    "classify_historical_manifest",
    "compute_build_input_fingerprint",
    "load_manifest",
    "manifest_self_hash",
    "release_file_names",
    "selection_fingerprint",
    "installer_selection_fingerprint",
    "strict_json_load",
    "validate_release_manifest",
    "verify_artifacts",
    "verify_manifest_file",
]
