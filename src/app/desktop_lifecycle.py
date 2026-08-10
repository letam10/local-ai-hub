"""Decision-aware desktop shutdown coordination.

The native shell is intentionally thin.  It asks the loopback API about active
jobs, never terminates an externally managed API, and only authorizes its
``finally`` cleanup after a safe exit decision.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any


ClosePrompt = Callable[[dict[str, Any]], None]
ActiveJobs = Callable[[], int]
CancelAndWait = Callable[[float], tuple[bool, str]]
PrepareClose = Callable[[], tuple[bool, int, str]]
Background = Callable[[], tuple[bool, str]]
Restore = Callable[[], None]
Destroy = Callable[[], None]


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
        self._prepare_close = prepare_close or (lambda: (True, 0, ""))
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

    def _show_prompt(self, *, message: str = "", kind: str = "attention", active_count: int | None = None) -> None:
        try:
            count = max(0, int(self._active_jobs())) if active_count is None else max(0, int(active_count))
        except Exception:
            # Unknown state must be treated as active: the desktop stays open
            # instead of terminating a potentially live owned API tree.
            count = 1
            message = message or "Không thể xác nhận trạng thái job; Hub vẫn được giữ mở an toàn."
            kind = "error"
        self._prompt({"active_jobs": count, "message": message, "kind": kind})

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
            self._show_prompt()
            return False
        try:
            ready, rechecked_active, message = self._prepare_close()
        except Exception:
            ready, rechecked_active, message = False, 1, "Không thể khóa an toàn việc nhận job mới; Hub vẫn được giữ mở."
        if not ready:
            with self._lock:
                self._state = "prompted"
            self._show_prompt(message=message, kind="error", active_count=rechecked_active)
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
