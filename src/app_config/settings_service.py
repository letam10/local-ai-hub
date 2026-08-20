"""
  FILE NOTE
  - Mục đích: Persistent settings service với atomic write (temp+fsync+replace), malformed-file recovery, revision/conflict detection, per-section reset, migration, và secret scrubbing
  - Liên kết trực tiếp: src/app_config/schema.py, src/app_config/defaults.py, src/app/main.py, src/services/api/
  - Vùng ảnh hưởng khi sửa: Toàn bộ settings R/W cho Hub (language, theme, sidebar, jobs, window)
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from src.app_config.schema import (
    SETTINGS_SCHEMA_VERSION,
    SETTINGS_SECTION_DEFAULTS,
    migrate_settings,
    scrub_secrets,
    validate_settings,
)
from src.shared.paths.registry import CONFIG_ROOT


DEFAULT_SETTINGS_PATH = CONFIG_ROOT / "settings.json"
ALLOWED_PERSISTENCE_SECTIONS: frozenset[str] = frozenset({"ui", "window", "jobs"})

_SETTINGS_LOCKS: dict[str, threading.RLock] = {}
_SETTINGS_LOCKS_GUARD = threading.Lock()


def _shared_settings_lock(path: Path) -> threading.RLock:
    key = str(path.resolve(strict=False))
    with _SETTINGS_LOCKS_GUARD:
        lock = _SETTINGS_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _SETTINGS_LOCKS[key] = lock
        return lock


def _default_persisted_settings() -> dict[str, Any]:
    import copy
    return {
        "schema_version": SETTINGS_SCHEMA_VERSION,
        "settings_revision": 0,
        **{section: copy.deepcopy(values) for section, values in SETTINGS_SECTION_DEFAULTS.items()},
    }


class SettingsPersistence:
    """Thread-safe, atomic, revision-aware settings persistence for Local AI Hub.

    Design guarantees:
    - Atomic writes: temp file in same directory -> os.fsync -> os.replace (Windows-safe).
    - Malformed-file recovery: parse failure or schema mismatch -> preserve file, return
      safe defaults with ``recovery_required`` status. Never silently overwrite corrupt data.
    - Revision conflict: write is rejected if ``expected_revision`` does not match current.
    - Migration: v1 flat keys are automatically promoted to v2 sections on load.
    - Secret scrubbing: known secret keys are stripped before any write or export.
    - Allowed sections: strictly ui, window, jobs. Network remains with hub_config.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else Path(CONFIG_ROOT / "settings.json")
        self._lock = threading.RLock()
        self._shared_lock = _shared_settings_lock(self.path)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read(self) -> tuple[dict[str, Any], dict[str, Any], bool]:
        """Return (settings, recovery_info, blocked).

        ``blocked=True`` means the file exists but could not be parsed/migrated
        safely. In that case the caller should NOT overwrite the file.
        """
        if not self.path.exists():
            return (
                _default_persisted_settings(),
                {"status": "clean", "reason": "Chưa có file settings; sẽ tạo khi lưu lần đầu.", "action": "Lưu settings để khởi tạo file."},
                False,
            )

        try:
            raw_bytes = self.path.read_bytes()
            source = json.loads(raw_bytes)
        except (OSError, json.JSONDecodeError):
            return (
                _default_persisted_settings(),
                {"status": "recovery_required", "reason": "File settings không đọc được; Hub không tự ghi đè.", "action": "Xóa file settings rồi khởi động lại Hub để tạo mặc định, hoặc restore từ backup."},
                True,
            )

        if not isinstance(source, dict):
            return (
                _default_persisted_settings(),
                {"status": "recovery_required", "reason": "File settings không đúng định dạng JSON object; Hub không tự ghi đè.", "action": "Xóa file settings rồi khởi động lại Hub để tạo mặc định."},
                True,
            )

        # Migration: promote old flat v1 to v2 sections.
        file_version = source.get("schema_version", 1)
        if isinstance(file_version, int) and file_version < SETTINGS_SCHEMA_VERSION:
            try:
                source = migrate_settings(source, from_version=file_version)
            except Exception:
                return (
                    _default_persisted_settings(),
                    {"status": "recovery_required", "reason": f"Không thể migrate settings từ v{file_version} lên v{SETTINGS_SCHEMA_VERSION}; Hub không tự ghi đè.", "action": "Kiểm tra file settings hoặc restore từ backup."},
                    True,
                )

        try:
            validated = validate_settings(source)
        except ValueError as exc:
            return (
                _default_persisted_settings(),
                {"status": "recovery_required", "reason": f"File settings không hợp lệ: {exc}; Hub không tự ghi đè.", "action": "Sửa file settings hoặc xóa để tạo mặc định."},
                True,
            )

        return validated, {"status": "clean", "reason": "Settings hợp lệ.", "action": "Có thể tiếp tục chỉnh sửa settings."}, False

    def _write(self, settings: dict[str, Any]) -> str:
        """Atomic write: temp -> fsync -> replace. Returns 'written' | 'error'."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=".settings-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                tmp = Path(handle.name)
                handle.write(json.dumps(settings, ensure_ascii=False, indent=2))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
            tmp = None
            return "written"
        except (OSError, TypeError, ValueError):
            return "error"
        finally:
            if tmp is not None:
                try:
                    tmp.unlink()
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> dict[str, Any]:
        """Return current settings snapshot with recovery metadata."""
        with self._shared_lock, self._lock:
            settings, recovery, _ = self._read()
            return {"settings": settings, "recovery": recovery}

    def save(self, patch: dict[str, Any], *, expected_revision: int | None = None) -> dict[str, Any]:
        """Merge patch into current settings and persist atomically.

        ``patch`` must be a dict of section dicts (e.g. ``{"ui": {"language": "en"}}``).
        Returns ``{"accepted": bool, "status": str, "settings_revision": int, ...}``.
        """
        with self._shared_lock, self._lock:
            settings, recovery, blocked = self._read()
            if blocked:
                return {"accepted": False, "status": "recovery_required", "reason": recovery["reason"], "action": recovery["action"]}

            current_revision = settings.get("settings_revision", 0)
            if expected_revision is not None and expected_revision != current_revision:
                return {
                    "accepted": False, "status": "conflict",
                    "settings_revision": current_revision,
                    "reason": "Settings đã thay đổi từ lần đọc trước.",
                    "action": "Tải lại settings, xem thay đổi rồi lưu lại.",
                }

            if not isinstance(patch, dict):
                return {"accepted": False, "status": "invalid", "reason": "Patch phải là object.", "action": "Truyền dict section hợp lệ."}

            unsupported = set(patch.keys()) - ALLOWED_PERSISTENCE_SECTIONS
            if unsupported:
                return {
                    "accepted": False,
                    "status": "invalid",
                    "reason": f"Không được phép sửa section ngoài allowlist: {sorted(unsupported)}. Network/infrastructure thuộc hub_config.",
                    "action": f"Chỉ truyền các section hợp lệ: {sorted(ALLOWED_PERSISTENCE_SECTIONS)}.",
                }

            # Apply patch section by section.
            import copy
            merged = copy.deepcopy(settings)
            for section_key, section_patch in patch.items():
                if section_key in ALLOWED_PERSISTENCE_SECTIONS and isinstance(section_patch, dict):
                    if not isinstance(merged.get(section_key), dict):
                        merged[section_key] = {}
                    merged[section_key].update(section_patch)

            try:
                validated = validate_settings(merged)
            except ValueError as exc:
                return {"accepted": False, "status": "invalid", "reason": str(exc), "action": "Sửa giá trị vi phạm ràng buộc rồi thử lại."}

            validated["settings_revision"] = current_revision + 1
            clean = scrub_secrets(validated)
            outcome = self._write(clean)
            if outcome != "written":
                return {"accepted": False, "status": "write_error", "reason": "Không ghi được file settings.", "action": "Kiểm tra quyền ghi thư mục Config."}

            return {"accepted": True, "status": "saved", "settings_revision": validated["settings_revision"], "settings": clean}

    def reset_section(self, section: str) -> dict[str, Any]:
        """Reset one settings section to safe defaults and persist."""
        if section not in ALLOWED_PERSISTENCE_SECTIONS:
            return {"accepted": False, "status": "invalid", "reason": f"Section '{section}' không thuộc quyền quản lý của SettingsPersistence.", "action": f"Chọn section hợp lệ: {sorted(ALLOWED_PERSISTENCE_SECTIONS)}."}
        import copy
        return self.save({section: copy.deepcopy(SETTINGS_SECTION_DEFAULTS[section])})

    def export_sanitized(self) -> dict[str, Any]:
        """Return a secret-free snapshot safe for diagnostics/export."""
        with self._shared_lock, self._lock:
            settings, recovery, _ = self._read()
            return {"settings": scrub_secrets(settings), "recovery": recovery}


def normalize(settings: dict[str, Any]) -> dict[str, Any]:
    """Backwards-compatible shim: validate and return normalised settings."""
    return validate_settings(dict(settings))
