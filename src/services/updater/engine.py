"""
/*
  FILE NOTE
  - Mục đích: Update engine cho Local AI Hub — cập nhật an toàn application binaries mà không xóa/ghi đè machine-local data (Config, Models, Environments, runtime, Output, Projects, Backups, Reports)
  - Liên kết trực tiếp: src/app_config/settings_service.py, src/shared/paths/registry.py, distribution/release_manifest.json
  - Vùng ảnh hưởng khi sửa: Quá trình update/upgrade ứng dụng, bảo toàn dữ liệu người dùng
*/
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.shared.paths.registry import CONFIG_ROOT, ROOT

HUB_ROOT = ROOT

# Protected directories that MUST NEVER be deleted or overwritten during update
PROTECTED_MUTABLE_ROOTS = frozenset({
    "Config",
    "Models",
    "Environments",
    "runtime",
    "Output",
    "Cache",
    "Logs",
    "Reports",
    "Projects",
    "Backups",
    "Modules",
})

# Protected specific forensic evidence files
PROTECTED_FORENSIC_FILES = frozenset({
    "Reports/p0_source_reconciliation.local.bundle",
    "Reports/p0_source_reconciliation_review_queue.local.json",
})

# Files / Directories owned by app code that can be updated
UPDATABLE_APP_ROOTS = frozenset({
    "src",
    "scripts",
    "distribution",
    "docs",
})


@dataclass(frozen=True)
class UpdateManifest:
    version: str
    git_commit: str
    build_timestamp: str
    target_platform: str = "windows-x64"
    app_files: list[str] = field(default_factory=list)


@dataclass
class UpdateResult:
    accepted: bool
    status: str
    reason: str = ""
    updated_files: list[str] = field(default_factory=list)
    preserved_roots: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class UpdateEngine:
    """Safely applies application code updates preserving all machine-local data."""

    def __init__(self, hub_root: Path | None = None) -> None:
        self.hub_root = (hub_root or HUB_ROOT).resolve()

    def inspect_update_package(self, update_zip_path: Path) -> dict[str, Any]:
        """Inspect update package without extracting, verifying safety bounds."""
        resolved = Path(update_zip_path).resolve()
        if not resolved.exists() or not resolved.is_file():
            return {"valid": False, "reason": "Update archive not found."}
        if not zipfile.is_zipfile(resolved):
            return {"valid": False, "reason": "File is not a valid zip archive."}

        errors: list[str] = []
        app_members: list[str] = []
        forbidden_members: list[str] = []

        try:
            with zipfile.ZipFile(resolved, "r") as zf:
                infolist = zf.infolist()
                if len(infolist) > 2000:
                    return {"valid": False, "reason": f"Too many members ({len(infolist)} > 2000)."}

                total_uncompressed = sum(info.file_size for info in infolist)
                if total_uncompressed > 200 * 1024 * 1024:  # 200MB limit for core app code
                    return {"valid": False, "reason": f"Decompressed size too large ({total_uncompressed} bytes)."}

                for info in infolist:
                    name = info.filename.replace("\\", "/")
                    if ".." in name or name.startswith("/"):
                        return {"valid": False, "reason": f"Dangerous path traversal in archive: {name}"}

                    root_part = name.split("/")[0] if "/" in name else name
                    if root_part in PROTECTED_MUTABLE_ROOTS:
                        forbidden_members.append(name)
                    else:
                        app_members.append(name)

            if forbidden_members:
                errors.append(f"Archive contains protected mutable targets: {forbidden_members[:5]}")

            return {
                "valid": len(errors) == 0,
                "errors": errors,
                "app_files_count": len(app_members),
                "total_uncompressed_bytes": total_uncompressed,
                "protected_roots_intact": list(PROTECTED_MUTABLE_ROOTS),
            }
        except Exception as exc:
            return {"valid": False, "reason": f"Failed to inspect update archive: {exc}"}

    def plan_update(self, update_zip_path: Path) -> dict[str, Any]:
        """Create a dry-run plan for the update."""
        inspection = self.inspect_update_package(update_zip_path)
        if not inspection.get("valid"):
            return {"accepted": False, "status": "invalid_package", "reason": inspection.get("reason") or "Inspection failed."}

        return {
            "accepted": True,
            "status": "ready",
            "hub_root": str(self.hub_root),
            "files_to_update": inspection.get("app_files_count", 0),
            "protected_roots": list(PROTECTED_MUTABLE_ROOTS),
            "forensic_evidence_preserved": list(PROTECTED_FORENSIC_FILES),
        }

    def apply_update(self, update_zip_path: Path, *, confirmed: bool = False) -> UpdateResult:
        """Apply app code update while strictly preserving all user/machine data."""
        if not confirmed:
            return UpdateResult(
                accepted=False,
                status="unconfirmed",
                reason="Update requires explicit confirmation.",
            )

        plan = self.plan_update(update_zip_path)
        if not plan.get("accepted"):
            return UpdateResult(
                accepted=False,
                status="plan_rejected",
                reason=plan.get("reason", "Plan rejected."),
            )

        updated_files: list[str] = []
        errors: list[str] = []

        try:
            with zipfile.ZipFile(update_zip_path, "r") as zf:
                for member in zf.namelist():
                    clean_name = member.replace("\\", "/")
                    if ".." in clean_name or clean_name.startswith("/"):
                        continue
                    root_part = clean_name.split("/")[0] if "/" in clean_name else clean_name
                    if root_part in PROTECTED_MUTABLE_ROOTS:
                        continue  # Skip any protected mutable data

                    target_path = self.hub_root / clean_name
                    if clean_name.endswith("/"):
                        target_path.mkdir(parents=True, exist_ok=True)
                        continue

                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    # Atomic write
                    temp_path = target_path.with_name(f"{target_path.name}.update_tmp")
                    try:
                        with zf.open(member) as src_file, open(temp_path, "wb") as dst_file:
                            shutil.copyfileobj(src_file, dst_file)
                        temp_path.replace(target_path)
                        updated_files.append(clean_name)
                    except Exception as err:
                        if temp_path.exists():
                            temp_path.unlink(missing_ok=True)
                        errors.append(f"{clean_name}: {err}")

            return UpdateResult(
                accepted=len(errors) == 0,
                status="completed" if len(errors) == 0 else "partial_failure",
                reason="Update applied successfully." if len(errors) == 0 else "Some files failed to update.",
                updated_files=updated_files,
                preserved_roots=list(PROTECTED_MUTABLE_ROOTS),
                errors=errors,
            )
        except Exception as exc:
            return UpdateResult(
                accepted=False,
                status="error",
                reason=f"Update failed with exception: {exc}",
                errors=[str(exc)],
            )
