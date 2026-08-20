"""
/*
  FILE NOTE
  - Mục đích: Packaging and release manifest builder cho Local AI Hub V7 — biên dịch Inno Setup installer EXE, tạo core release ZIP, kiểm tra SHA-256, và xuất release manifest
  - Liên kết trực tiếp: distribution/installer.iss, distribution/release_manifest.json, scripts/build_core_release.py
  - Vùng ảnh hưởng khi sửa: Quy trình đóng gói và xuất artifact release
*/
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.shared.version import PRODUCT_VERSION

DIST_DIR = ROOT / "dist"
MANIFEST_PATH = ROOT / "distribution" / "release_manifest.json"
ISS_PATH = ROOT / "distribution" / "installer.iss"


def find_iscc() -> str | None:
    candidates = [
        shutil.which("ISCC.exe"),
        shutil.which("iscc"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"),
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
        r"C:\Program Files (x86)\Inno Setup 5\ISCC.exe",
        r"C:\Program Files\Inno Setup 5\ISCC.exe",
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def get_git_info() -> tuple[str, str]:
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()
    except Exception:
        commit = "unknown"
    try:
        branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT).decode().strip()
    except Exception:
        branch = "unknown"
    return commit, branch


def compile_installer() -> tuple[Path | None, str | None]:
    iscc = find_iscc()
    if not iscc:
        return None, "Inno Setup compiler (ISCC.exe) not found on system."

    DIST_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [iscc, str(ISS_PATH)]
    try:
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=True)
        exe_path = DIST_DIR / f"LocalAIHub-Setup-Win64-v{PRODUCT_VERSION}.exe"
        if exe_path.exists():
            return exe_path, None
        return None, "Installer compilation completed but output EXE not found."
    except subprocess.CalledProcessError as exc:
        return None, f"ISCC compilation failed: {exc.stderr or exc.stdout}"


def build_release_package(output_zip: Path | None = None, compile_exe: bool = True) -> dict[str, Any]:
    """Build the clean Core release zip and Inno Setup installer, creating release manifest."""
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    out_zip = output_zip or (DIST_DIR / f"LocalAIHub-Core-Win64-v{PRODUCT_VERSION}.zip")

    commit, branch = get_git_info()
    timestamp = datetime.now(timezone.utc).isoformat()

    included_roots = ["src", "scripts", "distribution", "docs", "workflows", "architecture"]
    included_files = [
        "requirements-hub.txt",
        "dependencies.lock.json",
        "README.md",
        "LICENSES.md",
        "AGENTS.md",
        "LocalAIHub.vbs",
        "LocalAIHub.cmd",
    ]

    total_files = 0
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        # Add root files
        for f_name in included_files:
            f_path = ROOT / f_name
            if f_path.exists():
                zf.write(f_path, f_name)
                total_files += 1

        # Add config examples
        config_dir = ROOT / "Config"
        if config_dir.exists():
            for ex in config_dir.glob("*.example.json"):
                zf.write(ex, f"Config/{ex.name}")
                total_files += 1

        # Add roots
        for r_name in included_roots:
            r_path = ROOT / r_name
            if r_path.exists():
                for p in r_path.rglob("*"):
                    if p.is_file() and "__pycache__" not in p.parts:
                        rel = p.relative_to(ROOT).as_posix()
                        zf.write(p, rel)
                        total_files += 1

    zip_hash = sha256_file(out_zip)
    zip_size = out_zip.stat().st_size

    # Compile Inno Setup installer
    setup_exe_info: dict[str, Any] = {}
    if compile_exe:
        exe_path, err = compile_installer()
        if exe_path and exe_path.exists():
            setup_exe_info = {
                "file_name": exe_path.name,
                "size_bytes": exe_path.stat().st_size,
                "sha256": sha256_file(exe_path),
            }
        else:
            setup_exe_info = {"status": "compile_failed", "error": err}

    manifest = {
        "schema_version": 1,
        "application_name": "Local AI Hub",
        "version": PRODUCT_VERSION,
        "git_commit": commit,
        "git_branch": branch,
        "build_timestamp": timestamp,
        "packaging_tool": "Local AI Hub Release Packager (Python/zipfile) + Inno Setup 6",
        "platform": "windows-x64",
        "release_artifacts": {
            "core_zip": {
                "file_name": out_zip.name,
                "size_bytes": zip_size,
                "sha256": zip_hash,
                "total_files": total_files,
            },
            "setup_exe": setup_exe_info,
        },
        "included_application_components": [
            "src (Application Core, UI Shell, API Server, Settings, Diagnostics, Backups)",
            "scripts (Launchers, Verifiers, Updater, Repair, Uninstaller)",
            "distribution (Installer specs, Release manifests)",
            "docs (Architecture, Recovery, API documentation)",
            "Config/*.example.json (Tracked configuration templates)",
            "LocalAIHub.vbs (No-console Windows desktop launcher)",
            "LocalAIHub.cmd (Command-line desktop launcher)",
        ],
        "excluded_machine_local_data": [
            "Models (AI model weights preserved locally on host)",
            "Environments (Python virtual environments preserved locally on host)",
            "runtime (Managed native runtime binaries preserved on host)",
            "Output (User-generated images, videos, audio preserved on host)",
            "Config/settings.json (User settings preserved on host)",
            "Projects & Backups (User workspace and backup archives preserved on host)",
            "Reports (Forensic evidence and audit logs preserved on host)",
        ],
        "minimum_system_requirements": {
            "os": "Windows 10 / Windows 11 (64-bit)",
            "webview": "Microsoft Edge WebView2 Runtime",
            "python": "Python 3.10+ (64-bit)",
            "memory": "8 GiB RAM minimum (16 GiB+ recommended)",
            "gpu": "NVIDIA GeForce RTX (8GB+ VRAM recommended for heavy models)",
            "network": "Loopback-only (127.0.0.1) - No internet required for core operation",
        },
        "ai_runtime_validation_status": "DEFERRED BY USER",
        "known_limitations": [
            "AI runtime and model execution deferred until user-directed activation.",
            "Requires Microsoft Edge WebView2 runtime installed for desktop GUI.",
        ],
    }

    # Save manifest identically to both distribution/ and dist/
    manifest_json = json.dumps(manifest, indent=2, ensure_ascii=False)
    MANIFEST_PATH.write_text(manifest_json, encoding="utf-8")
    (DIST_DIR / "release_manifest.json").write_text(manifest_json, encoding="utf-8")

    return manifest


if __name__ == "__main__":
    res = build_release_package()
    print("=== RELEASE MANIFEST ===")
    print(json.dumps(res, indent=2, ensure_ascii=False))
