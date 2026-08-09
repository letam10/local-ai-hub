"""Hub-owned Windows process helpers.

Workers receive no visible console window, keep an explicit PID owner, and
write their diagnostics below ``Logs``.  The helpers never enumerate or kill
generic Python/ComfyUI processes; only handles created by this module can be
stopped.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence

from src.shared.paths.registry import LOG_ROOT
from src.services.process_manager.windows import (
    hidden_startupinfo as _hidden_startupinfo,
    no_console_flags as _no_console_flags,
    popen_hidden,
    terminate_process_tree,
)


class ProcessOwner(Protocol):
    def attach_process(self, process: subprocess.Popen[bytes], label: str) -> None: ...

    def detach_process(self, process: subprocess.Popen[bytes]) -> None: ...

    @property
    def cancelled(self) -> bool: ...


def no_console_flags() -> int:
    """Compatibility export for the shared CREATE_NO_WINDOW policy."""

    return _no_console_flags()


def hidden_startupinfo() -> subprocess.STARTUPINFO | None:
    """Compatibility export for the shared SW_HIDE startup policy."""

    return _hidden_startupinfo()


def _safe_log_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)[:100] or "worker"


def _log_path(label: str, job_id: str | None = None) -> Path:
    directory = LOG_ROOT / "workers"
    directory.mkdir(parents=True, exist_ok=True)
    suffix = f"_{_safe_log_name(job_id)}" if job_id else ""
    return directory / f"{_safe_log_name(label)}{suffix}.log"


def _append_log(path: Path, heading: str, content: bytes) -> None:
    with path.open("ab") as handle:
        handle.write(f"\n[{heading}]\n".encode("utf-8"))
        handle.write(content[-1_000_000:])
        if not content.endswith(b"\n"):
            handle.write(b"\n")


def terminate_owned_process(process: subprocess.Popen[Any]) -> None:
    """Stop exactly a process launched by the Hub, including its child tree."""

    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            if terminate_process_tree(process.pid):
                process.wait(timeout=5)
                return
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        process.terminate()
        process.wait(timeout=5)
        return
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        process.kill()
        process.wait(timeout=5)
    except (OSError, subprocess.SubprocessError):
        return


def run_json_worker(
    command: Sequence[str],
    payload: dict[str, Any],
    *,
    label: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    owner: ProcessOwner | None = None,
    timeout_seconds: float = 900,
) -> dict[str, Any]:
    """Run an allowlisted JSON worker without creating a user-visible window."""

    log_path = _log_path(label, getattr(owner, "job_id", None))
    stdout = b""
    with log_path.open("ab") as stderr_handle:
        try:
            process = popen_hidden(
                [str(item) for item in command],
                cwd=cwd,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr_handle,
            )
        except OSError as exc:
            return {"status": "error", "error": str(exc)}
        if owner:
            owner.attach_process(process, label)
        try:
            assert process.stdin is not None
            process.stdin.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            process.stdin.close()
            deadline = time.monotonic() + timeout_seconds
            while process.poll() is None:
                if owner and owner.cancelled:
                    terminate_owned_process(process)
                    return {"status": "cancelled", "reason": "Tác vụ đã được người dùng hủy."}
                if time.monotonic() >= deadline:
                    terminate_owned_process(process)
                    return {"status": "error", "error": f"{label} vượt quá thời gian cho phép của Hub."}
                time.sleep(0.15)
            stdout = process.stdout.read() if process.stdout else b""
        finally:
            if owner:
                owner.detach_process(process)
    _append_log(log_path, "stdout", stdout)
    text = stdout.decode("utf-8", errors="replace")
    json_line = next((line for line in reversed(text.splitlines()) if line.lstrip().startswith("{")), "")
    try:
        result = json.loads(json_line)
    except json.JSONDecodeError:
        result = {"status": "error", "error": f"{label} không trả về JSON hợp lệ."}
    if process.returncode != 0 and result.get("status") != "cancelled":
        result.setdefault("status", "error")
        result.setdefault("error", f"{label} kết thúc với mã {process.returncode}.")
    return result if isinstance(result, dict) else {"status": "error", "error": f"{label} trả về dữ liệu không hợp lệ."}


def run_command(
    command: Sequence[str],
    *,
    label: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    owner: ProcessOwner | None = None,
    timeout_seconds: float = 900,
) -> tuple[int, str]:
    """Run a fixed command without a console; return code and short output tail."""

    log_path = _log_path(label, getattr(owner, "job_id", None))
    stdout = b""
    with log_path.open("ab") as stderr_handle:
        try:
            process = popen_hidden(
                [str(item) for item in command],
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=stderr_handle,
            )
        except OSError as exc:
            return -1, str(exc)
        if owner:
            owner.attach_process(process, label)
        try:
            deadline = time.monotonic() + timeout_seconds
            while process.poll() is None:
                if owner and owner.cancelled:
                    terminate_owned_process(process)
                    return -2, "Tác vụ đã được người dùng hủy."
                if time.monotonic() >= deadline:
                    terminate_owned_process(process)
                    return -3, f"{label} vượt quá thời gian cho phép của Hub."
                time.sleep(0.15)
            stdout = process.stdout.read() if process.stdout else b""
        finally:
            if owner:
                owner.detach_process(process)
    _append_log(log_path, "stdout", stdout)
    return process.returncode or 0, stdout.decode("utf-8", errors="replace")[-6000:]


@dataclass
class BackgroundProcess:
    key: str
    process: subprocess.Popen[bytes]
    owned: bool


class BackgroundProcessRegistry:
    """Tracks only background services started by this API process."""

    def __init__(self) -> None:
        self._items: dict[str, BackgroundProcess] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> BackgroundProcess | None:
        with self._lock:
            item = self._items.get(key)
            if item and item.process.poll() is not None:
                self._items.pop(key, None)
                return None
            return item

    def start(self, key: str, command: Sequence[str], *, cwd: Path, env: dict[str, str]) -> BackgroundProcess:
        with self._lock:
            existing = self.get(key)
            if existing:
                return existing
            log_path = _log_path(f"backend_{key}")
            handle = log_path.open("ab")
            try:
                process = popen_hidden(
                    [str(item) for item in command],
                    cwd=cwd,
                    env=env,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                )
            except Exception:
                handle.close()
                raise
            item = BackgroundProcess(key=key, process=process, owned=True)
            self._items[key] = item
            return item

    def stop(self, key: str) -> bool:
        with self._lock:
            item = self._items.pop(key, None)
        if not item:
            return False
        terminate_owned_process(item.process)
        return True

    def stop_all(self) -> list[str]:
        with self._lock:
            keys = list(self._items)
        return [key for key in keys if self.stop(key)]


background_processes = BackgroundProcessRegistry()
