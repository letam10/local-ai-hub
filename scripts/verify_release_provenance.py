"""Strict, read-only release provenance and source-selection controls for V7.

This module deliberately separates three cases:

* ``historical`` classifies the byte-preserved v1 manifest;
* ``pre_tag`` validates a current source against an explicitly supplied,
  unoccupied future tag without creating or publishing that tag; and
* ``tagged`` verifies a detached v2 sidecar whose tag already points exactly
  at the clean source/build commit.

All projections are finite and redacted.  No helper writes a tag, changes a
checkout, executes a release artifact, or returns raw Git output.
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
APPROVED_RELEASE_VERSIONS = frozenset({"7.1.0"})
REVIEWED_RELEASE_VERSION = PRODUCT_VERSION
RELEASE_BRANCH = "feature/v7-operational-closure"
REVIEWED_INTENDED_TAG = "v7.1.0"
SOURCE_DATE_EPOCH = 315532800
ZIP_ENTRY_TIMESTAMP = "1980-01-01T00:00:00Z"
RAW_JSON_FRAMING = "canonical-json-sorted-keys-compact-utf8-with-exactly-one-terminal-lf"
MAX_MANIFEST_BYTES = 128 * 1024
MAX_GIT_OUTPUT_BYTES = 8 * 1024 * 1024
SUPPORTED_OID_WIDTHS = (40, 64)

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
RELEASE_SELECTION_CONTRACT = {
    "roots": RELEASE_ROOTS,
    "root_files": RELEASE_ROOT_FILES,
    "config_glob": "Config/*.example.json",
    "excluded_files": tuple(sorted(RELEASE_EXCLUDED_FILES)),
    "excluded_generated": ("__pycache__", "*.pyc"),
}
INSTALLER_SOURCE_RULES = tuple(
    [f'Source: "..\\{root}\\*"' for root in RELEASE_ROOTS]
    + [f'Source: "..\\{name}"' for name in RELEASE_ROOT_FILES]
    + ['Source: "..\\Config\\*.example.json"']
)
SAFE_TAG = re.compile(r"^v7\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z][0-9A-Za-z.-]{0,31})?$")
SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
HEX64 = re.compile(r"^[a-f0-9]{64}$")

_ROOT_KEYS = frozenset(
    {
        "schema_version",
        "application_name",
        "version",
        "phase",
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
        "json_framing",
        "selection_fingerprint",
        "installer_selection_fingerprint",
        "reproducibility",
    }
)
_ARTIFACT_KEYS = frozenset({"id", "filename", "size_bytes", "sha256", "content_fingerprint"})
_ARTIFACT_IDS = frozenset({"core_zip", "setup_exe"})
_REPRODUCIBILITY = frozenset({"not_claimed", "blocked", "reproducibility_failed"})


class ProvenanceRefusal(ValueError):
    """Fixed-code refusal with no client-controlled detail."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _refuse(code: str) -> None:
    raise ProvenanceRefusal(code)


def canonical_json(value: Any) -> bytes:
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


def strict_json_load(raw: bytes, *, require_framing: bool = False, max_bytes: int = MAX_MANIFEST_BYTES) -> Any:
    """Parse bounded JSON and, for v2, require exact detached bytes."""

    if not isinstance(raw, bytes) or len(raw) > max_bytes:
        _refuse("MANIFEST_TOO_LARGE")
    try:
        text = raw.decode("utf-8")

        def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
            keys = [key for key, _value in items]
            if len(keys) != len(set(keys)):
                _refuse("DUPLICATE_JSON_KEY")
            return dict(items)

        value = json.loads(text, object_pairs_hook=pairs, parse_constant=_reject_constant)
    except ProvenanceRefusal:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        _refuse("MALFORMED_JSON")
    if require_framing:
        expected = canonical_json(value) + b"\n"
        if raw != expected:
            _refuse("NON_CANONICAL_FRAMING")
    return value


