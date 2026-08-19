"""
  FILE NOTE
  - Mục đích: Validation schema có phiên bản, migration runner, section defaults và secret scrubbing cho settings machine-local của Local AI Hub
  - Liên kết trực tiếp: src/app_config/settings_service.py, src/app_config/defaults.py, src/app/main.py
  - Vùng ảnh hưởng khi sửa: Toàn bộ settings persistence (schema_version, migration, safe defaults, secret guard)
"""

from __future__ import annotations

from typing import Any


# Increment when the schema structure changes. Older files are migrated on load.
SETTINGS_SCHEMA_VERSION = 2

# Keys that must never be persisted in tracked output, logs, or diagnostic bundles.
_SECRET_KEYS: frozenset[str] = frozenset({
    "api_key", "token", "password", "secret", "credential", "credentials",
    "auth", "private_key", "access_key", "refresh_token",
})

# Valid enum values per field.
_VALID_LANGUAGES = frozenset({"vi", "en", "zh", "ja", "ko"})
_VALID_THEMES = frozenset({"system", "dark", "light"})
_VALID_LOAD_POLICIES = frozenset({"on_demand", "keep_loaded"})

SETTINGS_SECTION_DEFAULTS: dict[str, dict[str, Any]] = {
    "ui": {
        "language": "vi",
        "theme": "dark",
        "sidebar_collapsed": False,
    },
    "network": {
        "bind_host": "127.0.0.1",
        "api_port": 8765,
    },
    "jobs": {
        "max_heavy_gpu_jobs": 1,
        "model_load_policy": "on_demand",
    },
    "window": {
        "start_maximized": True,
        "minimum_width": 1280,
        "minimum_height": 720,
    },
}


def _default_settings() -> dict[str, Any]:
    import copy
    return {
        "schema_version": SETTINGS_SCHEMA_VERSION,
        "settings_revision": 0,
        **{section: copy.deepcopy(values) for section, values in SETTINGS_SECTION_DEFAULTS.items()},
    }


def _validate_ui(section: dict[str, Any]) -> dict[str, Any]:
    result = dict(SETTINGS_SECTION_DEFAULTS["ui"])
    lang = section.get("language")
    if lang in _VALID_LANGUAGES:
        result["language"] = lang
    theme = section.get("theme")
    if theme == "auto":
        theme = "system"
    if theme in _VALID_THEMES:
        result["theme"] = theme
    result["sidebar_collapsed"] = bool(section.get("sidebar_collapsed", False))
    return result


def _validate_network(section: dict[str, Any]) -> dict[str, Any]:
    result = dict(SETTINGS_SECTION_DEFAULTS["network"])
    host = section.get("bind_host", "127.0.0.1")
    if host != "127.0.0.1":
        raise ValueError("Hub API bind_host must remain loopback-only (127.0.0.1).")
    result["bind_host"] = "127.0.0.1"
    port = section.get("api_port", 8765)
    if isinstance(port, int) and 1024 <= port <= 65535:
        result["api_port"] = port
    return result


def _validate_jobs(section: dict[str, Any]) -> dict[str, Any]:
    result = dict(SETTINGS_SECTION_DEFAULTS["jobs"])
    gpu_jobs = section.get("max_heavy_gpu_jobs", 1)
    if isinstance(gpu_jobs, int) and 1 <= gpu_jobs <= 4:
        result["max_heavy_gpu_jobs"] = gpu_jobs
    policy = section.get("model_load_policy", "on_demand")
    if policy == "eager":
        policy = "keep_loaded"
    elif policy == "never":
        policy = "on_demand"
    if policy in _VALID_LOAD_POLICIES:
        result["model_load_policy"] = policy
    return result


def _validate_window(section: dict[str, Any]) -> dict[str, Any]:
    result = dict(SETTINGS_SECTION_DEFAULTS["window"])
    result["start_maximized"] = bool(section.get("start_maximized", True))
    width = section.get("minimum_width", 1280)
    if isinstance(width, int) and 800 <= width <= 3840:
        result["minimum_width"] = width
    height = section.get("minimum_height", 720)
    if isinstance(height, int) and 600 <= height <= 2160:
        result["minimum_height"] = height
    return result


def validate_settings(value: Any) -> dict[str, Any]:
    """Validate and normalise settings, enforcing all field contracts.

    Malformed sections fall back to safe defaults rather than raising, so that a
    single bad field never prevents Hub from starting.  The loopback-only guard on
    ``bind_host`` is the only hard error.
    """
    if not isinstance(value, dict):
        raise ValueError("settings must be an object")

    result = _default_settings()

    # Preserve revision if present and valid.
    revision = value.get("settings_revision", 0)
    if isinstance(revision, int) and revision >= 0:
        result["settings_revision"] = revision

    for section_name, validator in (
        ("ui", _validate_ui),
        ("network", _validate_network),
        ("jobs", _validate_jobs),
        ("window", _validate_window),
    ):
        raw_section = value.get(section_name, {})
        if not isinstance(raw_section, dict):
            raw_section = {}
        try:
            result[section_name] = validator(raw_section)
        except ValueError:
            raise  # Re-raise hard errors (e.g. bind_host guard).

    return result


def migrate_settings(raw: dict[str, Any], from_version: int) -> dict[str, Any]:
    """Migrate raw settings from an older schema version to SETTINGS_SCHEMA_VERSION.

    Returns a dict ready for validate_settings().  Only forward migrations are
    supported; downgrade is not.
    """
    import copy
    value = copy.deepcopy(raw)

    if from_version < 2:
        # V1 -> V2: restructure flat keys into sections.
        value.setdefault("ui", {})
        value.setdefault("network", {})
        value.setdefault("jobs", {})
        value.setdefault("window", {})

        # Migrate legacy flat keys to sections.
        for old_key, (section, new_key) in {
            "start_maximized": ("window", "start_maximized"),
            "minimum_width": ("window", "minimum_width"),
            "minimum_height": ("window", "minimum_height"),
            "max_heavy_gpu_jobs": ("jobs", "max_heavy_gpu_jobs"),
            "model_load_policy": ("jobs", "model_load_policy"),
            "bind_host": ("network", "bind_host"),
        }.items():
            if old_key in value and new_key not in value[section]:
                value[section][new_key] = value.pop(old_key)
            elif old_key in value:
                value.pop(old_key)

    value["schema_version"] = SETTINGS_SCHEMA_VERSION
    return value


def scrub_secrets(value: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy with known secret keys removed at all levels."""
    import copy

    def _scrub(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: _scrub(v) for k, v in obj.items() if k.lower() not in _SECRET_KEYS}
        if isinstance(obj, list):
            return [_scrub(item) for item in obj]
        return obj

    return _scrub(copy.deepcopy(value))
