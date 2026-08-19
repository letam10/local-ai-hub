"""
  FILE NOTE
  - Muc dich: BackupManager tao/kiem tra/khoi phuc backup machine-local Hub state (settings, projects, workflow, node drafts). Khong backup Models/Environments/runtime/Output.
  - Lien ket truc tiep: src/app_config/settings_service.py, src/services/project_manager/manager.py, src/services/workflow_library/library.py, src/services/node_studio/state.py, src/shared/paths/registry.py
  - Vung anh huong khi sua: Backup/restore settings, creative workspace, workflow library va node studio drafts; secret scrubbing; checksum verification
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tempfile
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.shared.paths.registry import CONFIG_ROOT

BACKUP_SCHEMA_VERSION = 1
_BACKUP_DIR = CONFIG_ROOT / "backups"
_MAX_BACKUP_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB guard

# Data classes included in a standard backup.
DATA_CLASSES = ("settings", "creative_workspace", "workflow_library", "node_studio_drafts")

# Secret patterns to scrub from JSON values before backup.
_SECRET_KEY_RE = re.compile(
    r"api[_-]?key|token|password|secret|credential|private[_-]?key|access[_-]?key|refresh[_-]?token",
    re.IGNORECASE,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _scrub(obj: Any) -> Any:
    """Deep-scrub known secret keys from a JSON-serialisable object."""
    if isinstance(obj, dict):
        return {k: ("[REDACTED]" if _SECRET_KEY_RE.search(k) else _scrub(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub(item) for item in obj]
    return obj


def _safe_json(path: Path) -> bytes:
    """Read + parse + scrub a JSON file, returning sanitised UTF-8 bytes."""
    try:
        raw = json.loads(path.read_bytes())
        cleaned = _scrub(raw)
        return json.dumps(cleaned, ensure_ascii=False, indent=2).encode("utf-8")
    except (OSError, json.JSONDecodeError):
        return json.dumps({"status": "unreadable", "path": path.name}).encode("utf-8")


def _collect_files() -> dict[str, Path]:
    """Map archive member name -> local path for all backed-up files."""
    files: dict[str, Path] = {}

    settings_path = CONFIG_ROOT / "settings.json"
    if settings_path.exists():
        files["settings.json"] = settings_path

    workspace_path = CONFIG_ROOT / "creative_workspace.json"
    if workspace_path.exists():
        files["creative_workspace.json"] = workspace_path

    workflow_path = CONFIG_ROOT / "workflow_library.json"
    if workflow_path.exists():
        files["workflow_library.json"] = workflow_path

    for draft in sorted(CONFIG_ROOT.glob("node_studio_draft_*.json")):
        files[f"drafts/{draft.name}"] = draft

    for draft in sorted(CONFIG_ROOT.glob("draft_*.json")):
        files[f"drafts/{draft.name}"] = draft

    return files


class BackupManager:
    """Create, inspect, plan and apply atomic backups for Local AI Hub machine-local state."""

    def __init__(self, backup_dir: Path = _BACKUP_DIR) -> None:
        self._backup_dir = Path(backup_dir)
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create_backup(self) -> dict[str, Any]:
        """Create a timestamped ZIP backup under backup_dir.

        Returns `{"accepted": bool, "backup_path": str, "manifest": dict}`.
        """
        with self._lock:
            self._backup_dir.mkdir(parents=True, exist_ok=True)
            timestamp = _now_iso().replace(":", "-").replace("+", "p")[:26]
            zip_name = f"hub-backup-{timestamp}.zip"
            zip_path = self._backup_dir / zip_name
            tmp: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    dir=self._backup_dir,
                    prefix=".backup-",
                    suffix=".zip.tmp",
                    delete=False,
                ) as handle:
                    tmp = Path(handle.name)

                files = _collect_files()
                manifest: dict[str, Any] = {
                    "schema_version": BACKUP_SCHEMA_VERSION,
                    "created_at": _now_iso(),
                    "hub_backup_version": "1.0",
                    "included_data_classes": list(DATA_CLASSES),
                    "files": {},
                }

                with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                    for member, path in files.items():
                        content = _safe_json(path)
                        manifest["files"][member] = {
                            "sha256": _sha256_bytes(content),
                            "size_bytes": len(content),
                        }
                        zf.writestr(member, content)
                    manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
                    zf.writestr("manifest.json", manifest_bytes)

                os.replace(tmp, zip_path)
                tmp = None
                return {"accepted": True, "backup_path": str(zip_path), "manifest": manifest}
            except (OSError, zipfile.BadZipFile, ValueError) as exc:
                return {"accepted": False, "reason": str(exc)}
            finally:
                if tmp is not None:
                    try:
                        tmp.unlink()
                    except OSError:
                        pass

    # ------------------------------------------------------------------
    # Inspect
    # ------------------------------------------------------------------

    def inspect_backup(self, backup_path: Path) -> dict[str, Any]:
        """Parse a backup ZIP and verify checksums.

        Returns `{"valid": bool, "manifest": dict|None, "errors": list[str]}`.
        """
        backup_path = Path(backup_path)
        errors: list[str] = []
        if not backup_path.exists():
            return {"valid": False, "manifest": None, "errors": ["Backup file not found."]}
        try:
            with zipfile.ZipFile(backup_path, "r") as zf:
                if "manifest.json" not in zf.namelist():
                    return {"valid": False, "manifest": None, "errors": ["manifest.json missing from backup."]}
                manifest = json.loads(zf.read("manifest.json"))
                if not isinstance(manifest, dict) or manifest.get("schema_version") != BACKUP_SCHEMA_VERSION:
                    return {"valid": False, "manifest": None, "errors": ["manifest.json schema_version invalid or missing."]}
                for member, meta in (manifest.get("files") or {}).items():
                    if member not in zf.namelist():
                        errors.append(f"{member}: missing in ZIP.")
                        continue
                    content = zf.read(member)
                    actual = _sha256_bytes(content)
                    if actual != meta.get("sha256"):
                        errors.append(f"{member}: checksum mismatch (expected {meta.get('sha256')[:8]}…, got {actual[:8]}…).")
        except (zipfile.BadZipFile, json.JSONDecodeError, OSError) as exc:
            return {"valid": False, "manifest": None, "errors": [str(exc)]}
        return {"valid": len(errors) == 0, "manifest": manifest, "errors": errors}

    # ------------------------------------------------------------------
    # Plan
    # ------------------------------------------------------------------

    def plan_restore(self, backup_path: Path) -> dict[str, Any]:
        """Compare backup with current state and produce a restore plan.

        Returns `{"accepted": bool, "changes": list, "newer_protected": list, "preview": dict}`.
        """
        inspect = self.inspect_backup(backup_path)
        if not inspect["valid"]:
            return {"accepted": False, "reason": "Backup không hợp lệ hoặc checksum lỗi.", "errors": inspect["errors"]}

        manifest = inspect["manifest"]
        changes: list[dict[str, Any]] = []
        newer_protected: list[str] = []

        for member in (manifest.get("files") or {}):
            clean_member = re.sub(r"[^a-zA-Z0-9._/-]", "_", member)
            target = CONFIG_ROOT / clean_member
            if target.exists():
                try:
                    current_mtime = target.stat().st_mtime
                    backup_ts = manifest.get("created_at", "")
                    backup_epoch = datetime.fromisoformat(backup_ts).timestamp() if backup_ts else 0.0
                    if current_mtime > backup_epoch + 5:
                        newer_protected.append(clean_member)
                        changes.append({"member": clean_member, "action": "skip_newer", "reason": "File local mới hơn backup."})
                        continue
                except (OSError, ValueError):
                    pass
                changes.append({"member": clean_member, "action": "overwrite", "reason": "File sẽ được ghi đè từ backup."})
            else:
                changes.append({"member": clean_member, "action": "create", "reason": "File chưa tồn tại, sẽ được tạo mới."})

        return {
            "accepted": True,
            "backup_path": str(backup_path),
            "changes": changes,
            "newer_protected": newer_protected,
            "preview": {
                "total": len(changes),
                "overwrite": sum(1 for c in changes if c["action"] == "overwrite"),
                "create": sum(1 for c in changes if c["action"] == "create"),
                "skip_newer": sum(1 for c in changes if c["action"] == "skip_newer"),
            },
        }

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    def apply_restore(self, backup_path: Path, *, plan: dict[str, Any], confirmed: bool) -> dict[str, Any]:
        """Apply a restore plan atomically.

        Returns `{"accepted": bool, "applied": list, "skipped": list}`.
        """
        if not confirmed:
            return {"accepted": False, "reason": "Restore chưa được xác nhận. Truyền confirmed=True sau khi user xem plan."}
        if not plan.get("accepted"):
            return {"accepted": False, "reason": "Plan không hợp lệ."}

        backup_path = Path(backup_path)
        applied: list[str] = []
        skipped: list[str] = []

        try:
            with zipfile.ZipFile(backup_path, "r") as zf:
                for change in plan.get("changes", []):
                    member = change["member"]
                    action = change["action"]
                    if action == "skip_newer":
                        skipped.append(member)
                        continue
                    # Reject any member with path traversal before resolving.
                    if ".." in Path(member).parts or re.search(r"\.\.[/\\]", member):
                        skipped.append(f"{member} (path traversal guard)")
                        continue
                    # Strip leading slashes to keep within CONFIG_ROOT.
                    clean = re.sub(r"^[/\\]+", "", member)
                    target = (CONFIG_ROOT / clean).resolve()
                    config_root_resolved = CONFIG_ROOT.resolve()
                    if not str(target).startswith(str(config_root_resolved)):
                        skipped.append(f"{member} (path traversal guard)")
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    content = zf.read(member)
                    tmp: Path | None = None
                    try:
                        with tempfile.NamedTemporaryFile(
                            dir=target.parent,
                            prefix=".restore-",
                            suffix=".tmp",
                            delete=False,
                        ) as handle:
                            tmp = Path(handle.name)
                            handle.write(content)
                        os.replace(tmp, target)
                        tmp = None
                        applied.append(member)
                    except OSError:
                        skipped.append(f"{member} (write error)")
                    finally:
                        if tmp is not None:
                            try:
                                tmp.unlink()
                            except OSError:
                                pass
        except (zipfile.BadZipFile, OSError) as exc:
            return {"accepted": False, "reason": str(exc)}

        return {"accepted": True, "applied": applied, "skipped": skipped}

    # ------------------------------------------------------------------
    # Verify
    # ------------------------------------------------------------------

    def verify_restore(self, result: dict[str, Any]) -> dict[str, Any]:
        """Post-apply integrity check: re-read applied files and parse as JSON."""
        if not result.get("accepted"):
            return {"valid": False, "reason": "Restore was not accepted."}
        errors: list[str] = []
        for member in result.get("applied", []):
            clean = re.sub(r"\.\.[/\\]|^[/\\]+", "", member)
            target = CONFIG_ROOT / clean
            try:
                json.loads(target.read_bytes())
            except (OSError, json.JSONDecodeError) as exc:
                errors.append(f"{member}: {exc}")
        return {"valid": len(errors) == 0, "errors": errors}