def load_manifest(path: Path, *, require_canonical: bool = False) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except (OSError, ValueError):
        _refuse("MANIFEST_UNREADABLE")
    value = strict_json_load(raw, require_framing=require_canonical)
    if not isinstance(value, dict):
        _refuse("MANIFEST_ROOT_TYPE")
    return value


def manifest_self_hash(manifest: Mapping[str, Any]) -> str:
    candidate = copy.deepcopy(dict(manifest))
    candidate["manifest_sha256"] = None
    return hashlib.sha256(canonical_json(candidate)).hexdigest()


def _is_oid(value: Any, width: int | None = None) -> bool:
    if not isinstance(value, str):
        return False
    expected = width if width in SUPPORTED_OID_WIDTHS else None
    return len(value) in (SUPPORTED_OID_WIDTHS if expected is None else (expected,)) and all(char in "0123456789abcdef" for char in value)


def _is_hex64(value: Any) -> bool:
    return isinstance(value, str) and HEX64.fullmatch(value) is not None


def _safe_branch(value: Any) -> bool:
    if not isinstance(value, str) or not value or len(value) > 120:
        return False
    if value.startswith(("/", "\\")) or ":" in value or "\\" in value:
        return False
    return all(part not in {"", ".", ".."} for part in PurePosixPath(value).parts)


def _safe_leaf(value: Any) -> bool:
    return isinstance(value, str) and SAFE_FILENAME.fullmatch(value) is not None and "/" not in value and "\\" not in value


def _strict_keys(value: Any, expected: frozenset[str]) -> bool:
    return isinstance(value, dict) and frozenset(value.keys()) == expected


