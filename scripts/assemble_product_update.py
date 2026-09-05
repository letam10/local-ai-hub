#!/usr/bin/env python3
"""Assemble the same-run app and Windows onedir artifacts into one product ZIP.

The Linux CI job can build the deterministic source payload while the Windows
job builds PyInstaller.  This small assembler runs only after both artifacts
are present and refuses to combine different commits or workflow runs.  The
result is the only artifact consumed by the normal product updater.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import stat
import zipfile


PRODUCT_ID = "LocalAIHub"
PRODUCT_VERSION = "8.0.1"
APP_ARCHIVE = "LocalAIHub-main-update.zip"
OUTPUT_ARCHIVE = APP_ARCHIVE
CONTRACT_NAME = "update-contract.json"
MANIFEST_NAME = "update-manifest.json"
SCHEMA = "local-ai-hub-composite-update.v1"
CONTRACT_SCHEMA = "local-ai-hub-product-update-contract.v1"
MAX_FILES = 22_000
MAX_LAUNCHER_FILES = 10_000
MAX_LAUNCHER_BYTES = 500 * 1024 * 1024


def _canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _find_file(root: Path, name: str) -> Path:
    direct = root / name
    if direct.is_file():
        return direct
    matches = sorted(path for path in root.rglob(name) if path.is_file())
    if len(matches) != 1:
        raise RuntimeError(f"{name.lower().replace('.', '_')}_ambiguous")
    return matches[0]


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("metadata_invalid")
    return value


def _read_launcher_zip(path: Path, *, metadata_path: Path | None = None) -> tuple[dict[str, object], dict[str, bytes]]:
    build_path = metadata_path or path.parent / "launcher-build.json"
    metadata = _read_json(build_path) if build_path.is_file() else {}
    with zipfile.ZipFile(path, "r") as bundle:
        members = bundle.infolist()
        files: dict[str, bytes] = {}
        for info in members:
            name = info.filename.replace("\\", "/")
            if not name or name.startswith("/") or "\\" in info.filename or any(part in {"", ".", ".."} for part in name.split("/") if info.is_dir() is False):
                raise RuntimeError("launcher_archive_path_invalid")
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                raise RuntimeError("launcher_archive_reparse")
            if info.is_dir():
                continue
            if not name.startswith("LocalAIHub/"):
                raise RuntimeError("launcher_archive_root_invalid")
            files[name.removeprefix("LocalAIHub/")] = bundle.read(info)
    if "LocalAIHub.exe" not in files or not any(name.startswith("_internal/") for name in files):
        raise RuntimeError("launcher_bundle_incomplete")
    rows = [{"name": name, "size": len(data), "sha256": _sha256_bytes(data)} for name, data in sorted(files.items())]
    total = sum(row["size"] for row in rows)
    if len(rows) > MAX_LAUNCHER_FILES or total > MAX_LAUNCHER_BYTES:
        raise RuntimeError("launcher_bounds_exceeded")
    metadata.update({
        "schema_version": "local-ai-hub-launcher-bundle.v1",
        "format": "onedir",
        "executable_sha256": _sha256_bytes(files["LocalAIHub.exe"]),
        "tree_manifest_sha256": _sha256_bytes(_canonical(rows)),
        "file_count": len(rows),
        "total_bytes": total,
        "files": rows,
    })
    return metadata, files


def assemble(app_artifact_dir: Path, launcher_artifact_dir: Path, output_dir: Path, *, workflow_run_id: int) -> dict[str, object]:
    if type(workflow_run_id) is not int or workflow_run_id <= 0:
        raise RuntimeError("workflow_run_id_invalid")
    app_root = app_artifact_dir.absolute()
    launcher_root = launcher_artifact_dir.absolute()
    app_manifest = _read_json(_find_file(app_root, MANIFEST_NAME))
    app_commit = app_manifest.get("source_commit")
    if not isinstance(app_commit, str) or len(app_commit) != 40 or any(char not in "0123456789abcdef" for char in app_commit):
        raise RuntimeError("source_commit_invalid")
    app_archive_path = _find_file(app_root, APP_ARCHIVE)
    launcher_zip = _find_file(launcher_root, "LocalAIHub-stable-launcher-onedir.zip")
    launcher_metadata_path = _find_file(launcher_root, "launcher-build.json")
    launcher_metadata, launcher_files = _read_launcher_zip(launcher_zip, metadata_path=launcher_metadata_path)
    launcher_commit = launcher_metadata.get("source_commit")
    if launcher_commit != app_commit:
        raise RuntimeError("cross_commit_mismatch")
    launcher_run = launcher_metadata.get("workflow_run_id")
    if isinstance(launcher_run, bool) or not isinstance(launcher_run, int) or launcher_run != workflow_run_id:
        raise RuntimeError("cross_run_mismatch")

    app_files: dict[str, bytes] = {}
    with zipfile.ZipFile(app_archive_path, "r") as app_zip:
        for info in app_zip.infolist():
            name = info.filename.replace("\\", "/")
            if info.is_dir():
                continue
            if not name.startswith("app/") or name.startswith("/") or "\\" in info.filename or any(part in {"", ".", ".."} for part in name.split("/")):
                raise RuntimeError("app_archive_path_invalid")
            app_files[name] = app_zip.read(info)
    if not app_files or len(app_files) + len(launcher_files) > MAX_FILES:
        raise RuntimeError("composite_file_count_invalid")
    app_rows = [{"name": name.removeprefix("app/"), "size": len(data), "sha256": _sha256_bytes(data)} for name, data in sorted(app_files.items())]
    app_total = sum(row["size"] for row in app_rows)
    app_digest = _sha256_bytes(_canonical(app_rows))
    launcher_rows = list(launcher_metadata["files"])
    launcher_digest = str(launcher_metadata["tree_manifest_sha256"])
    output = output_dir.absolute()
    output.mkdir(parents=True, exist_ok=True)
    archive = output / OUTPUT_ARCHIVE
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for name, data in sorted(app_files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            bundle.writestr(info, data)
        for name, data in sorted(launcher_files.items()):
            info = zipfile.ZipInfo(f"launcher/LocalAIHub/{name}", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            bundle.writestr(info, data)
    archive_digest = _sha256_file(archive)
    manifest = {
        "schema_version": SCHEMA,
        "product_id": PRODUCT_ID,
        "product_version": PRODUCT_VERSION,
        "channel": "main",
        "source_commit": app_commit,
        "workflow_run_id": workflow_run_id,
        "payload_id": f"main-{app_commit[:12]}",
        "runtime_strategy": "reuse-current",
        "archive": OUTPUT_ARCHIVE,
        "archive_sha256": archive_digest,
        "file_count": len(app_files) + len(launcher_files),
        "app_file_count": len(app_rows),
        "app_total_bytes": app_total,
        "app_manifest_sha256": app_digest,
        "launcher_format": "onedir",
        "launcher_file_count": len(launcher_rows),
        "launcher_total_bytes": int(launcher_metadata["total_bytes"]),
        "launcher_manifest_sha256": launcher_digest,
        "launcher_executable_sha256": launcher_metadata["executable_sha256"],
    }
    contract = {
        "schema_version": CONTRACT_SCHEMA,
        "product_id": PRODUCT_ID,
        "product_version": PRODUCT_VERSION,
        "update_kind": "APP_AND_LAUNCHER",
        "app_protocol": "v8-api.v1",
        "runtime_contract": "reuse-current",
        "runtime_version": None,
        "runtime_hash": None,
        "minimum_launcher_version": "v8.0.1",
        "source_commit": app_commit,
        "workflow_run_id": workflow_run_id,
        "app_payload_id": f"main-{app_commit[:12]}",
        "app_manifest_sha256": app_digest,
        "app_file_count": len(app_rows),
        "app_total_bytes": app_total,
        "launcher_format": "onedir",
        "launcher_executable_sha256": launcher_metadata["executable_sha256"],
        "launcher_tree_manifest_sha256": launcher_digest,
        "launcher_file_count": len(launcher_rows),
        "launcher_total_bytes": int(launcher_metadata["total_bytes"]),
    }
    (output / MANIFEST_NAME).write_bytes(_canonical(manifest))
    contract_raw = _canonical(contract)
    (output / CONTRACT_NAME).write_bytes(contract_raw)
    (output / "SHA256SUMS.txt").write_bytes(
        f"{archive_digest}  {OUTPUT_ARCHIVE}\n{_sha256_bytes(contract_raw)}  {CONTRACT_NAME}\n".encode("ascii")
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-artifact-dir", type=Path, required=True)
    parser.add_argument("--launcher-artifact-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workflow-run-id", type=int, required=True)
    args = parser.parse_args()
    print(json.dumps(assemble(args.app_artifact_dir, args.launcher_artifact_dir, args.output, workflow_run_id=args.workflow_run_id), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
