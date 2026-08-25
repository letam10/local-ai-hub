#!/usr/bin/env python3
"""Build a deterministic APP_ONLY update artifact for a validated ``main`` SHA.

The archive contains tracked repository files under ``app/`` only.  It never
contains the installed runtime, Models, Environments, Output, machine Config or
other ignored local state.  The installed updater reuses the already-reviewed
bundled runtime and validates imports before activating the new payload.
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


def build(output: Path, *, update_kind: str = "APP_ONLY", runtime_root: Path | None = None) -> dict[str, object]:
    if update_kind not in {"APP_ONLY", "FULL"}:
        raise RuntimeError("update_kind_invalid")
    if update_kind == "FULL" and runtime_root is None:
        raise RuntimeError("runtime_root_required")
    source_commit = os.environ.get("GITHUB_SHA") or git("rev-parse", "HEAD")
    if len(source_commit) != 40 or any(char not in "0123456789abcdef" for char in source_commit):
        raise RuntimeError("source_commit_invalid")
    output.mkdir(parents=True, exist_ok=True)
    archive = output / ARCHIVE_NAME
    paths = tracked_files(ROOT)
    runtime = runtime_files(runtime_root) if update_kind == "FULL" and runtime_root is not None else []
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
    digest = sha256(archive)
    runtime_inventory = [{"name": relative, "size": size, "sha256": file_digest} for _source, relative, size, file_digest in runtime]
    runtime_hash = hashlib.sha256((json.dumps(runtime_inventory, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode()).hexdigest() if runtime_inventory else None
    manifest = {
        "schema_version": SCHEMA,
        "product_id": PRODUCT_ID,
        "product_version": PRODUCT_VERSION,
        "channel": "main",
        "source_commit": source_commit,
        "payload_id": f"main-{source_commit[:12]}",
        "runtime_strategy": "bundled" if update_kind == "FULL" else "reuse-current",
        "archive": ARCHIVE_NAME,
        "archive_sha256": digest,
        "file_count": len(paths) + len(runtime),
    }
    (output / "update-manifest.json").write_text(json.dumps(manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n", encoding="utf-8")
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
    contract_raw = json.dumps(contract, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    (output / UPDATE_CONTRACT_NAME).write_text(contract_raw, encoding="utf-8")
    contract_digest = hashlib.sha256(contract_raw.encode("utf-8")).hexdigest()
    (output / "SHA256SUMS.txt").write_text(f"{digest}  {ARCHIVE_NAME}\n{contract_digest}  {UPDATE_CONTRACT_NAME}\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="dist/main-update")
    parser.add_argument("--update-kind", choices=("APP_ONLY", "FULL"), default="APP_ONLY")
    parser.add_argument("--runtime-root", type=Path)
    args = parser.parse_args()
    manifest = build(Path(args.output), update_kind=args.update_kind, runtime_root=args.runtime_root)
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
