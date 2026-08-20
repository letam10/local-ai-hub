"""Model-free, offline core bootstrap for clean clones.

The function only creates ignored roots and missing local Config documents from
tracked examples. It never downloads models, installs module dependencies,
starts a process, probes a provider or overwrites an existing local file.
"""

from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import json
import hashlib
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any

from src.platform.paths import HubPaths, get_paths
from src.services.runtime_manager.core_resolver import CoreRuntimeResolver


CORE_CONFIG_EXAMPLES = {
    "app.json": "app.example.json",
    "hub_config.json": "hub_config.example.json",
    "components.json": "components.example.json",
    "model_registry.json": "model_registry.example.json",
    "workflow_library.json": "workflow_library.example.json",
    "module_manager.json": "module_manager.example.json",
}
CORE_BOOTSTRAP_SCHEMA = "core-bootstrap.v2"
CORE_RUNTIME_VERSION = "v7-core-1"
CORE_DEPENDENCY_PROFILE = "core-requirements-v1"


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
        lexical_root = root.absolute()
        lexical = path.absolute()
        lexical.relative_to(lexical_root)
    except (OSError, ValueError):
        return False
    current = lexical
    while current.parent != current:
        if current.is_symlink():
            return False
        if os.name == "nt":
            try:
                import ctypes
                attrs = ctypes.windll.kernel32.GetFileAttributesW(str(current))
                attrs = int(attrs) & 0xFFFFFFFF
                if attrs != 0xFFFFFFFF and bool(attrs & 0x400):
                    return False
            except (AttributeError, OSError):
                pass
        current = current.parent
    try:
        lexical.resolve().relative_to(lexical_root.resolve())
    except (OSError, ValueError):
        return False
    return not path.is_symlink() and not root.is_symlink()


def _safe_directory(path: Path) -> bool:
    """Prove a fixed Hub root is not redirected before creating it."""

    current = path.absolute()
    while current.parent != current:
        if current.is_symlink():
            return False
        if os.name == "nt":
            try:
                import ctypes
                attrs = int(ctypes.windll.kernel32.GetFileAttributesW(str(current))) & 0xFFFFFFFF
                if attrs != 0xFFFFFFFF and bool(attrs & 0x400):
                    return False
            except (AttributeError, OSError):
                return False
        current = current.parent
    return not current.is_symlink()


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


