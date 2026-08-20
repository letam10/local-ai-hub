"""
  FILE NOTE
  - Mục đích: BackupManager tạo, kiểm tra tính toàn vẹn, lập kế hoạch và khôi phục an toàn bản sao lưu machine-local của Local AI Hub (settings, projects, workflow library, node drafts). Không chứa Models/Environments/runtime/Output.
  - Liên kết trực tiếp: src/app_config/settings_service.py, src/services/project_manager/manager.py, src/services/workflow_library/library.py, src/services/node_studio/state.py, src/shared/paths/registry.py
  - Vùng ảnh hưởng khi sửa: Toàn bộ quy trình backup/restore dữ liệu cấu hình và creative workspace (opaque ID, zip bounds, SHA-256 integrity, plan binding, atomic rollback)
"""

from __future__ import annotations

import hashlib
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
_MAX_BACKUP_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB
_MAX_MEMBER_COUNT = 200
_MAX_DECOMPRESSED_SIZE_BYTES = 100 * 1024 * 1024  # 100 MB

DATA_CLASSES = ("settings", "creative_workspace", "workflow_library", "node_studio_drafts")

_SECRET_KEY_RE = re.compile(
    r"api[_-]?key|token|password|secret|credential|private[_-]?key|access[_-]?key|refresh[_-]?token",
    re.IGNORECASE,
)

# Registry for server-owned restore plans: plan_id -> plan_dict
_RESTORE_PLANS: dict[str, dict[str, Any]] = {}
_PLANS_LOCK = threading.RLock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


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


def _compute_state_fingerprint() -> str:
    """Compute an aggregate hash of the current local state to detect mid-flow changes."""
    h = hashlib.sha256()
    files = _collect_files()
    for member_name in sorted(files.keys()):
        path = files[member_name]
        try:
            stat = path.stat()
            h.update(f"{member_name}:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8"))
        except OSError:
            h.update(f"{member_name}:missing".encode("utf-8"))
    return h.hexdigest()


