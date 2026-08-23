"""Build the small installed-product launcher with the canonical icon."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
ICON = ROOT / "distribution" / "assets" / "local-ai-hub.ico"
ENTRY = ROOT / "src" / "app" / "stable_launcher.py"


def build(output_dir: Path, *, clean: bool = True) -> Path:
    output_dir = output_dir.resolve()
    if output_dir == ROOT or ".git" in output_dir.parts or "Temp" in output_dir.parts:
        # A task-owned output under Temp is allowed for validation; source and
        # .git roots are never valid destinations.
        if output_dir != ROOT / "Temp" and not str(output_dir).casefold().startswith(str(ROOT / "Temp").casefold()):
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
        str(pyinstaller), "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", "LocalAIHub", "--icon", str(ICON), "--distpath", str(dist),
        "--workpath", str(work), "--specpath", str(spec), str(ENTRY),
    ]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False, timeout=300)
    output = dist / "LocalAIHub.exe"
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
