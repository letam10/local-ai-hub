"""Desktop shell for the one Local AI Hub frontend.

The browser and desktop paths intentionally share ``/ui/``.  This module only
owns the native window; capability and runtime state remain in the loopback API.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HOST = "127.0.0.1"
PORT = 8765
UI_URL = f"http://{HOST}:{PORT}/ui/"


def _no_console_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def _hidden_startupinfo() -> subprocess.STARTUPINFO | None:
    if os.name != "nt":
        return None
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = subprocess.SW_HIDE
    return info


def _api_ready() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=0.35):
            return True
    except OSError:
        return False


def ensure_api(timeout_seconds: float = 12.0) -> None:
    if _api_ready():
        return
    python = os.environ.get("LOCALAIHUB_PYTHON") or sys.executable
    log_path = ROOT / "Logs" / "api_server.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("a", encoding="utf-8")
    subprocess.Popen(
        [python, "-m", "src.services.api.api_server"],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT), "LOCALAIHUB_ROOT": str(ROOT)},
        stdout=log,
        stderr=subprocess.STDOUT,
        creationflags=_no_console_flags(),
        startupinfo=_hidden_startupinfo(),
    )
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _api_ready():
            return
        time.sleep(0.2)
    raise RuntimeError(f"Local AI Hub API did not become ready at {HOST}:{PORT}.")


def close_owned_idle_backends() -> None:
    """Ask the API process to stop only idle backends that it owns."""

    request = urllib.request.Request(f"http://{HOST}:{PORT}/api/lifecycle/close", data=b"{}", method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=4):
            pass
    except OSError:
        # Closing the desktop shell must not fail if the loopback API already stopped.
        return


def main() -> int:
    try:
        import webview
    except ImportError:
        print("pywebview is required for the desktop shell. Install requirements-hub.txt in the Hub environment.", file=sys.stderr)
        return 2

    try:
        ensure_api()
        window = webview.create_window(
            "Local AI Hub",
            UI_URL,
            width=1280,
            height=720,
            min_size=(1280, 720),
            resizable=True,
            confirm_close=True,
        )

        def maximize() -> None:
            # WebView2 applies the maximize request after the native handle exists.
            try:
                window.maximize()
            except Exception:
                # The window still respects the minimum size if a work area is small.
                pass

        webview.start(maximize, gui="edgechromium", debug=False)
        close_owned_idle_backends()
        return 0
    except Exception as exc:  # pragma: no cover - native GUI errors are host-specific
        print(f"Local AI Hub desktop shell failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
