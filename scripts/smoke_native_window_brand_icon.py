"""Bounded Windows icon smoke with static HTML only; no Hub API or jobs.

Run with the bundled desktop Python, from a source checkout. Supply a
task-owned storage directory and an existing installation containing the ICO.
The fixture closes its own window, never an existing Hub window.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import sys
import threading


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-root", type=Path, required=True)
    parser.add_argument("--storage", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ["LOCALAIHUB_INSTALL_ROOT"] = str(args.install_root)
    import webview
    from src.app.main import _start_native_webview
    from src.shared.runtime_identity import APP_USER_MODEL_ID

    result = {"status": "FAIL", "fixture_pid": os.getpid()}
    window = webview.create_window(
        "Local AI Hub — icon smoke (auto-close)",
        html="<h2>Local AI Hub</h2><p>Isolated icon check. Closes automatically.</p>",
        width=500, height=300,
    )

    def inspect_window():
        try:
            if not window.events.loaded.wait(15):
                raise TimeoutError("Fixture frontend did not load")
            from System import Action
            from System.Drawing import Icon
            from System.Drawing.Imaging import ImageFormat
            from System.IO import MemoryStream

            def digest(icon):
                bitmap = icon.ToBitmap()
                stream = MemoryStream()
                try:
                    bitmap.Save(stream, ImageFormat.Png)
                    return hashlib.sha256(bytes(stream.ToArray())).hexdigest()
                finally:
                    stream.Dispose()
                    bitmap.Dispose()

            def inspect_on_ui_thread():
                expected = Icon(str(args.install_root / "local-ai-hub.ico"))
                try:
                    native = window.native
                    result["native_icon_matches_canonical"] = digest(native.Icon) == digest(expected)
                    result["titlebar_icon_enabled"] = bool(native.ShowIcon)
                    result["taskbar_enabled"] = bool(native.ShowInTaskbar)
                finally:
                    expected.Dispose()

            window.native.Invoke(Action(inspect_on_ui_thread))
            value = ctypes.c_wchar_p()
            shell = ctypes.windll.shell32
            shell.GetCurrentProcessExplicitAppUserModelID.argtypes = [ctypes.POINTER(ctypes.c_wchar_p)]
            shell.GetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long
            hr = shell.GetCurrentProcessExplicitAppUserModelID(ctypes.byref(value))
            try:
                result["process_identity_matches"] = hr == 0 and value.value == APP_USER_MODEL_ID
            finally:
                if value:
                    ctypes.windll.ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
                    ctypes.windll.ole32.CoTaskMemFree(ctypes.cast(value, ctypes.c_void_p))
            checks = ("native_icon_matches_canonical", "titlebar_icon_enabled", "taskbar_enabled", "process_identity_matches")
            result["status"] = "PASS" if all(result.get(key) for key in checks) else "FAIL"
        except Exception as exc:
            result["error_type"] = type(exc).__name__
        finally:
            window.destroy()

    # Keep WebView storage inside the task-owned fixture path.
    class Host:
        @staticmethod
        def start(callback, **kwargs):
            webview.start(callback, storage_path=str(args.storage), **kwargs)

    watchdog = threading.Timer(25, lambda: os._exit(124))
    watchdog.daemon = True
    watchdog.start()
    try:
        _start_native_webview(Host, inspect_window)
    finally:
        watchdog.cancel()
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
