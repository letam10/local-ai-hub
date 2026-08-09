from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.shared.paths.registry import CONFIG_ROOT, ROOT


BASE_DIR = ROOT
CONFIG_DIR = CONFIG_ROOT


def load_json(name: str, default: Any) -> Any:
    candidates = [CONFIG_DIR / name]
    if name.endswith(".json") and not name.endswith(".example.json"):
        candidates.append(CONFIG_DIR / f"{name[:-5]}.example.json")
    for path in candidates:
        try:
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
    return default


def hub_config() -> dict[str, Any]:
    return load_json("hub_config.json", {})


def components() -> list[dict[str, Any]]:
    value = load_json("components.json", {})
    return list(value.get("components", []))


def models() -> list[dict[str, Any]]:
    value = load_json("model_registry.json", {})
    return list(value.get("models", []))


def component(component_id: str) -> dict[str, Any] | None:
    return next((item for item in components() if item.get("id") == component_id), None)
