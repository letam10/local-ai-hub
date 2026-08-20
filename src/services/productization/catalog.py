"""Production model/runtime catalog and bounded owner-install discovery.

This is the final V7 catalog boundary.  Catalog metadata is tracked and may
describe a real upstream source, but a source is never treated as a usable
download until its disposition says so.  Discovery is fixed-root and leaf
bounded; it does not scan drives, load models, run Python, or launch tools.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import math
import os
import re
from pathlib import Path
import stat
import tempfile
from typing import Any
from urllib.parse import urlsplit

from src.platform.paths import HubPaths, get_paths
from src.services.operational_closure.source_availability import SourceAvailabilityService
from src.services.operational_closure.evidence import runtime_catalog_binding, runtime_evidence_passed, runtime_fingerprint


SCHEMA = "v7-production-catalog.v1"
SCHEMA_V2 = "v7-production-catalog.v2"
SUPPORTED_SCHEMAS = frozenset({SCHEMA, SCHEMA_V2})
MODEL_DISPOSITIONS = frozenset({"AUTO_INSTALL_READY", "AUTH_REQUIRED", "LICENSE_REQUIRED", "MANUAL_IMPORT_ONLY", "UNSUPPORTED_SOURCE"})
RUNTIME_DISPOSITIONS = frozenset({"AUTO_INSTALL_READY", "REFERENCE_EXISTING", "MANUAL_INSTALL", "UNSUPPORTED"})
MODEL_STATES = frozenset({"INSTALLED", "NOT_INSTALLED", "PARTIAL", "UNAVAILABLE", "OPERATIONAL"})
RUNTIME_STATES = frozenset({"INSTALLED", "NOT_INSTALLED", "PARTIAL", "UNAVAILABLE", "OPERATIONAL"})

V2_MODEL_IDS = frozenset({
    "animesr-v2", "sam2.1-hiera-small", "faster-whisper-large-v3",
    "flux-2-klein-base-4b-fp8", "qwen-image-2512-fp8", "practical-rife-v4",
    "realesrgan-x4plus", "omniparser-v2", "rfdetr-base", "grounding-dino-base",
    "paddleocr-vl-0.9b", "qwen3-tts-1.7b", "seed-vc-1", "comfyui-workflow-assets",
})
V2_RUNTIME_IDS = frozenset({
    "ffmpeg", "sam2", "animesr", "faster-whisper", "comfyui", "practical-rife",
    "real-esrgan", "omniparser", "rfdetr", "grounding-dino", "paddleocr-vl",
    "qwen3-tts", "seed-vc", "voice", "airi",
})
V2_MODEL_DISPOSITIONS = frozenset({"AUTO_INSTALL_READY", "AUTH_REQUIRED", "MANUAL_IMPORT_ONLY", "UNSUPPORTED_SOURCE"})
V2_RUNTIME_DISPOSITIONS = frozenset({"AUTO_INSTALL_READY", "REFERENCE_EXISTING", "MANUAL_INSTALL", "UNSUPPORTED_SOURCE"})
V2_MODEL_INSTALL_STRATEGIES = frozenset({"portable_archive", "provider_authorization", "manual_import", "unsupported_source"})
V2_RUNTIME_INSTALL_STRATEGIES = frozenset({"portable_archive", "reference_existing", "manual_install", "unsupported_source"})
V2_SOURCE_STATES = frozenset({"verified", "unverified", "manual", "auth_required", "unsupported", "unknown"})
V2_LICENSE_STATES = frozenset({"review_required", "apache-2.0", "gated", "unknown"})
V2_AUTH_STATES = frozenset({"not_required", "required", "unknown"})
V2_UPDATE_PARTS = frozenset({"backend", "runtime", "dependencies", "model"})
V2_ROOT_CLASSES = frozenset({"runtime_root", "environments_root", "external_managed"})
_V2_CATALOG_KEYS = frozenset({"schema_version", "catalog_version", "models", "runtimes"})
_V2_MODEL_KEYS = frozenset({
    "model_id", "display_name", "kind", "category", "provider", "version", "revision",
    "runtime_id", "modules", "files", "disposition", "install_strategy", "primary_source",
    "trusted_fallback_sources", "source_identity", "source_verification", "latest_upstream_revision",
    "latest_supported_revision", "license", "authentication", "update_parts", "estimated_download_size",
    "estimated_disk_size", "minimum_vram_mb", "recommended_vram_mb", "notes",
})
_V2_RUNTIME_KEYS = frozenset({
    "runtime_id", "display_name", "kind", "provider", "version", "revision", "root_class",
    "required_leaves", "modules", "disposition", "install_strategy", "primary_source",
    "trusted_fallback_sources", "source_identity", "source_verification", "latest_upstream_revision",
    "latest_supported_revision", "license", "authentication", "update_parts", "integrity",
    "estimated_download_size", "estimated_disk_size", "notes",
})
_V2_SOURCE_KEYS = frozenset({"provider", "kind", "canonical_identity", "revision", "verification_state", "url", "authentication_required", "license_required"})
_V2_LICENSE_KEYS = frozenset({"state", "spdx_id", "url"})
_V2_AUTH_KEYS = frozenset({"required", "state"})
_V2_INTEGRITY_KEYS = frozenset({"verification", "size_bytes", "sha256"})
_V2_LEAF_KEYS = frozenset({"relative_path", "verification", "size_bytes", "sha256"})
_V2_VERSION_RE = re.compile(r"^\d{4}\.\d{2}\.\d{2}$")
_V2_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_V2_UNSAFE_TEXT_RE = re.compile(r"(?i)(?:[a-z]:[\\/]|\\\\|\b(?:cmd|powershell)(?:\.exe)?\b|\bpython(?:\.exe)?\s+[-/]|\b(?:api[_-]?key|secret|password|bearer|token)\b)")


class ProductionCatalogError(ValueError):
    """Raised when a production catalog is malformed or unsafe."""


def _strict_json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProductionCatalogError("duplicate_json_key")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise ProductionCatalogError("nonfinite_json_number")


def _reject_nonfinite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ProductionCatalogError("nonfinite_json_number")
    return parsed


def _load_json_strict(path: Path) -> object:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ProductionCatalogError("catalog_unreadable") from exc
    try:
        return json.loads(text, object_pairs_hook=_strict_json_pairs, parse_constant=_reject_nonfinite, parse_float=_reject_nonfinite_float)
    except ProductionCatalogError:
        raise
    except json.JSONDecodeError as exc:
        raise ProductionCatalogError("catalog_unreadable") from exc


def _v2_keys(value: Mapping[str, Any], expected: frozenset[str], code: str) -> None:
    if frozenset(value) != expected:
        raise ProductionCatalogError(code)


def _v2_text(value: object, field: str, *, nullable: bool = False, max_length: int = 256) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > max_length or _V2_UNSAFE_TEXT_RE.search(value):
        raise ProductionCatalogError(f"invalid_{field}")
    return value


def _v2_id(value: object, field: str) -> str:
    result = _safe_id(value, field)
    if _V2_UNSAFE_TEXT_RE.search(result):
        raise ProductionCatalogError(f"invalid_{field}")
    return result


def _v2_nullable_revision(value: object, field: str) -> str | None:
    return _v2_text(value, field, nullable=True, max_length=128)


def _v2_public_url(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 2048 or not value.startswith("https://"):
        raise ProductionCatalogError(f"invalid_{field}")
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise ProductionCatalogError(f"invalid_{field}") from exc
    if not parsed.hostname or parsed.username or parsed.password or any(marker in value.casefold() for marker in ("secret", "token", "password", "api_key", "bearer")):
        raise ProductionCatalogError(f"unsafe_{field}")
    return value


def _v2_relative(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or value.startswith("/") or ":" in value or "\x00" in value:
        raise ProductionCatalogError("unsafe_catalog_leaf")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or any(part in {"", "."} for part in path.parts):
        raise ProductionCatalogError("unsafe_catalog_leaf")
    return path.as_posix()


def _v2_integrity(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProductionCatalogError(f"invalid_{field}")
    if not frozenset(value).issubset(_V2_INTEGRITY_KEYS) or "verification" not in value:
        raise ProductionCatalogError(f"invalid_{field}_fields")
    verification = value.get("verification")
    if verification not in {"verified", "unverified"}:
        raise ProductionCatalogError(f"invalid_{field}_verification")
    result: dict[str, Any] = {"verification": verification}
    if verification == "verified":
        size = value.get("size_bytes")
        digest = value.get("sha256")
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0 or not isinstance(digest, str) or not _V2_SHA256_RE.fullmatch(digest):
            raise ProductionCatalogError(f"invalid_{field}_integrity")
        result.update({"size_bytes": size, "sha256": digest})
    elif "size_bytes" in value or "sha256" in value:
        raise ProductionCatalogError(f"unverified_{field}_must_omit_integrity")
    return result


def _v2_leaf(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProductionCatalogError("invalid_model_file")
    if not frozenset(value).issubset(_V2_LEAF_KEYS) or "relative_path" not in value or "verification" not in value:
        raise ProductionCatalogError("invalid_model_file_fields")
    relative = _v2_relative(value.get("relative_path"))
    verification = value.get("verification")
    if verification not in {"verified", "unverified"}:
        raise ProductionCatalogError("invalid_model_file_verification")
    result: dict[str, Any] = {"relative_path": relative, "verification": verification}
    if verification == "verified":
        size = value.get("size_bytes")
        digest = value.get("sha256")
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0 or not isinstance(digest, str) or not _V2_SHA256_RE.fullmatch(digest):
            raise ProductionCatalogError("invalid_model_file_integrity")
        result.update({"size_bytes": size, "sha256": digest})
    elif "size_bytes" in value or "sha256" in value:
        raise ProductionCatalogError("unverified_model_file_must_omit_integrity")
    return result


def _v2_source(value: object, expected_identity: str | None = None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProductionCatalogError("invalid_catalog_source")
    _v2_keys(value, _V2_SOURCE_KEYS, "invalid_catalog_source_fields")
    provider = _v2_text(value.get("provider"), "source_provider", max_length=96)
    kind = _v2_text(value.get("kind"), "source_kind", max_length=64)
    identity = _v2_text(value.get("canonical_identity"), "source_identity", max_length=256)
    revision = _v2_nullable_revision(value.get("revision"), "source_revision")
    state = value.get("verification_state")
    if state not in V2_SOURCE_STATES:
        raise ProductionCatalogError("invalid_source_verification")
    url = _v2_public_url(value.get("url"), "source_url")
    auth_required = value.get("authentication_required")
    license_required = value.get("license_required")
    if not isinstance(auth_required, bool) or not isinstance(license_required, bool):
        raise ProductionCatalogError("invalid_source_flags")
    if expected_identity is not None and identity != expected_identity:
        raise ProductionCatalogError("source_identity_mismatch")
    return {
        "provider": provider, "kind": kind, "canonical_identity": identity, "revision": revision,
        "verification_state": state, "url": url, "authentication_required": auth_required,
        "license_required": license_required,
    }


def _v2_sources(value: object, expected_identity: str | None) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 4:
        raise ProductionCatalogError("invalid_trusted_fallback_sources")
    result = [_v2_source(item, expected_identity) for item in value]
    identities = [item["canonical_identity"] for item in result]
    if len(set(identities)) != len(identities):
        raise ProductionCatalogError("duplicate_trusted_fallback_source")
    return result


def _v2_license(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProductionCatalogError("invalid_license")
    _v2_keys(value, _V2_LICENSE_KEYS, "invalid_license_fields")
    state = value.get("state")
    if state not in V2_LICENSE_STATES:
        raise ProductionCatalogError("invalid_license_state")
    spdx_id = _v2_text(value.get("spdx_id"), "license_spdx_id", nullable=True, max_length=64)
    url = _v2_public_url(value.get("url"), "license_url")
    return {"state": state, "spdx_id": spdx_id, "url": url}


def _v2_authentication(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProductionCatalogError("invalid_authentication")
    _v2_keys(value, _V2_AUTH_KEYS, "invalid_authentication_fields")
    required = value.get("required")
    state = value.get("state")
    if not isinstance(required, bool) or state not in V2_AUTH_STATES or (state == "required") != required and state != "unknown":
        raise ProductionCatalogError("invalid_authentication_state")
    return {"required": required, "state": state}


def _v2_update_parts(value: object) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value) or len(set(value)) != len(value) or any(item not in V2_UPDATE_PARTS for item in value):
        raise ProductionCatalogError("invalid_update_parts")
    return list(value)


def _v2_estimate(value: object, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProductionCatalogError(f"invalid_{field}")
    return value


def _v2_notes(value: object) -> str:
    if not isinstance(value, str) or len(value) > 500 or _V2_UNSAFE_TEXT_RE.search(value):
        raise ProductionCatalogError("invalid_catalog_notes")
    return value


def _validate_v2_model(item: object) -> dict[str, Any]:
    if not isinstance(item, Mapping):
        raise ProductionCatalogError("invalid_model_record")
    _v2_keys(item, _V2_MODEL_KEYS, "invalid_model_record_fields")
    model_id = _v2_id(item.get("model_id"), "model_id")
    if item.get("kind") != "model":
        raise ProductionCatalogError("invalid_model_kind")
    runtime_id = _v2_id(item.get("runtime_id"), "runtime_id")
    modules = item.get("modules")
    if not isinstance(modules, list) or any(not isinstance(value, str) for value in modules) or len(set(modules)) != len(modules):
        raise ProductionCatalogError("invalid_model_modules")
    normalized_modules = [_v2_id(value, "module_id") for value in modules]
    files_raw = item.get("files")
    if not isinstance(files_raw, list) or not files_raw:
        raise ProductionCatalogError("invalid_model_files")
    files = [_v2_leaf(value) for value in files_raw]
    if len({value["relative_path"] for value in files}) != len(files):
        raise ProductionCatalogError("duplicate_model_file")
    disposition = item.get("disposition")
    strategy = item.get("install_strategy")
    if disposition not in V2_MODEL_DISPOSITIONS or strategy not in V2_MODEL_INSTALL_STRATEGIES:
        raise ProductionCatalogError("invalid_model_install_contract")
    source_identity = _v2_text(item.get("source_identity"), "source_identity", nullable=True, max_length=256)
    primary = None if item.get("primary_source") is None else _v2_source(item.get("primary_source"), source_identity)
    fallbacks = _v2_sources(item.get("trusted_fallback_sources"), source_identity)
    if primary is not None and source_identity is None:
        raise ProductionCatalogError("source_identity_missing")
    if primary is None and fallbacks:
        raise ProductionCatalogError("fallback_without_primary_source")
    source_verification = item.get("source_verification")
    if source_verification not in V2_SOURCE_STATES or source_verification == "verified" and primary is None:
        raise ProductionCatalogError("invalid_model_source_verification")
    if primary is not None and source_verification != primary["verification_state"]:
        raise ProductionCatalogError("source_verification_mismatch")
    authentication = _v2_authentication(item.get("authentication"))
    if disposition == "AUTH_REQUIRED" and not authentication["required"]:
        raise ProductionCatalogError("auth_required_model_without_auth_state")
    return {
        "model_id": model_id, "display_name": _v2_text(item.get("display_name"), "display_name", max_length=160),
        "kind": "model", "category": _v2_text(item.get("category"), "category", max_length=48),
        "provider": _v2_text(item.get("provider"), "provider", max_length=96),
        "version": _v2_text(item.get("version"), "version", max_length=96),
        "revision": _v2_text(item.get("revision"), "revision", max_length=128), "runtime_id": runtime_id,
        "modules": normalized_modules, "files": files, "disposition": disposition, "install_strategy": strategy,
        "primary_source": primary, "trusted_fallback_sources": fallbacks, "source_identity": source_identity,
        "source_verification": source_verification, "latest_upstream_revision": _v2_nullable_revision(item.get("latest_upstream_revision"), "latest_upstream_revision"),
        "latest_supported_revision": _v2_nullable_revision(item.get("latest_supported_revision"), "latest_supported_revision"),
        "license": _v2_license(item.get("license")), "authentication": authentication,
        "update_parts": _v2_update_parts(item.get("update_parts")),
        "estimated_download_size": _v2_estimate(item.get("estimated_download_size"), "estimated_download_size"),
        "estimated_disk_size": _v2_estimate(item.get("estimated_disk_size"), "estimated_disk_size"),
        "minimum_vram_mb": _v2_estimate(item.get("minimum_vram_mb"), "minimum_vram_mb"),
        "recommended_vram_mb": _v2_estimate(item.get("recommended_vram_mb"), "recommended_vram_mb"),
        "notes": _v2_notes(item.get("notes")),
    }


def _validate_v2_runtime(item: object) -> dict[str, Any]:
    if not isinstance(item, Mapping):
        raise ProductionCatalogError("invalid_runtime_record")
    _v2_keys(item, _V2_RUNTIME_KEYS, "invalid_runtime_record_fields")
    runtime_id = _v2_id(item.get("runtime_id"), "runtime_id")
    disposition = item.get("disposition")
    strategy = item.get("install_strategy")
    if disposition not in V2_RUNTIME_DISPOSITIONS or strategy not in V2_RUNTIME_INSTALL_STRATEGIES:
        raise ProductionCatalogError("invalid_runtime_install_contract")
    modules = item.get("modules")
    if not isinstance(modules, list) or any(not isinstance(value, str) for value in modules) or len(set(modules)) != len(modules):
        raise ProductionCatalogError("invalid_runtime_modules")
    leaves = item.get("required_leaves")
    if not isinstance(leaves, list) or any(not isinstance(value, str) for value in leaves) or not leaves or len(set(leaves)) != len(leaves):
        raise ProductionCatalogError("invalid_runtime_leaves")
    normalized_leaves = [_v2_relative(value) for value in leaves]
    source_identity = _v2_text(item.get("source_identity"), "source_identity", nullable=True, max_length=256)
    primary = None if item.get("primary_source") is None else _v2_source(item.get("primary_source"), source_identity)
    fallbacks = _v2_sources(item.get("trusted_fallback_sources"), source_identity)
    if primary is not None and source_identity is None:
        raise ProductionCatalogError("source_identity_missing")
    if primary is None and fallbacks:
        raise ProductionCatalogError("fallback_without_primary_source")
    source_verification = item.get("source_verification")
    if source_verification not in V2_SOURCE_STATES or source_verification == "verified" and primary is None:
        raise ProductionCatalogError("invalid_runtime_source_verification")
    if primary is not None and source_verification != primary["verification_state"]:
        raise ProductionCatalogError("source_verification_mismatch")
    root_class = item.get("root_class")
    if root_class not in V2_ROOT_CLASSES:
        raise ProductionCatalogError("invalid_runtime_root_class")
    return {
        "runtime_id": runtime_id, "display_name": _v2_text(item.get("display_name"), "display_name", max_length=160),
        "kind": _v2_text(item.get("kind"), "runtime_kind", max_length=48),
        "provider": _v2_text(item.get("provider"), "provider", max_length=96),
        "version": _v2_text(item.get("version"), "version", max_length=96),
        "revision": _v2_text(item.get("revision"), "revision", max_length=128),
        "root_class": root_class,
        "required_leaves": normalized_leaves, "modules": [_v2_id(value, "module_id") for value in modules],
        "disposition": disposition, "install_strategy": strategy, "primary_source": primary,
        "trusted_fallback_sources": fallbacks, "source_identity": source_identity, "source_verification": source_verification,
        "latest_upstream_revision": _v2_nullable_revision(item.get("latest_upstream_revision"), "latest_upstream_revision"),
        "latest_supported_revision": _v2_nullable_revision(item.get("latest_supported_revision"), "latest_supported_revision"),
        "license": _v2_license(item.get("license")), "authentication": _v2_authentication(item.get("authentication")),
        "update_parts": _v2_update_parts(item.get("update_parts")), "integrity": _v2_integrity(item.get("integrity"), "runtime"),
        "estimated_download_size": _v2_estimate(item.get("estimated_download_size"), "estimated_download_size"),
        "estimated_disk_size": _v2_estimate(item.get("estimated_disk_size"), "estimated_disk_size"),
        "notes": _v2_notes(item.get("notes")),
    }


def _load_v2(raw: Mapping[str, Any]) -> dict[str, Any]:
    _v2_keys(raw, _V2_CATALOG_KEYS, "invalid_catalog_fields")
    if raw.get("schema_version") != SCHEMA_V2 or not isinstance(raw.get("catalog_version"), str) or not _V2_VERSION_RE.fullmatch(raw["catalog_version"]):
        raise ProductionCatalogError("invalid_catalog_version")
    models_raw = raw.get("models")
    runtimes_raw = raw.get("runtimes")
    if not isinstance(models_raw, list) or len(models_raw) != len(V2_MODEL_IDS) or not isinstance(runtimes_raw, list) or len(runtimes_raw) != len(V2_RUNTIME_IDS):
        raise ProductionCatalogError("catalog_record_count_mismatch")
    models = [_validate_v2_model(value) for value in models_raw]
    runtimes = [_validate_v2_runtime(value) for value in runtimes_raw]
    model_ids = [item["model_id"] for item in models]
    runtime_ids = [item["runtime_id"] for item in runtimes]
    if set(model_ids) != V2_MODEL_IDS or len(set(model_ids)) != len(model_ids) or set(runtime_ids) != V2_RUNTIME_IDS or len(set(runtime_ids)) != len(runtime_ids):
        raise ProductionCatalogError("catalog_id_set_mismatch")
    if any(item["runtime_id"] not in V2_RUNTIME_IDS for item in models):
        raise ProductionCatalogError("catalog_runtime_reference_missing")
    if any(item["disposition"] == "AUTO_INSTALL_READY" for item in models):
        raise ProductionCatalogError("model_auto_install_not_authorized")
    if any(item["disposition"] == "AUTO_INSTALL_READY" and item["runtime_id"] != "ffmpeg" for item in runtimes):
        raise ProductionCatalogError("runtime_auto_install_not_authorized")
    return {"schema_version": SCHEMA_V2, "catalog_version": raw["catalog_version"], "models": models, "runtimes": runtimes}


def _safe_id(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 96 or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789._-" for ch in value) or not value[0].isalpha():
        raise ProductionCatalogError(f"invalid_{field}")
    return value


def _safe_relative(value: object) -> str:
    if not isinstance(value, str) or not value or value.startswith(("/", "\\")) or ":" in value:
        raise ProductionCatalogError("unsafe_catalog_leaf")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or any(part in {"", "."} for part in path.parts):
        raise ProductionCatalogError("unsafe_catalog_leaf")
    return path.as_posix()


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


def _safe_leaf(root: Path, relative: str) -> Path | None:
    lexical_root = root.absolute()
    candidate = lexical_root / Path(relative)
    try:
        candidate.absolute().relative_to(lexical_root)
    except (OSError, ValueError):
        return None
    current = candidate.absolute()
    while True:
        if _is_reparse(current):
            return None
        if current == lexical_root:
            break
        if current.parent == current:
            return None
        current = current.parent
    try:
        candidate.resolve().relative_to(lexical_root.resolve())
    except (OSError, ValueError):
        return None
    return candidate


def _json_object(path: Path, fallback: object) -> object:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value
    except (OSError, UnicodeError, json.JSONDecodeError):
        return fallback


def _fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _validate_model(item: Mapping[str, Any]) -> dict[str, Any]:
    model_id = _safe_id(item.get("model_id"), "model_id")
    disposition = str(item.get("disposition", "MANUAL_IMPORT_ONLY"))
    if disposition not in MODEL_DISPOSITIONS:
        raise ProductionCatalogError("invalid_model_disposition")
    files_raw = item.get("files")
    if not isinstance(files_raw, list) or not files_raw:
        raise ProductionCatalogError("invalid_model_files")
    files: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in files_raw:
        if not isinstance(raw, Mapping):
            raise ProductionCatalogError("invalid_model_file")
        relative = _safe_relative(raw.get("relative_path"))
        if relative in seen:
            raise ProductionCatalogError("duplicate_model_file")
        seen.add(relative)
        size = raw.get("size_bytes")
        if not isinstance(size, int) or size < 0:
            raise ProductionCatalogError("invalid_model_file_size")
        digest = raw.get("sha256")
        if digest is not None and (not isinstance(digest, str) or len(digest) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in digest)):
            raise ProductionCatalogError("invalid_model_file_hash")
        files.append({"relative_path": relative, "size_bytes": size, "sha256": digest.lower() if isinstance(digest, str) else None})
    official_source = item.get("official_source")
    if isinstance(official_source, Mapping):
        official_source_value: Any = {
            key: value for key, value in official_source.items()
            if key in {"provider", "kind", "url", "https_url", "canonical_identity", "artifact_identity", "revision", "release", "priority", "authentication_required", "auth_required", "license_required"}
        }
    else:
        official_source_value = str(official_source or "local")[:2048]
    primary_source = item.get("primary_source")
    if primary_source is not None and not isinstance(primary_source, (str, Mapping)):
        primary_source = None
    fallback_sources = item.get("trusted_fallback_sources", [])
    if not isinstance(fallback_sources, list):
        fallback_sources = []
    return {
        "model_id": model_id,
        "display_name": str(item.get("display_name") or model_id)[:160],
        "category": str(item.get("category") or "Other")[:48],
        "provider": str(item.get("provider") or "unknown")[:96],
        "version": str(item.get("version") or "unknown")[:96],
        "revision": str(item.get("revision") or item.get("version") or "unknown")[:128],
        "official_source": official_source_value,
        "primary_source": primary_source,
        "trusted_fallback_sources": [dict(value) for value in fallback_sources[:4] if isinstance(value, Mapping)],
        "source_identity": str(item.get("source_identity") or item.get("canonical_identity") or item.get("revision") or "unknown")[:256],
        "latest_upstream_revision": str(item.get("latest_upstream_revision") or item.get("revision") or "unknown")[:128],
        "latest_supported_revision": str(item.get("latest_supported_revision") or item.get("revision") or "unknown")[:128],
        "update_parts": [str(value) for value in item.get("update_parts", []) if value in {"backend", "runtime", "dependencies", "model"}],
        "install_strategy": str(item.get("install_strategy") or "manual_import"),
        "archive_format": str(item.get("archive_format") or "")[:16],
        "archive_prefix": str(item.get("archive_prefix") or "")[:256],
        "archive_leaves": dict(item.get("archive_leaves")) if isinstance(item.get("archive_leaves"), Mapping) else {},
        "sha256": str(item.get("sha256"))[:64] if isinstance(item.get("sha256"), str) else None,
        "compatibility": dict(item.get("compatibility")) if isinstance(item.get("compatibility"), Mapping) else {},
        "update_candidate": dict(item.get("update_candidate")) if isinstance(item.get("update_candidate"), Mapping) else None,
        "source_type": str(item.get("source_type") or "metadata_only")[:64],
        "disposition": disposition,
        "license": str(item.get("license") or "review_required")[:160],
        "license_url": str(item.get("license_url") or "")[:2048],
        "authentication_required": bool(item.get("authentication_required", False)),
        "modules": sorted({_safe_id(value, "module_id") for value in item.get("modules", []) if isinstance(value, str)}),
        "runtime_id": str(item.get("runtime_id") or "")[:96] or None,
        "files": files,
        "estimated_download_size": max(0, int(item.get("estimated_download_size", 0) or 0)),
        "estimated_disk_size": max(0, int(item.get("estimated_disk_size", 0) or 0)),
        "minimum_vram_mb": max(0, int(item.get("minimum_vram_mb", 0) or 0)),
        "recommended_vram_mb": max(0, int(item.get("recommended_vram_mb", 0) or 0)),
        "notes": str(item.get("notes") or "")[:500],
    }


def _validate_runtime(item: Mapping[str, Any]) -> dict[str, Any]:
    runtime_id = _safe_id(item.get("runtime_id"), "runtime_id")
    disposition = str(item.get("disposition", "REFERENCE_EXISTING"))
    if disposition not in RUNTIME_DISPOSITIONS:
        raise ProductionCatalogError("invalid_runtime_disposition")
    leaves = item.get("required_leaves")
    if not isinstance(leaves, list) or not leaves:
        raise ProductionCatalogError("invalid_runtime_leaves")
    normalized = [_safe_relative(value) for value in leaves]
    official_source = item.get("official_source")
    if isinstance(official_source, Mapping):
        official_source_value: Any = {
            key: value for key, value in official_source.items()
            if key in {"provider", "kind", "url", "https_url", "canonical_identity", "artifact_identity", "revision", "release", "priority", "authentication_required", "auth_required", "license_required"}
        }
    else:
        official_source_value = str(official_source or "local")[:2048]
    primary_source = item.get("primary_source")
    if primary_source is not None and not isinstance(primary_source, (str, Mapping)):
        primary_source = None
    fallback_sources = item.get("trusted_fallback_sources", [])
    if not isinstance(fallback_sources, list):
        fallback_sources = []
    return {
        "runtime_id": runtime_id,
        "display_name": str(item.get("display_name") or runtime_id)[:160],
        "kind": str(item.get("kind") or "tool")[:48],
        "version": str(item.get("version") or "unknown")[:96],
        "revision": str(item.get("revision") or item.get("version") or "unknown")[:128],
        "root_class": str(item.get("root_class") or "runtime_root"),
        "required_leaves": normalized,
        "modules": sorted({_safe_id(value, "module_id") for value in item.get("modules", []) if isinstance(value, str)}),
        "official_source": official_source_value,
        "primary_source": primary_source,
        "trusted_fallback_sources": [dict(value) for value in fallback_sources[:4] if isinstance(value, Mapping)],
        "source_identity": str(item.get("source_identity") or item.get("canonical_identity") or item.get("revision") or "unknown")[:256],
        "latest_upstream_revision": str(item.get("latest_upstream_revision") or item.get("revision") or "unknown")[:128],
        "latest_supported_revision": str(item.get("latest_supported_revision") or item.get("revision") or "unknown")[:128],
        "update_parts": [str(value) for value in item.get("update_parts", []) if value in {"backend", "runtime", "dependencies", "model"}],
        "compatibility": dict(item.get("compatibility")) if isinstance(item.get("compatibility"), Mapping) else {},
        "update_candidate": dict(item.get("update_candidate")) if isinstance(item.get("update_candidate"), Mapping) else None,
        "source_type": str(item.get("source_type") or "metadata_only")[:64],
        "disposition": disposition,
        "install_strategy": str(item.get("install_strategy") or "reference_existing"),
        "archive_format": str(item.get("archive_format") or "")[:16],
        "archive_prefix": str(item.get("archive_prefix") or "")[:256],
        "archive_leaves": dict(item.get("archive_leaves")) if isinstance(item.get("archive_leaves"), Mapping) else {},
        "sha256": str(item.get("sha256"))[:64] if isinstance(item.get("sha256"), str) else None,
        "estimated_download_size": max(0, int(item.get("estimated_download_size", 0) or 0)),
        "estimated_disk_size": max(0, int(item.get("estimated_disk_size", 0) or 0)),
        "notes": str(item.get("notes") or "")[:500],
    }


def load_production_catalog(path: Path) -> dict[str, Any]:
    raw = _load_json_strict(path)
    if not isinstance(raw, Mapping) or raw.get("schema_version") not in SUPPORTED_SCHEMAS:
        raise ProductionCatalogError("unsupported_catalog_schema")
    if raw.get("schema_version") == SCHEMA_V2:
        return _load_v2(raw)
    models_raw = raw.get("models")
    runtimes_raw = raw.get("runtimes")
    if not isinstance(models_raw, list) or not isinstance(runtimes_raw, list):
        raise ProductionCatalogError("catalog_sections_missing")
    models = [_validate_model(item) for item in models_raw if isinstance(item, Mapping)]
    runtimes = [_validate_runtime(item) for item in runtimes_raw if isinstance(item, Mapping)]
    if len({item["model_id"] for item in models}) != len(models) or len({item["runtime_id"] for item in runtimes}) != len(runtimes):
        raise ProductionCatalogError("duplicate_catalog_id")
    return {"schema_version": SCHEMA, "models": models, "runtimes": runtimes}


class ProductionCatalog:
    """Read-only production catalog plus explicit, bounded size refresh."""

    def __init__(self, *, paths: HubPaths | None = None, catalog_path: Path | None = None) -> None:
        self.paths = paths or get_paths()
        self.catalog_path = catalog_path or self.paths.app_root / "Config" / "v7_production_catalog.example.json"
        loaded = load_production_catalog(self.catalog_path)
        self.catalog_schema_version = str(loaded.get("schema_version") or SCHEMA)
        self.catalog_version = loaded.get("catalog_version")
        self.models = {item["model_id"]: item for item in loaded["models"]}
        self.runtimes = {item["runtime_id"]: item for item in loaded["runtimes"]}
        self.fingerprint = _fingerprint(loaded)
        self.source_availability = SourceAvailabilityService(paths=self.paths)

    def _root_for_runtime(self, record: Mapping[str, Any]) -> Path:
        if record["root_class"] == "environments_root":
            return self.paths.environments_root
        if record["root_class"] == "external_managed":
            return self.paths.runtime_root / "external"
        return self.paths.runtime_root

    def _runtime_evidence_binding(self, runtime_id: str, record: Mapping[str, Any]) -> dict[str, Any] | None:
        catalog_version = self.catalog_version if isinstance(self.catalog_version, str) else None
        source_identity = record.get("source_identity") if isinstance(record.get("source_identity"), str) else None
        return runtime_catalog_binding(
            catalog_schema=self.catalog_schema_version,
            catalog_version=catalog_version,
            catalog_revision=catalog_version,
            catalog_fingerprint=self.fingerprint,
            source_identity=source_identity,
            runtime_id=runtime_id,
            record_revision=record.get("revision"),
            install_strategy=record.get("install_strategy"),
        )

    def _size_cache_path(self) -> Path:
        return self.paths.config_root / "model_size_cache.json"

    def _size_cache(self) -> dict[str, Any]:
        value = _json_object(self._size_cache_path(), {})
        return value if isinstance(value, dict) else {}

    def _receipt_size(self, model_id: str) -> int | None:
        value = _json_object(self.paths.config_root / "model_install_receipts.json", {})
        records = value.get("records") if isinstance(value, Mapping) else None
        record = records.get(model_id) if isinstance(records, Mapping) else None
        size = record.get("installed_size_bytes") if isinstance(record, Mapping) else None
        return int(size) if isinstance(size, int) and size >= 0 else None

    def inspect_model(self, model_id: str) -> dict[str, Any]:
        record = self.models.get(model_id)
        if record is None:
            raise ProductionCatalogError("unknown_model_id")
        root = self.paths.models_root / model_id
        leaves = []
        for expected in record["files"]:
            target = _safe_leaf(root, expected["relative_path"])
            present = bool(target and target.is_file())
            size = int(target.stat().st_size) if present else 0
            expected_size = expected.get("size_bytes")
            leaves.append({"relative_leaf": expected["relative_path"], "present": present, "size_bytes": size if present else None, "size_matches": bool(present and (expected_size is None or size == expected_size)), "verification": expected.get("verification", "unverified")})
        if leaves and all(item["present"] and item["size_matches"] for item in leaves):
            status = "INSTALLED"
            reason = "All catalog leaves are present; bounded smoke evidence is still required."
        elif any(item["present"] for item in leaves):
            status = "PARTIAL"
            reason = "Some catalog leaves are present but the managed installation is incomplete."
        else:
            status = "NOT_INSTALLED"
            reason = "No catalog leaf is present under the managed Models root."
        if record["disposition"] == "UNSUPPORTED_SOURCE":
            action = "Manual review is required; no trusted installation action is available."
        elif record["disposition"] == "AUTH_REQUIRED":
            action = "Authorize the official provider before installation."
        elif record["disposition"] == "LICENSE_REQUIRED":
            action = "Review and accept the upstream license before installation."
        elif record["disposition"] == "AUTO_INSTALL_READY":
            action = "Review the server-owned plan, then choose Download & Install."
        else:
            action = "Use Import Model after verifying the official source and license."
        cache = self._size_cache().get(model_id) if status == "INSTALLED" else None
        installed_size = cache.get("size_bytes") if isinstance(cache, Mapping) and isinstance(cache.get("size_bytes"), int) else self._receipt_size(model_id) if status == "INSTALLED" else None
        projected = {key: value for key, value in record.items() if key not in {"official_source", "primary_source", "trusted_fallback_sources", "license_url", "files", "update_candidate"}}
        projected.update({"status": status, "execution": "not_run", "operational": False, "leaves": leaves, "installed_size_bytes": installed_size, "expected_download_size_bytes": record["estimated_download_size"] or None, "expected_disk_size_bytes": record["estimated_disk_size"] or None, "source_availability": self.source_availability.cached(model_id), "reason": reason, "next_action": action})
        if installed_size is None and status == "INSTALLED":
            projected["size_label"] = "Size unavailable"
        elif installed_size is not None:
            projected["size_label"] = f"{installed_size} bytes"
        elif status == "NOT_INSTALLED":
            if record["estimated_download_size"]:
                projected["size_label"] = f"Download: {record['estimated_download_size']} bytes; Disk: {record['estimated_disk_size']} bytes"
            else:
                projected["size_label"] = "Size unavailable"
        return projected

    def inspect_runtime(self, runtime_id: str) -> dict[str, Any]:
        record = self.runtimes.get(runtime_id)
        if record is None:
            raise ProductionCatalogError("unknown_runtime_id")
        root = self._root_for_runtime(record)
        leaves = []
        for relative in record["required_leaves"]:
            target = _safe_leaf(root, relative)
            leaves.append({"relative_leaf": relative, "present": bool(target and target.is_file())})
        if leaves and all(item["present"] for item in leaves):
            binding = self._runtime_evidence_binding(runtime_id, record)
            status = "OPERATIONAL" if runtime_evidence_passed(self.paths, runtime_id, record, binding=binding) else "INSTALLED_UNVERIFIED"
            reason = "Required runtime leaves and fresh matching bounded smoke evidence are present." if status == "OPERATIONAL" else "Required runtime leaves are present; import/package and bounded smoke evidence are still required."
        elif any(item["present"] for item in leaves):
            status = "PARTIAL"
            reason = "Some runtime leaves are present but the runtime is incomplete."
        else:
            status = "NOT_INSTALLED"
            reason = "No required runtime leaf was observed at the managed root."
        binding = self._runtime_evidence_binding(runtime_id, record)
        projected = {key: value for key, value in record.items() if key not in {"official_source", "primary_source", "trusted_fallback_sources", "update_candidate"}}
        projected.update({"status": status, "execution": "not_run", "dry_run": True, "operational": status == "OPERATIONAL", "runtime_fingerprint": runtime_fingerprint(self.paths, record, binding=binding), "leaves": leaves, "source_availability": self.source_availability.cached(runtime_id), "reason": reason, "next_action": "Use Verify and run the bounded runtime smoke before operational promotion." if status != "OPERATIONAL" else "Runtime is operational under the last matching bounded evidence."})
        return projected

    def snapshot(self, *, query: str = "", category: str = "", installed: bool | None = None) -> dict[str, Any]:
        models = [self.inspect_model(model_id) for model_id in sorted(self.models)]
        runtimes = [self.inspect_runtime(runtime_id) for runtime_id in sorted(self.runtimes)]
        needle = query.strip().casefold()
        if needle:
            models = [item for item in models if needle in str(item.get("model_id", "")).casefold() or needle in str(item.get("display_name", "")).casefold()]
            runtimes = [item for item in runtimes if needle in str(item.get("runtime_id", "")).casefold() or needle in str(item.get("display_name", "")).casefold()]
        if category:
            models = [item for item in models if str(item.get("category", "")).casefold() == category.casefold()]
        if installed is not None:
            models = [item for item in models if (item["status"] == "INSTALLED") is installed]
        return {"schema_version": "v7-production-catalog-snapshot.v1", "catalog_schema_version": self.catalog_schema_version, "catalog_version": self.catalog_version, "status": "completed", "execution": "not_run", "dry_run": True, "catalog_fingerprint": self.fingerprint, "models": models, "runtimes": runtimes, "counts": {"models": len(models), "runtimes": len(runtimes), "installed_models": sum(item["status"] == "INSTALLED" for item in models), "install_ready": sum(item["disposition"] == "AUTO_INSTALL_READY" for item in models)}, "reason": "Catalog and fixed-leaf discovery only; no model/runtime process or network action ran.", "next_action": "Select a server-owned component plan before any installation."}

    def refresh_model_size(self, model_id: str, *, max_files: int = 10000) -> dict[str, Any]:
        """Explicit user-requested bounded size refresh; never runs on every UI refresh."""

        record = self.models.get(model_id)
        if record is None:
            raise ProductionCatalogError("unknown_model_id")
        root = (self.paths.models_root / model_id).absolute()
        if not root.is_dir() or _is_reparse(root):
            return {"status": "unavailable", "code": "model_root_missing", "execution": "not_run"}
        total = 0
        count = 0
        for path in root.rglob("*"):
            if count >= max_files or _is_reparse(path):
                if count >= max_files:
                    return {"status": "unavailable", "code": "size_scan_limit", "execution": "not_run"}
                continue
            if path.is_file():
                total += path.stat().st_size
                count += 1
        cache = self._size_cache()
        cache[model_id] = {"size_bytes": total, "file_count": count, "catalog_fingerprint": self.fingerprint}
        self.paths.config_root.mkdir(parents=True, exist_ok=True)
        target = self._size_cache_path()
        temporary = Path(tempfile.mkstemp(prefix=".model-size-", suffix=".tmp", dir=self.paths.config_root)[1])
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(cache, handle, ensure_ascii=True, sort_keys=True, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return {"status": "completed", "model_id": model_id, "size_bytes": total, "file_count": count, "execution": "not_run"}


def catalog_snapshot(*, paths: HubPaths | None = None, query: str = "", category: str = "", installed: bool | None = None) -> dict[str, Any]:
    return ProductionCatalog(paths=paths).snapshot(query=query, category=category, installed=installed)


__all__ = ["MODEL_DISPOSITIONS", "ProductionCatalog", "ProductionCatalogError", "RUNTIME_DISPOSITIONS", "SCHEMA", "SCHEMA_V2", "SUPPORTED_SCHEMAS", "catalog_snapshot", "load_production_catalog"]
