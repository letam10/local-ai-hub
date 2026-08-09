"""Create the ignored V3 legacy-cleanup inventory without changing data.

Only reviewed AI paths are inspected.  The scanner deliberately does not walk
ordinary personal media roots beyond the known application candidates, and it
never follows junctions when calculating bytes.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "Reports" / "LEGACY_CLEANUP_V3.local.md"
STATE = ROOT / "Config" / "legacy_cleanup_v3.local.json"
V3_REPORT = ROOT / "Reports" / "V3_INTEGRATION_AND_CLEANUP.local.md"

VIDEO_AI = "D:\\" + chr(0x1EA2) + "NH VIDEO - AI"
CANDIDATES = [
    ("FLUX Klein Studio", Path(r"D:\AI\FLUX-Klein-Studio")),
    ("FLUX test artifacts", Path(r"D:\AI\FLUX-Klein-Studio-test-artifacts")),
    ("Local Image Studio", Path(r"D:\AI\Local Image Studio")),
    ("Qwen Image", Path(r"D:\AI\Qwen Image")),
    ("AnimeSR jobs/source", Path(r"D:\AI_4K_TEMP\AnimeSR")),
    ("AnimeSR 4K temp", Path(r"D:\AI_4K_TEMP\AnimeSR_4K_Temp")),
    ("AnimeSR alternate build", Path(r"D:\AI_4K_TEMP\AnimeUpscaleStudioBuild")),
    ("AnimeSR spline build", Path(r"D:\AI_4K_TEMP\AnimeSR_V2_Spline_4K")),
    ("AnimeSR environment", Path(r"D:\AI_4K_TEMP\animesr_venv")),
    ("SAM2 runtime", Path(r"D:\AI_4K_TEMP\sam2")),
    ("SAM2 environment", Path(r"D:\AI_4K_TEMP\sam2_venv")),
    ("SAM2 Mask Studio projects", Path(r"D:\AI_4K_TEMP\SAM2_Mask_Studio")),
    ("Practical-RIFE", Path(r"D:\AI_4K_TEMP\Practical-RIFE")),
    ("Real-ESRGAN", Path(r"D:\AI_4K_TEMP\Real-ESRGAN")),
    ("Faster-Whisper", Path(r"D:\AI_4K_TEMP\Faster-Whisper")),
    ("Whisper", Path(r"D:\AI_4K_TEMP\Whisper")),
    ("Qwen Image temporary", Path(r"D:\AI_4K_TEMP\Qwen Image")),
    ("External ComfyUI", Path(r"D:\AI_4K_TEMP\ComfyUI")),
    ("Anime Upscale Studio", Path(VIDEO_AI) / "Anime Upscale Studio"),
    ("FLUX source", Path(VIDEO_AI) / "FLUX Klein Studio"),
    ("LocalAIHub Anime runtime", ROOT / "runtime" / "applications" / "Anime-Upscale-Studio"),
    ("LocalAIHub SAM2 runtime", ROOT / "runtime" / "engines" / "vision" / "SAM2"),
    ("LocalAIHub ComfyUI runtime", ROOT / "runtime" / "engines" / "image" / "ComfyUI"),
]
MODEL_SUFFIXES = {".pt", ".pth", ".ckpt", ".safetensors", ".bin", ".onnx", ".gguf"}
USER_DIR_NAMES = {"job", "jobs", "output", "outputs", "project", "projects", "input", "inputs", "media", "archive"}
RUNTIME_REFERENCE_ROOTS = [
    ROOT / "runtime" / "applications" / "FLUX-Klein-Studio" / "source",
    ROOT / "runtime" / "applications" / "Anime-Upscale-Studio-build",
    ROOT / "runtime" / "engines" / "vision" / "SAM2",
    ROOT / "runtime" / "engines" / "video" / "Practical-RIFE",
    ROOT / "runtime" / "engines" / "video" / "Real-ESRGAN",
]
TEXT_GLOBS = ("*.json", "*.py", "*.yaml", "*.yml", "*.ini", "*.bat", "*.cmd", "*.ps1", "*.txt")


def is_reparse(path: Path) -> bool:
    try:
        attrs = path.lstat().st_file_attributes  # type: ignore[attr-defined]
        return bool(attrs & getattr(stat_module(), "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    except OSError:
        return path.is_symlink()


def stat_module():
    import stat

    return stat


def path_type(path: Path) -> tuple[str, str]:
    if not path.exists() and not path.is_symlink():
        return "MISSING", ""
    if not is_reparse(path):
        return "REAL_DIRECTORY" if path.is_dir() else "REAL_FILE", ""
    target = ""
    try:
        target = str(path.readlink())
    except OSError:
        pass
    if hasattr(os.path, "isjunction") and os.path.isjunction(path):
        return "JUNCTION", target
    return "SYMLINK", target


def local_bytes(path: Path) -> tuple[int, int]:
    if not path.exists() or is_reparse(path):
        return 0, 0
    if path.is_file():
        return path.stat().st_size, 1
    total = 0
    files = 0
    for base, directories, filenames in os.walk(path, followlinks=False):
        directories[:] = [item for item in directories if not is_reparse(Path(base) / item)]
        for filename in filenames:
            candidate = Path(base) / filename
            try:
                if not is_reparse(candidate):
                    total += candidate.stat().st_size
                    files += 1
            except OSError:
                continue
    return total, files


def classify_content(path: Path, path_kind: str) -> tuple[bool, bool]:
    if path_kind != "REAL_DIRECTORY" or not path.exists():
        return False, False
    has_user_data = False
    has_model = False
    try:
        for base, directories, filenames in os.walk(path, followlinks=False):
            directories[:] = [item for item in directories if not is_reparse(Path(base) / item)]
            relative = Path(base).relative_to(path)
            # A virtual environment can contain third-party folders named
            # ``output`` or ``inputs``.  Treat only a top-level workspace
            # directory as potential user data, never arbitrary package data.
            if len(relative.parts) <= 1 and any(name.casefold() in USER_DIR_NAMES for name in directories):
                has_user_data = True
            for filename in filenames:
                file_path = Path(base) / filename
                model_parent = {part.casefold() for part in file_path.relative_to(path).parts[:-1]}
                if Path(filename).suffix.casefold() in MODEL_SUFFIXES and (
                    len(relative.parts) <= 2 or {"model", "models", "checkpoint", "checkpoints", "weight", "weights"} & model_parent
                ):
                    try:
                        # Editable-install ``.pth`` files and tiny runtime
                        # resources are not model artifacts.  The threshold
                        # intentionally errs on the retain side for real
                        # checkpoints while avoiding false positives in venvs.
                        if file_path.stat().st_size >= 1024 * 1024:
                            has_model = True
                    except OSError:
                        continue
                if has_model and has_user_data:
                    return has_user_data, has_model
    except OSError:
        pass
    return has_user_data, has_model


def project_references(path: Path) -> list[str]:
    """Count text references in Hub source/config, excluding inventory reports."""

    needle = str(path)
    command = [
        "git", "grep", "-Il", "--fixed-strings", needle,
        "--", ":(exclude)Reports", ":(exclude)Config/layout_migration.local.json",
        ":(exclude)scripts/refresh_final_inventory.py", ":(exclude)scripts/migrate_layout_v2.ps1",
        ":(exclude)scripts/inventory_legacy_v3.py", ":(exclude)scripts/cleanup_legacy_v3.ps1",
        ":(exclude)scripts/migrate_python_environment_v3.ps1", ":(exclude)scripts/refresh_managed_registry.py",
    ]
    try:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.decode("utf-8", errors="replace") for line in result.stdout.splitlines() if line]


def local_config_references(path: Path) -> list[str]:
    names = ["components.json", "application_registry.local.json", "hub_config.json", "model_registry.json"]
    needle = str(path).casefold()
    result: list[str] = []
    for name in names:
        candidate = ROOT / "Config" / name
        try:
            if needle in candidate.read_text(encoding="utf-8", errors="replace").casefold():
                result.append(f"Config/{name}")
        except OSError:
            continue
    return result


def shortcut_references(path: Path) -> list[str]:
    """Return only shortcut labels that still target a reviewed legacy path."""

    escaped = str(path).replace("'", "''")
    script = (
        "$p='" + escaped + "'; "
        "$roots=@([Environment]::GetFolderPath('Desktop'), (Join-Path $env:PUBLIC 'Desktop'), "
        "(Join-Path $env:APPDATA 'Microsoft\\Windows\\Start Menu\\Programs'), "
        "(Join-Path $env:ProgramData 'Microsoft\\Windows\\Start Menu\\Programs')); "
        "$shell=New-Object -ComObject WScript.Shell; "
        "foreach($root in $roots | Select-Object -Unique) { "
        "if(Test-Path -LiteralPath $root) { Get-ChildItem -LiteralPath $root -Filter '*.lnk' -File -Recurse -ErrorAction SilentlyContinue | ForEach-Object { "
        "$shortcut=$shell.CreateShortcut($_.FullName); "
        "if($shortcut.TargetPath -like \"*$p*\" -or $shortcut.Arguments -like \"*$p*\") { \"Shortcut/$($_.Name)\" } "
        "} } }"
    )
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def runtime_compatibility_references(path: Path) -> list[str]:
    """Find legacy literals in portable runtime text without scanning model blobs."""

    roots = [root for root in RUNTIME_REFERENCE_ROOTS if root.exists()]
    if not roots:
        return []
    command = ["rg", "-l", "--hidden", "--max-filesize", "5M"]
    for pattern in TEXT_GLOBS:
        command.extend(["-g", pattern])
    command.extend(["--fixed-strings", str(path), *[str(root) for root in roots]])
    try:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    records: list[str] = []
    for line in result.stdout.splitlines():
        name = line.decode("utf-8", errors="replace")
        try:
            records.append("Runtime/" + str((ROOT / name).resolve().relative_to(ROOT)))
        except (OSError, ValueError):
            records.append("Runtime/" + name)
    return records


def active_process_reference(path: Path) -> bool:
    script = (
        "$self=$PID; $p='" + str(path).replace("'", "''") + "'; "
        "@(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | "
        "Where-Object { $_.ProcessId -ne $self -and $_.Name -notin @('powershell.exe','pwsh.exe') -and "
        "($_.ExecutablePath -like \"*$p*\" -or $_.CommandLine -like \"*$p*\") }).Count"
    )
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=15, check=False)
        return int(result.stdout.strip() or "0") > 0
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def cleanup_state(path: Path, kind: str, refs: list[str], has_user_data: bool, has_model: bool, active: bool, bytes_value: int) -> str:
    if kind == "MISSING":
        return "NOT_PRESENT"
    if kind in {"JUNCTION", "SYMLINK"}:
        return "ACTIVE_COMPATIBILITY_JUNCTION" if refs else "JUNCTION_REFERENCE_AUDIT_REQUIRED"
    if active:
        return "ACTIVE_PROCESS_RETAIN"
    if kind == "REAL_DIRECTORY" and bytes_value == 0:
        return "EMPTY_LEGACY_FOLDER_SAFE_TO_DELETE"
    if has_user_data:
        return "USER_DATA_RETAIN_OR_MOVE"
    if has_model:
        return "UNIQUE_MODEL_REVIEW_REQUIRED"
    if path.name.casefold().endswith("_venv"):
        return "OLD_ENVIRONMENT_REBUILD_REQUIRED"
    if "test-artifact" in path.name.casefold():
        return "UNUSED_CACHE_REVIEW_REQUIRED"
    return "UNKNOWN_REQUIRES_REVIEW"


def size_text(value: int) -> str:
    return f"{value:,}"


def record(candidate: tuple[str, Path]) -> dict[str, Any]:
    label, path = candidate
    kind, target = path_type(path)
    bytes_value, files = local_bytes(path)
    user_data, model = classify_content(path, kind)
    refs = sorted(set(
        project_references(path)
        + local_config_references(path)
        + shortcut_references(path)
        + runtime_compatibility_references(path)
    ))
    active = active_process_reference(path)
    return {
        "label": label,
        "path": str(path),
        "type": kind,
        "real_or_junction": "JUNCTION" if kind == "JUNCTION" else ("SYMLINK" if kind == "SYMLINK" else "REAL"),
        "actual_local_bytes": bytes_value,
        "file_count": files,
        "target": target,
        "current_references": refs,
        "contains_user_data": user_data,
        "unique_model": model,
        "active_process": active,
        "cleanup_state": cleanup_state(path, kind, refs, user_data, model, active, bytes_value),
    }


def write_report(records: list[dict[str, Any]], disk_before: int) -> None:
    lines = [
        "# Legacy cleanup V3 (local)",
        "",
        f"Generated: {datetime.now(UTC).isoformat()}",
        "",
        "Read-only inventory. Junction targets are never counted as duplicate bytes.",
        "Reviewed legacy roots: `D:\\AI`, `D:\\AI_4K_TEMP`, `D:\\ẢNH VIDEO - AI` and `D:\\LocalAIHub`.",
        "",
        "| Path | Type | REAL/JUNCTION | Actual local bytes | Target | Current references | Contains user data? | Unique model? | Cleanup state |",
        "| --- | --- | --- | ---: | --- | --- | --- | --- | --- |",
    ]
    for item in records:
        references = ", ".join(item["current_references"]) or "0"
        target = item["target"] or "—"
        lines.append(
            "| `{path}` | {type} | {real} | {bytes} | `{target}` | {refs} | {user} | {model} | {state} |".format(
                path=item["path"], type=item["type"], real=item["real_or_junction"], bytes=size_text(item["actual_local_bytes"]),
                target=target, refs=references, user="yes" if item["contains_user_data"] else "no",
                model="yes" if item["unique_model"] else "no", state=item["cleanup_state"],
            )
        )
    lines.extend(["", f"Disk free before cleanup actions: {size_text(disk_before)} bytes.", "", "No move, deletion, or junction removal was executed by this inventory."])
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_summary(records: list[dict[str, Any]], disk_before: int) -> None:
    retained = sum(1 for item in records if item["cleanup_state"] not in {"NOT_PRESENT", "JUNCTION_REFERENCE_AUDIT_REQUIRED"})
    lines = [
        "# V3 integration and cleanup (local)",
        "",
        f"Generated: {datetime.now(UTC).isoformat()}",
        "",
        f"Disk before: {size_text(disk_before)} bytes free.",
        "Disk after: pending only safe cleanup actions.",
        "Real directories removed: 0 (inventory phase).",
        "Junctions removed: 0 (reference audit pending).",
        f"Junctions retained/reviewed: {sum(1 for item in records if item['type'] in {'JUNCTION', 'SYMLINK'})}.",
        "User data moved: 0 (no verified move performed).",
        "Environments rebuilt: 0 (active/legacy environment audit required).",
        "Integrated modules: SAM2, AnimeSR, Whisper, Voice, Vision, OCR, Image AI, FFmpeg workers (source integration state).",
        "External modules: AIRI only.",
        "Functional test rounds: pending after code completion (maximum three).",
        f"Remaining classified entries: {retained}.",
        "Rollback: no cleanup action has run; all legacy paths remain intact at inventory time.",
    ]
    V3_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    disk = shutil.disk_usage(ROOT)
    records = [record(candidate) for candidate in CANDIDATES]
    STATE.write_text(json.dumps({"schema_version": 1, "generated_at": datetime.now(UTC).isoformat(), "records": records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_report(records, disk.free)
    write_summary(records, disk.free)
    print(f"Inventory V3 completed for {len(records)} reviewed candidates.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
