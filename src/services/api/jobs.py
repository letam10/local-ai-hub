"""Bounded, durable public job state for the local Hub control plane.

Runner callables and retry payloads intentionally remain process-local in the
job manager.  The hot JSON file keeps active work plus a bounded terminal
history; older terminal entries move to an ignored cold archive.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.services.artifact_store import publicize

from .config import BASE_DIR


JOBS_PATH = BASE_DIR / "Config" / "jobs.json"
ARCHIVE_JOBS_ROOT = BASE_DIR / "Archive" / "Jobs"
HOT_TERMINAL_LIMIT = 500
DEFAULT_LIST_LIMIT = 200
MAX_LIST_LIMIT = 500
PROGRESS_DEBOUNCE_SECONDS = 0.5
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "unavailable", "interrupted"})
RETRYABLE_STATUSES = frozenset({"failed", "cancelled", "unavailable"})
ACTIVE_STATUSES = frozenset({"queued", "starting", "running", "cancelling"})
ARCHIVE_MAX_FILES = 30
ARCHIVE_ROTATE_BYTES = 16 * 1024 * 1024

_lock = threading.RLock()
_jobs: dict[str, dict[str, Any]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CoalescingWriter:
    """Debounce noisy progress persistence while retaining deterministic flushing.

    ``clock`` and ``timer_factory`` are injectable so contract tests can verify
    debouncing without waiting for real time.  Terminal transitions invoke an
    immediate flush through the same writer.
    """

    def __init__(
        self,
        write: Callable[[], None],
        *,
        clock: Callable[[], float] = time.monotonic,
        debounce_seconds: float = PROGRESS_DEBOUNCE_SECONDS,
        timer_factory: Callable[[float, Callable[[], None]], threading.Timer] = threading.Timer,
    ) -> None:
        self._write = write
        self._clock = clock
        self._debounce = max(0.0, float(debounce_seconds))
        self._timer_factory = timer_factory
        self._last_write = float("-inf")
        self._dirty = False
        self._timer: threading.Timer | None = None
        self._lock = threading.RLock()

    def _invoke_write(self) -> None:
        self._write()

    def _timer_flush(self) -> None:
        with self._lock:
            self._timer = None
            if not self._dirty:
                return
            self._dirty = False
            self._last_write = self._clock()
        self._invoke_write()

    def request(self, *, immediate: bool = False) -> None:
        invoke = False
        with self._lock:
            now = self._clock()
            self._dirty = True
            if immediate or now - self._last_write >= self._debounce:
                if self._timer is not None:
                    self._timer.cancel()
                    self._timer = None
                self._dirty = False
                self._last_write = now
                invoke = True
            elif self._timer is None:
                delay = max(0.0, self._debounce - (now - self._last_write))
                timer = self._timer_factory(delay, self._timer_flush)
                timer.daemon = True
                self._timer = timer
                timer.start()
        if invoke:
            self._invoke_write()

    def flush(self) -> None:
        invoke = False
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            if self._dirty:
                self._dirty = False
                self._last_write = self._clock()
                invoke = True
        if invoke:
            self._invoke_write()


def _terminal_sort_key(record: dict[str, Any]) -> str:
    return str(record.get("finished_at") or record.get("created_at") or "")


def _durable_record(record: dict[str, Any]) -> dict[str, Any]:
    """Project a job without retry callables, inputs, paths, or session flags."""

    allowed = {
        "id",
        "contract_version",
        "tool",
        "output",
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
    }
    durable: dict[str, Any] = {}
    for key in allowed:
        value = record.get(key)
        if value is not None:
            durable[key] = publicize(value, key=key)
    return durable


def _archive_terminal_records(records: list[dict[str, Any]]) -> bool:
    if not records:
        return True
    try:
        ARCHIVE_JOBS_ROOT.mkdir(parents=True, exist_ok=True)
        stem = f"jobs-{datetime.now(timezone.utc):%Y%m%d}"
        archive = ARCHIVE_JOBS_ROOT / f"{stem}.jsonl"
        suffix = 1
        while archive.exists() and archive.stat().st_size >= ARCHIVE_ROTATE_BYTES:
            archive = ARCHIVE_JOBS_ROOT / f"{stem}-{suffix:03d}.jsonl"
            suffix += 1
        with archive.open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(_durable_record(record), ensure_ascii=False, sort_keys=True))
                handle.write("\n")
        archives = sorted(
            (item for item in ARCHIVE_JOBS_ROOT.glob("jobs-*.jsonl") if item.is_file()),
            key=lambda item: (item.stat().st_mtime, item.name),
            reverse=True,
        )
        for stale in archives[ARCHIVE_MAX_FILES:]:
            stale.unlink(missing_ok=True)
        return True
    except OSError:
        # Retain records in the hot file rather than silently losing history if
        # a local archive drive cannot be written.
        return False


def _compact_locked() -> None:
    terminal = sorted(
        (record for record in _jobs.values() if str(record.get("status")) in TERMINAL_STATUSES),
        key=_terminal_sort_key,
        reverse=True,
    )
    overflow = terminal[HOT_TERMINAL_LIMIT:]
    if overflow and _archive_terminal_records(overflow):
        for record in overflow:
            job_id = record.get("id")
            if isinstance(job_id, str):
                _jobs.pop(job_id, None)


def _save_to_disk() -> None:
    with _lock:
        _compact_locked()
        JOBS_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = JOBS_PATH.with_suffix(".tmp")
        payload = {job_id: _durable_record(record) for job_id, record in _jobs.items()}
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        temporary.replace(JOBS_PATH)


_writer = CoalescingWriter(_save_to_disk)


def _save(*, immediate: bool = True) -> None:
    """Compatibility wrapper used by this module's mutation paths."""

    _writer.request(immediate=immediate)


