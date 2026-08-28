"""Stage a bounded V8.0.1 installed-product candidate.

This builder is intentionally separate from the historical V7 source archive
builder.  It creates an application payload below ``versions/<version>`` and
an immutable launcher/product shell at the install root.  It never writes the
machine's production installation, shortcuts, user data or a Git checkout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.app.stable_shell import (
    APP_USER_MODEL_ID,
    ICON_NAME,
    INSTALLATION_SCHEMA,
    POINTER_SCHEMA,
    PRODUCT_ID,
    PRODUCT_SCHEMA,
    VERSION_MANIFEST_SCHEMA,
    atomic_activate_pointer,
)
from src.shared.version import PRODUCT_VERSION


MAX_FILES = 100_000
MAX_RUNTIME_FILES = 50_000
MAX_RUNTIME_BYTES = 1_000_000_000
CORE_RUNTIME_MANIFEST_SCHEMA = "v8.0.1-core-runtime.v1"
BUILD_INFO_SCHEMA = "local-ai-hub-build-info.v1"
RUNTIME_EXCLUDED_PREFIXES = ("Lib/site-packages/bin/",)
# The manifest is regenerated from the copied leaf inventory below.  Keeping
# the source runtime's stale manifest in the destination would occupy the
# canonical output before ``_write_new_json`` can publish the new one.
RUNTIME_EXCLUDED_FILES = frozenset({"runtime-manifest.json"})
SOURCE_PREFIXES = (
    "src/", "scripts/", "architecture/", "workflows/", "extensions/",
    "asset_catalog/", "creative_recipes/", "Hub/", "MCP/", "Services/",
    "Adapters/", "privacy_diagnostics/", "workflow_packages/",
)
SOURCE_ROOT_FILES = {
    "AGENTS.md", "README.md", "LICENSES.md", "requirements-hub.txt",
    "dependencies.lock.json",
}


class StableProductBuildError(ValueError):
    """Fixed-code refusal from the candidate staging boundary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _reject_reparse(path: Path, code: str) -> None:
    try:
        if stat.S_ISLNK(path.lstat().st_mode):
            raise StableProductBuildError(code)
    except FileNotFoundError:
        return
    except OSError:
        raise StableProductBuildError(code) from None
    if os.name == "nt":
        try:
            import ctypes

            attributes = int(ctypes.windll.kernel32.GetFileAttributesW(str(path))) & 0xFFFFFFFF
            if attributes != 0xFFFFFFFF and attributes & 0x400:
                raise StableProductBuildError(code)
        except AttributeError:
            pass
        except OSError:
            raise StableProductBuildError(code) from None


def _ensure_task_root(path: Path, *, allow_test_root: bool = False) -> Path:
    root = path.absolute()
    if root == ROOT.absolute() or (root / ".git").exists() or any(part.casefold() == ".git" for part in root.parts):
        raise StableProductBuildError("OUTPUT_ROOT_REJECTED")
    if not allow_test_root and not any(part.casefold() == "temp" for part in root.parts):
        raise StableProductBuildError("OUTPUT_ROOT_MUST_BE_TASK_TEMP")
    _reject_reparse(root.parent, "OUTPUT_PARENT_REPARSE")
    if root.exists() or root.is_symlink():
        _reject_reparse(root, "OUTPUT_ROOT_REPARSE")
    return root


def _copy_regular(source: Path, destination: Path) -> None:
    _reject_reparse(source, "SOURCE_REPARSE")
    if not source.is_file():
        raise StableProductBuildError("SOURCE_FILE_MISSING")
    destination.parent.mkdir(parents=True, exist_ok=True)
    _reject_reparse(destination.parent, "DESTINATION_REPARSE")
    if destination.exists() or destination.is_symlink():
        raise StableProductBuildError("DESTINATION_OCCUPIED")
    shutil.copy2(source, destination)


