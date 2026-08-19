"""
  FILE NOTE
  - Má»¥c Ä‘Ã­ch: Persistent settings service vá»›i atomic write (temp+fsync+replace), malformed-file recovery, revision/conflict detection, per-section reset, migration, vÃ  secret scrubbing
  - LiÃªn káº¿t trá»±c tiáº¿p: src/app_config/schema.py, src/app_config/defaults.py, src/app/main.py, src/services/api/
  - VÃ¹ng áº£nh hÆ°á»Ÿng khi sá»­a: ToÃ n bá»™ settings R/W cho Hub (language, theme, sidebar, jobs, window, network)
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
    - Atomic writes: temp file in same directory â†’ os.fsync â†’ os.replace (Windows-safe).
    - Malformed-file recovery: parse failure or schema mismatch â†’ preserve file, return
      safe defaults with ``recovery_required`` status.  Never silently overwrite corrupt data.
    - Revision conflict: write is rejected if ``expected_revision`` does not match current.
    - Migration: v1 flat keys are automatically promoted to v2 sections on load.
    - Secret scrubbing: known secret keys are stripped before any write or export.
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
        safely.  In that case the caller should NOT overwrite the file.
        """
        if not self.path.exists():
            return (
                _default_persisted_settings(),
                {"status": "clean", "reason": "ChÆ°a cÃ³ file settings; sáº½ táº¡o khi lÆ°u láº§n Ä‘áº§u.", "action": "LÆ°u settings Ä‘á»ƒ khá»Ÿi táº¡o file."},
                False,
            )

        try:
            raw_bytes = self.path.read_bytes()
            source = json.loads(raw_bytes)
        except (OSError, json.JSONDecodeError):
            return (
                _default_persisted_settings(),
                {"status": "recovery_required", "reason": "File settings khÃ´ng Ä‘á»c Ä‘Æ°á»£c; Hub khÃ´ng tá»± ghi Ä‘Ã¨.", "action": "XÃ³a file settings rá»“i khá»Ÿi Ä‘á»™ng láº¡i Hub Ä‘á»ƒ táº¡o máº·c Ä‘á»‹nh, hoáº·c restore tá»« backup."},
                True,
            )

        if not isinstance(source, dict):
            return (
                _default_persisted_settings(),
                {"status": "recovery_required", "reason": "File settings khÃ´ng Ä‘Ãºng Ä‘á»‹nh dáº¡ng JSON object; Hub khÃ´ng tá»± ghi Ä‘Ã¨.", "action": "XÃ³a file settings rá»“i khá»Ÿi Ä‘á»™ng láº¡i Hub Ä‘á»ƒ táº¡o máº·c Ä‘á»‹nh."},
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
                    {"status": "recovery_required", "reason": f"KhÃ´ng thá»ƒ migrate settings tá»« v{file_version} lÃªn v{SETTINGS_SCHEMA_VERSION}; Hub khÃ´ng tá»± ghi Ä‘Ã¨.", "action": "Kiá»ƒm tra file settings hoáº·c restore tá»« backup."},
                    True,
                )

        try:
            validated = validate_settings(source)
        except ValueError as exc:
            return (
                _default_persisted_settings(),
                {"status": "recovery_required", "reason": f"File settings khÃ´ng há»£p lá»‡: {exc}; Hub khÃ´ng tá»± ghi Ä‘Ã¨.", "action": "Sá»­a file settings hoáº·c xÃ³a Ä‘á»ƒ táº¡o máº·c Ä‘á»‹nh."},
                True,
            )

        return validated, {"status": "clean", "reason": "Settings há»£p lá»‡.", "action": "CÃ³ thá»ƒ tiáº¿p tá»¥c chá»‰nh sá»­a settings."}, False

    def _write(self, settings: dict[str, Any]) -> str:
        """Atomic write: temp â†’ fsync â†’ replace.  Returns 'written' | 'error'."""
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
                    "reason": "Settings Ä‘Ã£ thay Ä‘á»•i tá»« láº§n Ä‘á»c trÆ°á»›c.",
                    "action": "Táº£i láº¡i settings, xem thay Ä‘á»•i rá»“i lÆ°u láº¡i.",
                }

            if not isinstance(patch, dict):
                return {"accepted": False, "status": "invalid", "reason": "Patch pháº£i lÃ  object.", "action": "Truyá»n dict section há»£p lá»‡."}

            # Apply patch section by section.
            import copy
            merged = copy.deepcopy(settings)
            for section_key, section_patch in patch.items():
                if section_key in SETTINGS_SECTION_DEFAULTS and isinstance(section_patch, dict):
                    if not isinstance(merged.get(section_key), dict):
                        merged[section_key] = {}
                    merged[section_key].update(section_patch)

            try:
                validated = validate_settings(merged)
            except ValueError as exc:
                return {"accepted": False, "status": "invalid", "reason": str(exc), "action": "Sá»­a giÃ¡ trá»‹ vi pháº¡m rÃ ng buá»™c rá»“i thá»­ láº¡i."}

            validated["settings_revision"] = current_revision + 1
            clean = scrub_secrets(validated)
            outcome = self._write(clean)
            if outcome != "written":
                return {"accepted": False, "status": "write_error", "reason": "KhÃ´ng ghi Ä‘Æ°á»£c file settings.", "action": "Kiá»ƒm tra quyá»n ghi thÆ° má»¥c Config."}

            return {"accepted": True, "status": "saved", "settings_revision": validated["settings_revision"], "settings": clean}

    def reset_section(self, section: str) -> dict[str, Any]:
        """Reset one settings section to safe defaults and persist."""
        if section not in SETTINGS_SECTION_DEFAULTS:
            return {"accepted": False, "status": "invalid", "reason": f"Section '{section}' khÃ´ng tá»“n táº¡i.", "action": f"Chá»n section há»£p lá»‡: {list(SETTINGS_SECTION_DEFAULTS)}."}
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
