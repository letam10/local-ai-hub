"""Explicit, path-free API feature discovery for the Post-V8 platform.

The document advertises protocol surfaces, not runtime health. A feature may be
discoverable while its execution mode is read-only, plan-only, or requires a
separately registered server-owned owner. This keeps clients from inferring
that an endpoint implies a model, provider, worker, or GPU workload is ready.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


FEATURE_DISCOVERY_V2_SCHEMA_VERSION = "feature-discovery.v2"


_FEATURES: tuple[dict[str, Any], ...] = (
    {
        "feature_id": "capability_graph_v2",
        "api_version": "v2",
        "feature_state": "READ_ONLY",
        "execution": "not_run",
        "dry_run": True,
        "routes": [
            "/api/capabilities/v2",
            "/api/capabilities/v2/{capability_id}",
            "/api/capabilities/v2/{capability_id}/dependency-tree",
            "/api/capabilities/v2/{capability_id}/blockers",
            "/api/capabilities/v2/{capability_id}/safe-actions",
            "/api/capabilities/v2/{capability_id}/verification-evidence",
        ],
        "reason": "Publishes bounded server-owned capability/dependency evidence only.",
        "next_action": "Inspect exact blockers before requesting a lifecycle plan.",
    },
    {
        "feature_id": "component_lifecycle_engine_v2",
        "api_version": "v2",
        "feature_state": "PLAN_ONLY",
        "execution": "not_run",
        "dry_run": True,
        "routes": [
            "/api/component-lifecycle/v2",
            "/api/component-lifecycle/v2/{capability_id}",
            "/api/component-lifecycle/v2/{capability_id}/plans",
        ],
        "reason": "Creates existing V8 server-owned plans; confirmation/execution stays outside this discovery surface.",
        "next_action": "Review the plan and its ownership requirements before confirmation.",
    },
    {
        "feature_id": "model_manager_v2",
        "api_version": "v2",
        "feature_state": "PLAN_ONLY",
        "execution": "not_run",
        "dry_run": True,
        "routes": [
            "/api/model-manager/v2",
            "/api/model-manager/v2/{model_id}",
            "/api/model-manager/v2/{model_id}/preflight",
            "/api/model-manager/v2/{model_id}/plans",
        ],
        "reason": "Uses bounded catalog metadata and delegates supported plans to V8; it does not load or download a model.",
        "next_action": "Use model preflight and a separately confirmed server-owned plan.",
    },
    {
        "feature_id": "resource_scheduler_v2",
        "api_version": "v2",
        "feature_state": "READ_ONLY",
        "execution": "not_run",
        "dry_run": True,
        "routes": [
            "/api/resource-scheduler/v2",
            "/api/resource-scheduler/v2/profiles",
            "/api/resource-scheduler/v2/jobs/{job_id}",
        ],
        "reason": "Exposes server-owned resource reservations without probing GPU or launching a worker.",
        "next_action": "Use a separately owned durable admission path for any future dispatch.",
    },
    {
        "feature_id": "durable_job_engine_v2",
        "api_version": "v2",
        "feature_state": "OWNER_REQUIRED",
        "execution": "not_run",
        "dry_run": True,
        "routes": [
            "/api/durable-job-engine/v2",
            "/api/durable-job-engine/v2/{job_id}",
            "/api/durable-job-engine/v2/{job_id}/retry",
            "/api/durable-job-engine/v2/{job_id}/cancel",
            "/api/durable-job-engine/v2/{job_id}/archive",
            "/api/durable-job-engine/v2/history/delete",
        ],
        "reason": "Persists/reconciles opaque job metadata, but actual dispatch requires a separately registered server-owned execution owner.",
        "next_action": "Treat reconstruct-only records as not executed until a real owner claims an exact reservation.",
    },
)

_FEATURE_IDS = frozenset(item["feature_id"] for item in _FEATURES)


def snapshot() -> dict[str, Any]:
    """Return a detached finite protocol catalog with no runtime assertion."""

    return {
        "schema_version": FEATURE_DISCOVERY_V2_SCHEMA_VERSION,
        "status": "completed",
        "features": deepcopy(list(_FEATURES)),
        "reason": "Feature discovery describes supported API contracts; runtime/capability state remains separately server-owned.",
        "next_action": "Use the advertised route only after inspecting its specific capability or preflight response.",
        "execution": "not_run",
        "dry_run": True,
    }


def detail(feature_id: object) -> dict[str, Any] | None:
    if not isinstance(feature_id, str) or feature_id not in _FEATURE_IDS:
        return None
    match = next(item for item in _FEATURES if item["feature_id"] == feature_id)
    return {
        "schema_version": FEATURE_DISCOVERY_V2_SCHEMA_VERSION,
        "status": "completed",
        "feature": deepcopy(match),
        "execution": "not_run",
        "dry_run": True,
    }


__all__ = ["FEATURE_DISCOVERY_V2_SCHEMA_VERSION", "detail", "snapshot"]