def flush() -> None:
    """Flush a coalesced progress write during graceful API shutdown."""

    _writer.flush()


def _load() -> None:
    """Load durable records and make an unclean prior session truthful."""

    try:
        with JOBS_PATH.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, dict):
        return
    with _lock:
        _jobs.clear()
        for key, raw_value in data.items():
            if not isinstance(raw_value, dict):
                continue
            value = dict(raw_value)
            # Private request data and any retry payload cannot survive a new
            # Python process.  The public status remains useful but never
            # advertises a runner which is no longer in this Hub session.
            value.pop("input", None)
            value.pop("resume_data", None)
            value.pop("resume_available", None)
            if str(value.get("status")) in ACTIVE_STATUSES:
                value.update({
                    "status": "interrupted",
                    "finished_at": _now(),
                    "error": "Hub đã đóng hoặc khởi động lại khi job còn chạy.",
                    "message": "Job của phiên trước đã bị gián đoạn.",
                    "next_action": "Tạo lại tác vụ từ workspace sau khi kiểm tra input và backend.",
                })
            _jobs[str(key)] = value
    # Importing a service module must be read-only for unit tools.  The actual
    # API process calls ``reconcile_startup`` once it owns its startup path.


def reconcile_startup() -> None:
    """Durably persist startup recovery and compact the hot job history."""

    with _lock:
        if not _jobs:
            return
    _save_to_disk()


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
        _save(immediate=True)
        return dict(record)


def update_job(job_id: str, **changes: Any) -> dict[str, Any] | None:
    with _lock:
        record = _jobs.get(job_id)
        if record is None:
            return None
        old_status = str(record.get("status"))
        record.update(changes)
        new_status = str(record.get("status"))
        progress_only = set(changes).issubset({"progress", "message"}) and bool(changes)
        immediate = not progress_only or new_status != old_status or new_status in TERMINAL_STATUSES
        _save(immediate=immediate)
        return dict(record)


def get_job_internal(job_id: str) -> dict[str, Any] | None:
    with _lock:
        record = _jobs.get(job_id)
        return dict(record) if record else None


def public_job(record: dict[str, Any]) -> dict[str, Any]:
    """Return job state without private machine paths, inputs, or retry payloads."""

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
    status = str(record.get("status"))
    resumable = bool(record.get("resume_data")) and status in RETRYABLE_STATUSES and record.get("resume_available") is True
    result["resumable"] = resumable
    if (status in RETRYABLE_STATUSES or status == "interrupted") and not resumable and not result.get("next_action"):
        result["next_action"] = "Job thuộc phiên Hub trước hoặc runner không còn; hãy tạo lại tác vụ từ workspace."
    return result


def get_job(job_id: str) -> dict[str, Any] | None:
    record = get_job_internal(job_id)
    return public_job(record) if record else None


def list_jobs(*, limit: int = DEFAULT_LIST_LIMIT) -> list[dict[str, Any]]:
    """List active jobs plus at most ``limit`` terminal records (1..500)."""

    bounded = max(1, min(MAX_LIST_LIMIT, int(limit)))
    with _lock:
        records = list(_jobs.values())
    active = sorted(
        (record for record in records if str(record.get("status")) in ACTIVE_STATUSES),
        key=lambda value: str(value.get("created_at") or ""),
        reverse=True,
    )
    terminal = sorted(
        (record for record in records if str(record.get("status")) not in ACTIVE_STATUSES),
        key=_terminal_sort_key,
        reverse=True,
    )
    return [public_job(item) for item in [*active, *terminal[:bounded]]]


def active_jobs() -> list[dict[str, Any]]:
    with _lock:
        return [dict(item) for item in _jobs.values() if str(item.get("status")) in ACTIVE_STATUSES]


def active_heavy_jobs() -> list[dict[str, Any]]:
    return [item for item in list_jobs() if item.get("status") in {"queued", "starting", "running", "cancelling"}]


_load()
