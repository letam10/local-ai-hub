from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
REPORT_LARGE_FILE_BYTES = 10 * 1024 * 1024
FAIL_LARGE_FILE_BYTES = 50 * 1024 * 1024
REVIEWED_LARGE_FILE_ALLOWLIST: frozenset[str] = frozenset()

FORBIDDEN_MODEL_SUFFIXES = {
    ".bin",
    ".ckpt",
    ".gguf",
    ".npy",
    ".npz",
    ".onnx",
    ".pickle",
    ".pkl",
    ".pt",
    ".pth",
    ".safetensors",
}

PRIVATE_MEDIA_SUFFIXES = {
    ".aac",
    ".aiff",
    ".avi",
    ".flac",
    ".gif",
    ".jpeg",
    ".jpg",
    ".m4a",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".png",
    ".tif",
    ".tiff",
    ".wav",
    ".webm",
    ".webp",
}

IGNORED_RUNTIME_ROOTS = {
    "backups",
    "cache",
    "environments",
    "logs",
    "models",
    "output",
    "runtime",
    "runtimes",
    "temp",
}

REQUIRED_SOURCE_FILES = {
    ".gitignore",
    "AGENTS.md",
    "README.md",
    "dependencies.lock.json",
    "Config/components.example.json",
    "Config/hub_config.example.json",
    "Config/application_registry.example.json",
    "scripts/refresh_final_inventory.py",
    "scripts/refresh_managed_registry.py",
    "scripts/inventory_legacy_v3.py",
    "scripts/cleanup_legacy_v3.ps1",
    "scripts/migrate_python_environment_v3.ps1",
    "scripts/write_v3_final_report.py",
    "scripts/start_web_ui.cmd",
    "scripts/start_web_ui.ps1",
    "scripts/update_managed_shortcuts.ps1",
    "src/app/main.py",
    "src/services/api/api_server.py",
    "src/services/api/core.py",
    "src/services/api/jobs.py",
    "src/services/artifact_store.py",
    "src/services/tool_smoke.py",
    "src/services/job_manager/manager.py",
    "src/services/process_manager/managed.py",
    "src/services/mcp/local_ai_mcp_server.py",
    "src/services/runtime_registry.py",
    "src/services/storage_manager/overview.py",
    "src/shared/paths/registry.py",
    "src/ui/index.html",
    "src/ui/app.js",
    "src/ui/api.js",
    "src/ui/pages.js",
    "src/ui/styles.css",
    "scripts/migrate_layout_v2.ps1",
    "scripts/rollback_layout_v2.ps1",
    "src/modules/sam2/backend/worker.py",
    "src/modules/animesr/backend/worker.py",
    "src/modules/image_generation/backend/comfyui.py",
    "docs/TRUE_SINGLE_WINDOW_V3.md",
}

TOKEN_PATTERNS = {
    "OpenAI-style token": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "GitHub fine-grained token": re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    "GitHub personal token": re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    "Bearer token": re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{16,}\b", re.IGNORECASE),
}

ASSIGNMENT_PATTERN = re.compile(
    r"""(?im)(?:[\"']?(?P<key>OPENAI_API_KEY|HF_TOKEN|GITHUB_TOKEN|api_key|password|secret)[\"']?)\s*(?:=|:)\s*(?P<value>\"(?:\\.|[^\"])*\"|'(?:\\.|[^'])*'|[^\s,}\]]+)"""
)


