"""Verify the real installed desktop app without treating HTTP 200 as readiness.

The command is intentionally conservative: a healthy loopback response is
useful evidence, but the result remains ``user_app_recovered=false`` until an
owned desktop window, exact payload identity, frontend-ready event and route
coverage agree.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time
import urllib.request
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

try:
    import psutil
except ImportError:  # pragma: no cover - optional on a stripped runtime
    psutil = None  # type: ignore[assignment]

try:
    import pygetwindow
except ImportError:  # pragma: no cover - optional on headless hosts
    pygetwindow = None  # type: ignore[assignment]

from src.app.readiness import classify_state, event_seen, evaluate_readiness, load_state


ROUTES = ("dashboard", "models", "diagnostics", "settings")


def _json(path: Path, limit: int = 64 * 1024) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def _get_json(url: str, timeout: float) -> tuple[int, object, float]:
    started = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            raw = response.read(512 * 1024 + 1)
            status = int(response.status)
        try:
            payload: object = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            payload = None
        return status, payload, time.monotonic() - started
    except Exception:
        return 0, None, time.monotonic() - started


def _desktop_process(payload_root: Path) -> tuple[int | None, bool, float | None]:
    if psutil is None:
        return None, False, None
    expected = str(payload_root).casefold()
    for process in psutil.process_iter(["pid", "cmdline", "create_time"]):
        try:
            command = " ".join(str(part) for part in (process.info.get("cmdline") or []))
            if "src.app.launcher" not in command.casefold() or expected not in command.casefold():
                continue
            return int(process.info["pid"]), process.is_running(), max(0.0, time.time() - float(process.info["create_time"]))
        except (psutil.Error, TypeError, ValueError, KeyError):
            continue
    return None, False, None


def _window_state() -> tuple[bool, bool, str | None]:
    if pygetwindow is None:
        return False, False, None
    try:
        windows = pygetwindow.getWindowsWithTitle("Local AI Hub")
        for window in windows:
            visible = not bool(getattr(window, "isMinimized", False)) and int(window.width) > 200 and int(window.height) > 150
            hwnd = getattr(window, "_hWnd", None)
            if hwnd and os.name == "nt":
                import ctypes
                user32 = ctypes.windll.user32
                visible = visible and bool(user32.IsWindowVisible(hwnd))
                responding = visible and not bool(user32.IsHungAppWindow(hwnd))
            else:
                responding = visible
            return visible, responding, str(window.title)
    except Exception:
        return False, False, None
    return False, False, None


def verify(install_root: Path) -> dict[str, Any]:
    root = Path(install_root).absolute()
    pointer = _json(root / "current.json", 16 * 1024)
    payload_id = str(pointer.get("version") or "")
    payload_root = root / "versions" / payload_id
    build = _json(payload_root / "build.json", 32 * 1024)
    source_commit = str(build.get("source_commit") or "")
    health_status, health, health_seconds = _get_json("http://127.0.0.1:8765/health", 5.0)
    bootstrap_status, bootstrap, bootstrap_seconds = _get_json("http://127.0.0.1:8765/api/bootstrap", 20.0)
    desktop_pid, desktop_alive, desktop_age = _desktop_process(payload_root)
    window_visible, window_responding, window_title = _window_state()
    raw_state = load_state(root)
    state = classify_state(raw_state, desktop_process_alive=desktop_alive, current_pid=desktop_pid)
    api_identity_match = isinstance(health, dict) and health.get("build_source_commit") == source_commit and health.get("build_payload_id") == payload_id
    payload_identity_match = bool(payload_id.startswith("main-") and source_commit and payload_id == f"main-{source_commit[:12]}")
    bootstrap_valid = isinstance(bootstrap, dict) and bootstrap.get("status") == "completed" and all(key in bootstrap for key in ("health", "components", "settings", "capabilities", "applications", "tools"))
    frontend_pid_match = desktop_pid is not None and state.get("pid") == desktop_pid
    frontend_ready = frontend_pid_match and state.get("active_session") is True and state.get("status") in {"running", "ready"} and event_seen(state, "frontend_ready")
    dashboard_rendered = frontend_pid_match and (
        event_seen(state, "route_rendered", route="dashboard")
        or event_seen(state, "frontend_rendered", route="dashboard")
    )
    routes_complete = frontend_pid_match and all(event_seen(state, "route_rendered", route=route) for route in ROUTES)
    evidence = {
        "desktop_process_alive": desktop_alive,
        "window_visible": window_visible,
        "window_responding": window_responding,
        "api_identity_match": api_identity_match,
        "payload_identity_match": payload_identity_match,
        "bootstrap_payload_valid": bootstrap_valid,
        "frontend_ready": frontend_ready,
        "dashboard_rendered": dashboard_rendered,
        "routes_complete": routes_complete,
        "stable_window": desktop_age is not None and desktop_age >= 5.0,
    }
    decision = evaluate_readiness(evidence)
    return {
        "status": decision["status"],
        "user_app_recovered": decision["user_app_recovered"],
        "missing": decision["missing"],
        "http_status_ignored": True,
        "current_payload": payload_id,
        "source_commit": source_commit,
        "desktop_pid": desktop_pid,
        "desktop_age_seconds": round(desktop_age, 2) if desktop_age is not None else None,
        "window_title": window_title,
        "api_status": health_status,
        "bootstrap_status": bootstrap_status,
        "api_health_seconds": round(health_seconds, 3),
        "bootstrap_seconds": round(bootstrap_seconds, 3),
        "evidence": evidence,
        "readiness_state": state,
        "readiness_session": {
            "status": state.get("status", "unknown"),
            "stale": state.get("stale") is True,
            "active": state.get("active_session") is True,
            "exit_reason": state.get("exit_reason"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--install-root", default=os.environ.get("LOCALAIHUB_INSTALL_ROOT") or r"C:\Users\TAM\AppData\Local\Programs\LocalAIHub")
    args = parser.parse_args()
    result = verify(Path(args.install_root))
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
    return 0 if result["user_app_recovered"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
