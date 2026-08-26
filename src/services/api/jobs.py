"""Bounded, durable public job state for the local Hub control plane.

Runner callables and retry payloads intentionally remain process-local in the
job manager.  The hot JSON file keeps active work plus a bounded terminal
history; older terminal entries move to an ignored cold archive.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.services import artifact_store
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
_MANAGED_JOB_ID = re.compile(r"jobv5_[a-f0-9]{32}")
_MANAGED_ARTIFACT_ID = re.compile(r"artifact_[a-f0-9]{32}")
_MANAGED_TRANSACTION_ID = re.compile(r"artifact_tx_[a-f0-9]{32}")
_MANAGED_FINGERPRINT = re.compile(r"[a-f0-9]{64}")
_MANAGED_ADAPTER_ID = re.compile(r"[a-z][a-z0-9_.-]{0,63}")
_MANAGED_DESCRIPTOR_SUMMARY = {
    "adapter_id": "media.video_grade.v1",
    "operation": "run",
    "reconstructable": True,
    "resources": {"cpu_slots": 1, "gpu_slots": 0, "ram_mb": 0, "disk_mb": 0, "exclusive_group": None},
}

_lock = threading.RLock()
_jobs: dict[str, dict[str, Any]] = {}


def _detached_json(value: Any) -> Any:
    """Return a JSON-detached copy without importing the job-manager package."""

    return json.loads(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_RESULT_SCALAR_KEYS = {
    "operation",
    "backend",
    "model",
    "audio",
    "container",
    "rate_control",
    "target_fps",
    "scale",
    "tool",
    "reason",
    "next_action",
    "error",
    "message",
    "failure_code",
}
def _job_fingerprint(record: dict[str, Any]) -> str:
    """Return an opaque legacy-job binding for produced artifact provenance."""

    payload = {
        "contract_version": "job.v2",
        "id": str(record.get("id") or ""),
        "tool": str(record.get("tool") or ""),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _adapter_id(record: dict[str, Any]) -> str:
    """Keep the provenance adapter identifier bounded and path-free."""

    value = str(record.get("tool") or "hub_job").lower()
    normalized = "".join(char if char.isalnum() or char in "_.-" else "_" for char in value)
    if not normalized or not normalized[0].isalpha():
        normalized = f"hub_{normalized}"
    return normalized[:64]


def _output_candidates(value: object) -> list[Path]:
    """Extract bounded worker output fields; never inspect arbitrary data.

    ComfyUI uses ``outputs`` for paths copied from prompt history, while older
    direct workers use ``output`` or ``files``.  Every candidate still passes
    through the artifact store's Hub-root containment and provenance checks.
    """

    if not isinstance(value, dict):
        return []
    candidates: list[Path] = []
    output = value.get("output")
    if isinstance(output, (str, Path)) and str(output):
        candidates.append(Path(str(output)))
    files = value.get("files")
    if isinstance(files, list):
        for item in files[:64]:
            if isinstance(item, (str, Path)) and str(item):
                candidates.append(Path(str(item)))
    outputs = value.get("outputs")
    if isinstance(outputs, list):
        for item in outputs[:64]:
            if isinstance(item, (str, Path)) and str(item):
                candidates.append(Path(str(item)))
    return candidates


def _safe_result_scalar(value: object, *, key: str) -> object | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return max(-1_000_000, min(1_000_000, value))
    if isinstance(value, float):
        return value if value == value and abs(value) <= 1_000_000 else None
    if isinstance(value, str):
        # publicize strips a local path from error/reason text while retaining
        # ordinary bounded worker guidance.  The result is capped before it
        # reaches jobs.json or the browser projection.
        return str(publicize(value, key=key))[:600]
    return None


def _publish_result(result: object, record: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Publish worker outputs as opaque, job-bound artifacts.

    Video/image workers are allowed to return a Hub-owned output path because
    that path is an internal hand-off only.  It is removed before persistence
    and replaced by artifact metadata carrying the terminal job provenance.
    Any path outside the artifact store's managed roots fails closed.
    """

    if not isinstance(result, dict):
        return {"status": "failed", "error": "Worker trả về dữ liệu không hợp lệ."}, "invalid_result"
    status = result.get("status")
    if status not in {"completed", "failed", "unavailable", "cancelled"}:
        return {"status": "failed", "error": "Worker trả về trạng thái không hợp lệ."}, "invalid_status"
    safe: dict[str, Any] = {"status": status}
    for key in _RESULT_SCALAR_KEYS:
        if key in result:
            value = _safe_result_scalar(result.get(key), key=key)
            if value is not None:
                safe[key] = value
    if status != "completed":
        return safe, None

    if record.get("tool") == "transcribe_media":
        if "srt" in result or not isinstance(result.get("files"), list):
            return {
                "status": "failed",
                "error": "Whisper transcript phải trả về batch JSON và SRT opaque.",
            }, "whisper_output_contract"

    for output_field in ("files", "outputs"):
        declared = result.get(output_field)
        if isinstance(declared, list) and len(declared) > 64:
            return {"status": "failed", "error": "Worker tạo quá nhiều output cho một job."}, "output_count"
    candidates = _output_candidates(result)
    if len(candidates) > 64:
        return {"status": "failed", "error": "Worker tạo quá nhiều output cho một job."}, "output_count"
    if record.get("tool") == "transcribe_media":
        suffixes = sorted(candidate.suffix.casefold() for candidate in candidates)
        if len(candidates) != 2 or suffixes != [".json", ".srt"]:
            return {
                "status": "failed",
                "error": "Whisper transcript phải publish đủ JSON và SRT opaque.",
            }, "whisper_output_contract"
    if not candidates:
        # Metadata-only operations such as probe are valid completions.
        return safe, None
    provenance = {
        "job_id": str(record.get("id") or ""),
        "job_spec_fingerprint": _job_fingerprint(record),
        "adapter_id": _adapter_id(record),
        "attempt": 1,
        "status": "completed",
    }
    for candidate in candidates:
        if not isinstance(candidate, Path):
            return {"status": "failed", "error": "Output không thể publish thành artifact Hub hợp lệ."}, "output_publish"
    try:
        artifacts = artifact_store.register_worker_outputs(candidates, provenance=provenance)
    except Exception:
        artifacts = None
    if not isinstance(artifacts, list) or not artifacts or any(
        not isinstance(artifact, dict) or artifact.get("provenance") != provenance
        for artifact in artifacts
    ):
        return {"status": "failed", "error": "Output không thể publish thành artifact Hub hợp lệ."}, "output_publish"
    safe["artifacts"] = artifacts
    return safe, None


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
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self._write = write
        self._clock = clock
        self._debounce = max(0.0, float(debounce_seconds))
        self._timer_factory = timer_factory
        self._on_error = on_error
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
        try:
            self._invoke_write()
        except Exception as exc:
            if self._on_error is None:
                raise
            # V5 stores opt into a bounded error sink so their timer does not
            # leak an unhandled daemon-thread traceback.
            self._on_error(exc)

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
    failure_code = record.get("failure_code")
    if not isinstance(failure_code, str):
        raw_result = record.get("result")
        failure_code = raw_result.get("failure_code") if isinstance(raw_result, dict) else None
    if isinstance(failure_code, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", failure_code):
        result["error_code"] = failure_code
    resumable = bool(record.get("resume_data")) and status in RETRYABLE_STATUSES and record.get("resume_available") is True
    result["resumable"] = resumable
    if (status in RETRYABLE_STATUSES or status == "interrupted") and not resumable and not result.get("next_action"):
        result["next_action"] = "Job thuộc phiên Hub trước hoặc runner không còn; hãy tạo lại tác vụ từ workspace."
    return result


def get_job(job_id: str) -> dict[str, Any] | None:
    record = get_job_internal(job_id)
    return public_job(record) if record else None


def delete_job(job_id: str) -> dict[str, Any]:
    """Soft-remove one terminal history record without touching artifacts."""

    if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:@_-]{0,119}", job_id):
        return {"status": "invalid", "code": "JOB_ID_INVALID", "message": "Mã tác vụ không hợp lệ."}
    with _lock:
        record = _jobs.get(job_id)
        if record is None:
            return {"status": "not_found", "code": "JOB_NOT_FOUND", "message": "Không tìm thấy tác vụ trong lịch sử."}
        current = str(record.get("status") or "")
        if current in ACTIVE_STATUSES:
            return {"status": "rejected", "code": "ACTIVE_JOB_NOT_DELETABLE", "message": "Tác vụ đang chạy không thể xóa khỏi lịch sử."}
        if current not in TERMINAL_STATUSES:
            return {"status": "rejected", "code": "JOB_NOT_TERMINAL", "message": "Chỉ tác vụ đã kết thúc mới có thể xóa."}
        removed = _jobs.pop(job_id)
        try:
            _save(immediate=True)
        except Exception:
            _jobs[job_id] = removed
            return {"status": "unavailable", "code": "JOB_HISTORY_PERSISTENCE_FAILED", "message": "Không thể lưu thay đổi lịch sử; bản ghi vẫn được giữ nguyên."}
        return {"status": "deleted", "job_id": job_id, "artifacts_preserved": True, "message": "Đã xóa tác vụ khỏi lịch sử; file đầu ra và artifact vẫn được giữ nguyên."}


