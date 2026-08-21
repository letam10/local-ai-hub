"""
  FILE NOTE
  - Mục đích: In-memory, path-safe status snapshots cho Node Studio graph jobs; cung cấp draft_persist/draft_load để phục hồi sau crash
  - Liên kết trực tiếp: src/services/node_studio/engine.py, src/services/artifact_store.py, src/shared/paths/registry.py
  - Vùng ảnh hưởng khi sửa: Node Studio run state, crash recovery draft, artifact public output scrubbing
"""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
import threading
from copy import deepcopy
from collections.abc import Mapping
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.services.artifact_store import publicize
from src.shared.paths.registry import CONFIG_ROOT
from .schema import validate_graph




_LOCAL_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|^/|^\\\\)")
_URL_VALUE = re.compile(r"(?i)^(?:https?|file|ftp)://")
ACTIVE_RUN_STATUSES = frozenset({"queued", "starting", "running", "cancelling"})
MAX_TERMINAL_RUNS = 100

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public_artifacts(value: Any, *, path: str = "") -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        artifact_id = value.get("id")
        if isinstance(artifact_id, str) and artifact_id.startswith("artifact_"):
            found.append({
                "artifact_id": artifact_id,
                "name": value.get("name"),
                "media_type": value.get("media_type"),
                "url": value.get("url"),
                "output_path": path or "output",
            })
        for key, child in value.items():
            found.extend(_public_artifacts(child, path=f"{path}.{key}" if path else str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_public_artifacts(child, path=f"{path}[{index}]"))
    return found


def _safe_output(value: Any) -> Any:
    """Apply the public artifact conversion and scrub raw paths at the state boundary."""

    try:
        value = publicize(value)
    except Exception:  # pragma: no cover - defensive boundary for worker payloads
        pass
    if isinstance(value, dict):
        return {str(key): _safe_output(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_safe_output(child) for child in value]
    if isinstance(value, str) and _LOCAL_PATH.search(value):
        return "[đường-dẫn-cục-bộ]"
    return value


class GraphRunRegistry:
    """Expose per-node progress without persisting graph inputs or local paths."""

    def __init__(self) -> None:
        self._runs: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._sequence = 0

    def _prune_terminal_locked(self) -> None:
        """Keep all active runs and the newest bounded terminal snapshots."""

        terminal = sorted(
            (item for item in self._runs.values() if str(item.get("status")) not in ACTIVE_RUN_STATUSES),
            key=lambda item: (str(item.get("finished_at") or item.get("created_at") or ""), int(item.get("_sequence") or 0)),
            reverse=True,
        )
        for item in terminal[MAX_TERMINAL_RUNS:]:
            job_id = item.get("job_id")
            if isinstance(job_id, str):
                self._runs.pop(job_id, None)

    def begin(self, job_id: str, graph: dict[str, Any]) -> None:
        nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
        with self._lock:
            current = self._runs.get(job_id)
            if current:
                return
            self._sequence += 1
            self._runs[job_id] = {
                "contract_version": "node-run.v2",
                "status": "queued",
                "job_id": job_id,
                "graph_id": str(graph.get("id") or "untitled"),
                "title": str(graph.get("title") or "Untitled workflow"),
                "created_at": _now(),
                "finished_at": None,
                "next_action": None,
                "_sequence": self._sequence,
                "provenance": [],
                "nodes": {
                    str(node.get("id")): {
                        "id": str(node.get("id")),
                        "type": str(node.get("type") or "unknown"),
                        "status": "queued",
                        "progress": 0,
                        "message": "Đang chờ Run Graph.",
                    }
                    for node in nodes
                    if isinstance(node, dict) and node.get("id")
                },
            }
            self._prune_terminal_locked()

    def update_node(
        self,
        job_id: str,
        node_id: str,
        *,
        status: str,
        progress: int,
        message: str = "",
        output: Any = None,
        error: str | None = None,
        next_action: str | None = None,
    ) -> None:
        with self._lock:
            run = self._runs.get(job_id)
            if not run:
                return
            node = run["nodes"].get(node_id)
            if not isinstance(node, dict):
                return
            node.update({"status": status, "progress": max(0, min(100, int(progress))), "message": message})
            if output is not None:
                output = _safe_output(output)
                node["output"] = output
            if error:
                node["error"] = error
            if next_action:
                node["next_action"] = next_action
                run["next_action"] = next_action
            if output is not None:
                existing = {item.get("artifact_id") for item in run["provenance"] if isinstance(item, dict)}
                for artifact in _public_artifacts(output):
                    artifact_id = artifact.get("artifact_id")
                    if artifact_id in existing:
                        continue
                    run["provenance"].append({
                        **artifact,
                        "node_id": node_id,
                        "node_type": node.get("type"),
                    })
                    existing.add(artifact_id)
            if status == "running":
                run["status"] = "running"

    def finish(self, job_id: str, *, status: str, error: str | None = None, next_action: str | None = None) -> None:
        with self._lock:
            run = self._runs.get(job_id)
            if not run:
                return
            run["status"] = status
            run["finished_at"] = _now()
            if error:
                run["error"] = error
            if next_action:
                run["next_action"] = next_action
            self._prune_terminal_locked()

    def snapshot(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            run = self._runs.get(job_id)
            if not run:
                return None
            # All callers supply already-public artifact metadata.  Copying still
            # prevents a handler from mutating the live worker state.
            return {
                **{key: value for key, value in run.items() if key not in {"nodes", "_sequence"}},
                "provenance": [dict(item) for item in run.get("provenance", [])],
                "nodes": [dict(item) for item in run["nodes"].values()],
            }


graph_runs = GraphRunRegistry()


# ---------------------------------------------------------------------------
# Graph draft persistence (crash recovery)
# ---------------------------------------------------------------------------

_DRAFT_SCHEMA_VERSION = 1
_DRAFT_OWNER = "node_studio"
_SAFE_SCOPE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_DRAFT_ID = re.compile(r"^draft_[a-z][a-z0-9_-]{0,31}\.json$")
_DRAFT_MAX_BYTES = 1_048_576
_GRAPH_MAX_BYTES = 768 * 1024
_GRAPH_MAX_DEPTH = 24
_GRAPH_MAX_ITEMS = 4096
_GRAPH_MAX_STRING = 16_384
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_DRAFT_FIELDS = frozenset({"draft_schema_version", "owner", "draft_id", "scope", "graph"})
_EDITABLE_GRAPH_ERRORS = frozenset({
    "unknown_node_type",
    "node_id",
    "node_type",
    "node_data",
    "node_position",
    "edge_type",
    "edge_id",
    "edge_endpoints",
    "edge_endpoint_type",
    "edge_unknown_node",
    "unknown_output_port",
    "unknown_input_port",
    "socket_type_mismatch",
    "input_already_connected",
    "cycle_detected",
})
_FIXED_REFUSAL_CODES = frozenset({
    "scope_invalid",
    "graph_invalid",
    "draft_not_found",
    "draft_storage_unavailable",
    "draft_storage_conflict",
    "draft_manual_review",
    "draft_read_unavailable",
    "draft_write_failed",
})


class _DraftStorageError(Exception):
    """Private finite refusal used by the storage boundary."""

    def __init__(self, code: str) -> None:
        self.code = code if code in _FIXED_REFUSAL_CODES else "draft_storage_unavailable"
        super().__init__(self.code)


@dataclass(frozen=True)
class _StorageSignature:
    device: int
    inode: int
    mode: int
    size: int
    mtime_ns: int
    ctime_ns: int
    attributes: int


@dataclass(frozen=True)
class _DraftLocation:
    root: Path
    path: Path
    chain: tuple[tuple[Path, _StorageSignature], ...]
    target: _StorageSignature | None


def _lexical_path(value: Path | str) -> Path:
    """Return an absolute lexical path without following links or reparses."""

    return Path(os.path.abspath(os.path.normpath(os.fspath(value))))


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.normpath(os.fspath(left))) == os.path.normcase(os.path.normpath(os.fspath(right)))


def _lexically_contained(root: Path, candidate: Path) -> bool:
    try:
        root_name = os.path.normcase(os.path.normpath(os.fspath(root)))
        candidate_name = os.path.normcase(os.path.normpath(os.fspath(candidate)))
        return os.path.commonpath((root_name, candidate_name)) == root_name
    except (OSError, ValueError):
        return False


def _is_reparse(stat_result: os.stat_result) -> bool:
    return stat.S_ISLNK(stat_result.st_mode) or bool(int(getattr(stat_result, "st_file_attributes", 0) or 0) & _REPARSE_POINT)


def _signature_from_stat(stat_result: os.stat_result) -> _StorageSignature:
    return _StorageSignature(
        device=int(getattr(stat_result, "st_dev", 0)),
        inode=int(getattr(stat_result, "st_ino", 0)),
        mode=int(stat_result.st_mode),
        size=int(getattr(stat_result, "st_size", 0)),
        mtime_ns=int(getattr(stat_result, "st_mtime_ns", 0)),
        ctime_ns=int(getattr(stat_result, "st_ctime_ns", 0)),
        attributes=int(getattr(stat_result, "st_file_attributes", 0) or 0),
    )


def _signature(path: Path, *, kind: str, allow_missing: bool = False) -> _StorageSignature | None:
    try:
        stat_result = os.lstat(path)
    except FileNotFoundError:
        if allow_missing:
            return None
        raise _DraftStorageError("draft_storage_unavailable")
    except OSError as exc:
        raise _DraftStorageError("draft_storage_unavailable") from exc
    if _is_reparse(stat_result):
        raise _DraftStorageError("draft_manual_review")
    if kind == "directory" and not stat.S_ISDIR(stat_result.st_mode):
        raise _DraftStorageError("draft_storage_unavailable")
    if kind == "file" and not stat.S_ISREG(stat_result.st_mode):
        raise _DraftStorageError("draft_manual_review")
    return _signature_from_stat(stat_result)


def _same_directory_identity(left: _StorageSignature, right: _StorageSignature) -> bool:
    return (left.device, left.inode, left.mode, left.attributes) == (right.device, right.inode, right.mode, right.attributes)


def _same_file_signature(left: _StorageSignature, right: _StorageSignature) -> bool:
    return left == right


def _same_delete_identity(left: _StorageSignature, right: _StorageSignature) -> bool:
    """Compare stable handle identity/metadata; Windows may update ctime on open."""

    return (
        left.device,
        left.inode,
        left.mode,
        left.size,
        left.mtime_ns,
        left.attributes,
    ) == (
        right.device,
        right.inode,
        right.mode,
        right.size,
        right.mtime_ns,
        right.attributes,
    )


def _ancestor_signatures(root: Path) -> tuple[tuple[Path, _StorageSignature], ...]:
    """Validate root and every existing ancestor without following reparses."""

    values: list[tuple[Path, _StorageSignature]] = []
    current = root
    while True:
        signature = _signature(current, kind="directory")
        assert signature is not None
        values.append((current, signature))
        parent = current.parent
        if _same_path(parent, current):
            break
        current = parent
    return tuple(values)


def _directory_chain(root: Path, parent: Path) -> tuple[tuple[Path, _StorageSignature], ...]:
    if not _lexically_contained(root, parent):
        raise _DraftStorageError("draft_manual_review")
    chain = list(_ancestor_signatures(root))
    relative = Path(os.path.relpath(parent, root))
    if str(relative) == ".":
        return tuple(chain)
    current = root
    for part in relative.parts:
        if part in {"", ".", ".."}:
            raise _DraftStorageError("draft_manual_review")
        current = current / part
        signature = _signature(current, kind="directory")
        assert signature is not None
        chain.append((current, signature))
    return tuple(chain)


def _capture_location(path: Path, *, root: Path, kind: str, allow_missing: bool = False) -> tuple[tuple[Path, _StorageSignature], _StorageSignature | None]:
    lexical_root = _lexical_path(root)
    lexical_path = _lexical_path(path)
    if not _lexically_contained(lexical_root, lexical_path):
        raise _DraftStorageError("draft_manual_review")
    parent = lexical_path if kind == "directory" else lexical_path.parent
    chain = _directory_chain(lexical_root, parent)
    target = _signature(lexical_path, kind=kind, allow_missing=allow_missing)
    return chain, target


def _location_current(location: _DraftLocation, *, target: _StorageSignature | None = None) -> bool:
    try:
        for path, expected in location.chain:
            current = _signature(path, kind="directory")
            if current is None or not _same_directory_identity(current, expected):
                return False
        current_target = _signature(location.path, kind="file", allow_missing=True)
        expected_target = location.target if target is None else target
        if expected_target is None:
            return current_target is None
        return current_target is not None and _same_file_signature(current_target, expected_target)
    except _DraftStorageError:
        return False


def _valid_scope(scope: object) -> bool:
    if not isinstance(scope, str) or not _SAFE_SCOPE.fullmatch(scope):
        return False
    if len(scope) > 32 or scope.casefold() != scope:
        return False
    if any(marker in scope for marker in ("..", "/", "\\", ":", "%", "\x00")):
        return False
    return True


def _draft_id(scope: str) -> str:
    return f"draft_{scope}.json"


def _draft_path(scope: str) -> Path:
    if not _valid_scope(scope):
        raise _DraftStorageError("scope_invalid")
    root = _lexical_path(CONFIG_ROOT)
    path = _lexical_path(root / f"node_studio_draft_{scope}.json")
    if not _lexically_contained(root, path) or path.parent != root:
        raise _DraftStorageError("draft_manual_review")
    return path


def _location_for_scope(scope: str) -> _DraftLocation:
    path = _draft_path(scope)
    root = _lexical_path(CONFIG_ROOT)
    chain, target = _capture_location(path, root=root, kind="file", allow_missing=True)
    return _DraftLocation(root=root, path=path, chain=chain, target=target)


def _read_bounded(path: Path) -> bytes:
    try:
        with path.open("rb") as handle:
            data = handle.read(_DRAFT_MAX_BYTES + 1)
    except OSError as exc:
        raise _DraftStorageError("draft_read_unavailable") from exc
    if len(data) > _DRAFT_MAX_BYTES:
        raise _DraftStorageError("draft_storage_unavailable")
    return data


def _read_stable(location: _DraftLocation) -> bytes | None:
    if location.target is None:
        return None
    if not _location_current(location):
        raise _DraftStorageError("draft_storage_conflict")
    first = _read_bounded(location.path)
    if not _location_current(location):
        raise _DraftStorageError("draft_storage_conflict")
    second = _read_bounded(location.path)
    if first != second or not _location_current(location):
        raise _DraftStorageError("draft_storage_conflict")
    return first


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DraftStorageError("draft_storage_unavailable")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise _DraftStorageError("draft_storage_unavailable")


def _strict_json(raw: bytes) -> Any:
    if not isinstance(raw, bytes) or len(raw) > _DRAFT_MAX_BYTES:
        raise _DraftStorageError("draft_storage_unavailable")
    try:
        text = raw.decode("utf-8", errors="strict")
        return json.loads(text, object_pairs_hook=_strict_pairs, parse_constant=_reject_constant)
    except _DraftStorageError:
        raise
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError, RecursionError) as exc:
        raise _DraftStorageError("draft_storage_unavailable") from exc


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    try:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise _DraftStorageError("graph_invalid") from exc


def _unsafe_key(key: object) -> bool:
    if not isinstance(key, str):
        return True
    lowered = key.casefold()
    return any(marker in lowered for marker in ("path", "url", "uri", "secret", "token", "password", "authorization", "command", "executable", "stderr", "stdout"))


def _unsafe_graph_value(value: object, *, depth: int = 0, count: list[int] | None = None) -> bool:
    if count is None:
        count = [0]
    count[0] += 1
    if depth > _GRAPH_MAX_DEPTH or count[0] > _GRAPH_MAX_ITEMS:
        return True
    if isinstance(value, str):
        if len(value) > _GRAPH_MAX_STRING or _LOCAL_PATH.search(value) or _URL_VALUE.match(value):
            return True
        lowered = value.casefold()
        return any(marker in lowered for marker in ("bearer ", "api_key", "private key", "powershell", "cmd.exe", "/bin/sh"))
    if value is None or isinstance(value, (bool, int, float)):
        return isinstance(value, float) and (value != value or value in {float("inf"), float("-inf")})
    if isinstance(value, Mapping):
        if len(value) > _GRAPH_MAX_ITEMS:
            return True
        return any(_unsafe_key(key) or _unsafe_graph_value(child, depth=depth + 1, count=count) for key, child in value.items())
    if isinstance(value, (list, tuple)):
        if len(value) > _GRAPH_MAX_ITEMS:
            return True
        return any(_unsafe_graph_value(child, depth=depth + 1, count=count) for child in value)
    return True


def _validated_graph(graph: object) -> dict[str, Any]:
    if not isinstance(graph, dict) or _unsafe_graph_value(graph):
        raise _DraftStorageError("graph_invalid")
    try:
        encoded = json.dumps(graph, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(encoded) > _GRAPH_MAX_BYTES:
            raise _DraftStorageError("graph_invalid")
        safe_graph = json.loads(encoded.decode("utf-8"), object_pairs_hook=_strict_pairs, parse_constant=_reject_constant)
    except _DraftStorageError:
        raise
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise _DraftStorageError("graph_invalid") from exc
    try:
        validation = validate_graph(deepcopy(safe_graph), require_runnable=False)
    except Exception as exc:  # schema errors are not a public storage detail
        raise _DraftStorageError("graph_invalid") from exc
    if not isinstance(validation, Mapping):
        raise _DraftStorageError("graph_invalid")
    if validation.get("valid") is not True:
        # Drafts are an editable recovery surface. Preserve incomplete editor
        # records and forward-compatible node types, while closed-property,
        # identity, schema-version and unsafe-value errors still refuse.
        errors = validation.get("errors")
        if not isinstance(errors, list) or not errors or any(
            not isinstance(item, Mapping) or item.get("code") not in _EDITABLE_GRAPH_ERRORS
            for item in errors
        ):
            raise _DraftStorageError("graph_invalid")
    return safe_graph


def _validated_envelope(value: object, *, raw: bytes | None = None, scope: str | None = None) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _DRAFT_FIELDS:
        raise _DraftStorageError("draft_manual_review")
    value_scope = value.get("scope")
    if not _valid_scope(value_scope) or (scope is not None and value_scope != scope):
        raise _DraftStorageError("draft_manual_review")
    expected_id = _draft_id(value_scope)
    if (
        value.get("draft_schema_version") != _DRAFT_SCHEMA_VERSION
        or value.get("owner") != _DRAFT_OWNER
        or not isinstance(value.get("draft_id"), str)
        or not _DRAFT_ID.fullmatch(value["draft_id"])
        or value.get("draft_id") != expected_id
    ):
        raise _DraftStorageError("draft_manual_review")
    graph = _validated_graph(value.get("graph"))
    normalized = {
        "draft_schema_version": _DRAFT_SCHEMA_VERSION,
        "owner": _DRAFT_OWNER,
        "draft_id": expected_id,
        "scope": value_scope,
        "graph": graph,
    }
    if raw is not None and raw != _canonical_json(normalized):
        raise _DraftStorageError("draft_storage_unavailable")
    return normalized


def _read_existing(location: _DraftLocation, scope: str) -> bytes | None:
    raw = _read_stable(location)
    if raw is None:
        return None
    parsed = _strict_json(raw)
    _validated_envelope(parsed, raw=raw, scope=scope)
    return raw


def _temp_location(root: Path, path: Path) -> _DraftLocation:
    chain, target = _capture_location(path, root=root, kind="file", allow_missing=False)
    if target is None:
        raise _DraftStorageError("draft_storage_unavailable")
    return _DraftLocation(root=root, path=path, chain=chain, target=target)


def _cleanup_temp(location: _DraftLocation | None) -> bool:
    if location is None:
        return True
    if not _location_current(location):
        return False
    try:
        location.path.unlink()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def _cleanup_temp_path(root: Path, path: Path | None) -> bool:
    """Remove a task temp only after recapturing its no-follow identity."""

    if path is None:
        return True
    try:
        chain, target = _capture_location(path, root=root, kind="file", allow_missing=True)
    except _DraftStorageError:
        return False
    if target is None:
        return True
    return _cleanup_temp(_DraftLocation(root=root, path=path, chain=chain, target=target))


def _write_temp(root: Path, data: bytes) -> _DraftLocation:
    fd: int | None = None
    temp_path: Path | None = None
    try:
        fd, raw_path = tempfile.mkstemp(prefix=".node-draft-", suffix=".tmp", dir=os.fspath(root))
        temp_path = _lexical_path(raw_path)
        chain = _directory_chain(root, temp_path.parent)
        with os.fdopen(fd, "wb") as handle:
            fd = None
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        final_chain, final_target = _capture_location(temp_path, root=root, kind="file", allow_missing=False)
        if final_target is None:
            raise _DraftStorageError("draft_storage_unavailable")
        location = _DraftLocation(root=root, path=temp_path, chain=final_chain or chain, target=final_target)
        if not _location_current(location):
            raise _DraftStorageError("draft_storage_conflict")
        return location
    except _DraftStorageError:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        _cleanup_temp_path(root, temp_path)
        raise
    except (OSError, ValueError, TypeError) as exc:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        _cleanup_temp_path(root, temp_path)
        raise _DraftStorageError("draft_write_failed") from exc


def _rollback_published(
    location: _DraftLocation,
    old_raw: bytes | None,
    new_raw: bytes,
    expected_target: _StorageSignature | None,
) -> bool:
    """Restore a just-published draft only when the new leaf is still ours."""

    try:
        current_chain, current_target = _capture_location(location.path, root=location.root, kind="file", allow_missing=False)
        if current_target is None:
            return old_raw is None
        current = _DraftLocation(location.root, location.path, current_chain, current_target)
        if expected_target is not None and not _same_file_signature(current_target, expected_target):
            return False
        if not _location_current(current) or _read_stable(current) != new_raw:
            return False
        if old_raw is None:
            current.path.unlink()
            return _signature(current.path, kind="file", allow_missing=True) is None
        rollback_temp = _write_temp(location.root, old_raw)
        try:
            if not _location_current(current) or _read_stable(current) != new_raw or not _location_current(rollback_temp):
                return False
            os.replace(rollback_temp.path, current.path)
            rollback_temp = None
            restored_chain, restored_target = _capture_location(current.path, root=location.root, kind="file", allow_missing=False)
            if restored_target is None:
                return False
            restored = _DraftLocation(location.root, current.path, restored_chain, restored_target)
            return _location_current(restored) and _read_stable(restored) == old_raw
        finally:
            if rollback_temp is not None:
                _cleanup_temp(rollback_temp)
    except (_DraftStorageError, OSError, ValueError, TypeError):
        return False


def _replace_safely(location: _DraftLocation, old_raw: bytes | None, new_raw: bytes) -> None:
    if not _location_current(location):
        raise _DraftStorageError("draft_storage_conflict")
    current_raw = _read_stable(location)
    if current_raw != old_raw:
        raise _DraftStorageError("draft_storage_conflict")
    temp = _write_temp(location.root, new_raw)
    expected_target = temp.target
    published = False
    try:
        if not _location_current(location) or _read_stable(location) != old_raw or not _location_current(temp):
            raise _DraftStorageError("draft_storage_conflict")
        os.replace(temp.path, location.path)
        published = True
        temp = None
        try:
            committed = _capture_location(location.path, root=location.root, kind="file", allow_missing=False)
            committed_location = _DraftLocation(location.root, location.path, committed[0], committed[1])
            post_commit_ok = (
                expected_target is not None
                and committed[1] is not None
                and _same_file_signature(committed[1], expected_target)
                and _location_current(committed_location)
                and _read_stable(committed_location) == new_raw
            )
        except (_DraftStorageError, OSError, ValueError, TypeError):
            post_commit_ok = False
        if not post_commit_ok:
            restored = _rollback_published(location, old_raw, new_raw, expected_target)
            if restored:
                published = False
            if not restored:
                raise _DraftStorageError("draft_manual_review")
            raise _DraftStorageError("draft_storage_conflict")
    except _DraftStorageError:
        if temp is not None:
            if not _cleanup_temp(temp):
                raise _DraftStorageError("draft_manual_review")
        elif published:
            # The post-replace guard may fail before a second location can be
            # captured.  Roll back only if the new bytes are still identity-safe.
            if not _rollback_published(location, old_raw, new_raw, expected_target):
                raise _DraftStorageError("draft_manual_review")
        raise
    except (OSError, ValueError, TypeError) as exc:
        if temp is not None and not _cleanup_temp(temp):
            raise _DraftStorageError("draft_manual_review") from exc
        if published and not _rollback_published(location, old_raw, new_raw, expected_target):
            raise _DraftStorageError("draft_manual_review") from exc
        raise _DraftStorageError("draft_write_failed") from exc


def _delete_identity_attested(location: _DraftLocation, expected_raw: bytes) -> bool:
    """Delete the already-attested file without reopening a pathname race.

    Windows is the production platform.  The handle is opened with
    ``FILE_FLAG_OPEN_REPARSE_POINT`` and without ``FILE_SHARE_DELETE``; a
    replacement cannot win after the handle is acquired.  Deletion is then
    marked on that same handle, rather than issuing a second pathname-based
    unlink.  Non-Windows callers refuse because the portable fallback would
    reintroduce the race this boundary is designed to prevent.
    """

    if os.name != "nt":
        return False
    fd: int | None = None
    try:
        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        create_file.restype = wintypes.HANDLE
        handle = create_file(
            os.fspath(location.path),
            0x80000000 | 0x00010000,  # GENERIC_READ | DELETE
            0x00000001 | 0x00000002,  # FILE_SHARE_READ | FILE_SHARE_WRITE; deny delete
            None,
            3,  # OPEN_EXISTING
            0x00200000,  # FILE_FLAG_OPEN_REPARSE_POINT
            None,
        )
        invalid_handle = ctypes.c_void_p(-1).value
        handle_value = int(handle)
        if handle_value == invalid_handle:
            return False
        try:
            fd = msvcrt.open_osfhandle(handle_value, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        except (OSError, ValueError):
            kernel32.CloseHandle(handle)
            return False

        opened = os.fstat(fd)
        opened_signature = _signature_from_stat(opened)
        if _is_reparse(opened) or location.target is None or not _same_delete_identity(opened_signature, location.target):
            return False
        os.lseek(fd, 0, os.SEEK_SET)
        observed = os.read(fd, _DRAFT_MAX_BYTES + 1)
        if observed != expected_raw or len(observed) > _DRAFT_MAX_BYTES:
            return False
        if not _location_current(location):
            return False

        class _FileDispositionInfo(ctypes.Structure):
            _fields_ = [("DeleteFile", wintypes.BOOLEAN)]

        disposition = _FileDispositionInfo(1)
        set_file_information = kernel32.SetFileInformationByHandle
        set_file_information.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD]
        set_file_information.restype = wintypes.BOOL
        # FileDispositionInfo = 4.  This marks this exact opened file for
        # deletion and cannot be redirected to a later pathname replacement.
        return bool(set_file_information(handle, 4, ctypes.byref(disposition), ctypes.sizeof(disposition)))
    except (AttributeError, ImportError, OSError, TypeError, ValueError):
        return False
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def draft_persist(scope: str, graph: dict[str, Any]) -> dict[str, Any]:
    """Persist a validated editable graph inside the fixed Config boundary."""

    if not _valid_scope(scope):
        return {"accepted": False, "status": "error", "reason": "scope_invalid", "execution": "not_run", "dry_run": True}
    try:
        safe_graph = _validated_graph(graph)
        location = _location_for_scope(scope)
        old_raw = _read_existing(location, scope)
        envelope = {
            "draft_schema_version": _DRAFT_SCHEMA_VERSION,
            "owner": _DRAFT_OWNER,
            "draft_id": _draft_id(scope),
            "scope": scope,
            "graph": safe_graph,
        }
        new_raw = _canonical_json(envelope)
        _replace_safely(location, old_raw, new_raw)
        return {"accepted": True, "status": "completed", "draft_id": _draft_id(scope)}
    except _DraftStorageError as exc:
        status = "manual_review" if exc.code in {"draft_manual_review", "draft_storage_conflict"} else "error"
        return {"accepted": False, "status": status, "reason": exc.code, "execution": "not_run", "dry_run": True}
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
        return {"accepted": False, "status": "error", "reason": "draft_storage_unavailable", "execution": "not_run", "dry_run": True}


def draft_load(scope: str) -> dict[str, Any] | None:
    """Return only a validated opaque draft projection; never expose storage paths."""

    if not _valid_scope(scope):
        return None
    try:
        location = _location_for_scope(scope)
        raw = _read_stable(location)
        if raw is None:
            return None
        value = _strict_json(raw)
        envelope = _validated_envelope(value, raw=raw, scope=scope)
        return {"draft_id": envelope["draft_id"], "scope": envelope["scope"], "graph": envelope["graph"]}
    except (_DraftStorageError, OSError, ValueError, TypeError, UnicodeError, RecursionError):
        return None


def draft_clear(scope: str) -> dict[str, Any]:
    """Delete only an identity-attested Hub-owned draft leaf."""

    draft_id = _draft_id(scope) if _valid_scope(scope) else None
    if not _valid_scope(scope):
        return {"accepted": False, "status": "manual_review", "reason": "scope_invalid", "execution": "not_run", "dry_run": True}
    try:
        location = _location_for_scope(scope)
        raw = _read_stable(location)
        if raw is None:
            return {"accepted": True, "status": "not_found", "draft_id": draft_id}
        value = _strict_json(raw)
        _validated_envelope(value, raw=raw, scope=scope)
        if not _location_current(location) or _read_stable(location) != raw:
            raise _DraftStorageError("draft_storage_conflict")
        if not _delete_identity_attested(location, raw):
            raise _DraftStorageError("draft_storage_conflict")
        if _signature(location.path, kind="file", allow_missing=True) is not None:
            raise _DraftStorageError("draft_storage_conflict")
        return {"accepted": True, "status": "completed", "draft_id": draft_id, "deleted": True}
    except _DraftStorageError as exc:
        return {"accepted": False, "status": "manual_review", "reason": exc.code, "draft_id": draft_id, "execution": "not_run", "dry_run": True}
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
        return {"accepted": False, "status": "manual_review", "reason": "draft_storage_unavailable", "draft_id": draft_id, "execution": "not_run", "dry_run": True}


def public_draft(value: object) -> dict[str, Any] | None:
    """Whitelist a draft projection before it crosses the route boundary."""

    if not isinstance(value, Mapping):
        return None
    scope = value.get("scope")
    draft_id = value.get("draft_id")
    if not _valid_scope(scope) or draft_id != _draft_id(scope) or set(value) - {"draft_id", "scope", "graph"}:
        return None
    try:
        graph = _validated_graph(value.get("graph"))
    except _DraftStorageError:
        return None
    return {"draft_id": draft_id, "scope": scope, "graph": graph}
