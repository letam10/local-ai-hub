"""Read-only storage and model overview for the unified UI."""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from src.services.api.config import load_json
from src.platform.paths import get_paths
from src.shared.paths.registry import (
    CACHE_ROOT,
    LOG_ROOT,
    MODEL_ROOT,
    OUTPUT_ROOT,
    ROOT,
    RUNTIME_ROOT,
    TEMP_ROOT,
)


_CACHE_SECONDS = 120.0
_LOW_SPACE_BYTES = 20 * 1024**3
_DIRECTORY_SCAN_MAX_ENTRIES = 12_000
_DIRECTORY_SCAN_MAX_DEPTH = 24
_DEEP_SCAN_YIELD_ENTRIES = 512
_DEEP_SCAN_YIELD_SECONDS = 0.25
_DEEP_SCAN_PROGRESS_WINDOW = 1_000
_OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"
_OLLAMA_TIMEOUT_SECONDS = 0.5
_VOLUME_ALLOWLIST = (
    ("c", "C:", Path("C:/")),
    ("d", "D:", Path("D:/")),
)
_cache_lock = threading.Lock()
_size_cache: tuple[float, dict[str, Any]] | None = None
_volume_snapshot_cache: tuple[float, dict[str, Any]] | None = None
_model_cache: tuple[float, list[dict[str, Any]]] | None = None
_scan_lock = threading.RLock()
_scan_thread: threading.Thread | None = None
_scan_cancel_events: dict[str, threading.Event] = {}
_scan_cache_loaded = False
_SCAN_CACHE_SCHEMA = "storage-scan-cache.v1"
_scan_state: dict[str, Any] = {
    "schema_version": "storage-scan.v1",
    "scan_id": None,
    "status": "idle",
    "execution": "not_run",
    "progress": 0,
    "current_area": None,
    "areas": {},
    "exact": False,
    "entries_scanned": 0,
    "started_at": None,
    "completed_at": None,
    "reason": "Chưa có lần quét storage nào được yêu cầu.",
    "next_action": "Bấm Quét lại để bắt đầu quét nền có giới hạn.",
    "mode": "fast",
    "total_bytes_counted": 0,
    "files_scanned": 0,
    "cancel_requested": False,
    "saved_at": None,
    "managed_root_counts": {},
    "owned_storage_total_bytes": 0,
    "owned_storage_exact": False,
    "deduplicated_targets": 0,
}
_SCAN_AREA_NAMES = ("Models", "Environments", "Runtime", "Cache", "Output", "Temp", "Logs")


def _scan_cache_path(data_root: Path | None = None) -> Path:
    """Return the machine-local exact-scan cache location.

    The cache contains only the path-free storage projection and is kept under
    the configured DATA_ROOT.  It is never committed to source control.
    """

    root = data_root if data_root is not None else _managed_roots()[0]
    return Path(root) / "Config" / "storage_scan_cache.json"


def _persist_exact_scan(result: dict[str, Any], data_root: Path) -> None:
    scan = result.get("scan", {}) if isinstance(result.get("scan"), dict) else {}
    if result.get("status") != "completed" or scan.get("mode") != "deep_exact" or scan.get("exact") is not True:
        return
    target = _scan_cache_path(data_root)
    config_root = target.parent
    try:
        # Do not write through a reparse-pointed local-state directory.
        if config_root.exists() and (config_root.is_symlink() or getattr(config_root.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400):
            return
        config_root.mkdir(parents=True, exist_ok=True)
        saved_at = str(result.get("scan", {}).get("saved_at") or datetime.now(timezone.utc).isoformat(timespec="seconds"))
        payload = {"schema_version": _SCAN_CACHE_SCHEMA, "saved_at": saved_at, "summary": result}
        temporary = config_root / ".storage_scan_cache.tmp"
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except (OSError, TypeError, ValueError):
        try:
            temporary.unlink(missing_ok=True)
        except (UnboundLocalError, OSError):
            pass


def _restore_exact_scan_cache() -> None:
    """Restore a previously completed exact result without scanning again."""

    global _scan_cache_loaded, _scan_state, _size_cache
    if _scan_cache_loaded or _scan_state.get("status") != "idle":
        return
    _scan_cache_loaded = True
    try:
        data_root = _managed_roots()[0]
        payload = json.loads(_scan_cache_path(data_root).read_text(encoding="utf-8"))
        result = payload.get("summary") if isinstance(payload, dict) else None
        scan = result.get("scan") if isinstance(result, dict) else None
        if not isinstance(result, dict) or not isinstance(scan, dict) or scan.get("mode") != "deep_exact" or scan.get("exact") is not True or result.get("status") != "completed":
            return
        areas = result.get("areas") if isinstance(result.get("areas"), dict) else {}
        saved_at = payload.get("saved_at") if isinstance(payload, dict) else None
        if not isinstance(saved_at, str) or not saved_at:
            saved_at = scan.get("saved_at")
        _scan_state = {
            "schema_version": "storage-scan.v1", "scan_id": scan.get("scan_id"), "status": "completed", "execution": "background",
            "progress": 100, "current_area": None, "areas": {str(k): dict(v) for k, v in areas.items() if isinstance(v, dict)},
            "exact": True, "mode": "deep_exact", "entries_scanned": int(scan.get("entries_scanned", 0)),
            "files_scanned": int(scan.get("files_scanned", 0)), "total_bytes_counted": int(scan.get("total_bytes_counted", 0)),
            "started_at": scan.get("started_at"), "completed_at": scan.get("completed_at"), "reason": result.get("reason", "Đã khôi phục tổng storage chính xác đã lưu."),
            "next_action": result.get("next_action", "Bấm Quét lại sau khi có thay đổi bên ngoài."), "cancel_requested": False,
            "saved_at": saved_at,
            "managed_root_counts": {str(k): dict(v) for k, v in (result.get("managed_root_counts") or {}).items() if isinstance(v, dict)},
            "owned_storage_total_bytes": int(result.get("owned_storage_total_bytes", scan.get("owned_storage_total_bytes", scan.get("total_bytes_counted", 0))) or 0),
            "owned_storage_exact": result.get("owned_storage_exact") is True or scan.get("owned_storage_exact") is True,
            "deduplicated_targets": int(result.get("deduplicated_targets", scan.get("deduplicated_targets", 0)) or 0),
            "disk": dict(result.get("disk") or {}), "volumes": [dict(item) for item in result.get("volumes") or [] if isinstance(item, dict)],
            "volume_projection": dict(result.get("volume_projection") or {}), "legacy": [dict(item) for item in result.get("legacy") or [] if isinstance(item, dict)],
            "legacy_counts": dict(result.get("legacy_counts") or {}),
        }
        _size_cache = (time.monotonic(), result)
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError, KeyError):
        return