def _copy_runtime_tree(source_root: Path, destination_root: Path) -> list[dict[str, Any]]:
    """Copy a complete reviewed runtime tree without following reparse entries."""

    source = source_root.absolute()
    if not source.is_dir() or source.is_symlink():
        raise StableProductBuildError("BUNDLED_RUNTIME_ROOT_REQUIRED")
    _reject_reparse(source, "RUNTIME_REPARSE")
    destination_root.mkdir(parents=True, exist_ok=False)
    records: list[dict[str, Any]] = []
    total_bytes = 0
    for current, directories, filenames in os.walk(source, topdown=True, followlinks=False):
        current_path = Path(current)
        _reject_reparse(current_path, "RUNTIME_REPARSE")
        safe_directories: list[str] = []
        for name in sorted(directories):
            candidate = current_path / name
            _reject_reparse(candidate, "RUNTIME_REPARSE")
            if not candidate.is_dir():
                raise StableProductBuildError("RUNTIME_DIRECTORY_INVALID")
            safe_directories.append(name)
        directories[:] = safe_directories
        for name in sorted(filenames):
            source_file = current_path / name
            _reject_reparse(source_file, "RUNTIME_REPARSE")
            if not source_file.is_file():
                raise StableProductBuildError("RUNTIME_FILE_INVALID")
            relative = source_file.relative_to(source).as_posix()
            if relative.casefold() in {item.casefold() for item in RUNTIME_EXCLUDED_FILES} or any(relative.startswith(prefix) for prefix in RUNTIME_EXCLUDED_PREFIXES):
                continue
            size = source_file.stat().st_size
            total_bytes += size
            if len(records) >= MAX_RUNTIME_FILES or total_bytes > MAX_RUNTIME_BYTES:
                raise StableProductBuildError("RUNTIME_BOUNDS_EXCEEDED")
            destination = destination_root / Path(relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            _reject_reparse(destination.parent, "DESTINATION_REPARSE")
            _copy_regular(source_file, destination)
            records.append({"name": relative, "size": size, "sha256": _sha256(destination)})
    if not any(record["name"].casefold() == "pythonw.exe" for record in records):
        raise StableProductBuildError("BUNDLED_RUNTIME_REQUIRED")
    return records


def _git_files(source_root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(source_root), "ls-files", "-z"],
        check=False,
        capture_output=True,
        timeout=20,
    )
    if result.returncode != 0:
        raise StableProductBuildError("SOURCE_FILE_LIST_UNAVAILABLE")
    names = [item.decode("utf-8") for item in result.stdout.split(b"\0") if item]
    selected = [
        name for name in names
        if name in SOURCE_ROOT_FILES
        or name.startswith(SOURCE_PREFIXES)
        or (name.startswith("Config/") and name.endswith(".example.json"))
    ]
    if not selected or len(selected) > MAX_FILES:
        raise StableProductBuildError("SOURCE_SELECTION_INVALID")
    return sorted(selected)