def validate_release_manifest(
    value: Any,
    *,
    verify_self_hash: bool = True,
    oid_width: int | None = None,
    require_phase: str | None = None,
) -> dict[str, Any]:
    codes: list[str] = []

    def add(code: str) -> None:
        if code not in codes:
            codes.append(code)

    if not isinstance(value, dict):
        return {"valid": False, "codes": ["MANIFEST_ROOT_TYPE"]}
    if frozenset(value.keys()) != _ROOT_KEYS:
        return {"valid": False, "codes": ["UNKNOWN_OR_MISSING_FIELD"]}
    if value.get("schema_version") != SCHEMA_VERSION:
        add("SCHEMA_MISMATCH")
    if value.get("application_name") != "Local AI Hub":
        add("APPLICATION_MISMATCH")
    if value.get("version") != REVIEWED_RELEASE_VERSION or REVIEWED_RELEASE_VERSION not in APPROVED_RELEASE_VERSIONS:
        add("UNREVIEWED_VERSION")
    phase = value.get("phase")
    if phase not in {"pre_tag", "tagged"}:
        add("PHASE_INVALID")
    elif require_phase is not None and phase != require_phase:
        add("PHASE_MISMATCH")
    for field in ("source_commit", "build_commit"):
        if not _is_oid(value.get(field), oid_width):
            add("OID_INVALID")
    tag_commit = value.get("tag_commit")
    if tag_commit is not None and not _is_oid(tag_commit, oid_width):
        add("TAG_OID_INVALID")
    oid_values = [value.get("source_commit"), value.get("build_commit"), tag_commit]
    widths = {len(item) for item in oid_values if isinstance(item, str) and _is_oid(item)}
    if len(widths) > 1 or (oid_width is not None and widths and widths != {oid_width}):
        add("OID_WIDTH_MISMATCH")
    if _is_oid(value.get("source_commit")) and _is_oid(value.get("build_commit")) and value["source_commit"] != value["build_commit"]:
        add("BUILD_COMMIT_NOT_DETACHED")
    if phase == "tagged":
        if tag_commit is None:
            add("TAG_COMMIT_REQUIRED")
        elif _is_oid(value.get("source_commit")) and tag_commit != value["source_commit"]:
            add("TAG_SOURCE_MISMATCH")
    elif phase == "pre_tag" and tag_commit is not None:
        add("PRE_TAG_COMMIT_MUST_BE_NULL")
    if not _safe_branch(value.get("source_branch")):
        add("INVALID_BRANCH")
    elif value.get("source_branch") != RELEASE_BRANCH:
        add("SOURCE_BRANCH_MISMATCH")
    if not isinstance(value.get("intended_tag"), str) or SAFE_TAG.fullmatch(value["intended_tag"]) is None:
        add("INTENDED_TAG_INVALID")
    elif phase == "tagged" and value.get("intended_tag") != REVIEWED_INTENDED_TAG:
        add("INTENDED_TAG_MISMATCH")

    build = value.get("build_parameters")
    if not _strict_keys(build, _BUILD_KEYS):
        add("BUILD_FIELDS_INVALID")
    else:
        if build.get("source_date_epoch") != SOURCE_DATE_EPOCH:
            add("SOURCE_DATE_EPOCH_MISMATCH")
        if build.get("zip_compression") != "deflate" or build.get("zip_compression_level") != 9:
            add("ZIP_PARAMETERS_MISMATCH")
        if build.get("zip_entry_timestamp") != ZIP_ENTRY_TIMESTAMP:
            add("ZIP_TIMESTAMP_MISMATCH")
        if build.get("json_framing") != RAW_JSON_FRAMING:
            add("JSON_FRAMING_MISMATCH")
        if not _is_hex64(build.get("selection_fingerprint")) or not _is_hex64(build.get("installer_selection_fingerprint")):
            add("SELECTION_FINGERPRINT_INVALID")
        if build.get("reproducibility") not in _REPRODUCIBILITY:
            add("REPRODUCIBILITY_STATUS_INVALID")

    artifacts = value.get("artifacts")
    seen: set[str] = set()
    if not isinstance(artifacts, list) or not 1 <= len(artifacts) <= 2:
        add("ARTIFACT_LIST_INVALID")
    else:
        for item in artifacts:
            if not _strict_keys(item, _ARTIFACT_KEYS):
                add("ARTIFACT_FIELDS_INVALID")
                continue
            artifact_id = item.get("id")
            if not isinstance(artifact_id, str) or artifact_id not in _ARTIFACT_IDS:
                add("ARTIFACT_ID_INVALID")
            elif artifact_id in seen:
                add("DUPLICATE_ARTIFACT_ID")
            else:
                seen.add(artifact_id)
            if not _safe_leaf(item.get("filename")):
                add("ARTIFACT_FILENAME_INVALID")
            if not isinstance(item.get("size_bytes"), int) or isinstance(item.get("size_bytes"), bool) or not 0 <= item.get("size_bytes", -1) <= 4 * 1024 * 1024 * 1024:
                add("ARTIFACT_SIZE_INVALID")
            if not _is_hex64(item.get("sha256")) or not _is_hex64(item.get("content_fingerprint")):
                add("ARTIFACT_DIGEST_INVALID")
        if "core_zip" not in seen:
            add("CORE_ARTIFACT_MISSING")
    if verify_self_hash and _is_hex64(value.get("manifest_sha256")) and manifest_self_hash(value) != value["manifest_sha256"]:
        add("MANIFEST_SELF_HASH_MISMATCH")
    return {"valid": not codes, "codes": codes}


def _git_text(repo_root: Path, args: list[str]) -> str:
    try:
        result = subprocess.run(["git", "-C", str(repo_root), *args], capture_output=True, check=False, timeout=10)
    except (OSError, subprocess.SubprocessError):
        _refuse("GIT_QUERY_FAILED")
    if result.returncode != 0 or len(result.stdout) > MAX_GIT_OUTPUT_BYTES:
        _refuse("GIT_QUERY_FAILED")
    try:
        return result.stdout.decode("utf-8").strip()
    except UnicodeDecodeError:
        _refuse("GIT_QUERY_FAILED")


