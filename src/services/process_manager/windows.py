"""Safe Windows process primitives shared by every Hub-owned command.

The desktop application is normally started through ``pythonw.exe``.  Child
processes do not inherit that guarantee automatically on Windows, therefore
every command launched by the Hub must opt into the same hidden-window policy.
This module is deliberately small so adapters can use it without importing a
model runtime or the job manager.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Sequence


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
