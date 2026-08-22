"""V8 Wave 3 trusted-source acceptance control plane.

This service is deliberately read-only. It classifies the tracked production
catalog using finite, path-free states and never probes a provider, downloads a
component, accepts a license, or upgrades catalog disposition on its own.
"""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from src.services.productization.catalog import ProductionCatalog


SCHEMA_VERSION = "v8-component-source-acceptance.v1"
_STATES = frozenset({
    "AUTO_INSTALL_READY",
    "AUTH_REQUIRED",
    "INTEGRITY_INCOMPLETE",
    "LICENSE_REVIEW_REQUIRED",
    "MANUAL_REVIEW_REQUIRED",
    "REFERENCE_EXISTING",
    "SIZE_UNKNOWN",
    "SOURCE_UNAVAILABLE",
    "SOURCE_UNVERIFIED",
    "UNSUPPORTED_SOURCE",
})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ComponentEnablementError(ValueError):
    pass


def _model_integrity(record: Mapping[str, Any]) -> bool:
    files = record.get("files")
    if not isinstance(files, list) or not files:
        return False
    for item in files:
        if not isinstance(item, Mapping) or item.get("verification") != "verified":
            return False
        size = item.get("size_bytes")
        digest = item.get("sha256")
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            return False
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            return False
    return True


def _runtime_integrity(record: Mapping[str, Any]) -> bool:
    value = record.get("integrity")
    if not isinstance(value, Mapping) or value.get("verification") != "verified":
        return False
    size = value.get("size_bytes")
    digest = value.get("sha256")
    return (
        not isinstance(size, bool)
        and isinstance(size, int)
        and size > 0
        and isinstance(digest, str)
        and _SHA256.fullmatch(digest) is not None
    )


class ComponentEnablementService:
    """Classify whether one tracked component is eligible for auto-install."""

    def __init__(self, *, catalog: Any | None = None) -> None:
        self.catalog = catalog or ProductionCatalog()

    def _record(self, component_id: str) -> tuple[str, Mapping[str, Any]]:
        if not isinstance(component_id, str) or not component_id:
            raise ComponentEnablementError("invalid_component_id")
        models = getattr(self.catalog, "models", {})
        runtimes = getattr(self.catalog, "runtimes", {})
        if isinstance(models, Mapping) and component_id in models and isinstance(models[component_id], Mapping):
            return "model", models[component_id]
        if isinstance(runtimes, Mapping) and component_id in runtimes and isinstance(runtimes[component_id], Mapping):
            return "runtime", runtimes[component_id]
        raise ComponentEnablementError("unknown_component")

    @staticmethod
    def _source_verified(record: Mapping[str, Any]) -> bool:
        primary = record.get("primary_source")
        if not isinstance(primary, Mapping):
            return False
        url = primary.get("url")
        return (
            record.get("source_verification") == "verified"
            and primary.get("verification_state") == "verified"
            and isinstance(url, str)
            and url.startswith("https://")
            and not primary.get("authentication_required")
        )

    @staticmethod
    def _auth_ready(record: Mapping[str, Any]) -> bool:
        value = record.get("authentication")
        return isinstance(value, Mapping) and value.get("required") is False and value.get("state") == "not_required"

    @staticmethod
    def _license_ready(record: Mapping[str, Any]) -> bool:
        value = record.get("license")
        return isinstance(value, Mapping) and value.get("state") == "apache-2.0"

    @staticmethod
    def _size_ready(record: Mapping[str, Any]) -> bool:
        download = record.get("estimated_download_size")
        disk = record.get("estimated_disk_size")
        return (
            not isinstance(download, bool)
            and isinstance(download, int)
            and download > 0
            and not isinstance(disk, bool)
            and isinstance(disk, int)
            and disk > 0
        )

    def assess(self, component_id: str) -> dict[str, Any]:
        component_type, record = self._record(component_id)
        disposition = str(record.get("disposition") or "UNSUPPORTED_SOURCE")
        source_present = isinstance(record.get("primary_source"), Mapping)
        source_verified = self._source_verified(record)
        auth_ready = self._auth_ready(record)
        license_ready = self._license_ready(record)
        integrity_ready = _model_integrity(record) if component_type == "model" else _runtime_integrity(record)
        size_ready = self._size_ready(record)
        catalog_auto = disposition == "AUTO_INSTALL_READY"
        eligible = bool(catalog_auto and source_verified and auth_ready and license_ready and integrity_ready and size_ready)

        if disposition in {"UNSUPPORTED_SOURCE", "UNSUPPORTED"}:
            state = "UNSUPPORTED_SOURCE"
        elif not auth_ready or disposition == "AUTH_REQUIRED":
            state = "AUTH_REQUIRED"
        elif not license_ready:
            state = "LICENSE_REVIEW_REQUIRED"
        elif not source_present:
            state = "SOURCE_UNAVAILABLE"
        elif not source_verified:
            state = "SOURCE_UNVERIFIED"
        elif not integrity_ready:
            state = "INTEGRITY_INCOMPLETE"
        elif not size_ready:
            state = "SIZE_UNKNOWN"
        elif eligible:
            state = "AUTO_INSTALL_READY"
        elif disposition == "REFERENCE_EXISTING":
            state = "REFERENCE_EXISTING"
        else:
            state = "MANUAL_REVIEW_REQUIRED"
        if state not in _STATES:  # pragma: no cover - finite-state guard
            raise ComponentEnablementError("invalid_acceptance_state")

        return {
            "schema_version": SCHEMA_VERSION,
            "status": "completed",
            "execution": "not_run",
            "dry_run": True,
            "component_id": component_id,
            "component_type": component_type,
            "disposition": disposition,
            "install_strategy": str(record.get("install_strategy") or "unknown"),
            "acceptance_state": state,
            "auto_install_eligible": eligible,
            "requirements": {
                "catalog_auto_install_ready": catalog_auto,
                "source_present": source_present,
                "source_verified": source_verified,
                "authentication_ready": auth_ready,
                "license_ready": license_ready,
                "integrity_ready": integrity_ready,
                "size_ready": size_ready,
            },
            "source_identity": record.get("source_identity") if isinstance(record.get("source_identity"), str) else None,
            "next_action": (
                "Create an explicit server-owned install plan; this assessment does not download anything."
                if eligible
                else "Keep the component non-automatic until every failed requirement and catalog disposition are explicitly reviewed."
            ),
        }

    def snapshot(self) -> dict[str, Any]:
        models = getattr(self.catalog, "models", {})
        runtimes = getattr(self.catalog, "runtimes", {})
        ids = sorted(
            [key for key in models if isinstance(key, str)] if isinstance(models, Mapping) else []
        ) + sorted(
            [key for key in runtimes if isinstance(key, str)] if isinstance(runtimes, Mapping) else []
        )
        records = [self.assess(component_id) for component_id in ids]
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "completed",
            "execution": "not_run",
            "dry_run": True,
            "records": records,
            "counts": {
                "total": len(records),
                "auto_install_eligible": sum(item["auto_install_eligible"] is True for item in records),
                "manual_or_blocked": sum(item["auto_install_eligible"] is not True for item in records),
            },
            "reason": "Acceptance is derived only from tracked catalog metadata; no provider request or download ran.",
        }


__all__ = ["ComponentEnablementError", "ComponentEnablementService", "SCHEMA_VERSION"]
