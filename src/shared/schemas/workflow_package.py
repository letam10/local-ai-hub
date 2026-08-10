"""Static, dependency-free contracts for portable Local AI Hub workflow packages.

The package format is deliberately descriptive.  It contains no executable
code, filesystem locations, secrets, media payloads, model weights, or shell
instructions.  Consumers must validate a package before projecting it into a
UI or an integration boundary; this module never imports or executes a graph.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections import defaultdict, deque
from typing import Any, Iterable


WORKFLOW_PACKAGE_SCHEMA_VERSION = "workflow-package.v1"
SUBGRAPH_BLUEPRINT_SCHEMA_VERSION = "subgraph-blueprint.v1"
EVALUATION_SCENARIO_SCHEMA_VERSION = "evaluation-scenario.v1"

MAX_PACKAGE_BYTES = 512 * 1024
MAX_TEXT_LENGTH = 2_000
MAX_NODES_PER_BLUEPRINT = 96
MAX_EDGES_PER_BLUEPRINT = 192
MAX_TOTAL_NODES = 192
MAX_TOTAL_EDGES = 384
MAX_SUBGRAPHS = 12
MAX_SUBGRAPH_NESTING = 3
MAX_PORTS = 32
MAX_REQUIRED_NODE_TYPES = 64
MAX_PARAMETERS = 24
MAX_PARAMETER_ENUM_VALUES = 32
MAX_MODEL_REQUIREMENTS = 24
MAX_RUNTIME_REQUIREMENTS = 24
MAX_EXCLUSIVE_RESOURCE_GROUPS = 12
MAX_RUBRIC_ITEMS = 12
MAX_JSON_DEPTH = 32

PORT_TYPES = (
    "AUDIO",
    "BOOLEAN",
    "IMAGE",
    "MASK",
    "METADATA",
    "MODEL",
    "NUMBER",
    "TEXT",
    "VIDEO",
)
PACKAGE_CAPABILITIES = ("audio", "image", "metadata", "text", "utility", "video")
NODE_KINDS = ("input", "operation", "output", "subgraph")
SOURCE_KINDS = ("exported-package", "managed-repository", "manual-authoring")
PARAMETER_TYPES = ("BOOLEAN", "ENUM", "NUMBER", "TEXT")
GPU_VENDORS = ("any", "none", "nvidia")
PREVIEW_CONTENT_TYPES = ("diagram", "metadata", "summary")

PACKAGE_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+){0,9}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,79}$")
SEMVER_RE = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*)?$"
)
VERSION_CONSTRAINT_RE = re.compile(
    r"^(?:>=|>|=)?(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*)?$"
)
SOURCE_REF_RE = re.compile(r"^[a-z][a-z0-9._-]{0,119}$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_WINDOWS_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")
_UNC_PATH_RE = re.compile(r"^(?:\\\\|//)")
_ABSOLUTE_PATH_RE = re.compile(r"^(?:/|~[\\/])")
_PARENT_PATH_RE = re.compile(r"(?:^|[\\/])\.\.(?:[\\/]|$)")
_RELATIVE_PATH_RE = re.compile(r"^(?:[A-Za-z0-9._-]+[\\/])+[A-Za-z0-9._-]+$")
_SECRET_RE = re.compile(
    r"(?:\bsk-[A-Za-z0-9_-]{12,}|\bgh[pous]_[A-Za-z0-9]{12,}|\bgithub_pat_[A-Za-z0-9_]{12,}|"
    r"\bAKIA[0-9A-Z]{12,}|\bBearer\s+[A-Za-z0-9._~+/-]{12,})",
    re.IGNORECASE,
)
_COMMAND_RE = re.compile(
    r"(?:^|\s)(?:cmd(?:\.exe)?|powershell(?:\.exe)?|pwsh|bash|sh|zsh|curl|wget|ffmpeg|python(?:\.exe)?|node(?:\.exe)?)(?:\s|$)|"
    r"(?:&&|\|\||;|`|\$\()",
    re.IGNORECASE,
)
_EMBEDDED_BINARY_RE = re.compile(
    r"(?:^data:(?:image|audio|video|application/octet-stream)/|;base64,|\.(?:safetensors|ckpt|pth|pt|onnx|gguf|bin)(?:$|[?#]))",
    re.IGNORECASE,
)
_FILE_URI_RE = re.compile(r"\bfile://", re.IGNORECASE)
_CREDENTIAL_URL_RE = re.compile(r"https?://[^/\s:@]+:[^@\s]+@", re.IGNORECASE)
_URL_RE = re.compile(r"\b(?:https?|ftp)://", re.IGNORECASE)
_LONG_BLOB_RE = re.compile(r"^[A-Za-z0-9+/=_-]{512,}$")

_FORBIDDEN_KEY_TOKENS = {
    "apikey",
    "argv",
    "auth",
    "authorization",
    "binary",
    "blob",
    "callable",
    "checkpoint",
    "cmd",
    "command",
    "configpath",
    "constructor",
    "credential",
    "data",
    "directory",
    "env",
    "executable",
    "file",
    "filepath",
    "import",
    "inputpath",
    "media",
    "module",
    "modelfile",
    "outputpath",
    "path",
    "password",
    "prototype",
    "python",
    "runner",
    "runtimeconfig",
    "script",
    "secret",
    "shell",
    "token",
    "uri",
    "url",
    "weights",
    "proto",
}


def _schema_object(properties: dict[str, Any], required: Iterable[str]) -> dict[str, Any]:
    """Build a closed JSON Schema object with requirement/property parity."""

    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


_PORT_SCHEMA = _schema_object(
    {
        "id": {"type": "string", "pattern": IDENTIFIER_RE.pattern},
        "type": {"type": "string", "enum": list(PORT_TYPES)},
        "required": {"type": "boolean"},
        "multi": {"type": "boolean"},
        "description": {"type": "string", "maxLength": MAX_TEXT_LENGTH},
    },
    ("id", "type"),
)

_INPUT_NODE_SCHEMA = _schema_object(
    {
        "id": {"type": "string", "pattern": IDENTIFIER_RE.pattern},
        "kind": {"const": "input"},
        "inputs": {"type": "array", "maxItems": 0, "items": {"$ref": "#/$defs/port"}},
        "outputs": {"type": "array", "maxItems": MAX_PORTS, "items": {"$ref": "#/$defs/port"}},
    },
    ("id", "kind", "inputs", "outputs"),
)

_OPERATION_NODE_SCHEMA = _schema_object(
    {
        "id": {"type": "string", "pattern": IDENTIFIER_RE.pattern},
        "kind": {"const": "operation"},
        "operation": {"type": "string", "pattern": IDENTIFIER_RE.pattern},
        "inputs": {"type": "array", "maxItems": MAX_PORTS, "items": {"$ref": "#/$defs/port"}},
        "outputs": {"type": "array", "maxItems": MAX_PORTS, "items": {"$ref": "#/$defs/port"}},
    },
    ("id", "kind", "operation", "inputs", "outputs"),
)

_OUTPUT_NODE_SCHEMA = _schema_object(
    {
        "id": {"type": "string", "pattern": IDENTIFIER_RE.pattern},
        "kind": {"const": "output"},
        "inputs": {"type": "array", "maxItems": MAX_PORTS, "items": {"$ref": "#/$defs/port"}},
        "outputs": {"type": "array", "maxItems": 0, "items": {"$ref": "#/$defs/port"}},
    },
    ("id", "kind", "inputs", "outputs"),
)

_SUBGRAPH_NODE_SCHEMA = _schema_object(
    {
        "id": {"type": "string", "pattern": IDENTIFIER_RE.pattern},
        "kind": {"const": "subgraph"},
        "ref": {"type": "string", "pattern": IDENTIFIER_RE.pattern},
        "inputs": {"type": "array", "maxItems": MAX_PORTS, "items": {"$ref": "#/$defs/port"}},
        "outputs": {"type": "array", "maxItems": MAX_PORTS, "items": {"$ref": "#/$defs/port"}},
    },
    ("id", "kind", "ref", "inputs", "outputs"),
)

# A discriminated ``oneOf`` keeps the published Draft 2020-12 contract in
# lockstep with the Python node-kind rules below.  In particular, an input or
# output node cannot quietly carry an operation/ref field.
_NODE_SCHEMA = {
    "oneOf": [_INPUT_NODE_SCHEMA, _OPERATION_NODE_SCHEMA, _OUTPUT_NODE_SCHEMA, _SUBGRAPH_NODE_SCHEMA]
}

_EDGE_SCHEMA = _schema_object(
    {
        "id": {"type": "string", "pattern": IDENTIFIER_RE.pattern},
        "from": _schema_object(
            {
                "node": {"type": "string", "pattern": IDENTIFIER_RE.pattern},
                "port": {"type": "string", "pattern": IDENTIFIER_RE.pattern},
            },
            ("node", "port"),
        ),
        "to": _schema_object(
            {
                "node": {"type": "string", "pattern": IDENTIFIER_RE.pattern},
                "port": {"type": "string", "pattern": IDENTIFIER_RE.pattern},
            },
            ("node", "port"),
        ),
    },
    ("id", "from", "to"),
)

_BLUEPRINT_SCHEMA = _schema_object(
    {
        "schema_version": {"const": SUBGRAPH_BLUEPRINT_SCHEMA_VERSION},
        "id": {"type": "string", "pattern": IDENTIFIER_RE.pattern},
        "title": {"type": "string", "minLength": 1, "maxLength": 160},
        "description": {"type": "string", "minLength": 1, "maxLength": MAX_TEXT_LENGTH},
        "inputs": {"type": "array", "maxItems": MAX_PORTS, "items": {"$ref": "#/$defs/port"}},
        "outputs": {"type": "array", "maxItems": MAX_PORTS, "items": {"$ref": "#/$defs/port"}},
        "nodes": {"type": "array", "maxItems": MAX_NODES_PER_BLUEPRINT, "items": {"$ref": "#/$defs/node"}},
        "edges": {"type": "array", "maxItems": MAX_EDGES_PER_BLUEPRINT, "items": {"$ref": "#/$defs/edge"}},
    },
    ("schema_version", "id", "title", "description", "inputs", "outputs", "nodes", "edges"),
)

_NUMBER_BOUNDS_SCHEMA = _schema_object(
    {
        "minimum": {"type": "number"},
        "maximum": {"type": "number"},
    },
    ("minimum", "maximum"),
)

_TEXT_BOUNDS_SCHEMA = _schema_object(
    {
        "min_length": {"type": "integer", "minimum": 0, "maximum": MAX_TEXT_LENGTH},
        "max_length": {"type": "integer", "minimum": 0, "maximum": MAX_TEXT_LENGTH},
    },
    ("min_length", "max_length"),
)

_PARAMETER_BASE_PROPERTIES = {
    "id": {"type": "string", "pattern": IDENTIFIER_RE.pattern},
    "type": {"type": "string", "enum": list(PARAMETER_TYPES)},
    "default": {"type": ["boolean", "number", "string"]},
    "bounds": {"type": ["object", "null"]},
    "enum": {"type": "array", "maxItems": MAX_PARAMETER_ENUM_VALUES, "items": {"type": "string", "maxLength": 160}},
    "description": {"type": "string", "minLength": 1, "maxLength": MAX_TEXT_LENGTH},
}

_BOOLEAN_PARAMETER_SCHEMA = _schema_object(
    {
        **_PARAMETER_BASE_PROPERTIES,
        "type": {"const": "BOOLEAN"},
        "default": {"type": "boolean"},
        "bounds": {"type": "null"},
        "enum": {"type": "array", "maxItems": 0},
    },
    ("id", "type", "default", "bounds", "enum", "description"),
)

_NUMBER_PARAMETER_SCHEMA = _schema_object(
    {
        **_PARAMETER_BASE_PROPERTIES,
        "type": {"const": "NUMBER"},
        "default": {"type": "number"},
        "bounds": _NUMBER_BOUNDS_SCHEMA,
        "enum": {"type": "array", "maxItems": 0},
    },
    ("id", "type", "default", "bounds", "enum", "description"),
)

_TEXT_PARAMETER_SCHEMA = _schema_object(
    {
        **_PARAMETER_BASE_PROPERTIES,
        "type": {"const": "TEXT"},
        "default": {"type": "string", "maxLength": MAX_TEXT_LENGTH},
        "bounds": _TEXT_BOUNDS_SCHEMA,
        "enum": {"type": "array", "maxItems": 0},
    },
    ("id", "type", "default", "bounds", "enum", "description"),
)

_ENUM_PARAMETER_SCHEMA = _schema_object(
    {
        **_PARAMETER_BASE_PROPERTIES,
        "type": {"const": "ENUM"},
        "default": {"type": "string", "maxLength": 160},
        "bounds": {"type": "null"},
        "enum": {"type": "array", "minItems": 1, "maxItems": MAX_PARAMETER_ENUM_VALUES, "items": {"type": "string", "minLength": 1, "maxLength": 160}},
    },
    ("id", "type", "default", "bounds", "enum", "description"),
)

_PARAMETER_SCHEMA = {
    "oneOf": [_BOOLEAN_PARAMETER_SCHEMA, _NUMBER_PARAMETER_SCHEMA, _TEXT_PARAMETER_SCHEMA, _ENUM_PARAMETER_SCHEMA]
}

_MODEL_REQUIREMENT_SCHEMA = _schema_object(
    {
        "id": {"type": "string", "pattern": IDENTIFIER_RE.pattern},
        "version": {"type": "string", "pattern": SEMVER_RE.pattern},
    },
    ("id", "version"),
)

_RUNTIME_REQUIREMENT_SCHEMA = _schema_object(
    {
        "id": {"type": "string", "pattern": IDENTIFIER_RE.pattern},
        "version": {"type": "string", "pattern": VERSION_CONSTRAINT_RE.pattern},
    },
    ("id", "version"),
)

_REQUIREMENTS_SCHEMA = _schema_object(
    {
        "models": {"type": "array", "maxItems": MAX_MODEL_REQUIREMENTS, "items": _MODEL_REQUIREMENT_SCHEMA},
        "runtimes": {"type": "array", "maxItems": MAX_RUNTIME_REQUIREMENTS, "items": _RUNTIME_REQUIREMENT_SCHEMA},
    },
    ("models", "runtimes"),
)

_CPU_HINT_SCHEMA = _schema_object(
    {
        "minimum_cores": {"type": "integer", "minimum": 0, "maximum": 256},
        "recommended_cores": {"type": "integer", "minimum": 0, "maximum": 256},
    },
    ("minimum_cores", "recommended_cores"),
)

_GPU_HINT_SCHEMA = _schema_object(
    {
        "required": {"type": "boolean"},
        "vendor": {"type": "string", "enum": list(GPU_VENDORS)},
        "minimum_vram_mb": {"type": "integer", "minimum": 0, "maximum": 1048576},
    },
    ("required", "vendor", "minimum_vram_mb"),
)

_MEMORY_HINT_SCHEMA = _schema_object(
    {
        "minimum_mb": {"type": "integer", "minimum": 0, "maximum": 1048576},
        "recommended_mb": {"type": "integer", "minimum": 0, "maximum": 1048576},
    },
    ("minimum_mb", "recommended_mb"),
)

_RESOURCE_HINTS_SCHEMA = _schema_object(
    {
        "cpu": _CPU_HINT_SCHEMA,
        "gpu": _GPU_HINT_SCHEMA,
        "ram": _MEMORY_HINT_SCHEMA,
        "disk": _MEMORY_HINT_SCHEMA,
        "exclusive_groups": {"type": "array", "maxItems": MAX_EXCLUSIVE_RESOURCE_GROUPS, "items": {"type": "string", "pattern": IDENTIFIER_RE.pattern}},
    },
    ("cpu", "gpu", "ram", "disk", "exclusive_groups"),
)

_PREVIEW_SCHEMA = _schema_object(
    {
        "kind": {"const": "opaque-preview.v1"},
        "id": {"type": "string", "pattern": IDENTIFIER_RE.pattern},
        "content_type": {"type": "string", "enum": list(PREVIEW_CONTENT_TYPES)},
    },
    ("kind", "id", "content_type"),
)

WORKFLOW_PACKAGE_V1_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://local-ai-hub.invalid/schemas/workflow-package.v1.json",
    "title": "Local AI Hub workflow-package.v1",
    **_schema_object(
        {
            "schema_version": {"const": WORKFLOW_PACKAGE_SCHEMA_VERSION},
            "id": {"type": "string", "pattern": PACKAGE_ID_RE.pattern},
            "version": {"type": "string", "pattern": SEMVER_RE.pattern},
            "title": {"type": "string", "minLength": 1, "maxLength": 160},
            "summary": {"type": "string", "minLength": 1, "maxLength": MAX_TEXT_LENGTH},
            "author": _schema_object({"name": {"type": "string", "minLength": 1, "maxLength": 160}}, ("name",)),
            "license": {"type": "string", "minLength": 1, "maxLength": 80},
            "source": _schema_object(
                {
                    "kind": {"type": "string", "enum": list(SOURCE_KINDS)},
                    "reference": {"type": "string", "pattern": SOURCE_REF_RE.pattern},
                },
                ("kind", "reference"),
            ),
            "capabilities": {"type": "array", "minItems": 1, "maxItems": len(PACKAGE_CAPABILITIES), "items": {"type": "string", "enum": list(PACKAGE_CAPABILITIES)}},
            "compatibility": _schema_object(
                {
                    "hub_version": {"type": "string", "pattern": VERSION_CONSTRAINT_RE.pattern},
                    "node_contract": {"const": SUBGRAPH_BLUEPRINT_SCHEMA_VERSION},
                    "required_node_types": {"type": "array", "maxItems": MAX_REQUIRED_NODE_TYPES, "items": {"type": "string", "pattern": IDENTIFIER_RE.pattern}},
                },
                ("hub_version", "node_contract", "required_node_types"),
            ),
            "catalog_ready": {"type": "boolean"},
            "parameters": {"type": "array", "maxItems": MAX_PARAMETERS, "items": {"$ref": "#/$defs/parameter"}},
            "requirements": {"$ref": "#/$defs/requirements"},
            "resource_hints": {"$ref": "#/$defs/resource_hints"},
            "preview": {"$ref": "#/$defs/preview"},
            "workflow": {"$ref": "#/$defs/blueprint"},
            "subgraphs": {"type": "array", "maxItems": MAX_SUBGRAPHS, "items": {"$ref": "#/$defs/blueprint"}},
        },
        (
            "schema_version",
            "id",
            "version",
            "title",
            "summary",
            "author",
            "license",
            "source",
            "capabilities",
            "compatibility",
            "catalog_ready",
            "parameters",
            "requirements",
            "resource_hints",
            "preview",
            "workflow",
            "subgraphs",
        ),
    ),
    "$defs": {
        "port": _PORT_SCHEMA,
        "node": _NODE_SCHEMA,
        "edge": _EDGE_SCHEMA,
        "blueprint": _BLUEPRINT_SCHEMA,
        "parameter": _PARAMETER_SCHEMA,
        "requirements": _REQUIREMENTS_SCHEMA,
        "resource_hints": _RESOURCE_HINTS_SCHEMA,
        "preview": _PREVIEW_SCHEMA,
    },
}

EVALUATION_SCENARIO_V1_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://local-ai-hub.invalid/schemas/evaluation-scenario.v1.json",
    "title": "Local AI Hub evaluation-scenario.v1",
    **_schema_object(
        {
            "schema_version": {"const": EVALUATION_SCENARIO_SCHEMA_VERSION},
            "id": {"type": "string", "pattern": PACKAGE_ID_RE.pattern},
            "title": {"type": "string", "minLength": 1, "maxLength": 160},
            "package": _schema_object(
                {
                    "id": {"type": "string", "pattern": PACKAGE_ID_RE.pattern},
                    "version": {"type": "string", "pattern": SEMVER_RE.pattern},
                },
                ("id", "version"),
            ),
            "candidates": {
                "type": "array",
                "minItems": 2,
                "maxItems": 2,
                "items": _schema_object(
                    {
                        "id": {"type": "string", "enum": ["A", "B"]},
                        "label": {"type": "string", "minLength": 1, "maxLength": 160},
                        "package_version": {"type": "string", "pattern": SEMVER_RE.pattern},
                    },
                    ("id", "label", "package_version"),
                ),
            },
            "rubric": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_RUBRIC_ITEMS,
                "items": _schema_object(
                    {
                        "id": {"type": "string", "pattern": IDENTIFIER_RE.pattern},
                        "label": {"type": "string", "minLength": 1, "maxLength": 160},
                        "guidance": {"type": "string", "minLength": 1, "maxLength": MAX_TEXT_LENGTH},
                        "weight": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
                    },
                    ("id", "label", "guidance", "weight"),
                ),
            },
            "review_protocol": _schema_object(
                {
                    "blind": {"type": "boolean"},
                    "randomize_order": {"type": "boolean"},
                    "human_review_required": {"const": True},
                },
                ("blind", "randomize_order", "human_review_required"),
            ),
            "limitations": {"type": "array", "minItems": 1, "maxItems": 12, "items": {"type": "string", "minLength": 1, "maxLength": MAX_TEXT_LENGTH}},
        },
        ("schema_version", "id", "title", "package", "candidates", "rubric", "review_protocol", "limitations"),
    ),
}


def workflow_package_schema() -> dict[str, Any]:
    """Return a detached Draft 2020-12 schema for offline tooling."""

    return copy.deepcopy(WORKFLOW_PACKAGE_V1_SCHEMA)


def evaluation_scenario_schema() -> dict[str, Any]:
    """Return a detached Draft 2020-12 evaluation schema."""

    return copy.deepcopy(EVALUATION_SCENARIO_V1_SCHEMA)


def compare_semver(left: str, right: str) -> int:
    """Compare two validated SemVer values, including prerelease precedence."""

    if not SEMVER_RE.fullmatch(left) or not SEMVER_RE.fullmatch(right):
        raise ValueError("SemVer comparison requires valid package versions")

    def split(value: str) -> tuple[tuple[int, int, int], tuple[str, ...] | None]:
        core, marker, prerelease = value.partition("-")
        return tuple(int(part) for part in core.split(".")), tuple(prerelease.split(".")) if marker else None  # type: ignore[return-value]

    left_core, left_pre = split(left)
    right_core, right_pre = split(right)
    if left_core != right_core:
        return -1 if left_core < right_core else 1
    if left_pre is None and right_pre is None:
        return 0
    if left_pre is None:
        return 1
    if right_pre is None:
        return -1
    for left_part, right_part in zip(left_pre, right_pre):
        if left_part == right_part:
            continue
        left_numeric = left_part.isdecimal()
        right_numeric = right_part.isdecimal()
        if left_numeric and right_numeric:
            return -1 if int(left_part) < int(right_part) else 1
        if left_numeric != right_numeric:
            return -1 if left_numeric else 1
        return -1 if left_part < right_part else 1
    if len(left_pre) == len(right_pre):
        return 0
    return -1 if len(left_pre) < len(right_pre) else 1


def _issue(code: str, message: str, *, location: str) -> dict[str, str]:
    """Issues intentionally omit rejected values to avoid reflecting secrets."""

    return {"code": code, "message": message, "location": location}


def _append(errors: list[dict[str, str]], code: str, message: str, location: str) -> None:
    errors.append(_issue(code, message, location=location))


def _json_bytes(value: object) -> bytes | None:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (RecursionError, TypeError, ValueError):
        return None


def _within_depth(value: object, errors: list[dict[str, str]], location: str) -> bool:
    """Bound traversal before inspecting untrusted nested JSON values."""

    pending: list[tuple[object, int]] = [(value, 1)]
    while pending:
        current, depth = pending.pop()
        if depth > MAX_JSON_DEPTH:
            _append(errors, "json_depth", "JSON nesting exceeds the static safety bound.", location)
            return False
        if isinstance(current, dict):
            pending.extend((child, depth + 1) for child in current.values())
        elif isinstance(current, list):
            pending.extend((child, depth + 1) for child in current)
    return True


def _strict_keys(value: object, allowed: set[str], errors: list[dict[str, str]], location: str) -> bool:
    if not isinstance(value, dict):
        _append(errors, "object_required", "Value must be a JSON object.", location)
        return False
    for key in value:
        if not isinstance(key, str) or key not in allowed:
            _append(errors, "unknown_field", "Unknown fields are not permitted in this static contract.", location)
    return True


def _require(value: dict[str, Any], fields: Iterable[str], errors: list[dict[str, str]], location: str) -> None:
    for field in fields:
        if field not in value:
            _append(errors, "required_field", f"Required field '{field}' is missing.", f"{location}.{field}")


def _safe_text(value: object, errors: list[dict[str, str]], location: str, *, minimum: int = 0, maximum: int = MAX_TEXT_LENGTH) -> bool:
    if not isinstance(value, str):
        _append(errors, "string_required", "Value must be a string.", location)
        return False
    if not minimum <= len(value) <= maximum:
        _append(errors, "text_length", "Text is outside the allowed length bound.", location)
        return False
    if "\x00" in value:
        _append(errors, "control_character", "NUL characters are not permitted.", location)
    if _WINDOWS_PATH_RE.search(value) or _UNC_PATH_RE.search(value) or _ABSOLUTE_PATH_RE.search(value) or _PARENT_PATH_RE.search(value) or _RELATIVE_PATH_RE.fullmatch(value):
        _append(errors, "raw_path", "Raw filesystem paths are not permitted in workflow packages.", location)
    if _FILE_URI_RE.search(value) or _CREDENTIAL_URL_RE.search(value) or _URL_RE.search(value):
        _append(errors, "unsafe_uri", "URLs and filesystem URIs are not permitted in workflow packages.", location)
    if _SECRET_RE.search(value):
        _append(errors, "secret_detected", "Secrets and credential-like values are not permitted.", location)
    if _COMMAND_RE.search(value):
        _append(errors, "command_detected", "Commands and shell syntax are not permitted.", location)
    if _EMBEDDED_BINARY_RE.search(value):
        _append(errors, "embedded_binary", "Media payloads and model-weight references are not permitted.", location)
    if _LONG_BLOB_RE.fullmatch(value):
        _append(errors, "embedded_binary", "Large inline blobs are not permitted.", location)
    return True


def _scan_forbidden_content(value: object, errors: list[dict[str, str]], location: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            key_token = re.sub(r"[^a-z]", "", key.casefold()) if isinstance(key, str) else ""
            if key_token in _FORBIDDEN_KEY_TOKENS:
                _append(errors, "forbidden_field", "Raw paths, secrets, commands, binary payloads, and model weights are not permitted.", location)
            _scan_forbidden_content(child, errors, f"{location}.{key}" if isinstance(key, str) else location)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_forbidden_content(child, errors, f"{location}[{index}]")
    elif isinstance(value, str):
        _safe_text(value, errors, location)


def _identifier(value: object, errors: list[dict[str, str]], location: str, *, package: bool = False) -> bool:
    if not _safe_text(value, errors, location, minimum=1, maximum=120):
        return False
    pattern = PACKAGE_ID_RE if package else IDENTIFIER_RE
    if isinstance(value, str) and not pattern.fullmatch(value):
        _append(errors, "identifier", "Identifier does not match the stable package contract.", location)
        return False
    return isinstance(value, str)


def _unique_strings(value: object, errors: list[dict[str, str]], location: str, *, maximum: int) -> list[str]:
    if not isinstance(value, list):
        _append(errors, "array_required", "Value must be an array.", location)
        return []
    if len(value) > maximum:
        _append(errors, "array_limit", "Array exceeds the static safety bound.", location)
    result: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        item_location = f"{location}[{index}]"
        if not isinstance(item, str):
            _append(errors, "string_required", "Array members must be strings.", item_location)
            continue
        if item in seen:
            _append(errors, "duplicate_value", "Duplicate values are not permitted.", item_location)
            continue
        seen.add(item)
        result.append(item)
    return result


def _finite_number(value: object, errors: list[dict[str, str]], location: str) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        _append(errors, "number_required", "Value must be a finite JSON number.", location)
        return None
    return float(value)


def _bounded_integer(value: object, errors: list[dict[str, str]], location: str, *, maximum: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        _append(errors, "resource_bound", "Value must be a bounded non-negative integer.", location)
        return None
    return value


def _validate_parameters(value: object, errors: list[dict[str, str]], location: str) -> None:
    if not isinstance(value, list):
        _append(errors, "array_required", "Parameters must be an array.", location)
        return
    if len(value) > MAX_PARAMETERS:
        _append(errors, "parameter_limit", "Parameter count exceeds the static safety bound.", location)
    parameter_ids: set[str] = set()
    required = ("id", "type", "default", "bounds", "enum", "description")
    allowed = set(required)
    for index, item in enumerate(value):
        item_location = f"{location}[{index}]"
        if not _strict_keys(item, allowed, errors, item_location):
            continue
        assert isinstance(item, dict)
        _require(item, required, errors, item_location)
        parameter_id = item.get("id")
        _identifier(parameter_id, errors, f"{item_location}.id")
        if isinstance(parameter_id, str):
            if parameter_id in parameter_ids:
                _append(errors, "duplicate_parameter_id", "Parameter IDs must be unique.", f"{item_location}.id")
            parameter_ids.add(parameter_id)
        parameter_type = item.get("type")
        if parameter_type not in PARAMETER_TYPES:
            _append(errors, "parameter_type", "Parameter type is not part of the static allowlist.", f"{item_location}.type")
        _safe_text(item.get("description"), errors, f"{item_location}.description", minimum=1)
        default = item.get("default")
        bounds = item.get("bounds")
        enum = item.get("enum")
        if not isinstance(enum, list):
            _append(errors, "array_required", "Parameter enum must be an array.", f"{item_location}.enum")
            enum = []
        if len(enum) > MAX_PARAMETER_ENUM_VALUES:
            _append(errors, "parameter_enum_limit", "Parameter enum exceeds the static safety bound.", f"{item_location}.enum")
        if parameter_type == "BOOLEAN":
            if not isinstance(default, bool):
                _append(errors, "parameter_default", "BOOLEAN parameters require a boolean default.", f"{item_location}.default")
            if bounds is not None:
                _append(errors, "parameter_bounds", "BOOLEAN parameters must use null bounds.", f"{item_location}.bounds")
            if enum:
                _append(errors, "parameter_enum", "BOOLEAN parameters must not declare enum values.", f"{item_location}.enum")
        elif parameter_type == "NUMBER":
            default_number = _finite_number(default, errors, f"{item_location}.default")
            if _strict_keys(bounds, {"minimum", "maximum"}, errors, f"{item_location}.bounds"):
                assert isinstance(bounds, dict)
                _require(bounds, ("minimum", "maximum"), errors, f"{item_location}.bounds")
                minimum = _finite_number(bounds.get("minimum"), errors, f"{item_location}.bounds.minimum")
                maximum = _finite_number(bounds.get("maximum"), errors, f"{item_location}.bounds.maximum")
                if minimum is not None and maximum is not None and minimum > maximum:
                    _append(errors, "parameter_bounds", "Parameter minimum cannot exceed maximum.", f"{item_location}.bounds")
                if default_number is not None and minimum is not None and maximum is not None and not minimum <= default_number <= maximum:
                    _append(errors, "parameter_default", "Number default must be within declared bounds.", f"{item_location}.default")
            if enum:
                _append(errors, "parameter_enum", "NUMBER parameters must not declare enum values.", f"{item_location}.enum")
        elif parameter_type == "TEXT":
            if not isinstance(default, str):
                _append(errors, "parameter_default", "TEXT parameters require a string default.", f"{item_location}.default")
            else:
                _safe_text(default, errors, f"{item_location}.default", maximum=MAX_TEXT_LENGTH)
            if _strict_keys(bounds, {"min_length", "max_length"}, errors, f"{item_location}.bounds"):
                assert isinstance(bounds, dict)
                _require(bounds, ("min_length", "max_length"), errors, f"{item_location}.bounds")
                minimum = _bounded_integer(bounds.get("min_length"), errors, f"{item_location}.bounds.min_length", maximum=MAX_TEXT_LENGTH)
                maximum = _bounded_integer(bounds.get("max_length"), errors, f"{item_location}.bounds.max_length", maximum=MAX_TEXT_LENGTH)
                if minimum is not None and maximum is not None and minimum > maximum:
                    _append(errors, "parameter_bounds", "Minimum text length cannot exceed maximum length.", f"{item_location}.bounds")
                if isinstance(default, str) and minimum is not None and maximum is not None and not minimum <= len(default) <= maximum:
                    _append(errors, "parameter_default", "Text default must be within declared length bounds.", f"{item_location}.default")
            if enum:
                _append(errors, "parameter_enum", "TEXT parameters must not declare enum values.", f"{item_location}.enum")
        elif parameter_type == "ENUM":
            if not isinstance(default, str):
                _append(errors, "parameter_default", "ENUM parameters require a string default.", f"{item_location}.default")
            else:
                _safe_text(default, errors, f"{item_location}.default", minimum=1, maximum=160)
            if bounds is not None:
                _append(errors, "parameter_bounds", "ENUM parameters must use null bounds.", f"{item_location}.bounds")
            enum_values: set[str] = set()
            if not enum:
                _append(errors, "parameter_enum", "ENUM parameters require at least one allowed value.", f"{item_location}.enum")
            for enum_index, enum_value in enumerate(enum):
                enum_location = f"{item_location}.enum[{enum_index}]"
                _safe_text(enum_value, errors, enum_location, minimum=1, maximum=160)
                if isinstance(enum_value, str):
                    if enum_value in enum_values:
                        _append(errors, "duplicate_parameter_enum", "Enum values must be unique.", enum_location)
                    enum_values.add(enum_value)
            if isinstance(default, str) and default not in enum_values:
                _append(errors, "parameter_default", "Enum default must be one declared enum value.", f"{item_location}.default")


def _validate_requirement_list(value: object, errors: list[dict[str, str]], location: str, *, maximum: int, runtime: bool) -> None:
    if not isinstance(value, list):
        _append(errors, "array_required", "Requirements must be an array.", location)
        return
    if len(value) > maximum:
        _append(errors, "requirement_limit", "Requirement count exceeds the static safety bound.", location)
    seen_ids: set[str] = set()
    for index, item in enumerate(value):
        item_location = f"{location}[{index}]"
        if not _strict_keys(item, {"id", "version"}, errors, item_location):
            continue
        assert isinstance(item, dict)
        _require(item, ("id", "version"), errors, item_location)
        requirement_id = item.get("id")
        _identifier(requirement_id, errors, f"{item_location}.id")
        if isinstance(requirement_id, str):
            if requirement_id in seen_ids:
                _append(errors, "duplicate_requirement_id", "Requirement IDs must be unique within their kind.", f"{item_location}.id")
            seen_ids.add(requirement_id)
        version = item.get("version")
        _safe_text(version, errors, f"{item_location}.version", minimum=1, maximum=80)
        pattern = VERSION_CONSTRAINT_RE if runtime else SEMVER_RE
        if isinstance(version, str) and not pattern.fullmatch(version):
            _append(errors, "version_constraint" if runtime else "semver", "Requirement version is outside the closed static contract.", f"{item_location}.version")


def _validate_requirements(value: object, errors: list[dict[str, str]], location: str) -> None:
    required = ("models", "runtimes")
    if not _strict_keys(value, set(required), errors, location):
        return
    assert isinstance(value, dict)
    _require(value, required, errors, location)
    _validate_requirement_list(value.get("models"), errors, f"{location}.models", maximum=MAX_MODEL_REQUIREMENTS, runtime=False)
    _validate_requirement_list(value.get("runtimes"), errors, f"{location}.runtimes", maximum=MAX_RUNTIME_REQUIREMENTS, runtime=True)


def _validate_resource_hints(value: object, errors: list[dict[str, str]], location: str) -> None:
    required = ("cpu", "gpu", "ram", "disk", "exclusive_groups")
    if not _strict_keys(value, set(required), errors, location):
        return
    assert isinstance(value, dict)
    _require(value, required, errors, location)

    def validate_pair(item: object, item_location: str, first: str, second: str, *, maximum: int) -> None:
        if not _strict_keys(item, {first, second}, errors, item_location):
            return
        assert isinstance(item, dict)
        _require(item, (first, second), errors, item_location)
        minimum = _bounded_integer(item.get(first), errors, f"{item_location}.{first}", maximum=maximum)
        recommended = _bounded_integer(item.get(second), errors, f"{item_location}.{second}", maximum=maximum)
        if minimum is not None and recommended is not None and recommended < minimum:
            _append(errors, "resource_hint_order", "Recommended resource hint cannot be below minimum.", item_location)

    validate_pair(value.get("cpu"), f"{location}.cpu", "minimum_cores", "recommended_cores", maximum=256)
    validate_pair(value.get("ram"), f"{location}.ram", "minimum_mb", "recommended_mb", maximum=1048576)
    validate_pair(value.get("disk"), f"{location}.disk", "minimum_mb", "recommended_mb", maximum=1048576)
    gpu = value.get("gpu")
    if _strict_keys(gpu, {"required", "vendor", "minimum_vram_mb"}, errors, f"{location}.gpu"):
        assert isinstance(gpu, dict)
        _require(gpu, ("required", "vendor", "minimum_vram_mb"), errors, f"{location}.gpu")
        required_gpu = gpu.get("required")
        if not isinstance(required_gpu, bool):
            _append(errors, "boolean_required", "GPU required must be a boolean.", f"{location}.gpu.required")
        vendor = gpu.get("vendor")
        if vendor not in GPU_VENDORS:
            _append(errors, "gpu_vendor", "GPU vendor is not part of the static allowlist.", f"{location}.gpu.vendor")
        vram = _bounded_integer(gpu.get("minimum_vram_mb"), errors, f"{location}.gpu.minimum_vram_mb", maximum=1048576)
        if required_gpu is True and vendor == "none":
            _append(errors, "gpu_hint", "A required GPU cannot use the none vendor.", f"{location}.gpu.vendor")
        if required_gpu is False and (vendor != "none" or vram not in {None, 0}):
            _append(errors, "gpu_hint", "A non-required GPU must use vendor none and zero VRAM.", f"{location}.gpu")
    groups = value.get("exclusive_groups")
    if not isinstance(groups, list):
        _append(errors, "array_required", "Exclusive resource groups must be an array.", f"{location}.exclusive_groups")
        return
    if len(groups) > MAX_EXCLUSIVE_RESOURCE_GROUPS:
        _append(errors, "resource_group_limit", "Exclusive resource group count exceeds the static safety bound.", f"{location}.exclusive_groups")
    seen_groups: set[str] = set()
    for index, group in enumerate(groups):
        group_location = f"{location}.exclusive_groups[{index}]"
        _identifier(group, errors, group_location)
        if isinstance(group, str):
            if group in seen_groups:
                _append(errors, "duplicate_resource_group", "Exclusive resource groups must be unique.", group_location)
            seen_groups.add(group)


def _validate_preview(value: object, errors: list[dict[str, str]], location: str) -> None:
    required = ("kind", "id", "content_type")
    if not _strict_keys(value, set(required), errors, location):
        return
    assert isinstance(value, dict)
    _require(value, required, errors, location)
    if value.get("kind") != "opaque-preview.v1":
        _append(errors, "preview_kind", "Preview metadata must declare opaque-preview.v1.", f"{location}.kind")
    _identifier(value.get("id"), errors, f"{location}.id")
    if value.get("content_type") not in PREVIEW_CONTENT_TYPES:
        _append(errors, "preview_content_type", "Preview content type is not part of the static allowlist.", f"{location}.content_type")


def _validate_port_list(value: object, errors: list[dict[str, str]], location: str, *, allow_required: bool = True) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list):
        _append(errors, "array_required", "Ports must be an array.", location)
        return {}
    if len(value) > MAX_PORTS:
        _append(errors, "port_limit", "Port count exceeds the static safety bound.", location)
    result: dict[str, dict[str, Any]] = {}
    for index, port in enumerate(value):
        item_location = f"{location}[{index}]"
        if not _strict_keys(port, {"id", "type", "required", "multi", "description"}, errors, item_location):
            continue
        assert isinstance(port, dict)
        _require(port, ("id", "type"), errors, item_location)
        port_id = port.get("id")
        _identifier(port_id, errors, f"{item_location}.id")
        kind = port.get("type")
        if kind not in PORT_TYPES:
            _append(errors, "port_type", "Port type is not part of the typed blueprint allowlist.", f"{item_location}.type")
        for optional_bool in ("required", "multi"):
            if optional_bool in port and not isinstance(port[optional_bool], bool):
                _append(errors, "boolean_required", "Port flags must be booleans.", f"{item_location}.{optional_bool}")
        if not allow_required and port.get("required"):
            _append(errors, "output_required", "Blueprint outputs may not declare required=true.", f"{item_location}.required")
        if "description" in port:
            _safe_text(port["description"], errors, f"{item_location}.description", minimum=1)
        if isinstance(port_id, str):
            if port_id in result:
                _append(errors, "duplicate_port_id", "Port IDs must be unique within their owner.", f"{item_location}.id")
            else:
                result[port_id] = {
                    "id": port_id,
                    "type": kind,
                    "required": bool(port.get("required", False)),
                    "multi": bool(port.get("multi", False)),
                }
    return result


def _port_signature(ports: dict[str, dict[str, Any]]) -> tuple[tuple[str, str, bool, bool], ...]:
    return tuple(sorted((item["id"], str(item["type"]), bool(item["required"]), bool(item["multi"])) for item in ports.values()))


def _validate_blueprint(
    value: object,
    errors: list[dict[str, str]],
    location: str,
    *,
    declared_operations: set[str],
    catalog_ready: bool,
) -> dict[str, Any] | None:
    allowed = {"schema_version", "id", "title", "description", "inputs", "outputs", "nodes", "edges"}
    if not _strict_keys(value, allowed, errors, location):
        return None
    assert isinstance(value, dict)
    _require(value, allowed, errors, location)
    if value.get("schema_version") != SUBGRAPH_BLUEPRINT_SCHEMA_VERSION:
        _append(errors, "blueprint_schema", "Blueprint must declare subgraph-blueprint.v1.", f"{location}.schema_version")
    blueprint_id = value.get("id")
    _identifier(blueprint_id, errors, f"{location}.id")
    _safe_text(value.get("title"), errors, f"{location}.title", minimum=1, maximum=160)
    _safe_text(value.get("description"), errors, f"{location}.description", minimum=1)
    inputs = _validate_port_list(value.get("inputs"), errors, f"{location}.inputs")
    outputs = _validate_port_list(value.get("outputs"), errors, f"{location}.outputs", allow_required=False)
    nodes_value = value.get("nodes")
    edges_value = value.get("edges")
    node_map: dict[str, dict[str, Any]] = {}
    references: list[str] = []
    operation_ids: list[str] = []
    if not isinstance(nodes_value, list):
        _append(errors, "array_required", "Blueprint nodes must be an array.", f"{location}.nodes")
        nodes_value = []
    if len(nodes_value) > MAX_NODES_PER_BLUEPRINT:
        _append(errors, "node_limit", "Blueprint node count exceeds the static safety bound.", f"{location}.nodes")
    for index, node in enumerate(nodes_value):
        node_location = f"{location}.nodes[{index}]"
        if not _strict_keys(node, {"id", "kind", "operation", "ref", "inputs", "outputs"}, errors, node_location):
            continue
        assert isinstance(node, dict)
        _require(node, ("id", "kind", "inputs", "outputs"), errors, node_location)
        node_id = node.get("id")
        _identifier(node_id, errors, f"{node_location}.id")
        kind = node.get("kind")
        if kind not in NODE_KINDS:
            _append(errors, "node_kind", "Node kind is not part of the static allowlist.", f"{node_location}.kind")
        node_inputs = _validate_port_list(node.get("inputs"), errors, f"{node_location}.inputs")
        node_outputs = _validate_port_list(node.get("outputs"), errors, f"{node_location}.outputs", allow_required=False)
        if kind == "operation":
            if set(node) - {"id", "kind", "operation", "inputs", "outputs"}:
                _append(errors, "node_field", "Operation nodes cannot carry unbounded fields.", node_location)
            if "operation" not in node:
                _append(errors, "operation_required", "Operation nodes require a declared operation ID.", node_location)
            operation = node.get("operation")
            _identifier(operation, errors, f"{node_location}.operation")
            if isinstance(operation, str):
                operation_ids.append(operation)
                if operation not in declared_operations:
                    _append(errors, "undeclared_operation", "Operation must appear in compatibility.required_node_types.", f"{node_location}.operation")
        elif kind == "subgraph":
            if set(node) - {"id", "kind", "ref", "inputs", "outputs"}:
                _append(errors, "node_field", "Subgraph nodes cannot carry unbounded fields.", node_location)
            if "ref" not in node:
                _append(errors, "subgraph_ref_required", "Subgraph nodes require a declared blueprint reference.", node_location)
            ref = node.get("ref")
            _identifier(ref, errors, f"{node_location}.ref")
            if isinstance(ref, str):
                references.append(ref)
        elif kind in {"input", "output"}:
            if set(node) - {"id", "kind", "inputs", "outputs"}:
                _append(errors, "node_field", "Input and output nodes cannot carry operations or arbitrary data.", node_location)
            if kind == "input" and node_inputs:
                _append(errors, "input_node_ports", "Input nodes may only expose output ports.", f"{node_location}.inputs")
            if kind == "output" and node_outputs:
                _append(errors, "output_node_ports", "Output nodes may only accept input ports.", f"{node_location}.outputs")
        if isinstance(node_id, str):
            if node_id in node_map:
                _append(errors, "duplicate_node_id", "Node IDs must be unique within a blueprint.", f"{node_location}.id")
            else:
                node_map[node_id] = {
                    "id": node_id,
                    "kind": kind,
                    "inputs": node_inputs,
                    "outputs": node_outputs,
                    "ref": node.get("ref"),
                }
    input_bindings: dict[str, int] = defaultdict(int)
    output_bindings: dict[str, int] = defaultdict(int)
    for node in node_map.values():
        if node["kind"] == "input":
            for port in node["outputs"].values():
                blueprint_port = inputs.get(port["id"])
                if blueprint_port is None or blueprint_port["type"] != port["type"]:
                    _append(errors, "input_port_contract", "Input node ports must match declared blueprint inputs.", f"{location}.nodes")
                else:
                    input_bindings[port["id"]] += 1
                    if input_bindings[port["id"]] > 1:
                        _append(errors, "duplicate_public_input_binding", "Each public blueprint input may be bound by only one input node port.", f"{location}.nodes")
        elif node["kind"] == "output":
            for port in node["inputs"].values():
                blueprint_port = outputs.get(port["id"])
                if blueprint_port is None or blueprint_port["type"] != port["type"]:
                    _append(errors, "output_port_contract", "Output node ports must match declared blueprint outputs.", f"{location}.nodes")
                else:
                    output_bindings[port["id"]] += 1
                    if output_bindings[port["id"]] > 1:
                        _append(errors, "duplicate_public_output_binding", "Each public blueprint output may be bound by only one output node port.", f"{location}.nodes")
    if catalog_ready:
        for port_id in sorted(inputs):
            if input_bindings.get(port_id, 0) != 1:
                _append(errors, "catalog_input_binding", "Catalog-ready blueprints require exactly one binding for every declared input.", f"{location}.inputs")
        for port_id in sorted(outputs):
            if output_bindings.get(port_id, 0) != 1:
                _append(errors, "catalog_output_binding", "Catalog-ready blueprints require exactly one binding for every declared output.", f"{location}.outputs")
    if not isinstance(edges_value, list):
        _append(errors, "array_required", "Blueprint edges must be an array.", f"{location}.edges")
        edges_value = []
    if len(edges_value) > MAX_EDGES_PER_BLUEPRINT:
        _append(errors, "edge_limit", "Blueprint edge count exceeds the static safety bound.", f"{location}.edges")
    edge_ids: set[str] = set()
    incoming: dict[tuple[str, str], int] = defaultdict(int)
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in node_map}
    indegree: dict[str, int] = {node_id: 0 for node_id in node_map}
    for index, edge in enumerate(edges_value):
        edge_location = f"{location}.edges[{index}]"
        if not _strict_keys(edge, {"id", "from", "to"}, errors, edge_location):
            continue
        assert isinstance(edge, dict)
        _require(edge, ("id", "from", "to"), errors, edge_location)
        edge_id = edge.get("id")
        _identifier(edge_id, errors, f"{edge_location}.id")
        if isinstance(edge_id, str):
            if edge_id in edge_ids:
                _append(errors, "duplicate_edge_id", "Edge IDs must be unique within a blueprint.", f"{edge_location}.id")
            edge_ids.add(edge_id)
        source = edge.get("from")
        target = edge.get("to")
        if not _strict_keys(source, {"node", "port"}, errors, f"{edge_location}.from") or not _strict_keys(target, {"node", "port"}, errors, f"{edge_location}.to"):
            continue
        assert isinstance(source, dict) and isinstance(target, dict)
        _require(source, ("node", "port"), errors, f"{edge_location}.from")
        _require(target, ("node", "port"), errors, f"{edge_location}.to")
        source_node = source.get("node")
        source_port = source.get("port")
        target_node = target.get("node")
        target_port = target.get("port")
        _identifier(source_node, errors, f"{edge_location}.from.node")
        _identifier(source_port, errors, f"{edge_location}.from.port")
        _identifier(target_node, errors, f"{edge_location}.to.node")
        _identifier(target_port, errors, f"{edge_location}.to.port")
        if not all(isinstance(item, str) for item in (source_node, source_port, target_node, target_port)):
            continue
        source_record = node_map.get(source_node)
        target_record = node_map.get(target_node)
        if source_record is None or target_record is None:
            _append(errors, "edge_unknown_node", "Edges may reference only declared nodes.", edge_location)
            continue
        source_definition = source_record["outputs"].get(source_port)
        target_definition = target_record["inputs"].get(target_port)
        if source_definition is None or target_definition is None:
            _append(errors, "edge_unknown_port", "Edges may reference only declared typed ports.", edge_location)
            continue
        if source_definition["type"] != target_definition["type"]:
            _append(errors, "type_mismatch", "Edges must connect exactly matching port types.", edge_location)
            continue
        incoming[(target_node, target_port)] += 1
        if incoming[(target_node, target_port)] > 1 and not target_definition["multi"]:
            _append(errors, "input_multiplicity", "A non-multi input may receive only one edge.", edge_location)
            continue
        if target_node not in adjacency[source_node]:
            adjacency[source_node].add(target_node)
            indegree[target_node] += 1
    queue = deque(sorted(node_id for node_id, degree in indegree.items() if degree == 0))
    seen_nodes: list[str] = []
    remaining = dict(indegree)
    while queue:
        node_id = queue.popleft()
        seen_nodes.append(node_id)
        for child in sorted(adjacency[node_id]):
            remaining[child] -= 1
            if remaining[child] == 0:
                queue.append(child)
    if len(seen_nodes) != len(node_map):
        _append(errors, "cycle_detected", "Blueprint graphs must be acyclic DAGs.", f"{location}.edges")
    return {
        "id": blueprint_id,
        "inputs": inputs,
        "outputs": outputs,
        "references": references,
        "operations": operation_ids,
        "input_bindings": dict(input_bindings),
        "output_bindings": dict(output_bindings),
        "node_count": len(nodes_value),
        "edge_count": len(edges_value),
        "node_map": node_map,
    }


def _validate_nested_subgraphs(
    entry: dict[str, Any] | None,
    subgraphs: dict[str, dict[str, Any]],
    errors: list[dict[str, str]],
) -> None:
    if entry is None:
        return
    for blueprint_id, blueprint in [("__entry__", entry), *sorted(subgraphs.items())]:
        for ref in blueprint["references"]:
            target = subgraphs.get(ref)
            if target is None:
                _append(errors, "unknown_subgraph", "Subgraph references must resolve inside the same package.", "workflow")
                continue
            caller = next((node for node in blueprint["node_map"].values() if node["kind"] == "subgraph" and node.get("ref") == ref), None)
            if caller is not None and (_port_signature(caller["inputs"]) != _port_signature(target["inputs"]) or _port_signature(caller["outputs"]) != _port_signature(target["outputs"])):
                _append(errors, "subgraph_type_contract", "Subgraph call ports must exactly match the referenced typed blueprint.", "workflow")

    def walk(current: str, depth: int, stack: tuple[str, ...]) -> None:
        if depth > MAX_SUBGRAPH_NESTING:
            _append(errors, "subgraph_nesting", "Subgraph nesting exceeds the static safety bound.", "workflow")
            return
        blueprint = entry if current == "__entry__" else subgraphs.get(current)
        if blueprint is None:
            return
        for target in sorted(set(blueprint["references"])):
            if target not in subgraphs:
                continue
            if target in stack:
                _append(errors, "subgraph_cycle", "Subgraph references must not form cycles.", "workflow")
                continue
            walk(target, depth + 1, (*stack, target))

    walk("__entry__", 0, ("__entry__",))


def validate_workflow_package(value: object) -> dict[str, Any]:
    """Validate a package without executing or persisting any of its contents.

    On failure no untrusted package is returned.  That makes the result safe for
    service-layer callers to use as the sole input to public projections.
    """

    errors: list[dict[str, str]] = []
    encoded = _json_bytes(value)
    if encoded is None:
        return {"valid": False, "errors": [_issue("json_value", "Package must contain JSON-compatible values only.", location="package")], "package": None}
    if len(encoded) > MAX_PACKAGE_BYTES:
        return {"valid": False, "errors": [_issue("package_size", "Package exceeds the static size bound.", location="package")], "package": None}
    if not _within_depth(value, errors, "package"):
        return {"valid": False, "errors": errors, "package": None}
    required = (
        "schema_version",
        "id",
        "version",
        "title",
        "summary",
        "author",
        "license",
        "source",
        "capabilities",
        "compatibility",
        "catalog_ready",
        "parameters",
        "requirements",
        "resource_hints",
        "preview",
        "workflow",
        "subgraphs",
    )
    if not _strict_keys(value, set(required), errors, "package"):
        return {"valid": False, "errors": errors, "package": None}
    assert isinstance(value, dict)
    _require(value, required, errors, "package")
    _scan_forbidden_content(value, errors, "package")
    if value.get("schema_version") != WORKFLOW_PACKAGE_SCHEMA_VERSION:
        _append(errors, "package_schema", "Package must declare workflow-package.v1.", "package.schema_version")
    _identifier(value.get("id"), errors, "package.id", package=True)
    version = value.get("version")
    _safe_text(version, errors, "package.version", minimum=1, maximum=80)
    if isinstance(version, str) and not SEMVER_RE.fullmatch(version):
        _append(errors, "semver", "Package version must be SemVer.", "package.version")
    _safe_text(value.get("title"), errors, "package.title", minimum=1, maximum=160)
    _safe_text(value.get("summary"), errors, "package.summary", minimum=1)
    author = value.get("author")
    if _strict_keys(author, {"name"}, errors, "package.author"):
        assert isinstance(author, dict)
        _require(author, ("name",), errors, "package.author")
        _safe_text(author.get("name"), errors, "package.author.name", minimum=1, maximum=160)
    _safe_text(value.get("license"), errors, "package.license", minimum=1, maximum=80)
    source = value.get("source")
    if _strict_keys(source, {"kind", "reference"}, errors, "package.source"):
        assert isinstance(source, dict)
        _require(source, ("kind", "reference"), errors, "package.source")
        if source.get("kind") not in SOURCE_KINDS:
            _append(errors, "source_kind", "Source kind is not on the static allowlist.", "package.source.kind")
        reference = source.get("reference")
        _safe_text(reference, errors, "package.source.reference", minimum=1, maximum=120)
        if isinstance(reference, str) and not SOURCE_REF_RE.fullmatch(reference):
            _append(errors, "source_reference", "Source reference must be a logical opaque identifier, not a path or URL.", "package.source.reference")
    capabilities = _unique_strings(value.get("capabilities"), errors, "package.capabilities", maximum=len(PACKAGE_CAPABILITIES))
    if not capabilities:
        _append(errors, "capabilities_required", "At least one package capability is required.", "package.capabilities")
    for index, capability in enumerate(capabilities):
        if capability not in PACKAGE_CAPABILITIES:
            _append(errors, "capability", "Capability is not on the static allowlist.", f"package.capabilities[{index}]")
    compatibility = value.get("compatibility")
    declared_operations: set[str] = set()
    if _strict_keys(compatibility, {"hub_version", "node_contract", "required_node_types"}, errors, "package.compatibility"):
        assert isinstance(compatibility, dict)
        _require(compatibility, ("hub_version", "node_contract", "required_node_types"), errors, "package.compatibility")
        hub_version = compatibility.get("hub_version")
        _safe_text(hub_version, errors, "package.compatibility.hub_version", minimum=1, maximum=80)
        if isinstance(hub_version, str) and not VERSION_CONSTRAINT_RE.fullmatch(hub_version):
            _append(errors, "version_constraint", "Hub compatibility must be a bounded SemVer constraint.", "package.compatibility.hub_version")
        if compatibility.get("node_contract") != SUBGRAPH_BLUEPRINT_SCHEMA_VERSION:
            _append(errors, "node_contract", "Compatibility must declare subgraph-blueprint.v1.", "package.compatibility.node_contract")
        declared = _unique_strings(compatibility.get("required_node_types"), errors, "package.compatibility.required_node_types", maximum=MAX_REQUIRED_NODE_TYPES)
        for index, item in enumerate(declared):
            _identifier(item, errors, f"package.compatibility.required_node_types[{index}]")
        declared_operations = set(declared)
    catalog_ready = value.get("catalog_ready")
    if not isinstance(catalog_ready, bool):
        _append(errors, "boolean_required", "Catalog readiness must be a boolean.", "package.catalog_ready")
    _validate_parameters(value.get("parameters"), errors, "package.parameters")
    _validate_requirements(value.get("requirements"), errors, "package.requirements")
    _validate_resource_hints(value.get("resource_hints"), errors, "package.resource_hints")
    _validate_preview(value.get("preview"), errors, "package.preview")
    entry = _validate_blueprint(
        value.get("workflow"),
        errors,
        "package.workflow",
        declared_operations=declared_operations,
        catalog_ready=catalog_ready is True,
    )
    subgraphs_value = value.get("subgraphs")
    subgraphs: dict[str, dict[str, Any]] = {}
    if not isinstance(subgraphs_value, list):
        _append(errors, "array_required", "Subgraphs must be an array.", "package.subgraphs")
        subgraphs_value = []
    if len(subgraphs_value) > MAX_SUBGRAPHS:
        _append(errors, "subgraph_limit", "Package exceeds the subgraph count bound.", "package.subgraphs")
    for index, blueprint in enumerate(subgraphs_value):
        inspection = _validate_blueprint(
            blueprint,
            errors,
            f"package.subgraphs[{index}]",
            declared_operations=declared_operations,
            catalog_ready=catalog_ready is True,
        )
        if inspection is not None and isinstance(inspection.get("id"), str):
            blueprint_id = inspection["id"]
            if blueprint_id in subgraphs:
                _append(errors, "duplicate_subgraph_id", "Subgraph IDs must be unique within a package.", f"package.subgraphs[{index}].id")
            else:
                subgraphs[blueprint_id] = inspection
    all_inspections = [item for item in [entry, *subgraphs.values()] if item is not None]
    if sum(item["node_count"] for item in all_inspections) > MAX_TOTAL_NODES:
        _append(errors, "total_node_limit", "Package node count exceeds the static safety bound.", "package")
    if sum(item["edge_count"] for item in all_inspections) > MAX_TOTAL_EDGES:
        _append(errors, "total_edge_limit", "Package edge count exceeds the static safety bound.", "package")
    _validate_nested_subgraphs(entry, subgraphs, errors)
    if errors:
        return {"valid": False, "errors": errors, "package": None}
    normalized = copy.deepcopy(value)
    canonical = canonical_workflow_package_json(normalized)
    return {
        "valid": True,
        "errors": [],
        "package": normalized,
        "fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "limits": {
            "max_package_bytes": MAX_PACKAGE_BYTES,
            "max_total_nodes": MAX_TOTAL_NODES,
            "max_total_edges": MAX_TOTAL_EDGES,
            "max_subgraph_nesting": MAX_SUBGRAPH_NESTING,
        },
    }


def validate_evaluation_scenario(value: object) -> dict[str, Any]:
    """Validate a human-only A/B review plan; it never starts a benchmark."""

    errors: list[dict[str, str]] = []
    encoded = _json_bytes(value)
    if encoded is None or len(encoded or b"") > MAX_PACKAGE_BYTES:
        return {"valid": False, "errors": [_issue("scenario_size", "Scenario must be bounded JSON.", location="scenario")], "scenario": None}
    if not _within_depth(value, errors, "scenario"):
        return {"valid": False, "errors": errors, "scenario": None}
    allowed = {"schema_version", "id", "title", "package", "candidates", "rubric", "review_protocol", "limitations"}
    if not _strict_keys(value, allowed, errors, "scenario"):
        return {"valid": False, "errors": errors, "scenario": None}
    assert isinstance(value, dict)
    _require(value, allowed, errors, "scenario")
    _scan_forbidden_content(value, errors, "scenario")
    if value.get("schema_version") != EVALUATION_SCENARIO_SCHEMA_VERSION:
        _append(errors, "scenario_schema", "Scenario must declare evaluation-scenario.v1.", "scenario.schema_version")
    _identifier(value.get("id"), errors, "scenario.id", package=True)
    _safe_text(value.get("title"), errors, "scenario.title", minimum=1, maximum=160)
    package = value.get("package")
    if _strict_keys(package, {"id", "version"}, errors, "scenario.package"):
        assert isinstance(package, dict)
        _require(package, ("id", "version"), errors, "scenario.package")
        _identifier(package.get("id"), errors, "scenario.package.id", package=True)
        package_version = package.get("version")
        _safe_text(package_version, errors, "scenario.package.version", minimum=1, maximum=80)
        if isinstance(package_version, str) and not SEMVER_RE.fullmatch(package_version):
            _append(errors, "semver", "Scenario package version must be SemVer.", "scenario.package.version")
    candidates = value.get("candidates")
    candidate_ids: set[str] = set()
    candidate_versions: set[str] = set()
    if not isinstance(candidates, list) or len(candidates) != 2:
        _append(errors, "ab_candidates", "A scenario must define exactly two A/B candidates.", "scenario.candidates")
        candidates = []
    for index, candidate in enumerate(candidates):
        location = f"scenario.candidates[{index}]"
        if not _strict_keys(candidate, {"id", "label", "package_version"}, errors, location):
            continue
        assert isinstance(candidate, dict)
        _require(candidate, ("id", "label", "package_version"), errors, location)
        candidate_id = candidate.get("id")
        if candidate_id not in {"A", "B"}:
            _append(errors, "candidate_id", "Candidates must use exactly the A and B identifiers.", f"{location}.id")
        elif candidate_id in candidate_ids:
            _append(errors, "duplicate_candidate", "Candidates must use distinct A and B identifiers.", f"{location}.id")
        else:
            candidate_ids.add(candidate_id)
        _safe_text(candidate.get("label"), errors, f"{location}.label", minimum=1, maximum=160)
        version = candidate.get("package_version")
        _safe_text(version, errors, f"{location}.package_version", minimum=1, maximum=80)
        if isinstance(version, str) and not SEMVER_RE.fullmatch(version):
            _append(errors, "semver", "Candidate package version must be SemVer.", f"{location}.package_version")
        if isinstance(version, str):
            candidate_versions.add(version)
    if candidate_ids != {"A", "B"}:
        _append(errors, "candidate_pair", "Scenario requires one A and one B candidate.", "scenario.candidates")
    if len(candidate_versions) != 2:
        _append(errors, "candidate_reference", "A and B must reference distinct package versions.", "scenario.candidates")
    rubric = value.get("rubric")
    rubric_ids: set[str] = set()
    weights = 0.0
    if not isinstance(rubric, list) or not rubric:
        _append(errors, "rubric_required", "Scenario must include at least one human review criterion.", "scenario.rubric")
        rubric = []
    if len(rubric) > MAX_RUBRIC_ITEMS:
        _append(errors, "rubric_limit", "Scenario rubric exceeds the static safety bound.", "scenario.rubric")
    for index, item in enumerate(rubric):
        location = f"scenario.rubric[{index}]"
        if not _strict_keys(item, {"id", "label", "guidance", "weight"}, errors, location):
            continue
        assert isinstance(item, dict)
        _require(item, ("id", "label", "guidance", "weight"), errors, location)
        rubric_id = item.get("id")
        _identifier(rubric_id, errors, f"{location}.id")
        if isinstance(rubric_id, str):
            if rubric_id in rubric_ids:
                _append(errors, "duplicate_rubric_id", "Rubric IDs must be unique.", f"{location}.id")
            rubric_ids.add(rubric_id)
        _safe_text(item.get("label"), errors, f"{location}.label", minimum=1, maximum=160)
        _safe_text(item.get("guidance"), errors, f"{location}.guidance", minimum=1)
        weight = item.get("weight")
        if not isinstance(weight, (int, float)) or isinstance(weight, bool) or not 0 < float(weight) <= 1:
            _append(errors, "rubric_weight", "Rubric weight must be a number in (0, 1].", f"{location}.weight")
        else:
            weights += float(weight)
    if rubric and abs(weights - 1.0) > 0.000001:
        _append(errors, "rubric_weight_total", "Rubric weights must add up to exactly 1.0.", "scenario.rubric")
    protocol = value.get("review_protocol")
    if _strict_keys(protocol, {"blind", "randomize_order", "human_review_required"}, errors, "scenario.review_protocol"):
        assert isinstance(protocol, dict)
        _require(protocol, ("blind", "randomize_order", "human_review_required"), errors, "scenario.review_protocol")
        for key in ("blind", "randomize_order", "human_review_required"):
            if not isinstance(protocol.get(key), bool):
                _append(errors, "boolean_required", "Review protocol fields must be booleans.", f"scenario.review_protocol.{key}")
        if protocol.get("human_review_required") is not True:
            _append(errors, "human_review_required", "Evaluation scenarios must remain human-review only.", "scenario.review_protocol.human_review_required")
    limitations = value.get("limitations")
    if not isinstance(limitations, list) or not limitations:
        _append(errors, "limitations_required", "Scenario must state at least one limitation.", "scenario.limitations")
        limitations = []
    if len(limitations) > 12:
        _append(errors, "limitations_limit", "Scenario limitations exceed the static safety bound.", "scenario.limitations")
    for index, limitation in enumerate(limitations):
        _safe_text(limitation, errors, f"scenario.limitations[{index}]", minimum=1)
    if errors:
        return {"valid": False, "errors": errors, "scenario": None}
    normalized = copy.deepcopy(value)
    canonical = canonical_evaluation_scenario_json(normalized)
    return {"valid": True, "errors": [], "scenario": normalized, "fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest(), "execution": "not_run"}


def _sorted_ports(value: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [copy.deepcopy(item) for item in sorted(value, key=lambda item: str(item["id"]))]


def _canonical_blueprint(value: dict[str, Any]) -> dict[str, Any]:
    """Return a detached blueprint with semantically unordered entities sorted."""

    result = copy.deepcopy(value)
    result["inputs"] = _sorted_ports(result["inputs"])
    result["outputs"] = _sorted_ports(result["outputs"])
    nodes: list[dict[str, Any]] = []
    for node in sorted(result["nodes"], key=lambda item: str(item["id"])):
        item = copy.deepcopy(node)
        item["inputs"] = _sorted_ports(item["inputs"])
        item["outputs"] = _sorted_ports(item["outputs"])
        nodes.append(item)
    result["nodes"] = nodes
    result["edges"] = [copy.deepcopy(item) for item in sorted(result["edges"], key=lambda item: str(item["id"]))]
    return result


def canonical_workflow_package_json(package: dict[str, Any]) -> str:
    """Canonical JSON for an already-valid package; never reads from disk."""

    value = copy.deepcopy(package)
    value["capabilities"] = sorted(value["capabilities"])
    value["compatibility"]["required_node_types"] = sorted(value["compatibility"]["required_node_types"])
    value["parameters"] = [copy.deepcopy(item) for item in sorted(value["parameters"], key=lambda item: str(item["id"]))]
    for parameter in value["parameters"]:
        parameter["enum"] = sorted(parameter["enum"])
    value["requirements"]["models"] = [copy.deepcopy(item) for item in sorted(value["requirements"]["models"], key=lambda item: str(item["id"]))]
    value["requirements"]["runtimes"] = [copy.deepcopy(item) for item in sorted(value["requirements"]["runtimes"], key=lambda item: str(item["id"]))]
    value["resource_hints"]["exclusive_groups"] = sorted(value["resource_hints"]["exclusive_groups"])
    value["workflow"] = _canonical_blueprint(value["workflow"])
    value["subgraphs"] = [_canonical_blueprint(item) for item in sorted(value["subgraphs"], key=lambda item: str(item["id"]))]
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def canonical_evaluation_scenario_json(scenario: dict[str, Any]) -> str:
    """Canonical JSON for an already-valid static human-review scenario."""

    value = copy.deepcopy(scenario)
    value["candidates"] = [copy.deepcopy(item) for item in sorted(value["candidates"], key=lambda item: str(item["id"]))]
    value["rubric"] = [copy.deepcopy(item) for item in sorted(value["rubric"], key=lambda item: str(item["id"]))]
    value["limitations"] = sorted(value["limitations"])
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