def _git_ref_exists(repo_root: Path, ref: str) -> tuple[bool, str | None]:
    try:
        result = subprocess.run(["git", "-C", str(repo_root), "show-ref", "--verify", "--quiet", ref], capture_output=True, check=False, timeout=10)
    except (OSError, subprocess.SubprocessError):
        _refuse("GIT_QUERY_FAILED")
    if result.returncode == 0:
        return True, _git_text(repo_root, ["rev-parse", f"{ref}^{{commit}}"])
    if result.returncode == 1:
        return False, None
    _refuse("GIT_QUERY_FAILED")


def repository_oid_width(repo_root: Path) -> int:
    head = _git_text(repo_root, ["rev-parse", "HEAD"])
    width = len(head)
    if not _is_oid(head, width):
        _refuse("OID_WIDTH_UNSUPPORTED")
    return width


def tracked_files(repo_root: Path) -> list[str]:
    raw = _git_text(repo_root, ["ls-files", "-z"])
    names = [item for item in raw.split("\x00") if item]
    if len(names) > 100_000:
        _refuse("TRACKED_FILE_LIST_TOO_LARGE")
    if any(not _safe_path_name(name) for name in names):
        _refuse("UNSAFE_TRACKED_PATH")
    return sorted(set(names))


def _safe_path_name(value: Any) -> bool:
    if not isinstance(value, str) or not value or value.startswith(("/", "\\")) or ":" in value or "\\" in value:
        return False
    return all(part not in {"", ".", ".."} for part in PurePosixPath(value).parts)


def release_file_names(repo_root: Path) -> list[str]:
    selected: list[str] = []
    for name in tracked_files(repo_root):
        if name in RELEASE_EXCLUDED_FILES:
            continue
        if name in RELEASE_ROOT_FILES or name.startswith("Config/") and name.endswith(".example.json"):
            selected.append(name)
        elif any(name == root or name.startswith(root + "/") for root in RELEASE_ROOTS):
            selected.append(name)
    return sorted(set(selected))


def _ignored_generated(relative: str) -> bool:
    parts = PurePosixPath(relative).parts
    return "__pycache__" in parts or relative.casefold().endswith(".pyc")


def audit_installer_selection(repo_root: Path, installer_spec: Path) -> list[str]:
    """Audit actual ISS Source rules and filesystem expansion against one contract."""

    try:
        text = installer_spec.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        _refuse("INSTALLER_SPEC_UNREADABLE")
    expected = set(INSTALLER_SOURCE_RULES)
    source_lines = [line.strip() for line in text.splitlines() if line.strip().startswith("Source:")]
    actual_rules = {line.split(";", 1)[0].strip() for line in source_lines}
    if actual_rules != expected:
        _refuse("INSTALLER_SOURCE_RULE_UNREVIEWED")
    for root in RELEASE_ROOTS:
        line = next(line for line in source_lines if line.startswith(f'Source: "..\\{root}\\*"'))
        if "__pycache__" not in line or "*.pyc" not in line:
            _refuse("INSTALLER_GENERATED_EXCLUDE_MISSING")
        if root == "distribution" and "release_manifest.json" not in line:
            _refuse("HISTORICAL_MANIFEST_NOT_EXCLUDED")
    config_line = next(line for line in source_lines if line.startswith('Source: "..\\Config\\*.example.json"'))
    if "*.pyc" in config_line:
        _refuse("INSTALLER_CONFIG_RULE_INVALID")

    tracked = set(tracked_files(repo_root))
    root_set = set(RELEASE_ROOTS)
    for root in RELEASE_ROOTS:
        root_path = repo_root / root
        if not root_path.exists():
            continue
        for path in root_path.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(repo_root).as_posix()
            if _ignored_generated(relative):
                continue
            if relative not in tracked:
                _refuse("UNTRACKED_INSTALLER_SOURCE")
    for name in RELEASE_ROOT_FILES:
        if not (repo_root / name).is_file() or name not in tracked:
            _refuse("INSTALLER_ROOT_FILE_UNAVAILABLE")
    config_path = repo_root / "Config"
    if config_path.exists():
        for path in config_path.glob("*.example.json"):
            relative = path.relative_to(repo_root).as_posix()
            if relative not in tracked:
                _refuse("UNTRACKED_INSTALLER_SOURCE")
    return release_file_names(repo_root)


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
        digest = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            size = path.stat().st_size
        except (OSError, ValueError):
            _refuse("SOURCE_FILE_UNREADABLE")
        records.append({"path": name, "size_bytes": size, "sha256": digest.hexdigest()})
    return records


