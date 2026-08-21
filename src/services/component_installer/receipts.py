"""Strict, atomic component-install receipt V3 helpers.

Receipts are metadata only.  They never contain an absolute path, URL,
command, executable, credential or component bytes.  V2 files remain readable
as legacy/unverified records, but only an explicit V3 verification write can
produce a verified state.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any


RECEIPT_SCHEMA = "component-install-receipts.v3"
LEGACY_RECEIPT_SCHEMA = "component-install-receipts.v2"
RECEIPT_FILENAME = "component_install_receipts.json"
MAX_RECEIPT_BYTES = 512 * 1024
MAX_RECEIPT_RECORDS = 256
MAX_RECEIPT_LEAVES = 512
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[a-z][a-z0-9._-]{1,95}$")
_SAFE_TEXT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+@ -]{0,255}$")
_ROOT_CLASSES = frozenset({"models_root", "runtime_root", "environments_root", "external_managed"})
_COMPONENT_TYPES = frozenset({"model", "runtime"})
_STATES = frozenset({"DISCOVERED", "INSTALLED_UNVERIFIED", "INSTALLED_VERIFIED", "PARTIAL", "NOT_INSTALLED", "UNAVAILABLE"})
_SOURCES = frozenset({"catalog_primary", "catalog_candidate", "existing_install_reuse", "manual_import", "legacy", "unknown"})
_LEGACY_INPUT_KEYS = frozenset({
    "component_id", "component_type", "catalog_schema", "catalog_revision", "catalog_fingerprint",
    "bundle_revision", "source", "source_identity", "root_class", "location_class", "leaves", "files",
    "recorded_at", "installed_at", "verified_at", "state", "operational", "installed_size_bytes",
    "previous_version", "rollback_candidate", "size_source", "last_repair", "shared_dependency_id",
})
_V3_REQUIRED_KEYS = frozenset({
    "component_id", "component_type", "catalog_schema", "catalog_revision", "catalog_fingerprint",
    "source_identity", "root_class", "location_class", "leaves", "recorded_at", "verified_at",
    "state", "source", "operational",
})
_V3_OPTIONAL_KEYS = frozenset({"previous_version", "rollback_candidate"})
V1_CATALOG_SCHEMAS = frozenset({"model-catalog.v1", "runtime-catalog.v1"})
V2_CATALOG_SCHEMA = "v7-production-catalog.v2"
_CATALOG_SCHEMAS = V1_CATALOG_SCHEMAS | {V2_CATALOG_SCHEMA}
_CATALOG_BINDING_KEYS = frozenset({"catalog_schema", "catalog_revision", "catalog_fingerprint", "source_identity"})


def _normalize_source_identity(value: object, *, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise ReceiptError("catalog_source_identity_missing")
        return None
    if not isinstance(value, str) or not value or len(value) > 256 or "\x00" in value:
        raise ReceiptError("catalog_source_identity_invalid")
    if _SHA256.fullmatch(value.casefold()):
        return value.casefold()
    lowered = value.casefold()
    if "http://" in lowered or "https://" in lowered or "\\" in value or "/" in value or value.startswith("."):
        raise ReceiptError("catalog_source_identity_invalid")
    if not all(char.isalnum() or char in "._:+@ -" for char in value):
        raise ReceiptError("catalog_source_identity_invalid")
    return value


@dataclass(frozen=True)
class CatalogBindingContext:
    """Explicit server-owned catalog identity carried into verification."""

    catalog_schema: str
    catalog_revision: str
    catalog_fingerprint: str
    source_identity: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.catalog_schema, str) or self.catalog_schema not in _CATALOG_SCHEMAS:
            raise ReceiptError("catalog_schema_invalid")
        if not isinstance(self.catalog_revision, str) or not self.catalog_revision or len(self.catalog_revision) > 256:
            raise ReceiptError("catalog_revision_invalid")
        if "http://" in self.catalog_revision.casefold() or "https://" in self.catalog_revision.casefold() or "\\" in self.catalog_revision or "/" in self.catalog_revision or ":" in self.catalog_revision:
            raise ReceiptError("catalog_revision_invalid")
        if not isinstance(self.catalog_fingerprint, str) or not _SHA256.fullmatch(self.catalog_fingerprint.casefold()):
            raise ReceiptError("catalog_fingerprint_invalid")
        normalized_source = _normalize_source_identity(self.source_identity, required=False)
        object.__setattr__(self, "catalog_fingerprint", self.catalog_fingerprint.casefold())
        object.__setattr__(self, "source_identity", normalized_source)

    @classmethod
    def for_v1(cls, *, component_type: str, record: Mapping[str, Any], catalog_fingerprint: str) -> "CatalogBindingContext":
        schema = "model-catalog.v1" if component_type == "model" else "runtime-catalog.v1" if component_type == "runtime" else ""
        return cls(
            catalog_schema=schema,
            catalog_revision=str(record.get("revision") or record.get("version") or "unknown"),
            catalog_fingerprint=catalog_fingerprint,
            source_identity=source_identity(record),
        )

    @classmethod
    def for_v2(cls, *, catalog_version: str, catalog_fingerprint: str, source_identity: str | None) -> "CatalogBindingContext":
        return cls(
            catalog_schema=V2_CATALOG_SCHEMA,
            catalog_revision=catalog_version,
            catalog_fingerprint=catalog_fingerprint,
            source_identity=source_identity,
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CatalogBindingContext":
        if not isinstance(value, Mapping):
            raise ReceiptError("catalog_binding_invalid")
        if set(value) != _CATALOG_BINDING_KEYS:
            raise ReceiptError("catalog_binding_fields_invalid")
        schema = value.get("catalog_schema")
        revision = value.get("catalog_revision")
        fingerprint = value.get("catalog_fingerprint")
        if schema == V2_CATALOG_SCHEMA and "source_identity" not in value:
            raise ReceiptError("catalog_source_identity_missing")
        return cls(catalog_schema=schema, catalog_revision=revision, catalog_fingerprint=fingerprint, source_identity=value.get("source_identity"))

    def validate_record(self, *, component_type: str, record: Mapping[str, Any]) -> None:
        expected_v1 = "model-catalog.v1" if component_type == "model" else "runtime-catalog.v1" if component_type == "runtime" else None
        if self.catalog_schema in V1_CATALOG_SCHEMAS and self.catalog_schema != expected_v1:
            raise ReceiptError("catalog_schema_component_mismatch")
        if self.catalog_schema in V1_CATALOG_SCHEMAS:
            expected_revision = str(record.get("revision") or record.get("version") or "unknown")
            if self.catalog_revision != expected_revision:
                raise ReceiptError("catalog_revision_component_mismatch")
            if self.source_identity != source_identity(record):
                raise ReceiptError("catalog_source_identity_mismatch")
        if self.catalog_schema == V2_CATALOG_SCHEMA:
            if "source_identity" not in record:
                raise ReceiptError("catalog_source_identity_missing")
            if _normalize_source_identity(record.get("source_identity"), required=False) != self.source_identity:
                raise ReceiptError("catalog_source_identity_mismatch")

    def as_record_fields(self) -> dict[str, Any]:
        return {
            "catalog_schema": self.catalog_schema,
            "catalog_revision": self.catalog_revision,
            "catalog_fingerprint": self.catalog_fingerprint.casefold(),
            "source_identity": self.source_identity,
        }


class ReceiptError(ValueError):
    """Fixed-code receipt validation failure with no client value echo."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ReceiptConflict(ReceiptError):
    pass


