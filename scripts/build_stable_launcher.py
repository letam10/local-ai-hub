"""Build the small installed-product launcher with the canonical icon.

The stable entrypoint is an onedir bundle.  Keeping the bootloader and its
support files side-by-side avoids a runtime ``_MEI`` extraction step during
normal startup while preserving the public ``LocalAIHub.exe`` path.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
ICON = ROOT / "distribution" / "assets" / "local-ai-hub.ico"
ENTRY = ROOT / "src" / "app" / "stable_launcher.py"
LAUNCHER_BUNDLE_NAME = "LocalAIHub"
ALLOWED_OUTPUT_ROOT_NAMES = ("Temp", "dist")


def build(output_dir: Path, *, clean: bool = True) -> Path:
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
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(build(args.output_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
