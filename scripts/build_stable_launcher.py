"""Build the small installed-product launcher with the canonical icon.

The stable entrypoint is an onedir bundle.  Keeping the bootloader and its
support files side-by-side avoids a runtime ``_MEI`` extraction step during
normal startup while preserving the public ``LocalAIHub.exe`` path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.shared.source_provenance import resolve_source_commit


ICON = ROOT / "distribution" / "assets" / "local-ai-hub.ico"
ENTRY = ROOT / "src" / "app" / "stable_launcher.py"
LAUNCHER_BUNDLE_NAME = "LocalAIHub"
ALLOWED_OUTPUT_ROOT_NAMES = ("Temp", "dist")
LAUNCHER_BUILD_SCHEMA = "local-ai-hub-stable-launcher-build.v1"


def _canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _launcher_rows(bundle: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for current, directories, filenames in os.walk(bundle, topdown=True, followlinks=False):
        current_path = Path(current)
        directories[:] = sorted(directories)
        for name in directories + filenames:
            candidate = current_path / name
            if candidate.is_symlink() or stat.S_ISLNK(candidate.lstat().st_mode):
                raise ValueError("LAUNCHER_BUILD_REPARSE")
        for name in sorted(filenames):
            source = current_path / name
            relative = source.relative_to(bundle).as_posix()
            data = source.read_bytes()
            rows.append({"name": relative, "size": len(data), "sha256": _sha256_bytes(data)})
    rows.sort(key=lambda row: str(row["name"]))
    if not rows or not any(str(row["name"]).casefold() == "localaihub.exe" for row in rows):
        raise ValueError("LAUNCHER_EXECUTABLE_MISSING")
    return rows


def _write_build_metadata(output_dir: Path, bundle: Path, *, source_commit: str, workflow_run_id: int) -> None:
    rows = _launcher_rows(bundle)
    metadata = {
        "schema_version": LAUNCHER_BUILD_SCHEMA,
        "source_commit": source_commit,
        "workflow_run_id": workflow_run_id,
        "format": "onedir",
        "executable_sha256": next(row["sha256"] for row in rows if str(row["name"]).casefold() == "localaihub.exe"),
        "files": rows,
        "file_count": len(rows),
        "total_bytes": sum(int(row["size"]) for row in rows),
        "tree_manifest_sha256": _sha256_bytes(_canonical(rows)),
    }
    (output_dir / "launcher-build.json").write_bytes(_canonical(metadata))


def build(
    output_dir: Path,
    *,
    clean: bool = True,
    expected_source_sha: str | None = None,
    workflow_run_id: int | None = None,
) -> Path:
    output_dir = output_dir.resolve()
    if output_dir == ROOT or any(part.casefold() == ".git" for part in output_dir.parts):
        raise ValueError("LAUNCHER_OUTPUT_ROOT_INVALID")
    allowed_output_roots = tuple(ROOT / name for name in ALLOWED_OUTPUT_ROOT_NAMES)
    if not any(output_dir == candidate or candidate in output_dir.parents for candidate in allowed_output_roots):
        # Build output is restricted to repository-owned disposable roots;
        # source and arbitrary user/system directories are never destinations.
        raise ValueError("LAUNCHER_OUTPUT_ROOT_INVALID")
    if not ENTRY.is_file() or not ICON.is_file():
        raise ValueError("LAUNCHER_INPUT_UNAVAILABLE")
    try:
        source_commit = resolve_source_commit(ROOT, expected_source_sha)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    if workflow_run_id is not None and (isinstance(workflow_run_id, bool) or workflow_run_id <= 0):
        raise ValueError("WORKFLOW_RUN_ID_INVALID")
    pyinstaller = shutil.which("pyinstaller") or str(Path(sys.executable).with_name("Scripts") / "pyinstaller.exe")
    if not Path(pyinstaller).is_file() and not shutil.which("pyinstaller"):
        raise ValueError("PYINSTALLER_UNAVAILABLE")
    work = output_dir / "work"
    spec = output_dir / "spec"
    dist = output_dir / "dist"
    if clean:
        for path in (work, spec, dist):
            if path.exists():
                raise ValueError("LAUNCHER_OUTPUT_OCCUPIED")
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        str(pyinstaller), "--noconfirm", "--clean", "--onedir", "--windowed",
        "--name", LAUNCHER_BUNDLE_NAME, "--icon", str(ICON), "--paths", str(ROOT), "--distpath", str(dist),
        "--workpath", str(work), "--specpath", str(spec), str(ENTRY),
    ]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False, timeout=300)
    bundle = dist / LAUNCHER_BUNDLE_NAME
    output = bundle / "LocalAIHub.exe"
    if result.returncode != 0 or not output.is_file():
        raise ValueError("LAUNCHER_BUILD_FAILED")
    if workflow_run_id is not None:
        _write_build_metadata(output_dir, bundle, source_commit=source_commit, workflow_run_id=workflow_run_id)
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-source-sha")
    parser.add_argument("--workflow-run-id", type=int)
    args = parser.parse_args()
    print(build(
        args.output_dir,
        expected_source_sha=args.expected_source_sha,
        workflow_run_id=args.workflow_run_id,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
