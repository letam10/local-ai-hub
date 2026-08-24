"""Job and durable-job transport adapters; scheduling stays in services."""

from __future__ import annotations

from typing import Mapping

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def list_jobs(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    raw = request.query.get("limit", [None])[0]
    try:
        limit = max(1, min(500, int(raw))) if raw is not None else 200
    except (TypeError, ValueError):
        limit = 200
    return ApiResponse(200, {"status": "completed", "jobs": context.call("list_jobs", limit=limit)})


def job_detail(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    value = context.call("get_job", params["job_id"])
    if value is None:
        return ApiResponse(404, {"status": "error", "error": "job_not_found"})
    return ApiResponse(200, value)


def cancel(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    manager = context.get("job_manager")
    ok, message = manager.cancel(params["job_id"])
    return ApiResponse(202 if ok else 400, {"status": "cancelling" if ok else "error", "message": message, "job": context.call("get_job", params["job_id"])})


def resume(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    ok, value = context.get("job_manager").resume(params["job_id"])
    if not ok:
        return ApiResponse(400, {"status": "error", "error": str(value)})
    record = value if isinstance(value, dict) else {}
    return ApiResponse(202, {"status": "queued", "job": context.call("get_job", str(record.get("id", "")))})


def submit_tool(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    status, payload = context.call("submit_tool", params["tool"], request.json(strict=True))
    return ApiResponse(status, payload)


def durable(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, context.call("durable_jobs_snapshot"))


def durable_admit(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    result = context.call("admit_durable_job", request.json(strict=True))
    status = {"accepted": 202, "invalid": 400, "unavailable": 503}.get(result.get("status"), 500)
    return ApiResponse(status, result)


def durable_resume(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    result = context.call("resume_durable_job", params["job_id"])
    status = {"conflict": 409, "invalid": 400, "unavailable": 503}.get(result.get("status"), 200)
    return ApiResponse(status, result)


def register(router: Router) -> None:
    owner = "src/services/api/routes/jobs.py"
    router.register(route_id="jobs.list", method="GET", path="/api/jobs", domain="jobs", owner=owner, handler=list_jobs)
    router.register(route_id="jobs.compat_list", method="GET", path="/jobs", domain="jobs", owner=owner, handler=list_jobs)
    router.register(route_id="jobs.detail", method="GET", path="/jobs/{job_id}", domain="jobs", owner=owner, handler=job_detail)
    router.register(route_id="jobs.cancel", method="POST", path="/jobs/{job_id}/cancel", domain="jobs", owner=owner, handler=cancel)
    router.register(route_id="jobs.resume", method="POST", path="/jobs/{job_id}/resume", domain="jobs", owner=owner, handler=resume)
    router.register(route_id="jobs.tool_submit", method="POST", path="/api/jobs/{tool}", domain="jobs", owner=owner, handler=submit_tool)
    router.register(route_id="jobs.durable_list", method="GET", path="/api/durable-jobs", domain="jobs", owner=owner, handler=durable)
    router.register(route_id="jobs.durable_admit", method="POST", path="/api/durable-jobs", domain="jobs", owner=owner, handler=durable_admit)
    router.register(route_id="jobs.durable_resume", method="POST", path="/api/durable-jobs/{job_id}/resume", domain="jobs", owner=owner, handler=durable_resume)