def _duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReceiptError("duplicate_receipt_key")
        result[key] = value
    return result


def _read_json(path: Path) -> Any:
    try:
        if path.is_symlink():
            raise ReceiptError("receipt_symlinked")
        if path.stat().st_size > MAX_RECEIPT_BYTES:
            raise ReceiptError("receipt_oversized")
        with path.open("rb") as stream:
            raw = stream.read(MAX_RECEIPT_BYTES + 1)
        if len(raw) > MAX_RECEIPT_BYTES:
            raise ReceiptError("receipt_oversized")
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_duplicate_pairs)
    except ReceiptError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReceiptError("receipt_unreadable") from exc


def _safe_text(value: object, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value or len(value) > 256 or not _SAFE_TEXT.fullmatch(value):
        raise ReceiptError("receipt_unsafe_text")
    lowered = value.casefold()
    if "http://" in lowered or "https://" in lowered or "\\" in value or "/" in value or ":" in value or ".." in value:
        raise ReceiptError("receipt_unsafe_text")
    return value


def _safe_relative(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ReceiptError("receipt_invalid_leaf")
    normalized = value.replace("\\", "/")
    if normalized.startswith(("/", "//")) or ":" in normalized:
        raise ReceiptError("receipt_unsafe_leaf")
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ReceiptError("receipt_unsafe_leaf")
    return "/".join(parts)


def _sha256(value: object, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not _SHA256.fullmatch(value.casefold()):
        raise ReceiptError("receipt_invalid_sha256")
    return value.casefold()


def _nonnegative_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReceiptError(code)
    return value


def source_identity(record: Mapping[str, Any]) -> str:
    """Derive a stable opaque source identity from server-owned catalog data."""

    source = record.get("primary_source") or record.get("official_source") or "local"
    value = {
        "source": source if isinstance(source, (str, Mapping)) else "unknown",
        "identity": record.get("source_identity") or record.get("revision") or "unknown",
    }
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _catalog_fingerprint(value: object) -> str:
    if isinstance(value, str) and _SHA256.fullmatch(value.casefold()):
        return value.casefold()
    if value is None:
        raise ReceiptError("receipt_catalog_fingerprint_missing")
    try:
        encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ReceiptError("receipt_catalog_fingerprint_invalid") from exc
    return hashlib.sha256(encoded).hexdigest()


def _catalog_schema(component_type: object, binding: CatalogBindingContext | None = None) -> str:
    if binding is not None:
        expected = "model-catalog.v1" if component_type == "model" else "runtime-catalog.v1" if component_type == "runtime" else None
        if binding.catalog_schema == V2_CATALOG_SCHEMA or binding.catalog_schema == expected:
            return binding.catalog_schema
        raise ReceiptError("catalog_schema_component_mismatch")
    if component_type == "model":
        return "model-catalog.v1"
    if component_type == "runtime":
        return "runtime-catalog.v1"
    raise ReceiptError("receipt_invalid_component_type")


def _normalize_leaf(item: Mapping[str, Any], *, default_verified_at: int | None = None) -> dict[str, Any]:
    relative = item.get("relative_path", item.get("relative_leaf"))
    verification_level = item.get("verification_level", "unverified")
    if not isinstance(verification_level, str):
        raise ReceiptError("receipt_invalid_verification_level")
    leaf: dict[str, Any] = {
        "relative_path": _safe_relative(relative),
        "observed_size_bytes": _nonnegative_int(item.get("observed_size_bytes", item.get("size_bytes", 0)), "receipt_invalid_size"),
        "observed_mtime_ns": _nonnegative_int(item.get("observed_mtime_ns", 0), "receipt_invalid_mtime"),
        "verification_level": verification_level,
    }
    if leaf["verification_level"] not in {"verified", "measured_only", "unverified"}:
        raise ReceiptError("receipt_invalid_verification_level")
    if leaf["verification_level"] == "verified":
        leaf["verified_size_bytes"] = _nonnegative_int(item.get("verified_size_bytes", item.get("size_bytes")), "receipt_invalid_verified_size")
        leaf["verified_sha256"] = _sha256(item.get("verified_sha256", item.get("sha256")))
        if item.get("hash_algorithm", "sha256") != "sha256":
            raise ReceiptError("receipt_invalid_hash_algorithm")
        verified_at = item.get("verified_at", default_verified_at)
        leaf["hash_algorithm"] = "sha256"
        leaf["verified_at"] = _nonnegative_int(verified_at, "receipt_invalid_verified_at")
    return leaf


def _validate_v3_record(component_id: str, value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ReceiptError("receipt_record_not_object")
    allowed = _V3_REQUIRED_KEYS | _V3_OPTIONAL_KEYS
    if set(value) - allowed or not _V3_REQUIRED_KEYS.issubset(set(value)):
        raise ReceiptError("receipt_record_fields_invalid")
    if value.get("component_id") != component_id or not isinstance(component_id, str) or not _ID.fullmatch(component_id):
        raise ReceiptError("receipt_component_id_mismatch")
    component_type = value.get("component_type")
    if not isinstance(component_type, str) or component_type not in _COMPONENT_TYPES:
        raise ReceiptError("receipt_invalid_component_type")
    schema = value.get("catalog_schema")
    expected_schema = _catalog_schema(component_type)
    if not isinstance(schema, str) or (schema != expected_schema and schema != V2_CATALOG_SCHEMA):
        raise ReceiptError("receipt_catalog_schema_mismatch")
    revision = _safe_text(value.get("catalog_revision"))
    fingerprint = _sha256(value.get("catalog_fingerprint"))
    source = _normalize_source_identity(value.get("source_identity"), required=False)
    root_class = value.get("root_class")
    location_class = value.get("location_class")
    if not isinstance(root_class, str) or not isinstance(location_class, str) or root_class not in _ROOT_CLASSES or location_class not in _ROOT_CLASSES:
        raise ReceiptError("receipt_invalid_root_class")
    if component_type == "model" and root_class != "models_root":
        raise ReceiptError("receipt_model_root_mismatch")
    if component_type == "runtime" and root_class == "models_root":
        raise ReceiptError("receipt_runtime_root_mismatch")
    leaves = value.get("leaves")
    if not isinstance(leaves, list) or not leaves or len(leaves) > MAX_RECEIPT_LEAVES:
        raise ReceiptError("receipt_invalid_leaves")
    normalized_leaves: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in leaves:
        if not isinstance(item, Mapping):
            raise ReceiptError("receipt_leaf_not_object")
        verification_level = item.get("verification_level")
        if not isinstance(verification_level, str):
            raise ReceiptError("receipt_invalid_verification_level")
        base_leaf_keys = {"relative_path", "observed_size_bytes", "observed_mtime_ns", "verification_level"}
        verified_leaf_keys = base_leaf_keys | {"verified_size_bytes", "verified_sha256", "hash_algorithm", "verified_at"}
        if set(item) - (verified_leaf_keys if verification_level == "verified" else base_leaf_keys):
            raise ReceiptError("receipt_leaf_fields_invalid")
        leaf = _normalize_leaf(item, default_verified_at=value.get("verified_at"))
        if leaf["relative_path"] in seen:
            raise ReceiptError("receipt_duplicate_leaf")
        seen.add(leaf["relative_path"])
        normalized_leaves.append(leaf)
    recorded_at = _nonnegative_int(value.get("recorded_at"), "receipt_invalid_recorded_at")
    verified_at = value.get("verified_at")
    if verified_at is not None:
        verified_at = _nonnegative_int(verified_at, "receipt_invalid_verified_at")
    state = value.get("state")
    if not isinstance(state, str) or state not in _STATES:
        raise ReceiptError("receipt_invalid_state")
    source_kind = value.get("source")
    if not isinstance(source_kind, str) or source_kind not in _SOURCES:
        raise ReceiptError("receipt_invalid_source")
    if value.get("operational") is not False:
        raise ReceiptError("receipt_operational_not_allowed")
    previous_version = value.get("previous_version")
    if previous_version is not None:
        previous_version = _safe_text(previous_version)
    rollback_candidate = value.get("rollback_candidate", False)
    if not isinstance(rollback_candidate, bool):
        raise ReceiptError("receipt_invalid_rollback_flag")
    if state == "INSTALLED_VERIFIED" and (verified_at is None or any(item["verification_level"] != "verified" for item in normalized_leaves)):
        raise ReceiptError("receipt_verified_binding_missing")
    return {
        "component_id": component_id,
        "component_type": component_type,
        "catalog_schema": schema,
        "catalog_revision": revision,
        "catalog_fingerprint": fingerprint,
        "source_identity": source,
        "root_class": root_class,
        "location_class": location_class,
        "leaves": normalized_leaves,
        "recorded_at": recorded_at,
        "verified_at": verified_at,
        "state": state,
        "source": source_kind,
        "operational": False,
        "previous_version": previous_version,
        "rollback_candidate": rollback_candidate,
    }


def _validate_document(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"schema_version", "records"}:
        raise ReceiptError("receipt_document_invalid")
    if value.get("schema_version") != RECEIPT_SCHEMA or not isinstance(value.get("records"), Mapping):
        raise ReceiptError("receipt_schema_unsupported")
    records = value["records"]
    if len(records) > MAX_RECEIPT_RECORDS:
        raise ReceiptError("receipt_too_many_records")
    normalized: dict[str, Any] = {}
    for component_id, record in records.items():
        if not isinstance(component_id, str) or not _ID.fullmatch(component_id):
            raise ReceiptError("receipt_invalid_record_id")
        normalized[component_id] = _validate_v3_record(component_id, record)
    return {"schema_version": RECEIPT_SCHEMA, "records": normalized}


def _legacy_value(value: object, *, depth: int = 0) -> object | None:
    if depth > 4:
        return None
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 2**63 - 1:
        return value
    if isinstance(value, str):
        if len(value) > 256 or "\x00" in value or "http://" in value.casefold() or "https://" in value.casefold() or "\\" in value or value.startswith("/") or ":" in value:
            return None
        return value
    if isinstance(value, list):
        return [_legacy_value(item, depth=depth + 1) for item in value[:MAX_RECEIPT_LEAVES]]
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in list(value.items())[:64]:
            if not isinstance(key, str) or key.casefold() in {"path", "url", "command", "executable", "secret", "token", "password", "authorization"}:
                continue
            safe = _legacy_value(item, depth=depth + 1)
            if safe is not None:
                result[key] = safe
        return result
    return None


def _legacy_record_projection(value: object) -> object | None:
    safe = _legacy_value(value)
    if not isinstance(safe, Mapping):
        return safe
    result = dict(safe)
    if isinstance(result.get("state"), str) and result.get("state") in {"INSTALLED_VERIFIED", "OPERATIONAL"}:
        result["state"] = "INSTALLED_UNVERIFIED"
    result["operational"] = False
    return result


def _read_document(config_root: Path) -> dict[str, Any]:
    path = Path(config_root) / RECEIPT_FILENAME
    try:
        raw = _read_json(path)
    except ReceiptError:
        return {"schema_version": RECEIPT_SCHEMA, "records": {}}
    if isinstance(raw, Mapping) and raw.get("schema_version") == RECEIPT_SCHEMA:
        try:
            return _validate_document(raw)
        except ReceiptError:
            return {"schema_version": RECEIPT_SCHEMA, "records": {}}
    if isinstance(raw, Mapping) and raw.get("schema_version") == LEGACY_RECEIPT_SCHEMA and isinstance(raw.get("records"), Mapping):
        return {"schema_version": LEGACY_RECEIPT_SCHEMA, "records": {str(key): _legacy_record_projection(value) for key, value in list(raw["records"].items())[:MAX_RECEIPT_RECORDS] if isinstance(key, str) and _ID.fullmatch(key)}}
    return {"schema_version": RECEIPT_SCHEMA, "records": {}}


def read_receipts(config_root: Path) -> dict[str, Any]:
    """Read V3 or bounded legacy V2 metadata without any filesystem writes."""

    return _read_document(Path(config_root))


def _legacy_to_v3_record(component_id: str, receipt: Mapping[str, Any]) -> dict[str, Any]:
    component_type_value = receipt.get("component_type")
    component_type = component_type_value if isinstance(component_type_value, str) and component_type_value in _COMPONENT_TYPES else "model"
    root_class = receipt.get("root_class") or receipt.get("location_class")
    if not isinstance(root_class, str) or root_class not in _ROOT_CLASSES:
        root_class = "models_root" if component_type == "model" else "runtime_root"
    catalog_revision = receipt.get("catalog_revision", receipt.get("bundle_revision", "unknown"))
    source_identity_value = receipt.get("source_identity")
    if source_identity_value is None:
        source_digest = None
    elif isinstance(source_identity_value, str) and _SHA256.fullmatch(source_identity_value.casefold()):
        source_digest = source_identity_value.casefold()
    else:
        source_digest = hashlib.sha256(str(source_identity_value).encode("utf-8")).hexdigest()
    raw_leaves = receipt.get("leaves", receipt.get("files", []))
    leaves: list[dict[str, Any]] = []
    for item in raw_leaves if isinstance(raw_leaves, list) else []:
        if isinstance(item, Mapping):
            leaf = {
                "relative_path": item.get("relative_path", item.get("relative_leaf")),
                "observed_size_bytes": item.get("observed_size_bytes", item.get("size_bytes", 0)),
                "observed_mtime_ns": item.get("observed_mtime_ns", 0),
                "verification_level": item.get("verification_level", "unverified"),
            }
            if item.get("verification_level") == "verified":
                leaf.update({"verified_size_bytes": item.get("verified_size_bytes", item.get("size_bytes")), "verified_sha256": item.get("verified_sha256", item.get("sha256")), "hash_algorithm": "sha256", "verified_at": item.get("verified_at", receipt.get("verified_at"))})
            leaves.append(leaf)
    if not leaves:
        leaves = [{"relative_path": "unknown", "observed_size_bytes": 0, "observed_mtime_ns": 0, "verification_level": "unverified"}]
    try:
        catalog_fingerprint = _catalog_fingerprint(receipt.get("catalog_fingerprint"))
    except ReceiptError:
        catalog_fingerprint = hashlib.sha256(json.dumps({"component_id": component_id, "legacy": True}, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    state_value = receipt.get("state")
    state = state_value if isinstance(state_value, str) and state_value in _STATES else "INSTALLED_UNVERIFIED"
    if state == "INSTALLED_VERIFIED":
        state = "INSTALLED_UNVERIFIED"
    source_value = receipt.get("source")
    source = source_value if isinstance(source_value, str) and source_value in _SOURCES else "legacy"
    value = {
        "component_id": component_id,
        "component_type": component_type,
        "catalog_schema": _catalog_schema(component_type),
        "catalog_revision": catalog_revision,
        "catalog_fingerprint": catalog_fingerprint,
        "source_identity": source_digest,
        "root_class": root_class,
        "location_class": root_class,
        "leaves": leaves,
        "recorded_at": receipt.get("recorded_at", receipt.get("installed_at", 0)),
        "verified_at": None,
        "state": state,
        "source": source,
        "operational": False,
        "previous_version": receipt.get("previous_version"),
        "rollback_candidate": False if receipt.get("rollback_candidate") is None else receipt.get("rollback_candidate", False),
    }
    return _validate_v3_record(component_id, value)


def _coerce_binding(value: CatalogBindingContext | Mapping[str, Any] | None) -> CatalogBindingContext | None:
    if value is None:
        return None
    if isinstance(value, CatalogBindingContext):
        return value
    if isinstance(value, Mapping):
        return CatalogBindingContext.from_mapping(value)
    raise ReceiptError("catalog_binding_invalid")


def _normalize_new_record(component_id: str, receipt: Mapping[str, Any], *, catalog_binding: CatalogBindingContext | Mapping[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(component_id, str) or not _ID.fullmatch(component_id):
        raise ReceiptError("receipt_invalid_component_id")
    if not isinstance(receipt, Mapping):
        raise ReceiptError("receipt_record_not_object")
    binding = _coerce_binding(catalog_binding)
    if binding is not None:
        component_type_value = receipt.get("component_type")
        if not isinstance(component_type_value, str):
            raise ReceiptError("receipt_invalid_component_type")
        expected_schema = "model-catalog.v1" if component_type_value == "model" else "runtime-catalog.v1" if component_type_value == "runtime" else None
        if binding.catalog_schema == V2_CATALOG_SCHEMA:
            binding.validate_record(component_type=component_type_value, record=receipt)
        elif binding.catalog_schema != expected_schema:
            raise ReceiptError("catalog_schema_component_mismatch")
        receipt = {**receipt, **binding.as_record_fields()}
    elif receipt.get("catalog_schema") == V2_CATALOG_SCHEMA:
        raise ReceiptError("catalog_binding_required")
    if _V3_REQUIRED_KEYS.issubset(set(receipt)) and not (set(receipt) - (_V3_REQUIRED_KEYS | _V3_OPTIONAL_KEYS)):
        return _validate_v3_record(component_id, receipt)
    if set(receipt) - _LEGACY_INPUT_KEYS:
        raise ReceiptError("receipt_record_fields_invalid")
    component_type = receipt.get("component_type")
    if not isinstance(component_type, str) or component_type not in _COMPONENT_TYPES:
        raise ReceiptError("receipt_invalid_component_type")
    root_class = receipt.get("root_class", receipt.get("location_class"))
    if not isinstance(root_class, str) or root_class not in _ROOT_CLASSES:
        root_class = "models_root" if component_type == "model" else "runtime_root"
    raw_leaves = receipt.get("leaves", receipt.get("files", []))
    leaves: list[dict[str, Any]] = []
    for item in raw_leaves if isinstance(raw_leaves, list) else []:
        if isinstance(item, Mapping):
            verification_level = item.get("verification_level")
            if verification_level is None:
                verification_level = "verified" if item.get("size_verified") is True and isinstance(item.get("sha256"), str) and receipt.get("verified_at") is not None else "measured_only" if isinstance(item.get("sha256"), str) else "unverified"
            leaves.append(_normalize_leaf({
                "relative_path": item.get("relative_path", item.get("relative_leaf")),
                "observed_size_bytes": item.get("observed_size_bytes", item.get("size_bytes", 0)),
                "observed_mtime_ns": item.get("observed_mtime_ns", 0),
                "verification_level": verification_level,
                "verified_size_bytes": item.get("verified_size_bytes", item.get("size_bytes")),
                "verified_sha256": item.get("verified_sha256", item.get("sha256")),
                "hash_algorithm": item.get("hash_algorithm", "sha256"),
                "verified_at": item.get("verified_at", receipt.get("verified_at")),
            }, default_verified_at=receipt.get("verified_at")))
    if not leaves:
        raise ReceiptError("receipt_invalid_leaves")
    state_value = receipt.get("state")
    state = state_value if isinstance(state_value, str) and state_value in _STATES else "INSTALLED_UNVERIFIED"
    if state == "INSTALLED_VERIFIED" and any(leaf["verification_level"] != "verified" for leaf in leaves):
        raise ReceiptError("receipt_verified_binding_missing")
    value = {
        "component_id": component_id,
        "component_type": component_type,
        "catalog_schema": receipt.get("catalog_schema", _catalog_schema(component_type)),
        "catalog_revision": receipt.get("catalog_revision", receipt.get("bundle_revision", "unknown")),
        "catalog_fingerprint": receipt.get("catalog_fingerprint"),
        "source_identity": receipt.get("source_identity"),
        "root_class": root_class,
        "location_class": receipt.get("location_class") if isinstance(receipt.get("location_class"), str) and receipt.get("location_class") in _ROOT_CLASSES else root_class,
        "leaves": leaves,
        "recorded_at": receipt.get("recorded_at", receipt.get("installed_at", 0)),
        "verified_at": receipt.get("verified_at"),
        "state": state,
        "source": receipt.get("source") if isinstance(receipt.get("source"), str) and receipt.get("source") in _SOURCES else "unknown",
        "operational": False,
        "previous_version": receipt.get("previous_version"),
        "rollback_candidate": False if receipt.get("rollback_candidate") is None else receipt.get("rollback_candidate", False),
    }
    if not isinstance(value["catalog_fingerprint"], str) or not _SHA256.fullmatch(value["catalog_fingerprint"].casefold()):
        value["catalog_fingerprint"] = hashlib.sha256(json.dumps({"component_id": component_id, "legacy": True}, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    if value["source_identity"] is not None and not _SHA256.fullmatch(str(value["source_identity"]).casefold()):
        value["source_identity"] = hashlib.sha256(str(value["source_identity"]).encode("utf-8")).hexdigest()
    return _validate_v3_record(component_id, value)


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        payload = (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        if len(payload) > MAX_RECEIPT_BYTES:
            raise ReceiptError("receipt_oversized")
        handle, name = tempfile.mkstemp(prefix=".component-receipts-", suffix=".tmp", dir=path.parent)
        os.close(handle)
        temporary = Path(name)
        with temporary.open("wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def write_component_receipt(config_root: Path, component_id: str, receipt: Mapping[str, Any], *, catalog_binding: CatalogBindingContext | Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Atomically write one server-owned V3 record, never a caller path."""

    current = _read_document(Path(config_root))
    records = current.get("records") if isinstance(current.get("records"), Mapping) else {}
    if current.get("schema_version") == LEGACY_RECEIPT_SCHEMA:
        records = {key: _legacy_to_v3_record(key, value) for key, value in records.items() if isinstance(key, str) and isinstance(value, Mapping)}
    candidate = _normalize_new_record(component_id, receipt, catalog_binding=catalog_binding)
    existing = records.get(component_id)
    if isinstance(existing, Mapping):
        try:
            existing_v3 = _validate_v3_record(component_id, existing)
        except ReceiptError:
            existing_v3 = None
        if existing_v3 and existing_v3.get("state") == "INSTALLED_VERIFIED" and candidate.get("state") != "INSTALLED_VERIFIED":
            raise ReceiptConflict("receipt_verified_downgrade")
    next_value = _validate_document({"schema_version": RECEIPT_SCHEMA, "records": {**records, component_id: candidate}})
    _atomic_write(Path(config_root) / RECEIPT_FILENAME, next_value)
    return dict(candidate)


def write_verified_receipt(config_root: Path, component_id: str, receipt: Mapping[str, Any], *, expected_catalog_fingerprint: str | None = None, catalog_binding: CatalogBindingContext | Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Receipt-only explicit verification update with a binding check."""

    if expected_catalog_fingerprint is not None and receipt.get("catalog_fingerprint") != expected_catalog_fingerprint:
        raise ReceiptConflict("receipt_catalog_fingerprint_mismatch")
    candidate = dict(receipt)
    candidate["state"] = "INSTALLED_VERIFIED" if all(isinstance(item, Mapping) and item.get("verification_level") == "verified" for item in candidate.get("leaves", [])) else "INSTALLED_UNVERIFIED"
    return write_component_receipt(config_root, component_id, candidate, catalog_binding=catalog_binding)


__all__ = [
    "LEGACY_RECEIPT_SCHEMA", "MAX_RECEIPT_BYTES", "RECEIPT_FILENAME", "RECEIPT_SCHEMA",
    "CatalogBindingContext", "ReceiptConflict", "ReceiptError", "read_receipts", "source_identity",
    "write_component_receipt", "write_verified_receipt",
]
