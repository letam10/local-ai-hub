"""Small in-process job manager for direct Hub workflows.

The API remains intentionally local and lightweight: a job owns only the
processes that it starts, exposes cancellation/resume state, and serializes
heavy GPU work through one configurable slot.  It is not a benchmark runner or
a distributed queue.
"""

from __future__ import annotations

import subprocess
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.services import artifact_store
from src.services.api.jobs import TERMINAL_STATUSES, _publish_result, active_jobs, create_job, get_job_internal, update_job
from src.services.process_manager.managed import terminate_owned_process
from src.services.tool_smoke import (
    has_published_artifact,
    record_completed,
    record_failed,
    record_unavailable,
    requires_published_artifact,
)


Runner = Callable[[dict[str, Any], "JobContext"], dict[str, Any]]
MAX_RUNNER_SPECS = 64
JOB_OUTPUT_SCOPE_TOOLS = frozenset({"frame_interpolate", "run_media_operation"})
_JOB_OUTPUT_SCOPE_ID = re.compile(r"^(?:jobv5_[a-f0-9]{32}|job_[0-9]{8}_[0-9]{6}_[a-f0-9]{8})$")


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
        retry_of: str | None = None,
        attempt: int = 1,
    ) -> dict[str, Any]:
        record = create_job(tool, payload, device=device, resume_data=payload, retry_of=retry_of, attempt=attempt)
        scope_required = tool in JOB_OUTPUT_SCOPE_TOOLS or requires_published_artifact(tool)
        if scope_required:
            scope = artifact_store.begin_job_output_scope(str(record.get("id") or ""))
            if scope is None and isinstance(record.get("id"), str) and _JOB_OUTPUT_SCOPE_ID.fullmatch(record["id"]):
                return update_job(
                    str(record["id"]),
                    status="failed",
                    progress=0,
                    finished_at=_now(),
                    result={"status": "failed", "failure_code": "OUTPUT_SCOPE_UNAVAILABLE"},
                    error="Hub không thể tạo phạm vi output an toàn cho job.",
                    message="Không thể bắt đầu output scope an toàn.",
                    next_action="Kiểm tra quyền Output/Config rồi tạo lại job.",
                ) or record
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
                        artifact_store.finalize_job_output_scope(job_id, terminal_state="cancelled")
                        record_failed(tool, failure_code="CANCELLED")
                        update_job(job_id, status="cancelled", finished_at=_now(), message="Tác vụ đã được hủy trước khi chạy.")
                        return
                    acquired = self._heavy_slot.acquire(timeout=0.2)
            if context.cancelled:
                artifact_store.finalize_job_output_scope(job_id, terminal_state="cancelled")
                record_failed(tool, failure_code="CANCELLED")
                update_job(job_id, status="cancelled", finished_at=_now(), message="Tác vụ đã được hủy trước khi chạy.")
                return
            update_job(job_id, status="running", progress=5, message="Worker Hub đang chạy nền.")
            artifact_store.reconcile_job_output_scopes(active_job_ids={job_id})
            raw_result = runner(payload, context)
            # Cancellation and publication share the context lock.  A cancel
            # that arrives before this critical section prevents publication;
            # a cancel that arrives during it waits until the terminal state
            # is durable, so it cannot leave an orphaned artifact behind.
            with context._lock:
                record = get_job_internal(job_id) or {"id": job_id, "tool": tool}
                scope_state = artifact_store.inspect_job_output_scope(job_id)
                if context.cancelled:
                    cleanup = artifact_store.finalize_job_output_scope(job_id, raw_result, terminal_state="cancelled")
                    result = {"status": "cancelled"}
                    if cleanup.get("status") == "manual_review":
                        result["cleanup_status"] = "manual_review"
                    publish_error = None
                else:
                    scope_result = artifact_store.prepare_job_output_scope(job_id, raw_result) if scope_state is not None else {"status": "no_scope"}
                    if scope_result.get("status") in {"manual_review", "invalid", "unavailable"}:
                        result, publish_error = {
                            "status": "failed",
                            "error": "Output ownership could not be proven; no artifact was published.",
                            "next_action": "Review output ownership and create a new job.",
                        }, "OUTPUT_OWNERSHIP_AMBIGUOUS"
                    else:
                        result, publish_error = _publish_result(raw_result, record)
                if context.cancelled or result.get("status") == "cancelled":
                    if not context.cancelled:
                        cleanup = artifact_store.finalize_job_output_scope(job_id, raw_result, terminal_state="cancelled")
                        if cleanup.get("status") == "manual_review":
                            result["cleanup_status"] = "manual_review"
                    record_failed(tool, failure_code="CANCELLED")
                    update_job(job_id, status="cancelled", progress=0, finished_at=_now(), result=result, message="Tác vụ đã được hủy.")
                elif publish_error is not None:
                    cleanup = artifact_store.finalize_job_output_scope(job_id, raw_result, terminal_state="failed")
                    if cleanup.get("status") == "manual_review":
                        result["cleanup_status"] = "manual_review"
                    record_failed(tool, failure_code="OUTPUT_PUBLISH_FAILED")
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
                    if requires_published_artifact(tool) and not has_published_artifact(result):
                        cleanup = artifact_store.finalize_job_output_scope(job_id, raw_result, terminal_state="failed")
                        if cleanup.get("status") == "manual_review":
                            result["cleanup_status"] = "manual_review"
                        record_failed(tool, failure_code="OUTPUT_MISSING")
                        update_job(
                            job_id,
                            status="failed",
                            progress=0,
                            finished_at=_now(),
                            result={
                                "status": "failed",
                                "failure_code": "OUTPUT_MISSING",
                                "error": "Worker completed without a publishable Hub artifact.",
                            },
                            error="Job completed without a publishable artifact; capability evidence was not recorded.",
                            message="Worker không tạo artifact Hub để publish.",
                            next_action="Kiểm tra output contract rồi tạo lại job.",
                        )
                    else:
                        artifact_store.finalize_job_output_scope(job_id, raw_result, terminal_state="completed", published=True)
                        update_job(job_id, status="completed", progress=100, finished_at=_now(), result=result, message="Hoàn tất.", next_action=result.get("next_action"))
                        record_completed(tool)
                elif result.get("status") == "unavailable":
                    artifact_store.finalize_job_output_scope(job_id, raw_result, terminal_state="unavailable")
                    record_unavailable(tool, failure_code="BACKEND_UNAVAILABLE")
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
                    artifact_store.finalize_job_output_scope(job_id, raw_result, terminal_state="failed")
                    record_failed(tool, failure_code="WORKER_FAILED")
                    update_job(job_id, status="failed", progress=0, finished_at=_now(), result=result, error=result.get("error") or result.get("reason") or "Worker không hoàn tất.", message="Không thể hoàn tất tác vụ.", next_action=result.get("next_action"))
        except Exception as exc:  # pragma: no cover - guards background threads
            artifact_store.finalize_job_output_scope(job_id, None, terminal_state="failed")
            record_failed(tool, failure_code="WORKER_EXCEPTION")
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
        raw_attempt = record.get("attempt", 1)
        attempt = raw_attempt if isinstance(raw_attempt, int) and not isinstance(raw_attempt, bool) and 1 <= raw_attempt <= 9_999 else 1
        return True, self.submit(
            str(record.get("tool") or "job"),
            payload,
            runner,
            device=device,
            heavy=heavy,
            retry_of=job_id,
            attempt=attempt + 1,
        )


job_manager = HubJobManager()
