"""
/*
  FILE NOTE
  - Mục đích: Native desktop shell (pywebview window, single-instance mutex, startup mutex, window bounds persistence, close prompt bridge, tray integration)
  - Liên kết trực tiếp: src/app/desktop_lifecycle.py, src/app/tray.py, src/services/api/api_server.py, src/app_config/settings_service.py, src/ui/index.html
  - Vùng ảnh hưởng khi sửa: Khởi động cửa sổ desktop native, single-instance guard, nạp cấu hình kích thước cửa sổ, vòng đời đóng app, background tray icon
*/
"""

from __future__ import annotations

import html
import hashlib
import inspect
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from src.services.process_manager.managed import terminate_owned_process
from src.services.process_manager.windows import popen_hidden, startup_mutex
from src.services.runtime_manager.core_resolver import CoreRuntimeResolver
from src.platform.paths import get_paths
from src.shared.runtime_identity import APP_USER_MODEL_ID, API_PROTOCOL_VERSION, PRODUCT_ID, api_identity
from src.shared.version import PRODUCT_VERSION

from .desktop_lifecycle import DesktopCloseController
from .readiness import record_event as record_readiness_event
from .stable_shell import StableShellError, resolve_launch_plan
from .tray import WindowsTray

ROOT = Path(__file__).resolve().parents[2]
HOST = "127.0.0.1"
PORT = 8765
UI_URL = f"http://{HOST}:{PORT}/ui/"
API_STARTUP_MUTEX = r"Local\LocalAIHub.ApiStartup.v1"
APP_INSTANCE_MUTEX = r"Local\LocalAIHub.AppInstance.v1"
_api_process: subprocess.Popen[object] | None = None
_api_process_lock = threading.RLock()
_shutdown_started = False
API_PROBE_ABSENT = "absent"
API_PROBE_COMPATIBLE = "compatible_owned_or_reusable"
API_PROBE_LOCAL_INCOMPATIBLE = "localaihub_incompatible"
API_PROBE_FOREIGN = "foreign_unknown"
API_STARTUP_EXITED = "API_STARTUP_EXITED"
API_IDENTITY_MISMATCH = "API_IDENTITY_MISMATCH"
API_STARTUP_TIMEOUT = "API_STARTUP_TIMEOUT"
API_BUNDLED_RUNTIME_UNAVAILABLE = "API_BUNDLED_RUNTIME_UNAVAILABLE"
API_STARTUP_FAILED = "API_STARTUP_FAILED"
FRONTEND_BOOTSTRAP_TIMEOUT = "FRONTEND_BOOTSTRAP_TIMEOUT"
WEBVIEW_NAVIGATION_FAILED = "WEBVIEW_NAVIGATION_FAILED"
FRONTEND_READY = "frontend_ready"
FRONTEND_READY_TIMEOUT_SECONDS = 20.0
_STARTUP_ERROR_MESSAGES = {
    API_STARTUP_EXITED: "Dịch vụ API bundled đã thoát trong khi khởi động. Mở Diagnostics để xem chi tiết.",
    API_IDENTITY_MISMATCH: "Dịch vụ API không thuộc installation này. Mở Diagnostics để xem chi tiết.",
    API_STARTUP_TIMEOUT: "Dịch vụ API không sẵn sàng trong thời gian giới hạn. Mở Diagnostics để xem chi tiết.",
    API_BUNDLED_RUNTIME_UNAVAILABLE: "Payload runtime bundled không hợp lệ hoặc không còn tồn tại. Mở Diagnostics để xem chi tiết.",
    API_STARTUP_FAILED: "Không thể khởi động dịch vụ API. Mở Diagnostics để xem chi tiết.",
    FRONTEND_BOOTSTRAP_TIMEOUT: "Giao diện Local AI Hub không hoàn tất bootstrap trong thời gian giới hạn. Thử lại hoặc khôi phục phiên bản trước.",
    WEBVIEW_NAVIGATION_FAILED: "WebView không thể nạp giao diện Local AI Hub. Thử lại hoặc khôi phục phiên bản trước.",
}


def _canonical_icon_path() -> str | None:
    """Return the installed product icon without falling back to Python/UI art."""

    install_root = os.environ.get("LOCALAIHUB_INSTALL_ROOT")
    if not install_root:
        return None
    candidate = Path(install_root) / "local-ai-hub.ico"
    try:
        return str(candidate) if candidate.is_file() and not candidate.is_symlink() else None
    except OSError:
        return None


def _configured_port() -> int:
    """Read the machine-local API port, retaining 8765 as the safe default."""

    candidate = os.environ.get("LOCALAIHUB_PORT")
    if candidate is None:
        try:
            config_path = get_paths(app_root=ROOT).config_root / "hub_config.json"
            value = json.loads(config_path.read_text(encoding="utf-8"))
            candidate = value.get("api_port") if isinstance(value, dict) else None
        except (OSError, UnicodeError, json.JSONDecodeError):
            candidate = None
    try:
        port = int(candidate)
    except (TypeError, ValueError):
        return PORT
    return port if 1024 <= port <= 65535 else PORT


def _api_base_url() -> str:
    return f"http://{HOST}:{_configured_port()}"


def _ui_url() -> str:
    return f"{_api_base_url()}/ui/"


def _scoped_mutex(base: str, *, port: int | None = None) -> str:
    """Keep single-instance semantics per installation, not per Windows user."""

    try:
        paths = get_paths(app_root=ROOT)
        digest = hashlib.sha256(str(paths.data_root).casefold().encode("utf-8")).hexdigest()[:16]
        suffix = f".{int(port)}" if port is not None else ""
        if paths.legacy_single_root_mode and not suffix:
            return base
        return f"{base}.{digest}{suffix}"
    except OSError:
        return base


