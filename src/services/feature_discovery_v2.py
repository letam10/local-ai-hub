"""Router-bound, path-free Post-V8 API feature discovery.

The discovery response is generated from the process Router contract instead
of a hand-maintained string list.  It describes protocol surfaces only: it
does not claim a worker, model, provider, GPU, or lifecycle action executed.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.services.api.router import Router


FEATURE_DISCOVERY_V2_SCHEMA_VERSION = "feature-discovery.v2"


def _route(route_id: str, method: str, path: str, *, execution_mode: str) -> dict[str, str]:
    return {
        "route_id": route_id,
        "method": method,
        "path": path,
        "contract_version": "v2",
        "execution_mode": execution_mode,
    }


_FEATURES: tuple[dict[str, Any], ...] = (
    {
        "feature_id": "capability_graph_v2",
        "api_version": "v2",
        "feature_state": "READ_ONLY",
        "execution": "not_run",
        "dry_run": True,
        "routes": [
            _route("capabilities.v2_snapshot", "GET", "/api/capabilities/v2", execution_mode="read_only"),
            _route("capabilities.v2_detail", "GET", "/api/capabilities/v2/{capability_id}", execution_mode="read_only"),
            _route("capabilities.v2_dependency_tree", "GET", "/api/capabilities/v2/{capability_id}/dependency-tree", execution_mode="read_only"),
            _route("capabilities.v2_blockers", "GET", "/api/capabilities/v2/{capability_id}/blockers", execution_mode="read_only"),
            _route("capabilities.v2_safe_actions", "GET", "/api/capabilities/v2/{capability_id}/safe-actions", execution_mode="read_only"),
            _route("capabilities.v2_verification_evidence", "GET", "/api/capabilities/v2/{capability_id}/verification-evidence", execution_mode="read_only"),
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
            _route("component_lifecycle.v2_snapshot", "GET", "/api/component-lifecycle/v2", execution_mode="read_only"),
            _route("component_lifecycle.v2_detail", "GET", "/api/component-lifecycle/v2/{capability_id}", execution_mode="read_only"),
            _route("component_lifecycle.v2_plan", "POST", "/api/component-lifecycle/v2/{capability_id}/plans", execution_mode="plan_only"),
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
            _route("model_manager.v2_snapshot", "GET", "/api/model-manager/v2", execution_mode="read_only"),
            _route("model_manager.v2_detail", "GET", "/api/model-manager/v2/{model_id}", execution_mode="read_only"),
            _route("model_manager.v2_preflight", "GET", "/api/model-manager/v2/{model_id}/preflight", execution_mode="read_only"),
            _route("model_manager.v2_plan", "POST", "/api/model-manager/v2/{model_id}/plans", execution_mode="plan_only"),
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
            _route("resource_scheduler.v2_snapshot", "GET", "/api/resource-scheduler/v2", execution_mode="read_only"),
            _route("resource_scheduler.v2_profiles", "GET", "/api/resource-scheduler/v2/profiles", execution_mode="read_only"),
            _route("resource_scheduler.v2_job", "GET", "/api/resource-scheduler/v2/jobs/{job_id}", execution_mode="read_only"),
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
            _route("durable_job_engine.v2_snapshot", "GET", "/api/durable-job-engine/v2", execution_mode="read_only"),
            _route("durable_job_engine.v2_detail", "GET", "/api/durable-job-engine/v2/{job_id}", execution_mode="read_only"),
            _route("durable_job_engine.v2_admit", "POST", "/api/durable-job-engine/v2", execution_mode="owner_required"),
            _route("durable_job_engine.v2_retry", "POST", "/api/durable-job-engine/v2/{job_id}/retry", execution_mode="reconstruct_or_owner_required"),
            _route("durable_job_engine.v2_cancel", "POST", "/api/durable-job-engine/v2/{job_id}/cancel", execution_mode="owner_required"),
            _route("durable_job_engine.v2_archive", "POST", "/api/durable-job-engine/v2/{job_id}/archive", execution_mode="metadata_only"),
            _route("durable_job_engine.v2_history_delete", "POST", "/api/durable-job-engine/v2/history/delete", execution_mode="metadata_only"),
        ],
        "reason": "Persists/reconciles opaque job metadata, but actual dispatch requires a separately registered server-owned execution owner.",
        "next_action": "Treat reconstruct-only records as not executed until a real owner claims an exact reservation.",
    },
    {
        "feature_id": "workflow_runtime_v2",
        "api_version": "v2",
        "feature_state": "PLAN_ONLY",
        "execution": "not_run",
        "dry_run": True,
        "routes": [
            _route("workflow_runtime.v2_contract", "GET", "/api/workflow-runtime/v2", execution_mode="read_only"),
            _route("workflow_runtime.v2_preflight", "POST", "/api/workflow-runtime/v2/preflight", execution_mode="plan_only"),
            _route("workflow_runtime.v2_dispatch", "POST", "/api/workflow-runtime/v2/dispatch", execution_mode="owner_required"),
        ],
        "reason": "Builds a typed graph/capability/resource/artifact preflight without reserving or executing a workflow.",
        "next_action": "Review the plan and register a separately verified server-owned execution owner before dispatch.",
    },
    {
        "feature_id": "project_workspace_v2",
        "api_version": "v2",
        "feature_state": "METADATA_ONLY",
        "execution": "not_run",
        "dry_run": True,
        "routes": [
            _route("project_workspace.v2_snapshot", "GET", "/api/project-workspace/v2", execution_mode="read_only"),
            _route("project_workspace.v2_detail", "GET", "/api/project-workspace/v2/{project_id}", execution_mode="read_only"),
            _route("project_workspace.v2_export_manifest", "GET", "/api/project-workspace/v2/{project_id}/manifest", execution_mode="read_only"),
            _route("project_workspace.v2_attach_workflow", "POST", "/api/project-workspace/v2/{project_id}/workflows", execution_mode="metadata_only"),
            _route("project_workspace.v2_attach_job", "POST", "/api/project-workspace/v2/{project_id}/jobs", execution_mode="metadata_only"),
        ],
        "reason": "Projects are projected from existing server-owned metadata; an explicit route may attach only an already-validated opaque workflow/job reference.",
        "next_action": "Review the project state, then attach an existing opaque reference explicitly; this does not run a workflow or copy an artifact.",
    },
    {
        "feature_id": "artifact_library_v2",
        "api_version": "v2",
        "feature_state": "READ_ONLY",
        "execution": "not_run",
        "dry_run": True,
        "routes": [
            _route("artifact_library.v2_snapshot", "GET", "/api/artifact-library/v2", execution_mode="read_only"),
            _route("artifact_library.v2_detail", "GET", "/api/artifact-library/v2/{artifact_id}", execution_mode="read_only"),
        ],
        "reason": "Exposes only existing opaque artifact metadata and bounded loopback preview references.",
        "next_action": "Inspect lineage or open an existing preview; no thumbnail or export task runs from this surface.",
    },
    {
        "feature_id": "media_pipeline_v2",
        "api_version": "v2",
        "feature_state": "PLAN_ONLY",
        "execution": "not_run",
        "dry_run": True,
        "routes": [
            _route("media_pipeline.v2_contract", "GET", "/api/media-pipeline/v2", execution_mode="read_only"),
            _route("media_pipeline.v2_preflight", "POST", "/api/media-pipeline/v2/preflight", execution_mode="plan_only"),
        ],
        "reason": "Publishes a closed media allowlist and preflight; FFmpeg, AnimeSR, RIFE and providers are not executed.",
        "next_action": "Review the media plan and use a future server-owned execution owner after bounded evidence exists.",
    },
)

_FEATURE_IDS = frozenset(item["feature_id"] for item in _FEATURES)


def _router_or_default(router: Router | None) -> Router:
    if isinstance(router, Router):
        return router
    # Lazy import avoids an API composition cycle at module import time.  It
    # only constructs the route table; it neither starts the API nor executes
    # a component.
    from src.services.api.router_registry import build_router

    return build_router()


def _actual_contracts(router: Router) -> dict[str, tuple[str, str]]:
    return {route.route_id: (route.method, route.path) for route in router.routes()}


def _bound_features(router: Router) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    actual = _actual_contracts(router)
    bound: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []
    for feature in _FEATURES:
        expected_routes = feature["routes"]
        absent = [
            route for route in expected_routes
            if actual.get(route["route_id"]) != (route["method"], route["path"])
        ]
        if absent:
            missing.extend({
                "feature_id": feature["feature_id"],
                "route_id": route["route_id"],
                "method": route["method"],
                "path": route["path"],
            } for route in absent)
            continue
        bound.append(deepcopy(feature))
    return bound, missing


def snapshot(router: Router | None = None) -> dict[str, Any]:
    """Return only feature contracts confirmed by this process Router."""

    features, missing = _bound_features(_router_or_default(router))
    complete = not missing and len(features) == len(_FEATURES)
    return {
        "schema_version": FEATURE_DISCOVERY_V2_SCHEMA_VERSION,
        "status": "completed" if complete else "unavailable",
        "features": features,
        "missing_required_routes": missing,
        "reason": "Feature discovery is bound to the active Router contract; absent or mismatched routes are not advertised." if not complete else "Feature discovery describes Router-confirmed API contracts; runtime/capability state remains separately server-owned.",
        "next_action": "Restore every required Post-V8 route contract before relying on discovery." if not complete else "Use the advertised route only after inspecting its specific capability or preflight response.",
        "execution": "not_run",
        "dry_run": True,
    }


def detail(feature_id: object, router: Router | None = None) -> dict[str, Any] | None:
    if not isinstance(feature_id, str) or feature_id not in _FEATURE_IDS:
        return None
    features, _ = _bound_features(_router_or_default(router))
    match = next((item for item in features if item["feature_id"] == feature_id), None)
    if match is None:
        return None
    return {
        "schema_version": FEATURE_DISCOVERY_V2_SCHEMA_VERSION,
        "status": "completed",
        "feature": match,
        "execution": "not_run",
        "dry_run": True,
    }


__all__ = ["FEATURE_DISCOVERY_V2_SCHEMA_VERSION", "detail", "snapshot"]
