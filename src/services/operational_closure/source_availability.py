"""Bounded, cached upstream source availability for V7 components.

Source availability is intentionally independent from local installation
state.  A component can remain operational while its upstream becomes
unavailable.  Normal reads use the local cache; only an explicit ``check``
with ``force=True`` performs a bounded request to a catalog-owned source.

The public projection contains only finite statuses, source fingerprints and
safe reason codes.  URLs, paths, credentials and response bodies never leave
this service.
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

_ID_RE = re.compile(r"^[a-z][a-z0-9._-]{0,95}$")
_DEFAULT_TTL_SECONDS = 24 * 60 * 60
_MAX_TTL_SECONDS = 7 * 24 * 60 * 60
_MAX_SOURCES = 4
_MAX_PROBE_SECONDS = 8.0


class SourceAvailabilityError(ValueError):
    """Raised when a catalog-owned source declaration is unsafe."""


def _safe_id(value: object) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise SourceAvailabilityError("invalid_component_id")
    return value


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _is_reparse(path: Path) -> bool:
    try:
        return bool(stat.S_ISLNK(path.lstat().st_mode))
    except FileNotFoundError:
        return False
    except OSError:
        return True


def _safe_source_url(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 2048:
        return None
    value = value.strip()
    if not value.startswith("https://") or any(ch in value for ch in "\r\n\x00"):
        return None
    return value


def _source_entry(raw: object, *, fallback: bool = False) -> dict[str, Any] | None:
    """Normalize one catalog source without exposing its URL."""

    if isinstance(raw, str):
        raw = {"url": raw}
    if not isinstance(raw, Mapping):
        return None
    url = _safe_source_url(raw.get("url") or raw.get("https_url") or raw.get("official_source"))
    identity = raw.get("canonical_identity") or raw.get("artifact_identity") or raw.get("revision") or raw.get("release")
    if not isinstance(identity, str) or not identity or len(identity) > 512:
        return None
    provider = raw.get("provider")
    provider = str(provider)[:96] if isinstance(provider, str) else "catalog"
    source_kind = raw.get("kind")
    source_kind = str(source_kind)[:48] if isinstance(source_kind, str) else "https"
    if url is None and source_kind not in {"internal_resolver", "local_reference"}:
        return None
    entry = {
        "provider": provider,
        "kind": source_kind,
        "identity": identity,
        "fingerprint": _fingerprint({"provider": provider, "kind": source_kind, "identity": identity}),
        "url": url,
        "fallback": bool(fallback),
        "authentication_required": bool(raw.get("authentication_required", raw.get("auth_required", False))),
        "license_required": bool(raw.get("license_required", False)),
        "priority": int(raw.get("priority", 100)) if isinstance(raw.get("priority", 100), int) else 100,
    }
    return entry


def _sources_for_record(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    primary = record.get("primary_source")
    if primary is None:
        primary = record.get("official_source")
    if isinstance(primary, str):
        primary = {
            "url": primary,
            "canonical_identity": record.get("source_identity") or record.get("revision") or "unknown",
        }
    elif isinstance(primary, Mapping) and not (primary.get("canonical_identity") or primary.get("artifact_identity") or primary.get("revision") or primary.get("release")):
        primary = {**primary, "canonical_identity": record.get("source_identity") or record.get("revision") or "unknown"}
    sources: list[dict[str, Any]] = []
    first = _source_entry(primary)
    if first is not None:
        sources.append(first)
    fallbacks = record.get("trusted_fallback_sources", [])
    if isinstance(fallbacks, Sequence) and not isinstance(fallbacks, (str, bytes)):
        for raw in list(fallbacks)[: _MAX_SOURCES - 1]:
            if isinstance(raw, str):
                raw = {"url": raw, "canonical_identity": record.get("source_identity") or record.get("revision") or "unknown"}
            elif isinstance(raw, Mapping) and not (raw.get("canonical_identity") or raw.get("artifact_identity") or raw.get("revision") or raw.get("release")):
                raw = {**raw, "canonical_identity": record.get("source_identity") or record.get("revision") or "unknown"}
            item = _source_entry(raw, fallback=True)
            if item is not None and (not sources or item["identity"] == sources[0]["identity"]):
                sources.append(item)
    # Keep one source identity/version only.  A fallback that silently points at
    # a different artifact is never eligible.
    if sources:
        identity = sources[0]["identity"]
        sources = [item for item in sources if item["identity"] == identity]
    return sorted(sources[:_MAX_SOURCES], key=lambda item: (item["priority"], item["fallback"]))


def source_status_projection(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a path/URL-free source status projection."""

    if not isinstance(value, Mapping):
        return {
            "status": "UNKNOWN",
            "source_identity": None,
            "selected_source": None,
            "checked_at": None,
            "expires_at": None,
            "reason_code": "not_checked",
            "retry_after_seconds": None,
        }
    status = value.get("status") if value.get("status") in SOURCE_STATUSES else "UNKNOWN"
    return {
        "status": status,
        "source_identity": value.get("source_identity") if isinstance(value.get("source_identity"), str) else None,
        "selected_source": value.get("selected_source") if value.get("selected_source") in {"primary", "fallback", None} else None,
        "checked_at": value.get("checked_at") if isinstance(value.get("checked_at"), int) else None,
        "expires_at": value.get("expires_at") if isinstance(value.get("expires_at"), int) else None,
        "reason_code": value.get("reason_code") if isinstance(value.get("reason_code"), str) else "unknown",
        "retry_after_seconds": value.get("retry_after_seconds") if isinstance(value.get("retry_after_seconds"), int) else None,
    }


