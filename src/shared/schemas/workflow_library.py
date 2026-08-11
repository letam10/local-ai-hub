"""Bounded, server-owned contract for the V5 local-first Workflow Library.

The library stores declarative Node Studio graphs and metadata only. It does
not load modules, resolve filesystem paths, start jobs, inspect devices, or
execute a graph. User state is written by the service to an ignored local
configuration root; tracked files contain only examples and presets.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Mapping
from typing import Any, Iterable


WORKFLOW_LIBRARY_SCHEMA_VERSION = "workflow-library.v1"
WORKFLOW_ENTRY_SCHEMA_VERSION = "workflow-entry.v1"
MIGRATION_PLAN_SCHEMA_VERSION = "workflow-library-migration.v1"

MAX_LIBRARY_BYTES = 2 * 1024 * 1024
MAX_WORKFLOWS = 256
MAX_GRAPH_NODES = 192
MAX_GRAPH_EDGES = 384
MAX_GRAPH_GROUPS = 64
MAX_TAGS = 24
MAX_TEXT_LENGTH = 2_000
MAX_ID_LENGTH = 96
MAX_JSON_DEPTH = 32
MAX_METADATA_KEYS = 48

WORKFLOW_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+){0,11}$")
GRAPH_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,79}$")
STATUS_VALUES = ("draft", "ready", "partial", "unavailable", "recovery_required")
SOURCE_VALUES = ("local", "imported", "preset", "migrated")

_WINDOWS_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]|(?:^|[\s(])\\\\")
_POSIX_PATH_RE = re.compile(r"(^|[\s(])/[^/\s]")
_TRAVERSAL_RE = re.compile(r"(?:^|[\\/])\.\.(?:[\\/]|$)")
_FILE_URI_RE = re.compile(r"\bfile://", re.IGNORECASE)
_DATA_URI_RE = re.compile(r"\bdata:(?:image|audio|video|application/octet-stream)", re.IGNORECASE)
_SECRET_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pous]_[A-Za-z0-9_]{8,}|github_pat_[A-Za-z0-9_]{8,}|"
    r"AKIA[0-9A-Z]{8,}|Bearer\s+[A-Za-z0-9._~+/-]{8,})\b",
    re.IGNORECASE,
)
_CREDENTIAL_URL_RE = re.compile(r"https?://[^/\s:@]+:[^@\s]+@", re.IGNORECASE)
_COMMAND_RE = re.compile(
    r"(?:^|\s)(?:cmd(?:\.exe)?|powershell(?:\.exe)?|pwsh|bash|sh|zsh|curl|wget|ffmpeg|"
    r"python(?:\.exe)?|node(?:\.exe)?)(?:\s|$)|(?:&&|\|\||;|\x60|\$\()",
    re.IGNORECASE,
)
_LONG_BLOB_RE = re.compile(r"^[A-Za-z0-9+/=_-]{512,}$")

_FORBIDDEN_KEY_PARTS = {
    "api_key", "apikey", "auth", "authorization", "credential", "password",
    "secret", "token", "path", "filepath", "inputpath", "outputpath",
    "directory", "command", "cmd", "shell", "argv", "executable", "runner",
    "callable", "module", "import", "env", "media", "blob", "weights",
    "checkpoint", "modelfile", "configpath", "url", "uri",
}


def _object_schema(properties: dict[str, Any], required: Iterable[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


_POSITION_SCHEMA = _object_schema(
    {"x": {"type": "number"}, "y": {"type": "number"}},
    ("x", "y"),
)

_NODE_SCHEMA = _object_schema(
    {
        "id": {"type": "string", "pattern": GRAPH_ID_RE.pattern},
        "type": {"type": "string", "pattern": GRAPH_ID_RE.pattern},
        "data": {"type": "object"},
        "properties": {"type": "object"},
        "position": _POSITION_SCHEMA,
    },
    ("id", "type"),
)

_ENDPOINT_SCHEMA = _object_schema(
    {
        "node": {"type": "string", "pattern": GRAPH_ID_RE.pattern},
        "port": {"type": "string", "pattern": GRAPH_ID_RE.pattern},
    },
    ("node", "port"),
)

_EDGE_SCHEMA = _object_schema(
    {"id": {"type": "string", "pattern": GRAPH_ID_RE.pattern}, "source": _ENDPOINT_SCHEMA, "target": _ENDPOINT_SCHEMA},
    ("id", "source", "target"),
)

_GRAPH_SCHEMA = _object_schema(
    {
        "schema_version": {"type": "integer", "minimum": 1},
        "id": {"type": "string", "pattern": GRAPH_ID_RE.pattern},
        "title": {"type": "string", "minLength": 1, "maxLength": MAX_TEXT_LENGTH},
        "scope": {"type": "string", "pattern": GRAPH_ID_RE.pattern},
        "nodes": {"type": "array", "maxItems": MAX_GRAPH_NODES, "items": _NODE_SCHEMA},
        "edges": {"type": "array", "maxItems": MAX_GRAPH_EDGES, "items": _EDGE_SCHEMA},
        "groups": {"type": "array", "maxItems": MAX_GRAPH_GROUPS, "items": {"type": "object"}},
    },
    ("schema_version", "id", "title", "scope", "nodes", "edges"),
)

_WORKFLOW_SCHEMA = _object_schema(
    {
        "schema_version": {"const": WORKFLOW_ENTRY_SCHEMA_VERSION},
        "id": {"type": "string", "pattern": WORKFLOW_ID_RE.pattern},
        "title": {"type": "string", "minLength": 1, "maxLength": 160},
        "description": {"type": "string", "maxLength": MAX_TEXT_LENGTH},
        "scope": {"type": "string", "pattern": GRAPH_ID_RE.pattern},
        "graph": _GRAPH_SCHEMA,
        "revision": {"type": "integer", "minimum": 1},
        "status": {"type": "string", "enum": list(STATUS_VALUES)},
        "source": {"type": "string", "enum": list(SOURCE_VALUES)},
        "tags": {"type": "array", "maxItems": MAX_TAGS, "items": {"type": "string", "maxLength": 64}},
        "created_at": {"type": "string", "maxLength": 80},
        "updated_at": {"type": "string", "maxLength": 80},
    },
    (
        "schema_version", "id", "title", "description", "scope", "graph",
        "revision", "status", "source", "tags", "created_at", "updated_at",
    ),
)


def workflow_library_schema() -> dict[str, Any]:
    """Return the closed Draft 2020-12 schema published by this contract."""

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://local-ai-hub.invalid/schema/workflow-library.v1.json",
        "title": "Local AI Hub Workflow Library",
        **_object_schema(
            {
                "schema_version": {"const": WORKFLOW_LIBRARY_SCHEMA_VERSION},
                "library_revision": {"type": "integer", "minimum": 0},
                "workflows": {"type": "array", "maxItems": MAX_WORKFLOWS, "items": {"$ref": "#/$defs/workflow"}},
            },
            ("schema_version", "library_revision", "workflows"),
        ),
        "$defs": {
            "position": _POSITION_SCHEMA,
            "node": _NODE_SCHEMA,
            "endpoint": _ENDPOINT_SCHEMA,
            "edge": _EDGE_SCHEMA,
            "graph": _GRAPH_SCHEMA,
            "workflow": _WORKFLOW_SCHEMA,
        },
    }


def _issue(code: str, action: str = "Sửa dữ liệu theo contract rồi thử lại.") -> dict[str, str]:
    messages = {
        "type": "Workflow Library phải là object JSON.",
        "schema_version": "Workflow Library dùng contract version không được hỗ trợ.",
        "unknown_field": "Workflow Library có field ngoài allowlist.",
        "missing_field": "Workflow Library thiếu field bắt buộc.",
        "bounds": "Workflow Library vượt giới hạn kích thước hoặc số lượng.",
        "unsafe_key": "Workflow Library chứa key nhạy cảm hoặc không an toàn.",
        "unsafe_value": "Workflow Library chứa path, secret, command, URL hoặc blob không được phép.",
        "invalid_id": "Workflow ID không hợp lệ.",
        "duplicate_id": "Workflow ID bị trùng.",
        "invalid_workflow": "Workflow record không hợp lệ.",
        "invalid_graph": "Graph declarative không hợp lệ.",
        "revision": "Revision phải là số nguyên dương.",
        "conflict": "Revision hiện tại không khớp evidence đã đọc.",
        "invalid_json": "Payload JSON không hợp lệ.",
        "duplicate_json_key": "Payload JSON chứa key trùng.",
        "nonfinite_number": "Payload JSON chứa số không hữu hạn.",
        "invalid_utf8": "Payload JSON không phải UTF-8 hợp lệ.",
    }
    return {"code": code, "message": messages.get(code, "Workflow Library payload không hợp lệ."), "action": action}


def _normalized_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _safe_scalar(value: object) -> bool:
    if isinstance(value, float) and not math.isfinite(value):
        return False
    if isinstance(value, str):
        if len(value) > MAX_TEXT_LENGTH:
            return False
        if (
            _WINDOWS_PATH_RE.search(value)
            or _POSIX_PATH_RE.search(value)
            or _TRAVERSAL_RE.search(value)
            or _FILE_URI_RE.search(value)
            or _DATA_URI_RE.search(value)
            or _SECRET_RE.search(value)
            or _CREDENTIAL_URL_RE.search(value)
            or _COMMAND_RE.search(value)
            or _LONG_BLOB_RE.fullmatch(value)
        ):
            return False
    return value is None or isinstance(value, (str, bool, int, float))


def _safe_tree(value: object, *, depth: int = 0) -> bool:
    if depth > MAX_JSON_DEPTH:
        return False
    if isinstance(value, Mapping):
        if len(value) > MAX_METADATA_KEYS:
            return False
        for key, child in value.items():
            if not isinstance(key, str) or len(key) > MAX_ID_LENGTH:
                return False
            normalized = _normalized_key(key)
            if any(token.replace("_", "") in normalized for token in _FORBIDDEN_KEY_PARTS):
                return False
            if not _safe_tree(child, depth=depth + 1):
                return False
        return True
    if isinstance(value, list):
        return len(value) <= MAX_GRAPH_EDGES and all(_safe_tree(item, depth=depth + 1) for item in value)
    return _safe_scalar(value)


def _copy(value: Any) -> Any:
    return copy.deepcopy(value)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _fingerprint(value: Mapping[str, Any]) -> str:
    semantic = _copy(dict(value))
    semantic.pop("created_at", None)
    semantic.pop("updated_at", None)
    semantic.pop("revision", None)
    return hashlib.sha256(_canonical(semantic).encode("utf-8")).hexdigest()


def _library_fingerprint(value: Mapping[str, Any]) -> str:
    semantic = {
        "schema_version": value.get("schema_version"),
        "workflows": [_fingerprint(item) for item in value.get("workflows", [])],
    }
    return hashlib.sha256(_canonical(semantic).encode("utf-8")).hexdigest()


def _normalize_graph(graph: Mapping[str, Any]) -> dict[str, Any]:
    nodes = sorted((_copy(item) for item in graph.get("nodes", [])), key=lambda item: str(item.get("id", "")))
    edges = sorted((_copy(item) for item in graph.get("edges", [])), key=lambda item: str(item.get("id", "")))
    groups = sorted((_copy(item) for item in graph.get("groups", [])), key=lambda item: _canonical(item))
    return {
        "schema_version": int(graph.get("schema_version", 1)),
        "id": str(graph.get("id", "workflow")),
        "title": str(graph.get("title", "Untitled workflow")),
        "scope": str(graph.get("scope", "image")),
        "nodes": nodes,
        "edges": edges,
        "groups": groups,
    }


def _normalize_workflow(raw: Mapping[str, Any]) -> dict[str, Any]:
    graph = _normalize_graph(raw["graph"])
    return {
        "schema_version": WORKFLOW_ENTRY_SCHEMA_VERSION,
        "id": str(raw["id"]),
        "title": str(raw["title"]),
        "description": str(raw.get("description", "")),
        "scope": str(raw.get("scope", graph.get("scope", "image"))),
        "graph": graph,
        "revision": int(raw.get("revision", 1)),
        "status": str(raw.get("status", "draft")),
        "source": str(raw.get("source", "local")),
        "tags": sorted(dict.fromkeys(str(item) for item in raw.get("tags", []))),
        "created_at": str(raw.get("created_at", "")),
        "updated_at": str(raw.get("updated_at", "")),
    }


def validate_workflow_entry(raw: object) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {"valid": False, "workflow": None, "errors": [_issue("invalid_workflow")]}
    if not _safe_tree(raw):
        return {"valid": False, "workflow": None, "errors": [_issue("unsafe_value")]}
    expected = set(_WORKFLOW_SCHEMA["properties"])
    if set(raw) - expected:
        return {"valid": False, "workflow": None, "errors": [_issue("unknown_field")]}
    if set(_WORKFLOW_SCHEMA["required"]) - set(raw):
        return {"valid": False, "workflow": None, "errors": [_issue("missing_field")]}
    workflow_id = raw.get("id")
    if not isinstance(workflow_id, str) or not WORKFLOW_ID_RE.fullmatch(workflow_id):
        return {"valid": False, "workflow": None, "errors": [_issue("invalid_id")]}
    if raw.get("schema_version") != WORKFLOW_ENTRY_SCHEMA_VERSION:
        return {"valid": False, "workflow": None, "errors": [_issue("schema_version")]}
    if not isinstance(raw.get("revision"), int) or isinstance(raw.get("revision"), bool) or raw["revision"] < 1:
        return {"valid": False, "workflow": None, "errors": [_issue("revision")]}
    if raw.get("status") not in STATUS_VALUES or raw.get("source") not in SOURCE_VALUES:
        return {"valid": False, "workflow": None, "errors": [_issue("invalid_workflow")]}
    if not isinstance(raw.get("tags"), list) or len(raw["tags"]) > MAX_TAGS or not all(isinstance(item, str) and 0 < len(item) <= 64 for item in raw["tags"]):
        return {"valid": False, "workflow": None, "errors": [_issue("bounds")]}
    graph = raw.get("graph")
    if not isinstance(graph, Mapping):
        return {"valid": False, "workflow": None, "errors": [_issue("invalid_graph")]}
    graph_keys = {"schema_version", "id", "title", "scope", "nodes", "edges", "groups"}
    if set(graph) - graph_keys or not {"schema_version", "id", "title", "scope", "nodes", "edges"} <= set(graph):
        return {"valid": False, "workflow": None, "errors": [_issue("invalid_graph")]}
    if not isinstance(graph.get("nodes"), list) or len(graph["nodes"]) > MAX_GRAPH_NODES:
        return {"valid": False, "workflow": None, "errors": [_issue("bounds")]}
    if not isinstance(graph.get("edges"), list) or len(graph["edges"]) > MAX_GRAPH_EDGES:
        return {"valid": False, "workflow": None, "errors": [_issue("bounds")]}
    node_ids: set[str] = set()
    for node in graph["nodes"]:
        if not isinstance(node, Mapping) or not isinstance(node.get("id"), str) or not GRAPH_ID_RE.fullmatch(node["id"]):
            return {"valid": False, "workflow": None, "errors": [_issue("invalid_graph")]}
        if node["id"] in node_ids:
            return {"valid": False, "workflow": None, "errors": [_issue("invalid_graph")]}
        node_ids.add(node["id"])
    edge_ids: set[str] = set()
    for edge in graph["edges"]:
        if not isinstance(edge, Mapping) or not isinstance(edge.get("id"), str) or not GRAPH_ID_RE.fullmatch(edge["id"]):
            return {"valid": False, "workflow": None, "errors": [_issue("invalid_graph")]}
        if edge["id"] in edge_ids:
            return {"valid": False, "workflow": None, "errors": [_issue("invalid_graph")]}
        edge_ids.add(edge["id"])
    normalized = _normalize_workflow(raw)
    return {"valid": True, "workflow": normalized, "errors": [], "fingerprint": _fingerprint(normalized)}


def validate_workflow_library(raw: object) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {"valid": False, "library": None, "errors": [_issue("type")]}
    if not _safe_tree(raw):
        return {"valid": False, "library": None, "errors": [_issue("unsafe_value")]}
    expected = {"schema_version", "library_revision", "workflows"}
    if set(raw) - expected:
        return {"valid": False, "library": None, "errors": [_issue("unknown_field")]}
    if raw.get("schema_version") != WORKFLOW_LIBRARY_SCHEMA_VERSION:
        return {"valid": False, "library": None, "errors": [_issue("schema_version")]}
    revision = raw.get("library_revision")
    workflows = raw.get("workflows")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0 or not isinstance(workflows, list) or len(workflows) > MAX_WORKFLOWS:
        return {"valid": False, "library": None, "errors": [_issue("bounds")]}
    normalized: list[dict[str, Any]] = []
    ids: set[str] = set()
    for item in workflows:
        result = validate_workflow_entry(item)
        if not result["valid"]:
            return {"valid": False, "library": None, "errors": result["errors"]}
        workflow = result["workflow"]
        if workflow["id"] in ids:
            return {"valid": False, "library": None, "errors": [_issue("duplicate_id")]}
        ids.add(workflow["id"])
        normalized.append(workflow)
    normalized.sort(key=lambda item: item["id"])
    library = {"schema_version": WORKFLOW_LIBRARY_SCHEMA_VERSION, "library_revision": revision, "workflows": normalized}
    return {"valid": True, "library": library, "errors": [], "fingerprint": _library_fingerprint(library)}


def canonical_workflow_library(raw: Mapping[str, Any]) -> str:
    result = validate_workflow_library(raw)
    if not result["valid"]:
        raise ValueError(result["errors"][0]["message"])
    return _canonical(result["library"])


def library_fingerprint(raw: Mapping[str, Any]) -> str:
    result = validate_workflow_library(raw)
    if not result["valid"]:
        raise ValueError(result["errors"][0]["message"])
    return str(result["fingerprint"])


def safe_import_workflow_library(payload: str | bytes) -> dict[str, Any]:
    if isinstance(payload, bytes):
        if len(payload) > MAX_LIBRARY_BYTES:
            return {"accepted": False, "library": None, "errors": [_issue("bounds")]}
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError:
            return {"accepted": False, "library": None, "errors": [_issue("invalid_utf8")]}
    if not isinstance(payload, str) or len(payload.encode("utf-8")) > MAX_LIBRARY_BYTES:
        return {"accepted": False, "library": None, "errors": [_issue("bounds")]}

    class _DuplicateKeyError(ValueError):
        pass

    class _NonFiniteError(ValueError):
        pass

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise _DuplicateKeyError("duplicate")
            result[key] = value
        return result

    try:
        value = json.loads(
            payload,
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda _: (_ for _ in ()).throw(_NonFiniteError("nonfinite")),
        )
    except _DuplicateKeyError:
        return {"accepted": False, "library": None, "errors": [_issue("duplicate_json_key")]}
    except _NonFiniteError:
        return {"accepted": False, "library": None, "errors": [_issue("nonfinite_number")]}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {"accepted": False, "library": None, "errors": [_issue("invalid_json")]}
    result = validate_workflow_library(value)
    return {"accepted": result["valid"], "library": result.get("library"), "errors": result["errors"], "fingerprint": result.get("fingerprint")}


def plan_localstorage_migration(entries: object) -> dict[str, Any]:
    """Build a user-mediated, dry-run migration plan without writing anything."""

    if not isinstance(entries, list) or len(entries) > MAX_WORKFLOWS:
        return {
            "schema_version": MIGRATION_PLAN_SCHEMA_VERSION,
            "dry_run": True,
            "status": "recovery_required",
            "plans": [],
            "errors": [_issue("bounds")],
        }
    plans: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for item in entries:
        if not isinstance(item, Mapping):
            errors.append(_issue("invalid_workflow"))
            continue
        result = validate_workflow_entry(item)
        if result["valid"]:
            workflow = result["workflow"]
            plans.append({"id": workflow["id"], "action": "review_then_import", "fingerprint": result["fingerprint"]})
        else:
            errors.extend(result["errors"])
    return {
        "schema_version": MIGRATION_PLAN_SCHEMA_VERSION,
        "dry_run": True,
        "status": "ready" if not errors else "partial",
        "plans": plans,
        "errors": errors[:8],
        "action": "Review each validated entry and explicitly confirm import; no localStorage value is overwritten.",
    }


__all__ = [
    "WORKFLOW_LIBRARY_SCHEMA_VERSION",
    "WORKFLOW_ENTRY_SCHEMA_VERSION",
    "MIGRATION_PLAN_SCHEMA_VERSION",
    "MAX_LIBRARY_BYTES",
    "MAX_WORKFLOWS",
    "workflow_library_schema",
    "validate_workflow_entry",
    "validate_workflow_library",
    "canonical_workflow_library",
    "library_fingerprint",
    "safe_import_workflow_library",
    "plan_localstorage_migration",
]
