"""
/*
  FILE NOTE
  - Mục đích: Safe uninstaller specification cho Local AI Hub — chỉ gỡ bỏ app binaries và shortcuts, BẢO TOÀN toàn bộ Models, Environments, runtime, Output, Config, Projects, Backups, Reports
  - Liên kết trực tiếp: distribution/installer.iss, src/shared/paths/registry.py
  - Vùng ảnh hưởng khi sửa: Quy trình gỡ cài đặt ứng dụng an toàn
*/
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PROTECTED_DATA_DIRECTORIES = frozenset({
    "Config",
    "Models",
    "Environments",
    "runtime",
    "Output",
    "Cache",
    "Reports",
    "Projects",
    "Backups",
    ".git",
})

REMOVABLE_APP_COMPONENTS = frozenset({
    "src",
    "scripts",
    "distribution",
    "docs",
    "requirements-hub.txt",
    "dependencies.lock.json",
    "README.md",
    "LICENSES.md",
})


def plan_uninstall(hub_root: Path = ROOT) -> dict[str, Any]:
    """Return explicit plan of what will be removed and what will be preserved."""
    root = Path(hub_root).resolve()
    removable = []
    preserved = []

    for item in REMOVABLE_APP_COMPONENTS:
        target = root / item
        if target.exists():
            removable.append(str(target.relative_to(root)))

    for item in PROTECTED_DATA_DIRECTORIES:
        target = root / item
        if target.exists():
            preserved.append(str(target.relative_to(root)))

    return {
        "hub_root": str(root),
        "policy": "Safe uninstall preserves all user data, models, environments, and configuration by default.",
        "removable_app_components": removable,
        "preserved_user_data": preserved,
        "destructive_wipe_authorized": False,
    }


def execute_safe_uninstall(hub_root: Path = ROOT, *, confirmed: bool = False) -> dict[str, Any]:
    """Execute removal of application components only, never user data."""
    if not confirmed:
        return {"accepted": False, "status": "unconfirmed", "reason": "Uninstall requires explicit confirmation."}

    plan = plan_uninstall(hub_root)
    root = Path(hub_root).resolve()
    removed = []
    errors = []

    for rel in plan["removable_app_components"]:
        target = root / rel
        try:
            if target.is_dir() and not target.is_symlink():
                # Remove only if inside hub root
                if root in target.resolve().parents:
                    shutil.rmtree(target)
                    removed.append(rel)
            elif target.is_file():
                target.unlink(missing_ok=True)
                removed.append(rel)
        except Exception as err:
            errors.append(f"{rel}: {err}")

    return {
        "accepted": len(errors) == 0,
        "status": "completed" if len(errors) == 0 else "partial_failure",
        "removed_components": removed,
        "preserved_user_data": plan["preserved_user_data"],
        "errors": errors,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Local AI Hub Safe Uninstaller")
    parser.add_argument("--plan", action="store_true", default=True, help="Display uninstallation plan")
    parser.add_argument("--apply", action="store_true", help="Execute safe uninstallation")
    args = parser.parse_args()

    if not args.apply:
        print(json.dumps(plan_uninstall(), indent=2, ensure_ascii=False))
    else:
        res = execute_safe_uninstall(confirmed=True)
        print(json.dumps(res, indent=2, ensure_ascii=False))
