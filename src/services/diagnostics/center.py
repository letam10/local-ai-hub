"""
  FILE NOTE
  - Mục đích: DiagnosticsCenter - hệ thống diagnostics read-only-first cho Local AI Hub V6, kiểm tra 12 subsystem không inference GPU/model
  - Liên kết trực tiếp: src/shared/paths/registry.py, src/app_config/settings_service.py, src/services/artifact_store.py, src/services/project_manager/manager.py, src/services/workflow_library/library.py, src/services/backup_manager.py
  - Vùng ảnh hưởng khi sửa: Trang diagnostics (Git integrity, Config, Jobs, Artifacts, Workflow, Models, Envs, Runtime, Storage, GPU, Errors, Recovery); chỉ đọc
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.shared.paths.registry import (
    ROOT,
    CONFIG_ROOT,
    LOG_ROOT,
    MODEL_ROOT,
    OUTPUT_ROOT,
    RUNTIME_ROOT,
    RUNTIME_PATHS,
    MODEL_PATHS,
)

# Status constants
HEALTHY = "HEALTHY"
NEEDS_ATTENTION = "NEEDS_ATTENTION"
UNAVAILABLE = "UNAVAILABLE"
UNKNOWN = "UNKNOWN"

# Maximum number of error log lines to tail
_MAX_LOG_LINES = 50
# Secret key pattern for sanitization
_SECRET_KEY_RE = re.compile(
    r"api[_-]?key|token|password|secret|credential|private[_-]?key|access[_-]?key",
    re.IGNORECASE,
)

DIAGNOSTIC_SUBSYSTEMS = (
    "git_integrity",
    "config_registry",
    "jobs_store",
    "artifact_store",
    "workflow_store",
    "models_inventory",
    "environments_inventory",
    "runtime_inventory",
    "storage",
    "gpu",
    "latest_app_errors",
    "recovery_forensic",
)
_PUBLIC_STATUSES = frozenset({HEALTHY, NEEDS_ATTENTION, UNAVAILABLE, UNKNOWN})
_PUBLIC_STATUS_MESSAGES = {
    HEALTHY: ("Diagnostics subsystem is healthy.", "No action required.", "diagnostics_healthy"),
    NEEDS_ATTENTION: ("Diagnostics subsystem needs attention.", "Review the bounded diagnostic details.", "diagnostics_needs_attention"),
    UNAVAILABLE: ("Diagnostics subsystem is unavailable.", "Review the managed subsystem state before retrying.", "diagnostics_unavailable"),
    UNKNOWN: ("Diagnostics subsystem state is unknown.", "Review the managed diagnostic source manually.", "diagnostic_projection_unavailable"),
}
_PUBLIC_CONFIG_FILES = frozenset({"settings.json", "creative_workspace.json", "workflow_library.json"})
_PUBLIC_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")
_PUBLIC_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_PUBLIC_UNSAFE_TEXT_RE = re.compile(
    r"(?i)(?:[A-Z]:[\\/]|\\\\|/(?:users|home|tmp)/|https?://|bearer\b|api[_-]?key|token\b|password|secret|private[_-]?key|command|executable|callable|\[object object\])"
)
_MAX_PUBLIC_COUNT = 100000
_MAX_PUBLIC_BYTES = 2**63 - 1
_PUBLIC_CATEGORY_FLAGS = ("draft", "temp", "unknown")
_CHECKED_AT_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.+-]+Z$")
_DIAGNOSTIC_INSPECTIONS = {
    "git_integrity": "Đã kiểm tra repository identity, branch và trạng thái worktree.",
    "config_registry": "Đã kiểm tra ba tệp cấu hình server-owned và schema_version có thể đọc được.",
    "jobs_store": "Đã đọc snapshot hàng đợi và lịch sử tác vụ qua API jobs giới hạn.",
    "artifact_store": "Đã kiểm tra chỉ mục artifact do Hub quản lý và khả năng đọc metadata.",
    "workflow_store": "Đã kiểm tra metadata Workflow Library và revision hiện tại.",
    "models_inventory": "Đã kiểm tra registry model và sự hiện diện của các root model; không nạp model.",
    "environments_inventory": "Đã kiểm tra root environment và các thư mục con cấp một; không chạy environment.",
    "runtime_inventory": "Đã kiểm tra các runtime leaf được allowlist; không khởi chạy runtime.",
    "storage": "Đã đọc metadata dung lượng volume allowlist; không quét lại cây storage.",
    "gpu": "Đã đọc metadata GPU bằng truy vấn nvidia-smi; không nạp model hoặc chạy inference.",
    "latest_app_errors": "Đã đọc hữu hạn các dòng lỗi gần đây từ log; giá trị nhạy cảm được loại bỏ.",
    "recovery_forensic": "Đã kiểm tra metadata draft, temporary và recovery; không xóa hay sửa dữ liệu.",
}


def _decorate_snapshot(name: str, value: object) -> dict[str, Any]:
    """Attach bounded inspection metadata to one internal diagnostic result.

    The public projection validates and drops unsafe values.  Keeping this
    decoration at the center boundary means every subsystem has the same
    ``inspected``, ``evidence_summary`` and ``checked_at`` contract without
    making each low-level check duplicate timestamp/wording logic.
    """

    result = dict(value) if isinstance(value, dict) else {"status": UNKNOWN}
    result.setdefault("inspected", _DIAGNOSTIC_INSPECTIONS.get(name, "Đã kiểm tra snapshot server-owned."))
    result.setdefault("evidence_summary", result.get("reason", "Snapshot chưa công bố bằng chứng chi tiết."))
    result.setdefault("impact", "Trạng thái này chỉ mô tả bằng chứng hiện có; không tự chạy workload.")
    result.setdefault("checked_at", datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"))
    return result


def _status(status: str, reason: str, next_action: str) -> dict[str, str]:
    return {"status": status, "reason": reason, "next_action": next_action}


def _sanitize_log_line(line: str) -> str:
    """Scrub likely secret values from a log line."""
    return re.sub(r"(?i)(api[_-]?key|token|password|secret)\s*[=:]\s*\S+", r"\1=[REDACTED]", line)


def _public_int(value: object, *, maximum: int = _MAX_PUBLIC_COUNT) -> int | None:
    if type(value) is not int or value < 0 or value > maximum:
        return None
    return value


def _public_bool(value: object) -> bool | None:
    return value if type(value) is bool else None


def _public_base(raw: object) -> dict[str, Any]:
    source = raw if type(raw) is dict else {}
    status = source.get("status") if type(raw) is dict else UNKNOWN
    if type(raw) is dict:
        for key in ("reason", "next_action"):
            if key in raw and (type(raw[key]) is not str or len(raw[key]) > 256 or _PUBLIC_UNSAFE_TEXT_RE.search(raw[key])):
                status = UNKNOWN
                break
    if type(status) is not str or status not in _PUBLIC_STATUSES:
        status = UNKNOWN
        reason, action, code = _PUBLIC_STATUS_MESSAGES[UNKNOWN]
    else:
        reason, action, code = _PUBLIC_STATUS_MESSAGES[status]
    result: dict[str, Any] = {
        "status": status,
        "reason": source.get("reason", reason) if status != UNKNOWN and isinstance(source.get("reason", reason), str) else reason,
        "next_action": source.get("next_action", action) if status != UNKNOWN and isinstance(source.get("next_action", action), str) else action,
        "reason_code": code,
        "execution": "not_run",
        "dry_run": True,
    }
    # These optional fields are emitted only by the center's decorated
    # snapshots.  Hostile/legacy input without them keeps the historical
    # fixed projection shape, while real diagnostics expose what was checked.
    for key in ("inspected", "evidence_summary", "impact"):
        value = source.get(key)
        if isinstance(value, str) and 0 < len(value) <= 256 and not _PUBLIC_UNSAFE_TEXT_RE.search(value):
            result[key] = value
    checked_at = source.get("checked_at")
    if isinstance(checked_at, str) and len(checked_at) <= 80 and _CHECKED_AT_RE.fullmatch(checked_at):
        result["checked_at"] = checked_at
    return result


def _public_fallback() -> dict[str, Any]:
    return _public_base({"status": UNKNOWN})


def _valid_optional_text(raw: dict[str, Any], key: str) -> bool:
    value = raw.get(key)
    return key not in raw or (type(value) is str and len(value) <= 256 and not _PUBLIC_UNSAFE_TEXT_RE.search(value))


def _public_git(raw: object) -> dict[str, Any]:
    if type(raw) is not dict or not all(_valid_optional_text(raw, key) for key in ("reason", "next_action")):
        return _public_fallback()
    root_verified = raw.get("root_verified", False)
    inside_work_tree = raw.get("inside_work_tree", False)
    if type(root_verified) is not bool or type(inside_work_tree) is not bool:
        return _public_fallback()
    return {**_public_base(raw), "root_verified": root_verified, "inside_work_tree": inside_work_tree}


def _public_config(raw: object) -> dict[str, Any]:
    if type(raw) is not dict:
        return _public_fallback()
    schema_versions = raw.get("schema_versions", {})
    if "schema_versions" not in raw and "file_count" in raw:
        file_count = _public_int(raw.get("file_count"), maximum=len(_PUBLIC_CONFIG_FILES))
        present_count = _public_int(raw.get("present_count", 0), maximum=len(_PUBLIC_CONFIG_FILES))
        invalid_count = _public_int(raw.get("invalid_count", 0), maximum=len(_PUBLIC_CONFIG_FILES))
        if file_count is None or present_count is None or invalid_count is None:
            return _public_fallback()
        return {
            **_public_base(raw),
            "file_count": file_count,
            "present_count": present_count,
            "invalid_count": invalid_count,
        }
    if "schema_versions" in raw and type(schema_versions) is not dict:
        return _public_fallback()
    present = 0
    invalid = 0
    for key, value in schema_versions.items():
        if type(key) is not str or key not in _PUBLIC_CONFIG_FILES:
            continue
        if type(value) is int and type(value) is not bool:
            if value < 0 or value > 100000:
                return _public_fallback()
        elif type(value) is not str or len(value) > 96 or not _PUBLIC_TOKEN_RE.fullmatch(value) or _PUBLIC_UNSAFE_TEXT_RE.search(value):
            return _public_fallback()
        if value not in {"absent", "unknown", "corrupt"}:
            present += 1
        if value in {"unknown", "corrupt"}:
            invalid += 1
    file_count = _public_int(raw.get("file_count", len(_PUBLIC_CONFIG_FILES)))
    present_count = _public_int(raw.get("present_count", present))
    invalid_count = _public_int(raw.get("invalid_count", invalid))
    if file_count is None or present_count is None or invalid_count is None:
        return _public_fallback()
    return {**_public_base(raw), "file_count": min(file_count, len(_PUBLIC_CONFIG_FILES)), "present_count": min(present_count, len(_PUBLIC_CONFIG_FILES)), "invalid_count": min(invalid_count, len(_PUBLIC_CONFIG_FILES))}


def _public_jobs(raw: object) -> dict[str, Any]:
    if type(raw) is not dict:
        return _public_fallback()
    if "counts" not in raw and "record_count" in raw:
        record_count = _public_int(raw.get("record_count"))
        if record_count is None:
            return _public_fallback()
        return {**_public_base(raw), "record_count": record_count}
    counts = raw.get("counts", {})
    if "counts" in raw and type(counts) is not dict:
        return _public_fallback()
    total = 0
    for key, value in counts.items():
        if type(key) is not str or len(key) > 64 or not _PUBLIC_TOKEN_RE.fullmatch(key) or _PUBLIC_UNSAFE_TEXT_RE.search(key):
            return _public_fallback()
        count = _public_int(value)
        if count is None:
            return _public_fallback()
        total += count
    record_count = _public_int(raw.get("record_count", total))
    if record_count is None:
        return _public_fallback()
    return {**_public_base(raw), "record_count": min(record_count, _MAX_PUBLIC_COUNT)}


def _public_artifact(raw: object) -> dict[str, Any]:
    if type(raw) is not dict:
        return _public_fallback()
    count = _public_int(raw.get("artifact_count", raw.get("total_artifacts", 0)))
    if count is None:
        return _public_fallback()
    return {**_public_base(raw), "artifact_count": count}


def _public_workflow(raw: object) -> dict[str, Any]:
    if type(raw) is not dict:
        return _public_fallback()
    count = _public_int(raw.get("workflow_count", 0))
    revision = raw.get("library_revision", 0)
    if count is None or (type(revision) is not int or type(revision) is bool or revision < 0 or revision > _MAX_PUBLIC_COUNT):
        return _public_fallback()
    return {**_public_base(raw), "workflow_count": count, "library_revision": revision}


def _public_inventory(raw: object, field: str) -> dict[str, Any]:
    if type(raw) is not dict:
        return _public_fallback()
    if field not in raw and "present_count" in raw and "total_count" in raw:
        present = _public_int(raw.get("present_count"))
        total = _public_int(raw.get("total_count"))
        if present is None or total is None or total < present:
            return _public_fallback()
        result = {**_public_base(raw), "present_count": present, "total_count": total}
        return _public_inventory_summary(result, raw)
    if field not in raw:
        # A missing managed root is still a meaningful unavailable inventory
        # result; do not turn it into an unexplained UNKNOWN merely because
        # there are no child identifiers to project.
        base = _public_base(raw)
        base.update({"present_count": 0, "total_count": 0})
        return _public_inventory_summary(base, raw)
    value = raw.get(field, {})
    if field == "environments":
        if type(value) is not list or len(value) > _MAX_PUBLIC_COUNT or any(type(item) is not str or len(item) > 256 or _PUBLIC_UNSAFE_TEXT_RE.search(item) for item in value):
            return _public_fallback()
        present = len(value)
        total = _public_int(raw.get("total_count", present))
    elif field == "runtimes":
        if type(value) is not dict or any(type(key) is not str or len(key) > 96 or _PUBLIC_UNSAFE_TEXT_RE.search(key) or type(item) is not bool for key, item in value.items()):
            return _public_fallback()
        present = sum(1 for item in value.values() if item)
        total = _public_int(raw.get("total_count", len(value)))
    else:
        if type(value) is not dict:
            return _public_fallback()
        present = 0
        for key, item in value.items():
            if type(key) is not str or len(key) > 96 or _PUBLIC_UNSAFE_TEXT_RE.search(key) or type(item) is not dict or type(item.get("present")) is not bool:
                return _public_fallback()
            if item["present"]:
                present += 1
        total = _public_int(raw.get("total_count", len(value)))
    if total is None or total < present:
        return _public_fallback()
    result = {**_public_base(raw), "present_count": present, "total_count": min(total, _MAX_PUBLIC_COUNT)}
    return _public_inventory_summary(result, raw)


def _public_inventory_summary(result: dict[str, Any], raw: object) -> dict[str, Any]:
    """Copy bounded inventory/readiness counters without exposing registry IDs."""

    source = raw if isinstance(raw, dict) else {}
    for key in (
        "registry_records",
        "observed_count",
        "verified_installed",
        "installed_unverified_count",
        "operational_count",
        "partial_count",
        "unknown_count",
        "not_installed_count",
        "unavailable_count",
    ):
        value = _public_int(source.get(key))
        if value is not None:
            result[key] = value
    healthy = _public_bool(source.get("inventory_healthy"))
    if healthy is not None:
        result["inventory_healthy"] = healthy
    note = source.get("readiness_note")
    if isinstance(note, str) and len(note) <= 256 and not _PUBLIC_UNSAFE_TEXT_RE.search(note):
        result["readiness_note"] = note
    return result


def _public_storage(raw: object) -> dict[str, Any]:
    if type(raw) is not dict:
        return _public_fallback()
    if "drives" not in raw and "drive_count" in raw:
        drive_count = _public_int(raw.get("drive_count"), maximum=16)
        low_space = raw.get("low_space")
        if drive_count is None or type(low_space) is not bool:
            return _public_fallback()
        return {**_public_base(raw), "drive_count": drive_count, "low_space": low_space}
    drives = raw.get("drives", {})
    if type(drives) is not dict:
        return _public_fallback()
    if len(drives) > 16:
        return _public_fallback()
    for key, value in drives.items():
        # Validate the fixed allowlist keys but never return them.  Arbitrary
        # drive/path labels remain rejected to keep the projection path-free.
        if type(key) is not str or key not in {"C:\\", "D:\\", "C:", "D:"}:
            return _public_fallback()
        if type(value) is not dict:
            return _public_fallback()
        for key in ("total_bytes", "used_bytes", "free_bytes"):
            if key in value and (type(value[key]) is not int or type(value[key]) is bool or value[key] < 0 or value[key] > _MAX_PUBLIC_BYTES):
                return _public_fallback()
    result = {**_public_base(raw), "drive_count": min(len(drives), 16), "low_space": raw.get("status") == NEEDS_ATTENTION}
    # Storage diagnostics reuse the storage-manager snapshot instead of doing
    # another recursive walk.  Keep only bounded counters/state; never expose
    # the managed root or a caller-supplied path.
    scan = raw.get("scan")
    if isinstance(scan, dict):
        scan_status = scan.get("status")
        if isinstance(scan_status, str) and scan_status in {"idle", "running", "cancelling", "cancelled", "partial", "completed", "unavailable"}:
            result["scan_status"] = scan_status
        scan_mode = scan.get("mode")
        if isinstance(scan_mode, str) and scan_mode in {"fast", "deep_exact"}:
            result["scan_mode"] = scan_mode
        if type(scan.get("exact")) is bool:
            result["scan_exact"] = scan["exact"]
        for source_key, public_key in (("progress", "scan_progress"), ("files_scanned", "scan_files"), ("total_bytes_counted", "scan_bytes")):
            value = _public_int(scan.get(source_key), maximum=_MAX_PUBLIC_COUNT if source_key == "progress" else _MAX_PUBLIC_BYTES)
            if value is not None:
                result[public_key] = value
        for source_key, public_key in (("reason", "scan_reason"), ("next_action", "scan_next_action")):
            value = scan.get(source_key)
            if isinstance(value, str) and len(value) <= 256 and not _PUBLIC_UNSAFE_TEXT_RE.search(value):
                result[public_key] = value
    return result


def _public_gpu(raw: object) -> dict[str, Any]:
    if type(raw) is not dict:
        return _public_fallback()
    if "gpus" not in raw and "gpu_count" in raw:
        gpu_count = _public_int(raw.get("gpu_count"), maximum=16)
        if gpu_count is None:
            return _public_fallback()
        return {**_public_base(raw), "gpu_count": gpu_count}
    gpus = raw.get("gpus", [])
    if type(gpus) is not list or len(gpus) > 16 or any(type(item) is not str or len(item) > 128 or _PUBLIC_UNSAFE_TEXT_RE.search(item) for item in gpus):
        return _public_fallback()
    return {**_public_base(raw), "gpu_count": len(gpus)}


def _public_errors(raw: object) -> dict[str, Any]:
    if type(raw) is not dict:
        return _public_fallback()
    if "lines" not in raw and "error_count" in raw and "has_errors" in raw:
        error_count = _public_int(raw.get("error_count"), maximum=_MAX_LOG_LINES)
        has_errors = raw.get("has_errors")
        digest = raw.get("digest")
        if error_count is None or type(has_errors) is not bool or (digest is not None and (type(digest) is not str or not _PUBLIC_DIGEST_RE.fullmatch(digest))):
            return _public_fallback()
        return {**_public_base(raw), "has_errors": has_errors, "error_count": error_count, "digest": digest}
    lines = raw.get("lines", [])
    if type(lines) is not list or len(lines) > _MAX_LOG_LINES or any(type(item) is not str or len(item) > 4096 or _PUBLIC_UNSAFE_TEXT_RE.search(item) for item in lines):
        return _public_fallback()
    digest = hashlib.sha256(json.dumps(lines, ensure_ascii=True, separators=(",", ":")).encode("utf-8")).hexdigest() if lines else None
    return {**_public_base(raw), "has_errors": bool(lines), "error_count": len(lines), "digest": digest}


def _public_recovery(raw: object) -> dict[str, Any]:
    if type(raw) is not dict:
        return _public_fallback()
    if "files" not in raw and "recovery_count" in raw and "has_recovery_files" in raw and "category_flags" in raw:
        recovery_count = _public_int(raw.get("recovery_count"))
        has_files = raw.get("has_recovery_files")
        category_flags = raw.get("category_flags")
        if recovery_count is None or type(has_files) is not bool or type(category_flags) is not dict:
            return _public_fallback()
        if set(category_flags) != set(_PUBLIC_CATEGORY_FLAGS) or any(type(value) is not bool for value in category_flags.values()):
            return _public_fallback()
        return {
            **_public_base(raw),
            "has_recovery_files": has_files,
            "recovery_count": recovery_count,
            "category_flags": {key: category_flags[key] for key in _PUBLIC_CATEGORY_FLAGS},
        }
    files = raw.get("files", [])
    if type(files) is not list or len(files) > _MAX_PUBLIC_COUNT or any(type(item) is not str or len(item) > 256 or _PUBLIC_UNSAFE_TEXT_RE.search(item) for item in files):
        return _public_fallback()
    flags = {key: False for key in _PUBLIC_CATEGORY_FLAGS}
    for item in files:
        if item.startswith("draft_") or item.startswith("node_studio_draft_"):
            flags["draft"] = True
        elif item.startswith(".") and item.endswith(".tmp"):
            flags["temp"] = True
        else:
            flags["unknown"] = True
    return {**_public_base(raw), "has_recovery_files": bool(files), "recovery_count": len(files), "category_flags": flags}


def _public_subsystem(name: str, raw: object) -> dict[str, Any]:
    if type(raw) is dict and raw.get("reason_code") == "diagnostic_projection_unavailable":
        detail_keys = {
            "schema_versions", "file_count", "counts", "record_count", "artifact_count", "total_artifacts",
            "workflow_count", "library_revision", "models", "environments", "runtimes", "present_count",
            "total_count", "drives", "drive_count", "low_space", "gpus", "gpu_count", "lines", "error_count",
            "has_errors", "digest", "files", "recovery_count", "has_recovery_files", "category_flags",
            "root_verified", "inside_work_tree", "inspected", "evidence_summary", "impact", "checked_at",
            "registry_records", "observed_count", "verified_installed", "installed_unverified_count",
            "operational_count", "partial_count", "unknown_count", "not_installed_count", "unavailable_count",
            "inventory_healthy", "readiness_note", "scan_status", "scan_mode", "scan_exact",
            "scan_progress", "scan_files", "scan_bytes", "scan_reason", "scan_next_action",
        }
        if not any(key in raw for key in detail_keys):
            return _public_fallback()
    if name == "git_integrity":
        return _public_git(raw)
    if name == "config_registry":
        return _public_config(raw)
    if name == "jobs_store":
        return _public_jobs(raw)
    if name == "artifact_store":
        return _public_artifact(raw)
    if name == "workflow_store":
        return _public_workflow(raw)
    if name == "models_inventory":
        return _public_inventory(raw, "models")
    if name == "environments_inventory":
        return _public_inventory(raw, "environments")
    if name == "runtime_inventory":
        return _public_inventory(raw, "runtimes")
    if name == "storage":
        return _public_storage(raw)
    if name == "gpu":
        return _public_gpu(raw)
    if name == "latest_app_errors":
        return _public_errors(raw)
    if name == "recovery_forensic":
        return _public_recovery(raw)
    return _public_fallback()


def public_snapshot_projection(raw: object) -> dict[str, dict[str, Any]]:
    """Return the only public diagnostics shape; unknown input is ignored."""

    source = raw if type(raw) is dict else {}
    return {name: _public_subsystem(name, source.get(name)) for name in DIAGNOSTIC_SUBSYSTEMS}


def public_export_projection(raw: object) -> dict[str, Any]:
    source = raw.get("bundle") if type(raw) is dict and type(raw.get("bundle")) is dict else raw
    return {"bundle": public_snapshot_projection(source), "sanitized": True}


class DiagnosticsCenter:
    """Read-only-first diagnostics for all Local AI Hub subsystems.

    Never starts workers, never performs AI inference, never accesses GPU,
    never loads models.  Allowed actions: refresh, verify, export_diagnostics_bundle,
    open_logs_path, rebuild_local_registry (machine-local only, guarded).
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Config / State subsystems
    # ------------------------------------------------------------------

    def git_integrity_state(self) -> dict[str, Any]:
        """Check repository HEAD and branch in read-only manner."""
        git_dir = ROOT / ".git"
        if not git_dir.exists():
            return {
                **_status(UNKNOWN, "Not a git repository or release build.", "No action required."),
                "root_verified": ROOT.exists(),
                "inside_work_tree": False,
                "head_sha": "",
                "branch": "",
                "origin": "",
            }

        inside_work_tree = False
        head_sha = ""
        branch = ""
        origin = ""

        import subprocess
        try:
            res_worktree = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=2.0,
            )
            inside_work_tree = res_worktree.returncode == 0 and res_worktree.stdout.strip() == "true"
        except (subprocess.SubprocessError, OSError):
            inside_work_tree = git_dir.exists()

        try:
            res_head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=2.0,
            )
            if res_head.returncode == 0:
                head_sha = res_head.stdout.strip()
        except (subprocess.SubprocessError, OSError):
            pass

        try:
            res_branch = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=2.0,
            )
            if res_branch.returncode == 0:
                branch = res_branch.stdout.strip()
        except (subprocess.SubprocessError, OSError):
            pass

        try:
            res_origin = subprocess.run(
                ["git", "config", "--get", "remote.origin.url"],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=2.0,
            )
            if res_origin.returncode == 0:
                raw_origin = res_origin.stdout.strip()
                origin = re.sub(r"(https?://)[^:@/\s]+:[^@/\s]+@", r"\1[REDACTED]@", raw_origin)
                origin = re.sub(r"(https?://)[^:@/\s]+@", r"\1[REDACTED]@", origin)
        except (subprocess.SubprocessError, OSError):
            pass

        if not head_sha:
            try:
                head_file = git_dir / "HEAD" if git_dir.is_dir() else None
                if head_file and head_file.exists():
                    ref = head_file.read_text(encoding="utf-8").strip()
                    if ref.startswith("ref: "):
                        branch = ref.removeprefix("ref: refs/heads/")
                    else:
                        head_sha = ref
            except Exception:
                pass

        return {
            **_status(HEALTHY, f"Git repository verified at branch '{branch or 'detached'}' @ {(head_sha[:7] if head_sha else 'HEAD')}.", "No action required."),
            "root_verified": ROOT.exists(),
            "inside_work_tree": inside_work_tree,
            "head_sha": head_sha,
            "branch": branch,
            "origin": origin,
        }

    def config_registry_state(self) -> dict[str, Any]:
        """Check settings, workspace, workflow library files."""
        files = {
            "settings.json": CONFIG_ROOT / "settings.json",
            "creative_workspace.json": CONFIG_ROOT / "creative_workspace.json",
            "workflow_library.json": CONFIG_ROOT / "workflow_library.json",
        }
        issues: list[str] = []
        schema_versions: dict[str, Any] = {}

        for name, path in files.items():
            if not path.exists():
                schema_versions[name] = "absent"
                continue
            try:
                data = json.loads(path.read_bytes())
                schema_versions[name] = data.get("schema_version", "unknown")
                if data.get("recovery_required") or data.get("status") == "recovery_required":
                    issues.append(f"{name}: recovery_required")
            except (OSError, json.JSONDecodeError):
                issues.append(f"{name}: unreadable")
                schema_versions[name] = "corrupt"

        if issues:
            return {
                **_status(NEEDS_ATTENTION, f"Config issues: {'; '.join(issues)}", "Inspect and repair or restore from backup."),
                "schema_versions": schema_versions,
            }
        return {
            **_status(HEALTHY, "All tracked config files readable.", "No action required."),
            "schema_versions": schema_versions,
        }

    def jobs_store_state(self) -> dict[str, Any]:
        """Report jobs store health (count by status)."""
        try:
            from src.services.api.jobs import list_jobs
            jobs = list_jobs(limit=1000)
            counts: dict[str, int] = {}
            for job in jobs:
                s = str(job.get("status", "unknown"))
                counts[s] = counts.get(s, 0) + 1
            return {
                **_status(HEALTHY, f"Jobs store readable. {sum(counts.values())} jobs.", "No action required."),
                "counts": counts,
            }
        except Exception as exc:
            return _status(UNAVAILABLE, f"Jobs store unavailable: {exc}", "Check job_manager module.")

    def artifact_store_state(self) -> dict[str, Any]:
        """Check artifact store health via public diagnostic method."""
        try:
            from src.services import artifact_store as ast
            summary = ast.diagnostics_summary()
            count = int(summary.get("total_artifacts", 0))
            return {
                **_status(HEALTHY, f"Artifact store accessible. {count} entries.", "No action required."),
                "artifact_count": count,
            }
        except Exception as exc:
            return _status(UNAVAILABLE, f"Artifact store unavailable: {exc}", "Restart Hub to re-initialise store.")

    def workflow_store_state(self) -> dict[str, Any]:
        """Check workflow library file state."""
        path = CONFIG_ROOT / "workflow_library.json"
        if not path.exists():
            return _status(HEALTHY, "Workflow library absent (no workflows saved yet).", "Create workflows to initialise.")
        try:
            data = json.loads(path.read_bytes())
            revision = data.get("library_revision", "?")
            count = len(data.get("workflows") or [])
            return {
                **_status(HEALTHY, f"Workflow library readable. {count} workflows, revision {revision}.", "No action required."),
                "workflow_count": count,
                "library_revision": revision,
            }
        except (OSError, json.JSONDecodeError) as exc:
            return _status(NEEDS_ATTENTION, f"Workflow library unreadable: {exc}", "Restore from backup or export/import.")

    # ------------------------------------------------------------------
    # Inventory subsystems (read-only filesystem scan)
    # ------------------------------------------------------------------

    @staticmethod
    def _catalog_inventory_counts(section: str) -> dict[str, int] | None:
        """Read the bounded production-catalog counters without starting a scan.

        The catalog owns fixed-leaf model/runtime readiness.  Diagnostics may
        reuse that metadata, but it must not recursively walk Models or
        Environments a second time just to render a health card.
        """

        try:
            from src.platform.paths import get_paths
            from src.services.productization.catalog import ProductionCatalog

            # Build the catalog directly instead of calling the compatibility
            # ``catalog_snapshot`` façade.  The façade may consult legacy
            # model summaries (and an Ollama loopback tags endpoint); a
            # diagnostics refresh must stay metadata-only and never probe a
            # provider just to render inventory counters.
            snapshot = ProductionCatalog(paths=get_paths()).snapshot()
            source: object = snapshot.get("inventory") if isinstance(snapshot, dict) else None
            if section == "runtimes" and isinstance(source, dict):
                source = source.get("runtimes")
            if not isinstance(source, dict) or source.get("status") != "healthy":
                return None
            result: dict[str, int] = {}
            for key in (
                "registry_records",
                "observed_count",
                "verified_installed",
                "installed_unverified_count",
                "operational_count",
                "partial_count",
                "unknown_count",
                "not_installed_count",
                "unavailable_count",
            ):
                value = source.get(key)
                if type(value) is int and value >= 0 and value <= _MAX_PUBLIC_COUNT:
                    result[key] = value
            return result if result.get("registry_records", 0) > 0 else None
        except Exception:
            # Diagnostics must remain available even when an optional catalog
            # snapshot cannot be read; the caller falls back to fixed roots.
            return None

    @staticmethod
    def _inventory_reason(
        label: str,
        counts: dict[str, int],
        *,
        healthy: bool,
    ) -> str:
        """Build a path-free explanation of inventory versus readiness."""

        records = counts.get("registry_records", 0)
        observed = counts.get("observed_count", 0)
        verified = counts.get("verified_installed", 0)
        unverified = counts.get("installed_unverified_count", 0)
        operational = counts.get("operational_count", 0)
        partial = counts.get("partial_count", 0)
        unknown = counts.get("unknown_count", 0)
        not_installed = counts.get("not_installed_count", 0)
        unavailable = counts.get("unavailable_count", 0)
        prefix = "Registry đọc được" if healthy else "Chưa đọc đủ registry"
        return (
            f"{prefix}: {records} bản ghi {label}; cục bộ {observed}; đã xác minh {verified}; "
            f"operational {operational}; chưa xác minh {unverified}; một phần {partial}; "
            f"chưa rõ {unknown}; chưa cài {not_installed}; không khả dụng {unavailable}. "
            "Healthy chỉ nói registry đọc được, không bảo đảm mọi mục chạy được."
        )

    def models_inventory(self) -> dict[str, Any]:
        """Read the model registry/root presence without recursively walking it.

        Recursive accounting belongs to the explicit storage DEEP_EXACT worker.
        Diagnostics must stay bounded and must not turn a refresh click into a
        second multi-hundred-thousand-entry scan.  Presence here is therefore
        inventory evidence only, never model-readiness or operational proof.
        """
        catalog_counts = self._catalog_inventory_counts("models")
        if not MODEL_ROOT.exists():
            fallback_counts = catalog_counts or {
                "registry_records": len(MODEL_PATHS),
                "observed_count": 0,
                "verified_installed": 0,
                "installed_unverified_count": 0,
                "operational_count": 0,
                "partial_count": 0,
                "unknown_count": len(MODEL_PATHS),
                "not_installed_count": 0,
                "unavailable_count": len(MODEL_PATHS),
            }
            return {
                **_status(UNAVAILABLE, "Models/ directory not found. " + self._inventory_reason("model", fallback_counts, healthy=False), "Review the managed model root before requesting component setup."),
                **fallback_counts,
                "inventory_healthy": False,
                "readiness_note": "Inventory không xác nhận model readiness; cần kiểm tra root managed và component evidence riêng.",
            }
        entries: dict[str, Any] = {}
        for key, path in MODEL_PATHS.items():
            try:
                stat = path.stat(follow_symlinks=False)
                is_reparse = bool(getattr(stat, "st_file_attributes", 0) & 0x400) or path.is_symlink()
                entries[key] = {"present": bool(path.exists()), "reparse": is_reparse, "size_bytes": None}
            except OSError:
                entries[key] = {"present": False, "reparse": False, "size_bytes": None}
        present = sum(1 for v in entries.values() if v["present"])
        missing_keys = [k for k, v in entries.items() if not v["present"]]
        reparse_partial = sum(1 for value in entries.values() if value["present"] and value.get("reparse"))
        counts = catalog_counts or {
            "registry_records": len(entries),
            "observed_count": present,
            "verified_installed": 0,
            "installed_unverified_count": 0,
            "operational_count": 0,
            "partial_count": reparse_partial,
            "unknown_count": 0,
            "not_installed_count": len(missing_keys),
            "unavailable_count": 0,
        }
        if reparse_partial:
            counts = {**counts, "partial_count": counts.get("partial_count", 0) + reparse_partial}
        missing_count = counts.get("not_installed_count", len(missing_keys))
        needs_review = counts.get("installed_unverified_count", 0) + counts.get("partial_count", 0) + counts.get("unknown_count", 0)
        next_action = (
            f"Có {missing_count} model chưa cài; mở Components để xem import/license/runtime và không tải lại resource đã quan sát."
            if missing_count else
            "Inventory đã đọc được; mở Components để xem model nào đã được xác minh runtime."
        )
        if needs_review:
            next_action += " Các mục một phần/chưa xác minh vẫn chưa nên coi là operational."
        return {
            **_status(HEALTHY if catalog_counts is not None or present or not missing_keys else NEEDS_ATTENTION, self._inventory_reason("model", counts, healthy=catalog_counts is not None or bool(present or not missing_keys)), next_action),
            "models": entries,
            "registry_records": len(entries),
            **counts,
            "inventory_healthy": catalog_counts is not None or bool(present or not missing_keys),
            "readiness_note": "Healthy chỉ mô tả registry/root inventory; model chỉ được coi là operational khi có bằng chứng runtime riêng.",
        }

    def environments_inventory(self) -> dict[str, Any]:
        """Check presence of runtime environments using canonical registry path."""
        envs_root = ROOT / "Environments"
        if not envs_root.exists():
            return _status(UNAVAILABLE, "Environments/ directory not found.", "Environment paths not found; review/install requires explicit user action.")
        envs: list[str] = []
        unreadable = 0
        try:
            for entry in envs_root.iterdir():
                try:
                    if entry.is_dir() and not entry.is_symlink():
                        envs.append(entry.name)
                except OSError:
                    unreadable += 1
        except OSError:
            unreadable = 1
        status = HEALTHY if unreadable == 0 else NEEDS_ATTENTION
        counts = {
            "registry_records": len(envs),
            "observed_count": len(envs),
            "verified_installed": 0,
            "installed_unverified_count": 0,
            "operational_count": 0,
            "partial_count": unreadable,
            "unknown_count": 0,
            "not_installed_count": 0 if envs else 1,
            "unavailable_count": 0,
        }
        return {
            **_status(status, self._inventory_reason("environment", counts, healthy=unreadable == 0), "Mở Components để kiểm tra runtime/environment readiness; cài đặt cần thao tác rõ ràng." if not envs else "Inventory đã đọc được; readiness runtime cần bằng chứng riêng."),
            "environments": envs,
            **counts,
            "inventory_healthy": unreadable == 0,
            "readiness_note": "Environment inventory chỉ xác nhận root cấp một; runtime/import/worker smoke được đánh giá riêng.",
        }

    def runtime_inventory(self) -> dict[str, Any]:
        """Check presence of registered runtime paths."""
        entries: dict[str, bool] = {key: path.exists() for key, path in RUNTIME_PATHS.items()}
        present = sum(entries.values())
        catalog_counts = self._catalog_inventory_counts("runtimes")
        counts = catalog_counts or {
            "registry_records": len(entries),
            "observed_count": present,
            "verified_installed": 0,
            "installed_unverified_count": 0,
            "operational_count": 0,
            "partial_count": 0,
            "unknown_count": 0,
            "not_installed_count": len(entries) - present,
            "unavailable_count": 0,
        }
        healthy = catalog_counts is not None or bool(present)
        return {
            **_status(HEALTHY if healthy else UNAVAILABLE, self._inventory_reason("runtime", counts, healthy=healthy), "Mở Components để kiểm tra import/runtime smoke và trạng thái cài đặt." if counts.get("not_installed_count", 0) else "Inventory đã đọc được; runtime vẫn cần bằng chứng bounded smoke."),
            "runtimes": entries,
            **counts,
            "inventory_healthy": healthy,
            "readiness_note": "Runtime inventory chỉ xác nhận registry/fixed leaves; import, worker và bounded smoke được đánh giá riêng.",
        }

    # ------------------------------------------------------------------
    # System subsystems
    # ------------------------------------------------------------------

    def storage_state(self) -> dict[str, Any]:
        """Report volume usage plus the existing storage-scan snapshot.

        This is intentionally a bounded diagnostic read.  The storage manager
        owns the optional deep walk; Diagnostics only consumes its latest
        path-free state and never starts a second scan.
        """
        results: dict[str, Any] = {}
        for drive in ("C:\\", "D:\\"):
            try:
                usage = shutil.disk_usage(drive)
                results[drive] = {
                    "total_bytes": usage.total,
                    "used_bytes": usage.used,
                    "free_bytes": usage.free,
                    "percent_used": round(usage.used / usage.total * 100, 1) if usage.total else 0,
                }
            except OSError:
                results[drive] = {"error": "inaccessible"}
        low_space = any(
            v.get("percent_used", 0) > 90 or v.get("free_bytes", float("inf")) < 2 * 1024 ** 3
            for v in results.values()
            if "error" not in v
        )
        result: dict[str, Any] = {
            **_status(NEEDS_ATTENTION if low_space else HEALTHY,
                      "Low disk space detected." if low_space else "Disk space adequate.",
                      "Free disk space on affected drives." if low_space else "No action required."),
            "drives": results,
        }
        try:
            from src.services.storage_manager.overview import storage_scan_snapshot

            snapshot = storage_scan_snapshot()
            scan = snapshot.get("scan") if isinstance(snapshot, dict) else None
            if isinstance(scan, dict):
                result["scan"] = {
                    key: scan[key]
                    for key in (
                        "status", "mode", "exact", "progress", "files_scanned",
                        "total_bytes_counted", "reason", "next_action",
                    )
                    if key in scan
                }
        except Exception:
            # A diagnostics refresh must remain useful even if the optional
            # storage manager snapshot is unavailable.
            result["scan"] = {"status": "unavailable", "mode": "fast", "exact": False, "progress": 0, "files_scanned": 0, "total_bytes_counted": 0, "reason": "Chưa đọc được snapshot storage hiện tại.", "next_action": "Mở Models & Storage và thử Quét lại nếu cần."}
        return result

    def gpu_detection(self) -> dict[str, Any]:
        """Read-only GPU detection via nvidia-smi query (no model load/inference)."""
        try:
            import subprocess
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                gpus = [line.strip() for line in result.stdout.strip().splitlines() if line.strip()]
                return {
                    **_status(HEALTHY, f"{len(gpus)} GPU(s) detected (metadata-only nvidia-smi query; no model load/inference).", "No action required."),
                    "gpus": gpus,
                }
            return _status(UNAVAILABLE, "nvidia-smi returned non-zero exit (metadata-only nvidia-smi query; no model load/inference).", "Check NVIDIA driver installation.")
        except FileNotFoundError:
            return _status(UNKNOWN, "nvidia-smi not found (metadata-only nvidia-smi query; no model load/inference).", "Install NVIDIA drivers or use CPU-only mode.")
        except Exception as exc:
            return _status(UNKNOWN, f"GPU detection error: {type(exc).__name__} (metadata-only nvidia-smi query; no model load/inference).", "Run nvidia-smi manually to diagnose.")

    def latest_app_errors(self, *, max_lines: int = _MAX_LOG_LINES) -> dict[str, Any]:
        """Tail sanitised error lines from Logs/."""
        if not LOG_ROOT.exists():
            return {**_status(HEALTHY, "Logs/ directory absent — no errors logged yet.", "No action required."), "lines": []}
        lines: list[str] = []
        try:
            for log_file in sorted(LOG_ROOT.glob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True)[:3]:
                try:
                    # Read only a bounded tail.  Diagnostics refresh must not
                    # synchronously buffer an ever-growing log file.
                    with log_file.open("rb") as handle:
                        handle.seek(0, os.SEEK_END)
                        handle.seek(max(0, handle.tell() - 128 * 1024), os.SEEK_SET)
                        text = handle.read(128 * 1024).decode("utf-8", errors="replace")
                    file_lines = [_sanitize_log_line(l) for l in text.splitlines() if "error" in l.lower() or "exception" in l.lower() or "critical" in l.lower()]
                    lines.extend(file_lines[-20:])
                    if len(lines) >= max_lines:
                        break
                except OSError:
                    continue
        except OSError:
            pass
        lines = lines[-max_lines:]
        return {
            **_status(NEEDS_ATTENTION if lines else HEALTHY,
                      f"{len(lines)} error line(s) found in logs." if lines else "No error lines found in logs.",
                      "Review log lines above." if lines else "No action required."),
            "lines": lines,
        }

    def recovery_forensic_state(self) -> dict[str, Any]:
        """Check for recovery/draft files indicating an abnormal previous shutdown."""
        if not CONFIG_ROOT.exists():
            return _status(HEALTHY, "Config/ absent — no recovery state.", "No action required.")
        drafts = list(CONFIG_ROOT.glob("draft_*.json")) + list(CONFIG_ROOT.glob("node_studio_draft_*.json"))
        tmps = list(CONFIG_ROOT.glob(".*.tmp"))
        items = [f.name for f in drafts + tmps]
        if items:
            return {
                **_status(NEEDS_ATTENTION, f"{len(items)} recovery/draft/tmp file(s) found.", "Review and clear drafts via Node Studio or Settings."),
                "files": items,
            }
        return {**_status(HEALTHY, "No recovery or draft files found.", "No action required."), "files": []}

    # ------------------------------------------------------------------
    # Full snapshot
    # ------------------------------------------------------------------

    def _raw_snapshot(self) -> dict[str, Any]:
        """Collect internal diagnostics; callers must use ``snapshot`` for public data."""
        with self._lock:
            checks = {
                "git_integrity": self.git_integrity_state(),
                "config_registry": self.config_registry_state(),
                "jobs_store": self.jobs_store_state(),
                "artifact_store": self.artifact_store_state(),
                "workflow_store": self.workflow_store_state(),
                "models_inventory": self.models_inventory(),
                "environments_inventory": self.environments_inventory(),
                "runtime_inventory": self.runtime_inventory(),
                "storage": self.storage_state(),
                "gpu": self.gpu_detection(),
                "latest_app_errors": self.latest_app_errors(),
                "recovery_forensic": self.recovery_forensic_state(),
            }
            return {name: _decorate_snapshot(name, value) for name, value in checks.items()}

    def snapshot(self) -> dict[str, Any]:
        """Return the bounded, path-free public diagnostics projection."""
        return public_snapshot_projection(self._raw_snapshot())

    def export_diagnostics_bundle(self) -> dict[str, Any]:
        """Return a sanitised snapshot safe for sharing (no secrets, no raw paths)."""
        return public_export_projection(self.snapshot())


diagnostics_center = DiagnosticsCenter()
