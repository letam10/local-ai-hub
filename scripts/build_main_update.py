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
import subprocess
import zipfile

from src.shared.version import PRODUCT_VERSION

SCHEMA = "local-ai-hub-main-update.v1"
PRODUCT_ID = "LocalAIHub"
ARCHIVE_NAME = "LocalAIHub-main-update.zip"
MAX_FILES = 12_000


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(*args: str) -> str:
    result = subprocess.run(["git", *args], text=True, capture_output=True, check=True)
    return result.stdout.strip()


def tracked_files(root: Path) -> list[Path]:
    raw = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True).stdout
    paths = [Path(item.decode("utf-8")) for item in raw.split(b"\0") if item]
    files = [item for item in paths if (root / item).is_file()]
    if not files or len(files) > MAX_FILES:
        raise RuntimeError("tracked_file_count_invalid")
    return sorted(files, key=lambda item: item.as_posix())


def build(output: Path) -> dict[str, object]:
    root = Path(__file__).resolve().parents[1]
    source_commit = os.environ.get("GITHUB_SHA") or git("rev-parse", "HEAD")
    if len(source_commit) != 40 or any(char not in "0123456789abcdef" for char in source_commit):
        raise RuntimeError("source_commit_invalid")
    output.mkdir(parents=True, exist_ok=True)
    archive = output / ARCHIVE_NAME
    paths = tracked_files(root)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for relative in paths:
            source = root / relative
            info = zipfile.ZipInfo(f"app/{relative.as_posix()}", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            bundle.writestr(info, source.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    digest = sha256(archive)
    manifest = {
        "schema_version": SCHEMA,
        "product_id": PRODUCT_ID,
        "product_version": PRODUCT_VERSION,
        "channel": "main",
        "source_commit": source_commit,
        "payload_id": f"main-{source_commit[:12]}",
        "runtime_strategy": "reuse-current",
        "archive": ARCHIVE_NAME,
        "archive_sha256": digest,
        "file_count": len(paths),
    }
    (output / "update-manifest.json").write_text(json.dumps(manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n", encoding="utf-8")
    (output / "SHA256SUMS.txt").write_text(f"{digest}  {ARCHIVE_NAME}\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="dist/main-update")
    args = parser.parse_args()
    manifest = build(Path(args.output))
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
