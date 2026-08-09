"""In-memory, path-safe status snapshots for Node Studio graph jobs."""

from __future__ import annotations

import threading
import re
from datetime import datetime, timezone
from typing import Any

from src.services.artifact_store import publicize


_LOCAL_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\)")

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

    def begin(self, job_id: str, graph: dict[str, Any]) -> None:
        nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
        with self._lock:
            current = self._runs.get(job_id)
            if current:
                return
            self._runs[job_id] = {
                "contract_version": "node-run.v2",
                "status": "queued",
                "job_id": job_id,
                "graph_id": str(graph.get("id") or "untitled"),
                "title": str(graph.get("title") or "Untitled workflow"),
                "created_at": _now(),
                "finished_at": None,
                "next_action": None,
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

    def snapshot(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            run = self._runs.get(job_id)
            if not run:
                return None
            # All callers supply already-public artifact metadata.  Copying still
            # prevents a handler from mutating the live worker state.
            return {
                **{key: value for key, value in run.items() if key != "nodes"},
                "provenance": [dict(item) for item in run.get("provenance", [])],
                "nodes": [dict(item) for item in run["nodes"].values()],
            }


graph_runs = GraphRunRegistry()
