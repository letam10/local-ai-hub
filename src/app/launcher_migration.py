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
from typing import Any


LAUNCHER_MANIFEST_SCHEMA = "local-ai-hub-launcher-bundle.v1"
LAUNCHER_ACTIVATION_SCHEMA = "local-ai-hub-launcher-activation.v1"
LAUNCHER_SHELL_MANIFEST_NAME = "launcher-manifest.json"
LAUNCHER_EXECUTABLE_NAME = "LocalAIHub.exe"
LAUNCHER_BUNDLE_NAME = "LocalAIHub"
LAUNCHER_INTERNAL_NAME = "_internal"
MAX_LAUNCHER_FILES = 10_000
MAX_LAUNCHER_BYTES = 500 * 1024 * 1024


class LauncherMigrationError(ValueError):
    """Fixed-code refusal from the launcher-shell transaction boundary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


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


def activate_launcher_bundle(
    install_root: Path,
    candidate_bundle: Path,
    *,
    transaction_id: str,
    payload_id: str,
    source_commit: str,
) -> dict[str, Any]:
    """Switch the root shell after the old owner has exited.

    The old shell is moved to an opaque transaction backup before the new
    shell is moved into place.  Any failure restores that backup immediately;
    the watchdog can call :func:`restore_launcher_bundle` again after a crash.
    """

    root = _root_is_safe(install_root)
    candidate = candidate_bundle.absolute()
    if not candidate.is_relative_to(root) or candidate == root:
        raise LauncherMigrationError("LAUNCHER_CANDIDATE_OUTSIDE_INSTALL")
    manifest = launcher_tree_manifest(candidate)
    if not isinstance(transaction_id, str) or not transaction_id.startswith("txn-"):
        raise LauncherMigrationError("LAUNCHER_TRANSACTION_INVALID")
    if not isinstance(payload_id, str) or not payload_id or not isinstance(source_commit, str) or len(source_commit) != 40:
        raise LauncherMigrationError("LAUNCHER_IDENTITY_INVALID")

    state_root = root / "update-state"
    backup = state_root / "launcher-rollback" / transaction_id
    activation = root / "staging" / "launcher-activation" / transaction_id
    if backup.exists() or activation.exists():
        raise LauncherMigrationError("LAUNCHER_TRANSACTION_OCCUPIED")
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
        "status": "prepared",
    }
    _write_json(state_path, state)
    try:
        # Copying first means the candidate remains available if the root
        # shell swap or a later pointer activation fails.
        copied = copy_launcher_bundle(candidate, activation / LAUNCHER_BUNDLE_NAME)
        state["status"] = "switching"
        _write_json(state_path, state)

        old_exe = root / LAUNCHER_EXECUTABLE_NAME
        old_internal = root / LAUNCHER_INTERNAL_NAME
        old_manifest = _shell_manifest_path(root)
        if old_exe.exists() or old_exe.is_symlink():
            if _is_reparse(old_exe) or not old_exe.is_file():
                raise LauncherMigrationError("LAUNCHER_CURRENT_INVALID")
            os.replace(old_exe, backup / LAUNCHER_EXECUTABLE_NAME)
        if old_internal.exists() or old_internal.is_symlink():
            if _is_reparse(old_internal) or not old_internal.is_dir():
                raise LauncherMigrationError("LAUNCHER_CURRENT_INVALID")
            os.replace(old_internal, backup / LAUNCHER_INTERNAL_NAME)
        if old_manifest.exists() or old_manifest.is_symlink():
            if _is_reparse(old_manifest) or not old_manifest.is_file():
                raise LauncherMigrationError("LAUNCHER_CURRENT_INVALID")
            os.replace(old_manifest, backup / LAUNCHER_SHELL_MANIFEST_NAME)

        staged = activation / LAUNCHER_BUNDLE_NAME
        os.replace(staged / LAUNCHER_EXECUTABLE_NAME, root / LAUNCHER_EXECUTABLE_NAME)
        os.replace(staged / LAUNCHER_INTERNAL_NAME, root / LAUNCHER_INTERNAL_NAME)
        shell = {
            "schema_version": LAUNCHER_MANIFEST_SCHEMA,
            "product_id": "LocalAIHub",
            "payload_id": payload_id,
            "source_commit": source_commit,
            "format": "onedir",
            "executable_sha256": copied["executable_sha256"],
            "tree_manifest_sha256": copied["tree_manifest_sha256"],
            "file_count": copied["file_count"],
            "total_bytes": copied["total_bytes"],
        }
        _write_json(root / LAUNCHER_SHELL_MANIFEST_NAME, shell)
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
    """Restore the shell saved by a previous activation transaction."""

    install = _root_is_safe(root)
    backup = install / "update-state" / "launcher-rollback" / transaction_id
    state_path = install / "update-state" / "launcher-activation.json"
    if not backup.is_dir() or backup.is_symlink() or _is_reparse(backup):
        raise LauncherMigrationError("LAUNCHER_ROLLBACK_UNAVAILABLE")
    try:
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        state = {}
    if not _allow_state_status and state.get("transaction_id") not in {None, transaction_id}:
        raise LauncherMigrationError("LAUNCHER_TRANSACTION_MISMATCH")

    # Preserve the currently selected candidate shell for forensic review;
    # never delete it while rolling back.
    failed = backup / "candidate-shell"
    failed.mkdir(parents=True, exist_ok=True)
    for name in (LAUNCHER_EXECUTABLE_NAME, LAUNCHER_INTERNAL_NAME, LAUNCHER_SHELL_MANIFEST_NAME):
        current = install / name
        if current.exists() or current.is_symlink():
            if _is_reparse(current):
                raise LauncherMigrationError("LAUNCHER_ROLLBACK_FAILED")
            os.replace(current, failed / name)
    for name in (LAUNCHER_EXECUTABLE_NAME, LAUNCHER_INTERNAL_NAME, LAUNCHER_SHELL_MANIFEST_NAME):
        previous = backup / name
        if previous.exists() or previous.is_symlink():
            os.replace(previous, install / name)
    if state_path.parent.exists():
        state["status"] = "restored"
        _write_json(state_path, state if isinstance(state, dict) else {"status": "restored", "transaction_id": transaction_id})
    return {"status": "restored", "transaction_id": transaction_id}


def launcher_projection(root: Path) -> dict[str, Any]:
    """Return a path-free shell status suitable for the updater API."""

    install = root.absolute()
    exe = install / LAUNCHER_EXECUTABLE_NAME
    internal = install / LAUNCHER_INTERNAL_NAME
    try:
        if exe.is_file() and not _is_reparse(exe) and internal.is_dir() and not _is_reparse(internal):
            value: dict[str, Any] = {"status": "verified", "format": "onedir", "migration_required": False}
            manifest_path = _shell_manifest_path(install)
            if manifest_path.is_file() and not _is_reparse(manifest_path):
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    if isinstance(manifest, dict) and manifest.get("format") == "onedir":
                        value.update({
                            "executable_sha256": manifest.get("executable_sha256"),
                            "tree_manifest_sha256": manifest.get("tree_manifest_sha256"),
                            "file_count": manifest.get("file_count"),
                            "total_bytes": manifest.get("total_bytes"),
                        })
                except (OSError, UnicodeError, json.JSONDecodeError):
                    value["status"] = "unverified"
            return value
        if exe.is_file() and not _is_reparse(exe):
            return {"status": "legacy", "format": "single_file", "migration_required": True}
    except OSError:
        pass
    return {"status": "unavailable", "format": "unknown", "migration_required": True}


__all__ = [
    "LAUNCHER_ACTIVATION_SCHEMA", "LAUNCHER_BUNDLE_NAME", "LAUNCHER_EXECUTABLE_NAME",
    "LAUNCHER_INTERNAL_NAME", "LAUNCHER_MANIFEST_SCHEMA", "LauncherMigrationError",
    "activate_launcher_bundle", "copy_launcher_bundle", "launcher_projection",
    "launcher_tree_manifest", "restore_launcher_bundle", "verify_launcher_manifest",
]