def relative_path(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(message or "git ls-files failed")
    return [ROOT / value.decode("utf-8") for value in result.stdout.split(b"\0") if value]


def source_asset_allowed(relative: str) -> bool:
    parts = tuple(part.casefold() for part in PurePosixPath(relative).parts)
    if len(parts) >= 3 and parts[:2] == ("docs", "assets"):
        return True
    if len(parts) >= 4 and parts[:3] == ("src", "ui", "assets"):
        return True
    if len(parts) >= 5 and parts[:2] == ("src", "modules"):
        return any(parts[index : index + 2] == ("ui", "assets") for index in range(2, len(parts) - 1))
    if len(parts) >= 3 and parts[0] == "apps" and "assets" in parts[1:-1]:
        return True
    return len(parts) >= 3 and parts[:2] == ("tests", "fixtures")


def is_placeholder(value: str) -> bool:
    candidate = value.strip().rstrip(",").strip().strip("\"'")
    normalized = candidate.casefold()
    return (
        not candidate
        or normalized in {"null", "none", "redacted", "placeholder", "example"}
        or candidate.startswith(("${", "<"))
        or normalized.startswith(("replace", "your_", "your-"))
    )


def validate_python(files: list[Path], failures: list[str]) -> None:
    for path in files:
        if path.suffix.casefold() != ".py":
            continue
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except (OSError, UnicodeDecodeError, SyntaxError) as exc:
            failures.append(f"Python syntax error in {relative_path(path)}: {exc}")


def validate_json(files: list[Path], failures: list[str]) -> None:
    for path in files:
        if path.suffix.casefold() != ".json":
            continue
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            failures.append(f"Invalid JSON in {relative_path(path)}: {exc}")


def validate_tracked_artifacts(files: list[Path], failures: list[str], warnings: list[str]) -> None:
    for path in files:
        relative = relative_path(path)
        parts = tuple(part.casefold() for part in PurePosixPath(relative).parts)
        suffix = path.suffix.casefold()
        try:
            size = path.stat().st_size
        except OSError as exc:
            failures.append(f"Cannot stat tracked file {relative}: {exc}")
            continue

        if parts and parts[0] in IGNORED_RUNTIME_ROOTS:
            failures.append(f"Tracked local runtime path: {relative}")
        if suffix in FORBIDDEN_MODEL_SUFFIXES:
            failures.append(f"Forbidden model artifact: {relative}")
        if suffix in PRIVATE_MEDIA_SUFFIXES and not source_asset_allowed(relative):
            failures.append(f"Private media outside source-asset allowlist: {relative}")
        if size > FAIL_LARGE_FILE_BYTES and relative not in REVIEWED_LARGE_FILE_ALLOWLIST:
            failures.append(f"Tracked file exceeds 50 MiB limit: {relative} ({size} bytes)")
        elif size > REPORT_LARGE_FILE_BYTES:
            warnings.append(f"Tracked file exceeds 10 MiB review threshold: {relative} ({size} bytes)")


def validate_secrets(files: list[Path], failures: list[str]) -> None:
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        relative = relative_path(path)
        for label, pattern in TOKEN_PATTERNS.items():
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                failures.append(f"Potential {label} in {relative}:{line}; value is intentionally masked.")
        for match in ASSIGNMENT_PATTERN.finditer(text):
            if is_placeholder(match.group("value")):
                continue
            line = text.count("\n", 0, match.start()) + 1
            key = match.group("key")
            failures.append(f"Potential secret assignment for {key} in {relative}:{line}; value is intentionally masked.")


def validate_structure(files: list[Path], failures: list[str]) -> None:
    tracked = {relative_path(path) for path in files}
    for required in sorted(REQUIRED_SOURCE_FILES):
        if required not in tracked:
            failures.append(f"Required source file is not tracked: {required}")


def validate_worktree_whitespace(failures: list[str]) -> None:
    result = subprocess.run(
        ["git", "diff", "--check", "HEAD"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stdout.strip() or result.stderr.strip() or "git diff --check failed"
        failures.append(f"Whitespace validation failed: {detail}")


def main() -> int:
    failures: list[str] = []
    warnings: list[str] = []
    try:
        files = tracked_files()
    except RuntimeError as exc:
        print(f"FAIL: {exc}")
        return 1

    validate_python(files, failures)
    validate_json(files, failures)
    validate_tracked_artifacts(files, failures, warnings)
    validate_secrets(files, failures)
    validate_structure(files, failures)
    validate_worktree_whitespace(failures)

    for warning in warnings:
        print(f"WARN: {warning}")
    for failure in failures:
        print(f"FAIL: {failure}")
    if failures:
        return 1
    print(f"PASS: validated {len(files)} tracked files; no forbidden artifacts or unmasked secrets found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
