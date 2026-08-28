"""Durable Job Engine V2 loopback transport adapters.

The browser receives only opaque job/artifact identifiers and finite action
codes.  It cannot claim a worker, report progress, finish a job, supply a
filesystem path, or construct an execution command.  Trusted server-owned
workers use the engine's internal exact-reservation methods separately.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


_STATES = frozenset({
    "QUEUED", "WAITING_RESOURCE", "PREPARING", "RUNNING", "PAUSED",
    "CANCELLING", "CANCELLED", "SUCCEEDED", "FAILED",
})
_RETRY_MODES = frozenset({"RETRY_EXECUTION", "RECONSTRUCT_ONLY"})
_RESULT_STATUS = {
    "accepted": 202,
    "queued": 202,
    "running": 200,
    "paused": 200,
    "resumed": 200,
    "cancelling": 202,
    "cancelled": 200,
    "completed": 200,
    "invalid": 400,
    "conflict": 409,
    "unavailable": 503,
    "not_found": 404,
}


def _error(status: int, code: str) -> ApiResponse:
    return ApiResponse(status, {
        "status": "invalid" if status == 400 else "unavailable",
        "error": code,
        "execution": "not_run",
        "dry_run": True,
    })


def _result(value: object) -> ApiResponse:
    if not isinstance(value, Mapping):
        return _error(503, "durable_job_v2_unavailable")
    status = value.get("status")
    return ApiResponse(_RESULT_STATUS.get(status, 500), dict(value))


def _query_one(request: ApiRequest, name: str) -> str | None:
    values = request.query.get(name, [])
    if len(values) != 1 or not isinstance(values[0], str):
        return None
    return values[0]


def snapshot(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del params
    status = _query_one(request, "status") if "status" in request.query else None
    query = _query_one(request, "query") if "query" in request.query else None
    archived_raw = _query_one(request, "archived") if "archived" in request.query else None
    if status is not None and status not in _STATES:
        return _error(400, "durable_job_v2_status_invalid")
    if "status" in request.query and status is None:
        return _error(400, "durable_job_v2_query_invalid")
    if "query" in request.query and (query is None or len(query) > 80):
        return _error(400, "durable_job_v2_query_invalid")
    if archived_raw not in {None, "true", "false"}:
        return _error(400, "durable_job_v2_archived_invalid")
    return ApiResponse(200, context.call("durable_job_v2_snapshot", status=status, query=query, archived=archived_raw == "true"))


def detail(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del request
    value: Any = context.call("durable_job_v2_detail", params.get("job_id", ""))
    if value is None:
        return ApiResponse(404, {
            "status": "not_found",
            "error": "durable_job_v2_not_found",
            "execution": "not_run",
            "dry_run": True,
        })
    return ApiResponse(200, value)


def admit(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del params
    return _result(context.call("durable_job_v2_admit", request.json(strict=True)))


def retry(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json(strict=True)
    mode = body.get("mode") if isinstance(body, dict) and set(body) == {"mode"} else None
    if not isinstance(mode, str) or mode not in _RETRY_MODES:
        return _error(400, "durable_job_v2_retry_payload_invalid")
    return _result(context.call("durable_job_v2_retry", params.get("job_id", ""), mode=mode))


def cancel(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json(strict=True)
    if not isinstance(body, dict) or body:
        return _error(400, "durable_job_v2_cancel_payload_invalid")
    return _result(context.call("durable_job_v2_cancel", params.get("job_id", "")))


def archive(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    body = request.json(strict=True)
    if not isinstance(body, dict) or body:
        return _error(400, "durable_job_v2_archive_payload_invalid")
    return _result(context.call("durable_job_v2_archive", params.get("job_id", "")))


def delete_history(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    del params
    body = request.json(strict=True)
    if not isinstance(body, dict) or set(body) != {"job_ids", "confirmed"} or body.get("confirmed") is not True:
        return _error(400, "durable_job_v2_delete_payload_invalid")
    return _result(context.call("durable_job_v2_delete_history", body.get("job_ids")))


def register(router: Router) -> None:
    owner = "src/services/api/routes/durable_job_engine_v2.py"
    root = "/api/durable-job-engine/v2"
    router.register(route_id="durable_job_engine.v2_snapshot", method="GET", path=root, domain="jobs", owner=owner, handler=snapshot)
    router.register(route_id="durable_job_engine.v2_detail", method="GET", path=f"{root}/{{job_id}}", domain="jobs", owner=owner, handler=detail)
    router.register(route_id="durable_job_engine.v2_admit", method="POST", path=root, domain="jobs", owner=owner, handler=admit)
    router.register(route_id="durable_job_engine.v2_retry", method="POST", path=f"{root}/{{job_id}}/retry", domain="jobs", owner=owner, handler=retry)
    router.register(route_id="durable_job_engine.v2_cancel", method="POST", path=f"{root}/{{job_id}}/cancel", domain="jobs", owner=owner, handler=cancel)
    router.register(route_id="durable_job_engine.v2_archive", method="POST", path=f"{root}/{{job_id}}/archive", domain="jobs", owner=owner, handler=archive)
    router.register(route_id="durable_job_engine.v2_history_delete", method="POST", path=f"{root}/history/delete", domain="jobs", owner=owner, handler=delete_history)


__all__ = ["register"]