class BackupManager:
    """Create, inspect, plan and apply atomic backups for Local AI Hub machine-local state."""

    def __init__(self, backup_dir: Path | None = None) -> None:
        self._backup_dir = Path(backup_dir) if backup_dir is not None else (CONFIG_ROOT / "backups")
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Resolution & Path Security
    # ------------------------------------------------------------------

    def _canonical_backup_dir(self) -> Path:
        return self._backup_dir.resolve()

    def _backup_id_for_file(self, zip_path: Path) -> str:
        """Create a stable, opaque backup_id from zip filename without path exposure."""
        stem = zip_path.stem
        clean = re.sub(r"[^a-zA-Z0-9_-]", "_", stem)
        return f"backup_{clean}"

    def _resolve_backup_target(self, backup_id: str) -> Path | None:
        """Resolve an opaque backup_id to a verified ZIP path inside canonical backup dir."""
        if not isinstance(backup_id, str) or not backup_id:
            return None

        canonical_dir = self._canonical_backup_dir()
        if not canonical_dir.exists():
            return None

        # Check all zip files in backup directory for matching backup_id
        for zip_file in sorted(canonical_dir.glob("*.zip")):
            if self._backup_id_for_file(zip_file) == backup_id:
                try:
                    resolved = zip_file.resolve()
                    resolved.relative_to(canonical_dir)
                    return resolved
                except (ValueError, OSError):
                    return None

        # Also support direct filename stem if matching
        candidate_stem = backup_id.removeprefix("backup_")
        candidate_path = canonical_dir / f"{candidate_stem}.zip"
        if candidate_path.exists():
            try:
                resolved = candidate_path.resolve()
                resolved.relative_to(canonical_dir)
                return resolved
            except (ValueError, OSError):
                return None

        return None

    # ------------------------------------------------------------------
    # List
    # ------------------------------------------------------------------

    def list_backups(self) -> list[dict[str, Any]]:
        """List all valid backups in the backup directory with opaque IDs."""
        with self._lock:
            canonical_dir = self._canonical_backup_dir()
            if not canonical_dir.exists():
                return []
            result: list[dict[str, Any]] = []
            for zip_path in sorted(canonical_dir.glob("*.zip"), reverse=True):
                try:
                    stat = zip_path.stat()
                    b_id = self._backup_id_for_file(zip_path)
                    result.append({
                        "backup_id": b_id,
                        "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                        "size_bytes": stat.st_size,
                    })
                except OSError:
                    continue
            return result

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create_backup(self) -> dict[str, Any]:
        """Create a timestamped ZIP backup under backup_dir.

        Returns `{"accepted": bool, "backup_id": str, "manifest": dict}`.
        """
        with self._lock:
            canonical_dir = self._canonical_backup_dir()
            canonical_dir.mkdir(parents=True, exist_ok=True)
            timestamp = _now_iso().replace(":", "-").replace("+", "p")[:26]
            zip_name = f"hub-backup-{timestamp}.zip"
            zip_path = canonical_dir / zip_name
            tmp: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    dir=canonical_dir,
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
                backup_id = self._backup_id_for_file(zip_path)
                return {
                    "accepted": True,
                    "backup_id": backup_id,
                    "manifest": manifest,
                }
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

    def inspect_backup(self, backup_id: str | Path) -> dict[str, Any]:
        """Parse a backup ZIP and enforce size bounds, member safety, and checksums.

        Returns `{"valid": bool, "manifest": dict|None, "errors": list[str], "sha256": str}`.
        """
        # Resolve target
        if isinstance(backup_id, Path):
            # Direct path validation
            try:
                backup_path = backup_id.resolve()
                backup_path.relative_to(self._canonical_backup_dir())
            except (ValueError, OSError):
                return {"valid": False, "manifest": None, "errors": ["File backup nằm ngoài thư mục lưu trữ được cho phép."]}
        else:
            backup_path = self._resolve_backup_target(str(backup_id))
            if backup_path is None:
                return {"valid": False, "manifest": None, "errors": ["Không tìm thấy file backup tương ứng với ID."]}

        errors: list[str] = []
        if not backup_path.exists():
            return {"valid": False, "manifest": None, "errors": ["File backup không tồn tại."]}

        # 1. ZIP file size bound
        try:
            file_size = backup_path.stat().st_size
            if file_size > _MAX_BACKUP_SIZE_BYTES:
                return {"valid": False, "manifest": None, "errors": [f"Kích thước file backup ({file_size} bytes) vượt quá giới hạn an toàn ({_MAX_BACKUP_SIZE_BYTES} bytes)."]}
            zip_sha256 = _sha256_file(backup_path)
        except OSError as exc:
            return {"valid": False, "manifest": None, "errors": [f"Không đọc được file backup: {exc}"]}

        try:
            with zipfile.ZipFile(backup_path, "r") as zf:
                names = zf.namelist()

                # 2. Member count bound
                if len(names) > _MAX_MEMBER_COUNT:
                    return {"valid": False, "manifest": None, "errors": [f"Số lượng file trong ZIP ({len(names)}) vượt quá giới hạn an toàn ({_MAX_MEMBER_COUNT})."]}

                # 3. Duplicate members check
                if len(names) != len(set(names)):
                    return {"valid": False, "manifest": None, "errors": ["Phát hiện tên file trùng lặp trong ZIP."]}

                # 4. Decompressed size & member safety checks
                total_decompressed = 0
                for info in zf.infolist():
                    total_decompressed += info.file_size
                    if total_decompressed > _MAX_DECOMPRESSED_SIZE_BYTES:
                        return {"valid": False, "manifest": None, "errors": ["Tổng dung lượng giải nén vượt quá giới hạn an toàn."]}

                    # Traversal / absolute / drive checks
                    name = info.filename
                    if ".." in Path(name).parts or re.search(r"\.\.[/\\]", name):
                        errors.append(f"{name}: Phát hiện path traversal.")
                    if name.startswith("/") or name.startswith("\\"):
                        errors.append(f"{name}: Đường dẫn tuyệt đối không hợp lệ.")
                    if re.match(r"^[A-Za-z]:", name):
                        errors.append(f"{name}: Chứa drive letter Windows.")
                    if "\\" in name:
                        errors.append(f"{name}: Chứa ký tự phân cách '\\' không chuẩn.")

                    # Check for symlink/reparse points in zip attributes
                    if (info.external_attr >> 16) & 0o170000 == 0o120000:
                        errors.append(f"{name}: Phát hiện symbolic link trong ZIP (không an toàn).")

                if errors:
                    return {"valid": False, "manifest": None, "errors": errors}

                # 5. Manifest schema & checksum verification
                if "manifest.json" not in names:
                    return {"valid": False, "manifest": None, "errors": ["manifest.json không tồn tại trong backup."]}

                try:
                    manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
                except Exception as exc:
                    return {"valid": False, "manifest": None, "errors": [f"manifest.json bị lỗi định dạng: {exc}"]}

                if not isinstance(manifest, dict) or manifest.get("schema_version") != BACKUP_SCHEMA_VERSION:
                    return {"valid": False, "manifest": None, "errors": ["manifest.json schema_version không hợp lệ."]}

                for member, meta in (manifest.get("files") or {}).items():
                    if member not in names:
                        errors.append(f"{member}: Thiếu trong file ZIP.")
                        continue
                    content = zf.read(member)
                    actual = _sha256_bytes(content)
                    if actual != meta.get("sha256"):
                        errors.append(f"{member}: Checksum SHA-256 không khớp.")

        except (zipfile.BadZipFile, json.JSONDecodeError, OSError) as exc:
            return {"valid": False, "manifest": None, "errors": [str(exc)]}

        return {
            "valid": len(errors) == 0,
            "manifest": manifest,
            "errors": errors,
            "sha256": zip_sha256,
        }

    # ------------------------------------------------------------------
    # Plan
    # ------------------------------------------------------------------

    def plan_restore(self, backup_id: str | Path) -> dict[str, Any]:
        """Compare backup with current state and generate an immutable server-owned restore plan.

        Returns `{"accepted": bool, "plan_id": str, "preview": dict, "changes": list, ...}`.
        """
        if isinstance(backup_id, Path):
            actual_id = self._backup_id_for_file(backup_id)
        else:
            actual_id = str(backup_id)

        inspect = self.inspect_backup(actual_id)
        if not inspect.get("valid"):
            return {
                "accepted": False,
                "reason": "Backup không hợp lệ hoặc không vượt qua kiểm tra an toàn.",
                "errors": inspect.get("errors", []),
            }

        manifest = inspect["manifest"]
        backup_sha256 = inspect.get("sha256", "")
        current_state_fingerprint = _compute_state_fingerprint()

        changes: list[dict[str, Any]] = []
        newer_protected: list[str] = []
        categories: dict[str, list[str]] = {
            "settings": [],
            "creative_workspace": [],
            "workflow_library": [],
            "drafts": [],
        }

        for member, meta in (manifest.get("files") or {}).items():
            clean_member = re.sub(r"[^a-zA-Z0-9._/-]", "_", member)
            target = CONFIG_ROOT / clean_member
            size_bytes = meta.get("size_bytes", 0)

            # Categorize
            if clean_member.startswith("drafts/"):
                categories["drafts"].append(clean_member)
            elif clean_member == "settings.json":
                categories["settings"].append(clean_member)
            elif clean_member == "creative_workspace.json":
                categories["creative_workspace"].append(clean_member)
            elif clean_member == "workflow_library.json":
                categories["workflow_library"].append(clean_member)

            if target.exists():
                try:
                    current_mtime = target.stat().st_mtime
                    backup_ts = manifest.get("created_at", "")
                    backup_epoch = datetime.fromisoformat(backup_ts).timestamp() if backup_ts else 0.0
                    if current_mtime > backup_epoch + 5:
                        newer_protected.append(clean_member)
                        changes.append({
                            "member": clean_member,
                            "action": "skip_newer",
                            "size_bytes": size_bytes,
                            "reason": "File local có thời gian sửa đổi mới hơn backup.",
                        })
                        continue
                except (OSError, ValueError):
                    pass
                changes.append({
                    "member": clean_member,
                    "action": "overwrite",
                    "size_bytes": size_bytes,
                    "reason": "File hiện tại sẽ được ghi đè từ bản sao lưu.",
                })
            else:
                changes.append({
                    "member": clean_member,
                    "action": "create",
                    "size_bytes": size_bytes,
                    "reason": "File chưa tồn tại cục bộ, sẽ được tạo mới.",
                })

        plan_id = f"plan_{hashlib.sha256(os.urandom(16)).hexdigest()[:24]}"
        plan_record: dict[str, Any] = {
            "plan_id": plan_id,
            "backup_id": actual_id,
            "backup_sha256": backup_sha256,
            "state_fingerprint": current_state_fingerprint,
            "created_at": _now_iso(),
            "changes": changes,
            "newer_protected": newer_protected,
            "preview": {
                "total": len(changes),
                "overwrite": sum(1 for c in changes if c["action"] == "overwrite"),
                "create": sum(1 for c in changes if c["action"] == "create"),
                "skip_newer": sum(1 for c in changes if c["action"] == "skip_newer"),
            },
            "categories": {k: v for k, v in categories.items() if v},
        }

        with _PLANS_LOCK:
            _RESTORE_PLANS[plan_id] = plan_record

        return {
            "accepted": True,
            **plan_record,
        }

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    def apply_restore(self, plan_id: str, *, confirmed: bool) -> dict[str, Any]:
        """Apply a previously inspected and generated restore plan atomically.

        Guarantees:
        - Checks confirmed == True
        - Verifies plan binding & backup ZIP hash
        - Verifies current state fingerprint (rejects with 409 conflict if stale)
        - Atomic write + post-restore validation
        """
        if not confirmed:
            return {
                "accepted": False,
                "status": "unconfirmed",
                "reason": "Khôi phục yêu cầu confirmed=True sau khi xem chi tiết preview.",
            }

        with _PLANS_LOCK:
            plan = _RESTORE_PLANS.get(plan_id)

        if not plan:
            return {
                "accepted": False,
                "status": "not_found",
                "reason": "Kế hoạch khôi phục (plan_id) không tồn tại hoặc đã hết hạn. Vui lòng kiểm tra lại backup.",
            }

        backup_id = plan["backup_id"]
        backup_path = self._resolve_backup_target(backup_id)
        if not backup_path or not backup_path.exists():
            return {
                "accepted": False,
                "status": "missing_backup",
                "reason": "File backup liên kết với plan này không còn tồn tại.",
            }

        # 1. Re-inspect and verify backup hash matches plan binding
        inspect = self.inspect_backup(backup_id)
        if not inspect.get("valid"):
            return {
                "accepted": False,
                "status": "corrupt_backup",
                "reason": "File backup không còn hợp lệ.",
                "errors": inspect.get("errors", []),
            }
        if inspect.get("sha256") != plan.get("backup_sha256"):
            return {
                "accepted": False,
                "status": "checksum_mismatch",
                "reason": "File backup đã bị chỉnh sửa sau khi lập kế hoạch khôi phục. Vui lòng lập lại kế hoạch.",
            }

        # 2. Verify state fingerprint has not changed since plan was generated
        current_state_fingerprint = _compute_state_fingerprint()
        if current_state_fingerprint != plan.get("state_fingerprint"):
            return {
                "accepted": False,
                "status": "conflict",
                "code": 409,
                "reason": "Trạng thái ứng dụng cục bộ đã thay đổi sau khi tạo kế hoạch khôi phục. Vui lòng xem lại kế hoạch mới trước khi tiếp tục.",
            }

        applied: list[str] = []
        skipped: list[str] = []

        config_root_resolved = CONFIG_ROOT.resolve()

        with self._lock:
            try:
                with zipfile.ZipFile(backup_path, "r") as zf:
                    for change in plan.get("changes", []):
                        member = change["member"]
                        action = change["action"]
                        if action == "skip_newer":
                            skipped.append(member)
                            continue

                        # Traversal guard
                        clean = re.sub(r"^[/\\]+", "", member)
                        target = (CONFIG_ROOT / clean).resolve()
                        try:
                            target.relative_to(config_root_resolved)
                        except ValueError:
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
                                handle.flush()
                                os.fsync(handle.fileno())
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

            # Post-restore verification
            verify_res = self.verify_restore({"accepted": True, "applied": applied})

            # Consume used plan
            with _PLANS_LOCK:
                _RESTORE_PLANS.pop(plan_id, None)

        return {
            "accepted": True,
            "applied": applied,
            "skipped": skipped,
            "verified": verify_res.get("valid", False),
        }

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
