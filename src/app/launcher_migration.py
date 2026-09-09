"""Transactional migration of the installed LocalAIHub launcher shell.

The application payload and the stable shell have different lifetimes.  A
payload can be staged while the old desktop is still running, but the shell
must not be replaced until the old process has exited.  This module keeps that
boundary small and deterministic so the updater/watchdog can share the same
verification and rollback rules in tests and on Windows.

Only the installed product root is accepted.  Launcher bundles are copied
without following symlinks or Windows reparse points and every activation is
recorded by an opaque transaction id; no path or command line is written to
the public updater projection.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import re
from typing import Any


LAUNCHER_MANIFEST_SCHEMA = "local-ai-hub-launcher-bundle.v1"
LAUNCHER_ACTIVATION_SCHEMA = "local-ai-hub-launcher-activation.v1"
LAUNCHER_SHELL_MANIFEST_NAME = "launcher-manifest.json"
LAUNCHER_EXECUTABLE_NAME = "LocalAIHub.exe"
LAUNCHER_BUNDLE_NAME = "LocalAIHub"
LAUNCHER_INTERNAL_NAME = "_internal"
MAX_LAUNCHER_FILES = 10_000
MAX_LAUNCHER_BYTES = 500 * 1024 * 1024
_TRANSACTION_RE = re.compile(r"^txn-[0-9a-f]{32}$")
_PAYLOAD_RE = re.compile(r"^main-[0-9a-f]{12}$")
_SOURCE_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_WORKFLOW_RUN_ID = (1 << 63) - 1
_HARD_CRASH_ENV = "LOCALAIHUB_TEST_HARD_CRASH_BOUNDARY"
_ACTIVATION_STATUSES = frozenset({
    "prepared", "activation_copy_complete", "old_exe_moved", "old_internal_moved",
    "old_manifest_moved", "candidate_exe_moved", "candidate_internal_moved",
    "shell_manifest_written", "shell_switched", "rollback_in_progress", "restored", "switching",
    "previous_exe_backup_complete", "candidate_internal_prepared", "candidate_internal_installed",
    "shell_retained", "candidate_exe_switched", "health_admission_pending",
}) | frozenset(
    f"{prefix}_{name.replace('.', '_')}_moved"
    for prefix in ("candidate", "previous")
    for name in (LAUNCHER_INTERNAL_NAME, LAUNCHER_EXECUTABLE_NAME, LAUNCHER_SHELL_MANIFEST_NAME)
)


class LauncherMigrationError(ValueError):
    """Fixed-code refusal from the launcher-shell transaction boundary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _hard_crash_checkpoint(name: str) -> None:
    """Test-only hard termination hook used by the subprocess fault matrix.

    The hook is inert unless a deliberately named test environment variable is
    present.  It models power loss/process termination at a durable boundary;
    unlike an exception injection, no ``finally`` block in the mutating
    process gets an opportunity to repair the shell.
    """

    if os.environ.get(_HARD_CRASH_ENV) == name:
        os._exit(197)


def _is_reparse(path: Path) -> bool:
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

            attributes = int(ctypes.windll.kernel32.GetFileAttributesW(str(path))) & 0xFFFFFFFF
            return attributes != 0xFFFFFFFF and bool(attributes & 0x400)
        except AttributeError:
            return False
        except OSError:
            return True
    return False


def _canonical(value: object) -> bytes:
    try:
        return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise LauncherMigrationError("LAUNCHER_MANIFEST_INVALID") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except (OSError, ValueError) as exc:
        raise LauncherMigrationError("LAUNCHER_FILE_READ_FAILED") from exc
    return digest.hexdigest()


def _safe_child(root: Path, relative: str) -> Path:
    candidate = root / Path(relative)
    try:
        candidate.absolute().relative_to(root.absolute())
    except ValueError as exc:
        raise LauncherMigrationError("LAUNCHER_PATH_INVALID") from exc
    if _is_reparse(candidate):
        raise LauncherMigrationError("LAUNCHER_REPARSE")
    return candidate


def launcher_tree_manifest(bundle: Path) -> dict[str, Any]:
    """Return the complete bounded manifest for an onedir bundle.

    The returned rows contain only relative names, sizes and hashes.  The
    caller may persist the digest and counts in an update contract without
    leaking a workstation path.
    """

    root = bundle.absolute()
    if not root.is_dir() or root.is_symlink() or root.name.casefold() != LAUNCHER_BUNDLE_NAME.casefold() or _is_reparse(root):
        raise LauncherMigrationError("STABLE_LAUNCHER_BUNDLE_REQUIRED")
    executable = root / LAUNCHER_EXECUTABLE_NAME
    internal = root / LAUNCHER_INTERNAL_NAME
    if not executable.is_file() or executable.is_symlink() or _is_reparse(executable):
        raise LauncherMigrationError("STABLE_LAUNCHER_REQUIRED")
    if not internal.is_dir() or internal.is_symlink() or _is_reparse(internal):
        raise LauncherMigrationError("LAUNCHER_INTERNAL_MISSING")

    rows: list[dict[str, Any]] = []
    total = 0
    for current, directories, filenames in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        if _is_reparse(current_path):
            raise LauncherMigrationError("LAUNCHER_REPARSE")
        safe_directories: list[str] = []
        for name in sorted(directories):
            child = current_path / name
            if _is_reparse(child) or not child.is_dir():
                raise LauncherMigrationError("LAUNCHER_REPARSE")
            safe_directories.append(name)
        directories[:] = safe_directories
        for name in sorted(filenames):
            path = current_path / name
            if _is_reparse(path) or not path.is_file():
                raise LauncherMigrationError("LAUNCHER_REPARSE")
            size = path.stat().st_size
            if type(size) is not int or size < 0:
                raise LauncherMigrationError("LAUNCHER_FILE_INVALID")
            total += size
            if len(rows) >= MAX_LAUNCHER_FILES or total > MAX_LAUNCHER_BYTES:
                raise LauncherMigrationError("LAUNCHER_BOUNDS_EXCEEDED")
            rows.append({
                "name": path.relative_to(root).as_posix(),
                "size": size,
                "sha256": _sha256(path),
            })
    rows.sort(key=lambda row: str(row["name"]))
    if not rows or not any(row["name"].casefold() == LAUNCHER_EXECUTABLE_NAME.casefold() for row in rows):
        raise LauncherMigrationError("STABLE_LAUNCHER_REQUIRED")
    raw = _canonical(rows)
    executable_row = next(row for row in rows if row["name"].casefold() == LAUNCHER_EXECUTABLE_NAME.casefold())
    return {
        "schema_version": LAUNCHER_MANIFEST_SCHEMA,
        "format": "onedir",
        "executable": LAUNCHER_EXECUTABLE_NAME,
        "executable_sha256": executable_row["sha256"],
        "tree_manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "file_count": len(rows),
        "total_bytes": total,
        "files": rows,
    }


def verify_launcher_manifest(bundle: Path, expected: dict[str, Any] | None = None) -> dict[str, Any]:
    """Re-read a bundle and optionally match an artifact contract."""

    actual = launcher_tree_manifest(bundle)
    if expected is None:
        return actual
    checks = {
        "format": "onedir",
        "executable_sha256": actual["executable_sha256"],
        "tree_manifest_sha256": actual["tree_manifest_sha256"],
        "file_count": actual["file_count"],
        "total_bytes": actual["total_bytes"],
    }
    for key, value in checks.items():
        if key in expected and expected.get(key) != value:
            raise LauncherMigrationError("LAUNCHER_MANIFEST_MISMATCH")
    expected_files = expected.get("files")
    if expected_files is not None and expected_files != actual["files"]:
        raise LauncherMigrationError("LAUNCHER_TREE_CHANGED")
    return actual


