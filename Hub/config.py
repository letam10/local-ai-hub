from __future__ import annotations

import json
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
CONFIG_DIR = BASE_DIR / "Config"


def load_json(name: str, default: Any) -> Any:
    path = CONFIG_DIR / name
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
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
