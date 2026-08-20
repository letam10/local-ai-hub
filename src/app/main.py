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
import json
import os
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

from .desktop_lifecycle import DesktopCloseController
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


def _api_ready() -> bool:
    try:
        with urllib.request.urlopen(f"{_api_base_url()}/health", timeout=0.35) as response:
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
            raise RuntimeError(f"Local AI Hub API startup lock timed out at {_api_base_url()}.")
        # Resolve the same Core interpreter used by the desktop launcher.  An
        # explicit environment override remains a development-only escape
        # hatch, but is accepted only when it is a regular file under a
        # bounded trusted root; arbitrary client paths never enter the API.
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
            raise RuntimeError("Local AI Hub Core Python is unavailable; run scripts/bootstrap_core.ps1 first.")
        python = str(candidate)
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
    raise RuntimeError(f"Local AI Hub API did not become ready at {_api_base_url()}.")


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


def _prepare_owned_api_close() -> tuple[bool, int, str]:
    """Close job admission and recheck under the owned API's server lock."""

    if not _owns_live_api():
        return True, 0, ""
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
        return False, 1, f"Không thể khóa nhận job mới trước khi đóng: {exc}"
    if not isinstance(value, dict):
        return False, 1, "Phản hồi chuẩn bị đóng API không hợp lệ; Hub vẫn được giữ mở."
    active = max(0, int(value.get("active_jobs", 0) or 0))
    if value.get("status") == "ready_to_close":
        return True, 0, str(value.get("message") or "")
    return False, max(1, active), str(value.get("message") or "Hub phát hiện job đang hoạt động; cửa sổ vẫn được giữ mở.")


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
        # Keep the native component picker in a nested bridge namespace so
        # the top-level close API remains the exact three-choice contract.
        self.component_import = _ComponentSelectionBridge(self)

    def _bind(self, window: object) -> None:
        self._window = window
        self._controller = DesktopCloseController(
            _api_active_job_count,
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


def _close_prompt_html(detail: dict[str, object]) -> str:
    """Local fallback for a close prompt delivered during a WebView transition."""

    count = max(0, int(detail.get("active_jobs", 0) or 0))
    message = html.escape(str(detail.get("message") or "Chọn một trong ba cách tiếp tục an toàn."))
    return f"""<!doctype html><html lang="vi"><meta charset="utf-8"><title>Local AI Hub</title>
    <style>html,body{{margin:0;height:100%;background:#0b1020;color:#edf2ff;font-family:Segoe UI,system-ui,sans-serif}}main{{max-width:680px;margin:0 auto;height:100%;display:grid;align-content:center;gap:16px;padding:28px;box-sizing:border-box}}.eyebrow{{color:#80aaff;font-size:12px;letter-spacing:.12em}}p,small{{color:#b7c3df;line-height:1.55}}.actions{{display:flex;flex-wrap:wrap;gap:10px}}button{{border:1px solid #45639d;border-radius:9px;background:#182340;color:#edf2ff;padding:10px 14px;font:inherit;cursor:pointer}}button.primary{{background:#4d7dff;border-color:#80aaff}}button.danger{{background:#562737;border-color:#b95c71}}button:disabled{{opacity:.65;cursor:wait}}</style>
    <main><span class="eyebrow">JOBS ĐANG HOẠT ĐỘNG</span><h1>Bạn muốn xử lý Local AI Hub thế nào?</h1><p>{count} job đang chờ, chuẩn bị, chạy hoặc hủy. Hub không tự dừng worker đang hoạt động.</p><p id="status">{message}</p><div class="actions"><button onclick="choose('return_to_hub')">Quay lại Hub</button><button class="danger" onclick="choose('cancel_jobs_and_exit')">Hủy jobs và thoát</button><button class="primary" onclick="choose('keep_running_in_background')">Giữ chạy nền vào khay</button></div><small>Chạy nền chỉ ẩn cửa sổ sau khi Windows đã tạo biểu tượng khay có lệnh Khôi phục và Thoát.</small></main>
    <script>async function choose(name){{const buttons=[...document.querySelectorAll('button')];const status=document.getElementById('status');const api=window.pywebview&&window.pywebview.api;if(!api||!api[name]){{status.textContent='Desktop bridge chưa sẵn sàng; Hub vẫn được giữ mở an toàn.';return}}buttons.forEach(button=>button.disabled=true);try{{const result=await api[name]();status.textContent=(result&&result.message)||'Đã nhận lựa chọn.';if(!result||result.status!=='pending')buttons.forEach(button=>button.disabled=false)}}catch(error){{status.textContent='Không thể xử lý lựa chọn: '+error;buttons.forEach(button=>button.disabled=false)}}}}</script></html>"""


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
        window.load_url(_ui_url())  # type: ignore[attr-defined]
    except Exception:
        # The user can still close the loading window normally if WebView2 fails.
        return


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

    with startup_mutex(APP_INSTANCE_MUTEX, 0.5) as instance_acquired:
        if not instance_acquired:
            print("Local AI Hub is already running in another instance.", file=sys.stderr)
            return 0

        try:
            bridge = DesktopBridge()
            window = webview.create_window(
                "Local AI Hub",
                html=_loading_html(),
                width=min_w,
                height=min_h,
                min_size=(1280, 720),
                resizable=True,
                confirm_close=False,
                js_api=bridge,
            )
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
                    args=(window,),
                    name="LocalAIHub-API-startup",
                    daemon=True,
                ).start()

            try:
                webview.start(initialize_window, gui="edgechromium", debug=False)
            finally:
                if bridge._controller and bridge._controller.cleanup_allowed:
                    close_owned_idle_backends()
                    close_owned_api()
            return 0
        except Exception as exc:  # pragma: no cover - native GUI errors are host-specific
            print(f"Local AI Hub desktop shell failed: {exc}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