def invalidate_storage_scan_cache() -> None:
    """Forget the exact snapshot; the next explicit scan starts fresh."""

    global _scan_cache_loaded, _size_cache, _scan_state
    with _scan_lock:
        _scan_cache_loaded = True
        _size_cache = None
        if _scan_thread is None or not _scan_thread.is_alive():
            _scan_state = {
                **_scan_state,
                "status": "idle",
                "exact": False,
                "scan_id": None,
                "progress": 0,
                "areas": {},
                "managed_root_counts": {},
                "saved_at": None,
                "owned_storage_total_bytes": 0,
                "owned_storage_exact": False,
                "deduplicated_targets": 0,
            }


def _managed_roots() -> tuple[Path, Path, Path, Path, Path, Path, Path, Path]:
    """Resolve the installed DATA_ROOT authority at call time.

    The registry constants are retained for legacy imports, but a split
    installation may select its data root through installation.json or the
    stable launch environment. Reading that authority here prevents a source
    checkout or historical Temp install from producing a false zero-sized
    storage card.
    """

    paths = get_paths()
    return (
        paths.data_root,
        paths.models_root,
        paths.environments_root,
        paths.runtime_root,
        paths.cache_root,
        paths.output_root,
        paths.temp_root,
        paths.log_root,
    )


