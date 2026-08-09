"""Read-only V5 audit for approved D: source candidates.

The audit never copies, moves, deletes, vendors, or archives a path. Its local
report can contain workstation paths, so the report is ignored by Git. A
FIRST_PARTY_SOURCE result is only a review candidate: sanitize it manually,
then add the smallest necessary source in a separate reviewed change.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LOCAL_CONFIG = ROOT / "Config" / "source_audit_v5.local.json"
DEFAULT_REPORT = ROOT / "Reports" / "SOURCE_AUDIT_V5.local.md"
CLASSIFICATIONS = {
    "FIRST_PARTY_SOURCE",
    "UPSTREAM_CLONE",
    "BUILD_ARTIFACT",
    "MODEL",
    "ENV",
    "CACHE",
    "USER_DATA",
}
PATH_KEYS = {
    "path",
    "environment",
    "executable",
    "working_directory",
    "local_path",
    "runtime",
    "model",
    "checkpoint",
}
MODEL_SUFFIXES = {".bin", ".ckpt", ".gguf", ".npy", ".npz", ".onnx", ".pkl", ".pt", ".pth", ".safetensors"}
MEDIA_SUFFIXES = {".aac", ".avi", ".flac", ".gif", ".jpeg", ".jpg", ".m4a", ".mkv", ".mov", ".mp3", ".mp4", ".png", ".wav", ".webm", ".webp"}
SKIP_DIRECTORY_NAMES = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    "environments",
    "cache",
    "caches",
    "node_modules",
    "dist",
    "build",
    "models",
    "checkpoints",
    "output",
    "outputs",
    "temp",
    "logs",
}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _walk(value: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key), child
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _configured_paths() -> Iterable[tuple[str, str, Path]]:
    for source in (
        ROOT / "Config" / "components.json",
        ROOT / "Config" / "application_registry.local.json",
        ROOT / "Config" / "model_registry.json",
    ):
        for key, raw in _walk(_read_json(source)):
            if key not in PATH_KEYS or not isinstance(raw, str) or not raw.strip() or raw.startswith("$" + "{"):
                continue
            try:
                yield source.name, key, Path(os.path.expandvars(raw)).expanduser()
            except (OSError, ValueError):
                continue


def _safe_resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


def _under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except (OSError, ValueError):
        return False


def _on_d_drive(path: Path) -> bool:
    return path.drive.casefold() == "d:"


def _git_metadata(path: Path) -> dict[str, str]:
    base = path if path.is_dir() else path.parent
    try:
        top = subprocess.run(
            ["git", "-C", str(base), "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            text=True,
            timeout=4,
        )
        head = subprocess.run(
            ["git", "-C", str(base), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=4,
        )
        remote = subprocess.run(
            ["git", "-C", str(base), "remote", "get-url", "origin"],
            check=False,
            capture_output=True,
            text=True,
            timeout=4,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if top.returncode != 0 or head.returncode != 0:
        return {}
    top_path = _safe_resolve(Path(top.stdout.strip()))
    if top_path == _safe_resolve(ROOT):
        return {"repository": "this repository", "commit": head.stdout.strip(), "inside_hub": "true"}
    url = remote.stdout.strip()
    if "://" in url and "@" in url:
        scheme, remainder = url.split("://", 1)
        url = scheme + "://" + remainder.rsplit("@", 1)[-1]
    return {"repository": url or "unknown", "commit": head.stdout.strip(), "inside_hub": "false"}


def _looks_like_source(path: Path) -> bool:
    if not path.is_dir():
        return path.suffix.casefold() in {".py", ".js", ".ts", ".ps1", ".cmd", ".bat"}
    try:
        names = {entry.name.casefold() for entry in path.iterdir()}
    except OSError:
        return False
    markers = {"pyproject.toml", "setup.py", "package.json", "cargo.toml", "src", "tools", "workflows"}
    if names & markers:
        return True
    return any(name.endswith((".sln", ".csproj", ".py", ".js", ".ts")) for name in names)


def _classification(path: Path, field: str) -> tuple[str, str]:
    parts = {part.casefold() for part in path.parts}
    suffix = path.suffix.casefold()
    if field == "environment" or {"env", ".venv", "venv", "environments"} & parts:
        return "ENV", "environment path"
    if field in {"model", "checkpoint"} or suffix in MODEL_SUFFIXES or {"models", "checkpoints"} & parts:
        return "MODEL", "model or checkpoint path"
    if {"cache", "caches", "huggingface"} & parts:
        return "CACHE", "cache path"
    if field == "runtime" or {"node_modules", "dist", "build", "runtime", "runtimes", "__pycache__"} & parts:
        return "BUILD_ARTIFACT", "generated or managed runtime path"
    if suffix in MEDIA_SUFFIXES or {"archive", "media", "output", "outputs", "uploads"} & parts:
        return "USER_DATA", "media or output path"
    metadata = _git_metadata(path)
    if metadata and metadata.get("inside_hub") == "false":
        return "UPSTREAM_CLONE", "{repository} @ {commit}".format(**metadata)
    if _looks_like_source(path):
        if metadata.get("inside_hub") == "true":
            return "FIRST_PARTY_SOURCE", "source candidate inside Local AI Hub; review Git tracking before copying"
        return "FIRST_PARTY_SOURCE", "source-like path without upstream Git metadata"
    return "USER_DATA", "external or installer-managed non-source path"


def _suggest_target(path: Path) -> str:
    name = path.name.casefold()
    if "workflow" in name:
        return "workflows/"
    if "patch" in name:
        return "patches/"
    return "src/tools/"


def _tracked_status(path: Path) -> str:
    if not _under(path, ROOT):
        return "outside_repository"
    try:
        relative = str(path.resolve().relative_to(ROOT.resolve()))
        result = subprocess.run(
            ["git", "ls-files", "--", relative],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=4,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return "tracked" if result.stdout.strip() else "not_tracked_as_path"


def _candidate_directories(root: Path, max_depth: int, max_directories: int) -> Iterable[Path]:
    queue: list[Path] = [root]
    visited = 0
    while queue and visited < max_directories:
        current = queue.pop(0)
        visited += 1
        yield current
        try:
            depth = len(current.relative_to(root).parts)
        except ValueError:
            continue
        if depth >= max_depth:
            continue
        try:
            children = list(current.iterdir())
        except OSError:
            continue
        for child in children:
            if child.name.casefold() in SKIP_DIRECTORY_NAMES or child.is_symlink():
                continue
            try:
                if child.is_dir():
                    queue.append(child)
            except OSError:
                continue


def _config_roots(path: Path) -> tuple[list[Path], int, int]:
    value = _read_json(path)
    roots: list[Path] = []
    raw_roots = value.get("approved_roots", []) if isinstance(value.get("approved_roots"), list) else []
    for item in raw_roots:
        raw = item.get("path") if isinstance(item, dict) else item
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            roots.append(Path(os.path.expandvars(raw)).expanduser())
        except (OSError, ValueError):
            continue
    depth = int(value.get("max_depth", 4)) if value else 4
    directories = int(value.get("max_directories", 1200)) if value else 1200
    return roots, max(0, min(depth, 8)), max(1, min(directories, 5000))


def audit(roots: list[Path], max_depth: int, max_directories: int) -> tuple[list[dict[str, str]], list[str]]:
    records: list[dict[str, str]] = []
    skipped: list[str] = []
    seen: set[str] = set()

    def add(path: Path, source: str, field: str) -> None:
        resolved = _safe_resolve(path)
        key = str(resolved).casefold()
        if key in seen:
            return
        seen.add(key)
        if not _on_d_drive(resolved):
            skipped.append(f"{source}:{field} is outside D: and was not scanned: {resolved}")
            return
        classification, evidence = _classification(resolved, field)
        assert classification in CLASSIFICATIONS
        action = {
            "FIRST_PARTY_SOURCE": f"Review license, secrets, host paths, and tracking. Copy only sanitized necessary files to {_suggest_target(resolved)} after review.",
            "UPSTREAM_CLONE": "Do not vendor. Record repository and commit in dependencies.lock.json; retain only reviewed local patches.",
            "BUILD_ARTIFACT": "Keep outside Git; regenerate from tracked source.",
            "MODEL": "Keep outside Git; do not duplicate, move, or download during source audit.",
            "ENV": "Keep outside Git; do not move or delete during source audit.",
            "CACHE": "Keep outside Git; do not bulk-clear existing cache.",
            "USER_DATA": "Keep outside Git; do not move or delete user or installer-managed data.",
        }[classification]
        records.append(
            {
                "source": source,
                "field": field,
                "path": str(resolved),
                "classification": classification,
                "tracking": _tracked_status(resolved),
                "evidence": evidence,
                "action": action,
            }
        )

    for source, field, path in _configured_paths():
        add(path, source, field)
    for root in roots:
        resolved_root = _safe_resolve(root)
        if not resolved_root.is_dir():
            skipped.append(f"approved root does not exist: {resolved_root}")
            continue
        if not _on_d_drive(resolved_root):
            skipped.append(f"approved root is outside D: and was not scanned: {resolved_root}")
            continue
        for candidate in _candidate_directories(resolved_root, max_depth, max_directories):
            if candidate == resolved_root or _looks_like_source(candidate):
                add(candidate, "approved_root", "path")
    return sorted(records, key=lambda item: (item["classification"], item["path"].casefold())), skipped


def write_report(records: list[dict[str, str]], skipped: list[str], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Local AI Hub V5 - Source Audit (local only)",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "This report is read-only. It does not approve copying, migration, relocation, deletion, archiving, or vendoring.",
        "",
        "| Classification | Source | Local path | Tracking | Evidence | Next action |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in records:
        lines.append("| {classification} | {source}:{field} | {path} | {tracking} | {evidence} | {action} |".format(**item))
    if not records:
        lines.append("| - | - | No approved D: candidate found | - | - | Configure an approved root and run again. |")
    if skipped:
        lines.extend(["", "## Skipped", ""])
        lines.extend(f"- {item}" for item in skipped)
    lines.extend(
        [
            "",
            "FIRST_PARTY_SOURCE is only a candidate. Sanitize and review a minimal selected file before adding it to Git.",
            "UPSTREAM_CLONE is never copied into Local AI Hub; retain repository, commit and reviewed patches only.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a read-only local V5 source audit for approved D: roots.")
    parser.add_argument("--config", type=Path, default=LOCAL_CONFIG, help="Ignored local approved-root JSON.")
    parser.add_argument("--root", action="append", default=[], type=Path, help="Additional explicit approved D: root; may be repeated.")
    parser.add_argument("--max-depth", type=int, default=None)
    parser.add_argument("--max-directories", type=int, default=None)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT, help="Ignored local Markdown output.")
    parser.add_argument("--json", action="store_true", help="Print a compact JSON summary after writing the report.")
    args = parser.parse_args()

    config_roots, config_depth, config_directories = _config_roots(args.config)
    roots = [*config_roots, *args.root]
    depth = args.max_depth if args.max_depth is not None else config_depth
    directories = args.max_directories if args.max_directories is not None else config_directories
    records, skipped = audit(roots, max(0, min(depth, 8)), max(1, min(directories, 5000)))
    report = write_report(records, skipped, args.report)
    summary = {
        "report": str(report),
        "records": len(records),
        "first_party_source": sum(item["classification"] == "FIRST_PARTY_SOURCE" for item in records),
        "upstream_clone": sum(item["classification"] == "UPSTREAM_CLONE" for item in records),
        "skipped": len(skipped),
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print("Wrote local V5 source audit: {report}".format(**summary))
        print("Candidates: {records}; first-party: {first_party_source}; upstream clones: {upstream_clone}; skipped: {skipped}".format(**summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
