"""Desktop shell for the one Local AI Hub frontend.

The browser and desktop paths intentionally share ``/ui/``.  This module only
owns the native window; capability and runtime state remain in the loopback API.
"""

from __future__ import annotations

import html
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from src.services.process_manager.managed import terminate_owned_process
from src.services.process_manager.windows import popen_hidden, startup_mutex

ROOT = Path(__file__).resolve().parents[2]
HOST = "127.0.0.1"
PORT = 8765
UI_URL = f"http://{HOST}:{PORT}/ui/"
API_STARTUP_MUTEX = r"Local\LocalAIHub.ApiStartup.v1"
_api_process: subprocess.Popen[object] | None = None
_api_process_lock = threading.RLock()
_shutdown_started = False


def _api_ready() -> bool:
    try:
        with urllib.request.urlopen(f"http://{HOST}:{PORT}/health", timeout=0.35) as response:
            return response.status == 200
    except OSError:
        return False


def _wait_for_api(deadline: float) -> bool:
    while time.monotonic() < deadline:
        if _api_ready():
            return True
        time.sleep(0.2)
    return False


def ensure_api(timeout_seconds: float = 20.0) -> subprocess.Popen[object] | None:
    """Return the API handle only when this desktop shell started it."""

    if _api_ready():
        return None
    deadline = time.monotonic() + timeout_seconds
    with startup_mutex(API_STARTUP_MUTEX, max(0.0, deadline - time.monotonic())) as acquired:
        if _api_ready():
            return None
        if not acquired:
            if _wait_for_api(deadline):
                return None
            raise RuntimeError(f"Local AI Hub API startup lock timed out at {HOST}:{PORT}.")
        python = os.environ.get("LOCALAIHUB_PYTHON") or sys.executable
        log_path = ROOT / "Logs" / "api_server.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as log:
            process = popen_hidden(
                [python, "-m", "src.services.api.api_server"],
                cwd=ROOT,
                env={**os.environ, "PYTHONPATH": str(ROOT), "LOCALAIHUB_ROOT": str(ROOT)},
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        if _wait_for_api(deadline):
            return process if process.poll() is None else None
    raise RuntimeError(f"Local AI Hub API did not become ready at {HOST}:{PORT}.")


def _remember_owned_api(process: subprocess.Popen[object] | None) -> None:
    if process is None or process.poll() is not None:
        return
    global _api_process
    terminate_late_process = False
    with _api_process_lock:
        if _shutdown_started:
            terminate_late_process = True
        else:
            _api_process = process
    if terminate_late_process:
        # Shutdown may win the startup race; never leave a late-owned API tree alive.
        terminate_owned_process(process)


def close_owned_api() -> None:
    """Stop only the API tree that this desktop shell itself started."""

    global _api_process, _shutdown_started
    with _api_process_lock:
        _shutdown_started = True
        process, _api_process = _api_process, None
    if process is not None:
        terminate_owned_process(process)


def close_owned_idle_backends() -> None:
    """Ask the API process to stop only idle backends that it owns."""

    request = urllib.request.Request(f"http://{HOST}:{PORT}/api/lifecycle/close", data=b"{}", method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=4):
            pass
    except OSError:
        # Closing the desktop shell must not fail if the loopback API already stopped.
        return


def _loading_html() -> str:
    """Return a tiny local screen shown before the loopback API is ready."""

    return """<!doctype html><html lang=\"vi\"><meta charset=\"utf-8\"><title>Local AI Hub</title>
    <style>html,body{margin:0;height:100%;background:#0b1020;color:#edf2ff;font-family:Segoe UI,system-ui,sans-serif}
    main{height:100%;display:grid;place-content:center;text-align:center;gap:14px}.mark{margin:auto;display:grid;place-items:center;width:48px;height:48px;border-radius:14px;background:linear-gradient(145deg,#80aaff,#4d7dff);font-weight:800}.spinner{width:18px;height:18px;border:2px solid #34518a;border-top-color:#80aaff;border-radius:50%;animation:s 1s linear infinite;margin:2px auto}@keyframes s{to{transform:rotate(1turn)}}p{margin:0;color:#9aa8c7;font-size:14px}</style>
    <main><div class=\"mark\">LA</div><strong>Local AI Hub</strong><div class=\"spinner\"></div><p>Đang khởi động dịch vụ cục bộ…</p></main></html>"""


def _error_html(message: str) -> str:
    safe = html.escape(message)
    return f"""<!doctype html><html lang=\"vi\"><meta charset=\"utf-8\"><title>Local AI Hub</title>
    <style>html,body{{margin:0;height:100%;background:#0b1020;color:#edf2ff;font-family:Segoe UI,system-ui,sans-serif}}main{{height:100%;display:grid;place-content:center;text-align:center;gap:14px;padding:32px}}p{{max-width:620px;color:#efb2bd;line-height:1.5}}</style>
    <main><strong>Không thể khởi động Local AI Hub</strong><p>{safe}</p></main></html>"""


def _load_ui_when_ready(window: object) -> None:
    """Wait in a worker thread, leaving the native loading window responsive."""

    try:
        _remember_owned_api(ensure_api())
    except Exception as exc:  # pragma: no cover - GUI error rendering is host-specific
        try:
            window.load_html(_error_html(str(exc)))  # type: ignore[attr-defined]
        except Exception:
            return
        return
    try:
        window.load_url(UI_URL)  # type: ignore[attr-defined]
    except Exception:
        # The user can still close the loading window normally if WebView2 fails.
        return


def main() -> int:
    try:
        import webview
    except ImportError:
        print("pywebview is required for the desktop shell. Install requirements-hub.txt in the Hub environment.", file=sys.stderr)
        return 2

    try:
        window = webview.create_window(
            "Local AI Hub",
            html=_loading_html(),
            width=1280,
            height=720,
            min_size=(1280, 720),
            resizable=True,
            confirm_close=False,
        )

        def initialize_window() -> None:
            # WebView2 applies the maximize request after the native handle exists.
            try:
                window.maximize()
            except Exception:
                # The window still respects the minimum size if a work area is small.
                pass
            threading.Thread(
                target=_load_ui_when_ready,
                args=(window,),
                name="LocalAIHub-API-startup",
                daemon=True,
            ).start()

        try:
            webview.start(initialize_window, gui="edgechromium", debug=False)
        finally:
            close_owned_idle_backends()
            close_owned_api()
        return 0
    except Exception as exc:  # pragma: no cover - native GUI errors are host-specific
        print(f"Local AI Hub desktop shell failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
