"""
  FILE NOTE
  - Mục đích: In-memory, path-safe status snapshots cho Node Studio graph jobs; cung cấp draft_persist/draft_load để phục hồi sau crash
  - Liên kết trực tiếp: src/services/node_studio/engine.py, src/services/artifact_store.py, src/shared/paths/registry.py
  - Vùng ảnh hưởng khi sửa: Node Studio run state, crash recovery draft, artifact public output scrubbing
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.services.artifact_store import publicize
from src.shared.paths.registry import CONFIG_ROOT




_LOCAL_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\)")
ACTIVE_RUN_STATUSES = frozenset({"queued", "starting", "running", "cancelling"})
MAX_TERMINAL_RUNS = 100

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public_artifacts(value: Any, *, path: str = "") -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        artifact_id = value.get("id")
        if isinstance(artifact_id, str) and artifact_id.startswith("artifact_"):
            found.append({
                "artifact_id": artifact_id,
                "name": value.get("name"),
                "media_type": value.get("media_type"),
                "url": value.get("url"),
                "output_path": path or "output",
            })
        for key, child in value.items():
            found.extend(_public_artifacts(child, path=f"{path}.{key}" if path else str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_public_artifacts(child, path=f"{path}[{index}]"))
    return found


def _safe_output(value: Any) -> Any:
    """Apply the public artifact conversion and scrub raw paths at the state boundary."""

    try:
        value = publicize(value)
    except Exception:  # pragma: no cover - defensive boundary for worker payloads
        pass
    if isinstance(value, dict):
        return {str(key): _safe_output(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_safe_output(child) for child in value]
    if isinstance(value, str) and _LOCAL_PATH.search(value):
        return "[đường-dẫn-cục-bộ]"
    return value


class GraphRunRegistry:
    """Expose per-node progress without persisting graph inputs or local paths."""

    def __init__(self) -> None:
        self._runs: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._sequence = 0

    def _prune_terminal_locked(self) -> None:
        """Keep all active runs and the newest bounded terminal snapshots."""

        terminal = sorted(
            (item for item in self._runs.values() if str(item.get("status")) not in ACTIVE_RUN_STATUSES),
            key=lambda item: (str(item.get("finished_at") or item.get("created_at") or ""), int(item.get("_sequence") or 0)),
            reverse=True,
        )
        for item in terminal[MAX_TERMINAL_RUNS:]:
            job_id = item.get("job_id")
            if isinstance(job_id, str):
                self._runs.pop(job_id, None)

    def begin(self, job_id: str, graph: dict[str, Any]) -> None:
        nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
        with self._lock:
            current = self._runs.get(job_id)
            if current:
                return
            self._sequence += 1
            self._runs[job_id] = {
                "contract_version": "node-run.v2",
                "status": "queued",
                "job_id": job_id,
                "graph_id": str(graph.get("id") or "untitled"),
                "title": str(graph.get("title") or "Untitled workflow"),
                "created_at": _now(),
                "finished_at": None,
                "next_action": None,
                "_sequence": self._sequence,
                "provenance": [],
                "nodes": {
                    str(node.get("id")): {
                        "id": str(node.get("id")),
                        "type": str(node.get("type") or "unknown"),
                        "status": "queued",
                        "progress": 0,
                        "message": "Đang chờ Run Graph.",
                    }
                    for node in nodes
                    if isinstance(node, dict) and node.get("id")
                },
            }
            self._prune_terminal_locked()

    def update_node(
        self,
        job_id: str,
        node_id: str,
        *,
        status: str,
        progress: int,
        message: str = "",
        output: Any = None,
        error: str | None = None,
        next_action: str | None = None,
    ) -> None:
        with self._lock:
            run = self._runs.get(job_id)
            if not run:
                return
            node = run["nodes"].get(node_id)
            if not isinstance(node, dict):
                return
            node.update({"status": status, "progress": max(0, min(100, int(progress))), "message": message})
            if output is not None:
                output = _safe_output(output)
                node["output"] = output
            if error:
                node["error"] = error
            if next_action:
                node["next_action"] = next_action
                run["next_action"] = next_action
            if output is not None:
                existing = {item.get("artifact_id") for item in run["provenance"] if isinstance(item, dict)}
                for artifact in _public_artifacts(output):
                    artifact_id = artifact.get("artifact_id")
                    if artifact_id in existing:
                        continue
                    run["provenance"].append({
                        **artifact,
                        "node_id": node_id,
                        "node_type": node.get("type"),
                    })
                    existing.add(artifact_id)
            if status == "running":
                run["status"] = "running"

    def finish(self, job_id: str, *, status: str, error: str | None = None, next_action: str | None = None) -> None:
        with self._lock:
            run = self._runs.get(job_id)
            if not run:
                return
            run["status"] = status
            run["finished_at"] = _now()
            if error:
                run["error"] = error
            if next_action:
                run["next_action"] = next_action
            self._prune_terminal_locked()

    def snapshot(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            run = self._runs.get(job_id)
            if not run:
                return None
            # All callers supply already-public artifact metadata.  Copying still
            # prevents a handler from mutating the live worker state.
            return {
                **{key: value for key, value in run.items() if key not in {"nodes", "_sequence"}},
                "provenance": [dict(item) for item in run.get("provenance", [])],
                "nodes": [dict(item) for item in run["nodes"].values()],
            }


graph_runs = GraphRunRegistry()


# ---------------------------------------------------------------------------
# Graph draft persistence (crash recovery)
# ---------------------------------------------------------------------------

_DRAFT_SCHEMA_VERSION = 1
_SAFE_SCOPE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def _draft_path(scope: str) -> Path:
    safe = scope if _SAFE_SCOPE.match(scope) else re.sub(r"[^a-z0-9_-]", "_", scope.lower())[:32]
    return CONFIG_ROOT / f"node_studio_draft_{safe}.json"


def draft_persist(scope: str, graph: dict[str, Any]) -> dict[str, Any]:
    """Write a crash-recovery draft for the given scope (e.g. 'image', 'sam2').

    The draft file is separate from main workspace state and is cleared on
    clean session close.  Returns ``{"accepted": bool}``.
    """
    if not isinstance(scope, str) or not scope:
        return {"accepted": False, "reason": "scope không hợp lệ."}
    if not isinstance(graph, dict):
        return {"accepted": False, "reason": "graph phải là object."}

    path = _draft_path(scope)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "draft_schema_version": _DRAFT_SCHEMA_VERSION,
        "scope": scope,
        "graph": graph,
    }
    tmp: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8",
            dir=path.parent,
            prefix=".node-draft-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp = Path(handle.name)
            handle.write(json.dumps(data, ensure_ascii=False, indent=2))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        tmp = None
        return {"accepted": True, "draft_path": str(path)}
    except (OSError, TypeError, ValueError):
        return {"accepted": False, "reason": "Không ghi được draft."}
    finally:
        if tmp is not None:
            try:
                tmp.unlink()
            except OSError:
                pass


def draft_load(scope: str) -> dict[str, Any] | None:
    """Load a crash-recovery draft for the given scope.  Returns None if absent or corrupt."""
    if not isinstance(scope, str) or not scope:
        return None
    path = _draft_path(scope)
    try:
        raw = json.loads(path.read_bytes())
        if not isinstance(raw, dict) or raw.get("draft_schema_version") != _DRAFT_SCHEMA_VERSION:
            return None
        return raw
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def draft_clear(scope: str) -> None:
    """Remove the crash-recovery draft for the given scope after clean save."""
    if not isinstance(scope, str) or not scope:
        return
    try:
        _draft_path(scope).unlink(missing_ok=True)
    except OSError:
        pass
