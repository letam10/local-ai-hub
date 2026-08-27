"""Read-only Resource Scheduler V2 API adapters.

Scheduling mutations are intentionally not exposed in Phase 4. A future
Durable Job Engine may call the server-owned scheduler after capability/input
preflight; the browser can only inspect profiles, reservations, queue state,
and an opaque job projection.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def _not_found() -> ApiResponse:
    return ApiResponse(404, {
        "status": "unavailable",
        "error": "scheduler_job_not_found",
        "execution": "not_run",
        "dry_run": True,
    })


def snapshot(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del request, params
    return ApiResponse(200, context.call("resource_scheduler_v2_snapshot"))


def profiles(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del request, params
    snapshot_value: Any = context.call("resource_scheduler_v2_snapshot")
    if not isinstance(snapshot_value, Mapping):
        return ApiResponse(503, {"status": "unavailable", "error": "scheduler_unavailable", "execution": "not_run", "dry_run": True})
    return ApiResponse(200, {
        "schema_version": snapshot_value.get("schema_version"),
        "status": snapshot_value.get("status"),
        "profiles": snapshot_value.get("profiles", []),
        "execution": "not_run",
        "dry_run": True,
    })


def job(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del request
    value: Any = context.call("resource_scheduler_v2_job", params.get("job_id", ""))
    return ApiResponse(200, value) if isinstance(value, Mapping) else _not_found()


def register(router: Router) -> None:
    owner = "src/services/api/routes/resource_scheduler_v2.py"
    root = "/api/resource-scheduler/v2"
    router.register(route_id="resource_scheduler.v2_snapshot", method="GET", path=root, domain="jobs", owner=owner, handler=snapshot)
    router.register(route_id="resource_scheduler.v2_profiles", method="GET", path=f"{root}/profiles", domain="jobs", owner=owner, handler=profiles)
    router.register(route_id="resource_scheduler.v2_job", method="GET", path=f"{root}/jobs/{{job_id}}", domain="jobs", owner=owner, handler=job)


__all__ = ["register"]
