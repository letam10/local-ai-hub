"""Deterministic server-owned capability registry composition."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
import hashlib
import json
from typing import Any

from src.shared.schemas.module_manager import (
    CAPABILITY_REGISTRY_SCHEMA_VERSION,
    EXECUTION_NOT_RUN,
    STATUS_ALLOWLIST,
    capability_registry_fingerprint,
    validate_module_record,
)


_STATUS_RANK = {
    "error": 6,
    "unavailable": 5,
    "partial": 4,
    "planned": 3,
    "not_published": 3,
    "available": 2,
    "installed": 1,
    "operational": 0,
}


def _safe_digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _status(value: object) -> str:
    return value if isinstance(value, str) and value in STATUS_ALLOWLIST else "unavailable"


def _truthful_status(record: Mapping[str, Any]) -> str:
    status = _status(record.get("status"))
    observed = record.get("observed") if isinstance(record.get("observed"), Mapping) else {}
    evidence = record.get("evidence") if isinstance(record.get("evidence"), Mapping) else {}
    observed_state = observed.get("state", "not_run")
    evidence_state = evidence.get("state", "not_run")
    if status == "operational" and (observed_state in {"stale", "missing", "not_run"} or evidence_state in {"stale", "missing", "not_run"}):
        return "partial"
    if observed.get("fingerprint") and evidence.get("fingerprint") and observed["fingerprint"] != evidence["fingerprint"]:
        return "partial"
    return status


def _record_from_source(
    *,
    identifier: str,
    provider: str,
    component: str,
    result: Mapping[str, Any],
    tool: str | None = None,
    workflow: str | None = None,
    version: str | None = None,
) -> dict[str, Any]:
    status = _status(result.get("status", "partial"))
    source_version = version if isinstance(version, str) and version else result.get("version")
    if not isinstance(source_version, str) or not source_version:
        source_version = result.get("contract_version") or result.get("schema_version")
    record_version = str(source_version) if isinstance(source_version, str) else None
    reason = result.get("reason") if isinstance(result.get("reason"), str) else "Static server-owned evidence is available without runtime execution."
    action = result.get("action") if isinstance(result.get("action"), str) else "Review the static evidence before requesting separately authorized runtime work."
    fingerprint = result.get("fingerprint") if isinstance(result.get("fingerprint"), str) and len(result["fingerprint"]) == 64 else _safe_digest(result)
    return {
        "id": identifier,
        "provider": provider,
        "component": component,
        "tool": tool,
        "workflow": workflow,
        "version": record_version,
        "dependencies": [],
        "observed": {"state": "observed", "fingerprint": fingerprint, "source": provider},
        "evidence": {"state": "static", "fingerprint": fingerprint},
        "resource_hints": None,
        "status": status,
        "reason": reason,
        "next_action": action,
        "source": "server_owned",
        "license": "repository-managed",
        "model_requirements": [],
    }


def _default_sources() -> dict[str, Mapping[str, Any]]:
    """Read only existing server-owned static cores; no provider is executed."""

    from src.services.asset_intelligence import discover_managed_asset_catalogs
    from src.services.capability_gateway import build_server_owned_gateway_snapshot
    from src.services.extension_platform import discover_extensions
    from src.services.privacy_diagnostics.policy_catalog import discover_managed_privacy_policies
    from src.services.workflow_packages import discover_managed_packages

    return {
        "extensions": discover_extensions(),
        "workflow_packages": discover_managed_packages(),
        "assets": discover_managed_asset_catalogs(),
        "privacy": discover_managed_privacy_policies(),
        "capability_gateway": build_server_owned_gateway_snapshot(include_plans=True),
        "release_evidence": {"status": "not_published", "reason": "No release-evidence packet was published for this local snapshot.", "action": "Publish a server-owned evidence packet and obtain manager QA admission."},
    }


def build_server_owned_records(sources: Mapping[str, Mapping[str, Any]] | None = None) -> list[dict[str, Any]]:
    source_map = dict(sources) if isinstance(sources, Mapping) else _default_sources()
    records: list[dict[str, Any]] = []
    for section in ("extensions", "workflow_packages", "assets", "privacy", "capability_gateway", "release_evidence"):
        result = source_map.get(section)
        if not isinstance(result, Mapping):
            result = {"status": "unavailable", "reason": "The server-owned static source did not return a bounded mapping.", "action": "Restore the managed source and rerun static validation."}
        records.append(_record_from_source(identifier=f"core:{section}", provider=section, component=section, result=result))
        raw_items = result.get("extensions") or result.get("records") or result.get("catalogs") or result.get("policies") or []
        if isinstance(raw_items, list):
            for item in raw_items[:256]:
                if not isinstance(item, Mapping):
                    continue
                item_id = item.get("id") or item.get("extension_id")
                if not isinstance(item_id, str) or not item_id:
                    continue
                availability = item.get("availability") if isinstance(item.get("availability"), Mapping) else item
                records.append(_record_from_source(
                    identifier=f"{section}:{item_id}",
                    provider=section,
                    component=item_id,
                    result=availability if isinstance(availability, Mapping) else {"status": "partial"},
                    workflow=section,
                    version=item.get("version") if isinstance(item.get("version"), str) else None,
                ))
    return records


class CapabilityRegistry:
    """Immutable-by-convention registry with a redacted deterministic snapshot."""

    def __init__(self, records: Iterable[Mapping[str, Any]]) -> None:
        accepted: list[dict[str, Any]] = []
        errors: list[str] = []
        for item in records:
            try:
                record = validate_module_record(item)
            except Exception:
                errors.append("record_rejected")
                continue
            record["status"] = _truthful_status(record)
            accepted.append(record)
        identities = {(item["id"], item.get("version")) for item in accepted}
        if len(identities) != len(accepted):
            deduplicated: dict[tuple[str, object], dict[str, Any]] = {}
            for item in accepted:
                key = (item["id"], item.get("version"))
                current = deduplicated.get(key)
                if current is None or _STATUS_RANK[item["status"]] > _STATUS_RANK[current["status"]]:
                    deduplicated[key] = item
            accepted = list(deduplicated.values())
            errors.append("duplicate_identity")
        self._records = tuple(sorted(deepcopy(accepted), key=lambda item: (item["provider"], item["component"], item["id"], item.get("version") or "")))
        self._errors = tuple(sorted(set(errors)))

    @property
    def records(self) -> tuple[dict[str, Any], ...]:
        return deepcopy(self._records)

    def snapshot(self) -> dict[str, Any]:
        records = [deepcopy(item) for item in self._records]
        counts = {status: sum(1 for item in records if item["status"] == status) for status in sorted(STATUS_ALLOWLIST)}
        status = max((item["status"] for item in records), key=lambda item: _STATUS_RANK[item], default="unavailable")
        if self._errors:
            status = "error"
        result: dict[str, Any] = {
            "schema_version": CAPABILITY_REGISTRY_SCHEMA_VERSION,
            "status": status,
            "records": records,
            "counts": counts,
            "execution": EXECUTION_NOT_RUN,
            "dry_run": True,
            "reason": "Capability records are deterministic server-owned static projections; no provider or runtime workload was executed.",
            "next_action": "Review evidence freshness and request separately authorized bounded smoke for any runtime claim.",
        }
        if self._errors:
            result["errors"] = [{"code": error} for error in self._errors]
        result["fingerprint"] = {"algorithm": "sha256", "value": capability_registry_fingerprint(result)}
        return result


def build_capability_registry(records: Iterable[Mapping[str, Any]] | None = None, *, sources: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
    return CapabilityRegistry(records if records is not None else build_server_owned_records(sources)).snapshot()


__all__ = ["CapabilityRegistry", "build_capability_registry", "build_server_owned_records"]