class SourceAvailabilityService:
    """Cached source-status reader/checker with explicit network authority."""

    def __init__(self, *, paths: HubPaths | None = None, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> None:
        self.paths = paths or get_paths()
        self.ttl_seconds = max(60, min(int(ttl_seconds), _MAX_TTL_SECONDS))
        self.cache_path = self.paths.config_root / "source_availability_cache.json"
        self._cache_lock = threading.RLock()

    def _read_cache(self) -> dict[str, Any]:
        try:
            value = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {"schema_version": "source-availability-cache.v1", "records": {}}
        records = value.get("records") if isinstance(value, Mapping) else None
        return {"schema_version": "source-availability-cache.v1", "records": dict(records) if isinstance(records, Mapping) else {}}

    def _write_cache(self, value: Mapping[str, Any]) -> None:
        self.paths.config_root.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            handle, name = tempfile.mkstemp(prefix=".source-status-", suffix=".tmp", dir=self.paths.config_root)
            os.close(handle)
            temporary = Path(name)
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                json.dump(value, stream, ensure_ascii=True, sort_keys=True, indent=2)
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
        if status_code == 408 or status_code == 429:
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

    def cached(self, component_id: str) -> dict[str, Any]:
        component_id = _safe_id(component_id)
        return source_status_projection(self._read_cache()["records"].get(component_id))

    def check(
        self,
        component_id: str,
        record: Mapping[str, Any],
        *,
        force: bool = False,
        now: int | None = None,
        probe: Callable[[Mapping[str, Any]], tuple[str, str, int | None]] | None = None,
    ) -> dict[str, Any]:
        """Check a catalog-owned source; network probing requires ``force``."""

        component_id = _safe_id(component_id)
        current = int(time.time()) if now is None else int(now)
        with self._cache_lock:
            cache = self._read_cache()
        cached = cache["records"].get(component_id)
        if not force and isinstance(cached, Mapping) and int(cached.get("expires_at", 0) or 0) > current:
            return source_status_projection(cached)

        sources = _sources_for_record(record)
        if not sources:
            result = {"status": "UNKNOWN", "reason_code": "no_trusted_source", "source_identity": None, "selected_source": None}
        else:
            selected: dict[str, Any] | None = None
            first_status: tuple[str, str, int | None] | None = None
            for entry in sources:
                status, reason, retry = (probe(entry) if probe is not None else self._probe(entry)) if force else ("UNKNOWN", "not_checked", None)
                if first_status is None:
                    first_status = (status, reason, retry)
                if status == "AVAILABLE":
                    selected = entry
                    break
            if selected is not None:
                status, reason, retry = ("DEGRADED", "primary_unavailable_fallback_selected", None) if selected.get("fallback") else ("AVAILABLE", "source_reachable", None)
                result = {"status": status, "reason_code": reason, "source_identity": selected["fingerprint"], "selected_source": "fallback" if selected.get("fallback") else "primary", "retry_after_seconds": retry}
            else:
                status, reason, retry = first_status or ("UNKNOWN", "source_not_checked", None)
                result = {"status": status, "reason_code": reason, "source_identity": sources[0]["fingerprint"], "selected_source": None, "retry_after_seconds": retry}

        ttl = int(result.get("retry_after_seconds") or self.ttl_seconds)
        result.update({"checked_at": current, "expires_at": current + max(60, min(ttl, _MAX_TTL_SECONDS))})
        with self._cache_lock:
            # Re-read before publishing so bounded concurrent Check All
            # workers do not discard another component's record.
            latest = self._read_cache()
            latest["records"][component_id] = {key: value for key, value in result.items() if key != "retry_after_seconds" or value is not None}
            self._write_cache(latest)
        return source_status_projection(result)

    def check_all(self, records: Mapping[str, Mapping[str, Any]], *, force: bool = False, now: int | None = None) -> dict[str, Any]:
        """Check a bounded catalog mapping sequentially; never starts a daemon."""

        from concurrent.futures import ThreadPoolExecutor, as_completed

        output: dict[str, Any] = {}
        selected = sorted(list(records)[:32])

        def run(component_id: str) -> tuple[str, dict[str, Any]]:
            safe = _safe_id(component_id)
            return safe, self.check(safe, records[component_id], force=force, now=now)

        with ThreadPoolExecutor(max_workers=min(4, max(1, len(selected)))) as pool:
            futures = [pool.submit(run, component_id) for component_id in selected]
            for future in as_completed(futures):
                try:
                    component_id, value = future.result()
                except SourceAvailabilityError:
                    continue
                output[component_id] = value
        output = {key: output[key] for key in sorted(output)}
        return {"schema_version": "source-availability-check.v1", "status": "completed", "execution": "completed" if force else "not_run", "dry_run": not force, "records": output, "checked_count": len(output), "network_action": bool(force)}


__all__ = ["SOURCE_STATUSES", "SourceAvailabilityError", "SourceAvailabilityService", "source_status_projection"]