def _expected_api_identity() -> dict[str, str]:
    paths = get_paths(app_root=ROOT)
    value = api_identity(
        product_version=PRODUCT_VERSION,
        installation_root=os.environ.get("LOCALAIHUB_INSTALL_ROOT"),
        app_root=paths.app_root,
        data_root=paths.data_root,
    )
    return {key: str(item) for key, item in value.items()}


def _classify_api_identity(payload: object) -> str:
    if not isinstance(payload, dict):
        return API_PROBE_FOREIGN
    if payload.get("product_id") != PRODUCT_ID:
        return API_PROBE_FOREIGN
    expected = _expected_api_identity()
    required = ("product_id", "product_version", "api_protocol_version", "app_user_model_id", "process_owner", "installation_id")
    if any(payload.get(key) != expected[key] for key in required):
        return API_PROBE_LOCAL_INCOMPATIBLE
    return API_PROBE_COMPATIBLE


def _probe_api(port: int | None = None) -> tuple[str, dict[str, object]]:
    selected = _configured_port() if port is None else int(port)
    try:
        with urllib.request.urlopen(f"http://{HOST}:{selected}/health", timeout=0.35) as response:
            if response.status != 200:
                return API_PROBE_FOREIGN, {}
            raw = response.read(128 * 1024 + 1)
            if len(raw) > 128 * 1024:
                return API_PROBE_FOREIGN, {}
            payload = json.loads(raw.decode("utf-8"))
            return _classify_api_identity(payload), payload if isinstance(payload, dict) else {}
    except urllib.error.HTTPError:
        return API_PROBE_FOREIGN, {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return API_PROBE_ABSENT, {}


def _api_ready() -> bool:
    return _probe_api()[0] == API_PROBE_COMPATIBLE


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind((HOST, 0))
        return int(probe.getsockname()[1])


def _select_api_port() -> tuple[int, str]:
    configured = _configured_port()
    state, _payload = _probe_api(configured)
    if state in {API_PROBE_ABSENT, API_PROBE_COMPATIBLE}:
        return configured, state
    return _free_loopback_port(), state


def _wait_for_api(deadline: float, process: subprocess.Popen[object] | None = None) -> bool:
    while time.monotonic() < deadline:
        if _api_ready():
            return True
        if process is not None and process.poll() is not None:
            return False
        time.sleep(0.2)
    return False


def _installed_launch_plan() -> object | None:
    installation_root = os.environ.get("LOCALAIHUB_INSTALL_ROOT")
    if not installation_root:
        return None
    try:
        return resolve_launch_plan(Path(installation_root))
    except (OSError, ValueError, StableShellError) as exc:
        raise RuntimeError(API_BUNDLED_RUNTIME_UNAVAILABLE) from exc


def _startup_log_path() -> Path:
    return get_paths(app_root=ROOT).log_root / "api_startup.log"


def _record_startup_diagnostic(
    code: str,
    *,
    selected_port: int,
    probe_state: str,
    runtime_class: str,
    child_exit_code: int | None = None,
) -> None:
    value = {
        "schema_version": "v8-api-startup-diagnostic.v1",
        "code": code,
        "selected_port": int(selected_port),
        "probe_class": str(probe_state)[:64],
        "runtime_class": str(runtime_class)[:64],
        "child_exit_code": child_exit_code if isinstance(child_exit_code, int) else None,
        "identity_match": code not in {API_IDENTITY_MISMATCH},
    }
    try:
        path = _startup_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n")
    except (OSError, ValueError):
        pass


def _record_startup_event(event: str, *, selected_port: int | None = None, probe_state: str = "unknown", runtime_class: str = "unknown") -> None:
    """Write bounded lifecycle evidence without paths, tokens, or exceptions."""

    _record_startup_diagnostic(
        event[:64],
        selected_port=_configured_port() if selected_port is None else selected_port,
        probe_state=probe_state,
        runtime_class=runtime_class,
    )


def _record_desktop_readiness(event: str, *, status: str | None = None, route: str | None = None) -> dict[str, object]:
    """Persist bounded desktop/frontend evidence, never an HTTP-only claim."""

    try:
        root = Path(os.environ.get("LOCALAIHUB_INSTALL_ROOT") or ROOT)
        return record_readiness_event(root, event, status=status, route=route)
    except (OSError, TypeError, ValueError):
        return {"status": "unavailable", "code": "READINESS_STATE_UNAVAILABLE"}


def ensure_api(timeout_seconds: float = 20.0) -> subprocess.Popen[object] | None:
    """Return the API handle only when this desktop shell started it."""

    selected_port, probe_state = _select_api_port()
    # This is session state only; never persist a collision fallback as the
    # machine default. All desktop/UI requests use the same selected port.
    os.environ["LOCALAIHUB_PORT"] = str(selected_port)
    os.environ["LOCALAIHUB_BIND_HOST"] = HOST
    if probe_state == API_PROBE_COMPATIBLE:
        return None
    deadline = time.monotonic() + timeout_seconds
    with startup_mutex(_scoped_mutex(API_STARTUP_MUTEX, port=selected_port), max(0.0, deadline - time.monotonic())) as acquired:
        current_state, _payload = _probe_api(selected_port)
        if current_state == API_PROBE_COMPATIBLE:
            return None
        if not acquired:
            if _wait_for_api(deadline):
                return None
            current_state, _payload = _probe_api(selected_port)
            code = API_IDENTITY_MISMATCH if current_state in {API_PROBE_LOCAL_INCOMPATIBLE, API_PROBE_FOREIGN} else API_STARTUP_TIMEOUT
            _record_startup_diagnostic(code, selected_port=selected_port, probe_state=current_state, runtime_class="unknown")
            raise RuntimeError(code)
        process: subprocess.Popen[object] | None = None
        success = False
        runtime_class = "development_python"
        try:
            try:
                launch_plan = _installed_launch_plan()
            except RuntimeError as exc:
                code = str(exc) if str(exc) in _STARTUP_ERROR_MESSAGES else API_BUNDLED_RUNTIME_UNAVAILABLE
                _record_startup_diagnostic(code, selected_port=selected_port, probe_state=probe_state, runtime_class="installed_bundled")
                raise
            if launch_plan is not None:
                python = str(launch_plan.runtime_pythonw)
                runtime_class = "installed_bundled"
                child_env = dict(launch_plan.environment)
                cwd = launch_plan.app_payload
            else:
                # Development/legacy mode retains the fixed Core resolver.
                candidate = CoreRuntimeResolver(paths=get_paths(app_root=ROOT)).resolve_python()
                override = os.environ.get("LOCALAIHUB_PYTHON")
                if candidate is None and override:
                    override_path = Path(override).expanduser()
                    try:
                        if override_path.is_file() and not override_path.is_symlink():
                            candidate = override_path.resolve()
                    except OSError:
                        candidate = None
                if candidate is None:
                    code = API_BUNDLED_RUNTIME_UNAVAILABLE if os.environ.get("LOCALAIHUB_INSTALL_ROOT") else API_STARTUP_FAILED
                    _record_startup_diagnostic(code, selected_port=selected_port, probe_state=probe_state, runtime_class=runtime_class)
                    raise RuntimeError(code)
                python = str(candidate)
                runtime_class = "development_python" if override else "core_environment"
                child_env = dict(os.environ)
                child_env["LOCALAIHUB_APP_ROOT"] = str(ROOT)
                cwd = ROOT
            child_env["PYTHONPATH"] = str(cwd)
            child_env["LOCALAIHUB_PORT"] = str(selected_port)
            child_env["LOCALAIHUB_BIND_HOST"] = HOST
            child_env["PYTHONNOUSERSITE"] = "1"
            child_env.pop("LOCALAIHUB_ROOT", None)
            log_path = _startup_log_path()
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as log:
                process = popen_hidden(
                    [python, "-m", "src.services.api.api_server"],
                    cwd=cwd,
                    env=child_env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
            if _wait_for_api(deadline, process=process):
                if process.poll() is None:
                    success = True
                    return process
            exit_code = process.poll()
            current_state, _payload = _probe_api(selected_port)
            code = API_STARTUP_EXITED if exit_code is not None else API_IDENTITY_MISMATCH if current_state in {API_PROBE_LOCAL_INCOMPATIBLE, API_PROBE_FOREIGN} else API_STARTUP_TIMEOUT
            _record_startup_diagnostic(code, selected_port=selected_port, probe_state=current_state, runtime_class=runtime_class, child_exit_code=exit_code)
            raise RuntimeError(code)
        finally:
            if process is not None and not success:
                terminate_owned_process(process)


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


def _owns_live_api() -> bool:
    with _api_process_lock:
        return _api_process is not None and _api_process.poll() is None


def close_owned_idle_backends() -> None:
    """Ask the API process to stop only idle backends that it owns."""

    if not _owns_live_api():
        # An API started by another shell/service owns its own lifecycle.  The
        # desktop must neither terminate it nor ask it to unload backends.
        return
    request = urllib.request.Request(f"{_api_base_url()}/api/lifecycle/close", data=b"{}", method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=4):
            pass
    except OSError:
        # Closing the desktop shell must not fail if the loopback API already stopped.
        return


def _api_active_job_count() -> int:
    """Return the truthful loopback count; callers veto close on any failure."""

    with urllib.request.urlopen(f"{_api_base_url()}/health", timeout=1.0) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("Phản hồi health của Hub không hợp lệ.")
    return max(0, int(value.get("active_jobs", 0)))


def _owned_api_active_job_count() -> int:
    """Read active jobs only from the API process this desktop owns."""

    if not _owns_live_api():
        raise RuntimeError("External API owner")
    return _api_active_job_count()


def _prepare_owned_api_close() -> dict[str, object]:
    """Close job admission and recheck under the owned API's server lock."""

    if not _owns_live_api():
        return {"status": "ready_to_close", "verification": "verified", "active_jobs": 0, "can_cancel": False, "message": ""}
    request = urllib.request.Request(
        f"{_api_base_url()}/api/lifecycle/prepare-close",
        data=b"{}",
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=3.0) as response:
            value = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            value = json.loads(exc.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            value = {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"status": "unknown", "verification": "unknown", "active_jobs": None, "can_cancel": False, "message": "Không thể xác minh trạng thái tác vụ; Hub chưa đóng để đảm bảo an toàn."}
    if not isinstance(value, dict):
        return {"status": "unknown", "verification": "unknown", "active_jobs": None, "can_cancel": False, "message": "Không thể xác minh trạng thái tác vụ; Hub chưa đóng để đảm bảo an toàn."}
    raw_active = value.get("active_jobs")
    if isinstance(raw_active, bool) or not isinstance(raw_active, int) or raw_active < 0:
        return {"status": "unknown", "verification": "unknown", "active_jobs": None, "can_cancel": False, "message": "Không thể xác minh trạng thái tác vụ; Hub chưa đóng để đảm bảo an toàn."}
    active = raw_active
    status = value.get("status")
    if status == "ready_to_close" and active == 0:
        return {"status": "ready_to_close", "verification": "verified", "active_jobs": 0, "can_cancel": False, "message": str(value.get("message") or "")}
    if status == "active_jobs" and active > 0:
        return {"status": "active_jobs", "verification": "verified", "active_jobs": active, "can_cancel": True, "message": str(value.get("message") or "Hub đang có tác vụ hoạt động; cửa sổ vẫn được giữ mở.")}
    return {"status": "unknown", "verification": "unknown", "active_jobs": None, "can_cancel": False, "message": "Không thể xác minh trạng thái tác vụ; Hub chưa đóng để đảm bảo an toàn."}


def _cancel_api_jobs_and_wait(timeout_seconds: float) -> tuple[bool, str]:
    """Request only cooperative Hub cancellation; never force-kill on timeout."""

    body = json.dumps({"timeout_seconds": max(1, min(60, int(timeout_seconds)))}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{_api_base_url()}/api/lifecycle/jobs/cancel-and-wait",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(request, timeout=max(3.0, timeout_seconds + 3.0)) as response:
            value = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            value = json.loads(exc.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            value = {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return False, f"Không thể xác nhận job đã dừng an toàn: {exc}"
    if not isinstance(value, dict):
        return False, "Phản hồi hủy job của Hub không hợp lệ; cửa sổ vẫn được giữ mở."
    return value.get("status") == "completed", str(value.get("message") or "Hub chưa thể dừng job an toàn.")


def _cancel_owned_api_jobs_and_wait(timeout_seconds: float) -> tuple[bool, str]:
    """Never send global cancellation to an externally managed API."""

    if not _owns_live_api():
        return False, "API do phiên hoặc dịch vụ khác quản lý; desktop không gửi lệnh hủy job của external owner."
    return _cancel_api_jobs_and_wait(timeout_seconds)


class DesktopBridge:
    """pywebview bridge for the exact three safe-close choices."""

    def __init__(self) -> None:
        # pywebview recursively exposes public object attributes. Keep native
        # handles/controllers private so the local UI receives only the three
        # explicit decision methods below.
        self._window: object | None = None
        self._controller: DesktopCloseController | None = None
        self._tray: WindowsTray | None = None
        self._close_prompt_fallback = False
        self._frontend_ready_event = threading.Event()
        self._frontend_ready_result: dict[str, object] | None = None
        self.frontend = _FrontendReadinessBridge(self)
        # Keep the native component picker in a nested bridge namespace so
        # the top-level close API remains the exact three-choice contract.
        self.component_import = _ComponentSelectionBridge(self)

    def _bind(self, window: object) -> None:
        self._window = window
        self._controller = DesktopCloseController(
            _owned_api_active_job_count,
            _cancel_owned_api_jobs_and_wait,
            self._prompt_close,
            prepare_close=_prepare_owned_api_close,
        )
        self._tray = WindowsTray(self._restore_from_tray, self._exit_from_tray)

    def _window_call(self, name: str) -> bool:
        window = self._window
        if window is None:
            return False
        try:
            getattr(window, name)()
        except Exception:
            return False
        return True

    def _prompt_close(self, detail: dict[str, object]) -> None:
        window = self._window
        if window is None:
            return
        script = f"window.dispatchEvent(new CustomEvent('local-ai-hub:close-request', {{ detail: {json.dumps(detail, ensure_ascii=False)} }}));"

        def deliver() -> None:
            for _attempt in range(3):
                try:
                    window.evaluate_js(script)  # type: ignore[attr-defined]
                    self._close_prompt_fallback = False
                    return
                except Exception:
                    time.sleep(0.15)
            # A loading/error WebView may not yet accept evaluate_js.  Keep
            # the close veto and replace it with a local, bridge-backed page
            # containing the same exact three safe choices.
            try:
                window.load_html(_close_prompt_html(detail))  # type: ignore[attr-defined]
                self._close_prompt_fallback = True
                self._restore_window()
            except Exception:
                # The native window remains visible and close stays vetoed;
                # never hide or terminate a potentially active worker.
                self._restore_window()

        # pywebview emits ``closing`` on the GUI thread.  Defer JavaScript until
        # the veto has returned to the native message loop.
        threading.Thread(target=deliver, name="LocalAIHub-close-prompt", daemon=True).start()

    def _restore_window(self) -> None:
        self._window_call("show")
        self._window_call("restore")

    def _destroy_window(self) -> None:
        self._window_call("destroy")

    def _restore_from_tray(self) -> None:
        if self._tray:
            self._tray.stop()
        if self._controller:
            self._controller.restore_from_background(self._restore_window)

    def _exit_from_tray(self) -> None:
        if self._tray:
            self._tray.stop()
        if self._controller:
            self._controller.request_exit_from_background(self._restore_window, self._destroy_window)

    # Methods below are intentionally public: pywebview exposes this object to
    # the local UI, not to a network API.
    def return_to_hub(self) -> dict[str, str]:
        if not self._controller:
            return {"status": "error", "message": "Desktop bridge chưa sẵn sàng."}
        result = self._controller.return_to_hub()
        if result.get("status") == "completed" and self._close_prompt_fallback:
            window = self._window
            try:
                window.load_url(_ui_url())  # type: ignore[attr-defined]
                self._close_prompt_fallback = False
            except Exception:
                result = {"status": "error", "message": "Không thể trở lại giao diện Hub; cửa sổ vẫn được giữ mở an toàn."}
        return result

    def cancel_jobs_and_exit(self) -> dict[str, str]:
        if not self._controller:
            return {"status": "error", "message": "Desktop bridge chưa sẵn sàng."}
        return self._controller.cancel_jobs_and_exit(self._destroy_window)

    def keep_running_in_background(self) -> dict[str, str]:
        if not self._controller or not self._tray:
            return {"status": "error", "message": "Desktop bridge chưa sẵn sàng."}

        def background() -> tuple[bool, str]:
            ok, message = self._tray.start()
            if ok:
                if not self._window_call("hide"):
                    self._tray.stop()
                    return False, "Không thể ẩn cửa sổ sau khi tạo khay; Hub vẫn được giữ mở."
            return ok, message

        return self._controller.keep_running_in_background(background)

    def retry_startup(self) -> dict[str, str]:
        """Retry the bounded API startup probe from the recovery screen."""

        window = self._window
        if window is None:
            return {"status": "error", "message": "Desktop bridge chưa sẵn sàng."}
        self._frontend_ready_event.clear()
        self._frontend_ready_result = None
        threading.Thread(target=_load_ui_when_ready, args=(window, self), name="LocalAIHub-API-retry", daemon=True).start()
        return {"status": "retrying", "message": "Đang thử kết nối lại Local AI Hub API…"}

    def confirm_frontend_ready(self) -> dict[str, object]:
        """Accept the frontend bootstrap proof and only then clear pending health."""

        try:
            with urllib.request.urlopen(f"{_api_base_url()}/health", timeout=2.0) as response:
                health = json.loads(response.read(128 * 1024 + 1).decode("utf-8"))
            install_root = Path(os.environ.get("LOCALAIHUB_INSTALL_ROOT") or ROOT)
            from src.services.app_update import mark_startup_health

            result = mark_startup_health(
                install_root,
                health=health if isinstance(health, dict) else None,
                frontend_ready=True,
            )
            if result.get("status") in {"healthy", "not_pending"}:
                self._frontend_ready_result = {**result, "status": "ready"}
                self._frontend_ready_event.set()
                _record_desktop_readiness(FRONTEND_READY, status="ready")
                state, _payload = _probe_api()
                _record_startup_event(FRONTEND_READY, probe_state=state, runtime_class="installed_bundled")
                return dict(self._frontend_ready_result)
            self._frontend_ready_result = {"status": "error", "code": str(result.get("code") or "FRONTEND_READY_REJECTED")}
            _record_desktop_readiness("frontend_ready_rejected", status="failed")
            return dict(self._frontend_ready_result)
        except Exception:
            self._frontend_ready_result = {"status": "error", "code": "FRONTEND_READY_REJECTED"}
            _record_desktop_readiness("frontend_ready_rejected", status="failed")
            return dict(self._frontend_ready_result)

    def rollback_previous_payload(self) -> dict[str, str]:
        """Restore only the verified installer-owned previous pointer."""

        if not _verified_previous_pointer_available():
            return {"status": "unavailable", "message": "Chưa có phiên bản trước đã được xác minh để khôi phục."}
        try:
            from src.services.app_update import app_update_service

            result = app_update_service().rollback()
            if result.get("status") != "rolled_back":
                return {"status": "error", "message": "Không thể khôi phục phiên bản trước; Hub vẫn giữ nguyên dữ liệu."}
            install_root = Path(os.environ.get("LOCALAIHUB_INSTALL_ROOT") or ROOT).absolute()
            launcher = install_root / "LocalAIHub.exe"
            if not launcher.is_file() or launcher.is_symlink():
                return {"status": "error", "message": "Không tìm thấy launcher ổn định để khởi động lại."}
            subprocess.Popen(
                [str(launcher)], cwd=str(install_root), env=dict(os.environ),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True, creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0,
            )
            self._destroy_window()
            return {"status": "relaunching", "message": "Đã khôi phục phiên bản trước; Hub đang khởi động lại an toàn."}
        except Exception:
            return {"status": "error", "message": "Không thể khôi phục phiên bản trước; Hub vẫn giữ nguyên dữ liệu."}

    def _select_component_source(self, component_id: str) -> dict[str, object]:
        """Open the native picker and return only a short-lived selection ID.

        The browser never receives the selected path.  The desktop bridge is
        the trusted owner of the native chooser and stores the opaque token in
        the server-side ComponentInstaller selection table.
        """

        if not isinstance(component_id, str) or not component_id or len(component_id) > 96:
            return {"status": "invalid", "code": "invalid_component_id", "execution": "not_run"}
        window = self._window
        if window is None:
            return {"status": "unavailable", "code": "desktop_bridge_unavailable", "execution": "not_run"}
        chooser = getattr(window, "create_file_dialog", None)
        if not callable(chooser):
            return {"status": "unavailable", "code": "native_picker_unavailable", "execution": "not_run"}
        try:
            import webview
            dialog_type = getattr(webview, "FOLDER_DIALOG", getattr(webview, "OPEN_DIALOG", 0))
            selected = chooser(dialog_type, allow_multiple=False)
            if isinstance(selected, (list, tuple)):
                selected = selected[0] if selected else None
            if not isinstance(selected, (str, os.PathLike)) or not str(selected):
                return {"status": "cancelled", "execution": "not_run"}
            from src.services.api.components import component_installer
            result = component_installer().issue_selection(component_id, Path(selected))
            return {key: result[key] for key in ("status", "selection_id", "component_id", "location_class", "expires_in_seconds") if key in result}
        except Exception:
            return {"status": "unavailable", "code": "native_selection_rejected", "execution": "not_run"}

    def _request_window_close(self) -> bool:
        if not self._controller:
            return False
        return self._controller.request_window_close()


class _ComponentSelectionBridge:
    """Nested pywebview namespace for native, opaque component selection."""

    def __init__(self, owner: DesktopBridge) -> None:
        self._owner = owner

    def select_source(self, component_id: str) -> dict[str, object]:
        return self._owner._select_component_source(component_id)


class _FrontendReadinessBridge:
    """Nested, allowlisted telemetry namespace for the native frontend."""

    _EVENTS = frozenset({
        "webview_navigation_completed",
        "frontend_bootstrap_started",
        "frontend_rendered",
        "frontend_ready",
        "frontend_js_bootstrap_failed",
        "frontend_ready_rejected",
        "frontend_timeout",
        "route_rendered",
    })

    def __init__(self, owner: DesktopBridge) -> None:
        self._owner = owner

    def record(self, event: str, route: str | None = None) -> dict[str, object]:
        if event not in self._EVENTS:
            return {"status": "rejected", "code": "READINESS_EVENT_INVALID"}
        if event == "frontend_ready":
            result = self._owner.confirm_frontend_ready()
            if result.get("status") == "ready":
                _record_startup_event(event, selected_port=_configured_port(), probe_state="frontend", runtime_class="installed_bundled")
                return result
            _record_desktop_readiness("frontend_ready_rejected", status="failed")
            _record_startup_event("frontend_ready_rejected", selected_port=_configured_port(), probe_state="frontend", runtime_class="installed_bundled")
            return result
        result = _record_desktop_readiness(event, route=route, status="failed" if "failed" in event or "rejected" in event else "running")
        _record_startup_event(event, selected_port=_configured_port(), probe_state="frontend", runtime_class="installed_bundled")
        return result

    def confirm_frontend_ready(self) -> dict[str, object]:
        """Commit readiness through the namespace already used by telemetry.

        Some pywebview/Edge WebView hosts expose nested API objects reliably
        before top-level methods become callable. Keeping the confirmation in
        this allowlisted namespace avoids a false timeout while preserving the
        native health, payload, and identity checks in ``DesktopBridge``.
        """

        return self._owner.confirm_frontend_ready()


def _loading_html() -> str:
    """Return a tiny local screen shown before the loopback API is ready."""

    return """<!doctype html><html lang=\"vi\"><meta charset=\"utf-8\"><title>Local AI Hub</title>
    <style>html,body{margin:0;height:100%;background:#0b1020;color:#edf2ff;font-family:Segoe UI,system-ui,sans-serif}
    main{height:100%;display:grid;place-content:center;text-align:center;gap:14px}.mark{margin:auto;display:grid;place-items:center;width:48px;height:48px;border-radius:14px;background:linear-gradient(145deg,#80aaff,#4d7dff);font-weight:800}.spinner{width:18px;height:18px;border:2px solid #34518a;border-top-color:#80aaff;border-radius:50%;animation:s 1s linear infinite;margin:2px auto}@keyframes s{to{transform:rotate(1turn)}}p{margin:0;color:#9aa8c7;font-size:14px}</style>
    <main><div class=\"mark\">LA</div><strong>Local AI Hub</strong><div class=\"spinner\"></div><p>Đang khởi động dịch vụ cục bộ…</p></main></html>"""


def _error_html(message: str) -> str:
    code = message if message in _STARTUP_ERROR_MESSAGES else API_STARTUP_FAILED
    safe = html.escape(_STARTUP_ERROR_MESSAGES[code])
    code_label = html.escape(code)
    payload_label = html.escape(_current_payload_short_sha())
    title = "Không thể kết nối Local AI Hub API" if code.startswith("API_") else "Không thể khởi động giao diện Local AI Hub"
    rollback_button = '<button class="secondary" onclick="choose(\'rollback_previous_payload\')">Khôi phục phiên bản trước</button>' if _verified_previous_pointer_available() else ''
    return f"""<!doctype html><html lang=\"vi\"><meta charset=\"utf-8\"><title>Local AI Hub</title>
    <style>html,body{{margin:0;height:100%;background:#0b1020;color:#edf2ff;font-family:Segoe UI,system-ui,sans-serif}}main{{max-width:680px;height:100%;margin:auto;display:grid;align-content:center;gap:14px;padding:32px;box-sizing:border-box}}h1{{font-size:24px;margin:0}}p{{max-width:620px;color:#b7c3df;line-height:1.5}}.code{{color:#efb2bd;font-family:ui-monospace,Consolas,monospace}}.actions{{display:flex;flex-wrap:wrap;gap:10px}}button{{border:1px solid #45639d;border-radius:9px;background:#182340;color:#edf2ff;padding:10px 14px;font:inherit;cursor:pointer}}button.primary{{background:#4d7dff;border-color:#80aaff}}button.secondary{{background:#2b2742;border-color:#9c86ff}}button:disabled{{opacity:.65;cursor:wait}}</style>
    <main><h1>{title}</h1><p data-error-code=\"{code_label}\">{safe}</p><p>Payload hiện tại: <span class=\"code\">{payload_label}</span></p><p id=\"status\">Hub chưa sẵn sàng; dữ liệu người dùng vẫn được giữ nguyên.</p><div class=\"actions\"><button class=\"primary\" onclick=\"choose('retry_startup')\">Thử lại</button>{rollback_button}</div></main>
    <script>async function choose(name){{const buttons=[...document.querySelectorAll('button')];const status=document.getElementById('status');const api=window.pywebview&&window.pywebview.api;if(!api||!api[name]){{status.textContent='Desktop bridge chưa sẵn sàng; Hub vẫn được giữ mở an toàn.';return}}buttons.forEach(button=>button.disabled=true);try{{const result=await api[name]();status.textContent=(result&&result.message)||'Đã nhận yêu cầu.';if(result&&result.status==='retrying')setTimeout(()=>window.location.reload(),500);}}catch(_error){{status.textContent='Không thể xử lý yêu cầu phục hồi; hãy thử lại.';buttons.forEach(button=>button.disabled=false)}}}}</script></html>"""


def _current_payload_short_sha() -> str:
    """Return only a bounded payload/build suffix for the recovery screen."""

    try:
        install_root = Path(os.environ.get("LOCALAIHUB_INSTALL_ROOT") or ROOT)
        pointer_path = install_root / "current.json"
        value = json.loads(pointer_path.read_text(encoding="utf-8"))
        version = str(value.get("version") or "") if isinstance(value, dict) else ""
        if version.startswith("main-") and len(version) == 17:
            return version[-12:]
        return version[:24] or "unknown"
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return "unknown"


def _verified_previous_pointer_available() -> bool:
    """Check the rollback record and manifest without changing installed state."""

    try:
        install_root = Path(os.environ.get("LOCALAIHUB_INSTALL_ROOT") or ROOT).absolute()
        value = json.loads((install_root / "update-state" / "previous-current.json").read_text(encoding="utf-8"))
        if not isinstance(value, dict) or set(value) != {"schema_version", "version", "payload_relative", "manifest_sha256"}:
            return False
        if value.get("schema_version") != "v8.0.1-pointer.v1":
            return False
        version = str(value.get("version") or "")
        relative = str(value.get("payload_relative") or "")
        digest = str(value.get("manifest_sha256") or "")
        if not version or relative != f"versions/{version}" or not re.fullmatch(r"[0-9a-f]{64}", digest):
            return False
        manifest = install_root / relative / "manifest.json"
        return manifest.is_file() and not manifest.is_symlink() and hashlib.sha256(manifest.read_bytes()).hexdigest() == digest
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return False


def _close_prompt_html(detail: dict[str, object]) -> str:
    """Local fallback for a close prompt delivered during a WebView transition."""

    verified = detail.get("verification") == "verified" and isinstance(detail.get("active_jobs"), int) and not isinstance(detail.get("active_jobs"), bool)
    count = max(0, int(detail.get("active_jobs", 0) or 0)) if verified else 0
    message = html.escape(str(detail.get("message") or ("Chọn một trong ba cách tiếp tục an toàn." if verified else "Không thể xác minh trạng thái tác vụ; Hub chưa đóng để đảm bảo an toàn.")))
    cancel_button = '<button class="danger" onclick="choose(\'cancel_jobs_and_exit\')">Hủy jobs và thoát</button>' if verified and bool(detail.get("can_cancel")) and count > 0 else ''
    eyebrow = "JOBS ĐANG HOẠT ĐỘNG" if verified and count > 0 else "KHÔNG THỂ XÁC MINH TÁC VỤ"
    copy = f"{count} job đang chờ, chuẩn bị, chạy hoặc hủy. Hub không tự dừng worker đang hoạt động." if verified and count > 0 else "Hub chưa đóng vì chưa xác minh được trạng thái tác vụ."
    return f"""<!doctype html><html lang="vi"><meta charset="utf-8"><title>Local AI Hub</title>
    <style>html,body{{margin:0;height:100%;background:#0b1020;color:#edf2ff;font-family:Segoe UI,system-ui,sans-serif}}main{{max-width:680px;margin:0 auto;height:100%;display:grid;align-content:center;gap:16px;padding:28px;box-sizing:border-box}}.eyebrow{{color:#80aaff;font-size:12px;letter-spacing:.12em}}p,small{{color:#b7c3df;line-height:1.55}}.actions{{display:flex;flex-wrap:wrap;gap:10px}}button{{border:1px solid #45639d;border-radius:9px;background:#182340;color:#edf2ff;padding:10px 14px;font:inherit;cursor:pointer}}button.primary{{background:#4d7dff;border-color:#80aaff}}button.danger{{background:#562737;border-color:#b95c71}}button:disabled{{opacity:.65;cursor:wait}}</style>
    <main><span class="eyebrow">{eyebrow}</span><h1>Bạn muốn xử lý Local AI Hub thế nào?</h1><p>{copy}</p><p id="status">{message}</p><div class="actions"><button onclick="choose('return_to_hub')">Quay lại Hub</button>{cancel_button}<button class="primary" onclick="choose('keep_running_in_background')">Giữ chạy nền vào khay</button></div><small>Chạy nền chỉ ẩn cửa sổ sau khi Windows đã tạo biểu tượng khay có lệnh Khôi phục và Thoát.</small></main>
    <script>async function choose(name){{const buttons=[...document.querySelectorAll('button')];const status=document.getElementById('status');const api=window.pywebview&&window.pywebview.api;if(!api||!api[name]){{status.textContent='Desktop bridge chưa sẵn sàng; Hub vẫn được giữ mở an toàn.';return}}buttons.forEach(button=>button.disabled=true);try{{const result=await api[name]();status.textContent=(result&&result.message)||'Đã nhận lựa chọn.';if(!result||result.status!=='pending')buttons.forEach(button=>button.disabled=false)}}catch(error){{status.textContent='Không thể xử lý lựa chọn: '+error;buttons.forEach(button=>button.disabled=false)}}}}</script></html>"""


def _load_ui_when_ready(window: object, bridge: DesktopBridge | None = None) -> None:
    """Load the WebView and wait for the explicit frontend-ready handshake."""

    try:
        _remember_owned_api(ensure_api())
    except Exception as exc:  # pragma: no cover - GUI error rendering is host-specific
        try:
            window.load_html(_error_html(str(exc)))  # type: ignore[attr-defined]
        except Exception:
            return
        return
    api_state, _payload = _probe_api()
    _record_startup_event("api_ready", selected_port=_configured_port(), probe_state=api_state, runtime_class="installed_bundled")
    _record_startup_event("webview_navigation_started", selected_port=_configured_port(), probe_state=api_state, runtime_class="installed_bundled")
    _record_desktop_readiness("api_ready", status="running")
    _record_desktop_readiness("webview_navigation_started", status="running")
    try:
        window.load_url(_ui_url())  # type: ignore[attr-defined]
    except Exception:
        _record_startup_event(WEBVIEW_NAVIGATION_FAILED, selected_port=_configured_port(), probe_state=api_state, runtime_class="installed_bundled")
        try:
            window.load_html(_error_html(WEBVIEW_NAVIGATION_FAILED))  # type: ignore[attr-defined]
        except Exception:
            pass
        return
    if bridge is None:
        return
    deadline = time.monotonic() + FRONTEND_READY_TIMEOUT_SECONDS
    while not bridge._frontend_ready_event.is_set() and time.monotonic() < deadline:
        try:
            marker = window.evaluate_js(  # type: ignore[attr-defined]
                "Boolean(globalThis.__localAiHubFrontendRendered && globalThis.__localAiHubFrontendBootstrapReady)"
            )
        except Exception:
            marker = False
        if marker is True or marker == "true":
            result = bridge.confirm_frontend_ready()
            if result.get("status") == "ready":
                bridge._frontend_ready_event.set()
                break
            _record_startup_event("frontend_ready_rejected", selected_port=_configured_port(), probe_state=api_state, runtime_class="installed_bundled")
            break
        time.sleep(0.2)
    if not bridge._frontend_ready_event.is_set():
        _record_startup_event("frontend_timeout", selected_port=_configured_port(), probe_state=api_state, runtime_class="installed_bundled")
        try:
            window.load_html(_error_html(FRONTEND_BOOTSTRAP_TIMEOUT))  # type: ignore[attr-defined]
        except Exception:
            pass


def _load_window_settings() -> tuple[int, int, bool]:
    """Read minimum window dimensions and start_maximized from settings.json."""
    try:
        from src.app_config.settings_service import SettingsPersistence
        from src.shared.paths.registry import CONFIG_ROOT
        svc = SettingsPersistence(CONFIG_ROOT / "settings.json")
        loaded = svc.load()
        settings = loaded.get("settings", {})
        window_cfg = settings.get("window", {}) if isinstance(settings, dict) else {}
        min_w = max(800, min(3840, int(window_cfg.get("minimum_width", 1280))))
        min_h = max(600, min(2160, int(window_cfg.get("minimum_height", 720))))
        maximized = bool(window_cfg.get("start_maximized", True))
        return min_w, min_h, maximized
    except Exception:
        return 1280, 720, True


def main() -> int:
    try:
        import webview
    except ImportError:
        print("pywebview is required for the desktop shell. Install requirements-hub.txt in the Hub environment.", file=sys.stderr)
        return 2

    min_w, min_h, start_maximized = _load_window_settings()

    with startup_mutex(_scoped_mutex(APP_INSTANCE_MUTEX), 0.5) as instance_acquired:
        if not instance_acquired:
            print("Local AI Hub is already running in another instance.", file=sys.stderr)
            return 0

        try:
            _record_startup_event("desktop_started", selected_port=_configured_port(), probe_state="unknown", runtime_class="installed_bundled")
            _record_desktop_readiness("desktop_started", status="starting")
            bridge = DesktopBridge()
            window_kwargs = {
                "html": _loading_html(),
                "width": min_w,
                "height": min_h,
                "min_size": (1280, 720),
                "resizable": True,
                "confirm_close": False,
                "js_api": bridge,
            }
            # pywebview versions differ: newer hosts may accept an icon path,
            # while the reviewed host does not. Never pass an unsupported kwarg
            # and never substitute a system/Python icon.
            if "icon" in inspect.signature(webview.create_window).parameters:
                window_kwargs["icon"] = _canonical_icon_path()
            window = webview.create_window("Local AI Hub", **window_kwargs)
            bridge._bind(window)
            window.events.closing += bridge._request_window_close

            def initialize_window() -> None:
                if start_maximized:
                    try:
                        window.maximize()
                    except Exception:
                        pass
                threading.Thread(
                    target=_load_ui_when_ready,
                    args=(window, bridge),
                    name="LocalAIHub-API-startup",
                    daemon=True,
                ).start()

            try:
                webview.start(initialize_window, gui="edgechromium", debug=False)
            finally:
                _record_startup_event("desktop_exit", selected_port=_configured_port(), probe_state="unknown", runtime_class="installed_bundled")
                _record_desktop_readiness("desktop_exit", status="stopped")
                if bridge._controller and bridge._controller.cleanup_allowed:
                    close_owned_idle_backends()
                    close_owned_api()
            return 0
        except Exception as exc:  # pragma: no cover - native GUI errors are host-specific
            print(f"Local AI Hub desktop shell failed: {exc}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
