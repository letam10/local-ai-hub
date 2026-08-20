"""Path-free component runtime evidence with strict catalog binding."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from typing import Any

from src.platform.paths import HubPaths


EVIDENCE_SCHEMA_V1 = "component-runtime-evidence.v1"
EVIDENCE_SCHEMA = "component-runtime-evidence.v2"
EVIDENCE_SCHEMA_V2 = EVIDENCE_SCHEMA
_V1_FILE = "component_runtime_evidence.v1.json"
_V2_FILE = "component_runtime_evidence.v2.json"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TOKEN = re.compile(r"^[a-z][a-z0-9_.-]{0,95}$")
_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}$")
_CATALOG_VERSION = re.compile(r"^\d{4}\.\d{2}\.\d{2}$")
_CATALOG_SCHEMAS = frozenset({"v7-production-catalog.v1", "v7-production-catalog.v2"})
_BINDING_KEYS = frozenset({
    "catalog_schema",
    "catalog_version",
    "catalog_revision",
    "catalog_fingerprint",
    "source_identity",
    "runtime_id",
    "record_revision",
    "install_strategy",
})
_V2_RECORD_KEYS = frozenset({
    "schema_version",
    "component_id",
    "runtime_id",
    "status",
    "execution",
    "catalog_schema",
    "catalog_version",
    "catalog_revision",
    "catalog_fingerprint",
    "source_identity",
    "record_revision",
    "install_strategy",
    "runtime_fingerprint",
    "smoke_id",
    "smoked_at",
    "details",
})
_DETAIL_KEYS = frozenset({"version", "exit_code", "artifact_kind", "artifact_verified"})
_EVIDENCE_DOCUMENT_KEYS = frozenset({"schema_version", "records"})
_UNSAFE_TEXT = re.compile(r"(?i)(?:[a-z]:[\\/]|\\\\|://|\b(?:cmd|powershell|python)(?:\.exe)?\b|\b(?:api[_-]?key|secret|password|bearer|token)\b)")


def _is_reparse(path: Path) -> bool:
    try:
        if stat.S_ISLNK(path.lstat().st_mode):
            return True
    except FileNotFoundError:
        return False
    except OSError:
        return True
    if os.name == "nt":
        try:
            import ctypes
            attrs = int(ctypes.windll.kernel32.GetFileAttributesW(str(path))) & 0xFFFFFFFF
            return attrs != 0xFFFFFFFF and bool(attrs & 0x400)
        except (AttributeError, OSError):
            return True
    return False


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        handle, name = tempfile.mkstemp(prefix=".component-evidence-", suffix=".tmp", dir=path.parent)
        os.close(handle)
        temporary = Path(name)
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False)
            stream.write("\n")
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


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate evidence key")
        result[key] = value
    return result


def _reject_nonfinite(_value: str) -> None:
    raise ValueError("nonfinite evidence number")


def _safe_text(value: object, *, nullable: bool = False, maximum: int = 128) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > maximum or any(ord(char) < 32 for char in value) or _UNSAFE_TEXT.search(value):
        return None
    return value


def _safe_token(value: object) -> str | None:
    if not isinstance(value, str) or _TOKEN.fullmatch(value) is None:
        return None
    return value


def _safe_identity(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None or _UNSAFE_TEXT.search(value):
        return None
    return value


def _normalize_binding(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping) or frozenset(value) != _BINDING_KEYS:
        return None
    schema = value.get("catalog_schema")
    if not isinstance(schema, str) or schema not in _CATALOG_SCHEMAS:
        return None
    raw_catalog_version = value.get("catalog_version")
    raw_catalog_revision = value.get("catalog_revision")
    if raw_catalog_version is not None and (_safe_text(raw_catalog_version) is None or _CATALOG_VERSION.fullmatch(str(raw_catalog_version)) is None):
        return None
    if raw_catalog_revision is not None and (_safe_text(raw_catalog_revision) is None or _CATALOG_VERSION.fullmatch(str(raw_catalog_revision)) is None):
        return None
    catalog_version = _safe_text(raw_catalog_version, nullable=True)
    catalog_revision = _safe_text(raw_catalog_revision, nullable=True)
    fingerprint = value.get("catalog_fingerprint")
    if not isinstance(fingerprint, str) or _SHA256.fullmatch(fingerprint.casefold()) is None:
        return None
    runtime_id = _safe_token(value.get("runtime_id"))
    record_revision = _safe_text(value.get("record_revision"), maximum=128)
    install_strategy = _safe_token(value.get("install_strategy"))
    raw_source_identity = value.get("source_identity")
    if raw_source_identity is not None and _safe_identity(raw_source_identity) is None:
        return None
    source_identity = _safe_identity(raw_source_identity)
    if runtime_id is None or record_revision is None or install_strategy is None:
        return None
    return {
        "catalog_schema": schema,
        "catalog_version": catalog_version,
        "catalog_revision": catalog_revision,
        "catalog_fingerprint": fingerprint.casefold(),
        "source_identity": source_identity,
        "runtime_id": runtime_id,
        "record_revision": record_revision,
        "install_strategy": install_strategy,
    }


def runtime_catalog_binding(
    *,
    catalog_schema: object,
    catalog_version: object,
    catalog_revision: object,
    catalog_fingerprint: object,
    source_identity: object,
    runtime_id: object,
    record_revision: object,
    install_strategy: object,
) -> dict[str, Any] | None:
    """Normalize one server-owned catalog/runtime binding for evidence checks."""

    return _normalize_binding({
        "catalog_schema": catalog_schema,
        "catalog_version": catalog_version,
        "catalog_revision": catalog_revision,
        "catalog_fingerprint": catalog_fingerprint,
        "source_identity": source_identity,
        "runtime_id": runtime_id,
        "record_revision": record_revision,
        "install_strategy": install_strategy,
    })


def _safe_leaf(value: object) -> str | None:
    if not isinstance(value, str) or not value or "\\" in value or value.startswith("/") or ":" in value or "\x00" in value:
        return None
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or any(part in {"", "."} for part in path.parts):
        return None
    return path.as_posix()


def _runtime_root(paths: HubPaths, record: Mapping[str, Any]) -> Path:
    if record.get("root_class") == "environments_root":
        return paths.environments_root
    if record.get("root_class") == "external_managed":
        return paths.runtime_root / "external"
    return paths.runtime_root


def _safe_runtime_target(root: Path, target: Path) -> bool:
    try:
        lexical_root = root.absolute()
        current = target.absolute()
        current.relative_to(lexical_root)
    except (OSError, ValueError):
        return False
    while True:
        if _is_reparse(current):
            return False
        if current == lexical_root:
            return True
        if current.parent == current:
            return False
        current = current.parent


def _leaf_observations(paths: HubPaths, record: Mapping[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    required = record.get("required_leaves")
    if not isinstance(required, list) or not required or len({item for item in required if isinstance(item, str)}) != len(required):
        return [], False
    leaves: list[dict[str, Any]] = []
    all_present = True
    root = _runtime_root(paths, record)
    for relative in required:
        safe_relative = _safe_leaf(relative)
        if safe_relative is None:
            return [], False
        target = root / Path(safe_relative)
        try:
            if not _safe_runtime_target(root, target) or not target.is_file():
                leaves.append({"relative_leaf": safe_relative, "present": False})
                all_present = False
            else:
                info = target.stat()
                leaves.append({"relative_leaf": safe_relative, "present": True, "size_bytes": int(info.st_size), "mtime_ns": int(info.st_mtime_ns)})
        except OSError:
            leaves.append({"relative_leaf": safe_relative, "present": False})
            all_present = False
    return leaves, all_present


def runtime_fingerprint(paths: HubPaths, record: Mapping[str, Any], *, binding: Mapping[str, Any] | None = None) -> str:
    """Fingerprint fixed runtime leaves without hashing large binaries."""

    leaves, _all_present = _leaf_observations(paths, record)
    payload: dict[str, Any] = {
        "runtime_id": record.get("runtime_id"),
        "revision": record.get("revision"),
        "root_class": record.get("root_class"),
        "leaves": leaves,
    }
    normalized = _normalize_binding(binding) if binding is not None else None
    if normalized is not None:
        payload.update({
            "install_strategy": normalized["install_strategy"],
            "source_identity": normalized["source_identity"],
            "catalog_binding": normalized,
        })
    return hashlib.sha256(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _path(paths: HubPaths, schema: str) -> Path:
    return paths.config_root / (_V2_FILE if schema == EVIDENCE_SCHEMA_V2 else _V1_FILE)


def _read_document(path: Path, schema: str) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_strict_pairs, parse_constant=_reject_nonfinite)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(value, Mapping) or frozenset(value) != _EVIDENCE_DOCUMENT_KEYS or value.get("schema_version") != schema or not isinstance(value.get("records"), Mapping):
        return None
    return {"schema_version": schema, "records": dict(value["records"])}


def read_runtime_evidence(paths: HubPaths, *, schema: str | None = None) -> dict[str, Any]:
    """Read v2 evidence first while retaining v1 as diagnostics-only data."""

    candidates = [schema] if schema in {EVIDENCE_SCHEMA_V1, EVIDENCE_SCHEMA_V2} else [EVIDENCE_SCHEMA_V2, EVIDENCE_SCHEMA_V1]
    for candidate in candidates:
        document = _read_document(_path(paths, candidate), candidate)
        if document is not None:
            records: dict[str, Any] = {}
            for component_id, value in document["records"].items():
                safe = _safe_diagnostic_record(value, candidate)
                if safe is not None and isinstance(component_id, str) and _safe_token(component_id) is not None:
                    records[component_id] = safe
            return {"schema_version": candidate, "records": records}
    return {"schema_version": schema or EVIDENCE_SCHEMA_V2, "records": {}}


def _safe_details(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping) or not frozenset(value).issubset(_DETAIL_KEYS):
        return None
    result: dict[str, Any] = {}
    for key, item in value.items():
        if key in {"version", "artifact_kind"}:
            safe = _safe_text(item, maximum=96)
            if safe is None:
                return None
            result[key] = safe
        elif key == "exit_code":
            if isinstance(item, bool) or not isinstance(item, int) or not -100000 <= item <= 100000:
                return None
            result[key] = item
        elif key == "artifact_verified":
            if not isinstance(item, bool):
                return None
            result[key] = item
    return result


def _safe_diagnostic_record(value: object, schema: str) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    if schema == EVIDENCE_SCHEMA_V1:
        component_id = _safe_token(value.get("component_id"))
        status = value.get("status")
        execution = value.get("execution")
        fingerprint = value.get("runtime_fingerprint")
        smoke_id = value.get("smoke_id")
        smoked_at = value.get("smoked_at")
        if (
            component_id is None
            or status not in {"completed", "failed", "unavailable", "not_run"}
            or execution not in {"completed", "not_run"}
            or not isinstance(fingerprint, str)
            or _SHA256.fullmatch(fingerprint) is None
            or not isinstance(smoke_id, str)
            or not smoke_id
            or len(smoke_id) > 96
            or _UNSAFE_TEXT.search(smoke_id)
            or isinstance(smoked_at, bool)
            or not isinstance(smoked_at, int)
            or smoked_at < 0
        ):
            return None
        details = _safe_details(value.get("details", {}))
        if details is None:
            return None
        return {
            "component_id": component_id,
            "status": status,
            "execution": execution,
            "runtime_fingerprint": fingerprint,
            "smoke_id": smoke_id,
            "smoked_at": smoked_at,
            "details": details,
        }
    if frozenset(value) != _V2_RECORD_KEYS:
        return None
    binding = _normalize_binding({key: value.get(key) for key in _BINDING_KEYS})
    component_id = _safe_token(value.get("component_id"))
    fingerprint = value.get("runtime_fingerprint")
    smoke_id = value.get("smoke_id")
    smoked_at = value.get("smoked_at")
    details = _safe_details(value.get("details"))
    if (
        binding is None
        or component_id is None
        or value.get("schema_version") != EVIDENCE_SCHEMA_V2
        or value.get("runtime_id") != component_id
        or value.get("status") not in {"completed", "failed", "unavailable", "not_run"}
        or value.get("execution") not in {"completed", "not_run"}
        or not isinstance(fingerprint, str)
        or _SHA256.fullmatch(fingerprint) is None
        or not isinstance(smoke_id, str)
        or not smoke_id
        or len(smoke_id) > 96
        or _UNSAFE_TEXT.search(smoke_id)
        or isinstance(smoked_at, bool)
        or not isinstance(smoked_at, int)
        or smoked_at < 0
        or details is None
    ):
        return None
    return {
        "schema_version": EVIDENCE_SCHEMA_V2,
        "component_id": component_id,
        "runtime_id": component_id,
        "status": value.get("status"),
        "execution": value.get("execution"),
        **binding,
        "runtime_fingerprint": fingerprint,
        "smoke_id": smoke_id,
        "smoked_at": smoked_at,
        "details": details,
    }


def record_runtime_smoke(
    paths: HubPaths,
    component_id: str,
    record: Mapping[str, Any],
    *,
    outcome: str,
    smoke_id: str,
    details: Mapping[str, Any] | None = None,
    timestamp: int | None = None,
    catalog_binding: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if _safe_token(component_id) is None or outcome not in {"completed", "failed", "unavailable", "not_run"} or not isinstance(smoke_id, str) or not smoke_id or len(smoke_id) > 96 or _UNSAFE_TEXT.search(smoke_id):
        return {"status": "invalid", "code": "evidence_invalid", "execution": "not_run"}
    smoked_at = int(time.time()) if timestamp is None else int(timestamp)
    if smoked_at < 0:
        return {"status": "invalid", "code": "evidence_invalid", "execution": "not_run"}
    if catalog_binding is None:
        schema = EVIDENCE_SCHEMA_V1
        value = read_runtime_evidence(paths, schema=schema)
        safe_details = {key: item for key, item in (details or {}).items() if key in _DETAIL_KEYS}
        evidence = {
            "component_id": component_id,
            "status": outcome,
            "execution": "completed" if outcome == "completed" else "not_run",
            "runtime_fingerprint": runtime_fingerprint(paths, record),
            "smoke_id": smoke_id,
            "smoked_at": smoked_at,
            "details": safe_details,
        }
    else:
        binding = _normalize_binding(catalog_binding)
        if binding is None or binding["runtime_id"] != component_id:
            return {"status": "invalid", "code": "evidence_binding_invalid", "execution": "not_run"}
        safe_details = _safe_details(details or {})
        if safe_details is None:
            return {"status": "invalid", "code": "evidence_details_invalid", "execution": "not_run"}
        schema = EVIDENCE_SCHEMA_V2
        value = read_runtime_evidence(paths, schema=schema)
        evidence = {
            "schema_version": EVIDENCE_SCHEMA_V2,
            "component_id": component_id,
            "runtime_id": binding["runtime_id"],
            "status": outcome,
            "execution": "completed" if outcome == "completed" else "not_run",
            **binding,
            "runtime_fingerprint": runtime_fingerprint(paths, record, binding=binding),
            "smoke_id": smoke_id,
            "smoked_at": smoked_at,
            "details": safe_details,
        }
    value["records"][component_id] = evidence
    _atomic_write(_path(paths, schema), value)
    return {"status": "saved", "component_id": component_id, "outcome": outcome, "runtime_fingerprint": evidence["runtime_fingerprint"]}


def _v2_record_matches(
    evidence: object,
    *,
    paths: HubPaths,
    component_id: str,
    record: Mapping[str, Any],
    binding: Mapping[str, Any],
    now: int,
    max_age_seconds: int,
) -> bool:
    if not isinstance(evidence, Mapping) or frozenset(evidence) != _V2_RECORD_KEYS:
        return False
    if evidence.get("schema_version") != EVIDENCE_SCHEMA_V2 or evidence.get("status") != "completed" or evidence.get("execution") != "completed":
        return False
    if evidence.get("component_id") != component_id or evidence.get("runtime_id") != component_id:
        return False
    candidate_binding = _normalize_binding({key: evidence.get(key) for key in _BINDING_KEYS})
    current_binding = _normalize_binding(binding)
    if candidate_binding is None or current_binding is None or candidate_binding != current_binding:
        return False
    fingerprint = evidence.get("runtime_fingerprint")
    if not isinstance(fingerprint, str) or _SHA256.fullmatch(fingerprint) is None:
        return False
    smoke_id = evidence.get("smoke_id")
    if not isinstance(smoke_id, str) or not smoke_id or len(smoke_id) > 96 or _UNSAFE_TEXT.search(smoke_id):
        return False
    smoked_at = evidence.get("smoked_at")
    if isinstance(smoked_at, bool) or not isinstance(smoked_at, int) or smoked_at < 0 or now < smoked_at or now - smoked_at > max_age_seconds:
        return False
    if _safe_details(evidence.get("details")) is None:
        return False
    leaves, all_present = _leaf_observations(paths, record)
    if not all_present or not leaves:
        return False
    return fingerprint == runtime_fingerprint(paths, record, binding=current_binding)


def runtime_evidence_passed(
    paths: HubPaths,
    component_id: str,
    record: Mapping[str, Any],
    *,
    binding: Mapping[str, Any] | None = None,
    now: int | None = None,
    max_age_seconds: int = 24 * 60 * 60,
) -> bool:
    """Promote only an exact, fresh v2 evidence record for current catalog state."""

    current_binding = _normalize_binding(binding)
    if current_binding is None or current_binding["runtime_id"] != component_id:
        return False
    expected_source_identity = record.get("source_identity") if isinstance(record.get("source_identity"), str) else None
    if (
        current_binding["runtime_id"] != record.get("runtime_id")
        or current_binding["record_revision"] != record.get("revision")
        or current_binding["install_strategy"] != record.get("install_strategy")
        or current_binding["source_identity"] != expected_source_identity
    ):
        return False
    document = _read_document(_path(paths, EVIDENCE_SCHEMA_V2), EVIDENCE_SCHEMA_V2)
    evidence = document["records"].get(component_id) if document is not None else None
    current = int(time.time()) if now is None else int(now)
    return _v2_record_matches(
        evidence,
        paths=paths,
        component_id=component_id,
        record=record,
        binding=current_binding,
        now=current,
        max_age_seconds=max_age_seconds,
    )


__all__ = [
    "EVIDENCE_SCHEMA",
    "EVIDENCE_SCHEMA_V1",
    "EVIDENCE_SCHEMA_V2",
    "read_runtime_evidence",
    "record_runtime_smoke",
    "runtime_catalog_binding",
    "runtime_evidence_passed",
    "runtime_fingerprint",
]
