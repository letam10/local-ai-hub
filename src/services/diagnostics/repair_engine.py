"""
/*
  FILE NOTE
  - Mục đích: Desktop repair engine cho Local AI Hub — chẩn đoán 5 bước (Inspect, Report, Plan, Repair, Verify) cho core files/templates/shortcuts mà KHÔNG xóa hoặc sửa đổi mutable assets (Models, Environments, runtime, Output, .git)
  - Liên kết trực tiếp: src/services/diagnostics/center.py, src/app_config/settings_service.py, scripts/update_managed_shortcuts.ps1
  - Vùng ảnh hưởng khi sửa: Desktop Repair Center trong UI và CLI repair tool
*/
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.app_config.schema import SETTINGS_SCHEMA_VERSION, validate_settings
from src.shared.paths.registry import (
    CONFIG_ROOT,
    LOG_ROOT,
    OUTPUT_ROOT,
    ROOT,
    TEMP_ROOT,
    ensure_managed_directories,
)

HUB_ROOT = ROOT


@dataclass
class InspectionReport:
    healthy: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    malformed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class DesktopRepairEngine:
    """Safe 5-step repair coordinator for Local AI Hub core application."""

    def __init__(self, hub_root: Path | None = None) -> None:
        self.hub_root = (hub_root or HUB_ROOT).resolve()
        self.config_root = self.hub_root / "Config"

    # Step 1: Inspect
    def inspect(self) -> InspectionReport:
        report = InspectionReport()

        # 1. Managed directories
        managed_dirs = [
            self.config_root,
            self.hub_root / "Logs",
            self.hub_root / "Output",
            self.hub_root / "Temp",
            self.hub_root / "Cache",
        ]
        for d in managed_dirs:
            if d.exists() and d.is_dir():
                report.healthy.append(f"directory:{d.name}")
            else:
                report.missing.append(f"directory:{d.name}")

        # 2. Config templates and settings.json
        settings_file = self.config_root / "settings.json"
        if not settings_file.exists():
            report.missing.append("file:Config/settings.json")
        else:
            try:
                content = json.loads(settings_file.read_text(encoding="utf-8"))
                validated = validate_settings(content)
                report.healthy.append("file:Config/settings.json")
            except Exception as exc:
                report.malformed.append(f"file:Config/settings.json (invalid JSON/schema: {exc})")

        # 3. Core example templates
        example_configs = [
            "hub_config.example.json",
            "model_registry.example.json",
            "workflow_library.example.json",
            "asset_intelligence.example.json",
        ]
        for ex in example_configs:
            ex_file = self.config_root / ex
            if ex_file.exists():
                report.healthy.append(f"example_config:{ex}")
            else:
                report.missing.append(f"example_config:{ex}")

        # 4. Core source entrypoints
        core_files = [
            "src/app/main.py",
            "src/app/launcher.py",
            "src/app/desktop_lifecycle.py",
            "src/services/api/api_server.py",
            "src/ui/index.html",
            "src/ui/app.js",
            "src/ui/pages.js",
        ]
        for cf in core_files:
            target = self.hub_root / cf
            if target.exists():
                report.healthy.append(f"core_file:{cf}")
            else:
                report.missing.append(f"core_file:{cf}")

        return report

    # Step 2: Report summary
    def report(self) -> dict[str, Any]:
        insp = self.inspect()
        is_clean = len(insp.missing) == 0 and len(insp.malformed) == 0
        return {
            "status": "healthy" if is_clean else "repair_recommended",
            "summary": {
                "healthy_count": len(insp.healthy),
                "missing_count": len(insp.missing),
                "malformed_count": len(insp.malformed),
                "warnings_count": len(insp.warnings),
            },
            "healthy": insp.healthy,
            "missing": insp.missing,
            "malformed": insp.malformed,
            "warnings": insp.warnings,
        }

    # Step 3: Preview plan
    def plan_repair(self) -> dict[str, Any]:
        insp = self.inspect()
        actions: list[dict[str, str]] = []

        for item in insp.missing:
            if item.startswith("directory:"):
                dir_name = item.split(":", 1)[1]
                actions.append({
                    "action": "create_directory",
                    "target": dir_name,
                    "reason": f"Directory {dir_name} is missing.",
                })
            elif item == "file:Config/settings.json":
                actions.append({
                    "action": "create_default_settings",
                    "target": "Config/settings.json",
                    "reason": "Settings file is missing, will create safe defaults.",
                })

        for item in insp.malformed:
            if item.startswith("file:Config/settings.json"):
                actions.append({
                    "action": "backup_and_reset_settings",
                    "target": "Config/settings.json",
                    "reason": "Settings file is corrupt; will preserve as settings.json.corrupt and reset safe defaults.",
                })

        return {
            "repair_needed": len(actions) > 0,
            "action_count": len(actions),
            "actions": actions,
            "protected_roots": ["Models", "Environments", "runtime", "Output", ".git"],
        }

    # Step 4: Execute repair
    def execute_repair(self, *, confirmed: bool = False) -> dict[str, Any]:
        if not confirmed:
            return {"accepted": False, "status": "unconfirmed", "reason": "Repair requires explicit confirmation."}

        plan = self.plan_repair()
        applied: list[str] = []
        errors: list[str] = []

        for act in plan.get("actions", []):
            action_type = act["action"]
            target = act["target"]
            try:
                if action_type == "create_directory":
                    (self.hub_root / target).mkdir(parents=True, exist_ok=True)
                    applied.append(f"created directory: {target}")
                elif action_type == "create_default_settings":
                    from src.app_config.schema import SETTINGS_SECTION_DEFAULTS
                    default_settings = {
                        "schema_version": SETTINGS_SCHEMA_VERSION,
                        "settings_revision": 0,
                        **SETTINGS_SECTION_DEFAULTS,
                    }
                    settings_path = self.config_root / "settings.json"
                    settings_path.parent.mkdir(parents=True, exist_ok=True)
                    settings_path.write_text(json.dumps(default_settings, indent=2), encoding="utf-8")
                    applied.append("created default Config/settings.json")
                elif action_type == "backup_and_reset_settings":
                    settings_path = self.config_root / "settings.json"
                    if settings_path.exists():
                        corrupt_backup = self.config_root / "settings.json.corrupt"
                        shutil.copy2(settings_path, corrupt_backup)
                    from src.app_config.schema import SETTINGS_SECTION_DEFAULTS
                    default_settings = {
                        "schema_version": SETTINGS_SCHEMA_VERSION,
                        "settings_revision": 0,
                        **SETTINGS_SECTION_DEFAULTS,
                    }
                    settings_path.write_text(json.dumps(default_settings, indent=2), encoding="utf-8")
                    applied.append("reset Config/settings.json (backed up corrupt copy)")
            except Exception as err:
                errors.append(f"{target}: {err}")

        # Step 5: Verify
        post_report = self.report()

        return {
            "accepted": len(errors) == 0,
            "status": "completed" if len(errors) == 0 else "partial_failure",
            "applied": applied,
            "errors": errors,
            "post_repair_status": post_report["status"],
            "remaining_missing": post_report["missing"],
            "remaining_malformed": post_report["malformed"],
        }
