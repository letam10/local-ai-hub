"""Canonical one-command clean-clone setup for Local AI Hub V7.

The default command is inspect/plan-only. ``--apply`` creates only missing
core folders/configuration through the existing absent-only bootstrap and then
optionally updates the managed desktop shortcut.  It never installs models,
downloads runtimes, changes system Python/CUDA, or touches user data.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.platform.paths import get_paths
from src.services.bootstrap_core import apply_core_bootstrap, inspect_core, plan_core_bootstrap, verify_core
from src.services.productization import ProductionCatalog


def inspect_setup(*, app_root: Path = ROOT, data_root: Path | None = None) -> dict[str, Any]:
    paths = get_paths(app_root=app_root, data_root=data_root)
    core = inspect_core(paths=paths, allow_system=True)
    catalog = ProductionCatalog(paths=paths).snapshot()
    return {
        "schema_version": "v7-setup-inspect.v1",
        "status": "available" if core["core_runtime"].get("status") == "AVAILABLE" else "blocked",
        "execution": "not_run",
        "dry_run": True,
        "paths": paths.safe_projection(),
        "core": core,
        "catalog": {"models": catalog["counts"]["models"], "runtimes": catalog["counts"]["runtimes"], "installed_models": catalog["counts"]["installed_models"]},
        "model_downloads": False,
        "runtime_installs": False,
        "next_action": "Run setup_local_ai_hub.ps1 -Apply only after reviewing the plan." if core["core_runtime"].get("status") == "AVAILABLE" else "Install a supported Core Python environment, then rerun setup.",
    }


def plan_setup(*, app_root: Path = ROOT, data_root: Path | None = None) -> dict[str, Any]:
    paths = get_paths(app_root=app_root, data_root=data_root)
    core_plan = plan_core_bootstrap(paths=paths, initialize_config=True, allow_system=True)
    inspection = inspect_setup(app_root=app_root, data_root=data_root)
    return {"schema_version": "v7-setup-plan.v1", "status": core_plan["status"], "execution": "not_run", "dry_run": True, "plan_id": core_plan["plan_id"], "plan_fingerprint": core_plan["plan_fingerprint"], "inspection": inspection, "core_plan": core_plan, "desktop_shortcut": "managed_shortcut_after_apply", "optional_ai": "not_installed", "next_action": core_plan["next_action"]}


def apply_setup(plan: dict[str, Any], *, app_root: Path = ROOT, data_root: Path | None = None, confirmed: bool = False) -> dict[str, Any]:
    if not confirmed:
        return {"status": "waiting_confirmation", "execution": "not_run", "dry_run": True}
    if not isinstance(plan, dict) or plan.get("schema_version") != "v7-setup-plan.v1":
        return {"status": "error", "code": "invalid_setup_plan", "execution": "not_run"}
    paths = get_paths(app_root=app_root, data_root=data_root)
    result = apply_core_bootstrap(plan["core_plan"], paths=paths, confirmed=True)
    if result.get("status") not in {"ready", "completed"}:
        return {"status": "failed", "code": "core_bootstrap_failed", "execution": "not_run", "result": result}
    return {"schema_version": "v7-setup-result.v1", "status": "ready", "execution": "completed", "dry_run": False, "core": result, "verification": verify_core(paths=paths), "desktop_shortcut": "run scripts/update_managed_shortcuts.ps1 -Apply after Hub-capable pythonw verification", "optional_ai": "not_installed", "next_action": "Double-click the managed Local AI Hub shortcut. Install optional components from the Components page."}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local AI Hub V7 setup (plan by default)")
    parser.add_argument("--apply", action="store_true", help="Apply absent-only Core setup")
    parser.add_argument("--data-root", type=Path, default=None)
    args = parser.parse_args(argv)
    plan = plan_setup(data_root=args.data_root)
    result = apply_setup(plan, data_root=args.data_root, confirmed=True) if args.apply else plan
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if result.get("status") in {"planned", "ready"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
