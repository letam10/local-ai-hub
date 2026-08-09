"""Small in-process job manager for direct Hub workflows.

The API remains intentionally local and lightweight: a job owns only the
processes that it starts, exposes cancellation/resume state, and serializes
heavy GPU work through one configurable slot.  It is not a benchmark runner or
a distributed queue.
"""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.services.api.jobs import create_job, get_job_internal, update_job
from src.services.process_manager.managed import terminate_owned_process
from src.services.tool_smoke import record_completed


Runner = Callable[[dict[str, Any], "JobContext"], dict[str, Any]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class JobContext:
    job_id: str
    _cancel_event: threading.Event = field(default_factory=threading.Event)
    _processes: set[subprocess.Popen[bytes]] = field(default_factory=set)
    _lock: threading.RLock = field(default_factory=threading.RLock)

    @property
    def cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def attach_process(self, process: subprocess.Popen[bytes], _label: str) -> None:
        with self._lock:
            self._processes.add(process)

    def detach_process(self, process: subprocess.Popen[bytes]) -> None:
        with self._lock:
            self._processes.discard(process)

    def cancel(self) -> None:
        self._cancel_event.set()
        with self._lock:
            processes = list(self._processes)
        for process in processes:
            terminate_owned_process(process)

    def progress(self, value: int, message: str | None = None) -> None:
        update_job(self.job_id, progress=max(0, min(100, int(value))), message=message)


class HubJobManager:
    def __init__(self) -> None:
        self._contexts: dict[str, JobContext] = {}
        self._runners: dict[str, Runner] = {}
        self._lock = threading.RLock()
        self._heavy_slot = threading.Semaphore(1)

    def submit(
        self,
        tool: str,
        payload: dict[str, Any],
        runner: Runner,
        *,
        device: str | None = None,
        heavy: bool = True,
    ) -> dict[str, Any]:
        record = create_job(tool, payload, device=device, resume_data=payload)
        context = JobContext(record["id"])
        with self._lock:
            self._contexts[record["id"]] = context
            self._runners[record["id"]] = runner
        thread = threading.Thread(
            target=self._run,
            args=(record["id"], payload, runner, context, heavy),
            name=f"LocalAIHub-{tool}-{record['id'][-8:]}",
            daemon=True,
        )
        thread.start()
        return record

    def _run(self, job_id: str, payload: dict[str, Any], runner: Runner, context: JobContext, heavy: bool) -> None:
        acquired = False
        try:
            update_job(job_id, status="starting", progress=1, started_at=_now(), message="Đang chuẩn bị worker Hub.")
            if heavy:
                while not acquired:
                    if context.cancelled:
                        update_job(job_id, status="cancelled", finished_at=_now(), message="Tác vụ đã được hủy trước khi chạy.")
                        return
                    acquired = self._heavy_slot.acquire(timeout=0.2)
            if context.cancelled:
                update_job(job_id, status="cancelled", finished_at=_now(), message="Tác vụ đã được hủy trước khi chạy.")
                return
            update_job(job_id, status="running", progress=5, message="Worker Hub đang chạy nền.")
            result = runner(payload, context)
            if context.cancelled or result.get("status") == "cancelled":
                update_job(job_id, status="cancelled", progress=0, finished_at=_now(), result=result, message="Tác vụ đã được hủy.")
            elif result.get("status") == "completed":
                update_job(job_id, status="completed", progress=100, finished_at=_now(), result=result, message="Hoàn tất.")
                record_completed(tool)
            elif result.get("status") == "unavailable":
                update_job(job_id, status="unavailable", progress=0, finished_at=_now(), result=result, error=result.get("reason"), message="Backend chưa khả dụng.")
            else:
                update_job(job_id, status="failed", progress=0, finished_at=_now(), result=result, error=result.get("error") or result.get("reason") or "Worker không hoàn tất.", message="Không thể hoàn tất tác vụ.")
        except Exception as exc:  # pragma: no cover - guards background threads
            update_job(job_id, status="failed", progress=0, finished_at=_now(), error=str(exc), message="Worker Hub gặp lỗi không mong đợi.")
        finally:
            if acquired:
                self._heavy_slot.release()
            with self._lock:
                self._contexts.pop(job_id, None)

    def cancel(self, job_id: str) -> tuple[bool, str]:
        with self._lock:
            context = self._contexts.get(job_id)
        record = get_job_internal(job_id)
        if record is None:
            return False, "Không tìm thấy job Hub."
        if record.get("status") in {"completed", "failed", "cancelled", "unavailable"}:
            return False, "Job này đã kết thúc."
        if context:
            context.cancel()
        update_job(job_id, status="cancelling", message="Đang dừng các process do Hub sở hữu.")
        return True, "Hub đang hủy job và chỉ dừng process do Hub tạo."

    def resume(self, job_id: str) -> tuple[bool, dict[str, Any] | str]:
        record = get_job_internal(job_id)
        if record is None:
            return False, "Không tìm thấy job Hub."
        if record.get("status") not in {"cancelled", "failed"}:
            return False, "Chỉ có thể tiếp tục job đã hủy hoặc thất bại."
        with self._lock:
            runner = self._runners.get(job_id)
        payload = record.get("resume_data")
        if runner is None or not isinstance(payload, dict):
            return False, "Job không còn runner trong phiên Hub hiện tại; hãy tạo lại tác vụ từ workspace."
        return True, self.submit(str(record.get("tool") or "job"), payload, runner, device=record.get("device"), heavy=True)


job_manager = HubJobManager()