def _shell_tree_manifest(root: Path) -> dict[str, Any]:
    """Manifest only the files that are switched as the installed shell."""

    install = root.absolute()
    if not install.is_dir() or install.is_symlink() or _is_reparse(install):
        raise LauncherMigrationError("INSTALL_ROOT_INVALID")
    executable = install / LAUNCHER_EXECUTABLE_NAME
    internal = install / LAUNCHER_INTERNAL_NAME
    if not executable.is_file() or executable.is_symlink() or _is_reparse(executable):
        raise LauncherMigrationError("STABLE_LAUNCHER_REQUIRED")
    if not internal.is_dir() or internal.is_symlink() or _is_reparse(internal):
        raise LauncherMigrationError("LAUNCHER_INTERNAL_MISSING")
    rows: list[dict[str, Any]] = []
    total = 0
    candidates = [executable]
    for current, directories, filenames in os.walk(internal, topdown=True, followlinks=False):
        current_path = Path(current)
        if _is_reparse(current_path):
            raise LauncherMigrationError("LAUNCHER_REPARSE")
        directories[:] = sorted(directories)
        for name in directories + filenames:
            child = current_path / name
            if _is_reparse(child):
                raise LauncherMigrationError("LAUNCHER_REPARSE")
        candidates.extend(Path(current) / name for name in sorted(filenames))
    for path in candidates:
        if not path.is_file() or _is_reparse(path):
            raise LauncherMigrationError("LAUNCHER_REPARSE")
        size = path.stat().st_size
        total += size
        if len(rows) >= MAX_LAUNCHER_FILES or total > MAX_LAUNCHER_BYTES:
            raise LauncherMigrationError("LAUNCHER_BOUNDS_EXCEEDED")
        rows.append({"name": path.relative_to(install).as_posix(), "size": size, "sha256": _sha256(path)})
    rows.sort(key=lambda row: str(row["name"]))
    raw = _canonical(rows)
    exe_row = next(row for row in rows if row["name"].casefold() == LAUNCHER_EXECUTABLE_NAME.casefold())
    return {
        "schema_version": LAUNCHER_MANIFEST_SCHEMA,
        "format": "onedir",
        "executable": LAUNCHER_EXECUTABLE_NAME,
        "executable_sha256": exe_row["sha256"],
        "tree_manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "file_count": len(rows),
        "total_bytes": total,
        "files": rows,
    }


def _verify_shell_manifest(root: Path, expected: dict[str, Any]) -> None:
    actual = _shell_tree_manifest(root)
    if actual != expected:
        raise LauncherMigrationError("LAUNCHER_TREE_CHANGED")


def _load_shell_manifest(root: Path) -> dict[str, Any]:
    path = _shell_manifest_path(root)
    if not path.is_file() or path.is_symlink() or _is_reparse(path):
        raise LauncherMigrationError("LAUNCHER_MANIFEST_MISSING")
    try:
        raw = path.read_bytes()
        if len(raw) > 64 * 1024:
            raise LauncherMigrationError("LAUNCHER_MANIFEST_INVALID")
        value = json.loads(raw.decode("utf-8"))
    except LauncherMigrationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise LauncherMigrationError("LAUNCHER_MANIFEST_INVALID") from exc
    if not isinstance(value, dict):
        raise LauncherMigrationError("LAUNCHER_MANIFEST_INVALID")
    required = {
        "schema_version", "product_id", "payload_id", "source_commit", "format",
        "executable", "executable_sha256", "tree_manifest_sha256", "file_count", "total_bytes",
        "workflow_run_id",
    }
    if set(value) != required:
        raise LauncherMigrationError("LAUNCHER_MANIFEST_INVALID")
    payload_id = value.get("payload_id")
    source_commit = value.get("source_commit")
    run_id = value.get("workflow_run_id")
    if (
        value.get("schema_version") != LAUNCHER_MANIFEST_SCHEMA
        or value.get("product_id") != "LocalAIHub"
        or value.get("format") != "onedir"
        or value.get("executable") != LAUNCHER_EXECUTABLE_NAME
        or not isinstance(payload_id, str) or _PAYLOAD_RE.fullmatch(payload_id) is None
        or not isinstance(source_commit, str) or _SOURCE_RE.fullmatch(source_commit) is None
        or payload_id != f"main-{source_commit[:12]}"
        or (run_id is not None and (isinstance(run_id, bool) or not isinstance(run_id, int) or not 0 < run_id <= _MAX_WORKFLOW_RUN_ID))
        or not isinstance(value.get("executable_sha256"), str) or _SHA256_RE.fullmatch(value["executable_sha256"]) is None
        or not isinstance(value.get("tree_manifest_sha256"), str) or _SHA256_RE.fullmatch(value["tree_manifest_sha256"]) is None
        or isinstance(value.get("file_count"), bool) or not isinstance(value.get("file_count"), int)
        or not 2 <= value["file_count"] <= MAX_LAUNCHER_FILES
        or isinstance(value.get("total_bytes"), bool) or not isinstance(value.get("total_bytes"), int)
        or not 1 <= value["total_bytes"] <= MAX_LAUNCHER_BYTES
    ):
        raise LauncherMigrationError("LAUNCHER_MANIFEST_INVALID")
    return value


def _attest_root_shell(root: Path) -> dict[str, Any]:
    """Re-attest the complete onedir root shell against its signed metadata."""

    install = _root_is_safe(root)
    manifest = _load_shell_manifest(install)
    actual = _shell_tree_manifest(install)
    for key in ("format", "executable_sha256", "tree_manifest_sha256", "file_count", "total_bytes"):
        if manifest.get(key) != actual.get(key):
            raise LauncherMigrationError("LAUNCHER_MANIFEST_MISMATCH")
    return manifest


def _bootable_format(root: Path) -> str | None:
    install = root.absolute()
    exe = install / LAUNCHER_EXECUTABLE_NAME
    internal = install / LAUNCHER_INTERNAL_NAME
    try:
        if not exe.is_file() or exe.is_symlink() or _is_reparse(exe):
            return None
        if internal.exists() or internal.is_symlink():
            if internal.is_dir() and not internal.is_symlink() and not _is_reparse(internal):
                return "onedir"
            return None
        return "single_file"
    except OSError:
        return None


