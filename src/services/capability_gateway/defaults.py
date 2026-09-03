"""Read-only default loaders for the server-owned V5 control plane."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .gateway import TrustedSectionLoaderRegistry, build_capability_gateway
from .projection import project_section
from src.shared.schemas.capability_gateway import CAPABILITY_GATEWAY_SCHEMA_VERSION


def _section_records(section: str, source: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = source.get("extensions") or source.get("records") or source.get("catalogs") or source.get("policies") or source.get("snapshots") or []
    records: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return records
    for index, item in enumerate(raw[:256]):
        if not isinstance(item, Mapping):
            continue
        identifier = item.get("id") or item.get("extension_id") or f"{section}-{index + 1}"
        if not isinstance(identifier, str):
            continue
        version = item.get("version") or item.get("contract_version") or "1.0.0"
        status = item.get("status")
        if not isinstance(status, str):
            availability = item.get("availability") if isinstance(item.get("availability"), Mapping) else {}
            status = availability.get("status", "partial")
        if status not in {"operational", "partial", "planned", "unavailable"}:
            status = "partial"
        # Gateway IDs use the closed identifier alphabet (no colon).  The
        # Module Manager keeps its own section:item identity separately.
        records.append({"id": f"{section}.{identifier}", "version": str(version), "status": status, "type_summary": ["static"], "counts": {"items": 1}})
    return records


def build_server_owned_gateway_snapshot(*, include_plans: bool = False) -> dict[str, Any]:
    """Compose V4 static cores behind the existing closed gateway boundary."""

    from src.services.asset_intelligence import discover_managed_asset_catalogs
    from src.services.extension_platform import discover_extensions
    from src.services.privacy_diagnostics.policy_catalog import discover_managed_privacy_policies
    from src.services.workflow_packages import discover_managed_packages

    sources = {
        "extensions": discover_extensions,
        "workflow_packages": discover_managed_packages,
        "assets": discover_managed_asset_catalogs,
        "privacy": discover_managed_privacy_policies,
        "recipes": lambda: {"status": "unavailable", "records": []},
    }

    def loader(section: str):
        def load(selectors: tuple[str, ...], requested_plans: bool) -> dict[str, Any]:
            source = sources[section]()
            if not isinstance(source, Mapping):
                return {
                    "status": "unavailable",
                    "reason_code": "section_unavailable",
                    "action_code": "restore_managed_source",
                    "records": [],
                    "counts": {"items": 0},
                    "type_summaries": [],
                    "execution": "not_run",
                    "dry_run": requested_plans,
                }
            records = _section_records(section, source)
            if selectors:
                records = [item for item in records if item["id"] in selectors]
            status = source.get("status", "partial")
            if status not in {"operational", "partial", "planned", "unavailable"}:
                status = "partial"
            return {
                "status": status,
                "reason_code": "section_unavailable" if status == "unavailable" else "static_metadata_only",
                "action_code": "restore_managed_source" if status == "unavailable" else "review_runtime_evidence",
                "records": records,
                "counts": {"items": len(records)},
                "type_summaries": ["static"] if records else [],
                "execution": "not_run",
                "dry_run": requested_plans,
            }

        return load

    registry = TrustedSectionLoaderRegistry({section: loader(section) for section in sources})
    request = {"schema_version": CAPABILITY_GATEWAY_SCHEMA_VERSION, "sections": list(sources), "selectors": {}, "include_plans": bool(include_plans)}
    return build_capability_gateway(request, loaders=registry)


__all__ = ["build_server_owned_gateway_snapshot"]
