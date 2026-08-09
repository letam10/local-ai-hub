"""In-memory, path-safe status snapshots for Node Studio graph jobs."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
                "status": "queued",
                "job_id": job_id,
                "graph_id": str(graph.get("id") or "untitled"),
                "title": str(graph.get("title") or "Untitled workflow"),
                "created_at": _now(),
                "finished_at": None,
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

    def update_node(self, job_id: str, node_id: str, *, status: str, progress: int, message: str = "", output: Any = None, error: str | None = None) -> None:
        with self._lock:
            run = self._runs.get(job_id)
            if not run:
                return
            node = run["nodes"].get(node_id)
            if not isinstance(node, dict):
                return
            node.update({"status": status, "progress": max(0, min(100, int(progress))), "message": message})
            if output is not None:
                node["output"] = output
            if error:
                node["error"] = error
            if status == "running":
                run["status"] = "running"

    def finish(self, job_id: str, *, status: str, error: str | None = None) -> None:
        with self._lock:
            run = self._runs.get(job_id)
            if not run:
                return
            run["status"] = status
            run["finished_at"] = _now()
            if error:
                run["error"] = error

    def snapshot(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            run = self._runs.get(job_id)
            if not run:
                return None
            # All callers supply already-public artifact metadata.  Copying still
            # prevents a handler from mutating the live worker state.
            return {
                **{key: value for key, value in run.items() if key != "nodes"},
                "nodes": [dict(item) for item in run["nodes"].values()],
            }


graph_runs = GraphRunRegistry()
