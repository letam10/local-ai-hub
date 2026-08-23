"""Opt-in real Windows desktop lifecycle smoke without an AI backend.

Run only from the Hub environment when port 8765 is free::

    D:\\LocalAIHub\\Environments\\hub\\Scripts\\python.exe tests\\windows_lifecycle_smoke.py --run

The parent launches a ``pythonw.exe`` child with the shared hidden-window
policy.  That child owns a tiny loopback HTTP fixture, bounded CPU-only dummy
processes, real pywebview/Edge close cycles, and two short Windows tray
registrations.  It never imports the Hub API server, calls ``/api/bootstrap``,
probes GPU hardware, or starts a model/media worker.  The fixture is removed
and every child handle is terminated before the command returns.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from collections.abc import Callable
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
HOST = "127.0.0.1"
PORT = 8765
SMOKE_TIMEOUT_SECONDS = 35.0

# Direct ``python tests\\windows_lifecycle_smoke.py`` execution places only the
# tests directory on sys.path.  Keep the harness self-contained without
# requiring an environment install or changing the production launcher.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _port_is_free() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.2)
        return probe.connect_ex((HOST, PORT)) != 0


def _write_result(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True), encoding="ascii")


class _FixtureServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def __init__(self) -> None:
        super().__init__((HOST, PORT), _FixtureHandler)
        self.calls: dict[str, int] = {"health": 0, "prepare_close": 0, "idle_close": 0, "cancel_and_wait": 0}
        self.calls_lock = threading.RLock()
        self.active_jobs = 0
        self.cancel_active_worker: Callable[[], bool] | None = None

    def mark(self, route: str) -> None:
        with self.calls_lock:
            self.calls[route] = self.calls.get(route, 0) + 1


class _FixtureHandler(BaseHTTPRequestHandler):
    server: _FixtureServer

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _write(self, status: int, payload: dict[str, Any], *, html: bool = False) -> None:
        if html:
            body = payload["html"].encode("utf-8")
            content_type = "text/html; charset=utf-8"
        else:
            body = json.dumps(payload, ensure_ascii=True).encode("ascii")
            content_type = "application/json; charset=utf-8"
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self.server.mark("health")
            with self.server.calls_lock:
                active_jobs = self.server.active_jobs
            self._write(200, {"status": "healthy", "active_jobs": active_jobs, "version": "4.0.0", "gpu": {}})
            return
        if self.path in {"/ui", "/ui/"}:
            self._write(200, {"html": "<!doctype html><meta charset='utf-8'><title>Local AI Hub smoke</title><main>Safe desktop smoke</main>"}, html=True)
            return
        self._write(404, {"status": "error"})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > 0:
            self.rfile.read(min(length, 64 * 1024))
        if self.path == "/api/lifecycle/prepare-close":
            self.server.mark("prepare_close")
            with self.server.calls_lock:
                active_jobs = self.server.active_jobs
            if active_jobs:
                self._write(409, {"status": "active_jobs", "active_jobs": active_jobs, "message": "fixture active worker"})
                return
            self._write(200, {"status": "ready_to_close", "active_jobs": 0, "message": "fixture ready"})
            return
        if self.path == "/api/lifecycle/jobs/cancel-and-wait":
            self.server.mark("cancel_and_wait")
            callback = self.server.cancel_active_worker
            complete = callback() if callback is not None else True
            with self.server.calls_lock:
                if complete:
                    self.server.active_jobs = 0
            self._write(200 if complete else 409, {"status": "completed" if complete else "timeout", "message": "fixture worker terminal" if complete else "fixture worker timeout"})
            return
        if self.path == "/api/lifecycle/close":
            self.server.mark("idle_close")
            self._write(200, {"status": "completed"})
            return
        self._write(404, {"status": "error"})


def _hub_pythonw() -> Path:
    current = Path(sys.executable)
    candidate = current.with_name("pythonw.exe")
    if candidate.is_file():
        return candidate
    raise RuntimeError("Hub Python environment does not expose pythonw.exe for the console-free smoke child.")


def _hub_python() -> Path:
    current = Path(sys.executable)
    candidate = current.with_name("python.exe")
    return candidate if candidate.is_file() else current


def _child_environment() -> dict[str, str]:
    existing = os.environ.get("PYTHONPATH", "")
    return {**os.environ, "PYTHONPATH": str(ROOT) + (os.pathsep + existing if existing else "")}


def _run_second_instance_probe() -> bool:
    """Verify that a second desktop process never creates a second API.

    Local AI Hub is intentionally single-instance.  When the primary desktop
    holds the installation-scoped mutex, a second process must exit cleanly
    without creating another native window or API listener.  The probe also
    accepts the external-API path used by development hosts that deliberately
    permit a second UI process.
    """

    from src.services.process_manager.windows import hidden_popen_kwargs

    command = [
        str(_hub_python()),
        str(Path(__file__).resolve()),
        "--second-instance-probe",
    ]
    probe = subprocess.Popen(
        command,
        cwd=str(ROOT),
        env=_child_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **hidden_popen_kwargs(),
    )
    try:
        stdout, _stderr = probe.communicate(timeout=8)
    except subprocess.TimeoutExpired:
        from src.services.process_manager.managed import terminate_owned_process

        terminate_owned_process(probe)
        return False
    return probe.returncode == 0 and (
        b'"external_api": true' in stdout or b'"single_instance_refused": true' in stdout
    )


def _run_second_instance_probe_mode() -> int:
    from src.app import main as desktop
    from src.services.process_manager.windows import startup_mutex

    # A real second desktop process should observe the primary instance mutex
    # and exit before any pywebview or API startup work.  This is the expected
    # product behavior, not a failed external-API discovery.
    with startup_mutex(desktop._scoped_mutex(desktop.APP_INSTANCE_MUTEX), 0.2) as acquired:
        if not acquired:
            print(json.dumps({"external_api": False, "single_instance_refused": True, "desktop_exit_code": 0}, ensure_ascii=True))
            return 0

    original_loader = desktop._load_ui_when_ready
    observed = {"external_api": False}
    exit_code = 1

    def load_second_window(window: object) -> None:
        value = desktop.ensure_api(timeout_seconds=1.5)
        observed["external_api"] = value is None
        desktop._remember_owned_api(value)
        try:
            window.load_url(desktop.UI_URL)
        finally:
            def close_later() -> None:
                time.sleep(0.75)
                try:
                    window.destroy()  # type: ignore[attr-defined]
                except Exception:
                    return

            threading.Thread(target=close_later, name="LocalAIHub-second-instance-close", daemon=True).start()

    try:
        desktop._api_process = None
        desktop._shutdown_started = False
        desktop._load_ui_when_ready = load_second_window
        exit_code = desktop.main()
    finally:
        desktop._load_ui_when_ready = original_loader
    print(json.dumps({"external_api": observed["external_api"], "single_instance_refused": False, "desktop_exit_code": exit_code}, ensure_ascii=True))
    return 0 if exit_code == 0 and observed["external_api"] else 3


def _tray_roundtrip() -> bool:
    """Register, remove, then register the real shell icon again."""

    from src.app.tray import WindowsTray

    tray = WindowsTray(lambda: None, lambda: None)
    first, _first_message = tray.start()
    tray.stop()
    second, _second_message = tray.start()
    tray.stop()
    return first and second


def _sleeping_cpu_process() -> subprocess.Popen[Any]:
    """Return a harmless, hidden, bounded process that this smoke owns."""

    from src.services.process_manager.windows import popen_hidden

    return popen_hidden(
        [str(_hub_python()), "-c", "import time; time.sleep(20)"],
        cwd=ROOT,
        env=_child_environment(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _cooperative_cpu_worker(stop_signal: Path) -> subprocess.Popen[Any]:
    """A tiny job-like worker that exits itself after the fixture cancel signal."""

    from src.services.process_manager.windows import popen_hidden

    program = (
        "import pathlib, sys, time; "
        "signal = pathlib.Path(sys.argv[1]); "
        "deadline = time.monotonic() + 20; "
        "while time.monotonic() < deadline and not signal.exists(): "
        " sum(range(64)); time.sleep(0.02)"
    )
    return popen_hidden(
        [str(_hub_python()), "-c", program, str(stop_signal)],
        cwd=ROOT,
        env=_child_environment(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_for_process(process: subprocess.Popen[Any], timeout_seconds: float) -> bool:
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        return False
    return True


def _run_desktop_cycle(
    desktop: object,
    api_process: subprocess.Popen[Any],
    *,
    active: bool,
    launch_second_instance: bool = False,
) -> tuple[int, dict[str, object]]:
    """Drive one real window through the native close pipeline."""

    original_loader = desktop._load_ui_when_ready  # type: ignore[attr-defined]
    original_bind = desktop.DesktopBridge._bind  # type: ignore[attr-defined]
    captured: dict[str, object] = {}

    def capture_bind(bridge: object, window: object) -> None:
        original_bind(bridge, window)
        captured["bridge"] = bridge

    def load_smoke_ui(window: object) -> None:
        desktop._remember_owned_api(api_process)  # type: ignore[attr-defined]
        try:
            window.load_url(desktop.UI_URL)  # type: ignore[attr-defined]
        finally:
            def drive_close() -> None:
                time.sleep(1.0)
                if launch_second_instance:
                    # Keep this first native window alive while a genuine
                    # second desktop process resolves the already-running
                    # fixture as external rather than spawning another API.
                    captured["second_instance_ok"] = _run_second_instance_probe()
                try:
                    # This invokes pywebview's native closing event.  For the
                    # active cycle it must be vetoed first, then the bridge
                    # drives the real cancel-and-exit decision.
                    window.destroy()  # type: ignore[attr-defined]
                except Exception:
                    return
                if active:
                    time.sleep(0.45)
                    bridge = captured.get("bridge")
                    if bridge is not None:
                        # Exercise the real bridge/tray path before returning
                        # to the Hub and selecting cooperative cancellation.
                        captured["background"] = bridge.keep_running_in_background()  # type: ignore[attr-defined]
                        time.sleep(0.45)
                        bridge._restore_from_tray()  # type: ignore[attr-defined]
                        captured["restored"] = True
                        time.sleep(0.35)
                        try:
                            window.destroy()  # type: ignore[attr-defined]
                        except Exception:
                            return
                        time.sleep(0.45)
                        bridge.cancel_jobs_and_exit()  # type: ignore[attr-defined]

            threading.Thread(target=drive_close, name="LocalAIHub-windows-smoke-close", daemon=True).start()

    try:
        desktop._api_process = None  # type: ignore[attr-defined]
        desktop._shutdown_started = False  # type: ignore[attr-defined]
        desktop.DesktopBridge._bind = capture_bind  # type: ignore[attr-defined]
        desktop._load_ui_when_ready = load_smoke_ui  # type: ignore[attr-defined]
        return int(desktop.main()), captured  # type: ignore[attr-defined]
    finally:
        desktop._load_ui_when_ready = original_loader  # type: ignore[attr-defined]
        desktop.DesktopBridge._bind = original_bind  # type: ignore[attr-defined]


def _run_child(result_path: Path) -> int:
    """Run zero-active and cooperative-active pywebview cycles on the fixture."""

    from src.app import main as desktop
    from src.services.process_manager.managed import terminate_owned_process

    if os.name != "nt":
        _write_result(result_path, {"status": "skipped", "reason": "Windows only"})
        return 0
    if not _port_is_free():
        _write_result(result_path, {"status": "deferred", "reason": "port 8765 is in use"})
        return 0

    fixture: _FixtureServer | None = None
    fixture_thread: threading.Thread | None = None
    zero_api: subprocess.Popen[Any] | None = None
    active_api: subprocess.Popen[Any] | None = None
    worker: subprocess.Popen[Any] | None = None
    worker_signal = result_path.parent / "cancel-worker.signal"
    original_api_process = desktop._api_process
    original_shutdown_started = desktop._shutdown_started
    payload: dict[str, Any] = {"status": "error"}
    try:
        fixture = _FixtureServer()
        fixture_thread = threading.Thread(target=fixture.serve_forever, kwargs={"poll_interval": 0.05}, name="LocalAIHub-windows-smoke-fixture", daemon=True)
        fixture_thread.start()
        tray_ok = _tray_roundtrip()
        zero_api = _sleeping_cpu_process()
        zero_exit_code, zero_cycle = _run_desktop_cycle(desktop, zero_api, active=False, launch_second_instance=True)
        second_instance_ok = zero_cycle.get("second_instance_ok") is True
        zero_api_stopped = zero_api.poll() is not None

        worker_signal.unlink(missing_ok=True)
        active_api = _sleeping_cpu_process()
        worker = _cooperative_cpu_worker(worker_signal)

        def cancel_worker() -> bool:
            worker_signal.write_text("cancel", encoding="ascii")
            return _wait_for_process(worker, 3.0)

        with fixture.calls_lock:
            fixture.active_jobs = 1
            fixture.cancel_active_worker = cancel_worker
        active_exit_code, active_cycle = _run_desktop_cycle(desktop, active_api, active=True)
        active_api_stopped = active_api.poll() is not None
        worker_stopped_cooperatively = worker.poll() is not None
        with fixture.calls_lock:
            calls = dict(fixture.calls)
        payload = {
            "status": "passed" if zero_exit_code == 0 and active_exit_code == 0 and second_instance_ok and tray_ok and zero_api_stopped and active_api_stopped and worker_stopped_cooperatively and active_cycle.get("background", {}).get("status") == "completed" and active_cycle.get("restored") is True and calls["prepare_close"] >= 1 and calls["idle_close"] >= 2 and calls["cancel_and_wait"] >= 1 else "failed",
            "zero_active_desktop_exit_code": zero_exit_code,
            "active_cancel_desktop_exit_code": active_exit_code,
            "second_instance_ok": second_instance_ok,
            "tray_roundtrip_ok": tray_ok,
            "prepare_close_calls": calls["prepare_close"],
            "idle_close_calls": calls["idle_close"],
            "cancel_and_wait_calls": calls["cancel_and_wait"],
            "zero_active_api_stopped": zero_api_stopped,
            "active_api_stopped": active_api_stopped,
            "worker_stopped_cooperatively": worker_stopped_cooperatively,
            "background_status": active_cycle.get("background", {}).get("status"),
            "background_restored": active_cycle.get("restored") is True,
        }
    except Exception as exc:  # pragma: no cover - real GUI host dependent
        payload = {"status": "failed", "error": type(exc).__name__}
    finally:
        desktop._api_process = original_api_process
        desktop._shutdown_started = original_shutdown_started
        for process in (zero_api, active_api, worker):
            if process is not None and process.poll() is None:
                terminate_owned_process(process)
        worker_signal.unlink(missing_ok=True)
        if fixture is not None:
            fixture.shutdown()
            fixture.server_close()
        if fixture_thread is not None:
            fixture_thread.join(timeout=3)
        payload["listener_released"] = _port_is_free()
        if payload.get("status") == "passed" and not payload["listener_released"]:
            payload["status"] = "failed"
        _write_result(result_path, payload)
    return 0 if payload.get("status") == "passed" else 1


def _run_parent() -> int:
    if os.name != "nt":
        print(json.dumps({"status": "skipped", "reason": "Windows only"}, ensure_ascii=True))
        return 0
    if not _port_is_free():
        print(json.dumps({"status": "deferred", "reason": "port 8765 is in use"}, ensure_ascii=True))
        return 0
    from src.services.process_manager.managed import terminate_owned_process
    from src.services.process_manager.windows import popen_hidden

    with TemporaryDirectory(prefix="local-ai-hub-windows-smoke-") as temporary:
        result_path = Path(temporary) / "result.json"
        child = popen_hidden(
            [str(_hub_pythonw()), str(Path(__file__).resolve()), "--child", "--result", str(result_path)],
            cwd=ROOT,
            env=_child_environment(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            child.wait(timeout=SMOKE_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            terminate_owned_process(child)
            print(json.dumps({"status": "failed", "reason": "desktop smoke timeout"}, ensure_ascii=True))
            return 1
        try:
            result = json.loads(result_path.read_text(encoding="ascii"))
        except (OSError, json.JSONDecodeError):
            result = {"status": "failed", "reason": "missing smoke result"}
        if child.returncode != 0 and result.get("status") not in {"deferred", "skipped"}:
            result["status"] = "failed"
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 0 if result.get("status") in {"passed", "deferred", "skipped"} else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--run", action="store_true", help="launch the console-free real desktop smoke child")
    mode.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    mode.add_argument("--second-instance-probe", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--result", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.second_instance_probe:
        return _run_second_instance_probe_mode()
    if args.child:
        if args.result is None:
            parser.error("--child requires --result")
        return _run_child(args.result)
    return _run_parent()


if __name__ == "__main__":
    raise SystemExit(main())
