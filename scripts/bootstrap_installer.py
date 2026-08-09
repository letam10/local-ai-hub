"""Safe planner and installer for optional Local AI Hub modules.

Default execution is read-only. Applying an optional module requires a
published HTTPS archive and a verified SHA-256 in the module manifest. Models
are deliberately not downloaded by this script unless a future reviewed
manifest and an explicit confirmation both permit that action.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MODULE_MANIFEST = ROOT / "distribution" / "modules.manifest.json"
DEFAULT_INSTALL_ROOT = ROOT / "Modules"
MODEL_SUFFIXES = {".bin", ".ckpt", ".gguf", ".npy", ".npz", ".onnx", ".pkl", ".pt", ".pth", ".safetensors"}


def load_manifest(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(value.get("modules"), list):
        raise ValueError("Invalid module manifest schema.")
    return value


def valid_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdefABCDEF" for char in value)


def verified_archive(module: dict[str, Any]) -> tuple[str | None, str | None]:
    archive = module.get("archive")
    if not isinstance(archive, dict):
        return None, "Module archive metadata is missing."
    url = archive.get("url")
    digest = archive.get("sha256")
    if not isinstance(url, str) or not url.startswith("https://"):
        return None, "Module is not published with a verified HTTPS archive."
    if not valid_sha256(digest):
        return None, "Module manifest lacks a valid SHA-256."
    return str(digest).lower(), None


def selected_modules(manifest: dict[str, Any], mode: str, requested: list[str]) -> list[dict[str, Any]]:
    modules = [item for item in manifest["modules"] if isinstance(item, dict)]
    by_id = {str(item.get("id")): item for item in modules if isinstance(item.get("id"), str)}
    if mode == "minimal":
        return []
    wanted = list(by_id) if mode == "full" else requested
    if not wanted:
        raise ValueError("Module mode requires one or more --module values.")
    result: list[dict[str, Any]] = []
    for module_id in wanted:
        if module_id == "all":
            result.extend(item for item in modules if item not in result)
            continue
        if module_id not in by_id:
            raise ValueError(f"Unknown module: {module_id}")
        result.append(by_id[module_id])
    return result


def plan_install(manifest: dict[str, Any], mode: str, requested: list[str], confirm_models: bool) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    for module in selected_modules(manifest, mode, requested):
        module_id = str(module["id"])
        digest, issue = verified_archive(module)
        models = module.get("models") if isinstance(module.get("models"), dict) else {}
        model_confirmation_required = bool(models.get("requires_confirmation", True))
        plan.append(
            {
                "id": module_id,
                "title": str(module.get("title") or module_id),
                "module_archive": "ready" if digest else "unavailable",
                "module_reason": issue,
                "model_download": "blocked_without_explicit_confirmation" if model_confirmation_required and not confirm_models else "not_configured",
                "model_downloads_started": False,
            }
        )
    return plan


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_verified(url: str, destination: Path, expected_sha256: str) -> Path:
    partial = destination.with_name(destination.name + ".part")
    if destination.exists() or partial.exists():
        raise ValueError(f"Refusing to overwrite an existing module archive: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=60) as response, partial.open("xb") as handle:
            while chunk := response.read(1024 * 1024):
                handle.write(chunk)
        actual = _sha256(partial)
        if actual.casefold() != expected_sha256.casefold():
            raise ValueError(f"SHA-256 mismatch for {destination.name}: expected {expected_sha256}, got {actual}")
        os.replace(partial, destination)
        return destination
    except Exception:
        if partial.exists():
            partial.unlink()
        raise


def _safe_extract_zip(archive_path: Path, destination: Path) -> None:
    if destination.exists():
        raise ValueError(f"Refusing to overwrite module destination: {destination}")
    staging = destination.with_name(destination.name + ".installing")
    if staging.exists():
        raise ValueError(f"Existing incomplete module staging path requires review: {staging}")
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for entry in archive.infolist():
                parts = PurePosixPath(entry.filename).parts
                if not entry.filename or entry.is_dir():
                    continue
                if PurePosixPath(entry.filename).is_absolute() or ".." in parts:
                    raise ValueError(f"Unsafe module archive path: {entry.filename}")
                if Path(entry.filename).suffix.casefold() in MODEL_SUFFIXES:
                    raise ValueError(f"Model payload is forbidden in a module archive: {entry.filename}")
            archive.extractall(staging)
        os.replace(staging, destination)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def apply_modules(manifest: dict[str, Any], mode: str, requested: list[str], install_root: Path) -> list[str]:
    installed: list[str] = []
    for module in selected_modules(manifest, mode, requested):
        module_id = str(module["id"])
        digest, issue = verified_archive(module)
        if digest is None:
            raise ValueError(f"{module_id}: {issue}")
        archive = module["archive"]
        assert isinstance(archive, dict)
        url = str(archive["url"])
        archive_path = install_root / "_downloads" / f"{module_id}.zip"
        downloaded = _download_verified(url, archive_path, digest)
        _safe_extract_zip(downloaded, install_root / module_id)
        installed.append(module_id)
    return installed


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan or install verified optional Local AI Hub modules.")
    parser.add_argument("--manifest", type=Path, default=MODULE_MANIFEST)
    parser.add_argument("--mode", choices=("minimal", "module", "full"), default="minimal")
    parser.add_argument("--module", action="append", default=[], help="Module id, or all; required in module mode.")
    parser.add_argument("--install-root", type=Path, default=DEFAULT_INSTALL_ROOT)
    parser.add_argument("--apply", action="store_true", help="Download and extract verified module archives.")
    parser.add_argument("--confirm-model-download", action="store_true", help="Explicitly acknowledge model download policy; no model download is implemented without a reviewed manifest.")
    args = parser.parse_args()

    try:
        manifest = load_manifest(args.manifest)
        plan = plan_install(manifest, args.mode, args.module, args.confirm_model_download)
        result: dict[str, Any] = {
            "mode": args.mode,
            "apply": bool(args.apply),
            "model_confirmation": bool(args.confirm_model_download),
            "plan": plan,
            "installed": [],
        }
        if args.apply:
            if args.mode == "minimal":
                result["message"] = "Minimal mode has no optional modules to download."
            else:
                result["installed"] = apply_modules(manifest, args.mode, args.module, args.install_root)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, urllib.error.URLError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