def selection_fingerprint(repo_root: Path, names: Iterable[str]) -> str:
    return hashlib.sha256(canonical_json(selection_records(repo_root, names))).hexdigest()


def installer_selection_fingerprint(names: Iterable[str]) -> str:
    return hashlib.sha256(canonical_json(sorted(set(names)))).hexdigest()


def compute_build_input_fingerprint(source_commit: str, phase: str, intended_tag: str, selection_digest: str, installer_digest: str) -> str:
    payload = {
        "version": REVIEWED_RELEASE_VERSION,
        "source_commit": source_commit,
        "phase": phase,
        "intended_tag": intended_tag,
        "selection_fingerprint": selection_digest,
        "installer_selection_fingerprint": installer_digest,
        "source_date_epoch": SOURCE_DATE_EPOCH,
        "zip_compression": "deflate",
        "zip_compression_level": 9,
        "zip_entry_timestamp": ZIP_ENTRY_TIMESTAMP,
        "json_framing": RAW_JSON_FRAMING,
    }
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def recompute_current_binding(repo_root: Path, *, intended_tag: str) -> tuple[dict[str, Any], list[str]]:
    """Recompute all source/selection/build fields from the exact repository."""

    codes: list[str] = []
    try:
        width = repository_oid_width(repo_root)
        head = _git_text(repo_root, ["rev-parse", "HEAD"])
        branch = _git_text(repo_root, ["symbolic-ref", "--quiet", "--short", "HEAD"])
        tag_exists, tag_commit = _git_ref_exists(repo_root, f"refs/tags/{intended_tag}")
        source_names = release_file_names(repo_root)
        installer_names = audit_installer_selection(repo_root, repo_root / "distribution" / "installer.iss")
        if source_names != installer_names:
            codes.append("CORE_INSTALLER_SELECTION_MISMATCH")
        selection_digest = selection_fingerprint(repo_root, source_names)
        installer_digest = installer_selection_fingerprint(installer_names)
        phase = "tagged" if tag_exists else "pre_tag"
        expected = {
            "phase": phase,
            "source_commit": head,
            "build_commit": head,
            "source_branch": branch,
            "intended_tag": intended_tag,
            "tag_commit": tag_commit if tag_exists else None,
            "build_input_fingerprint": compute_build_input_fingerprint(head, phase, intended_tag, selection_digest, installer_digest),
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
            "oid_width": width,
        }
        identity = source_identity_gate(repo_root, intended_tag=intended_tag, phase=phase)
        codes.extend(identity.get("codes", []))
        return expected, sorted(set(codes))
    except ProvenanceRefusal as exc:
        return {}, [exc.code]


