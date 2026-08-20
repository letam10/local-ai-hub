"""Fixed policies shared by model/runtime installation flows."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import re
from urllib.parse import urlparse


INSTALL_JOB_STATES = frozenset({
    "PLANNED", "WAITING_CONFIRMATION", "DOWNLOADING", "VERIFYING", "STAGING",
    "INSTALLING", "FINALIZING", "COMPLETED", "FAILED", "CANCELLED",
})
TRUSTED_HOSTS = frozenset({
    "github.com", "raw.githubusercontent.com", "objects.githubusercontent.com",
    "huggingface.co", "hf.co", "download.pytorch.org", "python.org",
    "www.python.org", "ffmpeg.org", "www.ffmpeg.org",
})
_SAFE_ID = re.compile(r"^[a-z][a-z0-9._-]{1,95}$")
_SECRET_QUERY = re.compile(r"(?:token|secret|password|api[_-]?key|access[_-]?token|authorization)", re.I)


class InstallPlanError(ValueError):
    pass


def validate_component_id(value: object) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise InstallPlanError("invalid_component_id")
    return value


def trusted_source(url: object, *, fixture_mode: bool = False) -> bool:
    """Return whether a source is eligible for the bounded downloader."""

    if not isinstance(url, str) or len(url) > 2048:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.username or parsed.password or _SECRET_QUERY.search(parsed.query or ""):
        return False
    if parsed.scheme == "https" and (parsed.hostname or "").casefold() in TRUSTED_HOSTS:
        return bool(parsed.path and not parsed.fragment)
    if fixture_mode and parsed.scheme == "http" and (parsed.hostname or "").casefold() in {"127.0.0.1", "localhost", "::1"}:
        return bool(parsed.path and not parsed.fragment)
    return False


def source_fingerprint(url: str) -> str:
    parsed = urlparse(url)
    safe = f"{parsed.scheme}://{(parsed.hostname or '').casefold()}{parsed.path}"
    return hashlib.sha256(safe.encode("utf-8")).hexdigest()


def plan_fingerprint(plan: Mapping[str, object]) -> str:
    payload = json.dumps(plan, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def safe_component_projection(record: Mapping[str, object]) -> dict[str, object]:
    """Project catalog metadata without paths, commands or credentials."""

    result: dict[str, object] = {}
    for key in (
        "component_id", "model_id", "runtime_id", "display_name", "component_type",
        "provider", "family", "version", "license", "authentication_required",
        "estimated_download_size", "estimated_disk_size", "minimum_vram", "recommended_vram",
    ):
        if key in record:
            result[key] = record[key]
    result["location_class"] = str(record.get("location_class") or record.get("root_class") or "managed")
    result["source_policy"] = "trusted_catalog" if trusted_source(record.get("official_source"), fixture_mode=False) else "manual_import_or_review"
    return result


__all__ = [
    "INSTALL_JOB_STATES", "InstallPlanError", "TRUSTED_HOSTS", "plan_fingerprint",
    "safe_component_projection", "source_fingerprint", "trusted_source", "validate_component_id",
]
