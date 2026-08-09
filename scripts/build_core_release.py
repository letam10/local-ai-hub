"""Build the small Local AI Hub Core release only from explicit safe inputs.

This tool never pads archives. A real build needs a separately prepared,
small portable bootstrap runtime and wheelhouse. It rejects models, CUDA,
Torch, Paddle, environments, caches and user data before writing an archive.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "distribution" / "core.manifest.json"
DEFAULT_OUTPUT = ROOT / "distribution" / "output" / "LocalAIHub-Core-Win64.zip"


def load_manifest(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("Invalid Core manifest schema.")
    return value


def git_tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace").strip() or "git ls-files failed")
    return [ROOT / part.decode("utf-8") for part in result.stdout.split(b"\0") if part]


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def selected_source_files(manifest: dict[str, Any]) -> list[Path]:
    roots = {str(item).strip("/") for item in manifest.get("tracked_source_roots", [])}
    files = {str(item) for item in manifest.get("tracked_source_files", [])}
    excluded = {str(item).casefold() for item in manifest.get("exclude_roots", [])}
    suffixes = {str(item).casefold() for item in manifest.get("forbidden_suffixes", [])}
    selected: list[Path] = []
    for path in git_tracked_files():
        rel = relative(path)
        parts = PurePosixPath(rel).parts
        if not parts or parts[0].casefold() in excluded:
            continue
        if path.suffix.casefold() in suffixes:
            raise ValueError(f"Forbidden file selected for Core: {rel}")
        if rel in files or parts[0] in roots:
            selected.append(path)
    return sorted(selected, key=relative)


def _runtime_files(path: Path, namespace: str, forbidden_tokens: set[str], forbidden_suffixes: set[str]) -> list[tuple[Path, str]]:
    if not path.is_dir():
        raise ValueError(f"Required staging directory is absent: {path}")
    selected: list[tuple[Path, str]] = []
    for candidate in path.rglob("*"):
        if not candidate.is_file() or candidate.is_symlink():
            continue
        rel = candidate.relative_to(path)
        parts = {part.casefold() for part in rel.parts}
        if parts & forbidden_tokens or candidate.suffix.casefold() in forbidden_suffixes:
            raise ValueError(f"Forbidden runtime payload: {candidate}")
        selected.append((candidate, f"{namespace}/{rel.as_posix()}"))
    if not selected:
        raise ValueError(f"Required staging directory is empty: {path}")
    return sorted(selected, key=lambda item: item[1])


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def describe(manifest: dict[str, Any], runtime_dir: Path | None, wheelhouse_dir: Path | None) -> tuple[list[tuple[Path, str]], list[str]]:
    source = [(path, relative(path)) for path in selected_source_files(manifest)]
    notices: list[str] = []
    if runtime_dir is None:
        notices.append("Portable runtime is not supplied; release build is intentionally blocked.")
    if wheelhouse_dir is None:
        notices.append("Wheelhouse is not supplied; release build is intentionally blocked.")
    if runtime_dir is None or wheelhouse_dir is None:
        return source, notices
    forbidden_tokens = {str(item).casefold() for item in manifest.get("forbidden_runtime_path_tokens", [])}
    forbidden_suffixes = {str(item).casefold() for item in manifest.get("forbidden_suffixes", [])}
    runtime_destination = str(manifest["portable_runtime"]["destination"]).strip("/")
    wheelhouse_destination = str(manifest["wheelhouse"]["destination"]).strip("/")
    payload = [
        *source,
        *_runtime_files(runtime_dir, runtime_destination, forbidden_tokens, forbidden_suffixes),
        *_runtime_files(wheelhouse_dir, wheelhouse_destination, forbidden_tokens, forbidden_suffixes),
    ]
    return payload, notices


def archive_payload(entries: list[tuple[Path, str]], output: Path, manifest: dict[str, Any], overwrite: bool) -> dict[str, Any]:
    if output.exists() and not overwrite:
        raise ValueError(f"Output already exists; pass --overwrite after reviewing it: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".part")
    if temporary.exists():
        raise ValueError(f"Temporary release path already exists; inspect it first: {temporary}")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for source, destination in entries:
                archive.write(source, destination)
        size_mib = temporary.stat().st_size / (1024 * 1024)
        target = manifest["target_size_mib"]
        minimum = float(target["minimum"])
        maximum = float(target["maximum"])
        if not minimum <= size_mib <= maximum:
            raise ValueError(
                f"Core archive is {size_mib:.2f} MiB, outside the approved {minimum:.0f}-{maximum:.0f} MiB range. "
                "Do not pad it; provide the reviewed small runtime/wheelhouse or adjust the reviewed manifest."
            )
        os.replace(temporary, output)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise
    return {
        "asset": output.name,
        "sha256": sha256(output),
        "size_bytes": output.stat().st_size,
        "size_mib": round(output.stat().st_size / (1024 * 1024), 3),
        "entries": len(entries),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a reviewed Local AI Hub Core release archive.")
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--runtime-dir", type=Path, default=None)
    parser.add_argument("--wheelhouse-dir", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--build", action="store_true", help="Write the archive after all validation succeeds.")
    parser.add_argument("--overwrite", action="store_true", help="Allow replacement of the exact --output path.")
    args = parser.parse_args()

    try:
        manifest = load_manifest(args.manifest)
        entries, notices = describe(manifest, args.runtime_dir, args.wheelhouse_dir)
        source_bytes = sum(path.stat().st_size for path, _name in entries)
        result = {
            "asset_name": manifest["asset_name"],
            "entries": len(entries),
            "input_bytes": source_bytes,
            "input_mib": round(source_bytes / (1024 * 1024), 3),
            "build_requested": bool(args.build),
            "release_ready": not notices,
            "notices": notices,
        }
        if args.build:
            if notices:
                raise ValueError("Cannot build a release without both --runtime-dir and --wheelhouse-dir.")
            result["archive"] = archive_payload(entries, args.output, manifest, args.overwrite)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