def compare_manifest_to_current(manifest: Mapping[str, Any], current: Mapping[str, Any], current_codes: Iterable[str]) -> list[str]:
    """Return fixed mismatch codes for a self-hashed stale/moved record."""

    codes = list(current_codes)

    def add(code: str) -> None:
        if code not in codes:
            codes.append(code)

    for field, code in (
        ("source_commit", "SOURCE_COMMIT_MISMATCH"),
        ("build_commit", "BUILD_COMMIT_MISMATCH"),
        ("source_branch", "BRANCH_HEAD_MISMATCH"),
        ("phase", "PHASE_MISMATCH"),
        ("intended_tag", "INTENDED_TAG_MISMATCH"),
        ("tag_commit", "TAG_COMMIT_MISMATCH"),
        ("build_input_fingerprint", "BUILD_INPUT_FINGERPRINT_MISMATCH"),
    ):
        if field in current and manifest.get(field) != current.get(field):
            add(code)
    expected_build = current.get("build_parameters")
    actual_build = manifest.get("build_parameters")
    if isinstance(expected_build, Mapping) and isinstance(actual_build, Mapping):
        if actual_build.get("selection_fingerprint") != expected_build.get("selection_fingerprint"):
            add("SELECTION_FINGERPRINT_MISMATCH")
        if actual_build.get("installer_selection_fingerprint") != expected_build.get("installer_selection_fingerprint"):
            add("INSTALLER_SELECTION_MISMATCH")
        for field, code in (
            ("source_date_epoch", "SOURCE_DATE_EPOCH_MISMATCH"),
            ("zip_compression", "ZIP_PARAMETERS_MISMATCH"),
            ("zip_compression_level", "ZIP_PARAMETERS_MISMATCH"),
            ("zip_entry_timestamp", "ZIP_TIMESTAMP_MISMATCH"),
            ("json_framing", "JSON_FRAMING_MISMATCH"),
            ("reproducibility", "REPRODUCIBILITY_STATUS_INVALID"),
        ):
            if actual_build.get(field) != expected_build.get(field):
                add(code)
    return sorted(set(codes))


def source_identity_gate(repo_root: Path, *, intended_tag: str, phase: str) -> dict[str, Any]:
    codes: list[str] = []
    try:
        width = repository_oid_width(repo_root)
        head = _git_text(repo_root, ["rev-parse", "HEAD"])
        branch = _git_text(repo_root, ["symbolic-ref", "--quiet", "--short", "HEAD"])
        dirty = _git_text(repo_root, ["status", "--porcelain=v1"])
    except ProvenanceRefusal as exc:
        return _projection("source_identity", False, [exc.code])
    if branch != RELEASE_BRANCH:
        codes.append("SOURCE_BRANCH_MISMATCH")
    if dirty:
        codes.append("DIRTY_SOURCE")
    for relative in ("scripts/build_installer.py", "scripts/build_core_release.py", "scripts/verify_release_provenance.py", "distribution/release_manifest.v2.schema.json"):
        try:
            _git_text(repo_root, ["cat-file", "-e", f"{head}:{relative}"])
        except ProvenanceRefusal:
            codes.append("BUILD_INPUT_NOT_COMMITTED")
            break
    if phase == "tagged":
        try:
            exists, tag_commit = _git_ref_exists(repo_root, f"refs/tags/{intended_tag}")
        except ProvenanceRefusal as exc:
            return _projection("source_identity", False, [exc.code], oid_width=width)
        if not exists:
            codes.append("INTENDED_TAG_UNAVAILABLE")
        elif tag_commit != head:
            codes.append("TAG_SOURCE_MISMATCH")
    elif phase == "pre_tag":
        if SAFE_TAG.fullmatch(intended_tag) is None:
            codes.append("INTENDED_TAG_INVALID")
        else:
            try:
                occupied, _tag_commit = _git_ref_exists(repo_root, f"refs/tags/{intended_tag}")
            except ProvenanceRefusal as exc:
                return _projection("source_identity", False, [exc.code], oid_width=width)
            if occupied:
                codes.append("TAG_OCCUPIED")
    else:
        codes.append("PHASE_INVALID")
    return _projection("source_identity", not codes, codes, oid_width=width)


def require_source_identity(repo_root: Path, *, intended_tag: str, phase: str) -> dict[str, Any]:
    result = source_identity_gate(repo_root, intended_tag=intended_tag, phase=phase)
    if not result["ok"]:
        priority = (
            "SOURCE_BRANCH_MISMATCH",
            "DIRTY_SOURCE",
            "TAG_SOURCE_MISMATCH",
            "TAG_OCCUPIED",
            "INTENDED_TAG_UNAVAILABLE",
            "BUILD_INPUT_NOT_COMMITTED",
        )
        code = next((item for item in priority if item in result["codes"]), result["codes"][0] if result["codes"] else "SOURCE_IDENTITY_REFUSED")
        _refuse(code)
    return result


