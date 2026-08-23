"""
  FILE NOTE
  - Mục đích: Desktop lifecycle coordination cho Local AI Hub — shutdown state machine, startup race guard, graceful shutdown checkpoint và startup failure UI
  - Liên kết trực tiếp: src/app/main.py, src/app/tray.py, src/services/job_manager/, src/app_config/settings_service.py
  - Vùng ảnh hưởng khi sửa: Shutdown flow (3 choices), tray hide/restore, single-instance mutex, startup error UI, crash-safe state flush
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any


ClosePrompt = Callable[[dict[str, Any]], None]
ActiveJobs = Callable[[], int]
CancelAndWait = Callable[[float], tuple[bool, str]]
PrepareClose = Callable[[], dict[str, Any]]
Background = Callable[[], tuple[bool, str]]
Restore = Callable[[], None]
Destroy = Callable[[], None]
StateFlusher = Callable[[], None]


# ---------------------------------------------------------------------------
# Startup helpers
# ---------------------------------------------------------------------------

def startup_failure_payload(reason: str, next_action: str) -> dict[str, Any]:
    """Build a standardised failure payload for the startup-error UI.

    The dict is consumed by the static HTML fallback screen embedded in
    main.py; it must never contain secrets or raw workstation paths.
    """
    safe_reason = str(reason)[:512].replace("\x00", "")
    safe_action = str(next_action)[:256].replace("\x00", "")
    return {
        "status": "startup_failure",
        "reason": safe_reason,
        "next_action": safe_action,
    }


class StartupRaceGuard:
    """Detect stale PID / port locks left by a previous crashed session.

    This is a source-only contract helper — it never probes live processes
    or open ports; it only inspects the PID file written by the launcher.
    """

    def __init__(self, pid_path: Path) -> None:
        self._pid_path = pid_path

    def read_stale_pid(self) -> int | None:
        """Return the PID recorded in the PID file, or None if absent/corrupt."""
        try:
            text = self._pid_path.read_text(encoding="utf-8").strip()
            pid = int(text)
            return pid if pid > 0 else None
        except (OSError, ValueError):
            return None

    def write_current_pid(self) -> None:
        """Overwrite PID file with the current process PID."""
        try:
            self._pid_path.parent.mkdir(parents=True, exist_ok=True)
            self._pid_path.write_text(str(os.getpid()), encoding="utf-8")
        except OSError:
            pass

    def clear(self) -> None:
        """Remove the PID file on clean shutdown."""
        try:
            self._pid_path.unlink(missing_ok=True)
        except OSError:
            pass

    def is_stale(self) -> bool:
        """Return True if a PID file exists from a previous run.

        Only checks file presence; process liveness is NOT probed so that
        no real subprocess is needed in tests or static validation.
        """
        return self._pid_path.exists()


# ---------------------------------------------------------------------------
# Graceful shutdown coordinator
# ---------------------------------------------------------------------------

class GracefulShutdownCoordinator:
    """Ensure critical state is flushed before process exit.

    Registered flushers are called in registration order.  Any that raise
    are logged but do not prevent remaining flushers from running.
    """

    def __init__(self) -> None:
        self._flushers: list[tuple[str, StateFlusher]] = []
        self._lock = threading.Lock()

    def register(self, name: str, flusher: StateFlusher) -> None:
        """Register a named state-flush callback."""
        with self._lock:
            self._flushers.append((name, flusher))

    def save_checkpoint(self) -> dict[str, Any]:
        """Call all registered flushers and return a summary.

        Returns ``{"flushed": [names], "errors": {name: str}}``
        """
        with self._lock:
            flushers = list(self._flushers)

        flushed: list[str] = []
        errors: dict[str, str] = {}
        for name, fn in flushers:
            try:
                fn()
                flushed.append(name)
            except Exception as exc:  # pragma: no cover - defensive
                errors[name] = str(exc)

        return {"flushed": flushed, "errors": errors}




class DesktopCloseController:
    """Serialize close choices without ever force-killing a stuck Hub job."""

    def __init__(
        self,
        active_jobs: ActiveJobs,
        cancel_and_wait: CancelAndWait,
        prompt: ClosePrompt,
        *,
        cancel_timeout_seconds: float = 12.0,
        prepare_close: PrepareClose | None = None,
    ) -> None:
        self._active_jobs = active_jobs
        self._cancel_and_wait = cancel_and_wait
        self._prompt = prompt
        self._cancel_timeout_seconds = max(1.0, float(cancel_timeout_seconds))
        self._prepare_close = prepare_close or (lambda: {"status": "ready_to_close", "verification": "verified", "active_jobs": 0, "can_cancel": False, "message": ""})
        self._lock = threading.RLock()
        self._state = "interactive"
        self._cleanup_allowed = False

    @property
    def cleanup_allowed(self) -> bool:
        with self._lock:
            return self._cleanup_allowed

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def _show_prompt(self, *, message: str = "", kind: str = "attention", active_count: int | None = None, verification: str = "verified", can_cancel: bool = False) -> None:
        if verification != "verified" or active_count is None:
            count = None
            can_cancel = False
            message = message or "Không thể xác minh trạng thái tác vụ; Hub chưa đóng để đảm bảo an toàn."
            kind = "error"
        else:
            count = max(0, int(active_count))
            can_cancel = bool(can_cancel and count > 0)
        self._prompt({"active_jobs": count, "verification": verification, "can_cancel": can_cancel, "message": message, "kind": kind})

    def request_window_close(self) -> bool:
        """Return True only when pywebview may actually destroy the window."""

        cancelling = False
        with self._lock:
            if self._cleanup_allowed:
                return True
            if self._state == "cancelling":
                cancelling = True
        if cancelling:
            self._show_prompt(message="Hub đang yêu cầu hủy job. Cửa sổ sẽ chỉ đóng sau khi các worker trở thành terminal.", kind="progress")
            return False
        try:
            active = max(0, int(self._active_jobs()))
        except Exception:
            active = 1
        if active:
            with self._lock:
                self._state = "prompted"
            # The active-count callback is an owned, in-process verification
            # boundary for this controller. Preserve the count and expose the
            # cancel choice only for this verified owned path.
            self._show_prompt(active_count=active, verification="verified", can_cancel=True)
            return False
        try:
            prepared = self._prepare_close()
            # Keep old in-process fixtures source-compatible while the public
            # desktop path uses the typed verification result below.
            if isinstance(prepared, tuple) and len(prepared) == 3:
                legacy_ready, legacy_active, legacy_message = prepared
                prepared = {
                    "status": "ready_to_close" if legacy_ready else "active_jobs",
                    "verification": "verified",
                    "active_jobs": max(0, int(legacy_active)),
                    "can_cancel": not bool(legacy_ready) and int(legacy_active) > 0,
                    "message": str(legacy_message or ""),
                }
        except Exception:
            prepared = {"status": "unknown", "verification": "error", "active_jobs": None, "can_cancel": False, "message": "Không thể xác minh trạng thái tác vụ; Hub chưa đóng để đảm bảo an toàn."}
        verification = prepared.get("verification") if isinstance(prepared, dict) else "error"
        rechecked_active = prepared.get("active_jobs") if isinstance(prepared, dict) else None
        ready = isinstance(prepared, dict) and prepared.get("status") == "ready_to_close" and verification == "verified" and rechecked_active == 0
        message = str(prepared.get("message") or "") if isinstance(prepared, dict) else ""
        if not ready:
            with self._lock:
                self._state = "prompted"
            self._show_prompt(message=message, kind="error", active_count=rechecked_active if isinstance(rechecked_active, int) and not isinstance(rechecked_active, bool) else None, verification=str(verification or "error"), can_cancel=bool(prepared.get("can_cancel")) if isinstance(prepared, dict) else False)
            return False
        with self._lock:
            self._state = "exiting"
            self._cleanup_allowed = True
        return True

    def return_to_hub(self) -> dict[str, str]:
        with self._lock:
            if self._cleanup_allowed:
                return {"status": "error", "message": "Hub đang thoát an toàn; không thể quay lại phiên tương tác."}
            if self._state == "cancelling":
                return {"status": "pending", "message": "Hub đang hủy job; không thể thay đổi quyết định cho đến khi worker trở thành terminal."}
            self._state = "interactive"
        return {"status": "completed", "message": "Đã quay lại Hub; các job tiếp tục theo trạng thái hiện tại."}

    def cancel_jobs_and_exit(self, destroy: Destroy) -> dict[str, str]:
        """Run a bounded cancellation in a worker and destroy only on success."""

        with self._lock:
            if self._cleanup_allowed:
                return {"status": "completed", "message": "Hub đang thoát an toàn."}
            if self._state == "cancelling":
                return {"status": "pending", "message": "Hub đã đang chờ job kết thúc."}
            self._state = "cancelling"

        def complete() -> None:
            ok, message = self._cancel_and_wait(self._cancel_timeout_seconds)
            with self._lock:
                if ok:
                    self._state = "exiting"
                    self._cleanup_allowed = True
                else:
                    self._state = "prompted"
            if ok:
                destroy()
            else:
                self._show_prompt(message=message, kind="error")

        threading.Thread(target=complete, name="LocalAIHub-close-jobs", daemon=True).start()
        return {"status": "pending", "message": "Đang hủy các job Hub và chờ worker dừng an toàn…"}

    def keep_running_in_background(self, background: Background) -> dict[str, str]:
        """Hide only after a tray/restore surface has been created successfully."""

        with self._lock:
            if self._cleanup_allowed:
                return {"status": "error", "message": "Hub đang thoát nên không thể chuyển sang chạy nền."}
            if self._state == "cancelling":
                return {"status": "pending", "message": "Hub đang hủy job; không thể ẩn cửa sổ trong lúc cancellation chưa hoàn tất."}
        ok, message = background()
        if not ok:
            self._show_prompt(message=message, kind="error")
            return {"status": "error", "message": message}
        with self._lock:
            self._state = "background"
        return {"status": "completed", "message": message}

    def restore_from_background(self, restore: Restore) -> dict[str, str]:
        restore()
        with self._lock:
            if not self._cleanup_allowed:
                self._state = "interactive"
        return {"status": "completed", "message": "Đã khôi phục cửa sổ Local AI Hub."}

    def request_exit_from_background(self, restore: Restore, destroy: Destroy) -> dict[str, str]:
        """Tray Exit restores first, then follows the exact normal close gate."""

        restore()
        if self.request_window_close():
            destroy()
            return {"status": "completed", "message": "Hub đang thoát an toàn."}
        return {"status": "prompted", "message": "Hub đã được khôi phục để xác nhận cách xử lý job đang hoạt động."}