def clear_terminal_history() -> dict[str, Any]:
    """Remove only terminal legacy history records; never deletes artifacts."""

    with _lock:
        removed = {job_id: record for job_id, record in _jobs.items() if str(record.get("status") or "") in TERMINAL_STATUSES}
        if not removed:
            return {"status": "completed", "removed_count": 0, "artifacts_preserved": True, "message": "Không có tác vụ đã kết thúc để xóa."}
        for job_id in removed:
            _jobs.pop(job_id, None)
        try:
            _save(immediate=True)
        except Exception:
            _jobs.update(removed)
            return {"status": "unavailable", "code": "JOB_HISTORY_PERSISTENCE_FAILED", "message": "Không thể lưu thay đổi lịch sử; các bản ghi vẫn được giữ nguyên."}
        return {"status": "completed", "removed_count": len(removed), "artifacts_preserved": True, "message": f"Đã xóa {len(removed)} tác vụ đã kết thúc khỏi lịch sử; file đầu ra và artifact vẫn được giữ nguyên."}


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


class DurableStoreHealthError(RuntimeError):
    """Fail-closed durable-store health error without filesystem reflection."""

    def __init__(self, code: str, action: str = "Restore or repair durable job state through an administrator-controlled recovery procedure.") -> None:
        super().__init__(code)
        self.code = code
        self.action = action

    def public(self) -> dict[str, str]:
        return {"status": "unavailable", "code": self.code, "action": self.action}


