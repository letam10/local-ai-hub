"""Fail-closed, static contracts for asset intelligence and provenance.

The contracts in this module intentionally carry only bounded descriptive
metadata.  They do not open a file, contact a provider, compute a hash,
load a model, execute a workflow, or delete an asset.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import defaultdict, deque
from typing import Any, Iterable


ASSET_RECORD_SCHEMA_VERSION = "asset-record.v1"
ASSET_CATALOG_SCHEMA_VERSION = "asset-catalog.v1"
PROVENANCE_LINEAGE_SCHEMA_VERSION = "provenance-lineage.v1"
SMART_COLLECTION_SCHEMA_VERSION = "smart-collection.v1"

MAX_DESCRIPTOR_BYTES = 512 * 1024
MAX_TEXT_LENGTH = 2_000
MAX_ASSETS_PER_CATALOG = 256
MAX_FINGERPRINTS_PER_KIND = 8
MAX_LINEAGE_NODES = 192
MAX_LINEAGE_EDGES = 384
MAX_RETENTION_DAYS = 3_650
MAX_QUERY_DEPTH = 5
MAX_QUERY_TERMS = 16
MAX_QUERY_NODES = 64
MAX_JSON_DEPTH = 32

ASSET_KINDS = ("audio", "document", "image", "other", "video")
MEDIA_TYPES = (
    "application/pdf",
    "application/octet-stream",
    "audio/wav",
    "image/jpeg",
    "image/png",
    "text/plain",
    "video/mp4",
)
SOURCE_KINDS = ("derived", "external-metadata", "managed-catalog")
RETENTION_CLASSES = ("preserve", "standard", "temporary")
PERCEPTUAL_ALGORITHMS = ("dhash-v1", "phash-v1")
LINEAGE_NODE_KINDS = ("asset", "package", "recipe", "run", "workflow")
LINEAGE_RELATIONS = {
    "package_to_run": ("package", "run"),
    "recipe_to_workflow": ("recipe", "workflow"),
    "run_to_derivative": ("run", "asset"),
    "source_to_recipe": ("asset", "recipe"),
    "source_to_workflow": ("asset", "workflow"),
    "workflow_to_package": ("workflow", "package"),
}
SMART_COLLECTION_FIELDS = (
    "asset.bytes",
    "asset.kind",
    "asset.media_type",
    "exact_duplicate",
    "fingerprints.has_embedding",
    "fingerprints.has_perceptual",
    "retention.classification",
)
SMART_COLLECTION_OPERATORS = ("eq", "gte", "in", "lte")

OPAQUE_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+){0,11}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,79}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SHORT_DIGEST_RE = re.compile(r"^[0-9a-f]{16,128}$")

_WINDOWS_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")
_WINDOWS_DRIVE_RELATIVE_RE = re.compile(r"^[A-Za-z]:(?![\\/])")
_WINDOWS_ROOT_RELATIVE_RE = re.compile(r"^[\\/](?![\\/])")
_UNC_PATH_RE = re.compile(r"^(?:\\\\|//)")
_ABSOLUTE_PATH_RE = re.compile(r"^(?:/|~[\\/])")
_PARENT_PATH_RE = re.compile(r"(?:^|[\\/])\.\.(?:[\\/]|$)")
_RELATIVE_PATH_RE = re.compile(r"^(?:[A-Za-z0-9._-]+[\\/])+[A-Za-z0-9._-]+$")
_URL_RE = re.compile(r"\b(?:https?|ftp)://", re.IGNORECASE)
_FILE_URI_RE = re.compile(r"\bfile://", re.IGNORECASE)
_CREDENTIAL_URL_RE = re.compile(r"https?://[^/\s:@]+:[^@\s]+@", re.IGNORECASE)
_SECRET_RE = re.compile(
    r"(?:\bsk-[A-Za-z0-9_-]{12,}|\bgh[pous]_[A-Za-z0-9]{12,}|\bgithub_pat_[A-Za-z0-9_]{12,}|"
    r"\bAKIA[0-9A-Z]{12,}|\bBearer\s+[A-Za-z0-9._~+/-]{12,})",
    re.IGNORECASE,
)
_SENSITIVE_ASSIGNMENT_RE = re.compile(r"\b(?:api[_-]?key|authorization|credential|password|secret|token)\s*(?:=|:)\s*[^\s,;]{6,}", re.IGNORECASE)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_COMMAND_RE = re.compile(
    r"(?:^|\s)(?:cmd(?:\.exe)?|powershell(?:\.exe)?|pwsh|bash|sh|zsh|curl|wget|ffmpeg|python(?:\.exe)?|node(?:\.exe)?)(?:\s|$)|"
    r"(?:&&|\|\||;|`|\$\()",
    re.IGNORECASE,
)
_EMBEDDED_BINARY_RE = re.compile(
    r"(?:^data:(?:image|audio|video|application/octet-stream)/|;base64,|\.(?:safetensors|ckpt|pth|pt|onnx|gguf|bin)(?:$|[?#]))",
    re.IGNORECASE,
)
_LONG_BLOB_RE = re.compile(r"^[A-Za-z0-9+/=_-]{512,}$")
_FORBIDDEN_KEY_TOKENS = {
    "apikey",
    "argv",
    "attachment",
    "authorization",
    "binary",
    "blob",
    "callable",
    "checkpoint",
    "cmd",
    "command",
    "config",
    "configpath",
    "constructor",
    "credential",
    "data",
    "directory",
    "env",
    "executable",
    "file",
    "filepath",
    "image",
    "import",
    "inputpath",
    "media",
    "modelconfig",
    "modelfile",
    "module",
    "outputpath",
    "password",
    "path",
    "proto",
    "prototype",
    "python",
    "runner",
    "script",
    "secret",
    "shell",
    "token",
    "uri",
    "url",
    "video",
    "weights",
}


def _schema_object(properties: dict[str, Any], required: Iterable[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


_OPAQUE_ID_SCHEMA = {
    "type": "string",
    "pattern": OPAQUE_ID_RE.pattern,
    "maxLength": 120,
    "allOf": [{"not": {"pattern": r"[\u0000-\u001F]"}}],
}
_IDENTIFIER_SCHEMA = {
    "type": "string",
    "pattern": IDENTIFIER_RE.pattern,
    "maxLength": 80,
    "allOf": [{"not": {"pattern": r"[\u0000-\u001F]"}}],
}


_SAFE_TEXT_SCHEMA = {
    "type": "string",
    "minLength": 1,
    "maxLength": MAX_TEXT_LENGTH,
    "allOf": [
        {"not": {"pattern": r"[\u0000-\u001F]"}},
        {"not": {"pattern": r"^(?:[A-Za-z]:|[\\/]|~[\\/])"}},
        {"not": {"pattern": r"^(?:[A-Za-z0-9._-]+[\\/])+[A-Za-z0-9._-]+$"}},
        {"not": {"pattern": r"(?:[Ff][Ii][Ll][Ee]|[Hh][Tt][Tt][Pp][Ss]?|[Ff][Tt][Pp]|[Dd][Aa][Tt][Aa]):"}},
        {"not": {"pattern": r"\b(?:api[_-]?key|authorization|credential|password|secret|token)\s*(?:=|:)"}},
        {"not": {"pattern": r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"}},
        {"not": {"pattern": r"\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pous]_[A-Za-z0-9]{12,}|github_pat_[A-Za-z0-9_]{12,}|AKIA[0-9A-Z]{12,})"}},
        {"not": {"pattern": r"(?:^|\s)(?:cmd(?:\.exe)?|powershell(?:\.exe)?|pwsh|bash|sh|zsh|curl|wget|ffmpeg|python(?:\.exe)?|node(?:\.exe)?)(?:\s|$)"}},
        {"not": {"pattern": r"(?:^|\s)(?:CMD(?:\.EXE)?|PowerShell(?:\.EXE)?|PWSH|BASH|SH|ZSH|CURL|WGET|FFMPEG|PYTHON(?:\.EXE)?|NODE(?:\.EXE)?)(?:\s|$)"}},
        {"not": {"pattern": r"(?:;base64,|\.(?:safetensors|ckpt|pth|pt|onnx|gguf|bin)(?:$|[?#]))"}},
        {"not": {"pattern": r"(?:&&|\|\||;|`|\$\()"}},
    ],
}


_ASSET_SCHEMA = _schema_object(
    {
        "kind": {"type": "string", "enum": list(ASSET_KINDS)},
        "media_type": {"type": "string", "enum": list(MEDIA_TYPES)},
        "sha256": {"type": "string", "pattern": SHA256_RE.pattern, "minLength": 64, "maxLength": 64},
        "bytes": {"type": "integer", "minimum": 0, "maximum": 1099511627776},
    },
    ("kind", "media_type", "sha256", "bytes"),
)

_SOURCE_SCHEMA = _schema_object(
    {
        "kind": {"type": "string", "enum": list(SOURCE_KINDS)},
        "reference": _OPAQUE_ID_SCHEMA,
    },
    ("kind", "reference"),
)

_RETENTION_SCHEMA = _schema_object(
    {
        "classification": {"type": "string", "enum": list(RETENTION_CLASSES)},
        "days": {"type": "integer", "minimum": 0, "maximum": MAX_RETENTION_DAYS},
    },
    ("classification", "days"),
)

_PERCEPTUAL_FINGERPRINT_SCHEMA = _schema_object(
    {
        "algorithm": {"type": "string", "enum": list(PERCEPTUAL_ALGORITHMS)},
        "digest": {"type": "string", "pattern": SHORT_DIGEST_RE.pattern, "minLength": 16, "maxLength": 128},
        "status": {"const": "not_run"},
    },
    ("algorithm", "digest", "status"),
)

_EMBEDDING_FINGERPRINT_SCHEMA = _schema_object(
    {
        "provider_id": _OPAQUE_ID_SCHEMA,
        "model_id": _OPAQUE_ID_SCHEMA,
        "dimension": {"type": "integer", "minimum": 1, "maximum": 8192},
        "digest": {"type": "string", "pattern": SHA256_RE.pattern, "minLength": 64, "maxLength": 64},
        "status": {"const": "not_run"},
    },
    ("provider_id", "model_id", "dimension", "digest", "status"),
)

_FINGERPRINTS_SCHEMA = _schema_object(
    {
        "perceptual": {"type": "array", "maxItems": MAX_FINGERPRINTS_PER_KIND, "items": _PERCEPTUAL_FINGERPRINT_SCHEMA},
        "embedding": {"type": "array", "maxItems": MAX_FINGERPRINTS_PER_KIND, "items": _EMBEDDING_FINGERPRINT_SCHEMA},
    },
    ("perceptual", "embedding"),
)

_ASSET_RECORD_CONTRACT = _schema_object(
    {
        "schema_version": {"const": ASSET_RECORD_SCHEMA_VERSION},
        "id": _OPAQUE_ID_SCHEMA,
        "label": {**_SAFE_TEXT_SCHEMA, "maxLength": 160},
        "asset": _ASSET_SCHEMA,
        "source": _SOURCE_SCHEMA,
        "retention": _RETENTION_SCHEMA,
        "fingerprints": _FINGERPRINTS_SCHEMA,
    },
    ("schema_version", "id", "label", "asset", "source", "retention", "fingerprints"),
)

ASSET_RECORD_V1_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://local-ai-hub.invalid/schemas/asset-record.v1.json",
    "title": "Local AI Hub asset-record.v1",
    **_ASSET_RECORD_CONTRACT,
}

ASSET_CATALOG_V1_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://local-ai-hub.invalid/schemas/asset-catalog.v1.json",
    "title": "Local AI Hub asset-catalog.v1",
    **_schema_object(
        {
            "schema_version": {"const": ASSET_CATALOG_SCHEMA_VERSION},
            "id": _OPAQUE_ID_SCHEMA,
            "label": {**_SAFE_TEXT_SCHEMA, "maxLength": 160},
            "assets": {"type": "array", "minItems": 1, "maxItems": MAX_ASSETS_PER_CATALOG, "items": _ASSET_RECORD_CONTRACT},
        },
        ("schema_version", "id", "label", "assets"),
    ),
}

_LINEAGE_NODE_SCHEMA = _schema_object(
    {
        "id": _IDENTIFIER_SCHEMA,
        "kind": {"type": "string", "enum": list(LINEAGE_NODE_KINDS)},
        "reference": _OPAQUE_ID_SCHEMA,
        "retention_days": {"type": "integer", "minimum": 0, "maximum": MAX_RETENTION_DAYS},
    },
    ("id", "kind", "reference", "retention_days"),
)

_LINEAGE_EDGE_SCHEMA = _schema_object(
    {
        "id": _IDENTIFIER_SCHEMA,
        "from": _IDENTIFIER_SCHEMA,
        "to": _IDENTIFIER_SCHEMA,
        "relation": {"type": "string", "enum": sorted(LINEAGE_RELATIONS)},
    },
    ("id", "from", "to", "relation"),
)

PROVENANCE_LINEAGE_V1_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://local-ai-hub.invalid/schemas/provenance-lineage.v1.json",
    "title": "Local AI Hub provenance-lineage.v1",
    **_schema_object(
        {
            "schema_version": {"const": PROVENANCE_LINEAGE_SCHEMA_VERSION},
            "id": _OPAQUE_ID_SCHEMA,
            "label": {**_SAFE_TEXT_SCHEMA, "maxLength": 160},
            "retention_bound_days": {"type": "integer", "minimum": 0, "maximum": MAX_RETENTION_DAYS},
            "nodes": {"type": "array", "minItems": 2, "maxItems": MAX_LINEAGE_NODES, "items": _LINEAGE_NODE_SCHEMA},
            "edges": {"type": "array", "minItems": 1, "maxItems": MAX_LINEAGE_EDGES, "items": _LINEAGE_EDGE_SCHEMA},
        },
        ("schema_version", "id", "label", "retention_bound_days", "nodes", "edges"),
    ),
}

def _enum_query_condition(field: str, values: Iterable[str]) -> dict[str, Any]:
    allowed = list(values)
    return {
        "oneOf": [
            _schema_object(
                {
                    "field": {"const": field},
                    "operator": {"const": "eq"},
                    "value": {"type": "string", "enum": allowed},
                },
                ("field", "operator", "value"),
            ),
            _schema_object(
                {
                    "field": {"const": field},
                    "operator": {"const": "in"},
                    "value": {"type": "array", "minItems": 1, "maxItems": 8, "uniqueItems": True, "items": {"type": "string", "enum": allowed}},
                },
                ("field", "operator", "value"),
            ),
        ]
    }


def _boolean_query_condition(field: str) -> dict[str, Any]:
    return _schema_object(
        {
            "field": {"const": field},
            "operator": {"const": "eq"},
            "value": {"type": "boolean"},
        },
        ("field", "operator", "value"),
    )


_QUERY_CONDITION_SCHEMA = {
    "oneOf": [
        _schema_object(
            {
                "field": {"const": "asset.bytes"},
                "operator": {"type": "string", "enum": ["eq", "gte", "lte"]},
                "value": {"type": "integer", "minimum": 0, "maximum": 1099511627776},
            },
            ("field", "operator", "value"),
        ),
        _enum_query_condition("asset.kind", ASSET_KINDS),
        _enum_query_condition("asset.media_type", MEDIA_TYPES),
        _enum_query_condition("retention.classification", RETENTION_CLASSES),
        _boolean_query_condition("exact_duplicate"),
        _boolean_query_condition("fingerprints.has_embedding"),
        _boolean_query_condition("fingerprints.has_perceptual"),
    ]
}

_QUERY_ALL_SCHEMA = _schema_object(
    {"all": {"type": "array", "minItems": 1, "maxItems": MAX_QUERY_TERMS, "items": {"$ref": "#/$defs/query"}}},
    ("all",),
)
_QUERY_ANY_SCHEMA = _schema_object(
    {"any": {"type": "array", "minItems": 1, "maxItems": MAX_QUERY_TERMS, "items": {"$ref": "#/$defs/query"}}},
    ("any",),
)
_QUERY_SCHEMA = {"oneOf": [_QUERY_CONDITION_SCHEMA, _QUERY_ALL_SCHEMA, _QUERY_ANY_SCHEMA]}

SMART_COLLECTION_V1_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://local-ai-hub.invalid/schemas/smart-collection.v1.json",
    "title": "Local AI Hub smart-collection.v1",
    **_schema_object(
        {
            "schema_version": {"const": SMART_COLLECTION_SCHEMA_VERSION},
            "id": _OPAQUE_ID_SCHEMA,
            "label": {**_SAFE_TEXT_SCHEMA, "maxLength": 160},
            "query": {"$ref": "#/$defs/query"},
        },
        ("schema_version", "id", "label", "query"),
    ),
    "$defs": {"query": _QUERY_SCHEMA},
}


def asset_record_schema() -> dict[str, Any]:
    return copy.deepcopy(ASSET_RECORD_V1_SCHEMA)


def asset_catalog_schema() -> dict[str, Any]:
    return copy.deepcopy(ASSET_CATALOG_V1_SCHEMA)


def provenance_lineage_schema() -> dict[str, Any]:
    return copy.deepcopy(PROVENANCE_LINEAGE_V1_SCHEMA)


def smart_collection_schema() -> dict[str, Any]:
    return copy.deepcopy(SMART_COLLECTION_V1_SCHEMA)


def _issue(code: str, message: str, location: str) -> dict[str, str]:
    return {"code": code, "message": message, "location": location}


def _append(errors: list[dict[str, str]], code: str, message: str, location: str) -> None:
    errors.append(_issue(code, message, location))


def _json_bytes(value: object) -> bytes | None:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (RecursionError, TypeError, ValueError):
        return None


def _within_depth(value: object, errors: list[dict[str, str]], location: str) -> bool:
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
    if "\x00" in value or any(ord(char) < 32 for char in value):
        _append(errors, "control_character", "Control characters are not permitted.", location)
    if _WINDOWS_PATH_RE.search(value) or _WINDOWS_DRIVE_RELATIVE_RE.search(value) or _WINDOWS_ROOT_RELATIVE_RE.search(value) or _UNC_PATH_RE.search(value) or _ABSOLUTE_PATH_RE.search(value) or _PARENT_PATH_RE.search(value) or (value not in MEDIA_TYPES and _RELATIVE_PATH_RE.fullmatch(value)):
        _append(errors, "raw_path", "Raw filesystem paths are not permitted in asset metadata.", location)
    if _FILE_URI_RE.search(value) or _CREDENTIAL_URL_RE.search(value) or _URL_RE.search(value):
        _append(errors, "unsafe_uri", "URLs and filesystem URIs are not permitted in asset metadata.", location)
    if _SECRET_RE.search(value) or _SENSITIVE_ASSIGNMENT_RE.search(value) or _JWT_RE.search(value):
        _append(errors, "secret_detected", "Secrets and credential-like values are not permitted.", location)
    if _COMMAND_RE.search(value):
        _append(errors, "command_detected", "Commands and shell syntax are not permitted.", location)
    if _EMBEDDED_BINARY_RE.search(value) or _LONG_BLOB_RE.fullmatch(value):
        _append(errors, "embedded_binary", "Media payloads and model-weight references are not permitted.", location)
    return True


def _scan_forbidden_content(value: object, errors: list[dict[str, str]], location: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z]", "", key.casefold()) if isinstance(key, str) else ""
            if normalized in _FORBIDDEN_KEY_TOKENS:
                _append(errors, "forbidden_field", "Paths, secrets, commands, payloads, and runtime configuration are not permitted.", location)
            _scan_forbidden_content(child, errors, f"{location}.{key}" if isinstance(key, str) else location)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_forbidden_content(child, errors, f"{location}[{index}]")
    elif isinstance(value, str):
        _safe_text(value, errors, location)


def _opaque_id(value: object, errors: list[dict[str, str]], location: str, *, identifier: bool = False) -> bool:
    if not _safe_text(value, errors, location, minimum=1, maximum=120):
        return False
    pattern = IDENTIFIER_RE if identifier else OPAQUE_ID_RE
    if isinstance(value, str) and not pattern.fullmatch(value):
        _append(errors, "identifier", "Identifier does not match the bounded opaque-ID contract.", location)
        return False
    return isinstance(value, str)


def _bounded_integer(value: object, errors: list[dict[str, str]], location: str, *, maximum: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        _append(errors, "integer_bound", "Value must be a bounded non-negative integer.", location)
        return None
    return value


def _validate_asset_block(value: object, errors: list[dict[str, str]], location: str) -> str | None:
    fields = ("kind", "media_type", "sha256", "bytes")
    if not _strict_keys(value, set(fields), errors, location):
        return None
    assert isinstance(value, dict)
    _require(value, fields, errors, location)
    if value.get("kind") not in ASSET_KINDS:
        _append(errors, "asset_kind", "Asset kind is not part of the static allowlist.", f"{location}.kind")
    if value.get("media_type") not in MEDIA_TYPES:
        _append(errors, "media_type", "Media type is not part of the static allowlist.", f"{location}.media_type")
    digest = value.get("sha256")
    _safe_text(digest, errors, f"{location}.sha256", minimum=64, maximum=64)
    if isinstance(digest, str) and not SHA256_RE.fullmatch(digest):
        _append(errors, "sha256", "SHA-256 metadata must be lowercase hexadecimal.", f"{location}.sha256")
    _bounded_integer(value.get("bytes"), errors, f"{location}.bytes", maximum=1099511627776)
    return digest if isinstance(digest, str) and SHA256_RE.fullmatch(digest) else None


def _validate_fingerprints(value: object, errors: list[dict[str, str]], location: str) -> None:
    fields = ("perceptual", "embedding")
    if not _strict_keys(value, set(fields), errors, location):
        return
    assert isinstance(value, dict)
    _require(value, fields, errors, location)
    perceptual = value.get("perceptual")
    if not isinstance(perceptual, list):
        _append(errors, "array_required", "Perceptual fingerprints must be an array.", f"{location}.perceptual")
        perceptual = []
    if len(perceptual) > MAX_FINGERPRINTS_PER_KIND:
        _append(errors, "fingerprint_limit", "Perceptual fingerprint count exceeds the safety bound.", f"{location}.perceptual")
    seen_perceptual: set[tuple[str, str]] = set()
    for index, fingerprint in enumerate(perceptual):
        item_location = f"{location}.perceptual[{index}]"
        fields = ("algorithm", "digest", "status")
        if not _strict_keys(fingerprint, set(fields), errors, item_location):
            continue
        assert isinstance(fingerprint, dict)
        _require(fingerprint, fields, errors, item_location)
        algorithm = fingerprint.get("algorithm")
        if algorithm not in PERCEPTUAL_ALGORITHMS:
            _append(errors, "perceptual_algorithm", "Perceptual algorithm is not part of the typed allowlist.", f"{item_location}.algorithm")
        digest = fingerprint.get("digest")
        _safe_text(digest, errors, f"{item_location}.digest", minimum=16, maximum=128)
        if isinstance(digest, str) and not SHORT_DIGEST_RE.fullmatch(digest):
            _append(errors, "fingerprint_digest", "Fingerprint digest must be lowercase hexadecimal metadata.", f"{item_location}.digest")
        if fingerprint.get("status") != "not_run":
            _append(errors, "fingerprint_status", "Optional fingerprint metadata must remain not_run.", f"{item_location}.status")
        if isinstance(algorithm, str) and isinstance(digest, str):
            key = (algorithm, digest)
            if key in seen_perceptual:
                _append(errors, "duplicate_fingerprint", "Perceptual fingerprint metadata must be unique.", item_location)
            seen_perceptual.add(key)
    embedding = value.get("embedding")
    if not isinstance(embedding, list):
        _append(errors, "array_required", "Embedding fingerprints must be an array.", f"{location}.embedding")
        embedding = []
    if len(embedding) > MAX_FINGERPRINTS_PER_KIND:
        _append(errors, "fingerprint_limit", "Embedding fingerprint count exceeds the safety bound.", f"{location}.embedding")
    seen_embedding: set[tuple[str, str, str]] = set()
    for index, fingerprint in enumerate(embedding):
        item_location = f"{location}.embedding[{index}]"
        fields = ("provider_id", "model_id", "dimension", "digest", "status")
        if not _strict_keys(fingerprint, set(fields), errors, item_location):
            continue
        assert isinstance(fingerprint, dict)
        _require(fingerprint, fields, errors, item_location)
        provider_id = fingerprint.get("provider_id")
        model_id = fingerprint.get("model_id")
        _opaque_id(provider_id, errors, f"{item_location}.provider_id")
        _opaque_id(model_id, errors, f"{item_location}.model_id")
        _bounded_integer(fingerprint.get("dimension"), errors, f"{item_location}.dimension", maximum=8192)
        digest = fingerprint.get("digest")
        _safe_text(digest, errors, f"{item_location}.digest", minimum=64, maximum=64)
        if isinstance(digest, str) and not SHA256_RE.fullmatch(digest):
            _append(errors, "sha256", "Embedding metadata requires a SHA-256 digest, not a vector payload.", f"{item_location}.digest")
        if fingerprint.get("status") != "not_run":
            _append(errors, "fingerprint_status", "Optional fingerprint metadata must remain not_run.", f"{item_location}.status")
        if all(isinstance(item, str) for item in (provider_id, model_id, digest)):
            key = (provider_id, model_id, digest)
            if key in seen_embedding:
                _append(errors, "duplicate_fingerprint", "Embedding fingerprint metadata must be unique.", item_location)
            seen_embedding.add(key)


def _validate_asset_record(value: object, errors: list[dict[str, str]], location: str) -> str | None:
    fields = ("schema_version", "id", "label", "asset", "source", "retention", "fingerprints")
    if not _strict_keys(value, set(fields), errors, location):
        return None
    assert isinstance(value, dict)
    _require(value, fields, errors, location)
    if value.get("schema_version") != ASSET_RECORD_SCHEMA_VERSION:
        _append(errors, "asset_record_schema", "Asset record must declare asset-record.v1.", f"{location}.schema_version")
    asset_id = value.get("id")
    _opaque_id(asset_id, errors, f"{location}.id")
    _safe_text(value.get("label"), errors, f"{location}.label", minimum=1, maximum=160)
    _validate_asset_block(value.get("asset"), errors, f"{location}.asset")
    source_fields = ("kind", "reference")
    source = value.get("source")
    if _strict_keys(source, set(source_fields), errors, f"{location}.source"):
        assert isinstance(source, dict)
        _require(source, source_fields, errors, f"{location}.source")
        if source.get("kind") not in SOURCE_KINDS:
            _append(errors, "source_kind", "Source kind is not part of the static allowlist.", f"{location}.source.kind")
        _opaque_id(source.get("reference"), errors, f"{location}.source.reference")
    retention_fields = ("classification", "days")
    retention = value.get("retention")
    if _strict_keys(retention, set(retention_fields), errors, f"{location}.retention"):
        assert isinstance(retention, dict)
        _require(retention, retention_fields, errors, f"{location}.retention")
        if retention.get("classification") not in RETENTION_CLASSES:
            _append(errors, "retention_class", "Retention classification is not part of the allowlist.", f"{location}.retention.classification")
        _bounded_integer(retention.get("days"), errors, f"{location}.retention.days", maximum=MAX_RETENTION_DAYS)
    _validate_fingerprints(value.get("fingerprints"), errors, f"{location}.fingerprints")
    return asset_id if isinstance(asset_id, str) and OPAQUE_ID_RE.fullmatch(asset_id) else None


def _validation_failure(code: str, message: str, location: str, key: str) -> dict[str, Any]:
    return {"valid": False, "errors": [_issue(code, message, location)], key: None}


def validate_asset_record(value: object) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    encoded = _json_bytes(value)
    if encoded is None:
        return _validation_failure("json_value", "Asset record must contain JSON-compatible values only.", "asset", "asset")
    if len(encoded) > MAX_DESCRIPTOR_BYTES:
        return _validation_failure("descriptor_size", "Asset record exceeds the static size bound.", "asset", "asset")
    if not _within_depth(value, errors, "asset"):
        return {"valid": False, "errors": errors, "asset": None}
    _scan_forbidden_content(value, errors, "asset")
    _validate_asset_record(value, errors, "asset")
    if errors:
        return {"valid": False, "errors": errors, "asset": None}
    normalized = copy.deepcopy(value)
    assert isinstance(normalized, dict)
    canonical = canonical_asset_record_json(normalized)
    return {"valid": True, "errors": [], "asset": normalized, "fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest(), "execution": "not_run"}


def validate_asset_catalog(value: object) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    encoded = _json_bytes(value)
    if encoded is None or len(encoded or b"") > MAX_DESCRIPTOR_BYTES:
        return _validation_failure("descriptor_size", "Asset catalog must be bounded JSON.", "catalog", "catalog")
    if not _within_depth(value, errors, "catalog"):
        return {"valid": False, "errors": errors, "catalog": None}
    fields = ("schema_version", "id", "label", "assets")
    if not _strict_keys(value, set(fields), errors, "catalog"):
        return {"valid": False, "errors": errors, "catalog": None}
    assert isinstance(value, dict)
    _require(value, fields, errors, "catalog")
    _scan_forbidden_content(value, errors, "catalog")
    if value.get("schema_version") != ASSET_CATALOG_SCHEMA_VERSION:
        _append(errors, "asset_catalog_schema", "Asset catalog must declare asset-catalog.v1.", "catalog.schema_version")
    _opaque_id(value.get("id"), errors, "catalog.id")
    _safe_text(value.get("label"), errors, "catalog.label", minimum=1, maximum=160)
    assets = value.get("assets")
    if not isinstance(assets, list) or not assets:
        _append(errors, "assets_required", "Catalog must include at least one asset record.", "catalog.assets")
        assets = []
    if len(assets) > MAX_ASSETS_PER_CATALOG:
        _append(errors, "asset_limit", "Catalog asset count exceeds the static safety bound.", "catalog.assets")
    asset_ids: set[str] = set()
    for index, asset in enumerate(assets):
        asset_id = _validate_asset_record(asset, errors, f"catalog.assets[{index}]")
        if asset_id is not None:
            if asset_id in asset_ids:
                _append(errors, "duplicate_asset_id", "Asset IDs must be unique within a catalog.", f"catalog.assets[{index}].id")
            asset_ids.add(asset_id)
    if errors:
        return {"valid": False, "errors": errors, "catalog": None}
    normalized = copy.deepcopy(value)
    assert isinstance(normalized, dict)
    canonical = canonical_asset_catalog_json(normalized)
    return {"valid": True, "errors": [], "catalog": normalized, "fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest(), "execution": "not_run"}


def validate_provenance_lineage(value: object) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    encoded = _json_bytes(value)
    if encoded is None or len(encoded or b"") > MAX_DESCRIPTOR_BYTES:
        return _validation_failure("descriptor_size", "Lineage descriptor must be bounded JSON.", "lineage", "lineage")
    if not _within_depth(value, errors, "lineage"):
        return {"valid": False, "errors": errors, "lineage": None}
    fields = ("schema_version", "id", "label", "retention_bound_days", "nodes", "edges")
    if not _strict_keys(value, set(fields), errors, "lineage"):
        return {"valid": False, "errors": errors, "lineage": None}
    assert isinstance(value, dict)
    _require(value, fields, errors, "lineage")
    _scan_forbidden_content(value, errors, "lineage")
    if value.get("schema_version") != PROVENANCE_LINEAGE_SCHEMA_VERSION:
        _append(errors, "lineage_schema", "Lineage must declare provenance-lineage.v1.", "lineage.schema_version")
    _opaque_id(value.get("id"), errors, "lineage.id")
    _safe_text(value.get("label"), errors, "lineage.label", minimum=1, maximum=160)
    bound = _bounded_integer(value.get("retention_bound_days"), errors, "lineage.retention_bound_days", maximum=MAX_RETENTION_DAYS)
    nodes = value.get("nodes")
    if not isinstance(nodes, list) or len(nodes) < 2:
        _append(errors, "lineage_nodes", "Lineage requires at least two nodes.", "lineage.nodes")
        nodes = []
    if len(nodes) > MAX_LINEAGE_NODES:
        _append(errors, "lineage_node_limit", "Lineage node count exceeds the static safety bound.", "lineage.nodes")
    node_map: dict[str, dict[str, Any]] = {}
    for index, node in enumerate(nodes):
        location = f"lineage.nodes[{index}]"
        node_fields = ("id", "kind", "reference", "retention_days")
        if not _strict_keys(node, set(node_fields), errors, location):
            continue
        assert isinstance(node, dict)
        _require(node, node_fields, errors, location)
        node_id = node.get("id")
        _opaque_id(node_id, errors, f"{location}.id", identifier=True)
        kind = node.get("kind")
        if kind not in LINEAGE_NODE_KINDS:
            _append(errors, "lineage_node_kind", "Lineage node kind is not part of the allowlist.", f"{location}.kind")
        _opaque_id(node.get("reference"), errors, f"{location}.reference")
        retention = _bounded_integer(node.get("retention_days"), errors, f"{location}.retention_days", maximum=MAX_RETENTION_DAYS)
        if bound is not None and retention is not None and retention > bound:
            _append(errors, "retention_bound", "Node retention cannot exceed the lineage retention bound.", f"{location}.retention_days")
        if isinstance(node_id, str):
            if node_id in node_map:
                _append(errors, "duplicate_lineage_node", "Lineage node IDs must be unique.", f"{location}.id")
            else:
                node_map[node_id] = {"kind": kind}
    edges = value.get("edges")
    if not isinstance(edges, list) or not edges:
        _append(errors, "lineage_edges", "Lineage requires at least one edge.", "lineage.edges")
        edges = []
    if len(edges) > MAX_LINEAGE_EDGES:
        _append(errors, "lineage_edge_limit", "Lineage edge count exceeds the static safety bound.", "lineage.edges")
    edge_ids: set[str] = set()
    adjacency: dict[str, set[str]] = defaultdict(set)
    indegree: dict[str, int] = {node_id: 0 for node_id in node_map}
    for index, edge in enumerate(edges):
        location = f"lineage.edges[{index}]"
        edge_fields = ("id", "from", "to", "relation")
        if not _strict_keys(edge, set(edge_fields), errors, location):
            continue
        assert isinstance(edge, dict)
        _require(edge, edge_fields, errors, location)
        edge_id = edge.get("id")
        _opaque_id(edge_id, errors, f"{location}.id", identifier=True)
        if isinstance(edge_id, str):
            if edge_id in edge_ids:
                _append(errors, "duplicate_lineage_edge", "Lineage edge IDs must be unique.", f"{location}.id")
            edge_ids.add(edge_id)
        source = edge.get("from")
        target = edge.get("to")
        _opaque_id(source, errors, f"{location}.from", identifier=True)
        _opaque_id(target, errors, f"{location}.to", identifier=True)
        relation = edge.get("relation")
        if relation not in LINEAGE_RELATIONS:
            _append(errors, "lineage_relation", "Lineage relation is not part of the static allowlist.", f"{location}.relation")
            continue
        if not isinstance(source, str) or not isinstance(target, str):
            continue
        source_node = node_map.get(source)
        target_node = node_map.get(target)
        if source_node is None or target_node is None:
            _append(errors, "lineage_unknown_node", "Lineage edges may reference only declared nodes.", location)
            continue
        expected = LINEAGE_RELATIONS[relation]
        if (source_node.get("kind"), target_node.get("kind")) != expected:
            _append(errors, "lineage_relation_type", "Lineage relation does not match the declared source and target node kinds.", location)
            continue
        if target not in adjacency[source]:
            adjacency[source].add(target)
            indegree[target] += 1
    queue = deque(sorted(node_id for node_id, degree in indegree.items() if degree == 0))
    visited = 0
    remaining = dict(indegree)
    while queue:
        node_id = queue.popleft()
        visited += 1
        for target in sorted(adjacency[node_id]):
            remaining[target] -= 1
            if remaining[target] == 0:
                queue.append(target)
    if node_map and visited != len(node_map):
        _append(errors, "lineage_cycle", "Provenance lineage must be acyclic.", "lineage.edges")
    if errors:
        return {"valid": False, "errors": errors, "lineage": None}
    normalized = copy.deepcopy(value)
    assert isinstance(normalized, dict)
    canonical = canonical_provenance_lineage_json(normalized)
    return {"valid": True, "errors": [], "lineage": normalized, "fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest(), "execution": "not_run"}


def _query_value_valid(field: str, operator: str, value: object, errors: list[dict[str, str]], location: str) -> None:
    allowed_operators = {
        "asset.bytes": {"eq", "gte", "lte"},
        "asset.kind": {"eq", "in"},
        "asset.media_type": {"eq", "in"},
        "retention.classification": {"eq", "in"},
        "fingerprints.has_embedding": {"eq"},
        "fingerprints.has_perceptual": {"eq"},
        "exact_duplicate": {"eq"},
    }
    if operator not in allowed_operators.get(field, set()):
        _append(errors, "query_operator", "Operator is not allowed for this typed query field.", f"{location}.operator")
        return
    if field == "asset.bytes":
        _bounded_integer(value, errors, f"{location}.value", maximum=1099511627776)
        return
    if field in {"fingerprints.has_embedding", "fingerprints.has_perceptual", "exact_duplicate"}:
        if not isinstance(value, bool):
            _append(errors, "query_value", "Boolean query fields require a boolean value.", f"{location}.value")
        return
    allowed_values = ASSET_KINDS if field == "asset.kind" else MEDIA_TYPES if field == "asset.media_type" else RETENTION_CLASSES
    values = value if operator == "in" else [value]
    if operator == "in" and (not isinstance(value, list) or not 1 <= len(value) <= 8):
        _append(errors, "query_value", "The in operator requires a bounded non-empty array.", f"{location}.value")
        return
    seen: set[str] = set()
    for index, item in enumerate(values):
        item_location = f"{location}.value[{index}]" if operator == "in" else f"{location}.value"
        if item not in allowed_values:
            _append(errors, "query_value", "Query value is not part of the typed field allowlist.", item_location)
        if isinstance(item, str):
            if item in seen:
                _append(errors, "duplicate_query_value", "Query in-values must be unique.", item_location)
            seen.add(item)


def _validate_query(
    value: object,
    errors: list[dict[str, str]],
    location: str,
    *,
    depth: int,
    state: dict[str, int],
) -> None:
    state["nodes"] = state.get("nodes", 0) + 1
    if state["nodes"] > MAX_QUERY_NODES:
        if not state.get("node_limit_reported", 0):
            _append(errors, "query_node_limit", "Smart collection query exceeds the total static safety bound.", location)
            state["node_limit_reported"] = 1
        return
    if depth > MAX_QUERY_DEPTH:
        _append(errors, "query_depth", "Smart collection query nesting exceeds the static safety bound.", location)
        return
    if not isinstance(value, dict):
        _append(errors, "object_required", "Query expression must be an object.", location)
        return
    keys = set(value)
    if keys == {"field", "operator", "value"}:
        field = value.get("field")
        operator = value.get("operator")
        if field not in SMART_COLLECTION_FIELDS:
            _append(errors, "query_field", "Query field is not part of the typed allowlist.", f"{location}.field")
            return
        if operator not in SMART_COLLECTION_OPERATORS:
            _append(errors, "query_operator", "Query operator is not part of the typed allowlist.", f"{location}.operator")
            return
        _query_value_valid(field, operator, value.get("value"), errors, location)
        return
    if keys not in ({"all"}, {"any"}):
        _append(errors, "query_shape", "Query expression must be one closed condition, all group, or any group.", location)
        return
    group_name = "all" if "all" in value else "any"
    terms = value.get(group_name)
    if not isinstance(terms, list) or not terms:
        _append(errors, "query_terms", "Query groups require a non-empty array of terms.", f"{location}.{group_name}")
        return
    if len(terms) > MAX_QUERY_TERMS:
        _append(errors, "query_term_limit", "Query group exceeds the static safety bound.", f"{location}.{group_name}")
    for index, term in enumerate(terms):
        _validate_query(term, errors, f"{location}.{group_name}[{index}]", depth=depth + 1, state=state)
        if state.get("node_limit_reported", 0):
            break


def validate_smart_collection(value: object) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    encoded = _json_bytes(value)
    if encoded is None or len(encoded or b"") > MAX_DESCRIPTOR_BYTES:
        return _validation_failure("descriptor_size", "Smart collection must be bounded JSON.", "collection", "collection")
    if not _within_depth(value, errors, "collection"):
        return {"valid": False, "errors": errors, "collection": None}
    fields = ("schema_version", "id", "label", "query")
    if not _strict_keys(value, set(fields), errors, "collection"):
        return {"valid": False, "errors": errors, "collection": None}
    assert isinstance(value, dict)
    _require(value, fields, errors, "collection")
    _scan_forbidden_content(value, errors, "collection")
    if value.get("schema_version") != SMART_COLLECTION_SCHEMA_VERSION:
        _append(errors, "collection_schema", "Smart collection must declare smart-collection.v1.", "collection.schema_version")
    _opaque_id(value.get("id"), errors, "collection.id")
    _safe_text(value.get("label"), errors, "collection.label", minimum=1, maximum=160)
    _validate_query(value.get("query"), errors, "collection.query", depth=1, state={"nodes": 0})
    if errors:
        return {"valid": False, "errors": errors, "collection": None}
    normalized = copy.deepcopy(value)
    assert isinstance(normalized, dict)
    canonical = canonical_smart_collection_json(normalized)
    return {"valid": True, "errors": [], "collection": normalized, "fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest(), "execution": "not_run"}


def _sorted_fingerprints(value: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    return [copy.deepcopy(item) for item in sorted(value, key=lambda item: tuple(str(item[key]) for key in keys))]


def canonical_asset_record_json(asset: dict[str, Any]) -> str:
    value = copy.deepcopy(asset)
    value["fingerprints"]["perceptual"] = _sorted_fingerprints(value["fingerprints"]["perceptual"], ("algorithm", "digest"))
    value["fingerprints"]["embedding"] = _sorted_fingerprints(value["fingerprints"]["embedding"], ("provider_id", "model_id", "digest"))
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def canonical_asset_catalog_json(catalog: dict[str, Any]) -> str:
    value = copy.deepcopy(catalog)
    value["assets"] = [json.loads(canonical_asset_record_json(asset)) for asset in sorted(value["assets"], key=lambda item: str(item["id"]))]
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def canonical_provenance_lineage_json(lineage: dict[str, Any]) -> str:
    value = copy.deepcopy(lineage)
    value["nodes"] = [copy.deepcopy(item) for item in sorted(value["nodes"], key=lambda item: str(item["id"]))]
    value["edges"] = [copy.deepcopy(item) for item in sorted(value["edges"], key=lambda item: str(item["id"]))]
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _canonical_query(value: dict[str, Any]) -> dict[str, Any]:
    if "field" in value:
        result = copy.deepcopy(value)
        if result["operator"] == "in":
            result["value"] = sorted(result["value"])
        return result
    group = "all" if "all" in value else "any"
    terms = [_canonical_query(item) for item in value[group]]
    terms.sort(key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return {group: terms}


def canonical_smart_collection_json(collection: dict[str, Any]) -> str:
    value = copy.deepcopy(collection)
    value["query"] = _canonical_query(value["query"])
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
