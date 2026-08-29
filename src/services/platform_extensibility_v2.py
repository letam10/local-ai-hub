"""Safe Extensibility V2 contract for plugins, API versions and remote workers.

The repository already contains a static extension-platform validator.  This
module publishes the next integration boundary without importing plugin code,
loading manifests from a client, opening a remote connection or launching a
worker.  It is deliberately a read-only contract until a separately reviewed
owner supplies those capabilities.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


PLATFORM_EXTENSIBILITY_V2_SCHEMA = "platform-extensibility.v2"

_PLUGIN_SDK = {
    "id": "plugin_sdk",
    "status": "PLAN_ONLY",
    "manifest_schema": "extension-manifest.v1",
    "permissions": [
        "read_extension_metadata",
        "read_capability_cards",
        "inspect_component_status",
        "inspect_model_status",
        "plan_resources",
        "render_compatibility_report",
    ],
    "lifecycle": ["discovered", "validated", "planned", "rejected"],
    "execution_owner": "server_owned_required",
    "forbidden": ["dynamic_import", "shell", "arbitrary_path", "credential_read", "model_load", "worker_launch"],
    "reason": "The existing Extension Platform validates declarative descriptors only; no plugin code is imported or executed.",
    "next_action": "Add a separately reviewed server-owned plugin owner before exposing any lifecycle execution.",
}

_API_VERSIONING = {
    "id": "api_versioning",
    "status": "READ_ONLY",
    "current_protocol": "v8-api.v1",
    "public_contract_line": "v2",
    "supported_contracts": ["v1", "v2"],
    "compatibility": "Older routes remain explicit compatibility surfaces; a changed meaning receives a new contract version and migration review.",
    "deprecation": "No route is silently removed or downgraded; deprecations name a replacement and remain visible to feature discovery.",
    "reason": "Router-bound route inventories and Feature Discovery V2 provide explicit version identity without changing existing APIs.",
    "next_action": "Register a versioned route and contract test before introducing a breaking shape or behavior.",
}

_REMOTE_WORKER = {
    "id": "remote_worker",
    "status": "NOT_CONFIGURED",
    "transport": "none",
    "connection_state": "NOT_CONFIGURED",
    "endpoint": "not_exposed",
    "credentials": "not_exposed",
    "execution_owner": "none",
    "typed_boundary": ["discover", "availability", "capabilities", "estimate_resources", "submit", "cancel", "collect_artifacts"],
    "reason": "No remote worker endpoint, credential or execution owner is configured in V2.",
    "next_action": "Define an authenticated, capability-bound transport and independent owner before any remote dispatch.",
}


def snapshot() -> dict[str, Any]:
    """Return the finite extensibility contract without plugin or network work."""

    return {
        "schema_version": PLATFORM_EXTENSIBILITY_V2_SCHEMA,
        "status": "completed",
        "plugin_sdk": deepcopy(_PLUGIN_SDK),
        "api_versioning": deepcopy(_API_VERSIONING),
        "remote_worker": deepcopy(_REMOTE_WORKER),
        "execution": "not_run",
        "dry_run": True,
        "overall_state": "READ_ONLY_CONTRACT",
        "reason": "Extensibility V2 publishes declarative plugin, versioning and future remote-worker boundaries only; no plugin, package, endpoint or worker was accessed.",
        "next_action": "Use static descriptor validation and explicit route contracts until a separately reviewed execution owner exists.",
    }


def detail(area_id: object) -> dict[str, Any] | None:
    if not isinstance(area_id, str):
        return None
    values = {"plugin_sdk": _PLUGIN_SDK, "api_versioning": _API_VERSIONING, "remote_worker": _REMOTE_WORKER}
    selected = values.get(area_id)
    if selected is None:
        return None
    return {
        "schema_version": PLATFORM_EXTENSIBILITY_V2_SCHEMA,
        "status": "completed",
        "area": deepcopy(selected),
        "execution": "not_run",
        "dry_run": True,
        "reason": selected["reason"],
        "next_action": selected["next_action"],
    }


__all__ = ["PLATFORM_EXTENSIBILITY_V2_SCHEMA", "detail", "snapshot"]