def _is_reparse_point(entry: os.DirEntry[str]) -> bool:
    """Do not count a Windows junction target again through an alias."""

    try:
        if entry.is_symlink():
            return True
        attributes = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
        return bool(attributes & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT
    except OSError:
        return True


def _directory_size_report(path: Path) -> dict[str, Any]:
    """Return a bounded, path-free size projection for one managed root.

    The storage page is a read-only overview, not a recursive inventory.  A
    historical implementation walked every file under Temp/Models/Runtime and
    could keep ``GET /api/storage`` blocked for minutes on a real installation
    containing payloads, caches and preserved task worktrees.  Keep the scan
    finite and report a partial total when the budget is reached; callers can
    still show a useful number without claiming that the entire tree was read.
    Reparse points and non-regular entries are never followed.
    """

    def unavailable(reason: str) -> dict[str, Any]:
        return {
            "bytes": 0,
            "gb": 0.0,
            "status": "unavailable",
            "complete": False,
            "entries_scanned": 0,
            "files_scanned": 0,
            "directories_scanned": 0,
            "reparse_entries": 0,
            "unreadable_entries": 0,
            "reason": reason,
            "next_action": "Review the managed storage root before retrying.",
        }

    try:
        root_stat = path.stat(follow_symlinks=False)
        if getattr(root_stat, "st_file_attributes", 0) & 0x400 or path.is_symlink():
            return unavailable("Managed storage root is a reparse point and was not scanned.")
        if path.is_file():
            size = path.stat(follow_symlinks=False).st_size
            return {
                "bytes": size,
                "gb": round(size / (1024**3), 3),
                "status": "available",
                "complete": True,
                "entries_scanned": 1,
                "files_scanned": 1,
                "directories_scanned": 0,
                "reparse_entries": 0,
                "unreadable_entries": 0,
                "reason": "Managed storage size was read from a regular file.",
                "next_action": "No action is required; refresh after external storage changes.",
            }
        if not path.is_dir():
            return unavailable("Managed storage root is not a regular directory.")
    except OSError:
        return unavailable("Managed storage root is unavailable or cannot be read.")

    total = 0
    entries_scanned = 0
    files_scanned = 0
    directories_scanned = 0
    reparse_entries = 0
    unreadable_entries = 0
    truncated = False
    stack: list[tuple[Path, int]] = [(path, 0)]
    while stack:
        current, depth = stack.pop()
        if depth > _DIRECTORY_SCAN_MAX_DEPTH:
            truncated = True
            continue
        try:
            entries = os.scandir(current)
        except OSError:
            truncated = True
            continue
        with entries:
            for entry in entries:
                if entries_scanned >= _DIRECTORY_SCAN_MAX_ENTRIES:
                    truncated = True
                    break
                entries_scanned += 1
                try:
                    if _is_reparse_point(entry):
                        reparse_entries += 1
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        directories_scanned += 1
                        if depth < _DIRECTORY_SCAN_MAX_DEPTH:
                            stack.append((Path(entry.path), depth + 1))
                        else:
                            truncated = True
                        continue
                    if entry.is_file(follow_symlinks=False):
                        files_scanned += 1
                        total += entry.stat(follow_symlinks=False).st_size
                    else:
                        truncated = True
                except OSError:
                    truncated = True
                    unreadable_entries += 1
        if entries_scanned >= _DIRECTORY_SCAN_MAX_ENTRIES:
            break

    if truncated:
        status = "partial"
        reason = "Storage size is a bounded partial scan; deeper entries were not read."
        next_action = "Refresh storage after external changes; the displayed total is not a full inventory."
    else:
        status = "available"
        reason = "Managed storage size was read from the fixed server-owned root."
        next_action = "No action is required; refresh after external storage changes."
    return {
        "bytes": total,
        "gb": round(total / (1024**3), 3),
        "status": status,
        "complete": not truncated,
        "entries_scanned": entries_scanned,
        "files_scanned": files_scanned,
        "directories_scanned": directories_scanned,
        "reparse_entries": reparse_entries,
        "unreadable_entries": unreadable_entries,
        "reason": reason,
        "next_action": next_action,
    }


def _deep_directory_size_report(
    path: Path,
    *,
    cancel_event: threading.Event | None = None,
    on_progress: Any = None,
) -> dict[str, Any]:
    """Stream an exact candidate tree without the FAST entry budget.

    Only regular files below the managed root are counted.  Directory entries
    are consumed in small batches and no file list is retained, so the worker
    can traverse large model/environment trees without blocking an API thread
    or growing memory with every file.  Symlinks and Windows reparse points are
    deliberately skipped; their target bytes are outside this root's safe,
    server-owned accounting scope and therefore make the result partial.
    """

    event = cancel_event or threading.Event()
    total = 0
    entries_scanned = 0
    files_scanned = 0
    directories_scanned = 0
    reparse_entries = 0
    unreadable_entries = 0
    last_publish = time.monotonic()
    last_publish_entries = 0
    stack: list[Path] = []

    def report(*, status: str, complete: bool, reason: str, next_action: str) -> dict[str, Any]:
        value = {
            "bytes": total,
            "gb": round(total / (1024**3), 3),
            "total_bytes_counted": total,
            "status": status,
            "complete": complete,
            "entries_scanned": entries_scanned,
            "files_scanned": files_scanned,
            "directories_scanned": directories_scanned,
            "reparse_entries": reparse_entries,
            "unreadable_entries": unreadable_entries,
            "reason": reason,
            "next_action": next_action,
        }
        callback = on_progress
        if callable(callback):
            try:
                callback(dict(value))
            except Exception:
                # Progress publication must never turn a readable scan into a
                # false failure because a UI observer disappeared.
                pass
        return value

    def cancelled() -> dict[str, Any]:
        return report(
            status="cancelled",
            complete=False,
            reason="Đã hủy quét trước khi toàn bộ cây storage được đọc; tổng hiện tại là số đã đếm.",
            next_action="Bấm Quét lại để bắt đầu một deep scan mới.",
        )

    try:
        root_stat = path.stat(follow_symlinks=False)
        if getattr(root_stat, "st_file_attributes", 0) & 0x400 or path.is_symlink():
            return report(
                status="partial",
                complete=False,
                reason="Gốc storage là symlink/reparse point nên không thể xác nhận tổng chính xác.",
                next_action="Kiểm tra gốc managed storage rồi quét lại.",
            )
        if path.is_file():
            try:
                size = path.stat(follow_symlinks=False).st_size
            except OSError:
                return report(
                    status="partial",
                    complete=False,
                    reason="Không thể đọc kích thước tệp managed storage.",
                    next_action="Kiểm tra quyền đọc rồi quét lại.",
                )
            total += size
            entries_scanned = files_scanned = 1
            return report(
                status="available",
                complete=True,
                reason="Đã đọc chính xác tệp managed storage.",
                next_action="Không cần thao tác; quét lại sau khi có thay đổi bên ngoài.",
            )
        if not path.is_dir():
            return report(
                status="partial",
                complete=False,
                reason="Gốc managed storage không phải thư mục đọc được.",
                next_action="Kiểm tra gốc managed storage rồi quét lại.",
            )
        stack.append(path)
    except OSError:
        return report(
            status="partial",
            complete=False,
            reason="Không thể mở gốc managed storage để xác nhận tổng chính xác.",
            next_action="Kiểm tra quyền đọc rồi quét lại.",
        )

    while stack:
        if event.is_set():
            return cancelled()
        current = stack.pop()
        try:
            entries = os.scandir(current)
        except OSError:
            unreadable_entries += 1
            if event.is_set():
                return cancelled()
            continue
        with entries:
            for entry in entries:
                if event.is_set():
                    return cancelled()
                entries_scanned += 1
                try:
                    if _is_reparse_point(entry):
                        reparse_entries += 1
                    elif entry.is_dir(follow_symlinks=False):
                        directories_scanned += 1
                        stack.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        files_scanned += 1
                        total += entry.stat(follow_symlinks=False).st_size
                    else:
                        unreadable_entries += 1
                except OSError:
                    unreadable_entries += 1

                now = time.monotonic()
                if (
                    entries_scanned - last_publish_entries >= _DEEP_SCAN_YIELD_ENTRIES
                    or now - last_publish >= _DEEP_SCAN_YIELD_SECONDS
                ):
                    report(
                        status="running",
                        complete=False,
                        reason="Đang đọc toàn bộ cây storage; tổng bytes và số tệp sẽ tăng dần.",
                        next_action="Giữ trang mở hoặc bấm Hủy quét.",
                    )
                    last_publish_entries = entries_scanned
                    last_publish = now

    if event.is_set():
        return cancelled()
    if unreadable_entries or reparse_entries:
        reasons: list[str] = []
        if unreadable_entries:
            reasons.append(f"{unreadable_entries} mục không đọc được")
        if reparse_entries:
            reasons.append(f"{reparse_entries} symlink/reparse point bị bỏ qua")
        return report(
            status="partial",
            complete=False,
            reason="Không thể xác nhận tổng chính xác: " + "; ".join(reasons) + ".",
            next_action="Sửa quyền hoặc loại trừ reparse point rồi quét lại.",
        )
    return report(
        status="available",
        complete=True,
        reason="Đã đọc hết các thư mục managed bình thường; tổng bytes là chính xác.",
        next_action="Không cần thao tác; bấm Quét lại sau khi có thay đổi bên ngoài.",
    )


def _directory_size(path: Path) -> int:
    """Compatibility helper for model summaries; never performs an unbounded walk."""

    return int(_directory_size_report(path)["bytes"])


def _disk_snapshot(data_root: Path) -> dict[str, Any]:
    """Return bounded disk metadata without echoing the machine path."""

    try:
        usage = shutil.disk_usage(data_root)
        total, used, free = usage
        if any(type(value) is not int or value < 0 for value in usage) or used > total or free > total:
            raise OSError("invalid_disk_usage")
    except (OSError, ValueError, TypeError):
        return {"total_bytes": None, "free_bytes": None, "used_bytes": None, "free_gb": None, "low_space": None, "status": "unavailable"}
    return {
        "total_bytes": total,
        "free_bytes": free,
        "used_bytes": used,
        "free_gb": round(free / (1024**3), 3),
        "low_space": free < _LOW_SPACE_BYTES,
        "status": "available",
    }


def _path_key(path: Path) -> str:
    """Return a deterministic lexical key without following reparse points."""

    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _deduplicated_managed_target(path: Path, prior_paths: list[Path]) -> Path | None:
    """Return the prior allowlisted root that already owns ``path``.

    Managed roots normally are disjoint direct children of DATA_ROOT.  A
    split installation can nevertheless configure aliases or nested roots.
    Comparing the lexical, no-follow paths lets the deep worker avoid counting
    such an internal target twice without retaining every visited file in
    memory.  Reparse points are handled by the scanner itself and never make a
    target eligible for this shortcut.
    """

    candidate_key = _path_key(path)
    for prior in prior_paths:
        prior_key = _path_key(prior)
        if candidate_key == prior_key:
            return prior
        try:
            path.absolute().relative_to(prior.absolute())
        except ValueError:
            continue
        return prior
    return None


def _storage_summary_from_reports(
    data_root: Path,
    reports: dict[str, dict[str, Any]],
    *,
    scan_status: str | None = None,
    scan_execution: str = "not_run",
    progress: int = 100,
    current_area: str | None = None,
    scan_id: str | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    saved_at: str | None = None,
    scan_mode: str = "fast",
    deduplicated_targets: int = 0,
) -> dict[str, Any]:
    areas = {
        name: {
            **report,
            "bytes": int(report.get("bytes", 0)),
            "gb": float(report.get("gb", 0.0)),
        }
        for name, report in reports.items()
    }
    # A result is exact only when every allowlisted storage area has a
    # terminal complete report.  ``all([])`` and a cancelled worker with only
    # its first area completed must never be promoted to exact.
    exact = _reports_are_exact(reports)
    owned_total_bytes = sum(int(report.get("bytes", 0) or 0) for report in reports.values())
    entries_scanned = sum(int(report.get("entries_scanned", 0) or 0) for report in reports.values())
    files_scanned = sum(int(report.get("files_scanned", 0) or 0) for report in reports.values())
    directories_scanned = sum(int(report.get("directories_scanned", 0) or 0) for report in reports.values())
    reparse_entries = sum(int(report.get("reparse_entries", 0) or 0) for report in reports.values())
    unreadable_entries = sum(int(report.get("unreadable_entries", 0) or 0) for report in reports.values())
    completed_roots = sum(report.get("complete") is True for report in reports.values())
    managed_root_counts = {}
    for name in _SCAN_AREA_NAMES:
        report = reports.get(name, {"status": "pending", "complete": False})
        managed_root_counts[name] = {
            "complete": report.get("complete") is True,
            "status": str(report.get("status") or "pending"),
            "entries_scanned": int(report.get("entries_scanned", 0) or 0),
            "files_scanned": int(report.get("files_scanned", 0) or 0),
            "directories_scanned": int(report.get("directories_scanned", 0) or 0),
            "reparse_entries": int(report.get("reparse_entries", 0) or 0),
            "unreadable_entries": int(report.get("unreadable_entries", 0) or 0),
            "deduplicated": report.get("deduplicated") is True,
        }
    status = scan_status or ("completed" if exact else "partial")
    if status == "completed" and not exact:
        status = "partial"
    if status == "running":
        reason = "Storage đang được quét nền theo từng vùng; số liệu hiện tại chưa phải tổng chính xác."
        next_action = "Giữ trang mở hoặc bấm làm mới để theo dõi tiến độ; không chạy lại khi scan đang hoạt động."
    elif status == "cancelled":
        reason = "Deep scan đã bị hủy; tổng bytes hiện tại chỉ gồm phần đã đếm trước khi hủy."
        next_action = "Bấm Quét lại để đọc tiếp toàn bộ cây storage."
    elif exact:
        reason = "Tất cả vùng storage được đọc hết; tổng bytes managed là chính xác và loại trừ mục reparse bên ngoài."
        next_action = "Không cần thao tác; bấm Quét lại sau khi có thay đổi bên ngoài."
    else:
        incomplete = [
            name for name in _SCAN_AREA_NAMES
            if name not in reports or reports[name].get("complete") is not True
        ]
        reparse = sum(int(report.get("reparse_entries", 0) or 0) for report in reports.values())
        unreadable = sum(int(report.get("unreadable_entries", 0) or 0) for report in reports.values())
        details: list[str] = []
        if incomplete:
            details.append("vùng chưa hoàn tất: " + ", ".join(incomplete[:7]))
        if reparse:
            details.append(f"{reparse} symlink/reparse point bị bỏ qua")
        if unreadable:
            details.append(f"{unreadable} mục không đọc được")
        suffix = "; ".join(details) if details else "chưa có đủ bằng chứng hoàn tất"
        reason = "Không thể xác nhận tổng storage chính xác: " + suffix + ". Tổng hiển thị chỉ là số liệu đã đếm."
        next_action = "Kiểm tra quyền/reparse point hoặc bấm Quét lại để xác nhận lại."
    volumes = _volume_projection()
    legacy = _legacy_records()
    return {
        "status": status,
        "execution": scan_execution,
        "scan": {
            "schema_version": "storage-scan.v1",
            "status": status,
            "execution": scan_execution,
            "progress": max(0, min(100, int(progress))),
            "current_area": current_area,
            "scan_id": scan_id,
            "mode": scan_mode,
            "started_at": started_at,
            "completed_at": completed_at,
            "saved_at": saved_at if exact else None,
            "exact": exact,
            "owned_storage_total_bytes": owned_total_bytes,
            "owned_storage_total_gb": round(owned_total_bytes / (1024**3), 3),
            "owned_storage_exact": exact,
            "owned_storage_scope": "allowlisted managed roots only; external reparse targets excluded",
            "deduplicated_targets": max(0, int(deduplicated_targets)),
            "max_entries": _DIRECTORY_SCAN_MAX_ENTRIES if scan_mode == "fast" else None,
            "max_depth": _DIRECTORY_SCAN_MAX_DEPTH,
            "entries_scanned": entries_scanned,
            "files_scanned": files_scanned,
            "directories_scanned": directories_scanned,
            "reparse_entries": reparse_entries,
            "unreadable_entries": unreadable_entries,
            "completed_roots": completed_roots,
            "total_roots": len(_SCAN_AREA_NAMES),
            "total_bytes_counted": owned_total_bytes,
            "cancel_requested": False,
            "reason": reason,
            "next_action": next_action,
        },
        "disk": _disk_snapshot(data_root),
        "volumes": volumes,
        "volume_projection": _volume_projection_payload(volumes),
        "areas": areas,
        "managed_root_counts": managed_root_counts,
        "owned_storage_total_bytes": owned_total_bytes,
        "owned_storage_total_gb": round(owned_total_bytes / (1024**3), 3),
        "owned_storage_exact": exact,
        "owned_storage_scope": "allowlisted managed roots only; external reparse targets excluded",
        "deduplicated_targets": max(0, int(deduplicated_targets)),
        "saved_at": saved_at if exact else None,
        "legacy": legacy,
        "legacy_counts": {
            "total": len(legacy),
            "cleanup_candidates": sum(1 for item in legacy if item["cleanup_allowed"]),
            "unverified": sum(1 for item in legacy if not item["managed"]),
        },
        "canonical_root": "LocalAIHub",
        "data_location_class": "persistent_configured" if data_root != get_paths().app_root else "app_root",
        "reason": reason,
        "next_action": next_action,
        "scan_mode": scan_mode,
    }


def _copy_scan_state() -> dict[str, Any]:
    with _scan_lock:
        state = dict(_scan_state)
        state["areas"] = {name: dict(value) for name, value in (_scan_state.get("areas") or {}).items()}
        state["managed_root_counts"] = {name: dict(value) for name, value in (_scan_state.get("managed_root_counts") or {}).items()}
        return state


def storage_scan_snapshot() -> dict[str, Any]:
    """Return a path-free, incremental scan projection for UI polling."""

    with _scan_lock:
        _restore_exact_scan_cache()
    state = _copy_scan_state()
    area_values = state.get("areas") if isinstance(state.get("areas"), dict) else {}

    def aggregate_count(key: str) -> int:
        value = state.get(key)
        if isinstance(value, int) and value >= 0:
            return value
        return sum(int(item.get(key, 0) or 0) for item in area_values.values() if isinstance(item, dict))

    entries_scanned = aggregate_count("entries_scanned")
    files_scanned = aggregate_count("files_scanned")
    directories_scanned = aggregate_count("directories_scanned")
    reparse_entries = aggregate_count("reparse_entries")
    unreadable_entries = aggregate_count("unreadable_entries")
    completed_roots = sum(item.get("complete") is True for item in area_values.values() if isinstance(item, dict))
    scan = {
        key: state.get(key)
        for key in (
            "schema_version", "scan_id", "status", "execution", "progress", "current_area", "exact",
            "entries_scanned", "files_scanned", "total_bytes_counted", "started_at", "completed_at",
            "saved_at", "owned_storage_total_bytes", "owned_storage_exact", "deduplicated_targets",
            "reason", "next_action", "mode", "cancel_requested",
        )
    }
    scan.update({
        "entries_scanned": entries_scanned,
        "files_scanned": files_scanned,
        "directories_scanned": directories_scanned,
        "reparse_entries": reparse_entries,
        "unreadable_entries": unreadable_entries,
        "completed_roots": completed_roots,
        "total_roots": len(_SCAN_AREA_NAMES),
        "current_root": state.get("current_area"),
    })
    return {
        "status": state.get("status", "idle"),
        "execution": state.get("execution", "not_run"),
        "scan": scan,
        "areas": state.get("areas", {}),
        "managed_root_counts": state.get("managed_root_counts", {}),
        "owned_storage_total_bytes": state.get("owned_storage_total_bytes", 0),
        "owned_storage_exact": state.get("owned_storage_exact", False),
        "deduplicated_targets": state.get("deduplicated_targets", 0),
        "saved_at": state.get("saved_at"),
        "disk": state.get("disk", {}),
        "volumes": state.get("volumes", []),
        "volume_projection": state.get("volume_projection", {"status": "partial", "execution": "not_run", "allowlist": ["c", "d"], "volumes": []}),
        "legacy": state.get("legacy", []),
        "legacy_counts": state.get("legacy_counts", {"total": 0, "cleanup_candidates": 0, "unverified": 0}),
        "canonical_root": "LocalAIHub",
        "reason": state.get("reason", ""),
        "next_action": state.get("next_action", ""),
        "scan_mode": state.get("mode", "fast"),
    }


def _empty_scan_area(*, mode: str) -> dict[str, Any]:
    return {
        "bytes": 0,
        "gb": 0.0,
        "total_bytes_counted": 0,
        "status": "running",
        "complete": False,
        "entries_scanned": 0,
        "files_scanned": 0,
        "directories_scanned": 0,
        "reparse_entries": 0,
        "unreadable_entries": 0,
        "progress": 0,
        "mode": mode,
        "reason": "Đang chờ vùng storage này được quét.",
        "next_action": "Giữ trang mở hoặc bấm Hủy quét.",
    }


def _scan_progress(index: int, total_areas: int, report: dict[str, Any], estimate: int | None) -> int:
    """Return a monotonic, explicitly-estimated area-weighted progress value."""

    entries = max(0, int(report.get("entries_scanned", 0)))
    if estimate and estimate > 0:
        fraction = min(0.99, entries / estimate)
    else:
        # There is no safe total-entry oracle without a second full walk.  A
        # bounded window still gives useful movement while truthfully avoiding
        # a fabricated completion percentage.
        fraction = min(0.99, entries / (entries + _DEEP_SCAN_PROGRESS_WINDOW)) if entries else 0.0
    return max(0, min(99, int(((index - 1) + fraction) * 100 / total_areas)))


def _reports_are_exact(reports: dict[str, dict[str, Any]]) -> bool:
    """Return true only when every managed root is complete and readable."""

    if len(reports) != len(_SCAN_AREA_NAMES) or any(name not in reports for name in _SCAN_AREA_NAMES):
        return False
    return all(
        report.get("complete") is True
        and int(report.get("reparse_entries", 0) or 0) == 0
        and int(report.get("unreadable_entries", 0) or 0) == 0
        for report in reports.values()
    )


def _scan_worker(scan_id: str, mode: str, cancel_event: threading.Event) -> None:
    global _scan_state, _size_cache, _scan_thread
    data_root, model_root, environments_root, runtime_root, cache_root, output_root, temp_root, log_root = _managed_roots()
    roots = dict(zip(_SCAN_AREA_NAMES, (model_root, environments_root, runtime_root, cache_root, output_root, temp_root, log_root)))
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    reports: dict[str, dict[str, Any]] = {}
    deep = mode == "deep_exact"
    prior_paths: list[Path] = []
    deduplicated_targets = 0
    previous_areas: dict[str, Any] = {}
    with _scan_lock:
        previous_areas = {name: dict(value) for name, value in (_scan_state.get("areas") or {}).items()}

    def publish_area(name: str, index: int, report: dict[str, Any]) -> None:
        estimate_value = previous_areas.get(name, {}).get("entries_scanned")
        estimate = estimate_value if isinstance(estimate_value, int) and estimate_value > 0 else None
        area = dict(report)
        if deep:
            area["progress"] = _scan_progress(index, len(roots), report, estimate)
            area["mode"] = "deep_exact"
        with _scan_lock:
            reports[name] = area
            _scan_state["areas"] = {key: dict(value) for key, value in reports.items()}
            _scan_state["entries_scanned"] = sum(int(item.get("entries_scanned", 0)) for item in reports.values())
            _scan_state["files_scanned"] = sum(int(item.get("files_scanned", 0)) for item in reports.values())
            _scan_state["total_bytes_counted"] = sum(int(item.get("bytes", 0)) for item in reports.values())
            _scan_state["owned_storage_total_bytes"] = _scan_state["total_bytes_counted"]
            _scan_state["owned_storage_exact"] = False
            _scan_state["deduplicated_targets"] = deduplicated_targets
            _scan_state["managed_root_counts"] = {}
            for root_name in _SCAN_AREA_NAMES:
                value = reports.get(root_name, {"status": "pending", "complete": False})
                _scan_state["managed_root_counts"][root_name] = {
                    "complete": value.get("complete") is True,
                    "status": str(value.get("status") or "pending"),
                    "entries_scanned": int(value.get("entries_scanned", 0) or 0),
                    "files_scanned": int(value.get("files_scanned", 0) or 0),
                    "directories_scanned": int(value.get("directories_scanned", 0) or 0),
                    "reparse_entries": int(value.get("reparse_entries", 0) or 0),
                    "unreadable_entries": int(value.get("unreadable_entries", 0) or 0),
                    "deduplicated": value.get("deduplicated") is True,
                }
            _scan_state["progress"] = _scan_progress(index, len(roots), report, estimate) if deep else _scan_state.get("progress", 0)
            _scan_state["current_area"] = name

    try:
        for index, (name, path) in enumerate(roots.items(), start=1):
            if cancel_event.is_set():
                break
            with _scan_lock:
                _scan_state.update({
                    "status": "running",
                    "execution": "background",
                    "mode": mode,
                    "progress": round((index - 1) * 100 / len(roots)),
                    "current_area": name,
                    "started_at": started_at,
                    "completed_at": None,
                    "saved_at": None,
                    "total_bytes_counted": sum(int(item.get("bytes", 0)) for item in reports.values()),
                    "files_scanned": sum(int(item.get("files_scanned", 0)) for item in reports.values()),
                    "owned_storage_total_bytes": sum(int(item.get("bytes", 0)) for item in reports.values()),
                    "owned_storage_exact": False,
                    "cancel_requested": cancel_event.is_set(),
                    "reason": "Đang đọc toàn bộ cây storage; tổng bytes và số tệp sẽ tăng dần." if deep else "Storage đang được quét nền theo từng vùng; số liệu hiện tại chưa phải tổng chính xác.",
                    "next_action": "Giữ trang mở hoặc bấm Hủy quét." if deep else "Giữ trang mở hoặc bấm làm mới để theo dõi tiến độ; không chạy lại khi scan đang hoạt động.",
                })
            if deep:
                duplicate_of = _deduplicated_managed_target(path, prior_paths)
                if duplicate_of is not None:
                    deduplicated_targets += 1
                    report = {
                        "bytes": 0,
                        "gb": 0.0,
                        "total_bytes_counted": 0,
                        "status": "available",
                        "complete": True,
                        "entries_scanned": 0,
                        "files_scanned": 0,
                        "directories_scanned": 0,
                        "reparse_entries": 0,
                        "unreadable_entries": 0,
                        "deduplicated": True,
                        "reason": "Managed root trùng hoặc nằm trong root đã được tính; không cộng lại.",
                        "next_action": "Không cần thao tác.",
                    }
                else:
                    # Only a normal directory can own a later nested target;
                    # reparse/missing roots must remain visible as partial.
                    try:
                        root_stat = path.stat(follow_symlinks=False)
                        if not path.is_symlink() and not (getattr(root_stat, "st_file_attributes", 0) & 0x400) and path.is_dir():
                            prior_paths.append(path)
                    except OSError:
                        pass
                    publish_area(name, index, _empty_scan_area(mode=mode))
                    report = _deep_directory_size_report(
                        path,
                        cancel_event=cancel_event,
                        on_progress=lambda value, n=name, i=index: publish_area(n, i, value),
                    )
            else:
                report = _directory_size_report(path)
            publish_area(name, index, report)
            if cancel_event.is_set():
                break
            with _scan_lock:
                _scan_state["progress"] = round(index * 100 / len(roots))
        cancelled_scan = cancel_event.is_set()
        exact = _reports_are_exact(reports) and not cancelled_scan
        completed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if cancelled_scan:
            final_status = "cancelled"
        else:
            final_status = "completed" if exact else "partial"
        saved_at = datetime.now(timezone.utc).isoformat(timespec="seconds") if exact else None
        result = _storage_summary_from_reports(
            data_root,
            reports,
            scan_status=final_status,
            scan_execution="background",
            progress=100,
            current_area=None,
            scan_id=scan_id,
            started_at=started_at,
            completed_at=completed_at,
            saved_at=saved_at,
            scan_mode=mode,
            deduplicated_targets=deduplicated_targets,
        )
        with _scan_lock:
            _scan_state = {
                "schema_version": "storage-scan.v1",
                "scan_id": scan_id,
                "status": result["status"],
                "execution": "background",
                "progress": 100 if final_status in {"completed", "partial"} and not cancelled_scan else min(99, int(_scan_state.get("progress", 0))),
                "current_area": None,
                "areas": {name: dict(value) for name, value in reports.items()},
                "exact": exact,
                "mode": mode,
                "entries_scanned": result["scan"]["entries_scanned"],
                "files_scanned": result["scan"]["files_scanned"],
                "total_bytes_counted": result["scan"]["total_bytes_counted"],
                "started_at": started_at,
                "completed_at": completed_at,
                "saved_at": saved_at,
                "managed_root_counts": {name: dict(value) for name, value in result.get("managed_root_counts", {}).items()},
                "owned_storage_total_bytes": result.get("owned_storage_total_bytes", 0),
                "owned_storage_exact": result.get("owned_storage_exact", False),
                "deduplicated_targets": result.get("deduplicated_targets", 0),
                "reason": result["reason"],
                "next_action": result["next_action"],
                "cancel_requested": cancelled_scan,
                "disk": dict(result.get("disk") or {}),
                "volumes": [dict(item) for item in result.get("volumes") or []],
                "volume_projection": dict(result.get("volume_projection") or {}),
                "legacy": [dict(item) for item in result.get("legacy") or []],
                "legacy_counts": dict(result.get("legacy_counts") or {}),
            }
            _size_cache = (time.monotonic(), result)
        if exact:
            _persist_exact_scan(result, data_root)
    except Exception:
        with _scan_lock:
            _scan_state.update({
                "status": "unavailable",
                "execution": "background",
                "progress": min(99, int(_scan_state.get("progress", 0))),
                "current_area": None,
                "reason": "Storage scan không hoàn tất; một vùng không thể đọc an toàn.",
                "next_action": "Kiểm tra quyền vùng storage rồi thử lại.",
                "cancel_requested": cancel_event.is_set(),
            })
    finally:
        with _scan_lock:
            _scan_cancel_events.pop(scan_id, None)
            _scan_thread = None


def start_storage_scan(*, force: bool = False, mode: str = "fast") -> dict[str, Any]:
    """Start a FAST snapshot or explicit DEEP_EXACT background scan."""

    global _scan_thread, _scan_state, _size_cache, _scan_cache_loaded
    selected_mode = "deep_exact" if mode in {"deep", "deep_exact"} else "fast"
    with _scan_lock:
        _restore_exact_scan_cache()
        if _scan_thread is not None and _scan_thread.is_alive():
            return storage_scan_snapshot()
        if not force and _scan_state.get("scan_id") and _scan_state.get("status") in {"completed", "partial", "cancelled", "unavailable"}:
            return storage_scan_snapshot()
        scan_id = f"scan-{int(time.time() * 1000):x}"
        cancel_event = threading.Event()
        initial_disk = _disk_snapshot(_managed_roots()[0])
        _scan_state = {
            "schema_version": "storage-scan.v1",
            "scan_id": scan_id,
            "status": "running",
            "execution": "background",
            "progress": 0,
            "current_area": None,
            "areas": {},
            "exact": False,
            "entries_scanned": 0,
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "completed_at": None,
            "reason": "Đang đọc toàn bộ cây storage; tổng bytes và số tệp sẽ tăng dần." if selected_mode == "deep_exact" else "Storage đang được quét nền theo từng vùng; số liệu hiện tại chưa phải tổng chính xác.",
            "next_action": "Giữ trang mở hoặc bấm Hủy quét." if selected_mode == "deep_exact" else "Giữ trang mở hoặc bấm làm mới để theo dõi tiến độ; không chạy lại khi scan đang hoạt động.",
            "mode": selected_mode,
            "total_bytes_counted": 0,
            "files_scanned": 0,
            "cancel_requested": False,
            "saved_at": None,
            "managed_root_counts": {},
            "owned_storage_total_bytes": 0,
            "owned_storage_exact": False,
            "deduplicated_targets": 0,
            "disk": initial_disk,
        }
        _scan_cache_loaded = True
        _scan_cancel_events[scan_id] = cancel_event
        _size_cache = None
        _scan_thread = threading.Thread(target=_scan_worker, args=(scan_id, selected_mode, cancel_event), name="LocalAIHub-storage-scan", daemon=True)
        _scan_thread.start()
        return storage_scan_snapshot()


def cancel_storage_scan(scan_id: str | None = None) -> dict[str, Any]:
    """Request cooperative cancellation of the current background scan."""

    with _scan_lock:
        active_id = _scan_state.get("scan_id")
        if scan_id is not None and scan_id != active_id:
            return storage_scan_snapshot()
        if _scan_thread is None or not _scan_thread.is_alive() or _scan_state.get("status") != "running":
            return storage_scan_snapshot()
        event = _scan_cancel_events.get(str(active_id))
        if event is not None:
            event.set()
        _scan_state["cancel_requested"] = True
        _scan_state["status"] = "cancelling"
        _scan_state["reason"] = "Đang hủy deep scan ở checkpoint gần nhất; tổng hiện tại chỉ là số đã đếm."
        _scan_state["next_action"] = "Chờ worker dừng an toàn hoặc bấm Quét lại sau khi trạng thái đã hủy."
        return storage_scan_snapshot()


def _bytes_record(value: int) -> dict[str, Any]:
    return {"bytes": value, "gb": round(value / (1024**3), 3)}


def _unavailable_volume(*, volume_id: str, label: str, reason: str, next_action: str) -> dict[str, Any]:
    """Return a truthful volume record without exposing a workstation path."""

    return {
        "id": volume_id,
        "label": label,
        "total_bytes": None,
        "free_bytes": None,
        "used_bytes": None,
        "total_gb": None,
        "free_gb": None,
        "used_gb": None,
        "low_space": None,
        "status": "unavailable",
        "availability": "unknown",
        "reason": reason,
        "next_action": next_action,
    }


def _volume_record(volume_id: str, label: str, root: Path) -> dict[str, Any]:
    """Project one fixed allowlisted volume using server-owned disk usage."""

    try:
        usage = shutil.disk_usage(root)
    except FileNotFoundError:
        return _unavailable_volume(
            volume_id=volume_id,
            label=label,
            reason="Volume is not mounted or is unavailable.",
            next_action="Connect or mount the volume, then refresh storage.",
        )
    except PermissionError:
        return _unavailable_volume(
            volume_id=volume_id,
            label=label,
            reason="Volume is present but its statistics cannot be read.",
            next_action="Check volume permissions, then refresh storage.",
        )
    except OSError:
        return _unavailable_volume(
            volume_id=volume_id,
            label=label,
            reason="Volume statistics are unavailable.",
            next_action="Verify the volume is readable, then refresh storage.",
        )

    total, used, free = usage
    if any(type(value) is not int or value < 0 for value in (total, used, free)):
        return _unavailable_volume(
            volume_id=volume_id,
            label=label,
            reason="Volume statistics are invalid and were not displayed.",
            next_action="Refresh storage after the volume reports valid statistics.",
        )
    if free > total or used > total:
        return _unavailable_volume(
            volume_id=volume_id,
            label=label,
            reason="Volume statistics are inconsistent and were not displayed.",
            next_action="Refresh storage after the volume reports consistent statistics.",
        )

    low_space = free < _LOW_SPACE_BYTES
    reason = "Free space is below the 20 GiB low-space threshold." if low_space else "Volume statistics are available from the server-owned allowlist."
    next_action = "Review output, cache, and temporary data before new writes." if low_space else "No action is required; refresh after external storage changes."
    return {
        "id": volume_id,
        "label": label,
        "total_bytes": total,
        "free_bytes": free,
        "used_bytes": used,
        "total_gb": round(total / (1024**3), 3),
        "free_gb": round(free / (1024**3), 3),
        "used_gb": round(used / (1024**3), 3),
        "low_space": low_space,
        "status": "available",
        "availability": "available",
        "reason": reason,
        "next_action": next_action,
    }


def _volume_projection() -> list[dict[str, Any]]:
    """Inspect only the fixed C:/D: volume allowlist; never accept client input."""

    return [_volume_record(volume_id, label, root) for volume_id, label, root in _VOLUME_ALLOWLIST]


def _volume_projection_payload(volumes: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": "completed" if all(item["status"] == "available" for item in volumes) else "partial",
        "execution": "not_run",
        "allowlist": [item["id"] for item in volumes],
        "volumes": volumes,
    }


def dashboard_volume_snapshot(*, force: bool = False) -> dict[str, Any]:
    """Return a cached, metadata-only C:/D: snapshot for Dashboard bootstrap.

    This path deliberately does not read managed directories, legacy metadata, or
    model/provider endpoints.  Full areas/legacy storage remains owned by
    ``storage_summary`` and its existing route.
    """

    global _volume_snapshot_cache
    now = time.monotonic()
    with _cache_lock:
        if not force and _volume_snapshot_cache and now - _volume_snapshot_cache[0] < _CACHE_SECONDS:
            return _volume_snapshot_cache[1]
    result = _volume_projection_payload(_volume_projection())
    with _cache_lock:
        _volume_snapshot_cache = (now, result)
    return result


def _is_ollama_model(item: dict[str, Any]) -> bool:
    return str(item.get("engine") or "").casefold() == "ollama"


def _ollama_tag_sizes() -> dict[str, int]:
    """Return per-tag byte sizes from Ollama's fixed loopback tags endpoint.

    Ollama owns manifests and blobs, so scanning its model-store directory would
    over-count shared blobs and make every registry row look like the entire
    store.  The local tags API supplies the model/tag size that Ollama itself
    reports.  A missing or unavailable service is intentionally represented by
    an empty result rather than a guessed filesystem total.
    """

    request = Request(_OLLAMA_TAGS_URL, method="GET")
    try:
        with urlopen(request, timeout=_OLLAMA_TIMEOUT_SECONDS) as response:  # noqa: S310 - fixed loopback endpoint
            payload = json.load(response)
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return {}
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return {}
    result: dict[str, int] = {}
    for model in models:
        if not isinstance(model, dict):
            continue
        name = str(model.get("name") or model.get("model") or "").strip()
        size = model.get("size")
        if not name or isinstance(size, bool):
            continue
        try:
            size_bytes = int(size)
        except (TypeError, ValueError):
            continue
        if size_bytes >= 0:
            result[name] = size_bytes
    return result


def invalidate_model_cache() -> None:
    """Discard the read-only model summary cache after an external model change."""

    global _model_cache
    with _cache_lock:
        _model_cache = None


def _legacy_records() -> list[dict[str, Any]]:
    data_root, _models, _environments, _runtime, _cache, _output, _temp, _logs = _managed_roots()
    path = data_root / "Config" / "layout_migration.local.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return []
    records: list[dict[str, Any]] = []
    for entry in value.get("entries", []):
        if not isinstance(entry, dict):
            continue
        records.append({
            "component": entry.get("component"),
            "classification": entry.get("classification") or entry.get("type") or "UNKNOWN",
            "status": entry.get("status", "unknown"),
            "confidence": entry.get("confidence", "unknown"),
            "cleanup_allowed": bool(entry.get("cleanup_allowed", False)),
            "managed": entry.get("status") in {"verified", "external_managed", "external_system_app", "not_installed"},
        })
    return records


def storage_summary(*, force: bool = False) -> dict[str, Any]:
    global _size_cache
    with _scan_lock:
        _restore_exact_scan_cache()
    now = time.monotonic()
    with _cache_lock:
        if not force and _size_cache and now - _size_cache[0] < _CACHE_SECONDS:
            return _size_cache[1]

    data_root, model_root, environments_root, runtime_root, cache_root, output_root, temp_root, log_root = _managed_roots()
    usage = os.statvfs(data_root) if hasattr(os, "statvfs") else None
    if usage is not None:
        total = usage.f_blocks * usage.f_frsize
        free = usage.f_bavail * usage.f_frsize
    else:
        total, _used, free = shutil.disk_usage(data_root)
    roots = {
        "Models": model_root,
        "Environments": environments_root,
        "Runtime": runtime_root,
        "Cache": cache_root,
        "Output": output_root,
        "Temp": temp_root,
        "Logs": log_root,
    }
    area_reports = {name: _directory_size_report(path) for name, path in roots.items()}
    areas = {
        name: {
            **report,
            "bytes": int(report["bytes"]),
            "gb": float(report["gb"]),
        }
        for name, report in area_reports.items()
    }
    fast_exact = _reports_are_exact(area_reports)
    fast_owned_total = sum(int(report.get("bytes", 0) or 0) for report in area_reports.values())
    fast_entries = sum(int(report.get("entries_scanned", 0) or 0) for report in area_reports.values())
    fast_files = sum(int(report.get("files_scanned", 0) or 0) for report in area_reports.values())
    fast_directories = sum(int(report.get("directories_scanned", 0) or 0) for report in area_reports.values())
    fast_reparse = sum(int(report.get("reparse_entries", 0) or 0) for report in area_reports.values())
    fast_unreadable = sum(int(report.get("unreadable_entries", 0) or 0) for report in area_reports.values())
    fast_completed_roots = sum(report.get("complete") is True for report in area_reports.values())
    managed_root_counts = {
        name: {
            "complete": report.get("complete") is True,
            "status": str(report.get("status") or "unavailable"),
            "entries_scanned": int(report.get("entries_scanned", 0) or 0),
            "files_scanned": int(report.get("files_scanned", 0) or 0),
            "directories_scanned": int(report.get("directories_scanned", 0) or 0),
            "reparse_entries": int(report.get("reparse_entries", 0) or 0),
            "unreadable_entries": int(report.get("unreadable_entries", 0) or 0),
            "deduplicated": False,
        }
        for name, report in area_reports.items()
    }
    legacy = _legacy_records()
    volumes = _volume_projection()
    result = {
        "status": "completed" if fast_exact else "partial",
        "scan": {
            "status": "completed" if fast_exact else "partial",
            "mode": "fast",
            "exact": fast_exact,
            "owned_storage_total_bytes": fast_owned_total,
            "owned_storage_total_gb": round(fast_owned_total / (1024**3), 3),
            "owned_storage_exact": fast_exact,
            "owned_storage_scope": "allowlisted managed roots only; external reparse targets excluded",
            "deduplicated_targets": 0,
            "max_entries": _DIRECTORY_SCAN_MAX_ENTRIES,
            "max_depth": _DIRECTORY_SCAN_MAX_DEPTH,
            "entries_scanned": fast_entries,
            "files_scanned": fast_files,
            "directories_scanned": fast_directories,
            "reparse_entries": fast_reparse,
            "unreadable_entries": fast_unreadable,
            "completed_roots": fast_completed_roots,
            "total_roots": len(_SCAN_AREA_NAMES),
            "total_bytes_counted": fast_owned_total,
            "saved_at": None,
            "reason": "All managed roots were scanned within the bounded budget." if all(report["complete"] for report in area_reports.values()) else "One or more managed roots exceeded the bounded scan budget; partial totals are shown.",
        },
        "disk": {
            "total_bytes": total,
            "free_bytes": free,
            "used_bytes": max(0, total - free),
            "free_gb": round(free / (1024**3), 3),
            "low_space": free < _LOW_SPACE_BYTES,
        },
        "volumes": volumes,
        "volume_projection": _volume_projection_payload(volumes),
        "areas": areas,
        "managed_root_counts": managed_root_counts,
        "owned_storage_total_bytes": fast_owned_total,
        "owned_storage_total_gb": round(fast_owned_total / (1024**3), 3),
        "owned_storage_exact": fast_exact,
        "owned_storage_scope": "allowlisted managed roots only; external reparse targets excluded",
        "deduplicated_targets": 0,
        "saved_at": None,
        "legacy": legacy,
        "legacy_counts": {
            "total": len(legacy),
            "cleanup_candidates": sum(1 for item in legacy if item["cleanup_allowed"]),
            "unverified": sum(1 for item in legacy if not item["managed"]),
        },
        "canonical_root": "LocalAIHub",
        "data_location_class": "persistent_configured" if data_root != get_paths().app_root else "app_root",
    }
    with _cache_lock:
        _size_cache = (now, result)
    return result


def model_summary(*, force: bool = False) -> list[dict[str, Any]]:
    global _model_cache
    now = time.monotonic()
    with _cache_lock:
        if not force and _model_cache and now - _model_cache[0] < _CACHE_SECONDS:
            return [dict(item) for item in _model_cache[1]]
    _data_root, model_root, _environments, _runtime, _cache, _output, _temp, _logs = _managed_roots()
    value = load_json("model_registry.json", {})
    items = [item for item in value.get("models", []) if isinstance(item, dict)]
    ollama_sizes = _ollama_tag_sizes() if any(_is_ollama_model(item) for item in items) else {}
    result: list[dict[str, Any]] = []
    for item in items:
        is_ollama = _is_ollama_model(item)
        model_name = str(item.get("model_name") or item.get("id") or "")
        if is_ollama:
            metadata_size = ollama_sizes.get(model_name)
            installed = metadata_size is not None
            location = "Ollama-managed model store"
            size = metadata_size or 0
            size_source = "ollama_api_tags" if installed else "ollama_api_tags_unavailable"
        else:
            local_path = str(item.get("local_path") or "")
            if local_path.startswith("${"):
                location = "not configured"
                installed = False
                size = 0
            else:
                path = Path(os.path.expandvars(local_path))
                installed = path.exists()
                location = "managed model store" if path.is_relative_to(model_root) else "external managed"
                size = _directory_size(path) if installed else 0
            size_source = "filesystem"
        result.append({
            "id": item.get("id"),
            "model_name": model_name,
            "engine": item.get("engine"),
            "version": item.get("version"),
            "installed": installed,
            "location": location,
            "size": _bytes_record(size),
            "size_source": size_source,
            "load_policy": "on_demand",
        })
    with _cache_lock:
        _model_cache = (now, [dict(item) for item in result])
    return result
