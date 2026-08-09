"""Safe Windows process primitives shared by every Hub-owned command.

The desktop application is normally started through ``pythonw.exe``.  Child
processes do not inherit that guarantee automatically on Windows, therefore
every command launched by the Hub must opt into the same hidden-window policy.
This module is deliberately small so adapters can use it without importing a
model runtime or the job manager.
"""

from __future__ import annotations

from contextlib import contextmanager
import os
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterator, Sequence


def _windows_process_tree(root_pid: int) -> list[int]:
    """Return ``root_pid`` and its current descendants without spawning a shell."""

    if os.name != "nt":
        return [root_pid]
    import ctypes
    from ctypes import wintypes

    class ProcessEntry32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W))
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W))
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    invalid_handle = ctypes.c_void_p(-1).value
    if snapshot == invalid_handle:
        return [root_pid]
    children: dict[int, list[int]] = defaultdict(list)
    try:
        entry = ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(ProcessEntry32W)
        if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            while True:
                children[int(entry.th32ParentProcessID)].append(int(entry.th32ProcessID))
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
    finally:
        kernel32.CloseHandle(snapshot)

    result: list[int] = []
    pending = [root_pid]
    seen: set[int] = set()
    while pending:
        process_id = pending.pop()
        if process_id in seen:
            continue
        seen.add(process_id)
        result.append(process_id)
        pending.extend(children.get(process_id, ()))
    return result


def _terminate_windows_process(process_id: int) -> bool:
    """Terminate one known Windows process without launching ``taskkill.exe``."""

    import ctypes
    from ctypes import wintypes

    process_terminate = 0x0001
    synchronize = 0x00100000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(process_terminate | synchronize, False, process_id)
    if not handle:
        return False
    try:
        if not kernel32.TerminateProcess(handle, 1):
            return False
        kernel32.WaitForSingleObject(handle, 5_000)
        return True
    finally:
        kernel32.CloseHandle(handle)


def terminate_process_tree(process_id: int) -> bool:
    """Terminate a Hub-owned process tree without a console or confirmation prompt."""

    if os.name != "nt":
        return False
    stopped = False
    for child_id in reversed(_windows_process_tree(process_id)):
        stopped = _terminate_windows_process(child_id) or stopped
    return stopped


@contextmanager
def startup_mutex(name: str, timeout_seconds: float) -> Iterator[bool]:
    """Serialize a short Hub startup critical section across Windows processes."""

    if os.name != "nt":
        yield True
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.ReleaseMutex.argtypes = (wintypes.HANDLE,)
    kernel32.ReleaseMutex.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        yield False
        return
    acquired = kernel32.WaitForSingleObject(handle, max(0, int(timeout_seconds * 1_000))) in {0x00000000, 0x00000080}
    try:
        yield acquired
    finally:
        if acquired:
            kernel32.ReleaseMutex(handle)
        kernel32.CloseHandle(handle)


def no_console_flags() -> int:
    """Return the Windows flags required for a non-interactive child process."""

    return getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def hidden_startupinfo() -> subprocess.STARTUPINFO | None:
    """Ask Windows to hide a third-party process even when it is console based."""

    if os.name != "nt":
        return None
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = subprocess.SW_HIDE
    return info


def minimized_startupinfo() -> subprocess.STARTUPINFO | None:
    """Return the explicit last-resort policy for a proven unhideable tool.

    Hub-owned commands must use :func:`hidden_startupinfo` by default.  This
    helper exists only for a third-party GUI that has been verified to ignore
    ``SW_HIDE``; callers must document that exception and keep it off the
    normal workflow.  No current Hub backend needs this fallback.
    """

    if os.name != "nt":
        return None
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = subprocess.SW_MINIMIZE
    return info


def hidden_popen_kwargs(**overrides: Any) -> dict[str, Any]:
    """Build the mandatory process options for a Hub-owned background command.

    ``stdin`` defaults to ``DEVNULL`` so a command can never attach to the
    desktop terminal.  Callers may explicitly replace it with ``PIPE`` for the
    JSON worker protocol.  Standard output and error are intentionally left to
    the caller: small read-only commands can use pipes while long workers write
    to Hub logs.
    """

    options: dict[str, Any] = {
        "shell": False,
        "stdin": subprocess.DEVNULL,
        "creationflags": no_console_flags(),
        "startupinfo": hidden_startupinfo(),
    }
    options.update(overrides)
    return options


def minimized_popen_kwargs(**overrides: Any) -> dict[str, Any]:
    """Build the documented fallback options without creating a console."""

    options: dict[str, Any] = {
        "shell": False,
        "stdin": subprocess.DEVNULL,
        "creationflags": no_console_flags(),
        "startupinfo": minimized_startupinfo(),
    }
    options.update(overrides)
    return options


def popen_hidden(
    command: Sequence[str | Path],
    *,
    cwd: Path | str | None = None,
    env: dict[str, str] | None = None,
    **overrides: Any,
) -> subprocess.Popen[Any]:
    """Start a Hub-owned command without a visible Windows console window."""

    return subprocess.Popen(
        [str(item) for item in command],
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        **hidden_popen_kwargs(**overrides),
    )


def popen_minimized(
    command: Sequence[str | Path],
    *,
    cwd: Path | str | None = None,
    env: dict[str, str] | None = None,
    **overrides: Any,
) -> subprocess.Popen[Any]:
    """Start a documented exception minimized, never with a new console."""

    return subprocess.Popen(
        [str(item) for item in command],
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        **minimized_popen_kwargs(**overrides),
    )


def run_hidden(
    command: Sequence[str | Path],
    *,
    cwd: Path | str | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
    check: bool = False,
    **overrides: Any,
) -> subprocess.CompletedProcess[Any]:
    """Run a short Hub-owned command with the same hidden-window policy."""

    return subprocess.run(
        [str(item) for item in command],
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        timeout=timeout,
        check=check,
        **hidden_popen_kwargs(**overrides),
    )
