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
    status = raw.get("status") if type(raw) is dict else UNKNOWN
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
    return {
        "status": status,
        "reason": reason,
        "next_action": action,
        "reason_code": code,
        "execution": "not_run",
        "dry_run": True,
    }


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
        return {**_public_base(raw), "present_count": present, "total_count": total}
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
    return {**_public_base(raw), "present_count": present, "total_count": min(total, _MAX_PUBLIC_COUNT)}


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
        if type(key) is not str or len(key) > 96 or _PUBLIC_UNSAFE_TEXT_RE.search(key):
            return _public_fallback()
        if type(value) is not dict:
            return _public_fallback()
        for key in ("total_bytes", "used_bytes", "free_bytes"):
            if key in value and (type(value[key]) is not int or type(value[key]) is bool or value[key] < 0 or value[key] > _MAX_PUBLIC_BYTES):
                return _public_fallback()
    return {**_public_base(raw), "drive_count": min(len(drives), 16), "low_space": raw.get("status") == NEEDS_ATTENTION}


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
            "root_verified", "inside_work_tree",
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

    def models_inventory(self) -> dict[str, Any]:
        """Scan Models/ directory for registered model families without loading."""
        if not MODEL_ROOT.exists():
            return _status(UNAVAILABLE, "Models/ directory not found.", "Model paths not initialised; review/install requires explicit user action.")
        entries: dict[str, Any] = {}
        total_bytes = 0
        for key, path in MODEL_PATHS.items():
            if path.exists():
                try:
                    size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
                    entries[key] = {"present": True, "size_bytes": size}
                    total_bytes += size
                except OSError:
                    entries[key] = {"present": True, "size_bytes": -1}
            else:
                entries[key] = {"present": False, "size_bytes": 0}
        present = sum(1 for v in entries.values() if v["present"])
        missing_keys = [k for k, v in entries.items() if not v["present"]]
        next_action = f"Missing model dependencies: {', '.join(missing_keys[:3])}; review/install requires explicit user action." if missing_keys else "No action required."
        return {
            **_status(HEALTHY if present else UNAVAILABLE, f"{present}/{len(entries)} model families present.", next_action),
            "models": entries,
            "total_size_bytes": total_bytes,
        }

    def environments_inventory(self) -> dict[str, Any]:
        """Check presence of runtime environments using canonical registry path."""
        envs_root = ROOT / "Environments"
        if not envs_root.exists():
            return _status(UNAVAILABLE, "Environments/ directory not found.", "Environment paths not found; review/install requires explicit user action.")
        envs = [d.name for d in envs_root.iterdir() if d.is_dir()] if envs_root.exists() else []
        return {
            **_status(HEALTHY if envs else NEEDS_ATTENTION, f"{len(envs)} environment(s) found.", "Review environments; install/update requires explicit user action." if not envs else "No action required."),
            "environments": envs,
        }

    def runtime_inventory(self) -> dict[str, Any]:
        """Check presence of registered runtime paths."""
        entries: dict[str, bool] = {key: path.exists() for key, path in RUNTIME_PATHS.items()}
        present = sum(entries.values())
        return {
            **_status(HEALTHY if present else UNAVAILABLE, f"{present}/{len(entries)} runtime paths present.", "Review runtime engines; install requires explicit user action." if present < len(entries) else "No action required."),
            "runtimes": entries,
        }

    # ------------------------------------------------------------------
    # System subsystems
    # ------------------------------------------------------------------

    def storage_state(self) -> dict[str, Any]:
        """Report C: and D: drive disk usage."""
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
        return {
            **_status(NEEDS_ATTENTION if low_space else HEALTHY,
                      "Low disk space detected." if low_space else "Disk space adequate.",
                      "Free disk space on affected drives." if low_space else "No action required."),
            "drives": results,
        }

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
                    text = log_file.read_text(encoding="utf-8", errors="replace")
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
            return {
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

    def snapshot(self) -> dict[str, Any]:
        """Return the bounded, path-free public diagnostics projection."""
        return public_snapshot_projection(self._raw_snapshot())

    def export_diagnostics_bundle(self) -> dict[str, Any]:
        """Return a sanitised snapshot safe for sharing (no secrets, no raw paths)."""
        return public_export_projection(self.snapshot())


diagnostics_center = DiagnosticsCenter()
