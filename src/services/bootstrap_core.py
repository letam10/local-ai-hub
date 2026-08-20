"""Model-free, offline core bootstrap for clean clones.

The function only creates ignored roots and missing local Config documents from
tracked examples. It never downloads models, installs module dependencies,
starts a process, probes a provider or overwrites an existing local file.
"""

from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any

from src.platform.paths import HubPaths, get_paths


CORE_CONFIG_EXAMPLES = {
    "app.json": "app.example.json",
    "hub_config.json": "hub_config.example.json",
    "components.json": "components.example.json",
    "model_registry.json": "model_registry.example.json",
    "workflow_library.json": "workflow_library.example.json",
    "module_manager.json": "module_manager.example.json",
}


@dataclass(frozen=True)
class BootstrapResult:
    schema_version: str
    status: str
    execution: str
    dry_run: bool
    config: tuple[dict[str, Any], ...]
    core_runtime: dict[str, Any]
    optional_ai: dict[str, Any]
    next_action: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "execution": self.execution,
            "dry_run": self.dry_run,
            "config": list(self.config),
            "core_runtime": self.core_runtime,
            "optional_ai": self.optional_ai,
            "next_action": self.next_action,
        }


def _safe_config_file(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return not path.is_symlink() and not root.is_symlink()


def _copy_if_absent(example: Path, target: Path, config_root: Path) -> str:
    if not example.is_file() or example.is_symlink() or not _safe_config_file(target, config_root):
        return "refused_unsafe_target"
    if target.exists():
        return "preserved_existing"
    config_root.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f".{target.name}.", suffix=".tmp", dir=config_root, delete=False) as handle:
            temp_path = Path(handle.name)
            handle.write(example.read_bytes())
            handle.flush()
            os.fsync(handle.fileno())
        # Recheck the absent-only precondition before publication.  A race is
        # never resolved by replacing a user's file.
        if target.exists() or target.is_symlink():
            return "preserved_intervening_file"
        os.replace(temp_path, target)
        temp_path = None
        return "created_from_example"
    except (OSError, ValueError):
        return "bootstrap_write_failed"
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def bootstrap_core(*, paths: HubPaths | None = None, initialize_config: bool = True) -> dict[str, Any]:
    paths = paths or get_paths()
    paths.config_root.mkdir(parents=True, exist_ok=True)
    for root in (paths.environments_root, paths.runtime_root, paths.models_root, paths.output_root, paths.cache_root, paths.temp_root, paths.log_root):
        root.mkdir(parents=True, exist_ok=True)
    config_results: list[dict[str, Any]] = []
    if initialize_config:
        for target_name, example_name in CORE_CONFIG_EXAMPLES.items():
            example = paths.app_root / "Config" / example_name
            target = paths.config_root / target_name
            state = _copy_if_absent(example, target, paths.config_root)
            config_results.append({"target": target_name, "state": state})
    core_ok = sys.version_info >= (3, 10)
    webview_available = importlib.util.find_spec("webview") is not None
    result = BootstrapResult(
        schema_version="core-bootstrap.v1",
        status="ready" if core_ok else "unavailable",
        execution="not_run",
        dry_run=False,
        config=tuple(config_results),
        core_runtime={"status": "available" if core_ok else "missing", "python_major": sys.version_info.major, "python_minor": sys.version_info.minor, "webview2": "available" if webview_available else "not_checked"},
        optional_ai={"status": "not_installed", "models_downloaded": False, "runtimes_started": False},
        next_action="Launch the desktop shell; install AI modules/models explicitly from Module Manager." if core_ok else "Provide Python 3.10+ for the core bootstrap.",
    )
    return result.as_dict()


__all__ = ["CORE_CONFIG_EXAMPLES", "BootstrapResult", "bootstrap_core"]
