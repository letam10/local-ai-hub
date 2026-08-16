"""Small in-process job manager for direct Hub workflows.

The API remains intentionally local and lightweight: a job owns only the
processes that it starts, exposes cancellation/resume state, and serializes
heavy GPU work through one configurable slot.  It is not a benchmark runner or
a distributed queue.
"""

from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.services.api.jobs import TERMINAL_STATUSES, _publish_result, active_jobs, create_job, get_job_internal, update_job
from src.services.process_manager.managed import terminate_owned_process
from src.services.tool_smoke import record_completed


Runner = Callable[[dict[str, Any], "JobContext"], dict[str, Any]]
MAX_RUNNER_SPECS = 64


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
        with self._lock:
            self._cancel_event.set()
            processes = list(self._processes)
        for process in processes:
            terminate_owned_process(process)

    def progress(self, value: int, message: str | None = None) -> None:
        update_job(self.job_id, progress=max(0, min(100, int(value))), message=message)


@dataclass(frozen=True)
class RunnerSpec:
    """Process-local retry metadata; never persisted as a callable."""

    runner: Runner
    device: str | None
    heavy: bool
    created_at: float


class HubJobManager:
    def __init__(self) -> None:
        self._contexts: dict[str, JobContext] = {}
        self._runners: dict[str, Runner] = {}
        self._runner_specs: dict[str, RunnerSpec] = {}
        # A cancel can arrive after the durable queued record exists but
        # before ``submit`` has installed its in-process context.  Keep only
        # that tiny hand-off tombstone so the future context starts cancelled
        # instead of becoming a phantom active job.
        self._pending_cancellations: set[str] = set()
        self._lock = threading.RLock()
        self._heavy_slot = threading.Semaphore(1)

    def _trim_runner_specs_locked(self) -> None:
        """Bound process-local retry state while preserving active runners."""

        overflow = len(self._runner_specs) - MAX_RUNNER_SPECS
        if overflow <= 0:
            return
        candidates = sorted(self._runner_specs.items(), key=lambda item: item[1].created_at)
        for job_id, _spec in candidates:
            if overflow <= 0:
                break
            if job_id in self._contexts:
                continue
            self._runner_specs.pop(job_id, None)
            self._runners.pop(job_id, None)
            # Keep the public contract truthful if a terminal job is evicted.
            update_job(job_id, resume_available=False)
            overflow -= 1

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
            pending_cancel = record["id"] in self._pending_cancellations
            self._pending_cancellations.discard(record["id"])
            self._contexts[record["id"]] = context
            self._runners[record["id"]] = runner
            self._runner_specs[record["id"]] = RunnerSpec(
                runner=runner,
                device=device,
                heavy=bool(heavy),
                created_at=time.monotonic(),
            )
            self._trim_runner_specs_locked()
            if pending_cancel:
                context.cancel()
        # This private flag is session-scoped and is set only after the
        # in-process runner has been registered.
        update_job(record["id"], resume_available=True)
        record["resume_available"] = True
        thread = threading.Thread(
            target=self._run,
            args=(record["id"], tool, payload, runner, context, heavy),
            name=f"LocalAIHub-{tool}-{record['id'][-8:]}",
            daemon=True,
        )
        thread.start()
        return record

    def _run(self, job_id: str, tool: str, payload: dict[str, Any], runner: Runner, context: JobContext, heavy: bool) -> None:
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
            raw_result = runner(payload, context)
            # Cancellation and publication share the context lock.  A cancel
            # that arrives before this critical section prevents publication;
            # a cancel that arrives during it waits until the terminal state
            # is durable, so it cannot leave an orphaned artifact behind.
            with context._lock:
                record = get_job_internal(job_id) or {"id": job_id, "tool": tool}
                result, publish_error = _publish_result(
                    {"status": "cancelled"} if context.cancelled else raw_result,
                    record,
                )
                if context.cancelled or result.get("status") == "cancelled":
                    update_job(job_id, status="cancelled", progress=0, finished_at=_now(), result={"status": "cancelled"}, message="Tác vụ đã được hủy.")
                elif publish_error is not None:
                    update_job(
                        job_id,
                        status="failed",
                        progress=0,
                        finished_at=_now(),
                        result=result,
                        error="Output không được publish thành artifact Hub; job giữ trạng thái failed.",
                        message="Không thể publish output.",
                        next_action="Kiểm tra runtime/output contract rồi tạo lại job.",
                    )
                elif result.get("status") == "completed":
                    update_job(job_id, status="completed", progress=100, finished_at=_now(), result=result, message="Hoàn tất.", next_action=result.get("next_action"))
                    record_completed(tool)
                elif result.get("status") == "unavailable":
                    update_job(
                        job_id,
                        status="unavailable",
                        progress=0,
                        finished_at=_now(),
                        result=result,
                        error=result.get("error") or result.get("reason") or "Backend chưa khả dụng.",
                        message=result.get("next_action") or "Backend chưa khả dụng.",
                        next_action=result.get("next_action"),
                    )
                else:
                    update_job(job_id, status="failed", progress=0, finished_at=_now(), result=result, error=result.get("error") or result.get("reason") or "Worker không hoàn tất.", message="Không thể hoàn tất tác vụ.", next_action=result.get("next_action"))
        except Exception as exc:  # pragma: no cover - guards background threads
            update_job(job_id, status="failed", progress=0, finished_at=_now(), error=str(exc), message="Worker Hub gặp lỗi không mong đợi.")
        finally:
            if acquired:
                self._heavy_slot.release()
            with self._lock:
                self._contexts.pop(job_id, None)
                self._pending_cancellations.discard(job_id)
                self._trim_runner_specs_locked()

    def cancel(self, job_id: str) -> tuple[bool, str]:
        with self._lock:
            # Re-read durable state while holding the context lock.  A worker
            # can otherwise finish between an earlier queued snapshot and this
            # cancellation, allowing a completed record to regress back into
            # ``cancelling`` after its context has gone away.
            record = get_job_internal(job_id)
            if record is None:
                return False, "Không tìm thấy job Hub."
            if record.get("status") in TERMINAL_STATUSES:
                return False, "Job này đã kết thúc."
            context = self._contexts.get(job_id)
            if context is not None:
                context.cancel()
                latest = get_job_internal(job_id)
                if latest is not None and latest.get("status") in TERMINAL_STATUSES:
                    return False, "Job này đã kết thúc."
            elif record.get("status") == "queued":
                self._pending_cancellations.add(job_id)
            else:
                return False, "Job không còn context worker trong phiên Hub hiện tại; hãy tạo lại tác vụ từ workspace."
        update_job(job_id, status="cancelling", message="Đang dừng các process do Hub sở hữu.")
        return True, "Hub đang hủy job và chỉ dừng process do Hub tạo."

    def cancel_all(self) -> tuple[int, list[str]]:
        """Request cancellation for every active job owned by this Hub session."""

        cancelled = 0
        messages: list[str] = []
        for record in active_jobs():
            job_id = record.get("id")
            if not isinstance(job_id, str):
                continue
            ok, message = self.cancel(job_id)
            if ok:
                cancelled += 1
            else:
                messages.append(message)
        return cancelled, messages

    def wait_for_idle(self, timeout_seconds: float = 12.0) -> tuple[bool, list[str]]:
        """Boundedly wait until contexts and durable active statuses are terminal."""

        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        while True:
            with self._lock:
                context_ids = set(self._contexts)
            durable_active = [str(item.get("id")) for item in active_jobs() if item.get("id")]
            if not context_ids and not durable_active:
                return True, []
            if time.monotonic() >= deadline:
                return False, sorted(set(context_ids) | set(durable_active))
            time.sleep(0.05)

    def cancel_all_and_wait(self, timeout_seconds: float = 12.0) -> tuple[bool, str]:
        """Cancel active Hub jobs without force-killing a non-cooperating worker."""

        cancelled, messages = self.cancel_all()
        ok, remaining = self.wait_for_idle(timeout_seconds)
        if ok:
            return True, f"Đã hủy và hoàn tất dừng {cancelled} job Hub."
        detail = ", ".join(remaining[:4]) or "; ".join(messages[:2]) or "job đang dừng"
        return False, f"Hub chưa thể dừng an toàn trong thời hạn; vẫn giữ cửa sổ mở. Job còn hoạt động: {detail}."

    def resume(self, job_id: str) -> tuple[bool, dict[str, Any] | str]:
        record = get_job_internal(job_id)
        if record is None:
            return False, "Không tìm thấy job Hub."
        if record.get("status") not in {"cancelled", "failed", "unavailable"}:
            return False, "Chỉ có thể thử lại job đã hủy, thất bại hoặc chưa khả dụng."
        with self._lock:
            spec = self._runner_specs.get(job_id)
            runner = spec.runner if spec else self._runners.get(job_id)
        payload = record.get("resume_data")
        if runner is None or not isinstance(payload, dict):
            return False, "Job không còn runner trong phiên Hub hiện tại; hãy tạo lại tác vụ từ workspace."
        device = spec.device if spec else record.get("device")
        heavy = spec.heavy if spec else bool(record.get("heavy", True))
        return True, self.submit(str(record.get("tool") or "job"), payload, runner, device=device, heavy=heavy)


job_manager = HubJobManager()
