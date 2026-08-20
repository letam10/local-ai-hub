"""Bounded, server-owned source availability and cache provenance.

Source availability is metadata only.  Normal reads use a small local cache;
only an explicit ``check(..., force=True)`` may call the catalog-owned HTTPS
probe.  A cache record is reusable only when its component and current
server-owned source/catalog binding are identical.  Public projections never
contain URLs, paths, credentials, response bodies, or arbitrary reason text.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from src.platform.paths import HubPaths, get_paths


SOURCE_STATUSES = frozenset({
    "AVAILABLE",
    "DEGRADED",
    "AUTH_REQUIRED",
    "LICENSE_REQUIRED",
    "RATE_LIMITED",
    "UNAVAILABLE",
    "UNKNOWN",
})

_CACHE_SCHEMA = "source-availability-cache.v1"
_CACHE_ROOT_KEYS = frozenset({"schema_version", "records"})
_CACHE_RECORD_KEYS = frozenset({
    "binding_fingerprint",
    "source_identity",
    "status",
    "selected_source",
    "checked_at",
    "expires_at",
    "reason_code",
    "retry_after_seconds",
})
_ID_RE = re.compile(r"^[a-z][a-z0-9._-]{0,95}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DEFAULT_TTL_SECONDS = 24 * 60 * 60
_MAX_TTL_SECONDS = 7 * 24 * 60 * 60
_MAX_PROBE_SECONDS = 8.0
_MAX_CACHE_BYTES = 128 * 1024
_MAX_CACHE_RECORDS = 128
_MAX_SOURCES = 4
_MAX_REASON_LENGTH = 64
_MAX_RETRY_SECONDS = 7 * 24 * 60 * 60

_CACHE_REASON_CODES = frozenset({
    "not_checked",
    "unknown",
    "no_trusted_source",
    "source_reachable",
    "source_authorization_required",
    "source_not_found",
    "source_rate_limited",
    "source_license_required",
    "source_upstream_error",
    "source_status_unknown",
    "catalog_auth_required",
    "catalog_license_required",
    "source_probe_not_supported",
    "source_probe_failed",
    "primary_unavailable_fallback_selected",
})


class SourceAvailabilityError(ValueError):
    """Raised when a catalog-owned source declaration is unsafe."""


def _empty_cache() -> dict[str, Any]:
    return {"schema_version": _CACHE_SCHEMA, "records": {}}


def _safe_id(value: object) -> str:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        raise SourceAvailabilityError("invalid_component_id")
    return value


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate cache key")
        result[key] = value
    return result


def _reject_nonfinite(_value: str) -> None:
    raise ValueError("nonfinite cache number")


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _safe_text(value: object, *, nullable: bool = False, maximum: int = 512) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > maximum:
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    return value


def _text_digest(value: object, *, nullable: bool = False, maximum: int = 512) -> str | None:
    text = _safe_text(value, nullable=nullable, maximum=maximum)
    if text is None:
        return None if nullable and value is None else None
    return _fingerprint(text)


def _safe_int(value: object, *, minimum: int = 0, maximum: int | None = None) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        return None
    if maximum is not None and value > maximum:
        return None
    return value


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
        attributes = int(getattr(info, "st_file_attributes", 0) or 0)
        reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
        return bool(stat.S_ISLNK(info.st_mode) or attributes & reparse_flag)
    except FileNotFoundError:
        return False
    except OSError:
        return True


def _safe_source_url(value: object) -> str | None:
    """Accept only catalog-owned HTTPS URLs without userinfo or controls."""

    if not isinstance(value, str) or len(value) > 2048:
        return None
    value = value.strip()
    if not value or any(ch in value for ch in "\r\n\x00\\"):
        return None
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        _ = parsed.port
    except (ValueError, UnicodeError):
        return None
    if parsed.scheme != "https" or not hostname or parsed.username is not None or parsed.password is not None:
        return None
    return value


def _safe_flag(raw: Mapping[str, Any], *names: str) -> bool | None:
    value: object = False
    found = False
    for name in names:
        if name in raw:
            value = raw[name]
            found = True
            break
    if not found:
        return False
    return value if isinstance(value, bool) else None


def _source_entry(raw: object, *, fallback: bool = False, default_identity: str = "unknown") -> dict[str, Any] | None:
    """Normalize one source internally; the returned URL never leaves this module."""

    if isinstance(raw, str):
        if raw.casefold() in {"local", "managed", "internal"}:
            raw = {"kind": "local_reference", "canonical_identity": default_identity}
        else:
            raw = {"url": raw}
    if not isinstance(raw, Mapping):
        return None

    url_value = raw.get("url")
    if url_value is None:
        url_value = raw.get("https_url")
    if url_value is None and "official_source" in raw:
        url_value = raw.get("official_source")
    url = _safe_source_url(url_value) if url_value is not None else None
    if url_value is not None and url is None:
        return None

    identity_value = raw.get("canonical_identity") or raw.get("artifact_identity") or raw.get("revision") or raw.get("release") or default_identity
    identity = _safe_text(identity_value, maximum=512)
    if identity is None:
        return None
    provider = _safe_text(raw.get("provider", "catalog"), maximum=96)
    source_kind = _safe_text(raw.get("kind", "https"), maximum=48)
    if provider is None or source_kind is None:
        return None
    if url is None and source_kind not in {"internal_resolver", "local_reference"}:
        return None
    authentication_required = _safe_flag(raw, "authentication_required", "auth_required")
    license_required = _safe_flag(raw, "license_required")
    priority = _safe_int(raw.get("priority", 100), minimum=0, maximum=1000)
    if authentication_required is None or license_required is None or priority is None:
        return None
    return {
        "provider": provider,
        "kind": source_kind,
        "identity": identity,
        "identity_digest": _fingerprint(identity),
        "fingerprint": _fingerprint(identity),
        "url_digest": _fingerprint(url) if url is not None else None,
        "url": url,
        "fallback": bool(fallback),
        "authentication_required": authentication_required,
        "license_required": license_required,
        "priority": priority,
    }


def _normalized_sources(record: Mapping[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    if not isinstance(record, Mapping):
        return [], False
    default_identity = record.get("source_identity") or record.get("revision") or "unknown"
    if not isinstance(default_identity, str) or not default_identity or len(default_identity) > 512:
        return [], False
    primary = record.get("primary_source")
    if primary is None and "official_source" in record:
        primary = record.get("official_source")
    sources: list[dict[str, Any]] = []
    if primary is not None:
        first = _source_entry(primary, default_identity=default_identity)
        if first is None:
            return [], False
        sources.append(first)
    fallbacks = record.get("trusted_fallback_sources", [])
    if not isinstance(fallbacks, Sequence) or isinstance(fallbacks, (str, bytes, bytearray)):
        return [], False
    if len(fallbacks) > _MAX_SOURCES - 1:
        return [], False
    for raw in fallbacks:
        item = _source_entry(raw, fallback=True, default_identity=default_identity)
        if item is None:
            return [], False
        if not sources or item["identity"] == sources[0]["identity"]:
            sources.append(item)
    if sources:
        identity = sources[0]["identity"]
        sources = [item for item in sources if item["identity"] == identity]
    return sorted(sources[:_MAX_SOURCES], key=lambda item: (item["priority"], item["fallback"])), True


def _sources_for_record(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    sources, valid = _normalized_sources(record)
    return sources if valid else []


_BINDING_KEYS = frozenset({
    "catalog_schema", "catalog_version", "catalog_revision", "catalog_fingerprint",
    "source_identity", "component_id", "component_type", "runtime_id",
    "record_revision", "install_strategy",
})


def _normalize_binding(value: object) -> dict[str, Any] | None:
    if value is None:
        return {"mode": "record_only"}
    if not isinstance(value, Mapping) or not set(value).issubset(_BINDING_KEYS):
        return None
    normalized: dict[str, Any] = {"mode": "catalog_bound"}
    schema = _safe_text(value.get("catalog_schema"), maximum=96)
    if schema is None:
        return None
    normalized["catalog_schema"] = schema
    for key in ("catalog_version", "catalog_revision"):
        raw = value.get(key)
        if raw is not None and _safe_text(raw, maximum=128) is None:
            return None
        normalized[key] = _text_digest(raw, nullable=True, maximum=128)
    fingerprint = value.get("catalog_fingerprint")
    if not isinstance(fingerprint, str) or _SHA256_RE.fullmatch(fingerprint.casefold()) is None:
        return None
    normalized["catalog_fingerprint"] = fingerprint.casefold()
    source_identity = value.get("source_identity")
    if source_identity is not None and _safe_text(source_identity, maximum=512) is None:
        return None
    normalized["source_identity"] = _text_digest(source_identity, nullable=True, maximum=512)
    for key in ("component_id", "runtime_id"):
        if key in value:
            try:
                normalized[key] = _safe_id(value[key])
            except SourceAvailabilityError:
                return None
    component_type = value.get("component_type")
    if component_type is not None:
        if component_type not in {"model", "runtime"}:
            return None
        normalized["component_type"] = component_type
    for key in ("record_revision", "install_strategy"):
        raw = value.get(key)
        if raw is not None and _safe_text(raw, maximum=128) is None:
            return None
        normalized[key] = _text_digest(raw, nullable=True, maximum=128)
    return normalized


def source_binding_fingerprint(record: Mapping[str, Any], *, binding: Mapping[str, Any] | None = None) -> str | None:
    """Compute a server-owned digest for all source/catalog reuse inputs."""

    if not isinstance(record, Mapping):
        return None
    sources, valid_sources = _normalized_sources(record)
    if not valid_sources:
        return None
    normalized_binding = _normalize_binding(binding)
    if normalized_binding is None:
        return None
    component_id: str | None = None
    for key in ("component_id", "model_id", "runtime_id"):
        if key not in record or record[key] is None:
            continue
        try:
            candidate = _safe_id(record[key])
        except SourceAvailabilityError:
            return None
        # A model's runtime_id is a related component, not a second identity
        # for the source record.  Prefer an explicit component/model ID.
        if component_id is None:
            component_id = candidate
        elif key == "component_id" or (key == "model_id" and "component_id" not in record):
            if candidate != component_id:
                return None
        elif key == "runtime_id" and "model_id" not in record and candidate != component_id:
            return None
    source_identity = record.get("source_identity")
    if source_identity is not None and _safe_text(source_identity, maximum=512) is None:
        return None
    revision = record.get("revision")
    if revision is not None and _safe_text(revision, maximum=128) is None:
        return None
    install_strategy = record.get("install_strategy")
    if install_strategy is not None and _safe_text(install_strategy, maximum=128) is None:
        return None
    provider = record.get("provider")
    kind = record.get("kind")
    if provider is not None and _safe_text(provider, maximum=96) is None:
        return None
    if kind is not None and _safe_text(kind, maximum=48) is None:
        return None
    descriptor = {
        "component_id": component_id,
        "provider": _text_digest(provider, nullable=True, maximum=96),
        "kind": _text_digest(kind, nullable=True, maximum=48),
        "source_identity": _text_digest(source_identity, nullable=True, maximum=512),
        "revision": _text_digest(revision, nullable=True, maximum=128),
        "install_strategy": _text_digest(install_strategy, nullable=True, maximum=128),
        "sources": [
            {
                "provider": item["provider"],
                "kind": item["kind"],
                "identity": item["identity_digest"],
                "url": item["url_digest"],
                "fallback": item["fallback"],
                "authentication_required": item["authentication_required"],
                "license_required": item["license_required"],
                "priority": item["priority"],
            }
            for item in sources
        ],
        "catalog_binding": normalized_binding,
    }
    return _fingerprint(descriptor)


def _unknown_projection() -> dict[str, Any]:
    return {
        "status": "UNKNOWN", "source_identity": None, "selected_source": None,
        "checked_at": None, "expires_at": None, "reason_code": "not_checked",
        "retry_after_seconds": None,
    }


def _cache_record(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping) or not set(value).issubset(_CACHE_RECORD_KEYS):
        return None
    required = _CACHE_RECORD_KEYS - {"retry_after_seconds"}
    if not required.issubset(set(value)):
        return None
    binding = value.get("binding_fingerprint")
    if not isinstance(binding, str) or _SHA256_RE.fullmatch(binding.casefold()) is None:
        return None
    source_identity = value.get("source_identity")
    if source_identity is not None and (not isinstance(source_identity, str) or _SHA256_RE.fullmatch(source_identity.casefold()) is None):
        return None
    status = value.get("status")
    selected_source = value.get("selected_source")
    reason = value.get("reason_code")
    checked_at = _safe_int(value.get("checked_at"), maximum=2**63 - 1)
    expires_at = _safe_int(value.get("expires_at"), maximum=2**63 - 1)
    if not isinstance(status, str) or status not in SOURCE_STATUSES:
        return None
    if selected_source is not None and (not isinstance(selected_source, str) or selected_source not in {"primary", "fallback"}):
        return None
    if not isinstance(reason, str) or reason not in _CACHE_REASON_CODES or len(reason) > _MAX_REASON_LENGTH:
        return None
    if checked_at is None or expires_at is None or expires_at < checked_at or expires_at - checked_at > _MAX_TTL_SECONDS:
        return None
    retry = value.get("retry_after_seconds")
    if retry is not None:
        retry = _safe_int(retry, maximum=_MAX_RETRY_SECONDS)
        if retry is None:
            return None
    return {
        "binding_fingerprint": binding.casefold(),
        "source_identity": source_identity.casefold() if isinstance(source_identity, str) else None,
        "status": status,
        "selected_source": selected_source,
        "checked_at": checked_at,
        "expires_at": expires_at,
        "reason_code": reason,
        "retry_after_seconds": retry,
    }


def source_status_projection(value: Mapping[str, Any] | None, *, binding_fingerprint: str | None = None, allow_public: bool = False) -> dict[str, Any]:
    """Return a finite path/URL-free projection, rejecting stale bindings."""

    # A result returned by this service is intentionally a public projection
    # and therefore omits its private cache binding.  Accept that already
    # sanitized shape for composition, but require a binding whenever the
    # caller is validating persisted/cache state.
    if isinstance(value, Mapping) and "binding_fingerprint" not in value and binding_fingerprint is None and allow_public:
        status = value.get("status")
        selected = value.get("selected_source")
        reason = value.get("reason_code")
        source_identity = value.get("source_identity")
        checked_at = value.get("checked_at")
        expires_at = value.get("expires_at")
        retry = value.get("retry_after_seconds")
        if not isinstance(status, str) or status not in SOURCE_STATUSES:
            return _unknown_projection()
        if selected is not None and (not isinstance(selected, str) or selected not in {"primary", "fallback"}):
            return _unknown_projection()
        if source_identity is not None and (not isinstance(source_identity, str) or _SHA256_RE.fullmatch(source_identity.casefold()) is None):
            return _unknown_projection()
        if checked_at is not None and _safe_int(checked_at, maximum=2**63 - 1) is None:
            return _unknown_projection()
        if expires_at is not None and _safe_int(expires_at, maximum=2**63 - 1) is None:
            return _unknown_projection()
        if checked_at is not None and expires_at is not None and expires_at < checked_at:
            return _unknown_projection()
        if not isinstance(reason, str) or reason not in _CACHE_REASON_CODES:
            return _unknown_projection()
        if retry is not None and _safe_int(retry, maximum=_MAX_RETRY_SECONDS) is None:
            return _unknown_projection()
        return {
            "status": status,
            "source_identity": source_identity.casefold() if isinstance(source_identity, str) else None,
            "selected_source": selected,
            "checked_at": checked_at,
            "expires_at": expires_at,
            "reason_code": reason,
            "retry_after_seconds": retry,
        }
    if isinstance(value, Mapping) and "binding_fingerprint" not in value:
        return _unknown_projection()
    record = _cache_record(value)
    if record is None:
        return _unknown_projection()
    if binding_fingerprint is not None:
        if not isinstance(binding_fingerprint, str) or _SHA256_RE.fullmatch(binding_fingerprint.casefold()) is None or record["binding_fingerprint"] != binding_fingerprint.casefold():
            return _unknown_projection()
    return {key: record[key] for key in ("status", "source_identity", "selected_source", "checked_at", "expires_at", "reason_code", "retry_after_seconds")}


def _canonical_reason(status: str, reason: object) -> str:
    if isinstance(reason, str) and reason in _CACHE_REASON_CODES:
        return reason
    return {
        "AVAILABLE": "source_reachable", "DEGRADED": "source_upstream_error",
        "AUTH_REQUIRED": "source_authorization_required", "LICENSE_REQUIRED": "source_license_required",
        "RATE_LIMITED": "source_rate_limited", "UNAVAILABLE": "source_not_found",
    }.get(status, "source_status_unknown")


def _safe_probe_result(value: object) -> tuple[str, str, int | None]:
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        return "UNKNOWN", "source_probe_failed", None
    status = value[0] if isinstance(value[0], str) and value[0] in SOURCE_STATUSES else "UNKNOWN"
    reason = _canonical_reason(status, value[1])
    retry = value[2]
    retry = _safe_int(retry, maximum=_MAX_RETRY_SECONDS) if retry is not None else None
    return status, reason, retry


class SourceAvailabilityService:
    """Cached source-status reader/checker with explicit network authority."""

    def __init__(self, *, paths: HubPaths | None = None, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> None:
        self.paths = paths or get_paths()
        self.ttl_seconds = max(60, min(int(ttl_seconds), _MAX_TTL_SECONDS))
        self.cache_path = self.paths.config_root / "source_availability_cache.json"
        self._cache_lock = threading.RLock()
        self._known_bindings: dict[str, str] = {}

    def _read_cache(self) -> dict[str, Any]:
        try:
            if _is_reparse(self.cache_path) or self.cache_path.stat().st_size > _MAX_CACHE_BYTES:
                return _empty_cache()
            value = json.loads(self.cache_path.read_text(encoding="utf-8"), object_pairs_hook=_strict_pairs, parse_constant=_reject_nonfinite)
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
            return _empty_cache()
        if not isinstance(value, Mapping) or set(value) != _CACHE_ROOT_KEYS or value.get("schema_version") != _CACHE_SCHEMA:
            return _empty_cache()
        records = value.get("records")
        if not isinstance(records, Mapping) or len(records) > _MAX_CACHE_RECORDS:
            return _empty_cache()
        normalized: dict[str, Any] = {}
        for component_id, record in records.items():
            try:
                safe_id = _safe_id(component_id)
            except SourceAvailabilityError:
                return _empty_cache()
            normalized_record = _cache_record(record)
            if normalized_record is None:
                return _empty_cache()
            normalized[safe_id] = normalized_record
        return {"schema_version": _CACHE_SCHEMA, "records": normalized}

    def _write_cache(self, value: Mapping[str, Any]) -> None:
        records = value.get("records") if isinstance(value, Mapping) else None
        bounded: dict[str, Any] = {}
        if isinstance(records, Mapping):
            for component_id in sorted(records)[:_MAX_CACHE_RECORDS]:
                try:
                    safe_id = _safe_id(component_id)
                except SourceAvailabilityError:
                    continue
                record = _cache_record(records[component_id])
                if record is not None:
                    bounded[safe_id] = record
        payload = {"schema_version": _CACHE_SCHEMA, "records": bounded}
        self.paths.config_root.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            handle, name = tempfile.mkstemp(prefix=".source-status-", suffix=".tmp", dir=self.paths.config_root)
            os.close(handle)
            temporary = Path(name)
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.cache_path)
            temporary = None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink()
                except OSError:
                    pass

    @staticmethod
    def _classify_response(status_code: int) -> tuple[str, str, int | None]:
        if 200 <= status_code < 400:
            return "AVAILABLE", "source_reachable", None
        if status_code in {401, 403}:
            return "AUTH_REQUIRED", "source_authorization_required", None
        if status_code == 404:
            return "UNAVAILABLE", "source_not_found", None
        if status_code in {408, 429}:
            return "RATE_LIMITED", "source_rate_limited", 3600
        if status_code == 451:
            return "LICENSE_REQUIRED", "source_license_required", None
        if 500 <= status_code < 600:
            return "DEGRADED", "source_upstream_error", 900
        return "UNKNOWN", "source_status_unknown", 900

    @staticmethod
    def _probe(entry: Mapping[str, Any], *, timeout: float = _MAX_PROBE_SECONDS) -> tuple[str, str, int | None]:
        if entry.get("authentication_required"):
            return "AUTH_REQUIRED", "catalog_auth_required", None
        if entry.get("license_required"):
            return "LICENSE_REQUIRED", "catalog_license_required", None
        url = entry.get("url")
        if not isinstance(url, str):
            return "UNKNOWN", "source_probe_not_supported", None
        request = Request(url, headers={"Accept": "application/json,application/octet-stream", "User-Agent": "LocalAIHub-UpdateCheck/1"}, method="HEAD")
        try:
            with urlopen(request, timeout=timeout) as response:  # nosec B310 - URL is catalog-owned and HTTPS validated
                return SourceAvailabilityService._classify_response(int(response.status or 200))
        except HTTPError as exc:
            return SourceAvailabilityService._classify_response(int(exc.code))
        except (URLError, TimeoutError, OSError):
            return "UNKNOWN", "source_probe_failed", 900

    @staticmethod
    def _record_component_matches(component_id: str, record: Mapping[str, Any]) -> bool:
        for key in ("component_id", "model_id", "runtime_id"):
            if key in record:
                if record[key] is None:
                    continue
                return isinstance(record[key], str) and record[key] == component_id
        return True

    def _current_binding(self, component_id: str, record: Mapping[str, Any] | None, binding: Mapping[str, Any] | None) -> str | None:
        if record is None:
            return self._known_bindings.get(component_id)
        if binding is not None and not self._record_component_matches(component_id, record):
            return None
        return source_binding_fingerprint(record, binding=binding)

    def cached(self, component_id: str, record: Mapping[str, Any] | None = None, *, binding: Mapping[str, Any] | None = None) -> dict[str, Any]:
        component_id = _safe_id(component_id)
        expected = self._current_binding(component_id, record, binding)
        if expected is None:
            return _unknown_projection()
        with self._cache_lock:
            cache = self._read_cache()
        return source_status_projection(cache["records"].get(component_id), binding_fingerprint=expected)

    def check(
        self,
        component_id: str,
        record: Mapping[str, Any],
        *,
        force: bool = False,
        now: int | None = None,
        probe: Callable[[Mapping[str, Any]], tuple[str, str, int | None]] | None = None,
        binding: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Check a catalog-owned source; network probing requires ``force``."""

        component_id = _safe_id(component_id)
        if not isinstance(record, Mapping) or (binding is not None and not self._record_component_matches(component_id, record)):
            return _unknown_projection()
        expected = self._current_binding(component_id, record, binding)
        if expected is None:
            return _unknown_projection()
        self._known_bindings[component_id] = expected
        current = int(time.time()) if now is None else int(now)
        with self._cache_lock:
            cache = self._read_cache()
        cached = cache["records"].get(component_id)
        if not force and isinstance(cached, Mapping):
            if cached.get("binding_fingerprint") != expected:
                return _unknown_projection()
            expires_at = cached.get("expires_at")
            if isinstance(expires_at, int) and expires_at > current:
                return source_status_projection(cached, binding_fingerprint=expected)

        sources, valid_sources = _normalized_sources(record)
        if not valid_sources:
            return _unknown_projection()
        if not sources:
            result = {"status": "UNKNOWN", "reason_code": "no_trusted_source", "source_identity": None, "selected_source": None, "retry_after_seconds": None}
        else:
            selected: dict[str, Any] | None = None
            first_status: tuple[str, str, int | None] | None = None
            for entry in sources:
                value = (probe(entry) if probe is not None else self._probe(entry)) if force else ("UNKNOWN", "not_checked", None)
                status, reason, retry = _safe_probe_result(value)
                if first_status is None:
                    first_status = (status, reason, retry)
                if force and status == "AVAILABLE":
                    selected = entry
                    break
            if selected is not None:
                status, reason, retry = ("DEGRADED", "primary_unavailable_fallback_selected", None) if selected.get("fallback") else ("AVAILABLE", "source_reachable", None)
                result = {"status": status, "reason_code": reason, "source_identity": selected["identity_digest"], "selected_source": "fallback" if selected.get("fallback") else "primary", "retry_after_seconds": retry}
            else:
                status, reason, retry = first_status or ("UNKNOWN", "source_not_checked", None)
                result = {"status": status, "reason_code": reason, "source_identity": sources[0]["identity_digest"], "selected_source": None, "retry_after_seconds": retry}

        ttl = int(result.get("retry_after_seconds") or self.ttl_seconds)
        result.update({"binding_fingerprint": expected, "checked_at": current, "expires_at": current + max(60, min(ttl, _MAX_TTL_SECONDS))})
        with self._cache_lock:
            latest = self._read_cache()
            latest["records"][component_id] = result
            self._write_cache(latest)
        return source_status_projection(result, binding_fingerprint=expected)

    def check_all(
        self,
        records: Mapping[str, Mapping[str, Any]],
        *,
        force: bool = False,
        now: int | None = None,
        bindings: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Check a bounded mapping with current bindings for each component."""

        from concurrent.futures import ThreadPoolExecutor, as_completed

        if not isinstance(records, Mapping):
            return {"schema_version": "source-availability-check.v1", "status": "completed", "execution": "completed" if force else "not_run", "dry_run": not force, "records": {}, "checked_count": 0, "network_action": bool(force)}
        selected = sorted(list(records)[:32])
        output: dict[str, Any] = {}

        def run(component_id: str) -> tuple[str, dict[str, Any]]:
            safe = _safe_id(component_id)
            if not isinstance(bindings, Mapping) or safe not in bindings:
                return safe, _unknown_projection()
            current_binding = bindings.get(safe)
            return safe, self.check(safe, records[component_id], force=force, now=now, binding=current_binding)

        with ThreadPoolExecutor(max_workers=min(4, max(1, len(selected)))) as pool:
            futures = [pool.submit(run, component_id) for component_id in selected]
            for future in as_completed(futures):
                try:
                    component_id, value = future.result()
                except (SourceAvailabilityError, TypeError, ValueError):
                    continue
                output[component_id] = value
        output = {key: output[key] for key in sorted(output)}
        return {"schema_version": "source-availability-check.v1", "status": "completed", "execution": "completed" if force else "not_run", "dry_run": not force, "records": output, "checked_count": len(output), "network_action": bool(force)}


__all__ = [
    "SOURCE_STATUSES",
    "SourceAvailabilityError",
    "SourceAvailabilityService",
    "source_binding_fingerprint",
    "source_status_projection",
]
