"""Common loader for lightweight module manifests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_manifest(module_dir: Path) -> dict[str, Any]:
    with (module_dir / "manifest.json").open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict) or not value.get("module_id"):
        raise ValueError(f"invalid module manifest: {module_dir}")
    return value
