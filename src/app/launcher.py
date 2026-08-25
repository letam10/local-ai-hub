"""
/*
  FILE NOTE
  - Mục đích: Desktop launcher entrypoint cho Local AI Hub — điều phối khởi động không console, kiểm tra môi trường, và chuyển tiếp vào main desktop shell
  - Liên kết trực tiếp: src/app/main.py, src/app/bootstrap.py, src/services/process_manager/windows.py, scripts/update_managed_shortcuts.ps1
  - Vùng ảnh hưởng khi sửa: Toàn bộ quá trình khởi động ứng dụng desktop từ shortcut Windows hoặc dòng lệnh
*/
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.app.bootstrap import bootstrap
from src.app.main import DesktopBridge, main
from src.app.update_bridge import install_update_bridge


def _wait_for_restart_parent() -> None:
    """Let a newly launched payload wait until the previous desktop exits."""

    raw = os.environ.pop("LOCALAIHUB_RESTART_WAIT_PID", "")
    try:
        pid = int(raw)
    except (TypeError, ValueError):
        return
    if pid <= 0 or pid == os.getpid():
        return
    if os.name == "nt":
        try:
            import ctypes

            synchronize = 0x00100000
            wait_object_0 = 0
            timeout = 45_000
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(synchronize, False, pid)
            if handle:
                try:
                    result = kernel32.WaitForSingleObject(handle, timeout)
                    if result == wait_object_0:
                        return
                finally:
                    kernel32.CloseHandle(handle)
        except (AttributeError, OSError, ValueError):
            pass
        return
    deadline = time.monotonic() + 45.0
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.1)


def launch() -> int:
    """Bootstrap managed directories and run the native desktop shell."""
    _wait_for_restart_parent()
    install_update_bridge(DesktopBridge)
    try:
        bootstrap()
    except Exception as exc:
        print(f"Bootstrap initialization failed: {exc}", file=sys.stderr)
    return main()


if __name__ == "__main__":
    raise SystemExit(launch())
