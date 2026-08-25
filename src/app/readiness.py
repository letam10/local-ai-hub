"""Evidence contract for declaring the installed desktop app ready.

An HTTP response is only transport evidence.  This module deliberately keeps
the readiness decision separate from HTTP status codes and requires proof that
the owned desktop window rendered the frontend against the exact payload.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping


READINESS_SCHEMA = "v8-desktop-readiness.v1"
READINESS_FILE = "desktop-readiness.json"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_PAYLOAD_RE = re.compile(r"^main-[0-9a-f]{12}$")
_EVENTS = frozenset({
    "desktop_started",
    "api_ready",
    "webview_navigation_started",
    "webview_navigation_completed",
    "frontend_bootstrap_started",
    "frontend_bootstrap_completed",
    "frontend_rendered",
    "route_rendered",
    "frontend_ready",
    "frontend_js_bootstrap_failed",
    "frontend_ready_rejected",
    "frontend_timeout",
    "webview_navigation_failed",
    "desktop_exit",
})
_ROUTES = frozenset({"dashboard", "models", "diagnostics", "settings"})
_MAX_EVENTS = 64
_FRONTEND_SIGNAL_EVENTS = frozenset({
    "frontend_bootstrap_started",
    "frontend_bootstrap_completed",
    "frontend_rendered",
    "frontend_js_bootstrap_failed",
    "frontend_ready_rejected",
    "frontend_ready",
    "route_rendered",
})


def _read_json(path: Path, *, max_bytes: int) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        return {}
    raw = path.read_bytes()
    if len(raw) > max_bytes:
        return {}
    value = json.loads(raw.decode("utf-8"))
    return value if isinstance(value, dict) else {}


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _identity(root: Path) -> tuple[str, str]:
    try:
        pointer = _read_json(root / "current.json", max_bytes=16 * 1024)
        payload_id = str(pointer.get("version") or "")
        payload_root = root / "versions" / payload_id
        build = _read_json(payload_root / "build.json", max_bytes=32 * 1024)
        source_commit = str(build.get("source_commit") or "")
        if not _PAYLOAD_RE.fullmatch(payload_id) or not _SHA_RE.fullmatch(source_commit):
            return "", ""
        if payload_id != f"main-{source_commit[:12]}":
            return "", ""
        return source_commit, payload_id
    except (OSError, UnicodeError, ValueError, TypeError):
        return "", ""


def readiness_path(install_root: Path) -> Path:
    return Path(install_root).absolute() / "update-state" / READINESS_FILE


def record_event(
    install_root: Path,
    event: str,
    *,
    status: str | None = None,
    route: str | None = None,
    pid: int | None = None,
) -> dict[str, Any]:
    """Record one bounded lifecycle/readiness event without raw paths/errors."""

    if event not in _EVENTS:
        return {"status": "rejected", "code": "READINESS_EVENT_INVALID"}
    if route is not None and route not in _ROUTES:
        return {"status": "rejected", "code": "READINESS_ROUTE_INVALID"}
    root = Path(install_root).absolute()
    source_commit, payload_id = _identity(root)
    current_pid = int(pid if isinstance(pid, int) and pid > 0 else os.getpid())
    previous = _read_json(readiness_path(root), max_bytes=64 * 1024)
    previous_pid = previous.get("pid")
    events = previous.get("events") if isinstance(previous.get("events"), list) else []
    if event == "desktop_started" or previous_pid != current_pid or previous.get("payload_id") != payload_id:
        events = []
    item: dict[str, Any] = {
        "event": event,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if route is not None:
        item["route"] = route
    events = [entry for entry in events if isinstance(entry, dict)][-_MAX_EVENTS + 1 :]
    events.append(item)
    value = {
        "schema_version": READINESS_SCHEMA,
        "status": status or ("stopped" if event == "desktop_exit" else "running"),
        "pid": current_pid,
        "payload_id": payload_id,
        "source_commit": source_commit,
        "session_fingerprint": hashlib.sha256(f"{current_pid}:{payload_id}:{item['at']}".encode("ascii")).hexdigest()[:16],
        "events": events,
    }
    try:
        _atomic_write(readiness_path(root), value)
    except OSError:
        return {"status": "unavailable", "code": "READINESS_STATE_WRITE_FAILED"}
    return {"status": "recorded", "event": event, "payload_id": payload_id}


def load_state(install_root: Path) -> dict[str, Any]:
    return _read_json(readiness_path(Path(install_root).absolute()), max_bytes=64 * 1024)


def record_frontend_signal(
    install_root: Path,
    event: str,
    *,
    route: str | None = None,
    source_commit: str | None = None,
    payload_id: str | None = None,
) -> dict[str, Any]:
    """Record an identity-bound frontend signal received on loopback."""

    if event not in _FRONTEND_SIGNAL_EVENTS:
        return {"status": "rejected", "code": "READINESS_EVENT_INVALID"}
    root = Path(install_root).absolute()
    expected_commit, expected_payload = _identity(root)
    if not expected_commit or not expected_payload:
        return {"status": "rejected", "code": "READINESS_IDENTITY_UNAVAILABLE"}
    if source_commit is not None and source_commit != expected_commit:
        return {"status": "rejected", "code": "READINESS_SOURCE_MISMATCH"}
    if payload_id is not None and payload_id != expected_payload:
        return {"status": "rejected", "code": "READINESS_PAYLOAD_MISMATCH"}
    if event == "frontend_ready" and (source_commit != expected_commit or payload_id != expected_payload):
        return {"status": "rejected", "code": "READINESS_IDENTITY_REQUIRED"}
    state = load_state(root)
    pid = state.get("pid")
    if state.get("payload_id") != expected_payload or not isinstance(pid, int) or pid <= 0:
        return {"status": "rejected", "code": "READINESS_DESKTOP_SESSION_UNAVAILABLE"}
    if event == "frontend_ready" and not (
        event_seen(state, "frontend_rendered", route="dashboard")
        or event_seen(state, "route_rendered", route="dashboard")
    ):
        return {"status": "rejected", "code": "FRONTEND_RENDER_REQUIRED"}
    status = "failed" if "failed" in event or "rejected" in event else "running"
    return record_event(root, event, status=status, route=route, pid=pid)


def event_seen(state: Mapping[str, Any], event: str, *, route: str | None = None) -> bool:
    events = state.get("events")
    if not isinstance(events, list):
        return False
    for item in events:
        if not isinstance(item, dict) or item.get("event") != event:
            continue
        if route is None or item.get("route") == route:
            return True
    return False


def evaluate_readiness(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Return ready only when desktop, frontend and route evidence all agree.

    ``http_status`` is intentionally not inspected.  A response code alone can
    never make this function return ``ready``.
    """

    required = {
        "desktop_process_alive": evidence.get("desktop_process_alive") is True,
        "window_visible": evidence.get("window_visible") is True,
        "window_responding": evidence.get("window_responding") is True,
        "api_identity_match": evidence.get("api_identity_match") is True,
        "payload_identity_match": evidence.get("payload_identity_match") is True,
        "bootstrap_payload_valid": evidence.get("bootstrap_payload_valid") is True,
        "frontend_ready": evidence.get("frontend_ready") is True,
        "dashboard_rendered": evidence.get("dashboard_rendered") is True,
        "routes_complete": evidence.get("routes_complete") is True,
        "stable_window": evidence.get("stable_window") is True,
    }
    missing = [name for name, passed in required.items() if not passed]
    return {
        "status": "ready" if not missing else "not_ready",
        "user_app_recovered": not missing,
        "missing": missing,
        "http_status_ignored": True,
    }


__all__ = [
    "READINESS_FILE",
    "READINESS_SCHEMA",
    "evaluate_readiness",
    "event_seen",
    "load_state",
    "record_frontend_signal",
    "readiness_path",
    "record_event",
]