def verify_pre_tag_manifest(value: Any, repo_root: Path, *, intended_tag: str) -> dict[str, Any]:
    try:
        width = repository_oid_width(repo_root)
        structural = validate_release_manifest(value, require_phase="pre_tag", oid_width=width)
    except ProvenanceRefusal as exc:
        return _projection("pre_tag", False, [exc.code])
    if not structural["valid"]:
        return _projection("pre_tag", False, structural["codes"])
    if value.get("intended_tag") != intended_tag:
        return _projection("pre_tag", False, ["INTENDED_TAG_MISMATCH"])
    current, current_codes = recompute_current_binding(repo_root, intended_tag=intended_tag)
    codes = compare_manifest_to_current(value, current, current_codes)
    return _projection("pre_tag", not codes, codes)


def _zip_content_fingerprint(path: Path) -> str:
    rows: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(path, "r") as archive:
            for info in sorted(archive.infolist(), key=lambda item: item.filename):
                if not _safe_path_name(info.filename):
                    _refuse("ARCHIVE_ENTRY_NAME_INVALID")
                digest = hashlib.sha256()
                with archive.open(info, "r") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                rows.append({"filename": info.filename, "size_bytes": info.file_size, "sha256": digest.hexdigest()})
    except ProvenanceRefusal:
        raise
    except (OSError, ValueError, zipfile.BadZipFile):
        _refuse("ARCHIVE_READ_FAILED")
    return hashlib.sha256(canonical_json(rows)).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except (OSError, ValueError):
        _refuse("ARTIFACT_READ_FAILED")
    return digest.hexdigest()


def verify_artifacts(manifest: Mapping[str, Any], artifact_root: Path) -> list[str]:
    codes: list[str] = []
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        return ["ARTIFACT_LIST_INVALID"]
    for artifact in artifacts:
        if not isinstance(artifact, dict) or not _safe_leaf(artifact.get("filename")):
            codes.append("ARTIFACT_FILENAME_INVALID")
            continue
        path = (artifact_root.resolve() / artifact["filename"]).resolve()
        try:
            path.relative_to(artifact_root.resolve())
        except ValueError:
            codes.append("ARTIFACT_PATH_ESCAPE")
            continue
        try:
            if not path.is_file() or path.is_symlink():
                codes.append("ARTIFACT_UNAVAILABLE")
                continue
            if path.stat().st_size != artifact.get("size_bytes"):
                codes.append("ARTIFACT_SIZE_MISMATCH")
                continue
            sha = _sha256_file(path)
            if sha != artifact.get("sha256"):
                codes.append("ARTIFACT_HASH_MISMATCH")
                continue
            content = _zip_content_fingerprint(path) if artifact.get("id") == "core_zip" else sha
            if content != artifact.get("content_fingerprint"):
                codes.append("ARTIFACT_CONTENT_FINGERPRINT_MISMATCH")
        except ProvenanceRefusal as exc:
            codes.append(exc.code)
    return sorted(set(codes))


def verify_manifest_file(manifest_path: Path, *, repo_root: Path = ROOT, artifact_root: Path | None = None) -> dict[str, Any]:
    try:
        manifest = load_manifest(manifest_path, require_canonical=True)
        width = repository_oid_width(repo_root)
        structural = validate_release_manifest(manifest, oid_width=width)
    except ProvenanceRefusal as exc:
        return _projection("verify", False, [exc.code])
    codes = list(structural["codes"])
    if structural["valid"]:
        current, current_codes = recompute_current_binding(repo_root, intended_tag=manifest["intended_tag"])
        codes.extend(compare_manifest_to_current(manifest, current, current_codes))
        if artifact_root is not None:
            codes.extend(verify_artifacts(manifest, artifact_root))
    return _projection("verify", not codes, sorted(set(codes)))


