from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any

from src.shared.paths.registry import CONFIG_ROOT, ROOT


BASE_DIR = ROOT
CONFIG_DIR = CONFIG_ROOT


LOCAL_PROVENANCES = frozenset({
    "local",
    "example_template",
    "missing",
    "malformed_local",
    "malformed_example",
})


def _example_name(name: str) -> str:
    return f"{name[:-5]}.example.json" if name.endswith(".json") and not name.endswith(".example.json") else name


def _fingerprint(value: Any) -> str | None:
    try:
        encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(encoded).hexdigest()


def read_local_config(
    name: str,
    default: Any,
    *,
    config_dir: Path = CONFIG_DIR,
    example_name: str | None = None,
) -> dict[str, Any]:
    """Read a config value while preserving whether it is machine-local or an example.

    The legacy :func:`load_json` API intentionally remains value-only.  Recovery
    and launch decisions must use this explicit provenance record instead, so a
    tracked example can never silently become live machine state.
    """

    local_path = config_dir / name
    example_path = config_dir / (example_name or _example_name(name))
    try:
        local_exists = local_path.is_file()
    except OSError:
        local_exists = False
    if local_exists:
        try:
            value = json.loads(local_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {
                "value": default,
                "provenance": "malformed_local",
                "present": True,
                "fingerprint": None,
            }
        return {
            "value": value,
            "provenance": "local",
            "present": True,
            "fingerprint": _fingerprint(value),
        }
    try:
        example_exists = example_path.is_file()
    except OSError:
        example_exists = False
    if example_exists:
        try:
            value = json.loads(example_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {
                "value": default,
                "provenance": "malformed_example",
                "present": True,
                "fingerprint": None,
            }
        return {
            "value": value,
            "provenance": "example_template",
            "present": True,
            "fingerprint": _fingerprint(value),
        }
    return {
        "value": default,
        "provenance": "missing",
        "present": False,
        "fingerprint": None,
    }


def load_json(name: str, default: Any) -> Any:
    """Legacy value-only reader; new decisions must use :func:`read_local_config`."""

    return read_local_config(name, default)["value"]


def hub_config() -> dict[str, Any]:
    return load_json("hub_config.json", {})


def module_manager_config() -> dict[str, Any]:
    """Load the bounded server-owned Module Manager example/config contract."""

    value = load_json("module_manager.json", {})
    return value if isinstance(value, dict) else {}


def components() -> list[dict[str, Any]]:
    value = load_json("components.json", {})
    return list(value.get("components", [])) if isinstance(value, dict) else []


def models() -> list[dict[str, Any]]:
    value = load_json("model_registry.json", {})
    return list(value.get("models", [])) if isinstance(value, dict) else []


def component(component_id: str) -> dict[str, Any] | None:
    return next((item for item in components() if item.get("id") == component_id), None)
