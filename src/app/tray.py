"""A reliable Windows notification-area surface without a new dependency.

The desktop never hides until this module has registered a real icon with
Restore and Exit actions.  All Win32 signatures are explicit so handles stay
pointer-width safe on 64-bit Windows.
"""

from __future__ import annotations

import ctypes
import os
import threading
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path


class WindowsTray:
    """Minimal Restore / Exit icon for the existing Windows shell."""

    _CALLBACK = 0x8000 + 73  # WM_APP + 73
    _RESTORE = 1001
    _EXIT = 1002
    _WM_CLOSE = 0x0010
    _WM_DESTROY = 0x0002
    _NIM_ADD = 0x00000000
    _NIM_DELETE = 0x00000002
    _NIF_MESSAGE = 0x0001
    _NIF_ICON = 0x0002
    _NIF_TIP = 0x0004
    _WS_EX_TOOLWINDOW = 0x00000080
    _IMAGE_ICON = 1
    _LR_LOADFROMFILE = 0x00000010
    _LR_DEFAULTSIZE = 0x00000040

    def __init__(self, on_restore: Callable[[], None], on_exit: Callable[[], None]) -> None:
        self._on_restore = on_restore
        self._on_exit = on_exit
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._stop_requested = threading.Event()
        self._thread: threading.Thread | None = None
        self._hwnd: int | None = None
        self._error = ""
        self._generation = 0
        self._lock = threading.RLock()
        self._wndproc: object | None = None  # retain the native callback

    @staticmethod
    def _post_close(hwnd: int) -> None:
        if os.name != "nt":
            return
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            user32.PostMessageW.restype = wintypes.BOOL
            user32.PostMessageW(wintypes.HWND(hwnd), WindowsTray._WM_CLOSE, 0, 0)
        except (AttributeError, OSError):
            return

    def start(self) -> tuple[bool, str]:
        if os.name != "nt":
            return False, "Chạy nền qua khay thông báo chỉ khả dụng trên Windows. Hub vẫn được giữ mở."
        with self._lock:
            if self._thread and self._thread.is_alive():
                if self._stop_requested.is_set():
                    return False, "Khay thông báo cũ vẫn đang dừng; Hub vẫn được giữ mở."
                return True, "Local AI Hub đang chạy nền; dùng biểu tượng khay để Khôi phục hoặc Thoát."
            self._ready.clear()
            self._stopped.clear()
            self._stop_requested.clear()
            self._error = ""
            self._generation += 1
            self._thread = threading.Thread(target=self._run, args=(self._generation,), name="LocalAIHub-tray", daemon=True)
            self._thread.start()
        if not self._ready.wait(2.0):
            # Do not let a late-starting thread create an unreachable icon.
            self._stop_requested.set()
            with self._lock:
                hwnd = self._hwnd
            if hwnd:
                self._post_close(hwnd)
            self._stopped.wait(1.0)
            return False, "Không thể tạo khay thông báo đáng tin cậy; Hub vẫn được giữ mở."
        if self._error:
            return False, self._error
        return True, "Local AI Hub đang chạy nền; dùng biểu tượng khay để Khôi phục hoặc Thoát."

    def stop(self) -> None:
        self._stop_requested.set()
        with self._lock:
            hwnd = self._hwnd
            thread = self._thread
        if hwnd:
            self._post_close(hwnd)
        if thread and thread is not threading.current_thread():
            self._stopped.wait(1.5)
            thread.join(timeout=0.2)

    def _run_callback(self, callback: Callable[[], None]) -> None:
        threading.Thread(target=callback, name="LocalAIHub-tray-action", daemon=True).start()

    def _run(self, generation: int) -> None:  # pragma: no cover - exercised by final Windows smoke
        hwnd: wintypes.HWND | None = None
        instance: wintypes.HINSTANCE | None = None
        class_name = f"LocalAIHubTray_{os.getpid()}_{id(self)}_{generation}"
        registered = False
        icon_added = False
        nid: object | None = None
        user32 = shell32 = None
        try:
            if os.name != "nt":
                raise OSError("Windows tray unavailable")
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            shell32 = ctypes.WinDLL("shell32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            LRESULT = ctypes.c_ssize_t
            UINT_PTR = ctypes.c_size_t
            # ``ctypes.wintypes`` omits HCURSOR in some supported CPython
            # builds even though it exposes the same pointer-width HANDLE.
            # Keep the WNDCLASS layout native-width instead of failing before
            # the tray can register on those hosts.
            HCURSOR = getattr(wintypes, "HCURSOR", wintypes.HANDLE)
            WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

            class WNDCLASSEXW(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                    ("hIcon", wintypes.HICON), ("hCursor", HCURSOR), ("hbrBackground", wintypes.HBRUSH),
                    ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON),
                ]

            class NOTIFYICONDATAW(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                    ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                    ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
                    ("szInfo", wintypes.WCHAR * 256), ("uVersion", wintypes.UINT),
                    ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", wintypes.DWORD),
                    ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wintypes.HICON),
                ]

            kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
            kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
            user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
            user32.RegisterClassExW.restype = wintypes.ATOM
            user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
            user32.UnregisterClassW.restype = wintypes.BOOL
            user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
            user32.CreateWindowExW.restype = wintypes.HWND
            user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            user32.DefWindowProcW.restype = LRESULT
            user32.DestroyWindow.argtypes = [wintypes.HWND]
            user32.DestroyWindow.restype = wintypes.BOOL
            user32.PostQuitMessage.argtypes = [ctypes.c_int]
            user32.PostQuitMessage.restype = None
            user32.CreatePopupMenu.argtypes = []
            user32.CreatePopupMenu.restype = wintypes.HMENU
            user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, UINT_PTR, wintypes.LPCWSTR]
            user32.AppendMenuW.restype = wintypes.BOOL
            user32.DestroyMenu.argtypes = [wintypes.HMENU]
            user32.DestroyMenu.restype = wintypes.BOOL
            user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
            user32.GetCursorPos.restype = wintypes.BOOL
            user32.SetForegroundWindow.argtypes = [wintypes.HWND]
            user32.SetForegroundWindow.restype = wintypes.BOOL
            user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, ctypes.c_void_p]
            user32.TrackPopupMenu.restype = wintypes.UINT
            user32.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
            user32.LoadIconW.restype = wintypes.HICON
            user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
            user32.GetMessageW.restype = ctypes.c_int
            user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
            user32.TranslateMessage.restype = wintypes.BOOL
            user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
            user32.DispatchMessageW.restype = LRESULT
            shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
            shell32.Shell_NotifyIconW.restype = wintypes.BOOL
            user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
            user32.LoadImageW.restype = wintypes.HANDLE

            def show_menu(owner: wintypes.HWND) -> None:
                menu = user32.CreatePopupMenu()
                if not menu:
                    return
                try:
                    user32.AppendMenuW(menu, 0x0000, self._RESTORE, "Khôi phục Local AI Hub")
                    user32.AppendMenuW(menu, 0x0800, 0, None)
                    user32.AppendMenuW(menu, 0x0000, self._EXIT, "Thoát Local AI Hub")
                    point = wintypes.POINT()
                    user32.GetCursorPos(ctypes.byref(point))
                    user32.SetForegroundWindow(owner)
                    command = user32.TrackPopupMenu(menu, 0x0100 | 0x0002, point.x, point.y, 0, owner, None)
                    if command == self._RESTORE:
                        self._run_callback(self._on_restore)
                    elif command == self._EXIT:
                        self._run_callback(self._on_exit)
                finally:
                    user32.DestroyMenu(menu)

            def callback(owner: wintypes.HWND, message: int, _wparam: int, lparam: int) -> int:
                if message == self._CALLBACK:
                    if lparam in {0x0202, 0x0203}:
                        self._run_callback(self._on_restore)
                    elif lparam == 0x0205:
                        show_menu(owner)
                    return 0
                if message == self._WM_CLOSE:
                    user32.DestroyWindow(owner)
                    return 0
                if message == self._WM_DESTROY:
                    user32.PostQuitMessage(0)
                    return 0
                return user32.DefWindowProcW(owner, message, _wparam, lparam)

            self._wndproc = WNDPROC(callback)
            instance = kernel32.GetModuleHandleW(None)
            window_class = WNDCLASSEXW()
            window_class.cbSize = ctypes.sizeof(WNDCLASSEXW)
            window_class.lpfnWndProc = self._wndproc
            window_class.hInstance = instance
            window_class.lpszClassName = class_name
            if not user32.RegisterClassExW(ctypes.byref(window_class)):
                raise OSError("Windows không thể đăng ký cửa sổ khay Local AI Hub.")
            registered = True
            hwnd = user32.CreateWindowExW(self._WS_EX_TOOLWINDOW, class_name, class_name, 0, 0, 0, 0, 0, None, None, instance, None)
            if not hwnd:
                raise OSError("Windows không thể tạo bề mặt khay Local AI Hub.")
            with self._lock:
                self._hwnd = int(hwnd)
            if self._stop_requested.is_set():
                user32.DestroyWindow(hwnd)
                hwnd = None
                return
            tray_icon = NOTIFYICONDATAW()
            tray_icon.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            tray_icon.hWnd = hwnd
            tray_icon.uID = 1
            tray_icon.uFlags = self._NIF_MESSAGE | self._NIF_ICON | self._NIF_TIP
            tray_icon.uCallbackMessage = self._CALLBACK
            install_root = os.environ.get("LOCALAIHUB_INSTALL_ROOT")
            icon_path = Path(install_root) / "local-ai-hub.ico" if install_root else None
            if icon_path is None or not icon_path.is_file() or icon_path.is_symlink():
                raise OSError("Canonical Local AI Hub icon is unavailable.")
            tray_icon.hIcon = user32.LoadImageW(None, str(icon_path), self._IMAGE_ICON, 0, 0, self._LR_LOADFROMFILE | self._LR_DEFAULTSIZE)
            if not tray_icon.hIcon:
                raise OSError("Canonical Local AI Hub icon could not be loaded.")
            tray_icon.szTip = "Local AI Hub — jobs đang chạy"
            nid = tray_icon
            if not shell32.Shell_NotifyIconW(self._NIM_ADD, ctypes.byref(tray_icon)):
                raise OSError("Windows từ chối đăng ký biểu tượng khay Local AI Hub.")
            icon_added = True
            self._ready.set()
            message = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except Exception:
            self._error = "Không thể tạo khay thông báo đáng tin cậy; Hub vẫn được giữ mở."
            self._ready.set()
        finally:
            if shell32 is not None and icon_added and nid is not None:
                try:
                    shell32.Shell_NotifyIconW(self._NIM_DELETE, ctypes.byref(nid))
                except (AttributeError, OSError):
                    pass
            if user32 is not None and hwnd:
                try:
                    user32.DestroyWindow(hwnd)
                except (AttributeError, OSError):
                    pass
            if user32 is not None and registered and instance is not None:
                try:
                    user32.UnregisterClassW(class_name, instance)
                except (AttributeError, OSError):
                    pass
            with self._lock:
                self._hwnd = None
                if self._thread is threading.current_thread():
                    self._thread = None
                self._wndproc = None
            self._ready.set()
            self._stopped.set()