def classify_historical_manifest(manifest_path: Path = HISTORICAL_MANIFEST_PATH, *, repo_root: Path = ROOT) -> dict[str, Any]:
    try:
        legacy = load_manifest(manifest_path, require_canonical=False)
        head = _git_text(repo_root, ["rev-parse", "HEAD"])
        branch = _git_text(repo_root, ["symbolic-ref", "--quiet", "--short", "HEAD"])
        exists, tag_commit = _git_ref_exists(repo_root, f"refs/tags/{REVIEWED_INTENDED_TAG}")
    except ProvenanceRefusal as exc:
        return _projection("historical", False, [exc.code], status="HISTORICAL_METADATA")
    codes: list[str] = []
    if legacy.get("version") != REVIEWED_RELEASE_VERSION:
        codes.append("VERSION_MISMATCH")
    if legacy.get("tag") != REVIEWED_INTENDED_TAG:
        codes.append("VERSION_TAG_MISMATCH")
    if legacy.get("git_commit") != head:
        codes.append("SOURCE_COMMIT_MISMATCH")
    if legacy.get("git_branch") != branch:
        codes.append("BRANCH_HEAD_MISMATCH")
    if not exists or legacy.get("tag_commit") != tag_commit:
        codes.append("TAG_COMMIT_MISMATCH")
    status = "LEGACY_INCONSISTENT" if codes else "HISTORICAL_METADATA"
    return _projection("historical", not codes, sorted(set(codes)), status=status)


def _projection(operation: str, ok: bool, codes: Iterable[str], *, status: str | None = None, oid_width: int | None = None) -> dict[str, Any]:
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
    if oid_width in SUPPORTED_OID_WIDTHS:
        result["oid_width"] = oid_width
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify detached Local AI Hub release provenance.")
    parser.add_argument("--manifest", type=Path, default=HISTORICAL_MANIFEST_PATH)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--artifact-root", type=Path, default=None)
    parser.add_argument("--historical", action="store_true")
    parser.add_argument("--pre-tag", action="store_true")
    parser.add_argument("--intended-tag", type=str, default=None)
    args = parser.parse_args(argv)
    if args.historical:
        result = classify_historical_manifest(args.manifest, repo_root=args.repo)
    elif args.pre_tag:
        if not args.intended_tag:
            result = _projection("pre_tag", False, ["PRE_TAG_REQUIRES_EXPLICIT_REVIEWED_INPUT"])
        else:
            try:
                result = verify_pre_tag_manifest(load_manifest(args.manifest, require_canonical=True), args.repo, intended_tag=args.intended_tag)
            except ProvenanceRefusal as exc:
                result = _projection("pre_tag", False, [exc.code])
    else:
        result = verify_manifest_file(args.manifest, repo_root=args.repo, artifact_root=args.artifact_root)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "APPROVED_RELEASE_VERSIONS",
    "HISTORICAL_MANIFEST_PATH",
    "INSTALLER_SOURCE_RULES",
    "RAW_JSON_FRAMING",
    "RELEASE_BRANCH",
    "RELEASE_EXCLUDED_FILES",
    "RELEASE_ROOT_FILES",
    "RELEASE_ROOTS",
    "RELEASE_SELECTION_CONTRACT",
    "REVIEWED_INTENDED_TAG",
    "REVIEWED_RELEASE_VERSION",
    "SCHEMA_PATH",
    "SCHEMA_VERSION",
    "SOURCE_DATE_EPOCH",
    "ZIP_ENTRY_TIMESTAMP",
    "ProvenanceRefusal",
    "audit_installer_selection",
    "canonical_json",
    "classify_historical_manifest",
    "compare_manifest_to_current",
    "compute_build_input_fingerprint",
    "installer_selection_fingerprint",
    "load_manifest",
    "manifest_self_hash",
    "release_file_names",
    "recompute_current_binding",
    "repository_oid_width",
    "require_source_identity",
    "selection_fingerprint",
    "source_identity_gate",
    "strict_json_load",
    "tracked_files",
    "validate_release_manifest",
    "verify_pre_tag_manifest",
    "verify_artifacts",
    "verify_manifest_file",
]
