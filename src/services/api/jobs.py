from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.services.artifact_store import publicize

from .config import BASE_DIR


JOBS_PATH = BASE_DIR / "Config" / "jobs.json"
_lock = threading.RLock()
_jobs: dict[str, dict[str, Any]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> None:
    if not JOBS_PATH.exists():
        return
    try:
        with JOBS_PATH.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            for key, value in data.items():
                if not isinstance(value, dict):
                    continue
                # Runner callables are process-local and deliberately never
                # survive an API restart.  Reset this private flag while
                # loading durable records so stale jobs cannot advertise a
                # retry button in a fresh Hub session.
                value = dict(value)
                value["resume_available"] = False
                _jobs[str(key)] = value
    except (OSError, json.JSONDecodeError):
        return


def _save() -> None:
    JOBS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = JOBS_PATH.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(_jobs, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.replace(JOBS_PATH)


_load()


def create_job(
    tool: str,
    input_data: Any,
    *,
    output: str | None = None,
    device: str | None = None,
    resume_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with _lock:
        job_id = f"job_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        record = {
            "contract_version": "job.v2",
            "id": job_id,
            "tool": tool,
            "input": input_data,
            "output": output,
            "status": "queued",
            "progress": 0,
            "created_at": _now(),
            "started_at": None,
            "finished_at": None,
            "device": device,
            "error": None,
            "resume_data": resume_data if resume_data is not None else (dict(input_data) if isinstance(input_data, dict) else None),
            "resume_available": False,
            "result": None,
            "next_action": None,
        }
        _jobs[job_id] = record
        _save()
        return dict(record)


def update_job(job_id: str, **changes: Any) -> dict[str, Any] | None:
    with _lock:
        record = _jobs.get(job_id)
        if record is None:
            return None
        record.update(changes)
        _save()
        return dict(record)


def get_job_internal(job_id: str) -> dict[str, Any] | None:
    with _lock:
        record = _jobs.get(job_id)
        return dict(record) if record else None


def public_job(record: dict[str, Any]) -> dict[str, Any]:
    """Return the durable job state without private machine paths or inputs."""

    allowed = {
        "id",
        "contract_version",
        "tool",
        "status",
        "progress",
        "created_at",
        "started_at",
        "finished_at",
        "device",
        "error",
        "message",
        "next_action",
        "result",
        "resumable",
    }
    result = {key: value for key, value in record.items() if key in allowed and value is not None}
    if "result" in result:
        result["result"] = publicize(result["result"])
    if result.get("error"):
        result["error"] = publicize(result["error"])
    terminal = record.get("status") in {"cancelled", "failed", "unavailable"}
    resumable = bool(record.get("resume_data")) and terminal and record.get("resume_available") is True
    result["resumable"] = resumable
    if terminal and bool(record.get("resume_data")) and not resumable:
        result["next_action"] = "Job thuộc phiên Hub trước hoặc runner không còn; hãy tạo lại tác vụ từ workspace."
    return result


def get_job(job_id: str) -> dict[str, Any] | None:
    record = get_job_internal(job_id)
    return public_job(record) if record else None


def list_jobs() -> list[dict[str, Any]]:
    with _lock:
        return [public_job(item) for item in sorted(_jobs.values(), key=lambda value: value.get("created_at", ""), reverse=True)]


def active_heavy_jobs() -> list[dict[str, Any]]:
    return [item for item in list_jobs() if item.get("status") in {"starting", "running"}]