def _json_fingerprint(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _stable_inspection_fingerprint(value: dict[str, Any]) -> str:
    """Fingerprint material state while ignoring volatile free-byte jitter."""

    snapshot = json.loads(json.dumps(value, ensure_ascii=True, sort_keys=True))
    disk = snapshot.get("disk") if isinstance(snapshot, dict) else None
    if isinstance(disk, dict) and isinstance(disk.get("free_bytes"), int):
        # A bootstrap writes only small Config/receipt files.  Bucket free
        # space to MiB so a filesystem allocation quantum cannot invalidate a
        # freshly reviewed plan, while a meaningful disk change still does.
        disk["free_bytes_bucket"] = disk["free_bytes"] // (1024 * 1024)
        disk.pop("free_bytes", None)
    return _json_fingerprint(snapshot)


def _webview2_state() -> dict[str, Any]:
    """Detect the Microsoft runtime separately from the Python webview binding."""

    if os.name != "nt":
        return {"status": "not_applicable", "reason": "WebView2 detection is Windows-specific."}
    try:
        import winreg

        keys = (
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\\Microsoft\\EdgeUpdate\\Clients\\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\\WOW6432Node\\Microsoft\\EdgeUpdate\\Clients\\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\\Microsoft\\EdgeUpdate\\Clients\\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
        )
        for hive, subkey in keys:
            try:
                with winreg.OpenKey(hive, subkey) as handle:
                    version, _ = winreg.QueryValueEx(handle, "pv")
                if isinstance(version, str) and version:
                    return {"status": "available", "version": version}
            except OSError:
                continue
    except ImportError:
        pass
    return {"status": "missing", "reason": "Microsoft Edge WebView2 Runtime was not found in the supported registry locations.", "next_action": "Install the official Microsoft Evergreen WebView2 Runtime."}


def inspect_core(*, paths: HubPaths | None = None, allow_system: bool = True) -> dict[str, Any]:
    paths = paths or get_paths()
    resolver = CoreRuntimeResolver(paths=paths, allow_system=allow_system)
    runtime = resolver.inspect()
    try:
        free_bytes = shutil.disk_usage(paths.data_root).free
    except OSError:
        free_bytes = 0
    return {
        "schema_version": "core-inspect.v1",
        "core_runtime": runtime,
        "python_binding": {"status": "available" if importlib.util.find_spec("webview") is not None else "missing", "package": "pywebview"},
        "webview2_runtime": _webview2_state(),
        "disk": {"location_class": "data_root", "free_bytes": max(0, int(free_bytes)), "status": "available" if free_bytes > 0 else "unknown"},
        "legacy_single_root_mode": paths.legacy_single_root_mode,
        "preserved_targets": sorted(CORE_CONFIG_EXAMPLES),
        "optional_ai": {"status": "not_installed", "models_downloaded": False, "runtimes_started": False},
        "execution": "not_run",
        "dry_run": True,
    }


def plan_core_bootstrap(*, paths: HubPaths | None = None, initialize_config: bool = True, allow_system: bool = True) -> dict[str, Any]:
    paths = paths or get_paths()
    inspection = inspect_core(paths=paths, allow_system=allow_system)
    body = {
        "schema_version": "core-bootstrap-plan.v1",
        "core_runtime_version": CORE_RUNTIME_VERSION,
        "dependency_profile_version": CORE_DEPENDENCY_PROFILE,
        "initialize_config": bool(initialize_config),
        "allow_system": bool(allow_system),
        "inspection_fingerprint": _stable_inspection_fingerprint(inspection),
        "data_root_class": "legacy_single_root" if paths.legacy_single_root_mode else "split_data_root",
        "preserve_existing_config": True,
        "required_writes": sorted(CORE_CONFIG_EXAMPLES) if initialize_config else [],
        "download_required": False,
        "estimated_size_bytes": 0,
    }
    fingerprint = _json_fingerprint(body)
    plan_id = f"core_plan_{fingerprint[:32]}"
    return {
        **body,
        "plan_id": plan_id,
        "plan_fingerprint": fingerprint,
        "status": "planned" if inspection["core_runtime"]["status"] == "AVAILABLE" else "blocked",
        "execution": "not_run",
        "dry_run": True,
        "reason": "Core bootstrap is an explicit plan; optional AI components are never included.",
        "next_action": "Confirm this plan to initialize missing Core files." if inspection["core_runtime"]["status"] == "AVAILABLE" else "Install a supported Core Python prerequisite before confirmation.",
    }


def verify_core(*, paths: HubPaths | None = None) -> dict[str, Any]:
    paths = paths or get_paths()
    receipt = paths.config_root / "core_install_receipt.json"
    inspection = inspect_core(paths=paths)
    receipt_state = "missing"
    receipt_value: dict[str, Any] = {}
    try:
        value = json.loads(receipt.read_text(encoding="utf-8"))
        if isinstance(value, dict) and value.get("schema_version") == "core-install-receipt.v1":
            receipt_state, receipt_value = "valid", value
    except (OSError, UnicodeError, json.JSONDecodeError):
        receipt_state = "invalid"
    config_state = {name: "present" if (paths.config_root / name).is_file() else "missing" for name in CORE_CONFIG_EXAMPLES}
    ready = inspection["core_runtime"]["status"] == "AVAILABLE" and receipt_state in {"valid", "missing"}
    return {
        "schema_version": "core-verify.v1", "status": "ready" if ready else "unavailable", "execution": "not_run", "dry_run": True,
        "core_runtime": inspection["core_runtime"], "python_binding": inspection["python_binding"], "webview2_runtime": inspection["webview2_runtime"],
        "receipt": {"status": receipt_state, "location_class": "config_root", "core_runtime_version": receipt_value.get("core_runtime_version")},
        "config": config_state, "optional_ai": inspection["optional_ai"],
        "reason": "Core prerequisites are verified without loading optional AI runtimes." if ready else "Core runtime or required receipt is unavailable.",
    }


def apply_core_bootstrap(plan: dict[str, Any], *, paths: HubPaths | None = None, confirmed: bool = False) -> dict[str, Any]:
    """Apply only a fresh server-owned Core plan; never installs AI components."""

    paths = paths or get_paths()
    if not confirmed:
        return {"status": "waiting_confirmation", "execution": "not_run", "plan_id": plan.get("plan_id") if isinstance(plan, dict) else None}
    if not isinstance(plan, dict) or plan.get("schema_version") != "core-bootstrap-plan.v1":
        return {"status": "error", "code": "invalid_core_plan", "execution": "not_run"}
    body = {key: plan.get(key) for key in ("schema_version", "core_runtime_version", "dependency_profile_version", "initialize_config", "allow_system", "inspection_fingerprint", "data_root_class", "preserve_existing_config", "required_writes", "download_required", "estimated_size_bytes")}
    if _json_fingerprint(body) != plan.get("plan_fingerprint"):
        return {"status": "conflict", "code": "stale_core_plan", "execution": "not_run", "next_action": "Create a fresh Core plan."}
    fresh = inspect_core(paths=paths, allow_system=bool(plan.get("allow_system", True)))
    if _stable_inspection_fingerprint(fresh) != plan.get("inspection_fingerprint"):
        return {"status": "conflict", "code": "core_state_changed", "execution": "not_run", "next_action": "Create a fresh Core plan."}
    if fresh["core_runtime"]["status"] != "AVAILABLE":
        return {"status": "unavailable", "code": "core_runtime_missing", "execution": "not_run", "next_action": "Install a supported Core Python prerequisite manually."}
    fixed_roots = (paths.config_root, paths.environments_root, paths.runtime_root, paths.models_root, paths.output_root, paths.cache_root, paths.temp_root, paths.log_root, paths.report_root, paths.backup_root)
    if any(not _safe_directory(root) for root in fixed_roots):
        return {"status": "error", "code": "unsafe_data_root", "execution": "not_run", "dry_run": True}
    paths.config_root.mkdir(parents=True, exist_ok=True)
    for root in (paths.environments_root, paths.runtime_root, paths.models_root, paths.output_root, paths.cache_root, paths.temp_root, paths.log_root, paths.report_root, paths.backup_root):
        root.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    if plan.get("initialize_config"):
        for target_name, example_name in CORE_CONFIG_EXAMPLES.items():
            results.append({"target": target_name, "state": _copy_if_absent(paths.app_root / "Config" / example_name, paths.config_root / target_name, paths.config_root)})
    selected = CoreRuntimeResolver(paths=paths).resolve()
    receipt = {
        "schema_version": "core-install-receipt.v1", "core_runtime_version": CORE_RUNTIME_VERSION,
        "python_version": (
            f"{selected.version[0]}.{selected.version[1]}"
            if selected is not None and selected.version is not None
            else None
        ),
        "dependency_profile_version": CORE_DEPENDENCY_PROFILE, "installed_at": int(__import__("time").time()), "verified_at": int(__import__("time").time()),
        "installation_mode": "existing_or_development_runtime", "location_class": fresh["core_runtime"].get("location_class"), "catalog_fingerprint": None,
    }
    receipt_target = paths.config_root / "core_install_receipt.json"
    temporary = receipt_target.with_suffix(".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(receipt, ensure_ascii=True, sort_keys=True, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, receipt_target)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return {"schema_version": CORE_BOOTSTRAP_SCHEMA, "status": "ready", "execution": "completed", "dry_run": False, "config": results, "core_runtime": fresh["core_runtime"], "python_binding": fresh["python_binding"], "webview2_runtime": fresh["webview2_runtime"], "optional_ai": fresh["optional_ai"], "receipt": {"status": "written", "location_class": "config_root"}, "next_action": "Launch the desktop shell; install AI modules/models explicitly from Module Manager."}


def bootstrap_core(*, paths: HubPaths | None = None, initialize_config: bool = True, allow_system: bool = True) -> dict[str, Any]:
    paths = paths or get_paths()
    plan = plan_core_bootstrap(paths=paths, initialize_config=initialize_config, allow_system=allow_system)
    if plan["status"] != "planned":
        inspection = inspect_core(paths=paths, allow_system=allow_system)
        return {"schema_version": CORE_BOOTSTRAP_SCHEMA, "status": "unavailable", "execution": "not_run", "dry_run": True, "config": [], "core_runtime": inspection["core_runtime"], "python_binding": inspection["python_binding"], "webview2_runtime": inspection["webview2_runtime"], "optional_ai": inspection["optional_ai"], "next_action": plan["next_action"]}
    return apply_core_bootstrap(plan, paths=paths, confirmed=True)


__all__ = ["CORE_BOOTSTRAP_SCHEMA", "CORE_CONFIG_EXAMPLES", "CORE_DEPENDENCY_PROFILE", "CORE_RUNTIME_VERSION", "BootstrapResult", "apply_core_bootstrap", "bootstrap_core", "inspect_core", "plan_core_bootstrap", "verify_core"]
