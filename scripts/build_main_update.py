#!/usr/bin/env python3
"""Build deterministic application or composite product update artifacts.

``APP_ONLY`` retains its historical meaning: it contains tracked repository
files under ``app/`` and the installed updater reuses the reviewed runtime.
``APP_AND_LAUNCHER`` is an explicit composite contract carrying one exact
PyInstaller onedir launcher tree bound to the same source commit/workflow run.
Models, Environments, Output, machine Config and other ignored state are never
packaged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.shared.version import PRODUCT_VERSION
from src.shared.runtime_identity import API_PROTOCOL_VERSION

SCHEMA = "local-ai-hub-main-update.v1"
UPDATE_CONTRACT_SCHEMA = "local-ai-hub-update-contract.v1"
UPDATE_CONTRACT_NAME = "update-contract.json"
PRODUCT_ID = "LocalAIHub"
ARCHIVE_NAME = "LocalAIHub-main-update.zip"
MAX_FILES = 12_000
MAX_RUNTIME_FILES = 50_000
MAX_RUNTIME_BYTES = 1_000_000_000
COMPOSITE_SCHEMA = "local-ai-hub-composite-update.v1"
MAX_LAUNCHER_FILES = 10_000
MAX_LAUNCHER_BYTES = 500 * 1024 * 1024


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def tracked_files(root: Path) -> list[Path]:
    raw = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True).stdout
    paths = [Path(item.decode("utf-8")) for item in raw.split(b"\0") if item]
    files = [item for item in paths if (root / item).is_file()]
    if not files or len(files) > MAX_FILES:
        raise RuntimeError("tracked_file_count_invalid")
    return sorted(files, key=lambda item: item.as_posix())


def runtime_files(runtime_root: Path) -> list[tuple[Path, str, int, str]]:
    root = runtime_root.absolute()
    if not root.is_dir() or root.is_symlink():
        raise RuntimeError("runtime_root_invalid")
    rows: list[tuple[Path, str, int, str]] = []
    total = 0
    for current, directories, filenames in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        directories[:] = sorted(directories)
        for name in directories + filenames:
            candidate = current_path / name
            if candidate.is_symlink() or stat.S_ISLNK(candidate.lstat().st_mode):
                raise RuntimeError("runtime_reparse_rejected")
        for name in sorted(filenames):
            source = current_path / name
            relative = source.relative_to(root).as_posix()
            size = source.stat().st_size
            total += size
            if len(rows) >= MAX_RUNTIME_FILES or total > MAX_RUNTIME_BYTES:
                raise RuntimeError("runtime_bounds_exceeded")
            rows.append((source, relative, size, sha256(source)))
    if not rows or not any(relative.casefold().endswith("pythonw.exe") for _source, relative, _size, _digest in rows):
        raise RuntimeError("runtime_python_required")
    return sorted(rows, key=lambda row: row[1])


def launcher_files(launcher_bundle: Path) -> list[tuple[Path, str, int, str]]:
    """Return a complete bounded, no-reparse onedir launcher inventory."""

    root = launcher_bundle.absolute()
    if not root.is_dir() or root.is_symlink() or root.name.casefold() != "localaihub":
        raise RuntimeError("launcher_bundle_invalid")
    executable = root / "LocalAIHub.exe"
    internal = root / "_internal"
    if not executable.is_file() or executable.is_symlink() or not internal.is_dir() or internal.is_symlink():
        raise RuntimeError("launcher_bundle_incomplete")
    rows: list[tuple[Path, str, int, str]] = []
    total = 0
    for current, directories, filenames in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        directories[:] = sorted(directories)
        for name in directories + filenames:
            candidate = current_path / name
            if candidate.is_symlink() or stat.S_ISLNK(candidate.lstat().st_mode):
                raise RuntimeError("launcher_reparse_rejected")
        for name in sorted(filenames):
            source = current_path / name
            relative = source.relative_to(root).as_posix()
            size = source.stat().st_size
            total += size
            if len(rows) >= MAX_LAUNCHER_FILES or total > MAX_LAUNCHER_BYTES:
                raise RuntimeError("launcher_bounds_exceeded")
            rows.append((source, relative, size, sha256(source)))
    rows.sort(key=lambda row: row[1])
    if not any(relative.casefold() == "localaihub.exe" for _source, relative, _size, _digest in rows):
        raise RuntimeError("launcher_executable_missing")
    return rows


def _canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def build(
    output: Path,
    *,
    update_kind: str = "APP_ONLY",
    runtime_root: Path | None = None,
    launcher_bundle: Path | None = None,
    workflow_run_id: int | None = None,
) -> dict[str, object]:
    if update_kind not in {"APP_ONLY", "FULL", "APP_AND_LAUNCHER"}:
        raise RuntimeError("update_kind_invalid")
    if update_kind == "FULL" and runtime_root is None:
        raise RuntimeError("runtime_root_required")
    if update_kind == "APP_AND_LAUNCHER" and launcher_bundle is None:
        raise RuntimeError("launcher_bundle_required")
    source_commit = os.environ.get("GITHUB_SHA") or git("rev-parse", "HEAD")
    if len(source_commit) != 40 or any(char not in "0123456789abcdef" for char in source_commit):
        raise RuntimeError("source_commit_invalid")
    output.mkdir(parents=True, exist_ok=True)
    archive = output / ARCHIVE_NAME
    paths = tracked_files(ROOT)
    runtime = runtime_files(runtime_root) if update_kind == "FULL" and runtime_root is not None else []
    launcher = launcher_files(launcher_bundle) if update_kind == "APP_AND_LAUNCHER" and launcher_bundle is not None else []
    if update_kind == "APP_AND_LAUNCHER":
        raw_run_id = workflow_run_id if workflow_run_id is not None else os.environ.get("GITHUB_RUN_ID")
        try:
            workflow_run_id = int(raw_run_id)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise RuntimeError("workflow_run_id_required") from None
        if workflow_run_id <= 0:
            raise RuntimeError("workflow_run_id_invalid")
    runtime_version = os.environ.get("LOCALAIHUB_RUNTIME_VERSION") if update_kind == "FULL" else None
    if update_kind == "FULL" and not runtime_version:
        raise RuntimeError("runtime_version_required")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for relative in paths:
            source = ROOT / relative
            info = zipfile.ZipInfo(f"app/{relative.as_posix()}", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            bundle.writestr(info, source.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        for source, relative, _size, _digest in runtime:
            info = zipfile.ZipInfo(f"runtime/{relative}", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            bundle.writestr(info, source.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        for source, relative, _size, _digest in launcher:
            info = zipfile.ZipInfo(f"launcher/LocalAIHub/{relative}", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            bundle.writestr(info, source.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    digest = sha256(archive)
    runtime_inventory = [{"name": relative, "size": size, "sha256": file_digest} for _source, relative, size, file_digest in runtime]
    runtime_hash = hashlib.sha256((json.dumps(runtime_inventory, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode()).hexdigest() if runtime_inventory else None
    app_rows = [{"name": path.as_posix(), "size": (ROOT / path).stat().st_size, "sha256": sha256(ROOT / path)} for path in paths]
    app_rows.sort(key=lambda row: row["name"])
    app_manifest_sha256 = hashlib.sha256(_canonical(app_rows)).hexdigest()
    launcher_rows = [{"name": relative, "size": size, "sha256": file_digest} for _source, relative, size, file_digest in launcher]
    launcher_rows.sort(key=lambda row: row["name"])
    launcher_manifest_sha256 = hashlib.sha256(_canonical(launcher_rows)).hexdigest() if launcher_rows else None
    launcher_executable_sha256 = next((row["sha256"] for row in launcher_rows if row["name"].casefold() == "localaihub.exe"), None)
    manifest = {
        "schema_version": COMPOSITE_SCHEMA if update_kind == "APP_AND_LAUNCHER" else SCHEMA,
        "product_id": PRODUCT_ID,
        "product_version": PRODUCT_VERSION,
        "channel": "main",
        "source_commit": source_commit,
        "payload_id": f"main-{source_commit[:12]}",
        "runtime_strategy": "bundled" if update_kind == "FULL" else "reuse-current",
        "archive": ARCHIVE_NAME,
        "archive_sha256": digest,
        "file_count": len(paths) + len(runtime) + len(launcher),
    }
    if update_kind == "APP_AND_LAUNCHER":
        manifest.update({
            "workflow_run_id": workflow_run_id,
            "app_file_count": len(paths),
            "app_total_bytes": sum(row["size"] for row in app_rows),
            "app_manifest_sha256": app_manifest_sha256,
            "launcher_format": "onedir",
            "launcher_file_count": len(launcher_rows),
            "launcher_total_bytes": sum(row["size"] for row in launcher_rows),
            "launcher_manifest_sha256": launcher_manifest_sha256,
            "launcher_executable_sha256": launcher_executable_sha256,
        })
    # Write the bytes that are hashed/packaged explicitly.  ``Path.write_text``
    # uses the Windows text-mode newline translation, which turns the LF used
    # for the digest calculation into CRLF on disk and makes the updater reject
    # an otherwise valid artifact with UPDATE_CONTRACT_HASH_MISMATCH.
    manifest_raw = (json.dumps(manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    (output / "update-manifest.json").write_bytes(manifest_raw)
    if update_kind == "APP_AND_LAUNCHER":
        contract = {
            "schema_version": "local-ai-hub-product-update-contract.v1",
            "product_id": PRODUCT_ID,
            "product_version": PRODUCT_VERSION,
            "update_kind": update_kind,
            "app_protocol": API_PROTOCOL_VERSION,
            "runtime_contract": "reuse-current",
            "runtime_version": None,
            "runtime_hash": None,
            "minimum_launcher_version": "v8.0.1",
            "source_commit": source_commit,
            "workflow_run_id": workflow_run_id,
            "app_payload_id": f"main-{source_commit[:12]}",
            "app_manifest_sha256": app_manifest_sha256,
            "app_file_count": len(paths),
            "app_total_bytes": sum(row["size"] for row in app_rows),
            "launcher_format": "onedir",
            "launcher_executable_sha256": launcher_executable_sha256,
            "launcher_tree_manifest_sha256": launcher_manifest_sha256,
            "launcher_file_count": len(launcher_rows),
            "launcher_total_bytes": sum(row["size"] for row in launcher_rows),
        }
    else:
        contract = {
            "schema_version": UPDATE_CONTRACT_SCHEMA,
            "update_kind": update_kind,
            "app_protocol": API_PROTOCOL_VERSION,
            "runtime_contract": "bundled" if update_kind == "FULL" else "reuse-current",
            "runtime_version": runtime_version,
            "runtime_hash": runtime_hash,
            "minimum_launcher_version": "v8.0.1",
            "source_commit": source_commit,
        }
    contract_raw = (json.dumps(contract, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    (output / UPDATE_CONTRACT_NAME).write_bytes(contract_raw)
    contract_digest = hashlib.sha256(contract_raw).hexdigest()
    sums_raw = f"{digest}  {ARCHIVE_NAME}\n{contract_digest}  {UPDATE_CONTRACT_NAME}\n".encode("ascii")
    (output / "SHA256SUMS.txt").write_bytes(sums_raw)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="dist/main-update")
    parser.add_argument("--update-kind", choices=("APP_ONLY", "FULL", "APP_AND_LAUNCHER"), default="APP_ONLY")
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--launcher-bundle", type=Path)
    parser.add_argument("--workflow-run-id", type=int)
    args = parser.parse_args()
    manifest = build(
        Path(args.output), update_kind=args.update_kind, runtime_root=args.runtime_root,
        launcher_bundle=args.launcher_bundle, workflow_run_id=args.workflow_run_id,
    )
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