class DurableJobStore:
    """Small atomic JSON store used by the V5 declarative work engine.

    It is intentionally separate from the legacy ``job.v2`` module state so
    the V5 engine can be constructed with a test-root path and cannot persist
    process-local runners or arbitrary Python values.  All callers receive
    detached JSON copies.  Progress-only updates share the existing
    coalescing writer; state transitions and shutdown flush immediately.
    """

    MAX_RECORD_BYTES = 128 * 1024

    def __init__(
        self,
        path: Path,
        *,
        history_limit: int = 256,
        clock: Callable[[], float] = time.monotonic,
        debounce_seconds: float = PROGRESS_DEBOUNCE_SECONDS,
        timer_factory: Callable[[float, Callable[[], None]], threading.Timer] = threading.Timer,
    ) -> None:
        self.path = Path(path)
        self.history_limit = max(1, min(1000, int(history_limit)))
        self._records: dict[str, dict[str, Any]] = {}
        self._pending_records: dict[str, dict[str, Any]] | None = None
        self._lock = threading.RLock()
        self._disk_fingerprint: str | None = None
        self._health_error: DurableStoreHealthError | None = None
        self._writer = CoalescingWriter(
            self._flush_pending,
            clock=clock,
            debounce_seconds=debounce_seconds,
            timer_factory=timer_factory,
            on_error=self._record_background_error,
        )
        self._load_records()

    @staticmethod
    def _copy(value: Any) -> Any:
        try:
            encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("Durable job data must be JSON serializable.") from exc
        if len(encoded.encode("utf-8")) > DurableJobStore.MAX_RECORD_BYTES:
            raise ValueError("Durable job record exceeds the bounded storage limit.")
        return json.loads(encoded)

    @staticmethod
    def _sort_key(record: dict[str, Any]) -> str:
        return str(record.get("finished_at") or record.get("updated_at") or record.get("created_at") or "")

    @staticmethod
    def _fingerprint(value: bytes) -> str:
        return hashlib.sha256(value).hexdigest()

    def _read_existing_bytes(self) -> bytes | None:
        try:
            return self.path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise DurableStoreHealthError("DURABLE_STORE_UNREADABLE") from exc

    @staticmethod
    def _validate_root(raw: bytes) -> dict[str, Any]:
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DurableStoreHealthError("DURABLE_STORE_UNREADABLE") from exc
        if type(value) is not dict:
            raise DurableStoreHealthError("DURABLE_STORE_INVALID_ROOT")
        return value

    def _verify_disk_locked(self) -> None:
        """Refuse an overwrite if the original file changed or became unreadable."""

        current = self._read_existing_bytes()
        current_fingerprint = self._fingerprint(current) if current is not None else None
        if current_fingerprint == self._disk_fingerprint:
            return
        if current is None and self._disk_fingerprint is None:
            return
        if current is not None:
            # Different but valid bytes still require a human-controlled
            # resolution; malformed/non-object content gets a more specific
            # fail-closed code and is never replaced.
            self._validate_root(current)
        raise DurableStoreHealthError("DURABLE_STORE_CHANGED")

    @staticmethod
    def _copy_health_error(error: DurableStoreHealthError) -> DurableStoreHealthError:
        return DurableStoreHealthError(error.code, error.action)

    def _fail_locked(self, error: Exception) -> DurableStoreHealthError:
        safe = error if isinstance(error, DurableStoreHealthError) else DurableStoreHealthError("DURABLE_STORE_PERSISTENCE_FAILED")
        self._health_error = self._copy_health_error(safe)
        # Pending changes were never committed.  Discarding them makes the
        # committed in-memory view match the preserved on-disk state.
        self._pending_records = None
        return self._copy_health_error(safe)

    def _check_health_locked(self) -> None:
        if self._health_error is not None:
            raise self._copy_health_error(self._health_error)
        try:
            self._verify_disk_locked()
        except DurableStoreHealthError as exc:
            raise self._fail_locked(exc) from None

    def _record_background_error(self, error: Exception) -> None:
        """Record timer failures instead of leaking an unhandled traceback."""

        with self._lock:
            self._fail_locked(error)

    def _load_records(self) -> None:
        raw_bytes = self._read_existing_bytes()
        if raw_bytes is None:
            return
        raw = self._validate_root(raw_bytes)
        with self._lock:
            for job_id, record in raw.items():
                if not isinstance(job_id, str) or type(record) is not dict:
                    raise DurableStoreHealthError("DURABLE_STORE_INVALID_RECORD")
                try:
                    copied = self._copy(record)
                except ValueError as exc:
                    raise DurableStoreHealthError("DURABLE_STORE_INVALID_RECORD") from exc
                if copied.get("id") != job_id:
                    raise DurableStoreHealthError("DURABLE_STORE_INVALID_RECORD")
                self._records[job_id] = copied
            self._disk_fingerprint = self._fingerprint(raw_bytes)
            self._trim_records_locked(self._records)

    def _trim_records_locked(self, records: dict[str, dict[str, Any]]) -> None:
        terminal = [
            record
            for record in records.values()
            if str(record.get("status")) in {"completed", "failed", "unavailable", "interrupted"}
        ]
        terminal.sort(key=self._sort_key, reverse=True)
        for record in terminal[self.history_limit :]:
            job_id = record.get("id")
            if isinstance(job_id, str):
                records.pop(job_id, None)

    def _write_snapshot_locked(self, records: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Atomically persist a candidate without changing committed memory first."""

        self._check_health_locked()
        payload = self._copy({job_id: records[job_id] for job_id in sorted(records)})
        self._trim_records_locked(payload)
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(encoded)
            # Re-check immediately before replacement so a later corrupt write
            # cannot be silently overwritten by this store.
            self._check_health_locked()
            temporary.replace(self.path)
            self._disk_fingerprint = self._fingerprint(encoded.encode("utf-8"))
            return payload
        except DurableStoreHealthError:
            temporary.unlink(missing_ok=True)
            raise
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise DurableStoreHealthError("DURABLE_STORE_PERSISTENCE_FAILED") from exc

    def _flush_pending(self) -> None:
        """Commit one pending snapshot or fail it closed without divergence."""

        with self._lock:
            if self._pending_records is None:
                self._check_health_locked()
                return
            try:
                committed = self._write_snapshot_locked(self._pending_records)
            except DurableStoreHealthError as exc:
                raise self._fail_locked(exc) from None
            except Exception as exc:
                raise self._fail_locked(exc) from None
            self._records = committed
            self._pending_records = None

    def put(self, record: dict[str, Any]) -> dict[str, Any]:
        copied = self._copy(record)
        job_id = copied.get("id")
        if not isinstance(job_id, str) or not job_id:
            raise ValueError("Durable job record requires an opaque ID.")
        with self._lock:
            self._check_health_locked()
            candidate = self._copy(self._pending_records if self._pending_records is not None else self._records)
            if job_id in candidate:
                raise ValueError("Durable job ID already exists.")
            candidate[job_id] = copied
            self._pending_records = candidate
        self._writer.request(immediate=True)
        with self._lock:
            self._check_health_locked()
            committed = self._records.get(job_id)
            return self._copy(committed) if committed is not None else self._copy(copied)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            self._check_health_locked()
            record = self._records.get(job_id)
            return self._copy(record) if record is not None else None

    def records(self) -> list[dict[str, Any]]:
        with self._lock:
            self._check_health_locked()
            return [self._copy(self._records[job_id]) for job_id in sorted(self._records)]

    def update(self, job_id: str, changes: dict[str, Any], *, progress_only: bool = False) -> dict[str, Any] | None:
        copied = self._copy(changes)
        with self._lock:
            self._check_health_locked()
            candidate = self._copy(self._pending_records if self._pending_records is not None else self._records)
            record = candidate.get(job_id)
            if record is None:
                return None
            record.update(copied)
            result = self._copy(record)
            self._pending_records = candidate
        self._writer.request(immediate=not progress_only)
        with self._lock:
            self._check_health_locked()
            committed = self._records.get(job_id)
            # A delayed progress update remains private staging data until the
            # atomic write succeeds; callers never receive it as durable.
            return self._copy(committed) if committed is not None else result

    @staticmethod
    def _managed_link(value: object) -> dict[str, Any] | None:
        if type(value) is not dict or set(value) != {"artifact_id", "transaction_id", "provenance"}:
            return None
        artifact_id = value.get("artifact_id")
        transaction_id = value.get("transaction_id")
        provenance = value.get("provenance")
        if (
            not isinstance(artifact_id, str)
            or not _MANAGED_ARTIFACT_ID.fullmatch(artifact_id)
            or not isinstance(transaction_id, str)
            or not _MANAGED_TRANSACTION_ID.fullmatch(transaction_id)
            or type(provenance) is not dict
            or set(provenance) != {"job_id", "job_spec_fingerprint", "adapter_id", "attempt", "status"}
        ):
            return None
        if (
            not isinstance(provenance.get("job_id"), str)
            or not _MANAGED_JOB_ID.fullmatch(provenance["job_id"])
            or not isinstance(provenance.get("job_spec_fingerprint"), str)
            or not _MANAGED_FINGERPRINT.fullmatch(provenance["job_spec_fingerprint"])
            or not isinstance(provenance.get("adapter_id"), str)
            or not _MANAGED_ADAPTER_ID.fullmatch(provenance["adapter_id"])
            or provenance.get("adapter_id") != "media.video_grade.v1"
            or isinstance(provenance.get("attempt"), bool)
            or not isinstance(provenance.get("attempt"), int)
            or not 1 <= provenance["attempt"] <= 10_000
            or provenance.get("status") != "completed"
        ):
            return None
        return {
            "artifact_id": artifact_id,
            "transaction_id": transaction_id,
            "provenance": {
                "job_id": provenance["job_id"],
                "job_spec_fingerprint": provenance["job_spec_fingerprint"],
                "adapter_id": provenance["adapter_id"],
                "attempt": provenance["attempt"],
                "status": "completed",
            },
        }

    def commit_managed_artifact(
        self,
        job_id: str,
        artifact_id: str,
        transaction_id: str,
        provenance: dict[str, Any],
    ) -> dict[str, Any]:
        """CAS-link one exact hidden artifact to an unchanged completed job."""

        link = self._managed_link({
            "artifact_id": artifact_id,
            "transaction_id": transaction_id,
            "provenance": provenance,
        })
        if (
            not isinstance(job_id, str)
            or not _MANAGED_JOB_ID.fullmatch(job_id)
            or link is None
            or link["provenance"]["job_id"] != job_id
        ):
            raise DurableStoreHealthError("DURABLE_ARTIFACT_INVALID", "Create a new managed output after server validation.")
        with self._lock:
            self._check_health_locked()
            candidate = self._copy(self._pending_records if self._pending_records is not None else self._records)
            record = candidate.get(job_id)
            if not isinstance(record, dict) or record.get("id") != job_id or record.get("status") != "completed":
                raise DurableStoreHealthError("DURABLE_ARTIFACT_UNAVAILABLE", "Create a new managed output after the job is completed.")
            if (
                record.get("job_spec_fingerprint") != link["provenance"]["job_spec_fingerprint"]
                or not isinstance(record.get("job_spec_fingerprint"), str)
                or not _MANAGED_FINGERPRINT.fullmatch(record["job_spec_fingerprint"])
                or record.get("descriptor_summary") != _MANAGED_DESCRIPTOR_SUMMARY
                or record.get("attempt") != link["provenance"]["attempt"]
            ):
                raise DurableStoreHealthError("DURABLE_ARTIFACT_CONFLICT", "Create a new managed output after server validation.")
            artifacts = record.get("artifacts")
            if type(artifacts) is not list or any(not isinstance(item, str) or not _MANAGED_ARTIFACT_ID.fullmatch(item) for item in artifacts):
                raise DurableStoreHealthError("DURABLE_ARTIFACT_CONFLICT", "Create a new managed output after server validation.")
            existing_value = record.get("managed_artifact")
            existing = self._managed_link(existing_value) if existing_value is not None else None
            if existing_value is not None and existing is None:
                raise DurableStoreHealthError("DURABLE_ARTIFACT_CONFLICT", "Create a new managed output after server validation.")
            if existing is not None:
                if existing == link and link["artifact_id"] in artifacts:
                    return _detached_json(existing)
                raise DurableStoreHealthError("DURABLE_ARTIFACT_CONFLICT", "Create a new managed output after server validation.")
            if artifacts:
                raise DurableStoreHealthError("DURABLE_ARTIFACT_CONFLICT", "Create a new managed output after server validation.")
            record["managed_artifact"] = link
            record["artifacts"] = [link["artifact_id"]]
            try:
                committed = self._write_snapshot_locked(candidate)
            except DurableStoreHealthError as exc:
                raise self._fail_locked(exc) from None
            except Exception as exc:
                raise self._fail_locked(exc) from None
            self._records = committed
            self._pending_records = None
            return _detached_json(link)

    def get_managed_artifact_link(self, job_id: str) -> dict[str, Any] | None:
        if not isinstance(job_id, str) or not _MANAGED_JOB_ID.fullmatch(job_id):
            return None
        with self._lock:
            self._check_health_locked()
            record = self._records.get(job_id)
            link = self._managed_link(record.get("managed_artifact")) if isinstance(record, dict) else None
            return _detached_json(link) if link is not None else None

    def remove_managed_artifact_link(self, job_id: str, artifact_id: str, transaction_id: str) -> bool:
        """Remove only an exact managed link during fail-closed reconciliation."""

        if (
            not isinstance(job_id, str)
            or not _MANAGED_JOB_ID.fullmatch(job_id)
            or not isinstance(artifact_id, str)
            or not _MANAGED_ARTIFACT_ID.fullmatch(artifact_id)
            or not isinstance(transaction_id, str)
            or not _MANAGED_TRANSACTION_ID.fullmatch(transaction_id)
        ):
            return False
        with self._lock:
            self._check_health_locked()
            candidate = self._copy(self._pending_records if self._pending_records is not None else self._records)
            record = candidate.get(job_id)
            link = self._managed_link(record.get("managed_artifact")) if isinstance(record, dict) else None
            if link is None or link["artifact_id"] != artifact_id or link["transaction_id"] != transaction_id:
                return False
            record.pop("managed_artifact", None)
            artifacts = record.get("artifacts") if isinstance(record.get("artifacts"), list) else []
            record["artifacts"] = [item for item in artifacts if item != artifact_id]
            try:
                committed = self._write_snapshot_locked(candidate)
            except DurableStoreHealthError as exc:
                raise self._fail_locked(exc) from None
            except Exception as exc:
                raise self._fail_locked(exc) from None
            self._records = committed
            self._pending_records = None
            return True

    def flush(self) -> None:
        with self._lock:
            self._check_health_locked()
        self._writer.flush()
        with self._lock:
            self._check_health_locked()

    def health(self) -> dict[str, str]:
        """Return a bounded store-health projection without a local path."""

        with self._lock:
            try:
                self._check_health_locked()
            except DurableStoreHealthError as exc:
                return exc.public()
        return {"status": "available"}

    def close(self) -> None:
        """Flush without starting or stopping any external process."""

        self.flush()
