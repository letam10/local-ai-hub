"""Path-free component runtime evidence with fingerprint binding."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import time
from typing import Any

from src.platform.paths import HubPaths


EVIDENCE_SCHEMA = "component-runtime-evidence.v1"


def _is_reparse(path: Path) -> bool:
    try:
        if stat.S_ISLNK(path.lstat().st_mode):
            return True
    except FileNotFoundError:
        return False
    except OSError:
        return True
    if os.name == "nt":
        try:
            import ctypes
            attrs = int(ctypes.windll.kernel32.GetFileAttributesW(str(path))) & 0xFFFFFFFF
            return attrs != 0xFFFFFFFF and bool(attrs & 0x400)
        except (AttributeError, OSError):
            return True
    return False


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        handle, name = tempfile.mkstemp(prefix=".component-evidence-", suffix=".tmp", dir=path.parent)
        os.close(handle)
        temporary = Path(name)
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=True, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def runtime_fingerprint(paths: HubPaths, record: Mapping[str, Any]) -> str:
    """Fingerprint fixed runtime leaves without hashing large binaries."""

    root = paths.runtime_root
    leaves: list[dict[str, Any]] = []
    for relative in record.get("required_leaves", []):
        if not isinstance(relative, str):
            continue
        target = root / Path(relative)
        try:
            if _is_reparse(target) or not target.is_file():
                leaves.append({"relative_leaf": relative, "present": False})
            else:
                info = target.stat()
                leaves.append({"relative_leaf": relative, "present": True, "size_bytes": int(info.st_size), "mtime_ns": int(info.st_mtime_ns)})
        except OSError:
            leaves.append({"relative_leaf": relative, "present": False})
    payload = {"runtime_id": record.get("runtime_id"), "revision": record.get("revision"), "leaves": leaves}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _path(paths: HubPaths) -> Path:
    return paths.config_root / "component_runtime_evidence.v1.json"


def read_runtime_evidence(paths: HubPaths) -> dict[str, Any]:
    try:
        value = json.loads(_path(paths).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        value = {}
    records = value.get("records") if isinstance(value, Mapping) else None
    return {"schema_version": EVIDENCE_SCHEMA, "records": dict(records) if isinstance(records, Mapping) else {}}


def record_runtime_smoke(paths: HubPaths, component_id: str, record: Mapping[str, Any], *, outcome: str, smoke_id: str, details: Mapping[str, Any] | None = None, timestamp: int | None = None) -> dict[str, Any]:
    if outcome not in {"completed", "failed", "unavailable", "not_run"} or not isinstance(smoke_id, str) or not smoke_id or len(smoke_id) > 96:
        return {"status": "invalid", "code": "evidence_invalid", "execution": "not_run"}
    value = read_runtime_evidence(paths)
    safe_details = {key: value for key, value in (details or {}).items() if key in {"version", "exit_code", "artifact_kind", "artifact_verified"}}
    evidence = {
        "component_id": component_id,
        "status": outcome,
        "execution": "completed" if outcome == "completed" else "not_run",
        "runtime_fingerprint": runtime_fingerprint(paths, record),
        "smoke_id": smoke_id,
        "smoked_at": int(time.time()) if timestamp is None else int(timestamp),
        "details": safe_details,
    }
    value["records"][component_id] = evidence
    _atomic_write(_path(paths), value)
    return {"status": "saved", "component_id": component_id, "outcome": outcome, "runtime_fingerprint": evidence["runtime_fingerprint"]}


def runtime_evidence_passed(paths: HubPaths, component_id: str, record: Mapping[str, Any], *, now: int | None = None, max_age_seconds: int = 24 * 60 * 60) -> bool:
    evidence = read_runtime_evidence(paths)["records"].get(component_id)
    if not isinstance(evidence, Mapping) or evidence.get("status") != "completed" or evidence.get("execution") != "completed":
        return False
    if evidence.get("runtime_fingerprint") != runtime_fingerprint(paths, record):
        return False
    smoked_at = evidence.get("smoked_at")
    current = int(time.time()) if now is None else int(now)
    return isinstance(smoked_at, int) and 0 <= current - smoked_at <= max_age_seconds


__all__ = ["EVIDENCE_SCHEMA", "read_runtime_evidence", "record_runtime_smoke", "runtime_evidence_passed", "runtime_fingerprint"]