def _exact_source_commit(source_root: Path, requested: str | None) -> str | None:
    """Return a source identity only when it is this checkout's exact HEAD.

    A side-by-side candidate is useful for testing the post-update readiness
    contract only when its payload identity proves the source bytes that were
    staged.  Never accept a caller-supplied SHA merely as a label: this
    builder already requires a Git-backed source tree, so bind the optional
    identity to that tree's current commit before writing ``build.json``.
    """

    if requested is None:
        return None
    if not isinstance(requested, str) or not re.fullmatch(r"[0-9a-f]{40}", requested):
        raise StableProductBuildError("SOURCE_COMMIT_INVALID")
    result = subprocess.run(
        ["git", "-C", str(source_root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    actual = result.stdout.strip()
    if result.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", actual):
        raise StableProductBuildError("SOURCE_COMMIT_UNAVAILABLE")
    if actual != requested:
        raise StableProductBuildError("SOURCE_COMMIT_MISMATCH")
    return actual


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _write_new_json(path: Path, value: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise StableProductBuildError("MANIFEST_OUTPUT_OCCUPIED")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise StableProductBuildError("MANIFEST_TEMPORARY_OCCUPIED")
    try:
        with temporary.open("xb") as handle:
            handle.write(_canonical(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except (OSError, ValueError):
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise StableProductBuildError("MANIFEST_WRITE_FAILED") from None


def stage_product(
    output_root: Path,
    *,
    runtime_pythonw: Path,
    runtime_root: Path | None = None,
    runtime_metadata: Path | None = None,
    launcher: Path,
    data_root: Path,
    source_root: Path = ROOT,
    version: str = PRODUCT_VERSION,
    source_commit: str | None = None,
    allow_test_root: bool = False,
) -> dict[str, Any]:
    source = source_root.absolute()
    exact_commit = _exact_source_commit(source, source_commit)
    if exact_commit is not None:
        if version != PRODUCT_VERSION:
            raise StableProductBuildError("VERSION_SOURCE_CONFLICT")
        version = f"main-{exact_commit[:12]}"
    elif version != "8.0.1":
        raise StableProductBuildError("VERSION_NOT_REVIEWED")
    output = _ensure_task_root(output_root, allow_test_root=allow_test_root)
    if output.exists() and any(output.iterdir()):
        raise StableProductBuildError("OUTPUT_ROOT_OCCUPIED")
    runtime = runtime_pythonw.absolute()
    launcher = launcher.absolute()
    data = data_root.absolute()
    _reject_reparse(data.parent, "DATA_PARENT_REPARSE")
    if data.exists() or data.is_symlink():
        _reject_reparse(data, "DATA_ROOT_REPARSE")
    _reject_reparse(runtime, "RUNTIME_REPARSE")
    _reject_reparse(launcher, "LAUNCHER_REPARSE")
    if runtime.name.casefold() != "pythonw.exe" or not runtime.is_file():
        raise StableProductBuildError("BUNDLED_RUNTIME_REQUIRED")
    if launcher.name.casefold() != "localaihub.exe" or not launcher.is_file():
        raise StableProductBuildError("STABLE_LAUNCHER_REQUIRED")
    if data == output:
        raise StableProductBuildError("APP_DATA_ROOT_MUST_DIFFER")

    payload = output / "versions" / version
    app_payload = payload / "app"
    runtime_payload_root = payload / "runtime" / "Python312"
    runtime_payload = runtime_payload_root / "pythonw.exe"
    payload.mkdir(parents=True, exist_ok=False)
    for relative in _git_files(source):
        _copy_regular(source / relative, app_payload / relative)
    if runtime_root is None:
        _copy_regular(runtime, runtime_payload)
        runtime_inventory = [{"name": "pythonw.exe", "size": runtime_payload.stat().st_size, "sha256": _sha256(runtime_payload)}]
    else:
        runtime_inventory = _copy_runtime_tree(runtime_root, runtime_payload_root)
        if not runtime_payload.is_file():
            raise StableProductBuildError("BUNDLED_RUNTIME_REQUIRED")
    _copy_regular(launcher, output / "LocalAIHub.exe")
    icon = source / "distribution" / "assets" / ICON_NAME
    _copy_regular(icon, output / ICON_NAME)

    runtime_metadata_value: dict[str, Any] = {
        "schema_version": CORE_RUNTIME_MANIFEST_SCHEMA,
        "provider": "python.org",
        "source_url": "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip",
        "version": "3.12.10",
        "platform": "win-amd64",
        "license": "PSF-2.0",
        "archive": "python-3.12.10-embed-amd64.zip",
        "archive_size": 11133606,
        "archive_sha256": "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3",
        "files": runtime_inventory,
    }
    if runtime_metadata is not None:
        try:
            loaded_metadata = json.loads(runtime_metadata.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise StableProductBuildError("RUNTIME_METADATA_INVALID") from None
        if not isinstance(loaded_metadata, dict) or loaded_metadata.get("schema_version") != CORE_RUNTIME_MANIFEST_SCHEMA:
            raise StableProductBuildError("RUNTIME_METADATA_INVALID")
        runtime_metadata_value = loaded_metadata | {"files": runtime_inventory}
    _write_new_json(runtime_payload_root / "runtime-manifest.json", runtime_metadata_value)
    manifest = {
        "schema_version": VERSION_MANIFEST_SCHEMA,
        "product_id": PRODUCT_ID,
        "version": version,
        "app_relative": "app",
        "runtime_relative": "runtime/Python312/pythonw.exe",
        "entrypoint": "src.app.launcher",
    }
    manifest_path = payload / "manifest.json"
    _write_new_json(manifest_path, manifest)
    if exact_commit is not None:
        _write_new_json(payload / "build.json", {
            "schema_version": BUILD_INFO_SCHEMA,
            "source_commit": exact_commit,
            "workflow_run_id": "local-candidate",
            "channel": "candidate",
            "product_version": PRODUCT_VERSION,
        })
    product = {
        "schema_version": PRODUCT_SCHEMA,
        "product_id": PRODUCT_ID,
        # The product remains the reviewed 8.0.1 shell; only the payload
        # pointer carries a ``main-<sha>`` candidate identity.  A local
        # exact-head smoke must never masquerade as a product-version bump.
        "version": PRODUCT_VERSION,
        "launcher": "LocalAIHub.exe",
        "icon": ICON_NAME,
        "current_pointer": "current.json",
    }
    installation = {
        "schema_version": INSTALLATION_SCHEMA,
        "product_id": PRODUCT_ID,
        "app_root": str(output),
        "data_root": str(data),
        "app_user_model_id": APP_USER_MODEL_ID,
        "launcher": "LocalAIHub.exe",
    }
    _write_new_json(output / "product.json", product)
    _write_new_json(output / "installation.json", installation)
    pointer = atomic_activate_pointer(output, version=version, manifest_sha256=_sha256(manifest_path))
    inventory = []
    for path in sorted(output.rglob("*")):
        if path.is_file() and not path.is_symlink():
            inventory.append({"name": path.relative_to(output).as_posix(), "size": path.stat().st_size, "sha256": _sha256(path)})
    return {
        "schema_version": "v8.0.1-stable-product-candidate.v1",
        "product_id": PRODUCT_ID,
        "version": version,
        "source_commit": exact_commit,
        "identity": "exact_source_head" if exact_commit is not None else "legacy_stable_product",
        "pointer": pointer,
        "file_count": len(inventory),
        "inventory_sha256": hashlib.sha256(_canonical({"files": inventory})).hexdigest(),
        "runtime": runtime_metadata_value,
        "execution": "not_run",
        "dry_run": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage a task-owned stable Local AI Hub product candidate.")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--runtime-pythonw", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--runtime-metadata", type=Path)
    parser.add_argument("--launcher", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--source-commit", help="Exact lowercase Git HEAD to identity-bind a side-by-side candidate.")
    args = parser.parse_args()
    try:
        print(json.dumps(stage_product(args.output_root, runtime_pythonw=args.runtime_pythonw, runtime_root=args.runtime_root, runtime_metadata=args.runtime_metadata, launcher=args.launcher, data_root=args.data_root, source_root=args.source_root, source_commit=args.source_commit), sort_keys=True, indent=2))
        return 0
    except StableProductBuildError as exc:
        print(json.dumps({"status": "blocked", "code": exc.code, "execution": "not_run", "dry_run": True}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["StableProductBuildError", "stage_product"]