def _current_activation_state(path: Path, transaction_id: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LauncherMigrationError("LAUNCHER_STATE_INVALID") from exc
    if not isinstance(value, dict) or value.get("schema_version") != LAUNCHER_ACTIVATION_SCHEMA or value.get("transaction_id") != transaction_id:
        raise LauncherMigrationError("LAUNCHER_TRANSACTION_MISMATCH")
    required = {
        "schema_version", "transaction_id", "payload_id", "source_commit", "format",
        "executable_sha256", "tree_manifest_sha256", "file_count", "total_bytes", "status",
    }
    if not required.issubset(value) or value.get("format") != "onedir" or value.get("status") not in _ACTIVATION_STATUSES:
        raise LauncherMigrationError("LAUNCHER_STATE_INVALID")
    payload_id = value.get("payload_id")
    source_commit = value.get("source_commit")
    if (
        not isinstance(payload_id, str) or _PAYLOAD_RE.fullmatch(payload_id) is None
        or not isinstance(source_commit, str) or _SOURCE_RE.fullmatch(source_commit) is None
        or payload_id != f"main-{source_commit[:12]}"
        or not isinstance(value.get("executable_sha256"), str) or _SHA256_RE.fullmatch(value["executable_sha256"]) is None
        or not isinstance(value.get("tree_manifest_sha256"), str) or _SHA256_RE.fullmatch(value["tree_manifest_sha256"]) is None
        or isinstance(value.get("file_count"), bool) or not isinstance(value.get("file_count"), int)
        or not 2 <= value["file_count"] <= MAX_LAUNCHER_FILES
        or isinstance(value.get("total_bytes"), bool) or not isinstance(value.get("total_bytes"), int)
        or not 1 <= value["total_bytes"] <= MAX_LAUNCHER_BYTES
    ):
        raise LauncherMigrationError("LAUNCHER_STATE_INVALID")
    run_id = value.get("workflow_run_id")
    if run_id is not None and (isinstance(run_id, bool) or not isinstance(run_id, int) or not 0 < run_id <= _MAX_WORKFLOW_RUN_ID):
        raise LauncherMigrationError("LAUNCHER_STATE_INVALID")
    previous_format = value.get("previous_format")
    if previous_format is not None and previous_format not in {"single_file", "onedir"}:
        raise LauncherMigrationError("LAUNCHER_STATE_INVALID")
    for key in ("previous_executable_sha256", "previous_tree_manifest_sha256"):
        item = value.get(key)
        if item is not None and (not isinstance(item, str) or _SHA256_RE.fullmatch(item) is None):
            raise LauncherMigrationError("LAUNCHER_STATE_INVALID")
    previous_manifest_sha256 = value.get("previous_manifest_sha256")
    if previous_manifest_sha256 is not None and (
        not isinstance(previous_manifest_sha256, str) or _SHA256_RE.fullmatch(previous_manifest_sha256) is None
    ):
        raise LauncherMigrationError("LAUNCHER_STATE_INVALID")
    previous_file_count_value = value.get("previous_file_count")
    if previous_file_count_value is not None and (
        isinstance(previous_file_count_value, bool)
        or not isinstance(previous_file_count_value, int)
        or not 2 <= previous_file_count_value <= MAX_LAUNCHER_FILES
    ):
        raise LauncherMigrationError("LAUNCHER_STATE_INVALID")
    previous_total_bytes_value = value.get("previous_total_bytes")
    if previous_total_bytes_value is not None and (
        isinstance(previous_total_bytes_value, bool)
        or not isinstance(previous_total_bytes_value, int)
        or not 1 <= previous_total_bytes_value <= MAX_LAUNCHER_BYTES
    ):
        raise LauncherMigrationError("LAUNCHER_STATE_INVALID")
    retained_identity = value.get("retained_shell_identity")
    if retained_identity is not None:
        if not isinstance(retained_identity, dict) or set(retained_identity) != {
            "schema_version", "product_id", "payload_id", "source_commit", "format", "executable",
            "workflow_run_id", "executable_sha256", "tree_manifest_sha256", "file_count", "total_bytes",
        }:
            raise LauncherMigrationError("LAUNCHER_STATE_INVALID")
        if (
            retained_identity.get("schema_version") != LAUNCHER_MANIFEST_SCHEMA
            or retained_identity.get("product_id") != "LocalAIHub"
            or retained_identity.get("format") != "onedir"
            or retained_identity.get("executable") != LAUNCHER_EXECUTABLE_NAME
            or not isinstance(retained_identity.get("payload_id"), str)
            or _PAYLOAD_RE.fullmatch(retained_identity["payload_id"]) is None
            or not isinstance(retained_identity.get("source_commit"), str)
            or _SOURCE_RE.fullmatch(retained_identity["source_commit"]) is None
            or retained_identity.get("payload_id") != f"main-{retained_identity['source_commit'][:12]}"
            or not isinstance(retained_identity.get("executable_sha256"), str)
            or _SHA256_RE.fullmatch(retained_identity["executable_sha256"]) is None
            or not isinstance(retained_identity.get("tree_manifest_sha256"), str)
            or _SHA256_RE.fullmatch(retained_identity["tree_manifest_sha256"]) is None
            or not isinstance(retained_identity.get("file_count"), int)
            or isinstance(retained_identity.get("file_count"), bool)
            or not 2 <= retained_identity["file_count"] <= MAX_LAUNCHER_FILES
            or not isinstance(retained_identity.get("total_bytes"), int)
            or isinstance(retained_identity.get("total_bytes"), bool)
            or not 1 <= retained_identity["total_bytes"] <= MAX_LAUNCHER_BYTES
            or (
                retained_identity.get("workflow_run_id") is not None
                and (
                    isinstance(retained_identity.get("workflow_run_id"), bool)
                    or not isinstance(retained_identity.get("workflow_run_id"), int)
                    or not 0 < retained_identity["workflow_run_id"] <= _MAX_WORKFLOW_RUN_ID
                )
            )
        ):
            raise LauncherMigrationError("LAUNCHER_STATE_INVALID")
        if value.get("previous_format") not in {None, "onedir"}:
            raise LauncherMigrationError("LAUNCHER_STATE_INVALID")
        for state_key, identity_key in (
            ("previous_executable_sha256", "executable_sha256"),
            ("previous_tree_manifest_sha256", "tree_manifest_sha256"),
            ("previous_file_count", "file_count"),
            ("previous_total_bytes", "total_bytes"),
        ):
            if value.get(state_key) is not None and value.get(state_key) != retained_identity.get(identity_key):
                raise LauncherMigrationError("LAUNCHER_STATE_INVALID")
    return value


def _forensic_destination(backup: Path) -> Path:
    base = backup / "candidate-shell"
    if not base.exists():
        base.mkdir(parents=True, exist_ok=False)
        return base
    for index in range(2, 101):
        candidate = backup / f"candidate-shell-{index}"
        if not candidate.exists():
            candidate.mkdir(parents=True, exist_ok=False)
            return candidate
    raise LauncherMigrationError("LAUNCHER_FORENSIC_BOUNDS_EXCEEDED")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(_canonical(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except (OSError, ValueError, UnicodeError) as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise LauncherMigrationError("LAUNCHER_STATE_WRITE_FAILED") from exc


def _copy_regular(source: Path, destination: Path) -> None:
    if _is_reparse(source) or not source.is_file():
        raise LauncherMigrationError("LAUNCHER_REPARSE")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink() or _is_reparse(destination.parent):
        raise LauncherMigrationError("LAUNCHER_DESTINATION_OCCUPIED")
    shutil.copy2(source, destination)


def copy_launcher_bundle(source: Path, destination: Path) -> dict[str, Any]:
    """Copy and verify an onedir bundle into an empty destination."""

    manifest = launcher_tree_manifest(source)
    target = destination.absolute()
    if target.exists() or target.is_symlink():
        raise LauncherMigrationError("LAUNCHER_DESTINATION_OCCUPIED")
    if _is_reparse(target.parent):
        raise LauncherMigrationError("LAUNCHER_REPARSE")
    target.mkdir(parents=True, exist_ok=False)
    for row in manifest["files"]:
        _copy_regular(source.absolute() / Path(str(row["name"])), target / Path(str(row["name"])))
    verify_launcher_manifest(target, manifest)
    return manifest


def _root_is_safe(root: Path) -> Path:
    value = root.absolute()
    if not value.is_dir() or value.is_symlink() or _is_reparse(value):
        raise LauncherMigrationError("INSTALL_ROOT_INVALID")
    return value


def _shell_manifest_path(root: Path) -> Path:
    return root / LAUNCHER_SHELL_MANIFEST_NAME


def shell_identity(manifest: dict[str, Any], payload_id: str, source_commit: str, workflow_run_id: int | None) -> dict[str, Any]:
    """Create the path-free root manifest projected by a committed shell."""

    return {
        "schema_version": LAUNCHER_MANIFEST_SCHEMA,
        "product_id": "LocalAIHub",
        "payload_id": payload_id,
        "source_commit": source_commit,
        "format": "onedir",
        "executable": LAUNCHER_EXECUTABLE_NAME,
        "workflow_run_id": workflow_run_id,
        "executable_sha256": manifest["executable_sha256"],
        "tree_manifest_sha256": manifest["tree_manifest_sha256"],
        "file_count": manifest["file_count"],
        "total_bytes": manifest["total_bytes"],
    }


def activate_launcher_bundle(
    install_root: Path,
    candidate_bundle: Path,
    *,
    transaction_id: str,
    payload_id: str,
    source_commit: str,
    workflow_run_id: int | None = None,
    expected_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    r"""Activate a launcher without ever leaving the root without an EXE.

    A legacy one-file shell is *copied* to rollback first.  Its EXE remains at
    ``root\LocalAIHub.exe`` while the complete candidate ``_internal`` tree is
    atomically installed beside it; only then is the EXE atomically replaced.
    Thus a hard process death can leave either the old one-file route or the
    complete new onedir route, never an empty root.  Existing onedir shells
    are deliberately retained: replacing an EXE and its support tree is a
    two-object swap and is not crash-safe without an installer-level A/B shell.
    """

    root = _root_is_safe(install_root)
    candidate = candidate_bundle.absolute()
    if not candidate.is_relative_to(root) or candidate == root:
        raise LauncherMigrationError("LAUNCHER_CANDIDATE_OUTSIDE_INSTALL")
    manifest = launcher_tree_manifest(candidate)
    if expected_manifest is not None and manifest != expected_manifest:
        raise LauncherMigrationError("LAUNCHER_MANIFEST_MISMATCH")
    if any(
        str(row.get("name")) != LAUNCHER_EXECUTABLE_NAME
        and not str(row.get("name", "")).startswith(f"{LAUNCHER_INTERNAL_NAME}/")
        for row in manifest.get("files", [])
    ):
        raise LauncherMigrationError("LAUNCHER_BUNDLE_LAYOUT_INVALID")
    if not isinstance(transaction_id, str) or _TRANSACTION_RE.fullmatch(transaction_id) is None:
        raise LauncherMigrationError("LAUNCHER_TRANSACTION_INVALID")
    if not isinstance(payload_id, str) or _PAYLOAD_RE.fullmatch(payload_id) is None or not isinstance(source_commit, str) or _SOURCE_RE.fullmatch(source_commit) is None or payload_id != f"main-{source_commit[:12]}":
        raise LauncherMigrationError("LAUNCHER_IDENTITY_INVALID")
    if workflow_run_id is not None and (
        isinstance(workflow_run_id, bool)
        or not isinstance(workflow_run_id, int)
        or not 0 < workflow_run_id <= _MAX_WORKFLOW_RUN_ID
    ):
        raise LauncherMigrationError("LAUNCHER_WORKFLOW_RUN_INVALID")

    state_root = root / "update-state"
    backup = state_root / "launcher-rollback" / transaction_id
    activation = root / "staging" / "launcher-activation" / transaction_id
    if backup.exists() or activation.exists():
        raise LauncherMigrationError("LAUNCHER_TRANSACTION_OCCUPIED")
    old_exe = root / LAUNCHER_EXECUTABLE_NAME
    old_internal = root / LAUNCHER_INTERNAL_NAME
    old_manifest = _shell_manifest_path(root)
    previous_format = _bootable_format(root)
    if previous_format is None:
        raise LauncherMigrationError("LAUNCHER_CURRENT_INVALID")
    previous_shell_manifest = _attest_root_shell(root) if previous_format == "onedir" else None
    backup.mkdir(parents=True, exist_ok=False)
    activation.mkdir(parents=True, exist_ok=False)
    state_path = state_root / "launcher-activation.json"
    state: dict[str, Any] = {
        "schema_version": LAUNCHER_ACTIVATION_SCHEMA,
        "transaction_id": transaction_id,
        "payload_id": payload_id,
        "source_commit": source_commit,
        "format": "onedir",
        "executable_sha256": manifest["executable_sha256"],
        "tree_manifest_sha256": manifest["tree_manifest_sha256"],
        "file_count": manifest["file_count"],
        "total_bytes": manifest["total_bytes"],
        "workflow_run_id": workflow_run_id,
        "previous_format": previous_format,
        "previous_executable_sha256": _sha256(old_exe),
        "previous_tree_manifest_sha256": previous_shell_manifest.get("tree_manifest_sha256") if previous_shell_manifest else None,
        "previous_manifest_sha256": _sha256(old_manifest) if old_manifest.is_file() and not _is_reparse(old_manifest) else None,
        "previous_file_count": previous_shell_manifest.get("file_count") if previous_shell_manifest else None,
        "previous_total_bytes": previous_shell_manifest.get("total_bytes") if previous_shell_manifest else None,
        "transition_policy": "retain_verified_onedir" if previous_format == "onedir" else "legacy_copy_then_atomic_exe_switch",
        "retained_shell_identity": previous_shell_manifest if previous_shell_manifest else None,
        "status": "prepared",
    }
    try:
        _write_json(state_path, state)
        _hard_crash_checkpoint("after_transaction_journal")
    except Exception:
        # No shell move has happened yet.  If the durable transaction state
        # could not be published, remove only the two empty directories owned
        # by this transaction so a retry cannot be mistaken for a live move.
        if not state_path.exists():
            for empty in (activation, backup):
                try:
                    empty.rmdir()
                except OSError:
                    pass
        raise
    try:
        # Copying first means the candidate remains available if the root
        # shell swap or a later pointer activation fails.
        copied = copy_launcher_bundle(candidate, activation / LAUNCHER_BUNDLE_NAME)
        if expected_manifest is not None and copied != expected_manifest:
            raise LauncherMigrationError("LAUNCHER_TREE_CHANGED")
        state["status"] = "activation_copy_complete"
        _write_json(state_path, state)
        _hard_crash_checkpoint("after_candidate_copy")
        state["status"] = "candidate_internal_prepared"
        _write_json(state_path, state)
        _hard_crash_checkpoint("after_candidate_internal_preparation")

        if previous_format == "onedir":
            # Keep the currently verified shell in place.  A future installer
            # can make the shell itself A/B/versioned; this application-level
            # transaction must not attempt an unsafe EXE/_internal swap.
            _copy_regular(old_exe, backup / LAUNCHER_EXECUTABLE_NAME)
            _hard_crash_checkpoint("after_previous_exe_backup")
            state["status"] = "old_exe_moved"
            _write_json(state_path, state)
            current_internal_backup = backup / LAUNCHER_INTERNAL_NAME
            shutil.copytree(old_internal, current_internal_backup, symlinks=False)
            state["status"] = "old_internal_moved"
            _write_json(state_path, state)
            if old_manifest.is_file() and not _is_reparse(old_manifest):
                _copy_regular(old_manifest, backup / LAUNCHER_SHELL_MANIFEST_NAME)
            state["status"] = "old_manifest_moved"
            _write_json(state_path, state)
            _hard_crash_checkpoint("after_previous_internal_backup")
            state["status"] = "shell_retained"
            _write_json(state_path, state)
            return {
                **shell_identity(copied, payload_id, source_commit, workflow_run_id),
                "status": "shell_retained", "transaction_id": transaction_id, "shell_retained": True,
                "retained_shell_identity": previous_shell_manifest,
            }

        if _is_reparse(old_exe) or not old_exe.is_file():
            raise LauncherMigrationError("LAUNCHER_CURRENT_INVALID")
        # Compatibility status retained for pre-PR diagnostics; the boundary
        # is now a copy, never a move of the only root executable.
        _copy_regular(old_exe, backup / LAUNCHER_EXECUTABLE_NAME)
        state["status"] = "old_exe_moved"
        _write_json(state_path, state)
        _hard_crash_checkpoint("after_previous_exe_backup")
        _hard_crash_checkpoint("after_previous_internal_backup")

        staged = activation / LAUNCHER_BUNDLE_NAME
        if old_internal.exists() or old_internal.is_symlink():
            raise LauncherMigrationError("LAUNCHER_CURRENT_INVALID")
        # The directory rename is atomic and complete before it becomes the
        # root support tree.  The old one-file EXE remains bootable throughout.
        os.replace(staged / LAUNCHER_INTERNAL_NAME, root / LAUNCHER_INTERNAL_NAME)
        state["status"] = "candidate_internal_moved"
        _write_json(state_path, state)
        state["status"] = "candidate_internal_installed"
        _write_json(state_path, state)
        _hard_crash_checkpoint("after_candidate_internal_install")
        _hard_crash_checkpoint("before_final_exe_replacement")
        os.replace(staged / LAUNCHER_EXECUTABLE_NAME, root / LAUNCHER_EXECUTABLE_NAME)
        state["status"] = "candidate_exe_moved"
        _write_json(state_path, state)
        state["status"] = "candidate_exe_switched"
        _write_json(state_path, state)
        _hard_crash_checkpoint("after_final_exe_replacement")
        shell = shell_identity(copied, payload_id, source_commit, workflow_run_id)
        _hard_crash_checkpoint("before_shell_manifest_publication")
        _write_json(root / LAUNCHER_SHELL_MANIFEST_NAME, shell)
        _hard_crash_checkpoint("after_shell_manifest_publication")
        _verify_shell_manifest(root, copied)
        state["status"] = "shell_manifest_written"
        _write_json(state_path, state)
        state["status"] = "shell_switched"
        _write_json(state_path, state)
        return {**shell, "status": "shell_switched", "transaction_id": transaction_id}
    except Exception:
        try:
            restore_launcher_bundle(root, transaction_id=transaction_id, _allow_state_status=True)
        except Exception as rollback_error:
            raise LauncherMigrationError("LAUNCHER_ROLLBACK_FAILED") from rollback_error
        raise


def restore_launcher_bundle(root: Path, *, transaction_id: str, _allow_state_status: bool = False) -> dict[str, Any]:
    """Restore a shell exactly once and make later calls verified no-ops.

    The rollback directory is intentionally retained as forensic evidence.  A
    second watchdog retry must first recognize the verified previous shell and
    return ``already_restored``; it must never move that shell back into the
    forensic directory just because the transaction backup no longer contains
    the files that were already restored.
    """

    if not isinstance(transaction_id, str) or _TRANSACTION_RE.fullmatch(transaction_id) is None:
        raise LauncherMigrationError("LAUNCHER_TRANSACTION_INVALID")
    install = _root_is_safe(root)
    state_path = install / "update-state" / "launcher-activation.json"
    if not state_path.is_file() or state_path.is_symlink():
        raise LauncherMigrationError("LAUNCHER_STATE_INVALID")
    state = _current_activation_state(state_path, transaction_id)
    backup = install / "update-state" / "launcher-rollback" / transaction_id
    status = str(state.get("status") or "")
    previous_format = state.get("previous_format")
    if previous_format not in {"single_file", "onedir"}:
        # Transactions written by the first PR revision had no explicit
        # previous format.  Infer it only from the preserved backup, never
        # from a candidate/forensic directory.
        previous_format = "onedir" if (backup / LAUNCHER_INTERNAL_NAME).is_dir() else "single_file"
    previous_hash = state.get("previous_executable_sha256")
    if previous_hash is not None and (not isinstance(previous_hash, str) or re.fullmatch(r"[0-9a-f]{64}", previous_hash) is None):
        raise LauncherMigrationError("LAUNCHER_STATE_INVALID")
    previous_tree_hash = state.get("previous_tree_manifest_sha256")
    previous_manifest_hash = state.get("previous_manifest_sha256")
    previous_file_count = state.get("previous_file_count")
    previous_total_bytes = state.get("previous_total_bytes")
    if previous_tree_hash is not None and (not isinstance(previous_tree_hash, str) or _SHA256_RE.fullmatch(previous_tree_hash) is None):
        raise LauncherMigrationError("LAUNCHER_STATE_INVALID")
    if previous_manifest_hash is not None and (not isinstance(previous_manifest_hash, str) or _SHA256_RE.fullmatch(previous_manifest_hash) is None):
        raise LauncherMigrationError("LAUNCHER_STATE_INVALID")
    if previous_file_count is not None and (isinstance(previous_file_count, bool) or not isinstance(previous_file_count, int) or not 2 <= previous_file_count <= MAX_LAUNCHER_FILES):
        raise LauncherMigrationError("LAUNCHER_STATE_INVALID")
    if previous_total_bytes is not None and (isinstance(previous_total_bytes, bool) or not isinstance(previous_total_bytes, int) or not 1 <= previous_total_bytes <= MAX_LAUNCHER_BYTES):
        raise LauncherMigrationError("LAUNCHER_STATE_INVALID")

    transition_policy = state.get("transition_policy")

    def mark_restored_state() -> None:
        state["status"] = "restored"
        try:
            _write_json(state_path, state)
        except LauncherMigrationError:
            # The root shell has already been proved.  A later recovery pass
            # derives the same no-op from the bytes and transaction state.
            pass

    if transition_policy == "retain_verified_onedir":
        retained_identity = state.get("retained_shell_identity")
        try:
            current = _attest_root_shell(install)
            retained_ok = (
                previous_format == "onedir"
                and isinstance(retained_identity, dict)
                and current == retained_identity
                and current.get("tree_manifest_sha256") == previous_tree_hash
                and current.get("file_count") == previous_file_count
                and current.get("total_bytes") == previous_total_bytes
                and previous_hash is not None
                and _sha256(install / LAUNCHER_EXECUTABLE_NAME) == previous_hash
            )
        except LauncherMigrationError:
            retained_ok = False
        if not retained_ok:
            # A retained shell must remain the exact previously attested shell.
            # A manifest-only tamper can be repaired without a two-object
            # EXE/_internal swap; any support-tree mutation stays fail-closed.
            try:
                actual_tree = _shell_tree_manifest(install)
                bytes_match = (
                    previous_hash is not None
                    and _sha256(install / LAUNCHER_EXECUTABLE_NAME) == previous_hash
                    and actual_tree.get("tree_manifest_sha256") == previous_tree_hash
                    and actual_tree.get("file_count") == previous_file_count
                    and actual_tree.get("total_bytes") == previous_total_bytes
                )
                previous_manifest = backup / LAUNCHER_SHELL_MANIFEST_NAME
                if bytes_match and previous_manifest.is_file() and not _is_reparse(previous_manifest):
                    retained_manifest = json.loads(previous_manifest.read_text(encoding="utf-8"))
                    if not isinstance(retained_manifest, dict) or retained_manifest != retained_identity:
                        raise LauncherMigrationError("LAUNCHER_ROLLBACK_UNAVAILABLE")
                    temporary = install / f".{LAUNCHER_SHELL_MANIFEST_NAME}.{transaction_id}.restore"
                    shutil.copy2(previous_manifest, temporary)
                    os.replace(temporary, install / LAUNCHER_SHELL_MANIFEST_NAME)
                    if _attest_root_shell(install) != retained_identity:
                        raise LauncherMigrationError("LAUNCHER_ROLLBACK_UNAVAILABLE")
                    retained_ok = True
            except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, LauncherMigrationError):
                retained_ok = False
        if not retained_ok:
            raise LauncherMigrationError("LAUNCHER_ROLLBACK_UNAVAILABLE")
        if state.get("status") != "restored":
            # Retained-shell rollback has no byte move, but retain the durable
            # phase labels so older watchdogs and forensic readers can observe
            # the same monotonic recovery checkpoints.  A state-write fault
            # cannot make the already-attested root unbootable.
            for retained_status in ("previous__internal_moved", "previous_LocalAIHub_exe_moved"):
                state["status"] = retained_status
                try:
                    _write_json(state_path, state)
                except LauncherMigrationError:
                    pass
            mark_restored_state()
            return {"status": "restored", "transaction_id": transaction_id}
        return {"status": "already_restored", "transaction_id": transaction_id}

    if transition_policy == "legacy_copy_then_atomic_exe_switch":
        previous_exe = backup / LAUNCHER_EXECUTABLE_NAME
        if not previous_exe.is_file() or previous_exe.is_symlink() or _is_reparse(previous_exe):
            raise LauncherMigrationError("LAUNCHER_ROLLBACK_UNAVAILABLE")
        if previous_hash is None or _sha256(previous_exe) != previous_hash:
            raise LauncherMigrationError("LAUNCHER_ROLLBACK_FAILED")
        if state.get("status") != "restored":
            state["status"] = "rollback_in_progress"
            _write_json(state_path, state)
        try:
            already_previous = (
                (install / LAUNCHER_EXECUTABLE_NAME).is_file()
                and not _is_reparse(install / LAUNCHER_EXECUTABLE_NAME)
                and _sha256(install / LAUNCHER_EXECUTABLE_NAME) == previous_hash
                and not (install / LAUNCHER_INTERNAL_NAME).exists()
                and not (install / LAUNCHER_INTERNAL_NAME).is_symlink()
            )
        except LauncherMigrationError:
            already_previous = False
        if already_previous:
            if state.get("status") != "restored":
                candidate_source = install / "staging" / "launcher-activation" / transaction_id / LAUNCHER_BUNDLE_NAME
                if candidate_source.is_dir() and not candidate_source.is_symlink() and not _is_reparse(candidate_source):
                    forensic = _forensic_destination(backup)
                    shutil.copytree(candidate_source, forensic / LAUNCHER_BUNDLE_NAME, symlinks=False)
                mark_restored_state()
                return {"status": "restored", "transaction_id": transaction_id}
            return {"status": "already_restored", "transaction_id": transaction_id}

        failed = _forensic_destination(backup)

        def preserve_current(name: str) -> None:
            current = install / name
            if current.exists() or current.is_symlink():
                if _is_reparse(current):
                    raise LauncherMigrationError("LAUNCHER_ROLLBACK_FAILED")
                destination = failed / name
                if not destination.exists() and not destination.is_symlink():
                    move_known_copy_policy(current, destination)

        def move_known_copy_policy(source: Path, destination: Path) -> None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() or destination.is_symlink():
                raise LauncherMigrationError("LAUNCHER_DESTINATION_OCCUPIED")
            os.replace(source, destination)

        try:
            # Keep the current EXE live while its replacement is prepared.
            if (install / LAUNCHER_EXECUTABLE_NAME).is_file():
                current_bytes = (install / LAUNCHER_EXECUTABLE_NAME).read_bytes()
                (failed / LAUNCHER_EXECUTABLE_NAME).write_bytes(current_bytes)
            temporary = install / f".{LAUNCHER_EXECUTABLE_NAME}.{transaction_id}.rollback"
            shutil.copy2(previous_exe, temporary)
            _hard_crash_checkpoint("rollback_before_exe_replacement")
            os.replace(temporary, install / LAUNCHER_EXECUTABLE_NAME)
            _hard_crash_checkpoint("rollback_after_exe_replacement")
            state["status"] = "previous_LocalAIHub_exe_moved"
            _write_json(state_path, state)
            _hard_crash_checkpoint("rollback_after_previous_exe_restore")
            preserve_current(LAUNCHER_INTERNAL_NAME)
            state["status"] = "previous__internal_moved"
            _write_json(state_path, state)
            _hard_crash_checkpoint("rollback_after_previous_internal_restore")
            preserve_current(LAUNCHER_SHELL_MANIFEST_NAME)
            previous_manifest = backup / LAUNCHER_SHELL_MANIFEST_NAME
            if previous_manifest.is_file() and not _is_reparse(previous_manifest):
                shutil.copy2(previous_manifest, install / LAUNCHER_SHELL_MANIFEST_NAME)
            mark_restored_state()
            return {"status": "restored", "transaction_id": transaction_id}
        except (OSError, LauncherMigrationError) as exc:
            # A future recovery process can safely re-enter this branch.  The
            # root EXE is either the old copy or the candidate, never absent.
            if isinstance(exc, LauncherMigrationError):
                raise
            raise LauncherMigrationError("LAUNCHER_ROLLBACK_FAILED") from exc

    def previous_shell_is_verified() -> bool:
        if _bootable_format(install) != previous_format:
            return False
        if previous_hash is None:
            # Pre-correction transactions did not persist the previous shell
            # hash.  While its backup is still present, compare the live root
            # with that backup instead of treating any shell of the same
            # format as the already-restored shell.
            previous_backup_exe = backup / LAUNCHER_EXECUTABLE_NAME
            if previous_backup_exe.is_file() and not previous_backup_exe.is_symlink() and not _is_reparse(previous_backup_exe):
                try:
                    if _sha256(install / LAUNCHER_EXECUTABLE_NAME) != _sha256(previous_backup_exe):
                        return False
                    if previous_format == "onedir":
                        current = _shell_tree_manifest(install)
                        expected = _shell_tree_manifest(backup)
                        return current.get("tree_manifest_sha256") == expected.get("tree_manifest_sha256")
                    return True
                except LauncherMigrationError:
                    return False
            # The previous executable may already have been moved back before
            # an older transaction wrote its final state.  The format check at
            # the top of this helper is the remaining bounded proof in that
            # legacy state shape.
            live_status = str(state.get("status") or status)
            return live_status == "restored" or live_status.startswith("previous_")
        try:
            if _sha256(install / LAUNCHER_EXECUTABLE_NAME) != previous_hash:
                return False
            if previous_format == "onedir" and previous_tree_hash is not None:
                current = _shell_tree_manifest(install)
                if (
                    current.get("tree_manifest_sha256") != previous_tree_hash
                    or (previous_file_count is not None and current.get("file_count") != previous_file_count)
                    or (previous_total_bytes is not None and current.get("total_bytes") != previous_total_bytes)
                ):
                    return False
            if previous_format == "onedir":
                live_manifest = install / LAUNCHER_SHELL_MANIFEST_NAME
                if previous_manifest_hash is not None:
                    if (
                        not live_manifest.is_file()
                        or live_manifest.is_symlink()
                        or _is_reparse(live_manifest)
                        or _sha256(live_manifest) != previous_manifest_hash
                    ):
                        return False
                elif previous_manifest.is_file() and not _is_reparse(previous_manifest):
                    # Older activation states did not persist this hash.  If
                    # the preserved manifest is still in backup, a complete
                    # restore must put the same bytes back at the root.
                    if (
                        not live_manifest.is_file()
                        or live_manifest.is_symlink()
                        or _is_reparse(live_manifest)
                        or _sha256(live_manifest) != _sha256(previous_manifest)
                    ):
                        return False
            return True
        except LauncherMigrationError:
            return False

    def mark_restored() -> None:
        state["status"] = "restored"
        try:
            _write_json(state_path, state)
        except LauncherMigrationError:
            # The shell is already verified.  A later retry will use the
            # previous hash/format and still remain a no-op.
            pass

    def assert_transaction_ready() -> None:
        live = _current_activation_state(state_path, transaction_id)
        if live.get("status") == "restored":
            raise LauncherMigrationError("LAUNCHER_ALREADY_RESTORED")

    def move_known(source: Path, destination: Path) -> None:
        assert_transaction_ready()
        if _is_reparse(source) or _is_reparse(destination.parent):
            raise LauncherMigrationError("LAUNCHER_REPARSE")
        if destination.exists() or destination.is_symlink():
            raise LauncherMigrationError("LAUNCHER_DESTINATION_OCCUPIED")
        os.replace(source, destination)

    previous_exe = backup / LAUNCHER_EXECUTABLE_NAME
    previous_internal = backup / LAUNCHER_INTERNAL_NAME
    previous_manifest = backup / LAUNCHER_SHELL_MANIFEST_NAME

    def restore_partial_previous_onedir() -> bool:
        """Finish the narrow old-exe-only move before candidate activation.

        Onedir activation moves the previous executable before its internal
        tree.  A crash or state-write failure in that window leaves the
        previous internal tree at the root and the executable in the backup;
        requiring a complete backup here would reject a recoverable shell.
        The absence of a backup internal tree is used only for this exact
        partial shape; a candidate internal tree has a complete previous
        internal backup and follows the normal forensic path below.
        """

        if previous_format != "onedir":
            return False
        if not backup.is_dir() or backup.is_symlink() or _is_reparse(backup):
            return False
        current_exe = install / LAUNCHER_EXECUTABLE_NAME
        current_internal = install / LAUNCHER_INTERNAL_NAME
        if (
            previous_internal.exists() or previous_internal.is_symlink()
            or not previous_exe.is_file() or previous_exe.is_symlink() or _is_reparse(previous_exe)
            or current_exe.exists() or current_exe.is_symlink()
            or not current_internal.is_dir() or current_internal.is_symlink() or _is_reparse(current_internal)
        ):
            return False
        move_known(previous_exe, current_exe)
        if not previous_shell_is_verified():
            raise LauncherMigrationError("LAUNCHER_ROLLBACK_FAILED")
        state["status"] = f"previous_{LAUNCHER_EXECUTABLE_NAME.replace('.', '_')}_moved"
        try:
            _write_json(state_path, state)
        except LauncherMigrationError:
            pass
        return True

    # This check intentionally precedes any backup existence or move check.
    # It covers the second/third/post-crash watchdog call and the case where a
    # previous restore completed but its state write was interrupted.
    if previous_shell_is_verified():
        if status != "restored":
            mark_restored()
        return {"status": "already_restored", "transaction_id": transaction_id}

    if restore_partial_previous_onedir():
        mark_restored()
        return {"status": "restored", "transaction_id": transaction_id}

    if not backup.is_dir() or backup.is_symlink() or _is_reparse(backup):
        raise LauncherMigrationError("LAUNCHER_ROLLBACK_UNAVAILABLE")
    if (
        not previous_exe.is_file()
        or previous_exe.is_symlink()
        or _is_reparse(previous_exe)
        or (previous_format == "onedir" and (not previous_internal.is_dir() or previous_internal.is_symlink() or _is_reparse(previous_internal)))
        or (previous_format == "single_file" and (previous_internal.exists() or previous_internal.is_symlink()))
    ):
        # If the backup is incomplete but the root is not the exact previous
        # shell, moving anything would make an ambiguous state worse.
        raise LauncherMigrationError("LAUNCHER_ROLLBACK_UNAVAILABLE")
    if previous_hash is not None and _sha256(previous_exe) != previous_hash:
        raise LauncherMigrationError("LAUNCHER_ROLLBACK_FAILED")

    failed = _forensic_destination(backup)

    def reconcile_previous_shell() -> bool:
        """Finish a partially interrupted restore without broad cleanup."""

        try:
            # A state-write failure can happen after the final previous shell
            # component was moved successfully.  Verify that complete shell
            # first; moving any of its bytes into forensic storage would turn
            # a recoverable transaction into a launcher outage.
            if previous_shell_is_verified():
                return True
            # If the previous onedir internal tree was already restored but
            # its executable move/state write failed, it is the recovery
            # source, not a candidate byte set.  Keep it at root while the
            # preserved executable is returned below.
            previous_internal_at_root = (
                previous_format == "onedir"
                and not (previous_internal.exists() or previous_internal.is_symlink())
                and previous_exe.is_file()
                and not (install / LAUNCHER_EXECUTABLE_NAME).exists()
                and (install / LAUNCHER_INTERNAL_NAME).is_dir()
                and not (install / LAUNCHER_INTERNAL_NAME).is_symlink()
                and not _is_reparse(install / LAUNCHER_INTERNAL_NAME)
            )
            # Remove any remaining candidate component into the same unique
            # forensic directory before putting the preserved shell back.
            for name in (LAUNCHER_INTERNAL_NAME, LAUNCHER_EXECUTABLE_NAME, LAUNCHER_SHELL_MANIFEST_NAME):
                current = install / name
                if current.exists() or current.is_symlink():
                    if _is_reparse(current):
                        return False
                    if name == LAUNCHER_INTERNAL_NAME and previous_internal_at_root:
                        continue
                    target = failed / name
                    if not target.exists() and not target.is_symlink():
                        move_known(current, target)
            # Restore internal files before the executable so an onedir shell
            # is complete as soon as its root executable is switched back.
            for name in (LAUNCHER_INTERNAL_NAME, LAUNCHER_EXECUTABLE_NAME, LAUNCHER_SHELL_MANIFEST_NAME):
                source = backup / name
                if source.exists() or source.is_symlink():
                    if name == LAUNCHER_INTERNAL_NAME and previous_format != "onedir":
                        continue
                    move_known(source, install / name)
            return previous_shell_is_verified()
        except (OSError, LauncherMigrationError):
            return False

    try:
        state["status"] = "rollback_in_progress"
        _write_json(state_path, state)
        # Preserve the candidate first.  Each move is scoped to this exact
        # transaction and never overwrites an existing forensic byte set.
        for name in (LAUNCHER_INTERNAL_NAME, LAUNCHER_EXECUTABLE_NAME, LAUNCHER_SHELL_MANIFEST_NAME):
            current = install / name
            if current.exists() or current.is_symlink():
                if _is_reparse(current):
                    raise LauncherMigrationError("LAUNCHER_ROLLBACK_FAILED")
                move_known(current, failed / name)
                state["status"] = f"candidate_{name.replace('.', '_')}_moved"
                _write_json(state_path, state)
        for name in (LAUNCHER_INTERNAL_NAME, LAUNCHER_EXECUTABLE_NAME, LAUNCHER_SHELL_MANIFEST_NAME):
            previous = backup / name
            if previous.exists() or previous.is_symlink():
                if name == LAUNCHER_INTERNAL_NAME and previous_format != "onedir":
                    continue
                move_known(previous, install / name)
                state["status"] = f"previous_{name.replace('.', '_')}_moved"
                _write_json(state_path, state)
    except (OSError, LauncherMigrationError):
        if not reconcile_previous_shell():
            raise LauncherMigrationError("LAUNCHER_ROLLBACK_FAILED")
    if not previous_shell_is_verified():
        raise LauncherMigrationError("LAUNCHER_ROLLBACK_FAILED")
    mark_restored()
    return {"status": "restored", "transaction_id": transaction_id}


def reconcile_launcher_transaction(root: Path, *, transaction_id: str | None = None) -> dict[str, Any]:
    """Reconcile an interrupted shell journal from a fresh process.

    The normal activation path makes the root bootable before every durable
    phase.  This helper therefore never tries to manufacture a missing EXE
    from a forensic directory without first using the transaction's preserved
    copy; it only finalizes an exact candidate or invokes the idempotent,
    copy-backed rollback path.
    """

    install = _root_is_safe(root)
    state_path = install / "update-state" / "launcher-activation.json"
    if not state_path.is_file() or state_path.is_symlink():
        return {"status": "not_pending"}
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise LauncherMigrationError("LAUNCHER_STATE_INVALID")
    selected = transaction_id or raw.get("transaction_id")
    if not isinstance(selected, str):
        raise LauncherMigrationError("LAUNCHER_TRANSACTION_INVALID")
    state = _current_activation_state(state_path, selected)
    policy = state.get("transition_policy")
    if state.get("status") == "rollback_in_progress":
        result = restore_launcher_bundle(install, transaction_id=selected, _allow_state_status=True)
        return {"status": result.get("status", "restored"), "transaction_id": selected}
    if state.get("status") in {"prepared", "activation_copy_complete", "candidate_internal_prepared", "old_exe_moved"} and policy == "legacy_copy_then_atomic_exe_switch":
        try:
            root_exe = install / LAUNCHER_EXECUTABLE_NAME
            root_internal = install / LAUNCHER_INTERNAL_NAME
            if root_exe.is_file() and not root_internal.exists() and _sha256(root_exe) == state.get("previous_executable_sha256"):
                backup = install / "update-state" / "launcher-rollback" / selected
                activation = install / "staging" / "launcher-activation" / selected
                if backup.is_dir() and not _is_reparse(backup):
                    shutil.rmtree(backup)
                if activation.is_dir() and not _is_reparse(activation):
                    shutil.rmtree(activation)
                state_path.unlink(missing_ok=True)
                return {"status": "retry_activation", "transaction_id": selected}
        except (OSError, LauncherMigrationError):
            raise LauncherMigrationError("LAUNCHER_RECOVERY_REQUIRED")
    if state.get("status") in {"restored", "shell_switched", "shell_retained"}:
        if state.get("status") == "restored":
            return {"status": "already_reconciled", "transaction_id": selected, "shell_restored": True}
        try:
            _attest_root_shell(install)
            if state.get("status") == "shell_retained":
                retained_identity = state.get("retained_shell_identity")
                if not isinstance(retained_identity, dict) or _attest_root_shell(install) != retained_identity:
                    raise LauncherMigrationError("LAUNCHER_RETAINED_SHELL_MISMATCH")
                return {
                    "status": "shell_retained", "transaction_id": selected,
                    "retained_shell_identity": state.get("retained_shell_identity"),
                }
            return {"status": "already_reconciled", "transaction_id": selected}
        except LauncherMigrationError:
            if policy == "retain_verified_onedir":
                raise

    if policy == "retain_verified_onedir":
        retained_identity = state.get("retained_shell_identity")
        if not isinstance(retained_identity, dict) or _attest_root_shell(install) != retained_identity:
            raise LauncherMigrationError("LAUNCHER_RETAINED_SHELL_MISMATCH")
        return {
            "status": "shell_retained", "transaction_id": selected,
            "retained_shell_identity": retained_identity,
        }

    if policy != "legacy_copy_then_atomic_exe_switch":
        raise LauncherMigrationError("LAUNCHER_STATE_INVALID")
    candidate_manifest = {
        "schema_version": LAUNCHER_MANIFEST_SCHEMA,
        "format": "onedir",
        "executable": LAUNCHER_EXECUTABLE_NAME,
        "executable_sha256": state["executable_sha256"],
        "tree_manifest_sha256": state["tree_manifest_sha256"],
        "file_count": state["file_count"],
        "total_bytes": state["total_bytes"],
    }
    try:
        actual = _shell_tree_manifest(install)
        candidate_exact = all(actual.get(key) == value for key, value in candidate_manifest.items() if key != "schema_version")
    except LauncherMigrationError:
        candidate_exact = False
    if candidate_exact:
        shell = shell_identity(candidate_manifest, str(state["payload_id"]), str(state["source_commit"]), state.get("workflow_run_id"))
        try:
            current_manifest = _load_shell_manifest(install)
        except LauncherMigrationError:
            _write_json(_shell_manifest_path(install), shell)
        else:
            if current_manifest != shell:
                _write_json(_shell_manifest_path(install), shell)
        _attest_root_shell(install)
        state["status"] = "shell_switched"
        _write_json(state_path, state)
        return {"status": "shell_switched", "transaction_id": selected}

    # A previous one-file EXE with a candidate internal tree is a safe
    # transitional state; an incomplete candidate is also safe to roll back.
    result = restore_launcher_bundle(install, transaction_id=selected, _allow_state_status=True)
    return {"status": "restored" if result.get("status") == "restored" else "already_restored", "transaction_id": selected}


def launcher_projection(root: Path) -> dict[str, Any]:
    """Return a path-free, fail-closed shell status for the updater API."""

    install = root.absolute()
    exe = install / LAUNCHER_EXECUTABLE_NAME
    internal = install / LAUNCHER_INTERNAL_NAME
    try:
        if exe.is_file() and not _is_reparse(exe) and internal.is_dir() and not _is_reparse(internal):
            # Do not reuse a size/mtime-only attestation.  A manifest or
            # support file can be replaced while those metadata values remain
            # unchanged on some filesystems; integrity admission must re-read
            # the complete shell bytes every time.
            manifest = _attest_root_shell(install)
            value: dict[str, Any] = {
                "status": "verified", "format": "onedir", "migration_required": False,
                "repair_required": False,
                "payload_id": manifest["payload_id"], "source_commit": manifest["source_commit"],
                "workflow_run_id": manifest.get("workflow_run_id"),
                "executable_sha256": manifest["executable_sha256"],
                "tree_manifest_sha256": manifest["tree_manifest_sha256"],
                "file_count": manifest["file_count"], "total_bytes": manifest["total_bytes"],
            }
            return value
        if exe.is_file() and not _is_reparse(exe):
            if internal.exists() or internal.is_symlink():
                return {"status": "present_unverified", "format": "onedir", "migration_required": True, "repair_required": True}
            return {"status": "legacy", "format": "single_file", "migration_required": True, "repair_required": False}
    except (OSError, LauncherMigrationError):
        if exe.is_file() and not _is_reparse(exe) and (internal.exists() or internal.is_symlink()):
            return {"status": "present_unverified", "format": "onedir", "migration_required": True, "repair_required": True}
    return {"status": "unavailable", "format": "unknown", "migration_required": True, "repair_required": True}


__all__ = [
    "LAUNCHER_ACTIVATION_SCHEMA", "LAUNCHER_BUNDLE_NAME", "LAUNCHER_EXECUTABLE_NAME",
    "LAUNCHER_INTERNAL_NAME", "LAUNCHER_MANIFEST_SCHEMA", "LauncherMigrationError",
    "activate_launcher_bundle", "copy_launcher_bundle", "launcher_projection", "reconcile_launcher_transaction", "shell_identity",
    "launcher_tree_manifest", "restore_launcher_bundle", "verify_launcher_manifest",
]
