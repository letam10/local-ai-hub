"""Versioned, non-destructive graph migration for Workflow Runtime V2."""

from __future__ import annotations

from copy import deepcopy
import json
import math
from collections.abc import Mapping
from typing import Any


WORKFLOW_GRAPH_V2_SCHEMA_VERSION = 2
_MAX_GRAPH_BYTES = 512 * 1024
_GRAPH_KEYS_V1 = frozenset({"schema_version", "id", "title", "scope", "nodes", "edges", "groups"})
_GRAPH_KEYS_V2 = _GRAPH_KEYS_V1 | {"metadata"}


def _safe_metadata(value: object, *, depth: int = 0) -> bool:
    if depth > 4:
        return False
    if value is None or isinstance(value, (bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, str):
        return len(value) <= 240 and "\x00" not in value
    if isinstance(value, list):
        return len(value) <= 32 and all(_safe_metadata(item, depth=depth + 1) for item in value)
    if isinstance(value, Mapping):
        return len(value) <= 32 and all(isinstance(key, str) and 0 < len(key) <= 64 and _safe_metadata(item, depth=depth + 1) for key, item in value.items())
    return False


def _bounded(value: object) -> bool:
    try:
        encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        return False
    return len(encoded) <= _MAX_GRAPH_BYTES


def migrate_graph(graph: object) -> dict[str, Any]:
    """Return a detached V2 migration view and Node Studio-compatible graph.

    Migration is deliberately pure: it never writes a draft/library record.
    V1 graphs gain empty V2 metadata in the returned view while retaining the
    exact node/edge topology. V2 graphs are accepted only under a closed shape
    and downgraded *in memory* to the V1 Node Studio validator contract. This
    preserves forward-compatible unknown node records for reporting rather
    than deleting or executing them.
    """

    if not isinstance(graph, Mapping) or not _bounded(graph):
        return {"accepted": False, "code": "workflow_runtime_graph_bounds"}
    source = deepcopy(dict(graph))
    version = source.get("schema_version")
    if version == 1:
        if set(source) - _GRAPH_KEYS_V1:
            return {"accepted": False, "code": "workflow_runtime_graph_fields"}
        runtime_graph = deepcopy(source)
        v2_graph = {**deepcopy(source), "schema_version": WORKFLOW_GRAPH_V2_SCHEMA_VERSION, "metadata": {}}
        return {
            "accepted": True,
            "source_schema_version": 1,
            "target_schema_version": WORKFLOW_GRAPH_V2_SCHEMA_VERSION,
            "runtime_graph": runtime_graph,
            "v2_graph": v2_graph,
            "changed": True,
            "actions": ["add_empty_metadata"],
        }
    if version == WORKFLOW_GRAPH_V2_SCHEMA_VERSION:
        if set(source) - _GRAPH_KEYS_V2 or not _safe_metadata(source.get("metadata", {})):
            return {"accepted": False, "code": "workflow_runtime_graph_fields"}
        runtime_graph = {key: deepcopy(value) for key, value in source.items() if key in _GRAPH_KEYS_V1}
        runtime_graph["schema_version"] = 1
        return {
            "accepted": True,
            "source_schema_version": WORKFLOW_GRAPH_V2_SCHEMA_VERSION,
            "target_schema_version": WORKFLOW_GRAPH_V2_SCHEMA_VERSION,
            "runtime_graph": runtime_graph,
            "v2_graph": source,
            "changed": False,
            "actions": [],
        }
    return {"accepted": False, "code": "workflow_runtime_graph_schema_unsupported"}


__all__ = ["WORKFLOW_GRAPH_V2_SCHEMA_VERSION", "migrate_graph"]
