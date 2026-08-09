"""Local, sanitized evidence for bounded direct-workflow smoke checks.

The file is deliberately ignored because it represents this machine's observed
runtime state.  It stores no inputs, output paths, logs, credentials or model
metadata—only the tool name and when a completed direct job was recorded.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.shared.paths.registry import CONFIG_ROOT


STATE_PATH = CONFIG_ROOT / "tool_smoke_v3.local.json"
_LOCK = threading.RLock()


def _load() -> dict[str, dict[str, Any]]:
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def record_completed(tool: str) -> None:
    """Persist the outcome of one completed direct job without private data."""

    if not tool:
        return
    with _LOCK:
        state = _load()
        state[tool] = {
            "status": "completed",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "evidence": "bounded_direct_job",
        }
        temporary = STATE_PATH.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(STATE_PATH)


def passed(tool: str) -> bool:
    with _LOCK:
        value = _load().get(tool)
    return isinstance(value, dict) and value.get("status") == "completed" and value.get("evidence") == "bounded_direct_job"
