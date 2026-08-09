"""Read-only classification of configured local paths used by Local AI Hub.

The report is deliberately local/ignored because it may contain workstation
paths.  It never moves, deletes, copies, vendors, or patches an installation.
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
REPORT_PATH = ROOT / "Reports" / "SOURCE_AUDIT_V4.local.md"
CLASSIFICATIONS = {"FIRST_PARTY_SOURCE", "UPSTREAM_CLONE", "BUILD_ARTIFACT", "MODEL", "ENV", "CACHE", "USER_DATA"}
PATH_KEYS = {"path", "environment", "executable", "working_directory", "local_path", "runtime", "model", "checkpoint"}
MODEL_SUFFIXES = {".pt", ".pth", ".ckpt", ".safetensors", ".onnx", ".gguf", ".bin"}
MEDIA_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mkv", ".wav", ".mp3", ".flac"}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _configuration_paths() -> Iterable[tuple[str, str, Path]]:
    sources = (
        ROOT / "Config" / "components.json",
        ROOT / "Config" / "application_registry.local.json",
        ROOT / "Config" / "model_registry.json",
    )
    for source in sources:
        value = _read_json(source)
        for item in _walk(value):
            if not isinstance(item, tuple):
                continue
            key, raw = item
            if key not in PATH_KEYS or not isinstance(raw, str) or not raw.strip() or raw.startswith("${"):
                continue
            try:
                path = Path(os.path.expandvars(raw)).expanduser()
            except (OSError, ValueError):
                continue
            yield source.name, key, path


def _walk(value: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key), child
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _under_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _git_metadata(path: Path) -> dict[str, str]:
    base = path if path.is_dir() else path.parent
    try:
        top_level = subprocess.run(["git", "-C", str(base), "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=5, check=False)
        head = subprocess.run(["git", "-C", str(base), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, check=False)
        remote = subprocess.run(["git", "-C", str(base), "remote", "get-url", "origin"], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return {}
    if top_level.returncode != 0 or head.returncode != 0:
        return {}
    try:
        if Path(top_level.stdout.strip()).resolve() == ROOT.resolve() and base.resolve() != ROOT.resolve():
            return {}
    except OSError:
        return {}
    url = remote.stdout.strip()
    # Do not emit an accidental user:password@ origin in a local report.
    if "@" in url and "://" in url:
        scheme, remainder = url.split("://", 1)
        url = scheme + "://" + remainder.rsplit("@", 1)[-1]
    return {"commit": head.stdout.strip(), "repository": url or "unknown"}


def _classification(path: Path, field: str) -> tuple[str, str]:
    lowered = {part.casefold() for part in path.parts}
    suffix = path.suffix.casefold()
    if field == "environment" or {"venv", ".venv", "env", "environments"} & lowered:
        return "ENV", "environment path"
    if field in {"model", "checkpoint"} or suffix in MODEL_SUFFIXES or "models" in lowered or "checkpoints" in lowered:
        return "MODEL", "model/checkpoint path"
    if {"cache", "caches", "huggingface"} & lowered:
        return "CACHE", "cache path"
    if {"node_modules", "dist", "build", "__pycache__"} & lowered:
        return "BUILD_ARTIFACT", "rebuildable build artifact"
    if suffix in MEDIA_SUFFIXES or {"output", "outputs", "media", "uploads", "archive"} & lowered:
        return "USER_DATA", "media/output path"
    metadata = _git_metadata(path)
    if metadata:
        return "UPSTREAM_CLONE", f"{metadata.get('repository', 'unknown')} @ {metadata.get('commit', 'unknown')}"
    if path.is_dir() and any((path / name).exists() for name in ("pyproject.toml", "setup.py", "package.json", "src")):
        return "FIRST_PARTY_SOURCE", "source-like directory without upstream Git metadata"
    return "USER_DATA", "external managed/non-source path"


def audit() -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for source, field, path in _configuration_paths():
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved == ROOT.resolve():
            continue
        key = (field, str(resolved).casefold())
        if key in seen:
            continue
        seen.add(key)
        classification, evidence = _classification(resolved, field)
        assert classification in CLASSIFICATIONS
        action = {
            "FIRST_PARTY_SOURCE": "Review, sanitize secrets/host paths, then move only the necessary source into src/, tools/, workflows/ or patches/.",
            "UPSTREAM_CLONE": "Do not vendor; ensure repository and commit are present in dependencies.lock.json, export local changes as patches/.",
            "BUILD_ARTIFACT": "Keep outside Git; regenerate from tracked source when needed.",
            "MODEL": "Keep outside Git; do not duplicate or relocate in this task.",
            "ENV": "Keep outside Git; do not move/delete while migration is incomplete.",
            "CACHE": "Keep outside Git; do not bulk-clear existing cache.",
            "USER_DATA": "Keep outside Git and do not move/delete user or installer-managed data.",
        }[classification]
        records.append({"config": source, "field": field, "path": str(resolved), "classification": classification, "evidence": evidence, "action": action})
    return sorted(records, key=lambda item: (item["classification"], item["path"].casefold()))


def write_report(records: list[dict[str, str]], path: Path = REPORT_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Local AI Hub V4 — Source Audit (local only)",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "This report is read-only. It does not approve migration, relocation, deletion, or vendoring.",
        "",
        "| Classification | Config field | Local path | Evidence | Next action |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in records:
        lines.append("| {classification} | {config}:{field} | `{path}` | {evidence} | {action} |".format(**item))
    if not records:
        lines.append("| — | — | No configured external important source path found | — | Re-run after local configuration changes. |")
    lines.extend(["", "Classifications are limited to FIRST_PARTY_SOURCE, UPSTREAM_CLONE, BUILD_ARTIFACT, MODEL, ENV, CACHE and USER_DATA.", ""])
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a local ignored V4 source audit report.")
    parser.add_argument("--report", type=Path, default=REPORT_PATH, help="Ignored local Markdown output path.")
    args = parser.parse_args()
    report = write_report(audit(), args.report)
    print(f"Wrote local source audit: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
