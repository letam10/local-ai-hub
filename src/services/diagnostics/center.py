"""
  FILE NOTE
  - Mục đích: DiagnosticsCenter - hệ thống diagnostics read-only-first cho Local AI Hub V6, kiểm tra 12 subsystem không inference GPU/model
  - Liên kết trực tiếp: src/shared/paths/registry.py, src/app_config/settings_service.py, src/services/artifact_store.py, src/services/project_manager/manager.py, src/services/workflow_library/library.py, src/services/backup_manager.py
  - Vùng ảnh hưởng khi sửa: Trang diagnostics (Git integrity, Config, Jobs, Artifacts, Workflow, Models, Envs, Runtime, Storage, GPU, Errors, Recovery); chỉ đọc
"""

from __future__ import annotations

import json
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

PUBLIC_SUBSYSTEM_KEYS = (
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
_PUBLIC_STATUS_VALUES = frozenset({HEALTHY, NEEDS_ATTENTION, UNAVAILABLE, UNKNOWN, "MANUAL_REVIEW"})
_PUBLIC_MAX_COUNT = 100_000
_PUBLIC_MAX_BYTES = 1 << 50
_PUBLIC_MAX_TEXT = 4_096
_PUBLIC_CONFIG_KEYS = frozenset({"settings.json", "creative_workspace.json", "workflow_library.json"})
_PUBLIC_SCHEMA_VALUES = frozenset({"absent", "present", "unknown", "corrupt", "invalid"})
_PUBLIC_STATUS_COPY = {
    HEALTHY: ("Diagnostic subsystem is healthy.", "No action required."),
    NEEDS_ATTENTION: ("Diagnostic subsystem needs attention.", "Review the bounded diagnostic summary."),
    UNAVAILABLE: ("Diagnostic subsystem is unavailable.", "Restore the managed source or review it manually."),
    UNKNOWN: ("Diagnostic subsystem state is unknown.", "Review the diagnostic source manually."),
    "MANUAL_REVIEW": ("Diagnostic subsystem requires manual review.", "Review the diagnostic source manually."),
}
_PUBLIC_ROW_FIELDS = frozenset({
    "status", "reason", "next_action", "execution", "dry_run", "code",
    "root_verified", "inside_work_tree", "record_count", "status_bucket_count",
    "artifact_count", "workflow_count", "total_size_bytes", "environment_count",
    "drive_count", "gpu_count", "error_count", "has_errors", "file_count",
    "has_recovery_files", "category_flags",
})
_PUBLIC_COUNT_FIELDS = frozenset({
    "record_count", "status_bucket_count", "artifact_count", "workflow_count",
    "environment_count", "drive_count", "gpu_count", "error_count", "file_count",
})
_PUBLIC_BOOL_FIELDS = frozenset({"root_verified", "inside_work_tree", "has_errors", "has_recovery_files"})

# Maximum number of error log lines to tail
_MAX_LOG_LINES = 50
# Secret key pattern for sanitization
_SECRET_KEY_RE = re.compile(
    r"api[_-]?key|token|password|secret|credential|private[_-]?key|access[_-]?key",
    re.IGNORECASE,
)


def _status(status: str, reason: str, next_action: str) -> dict[str, str]:
    return {"status": status, "reason": reason, "next_action": next_action}


def _sanitize_log_line(line: str) -> str:
    """Scrub likely secret values from a log line."""
    return re.sub(r"(?i)(api[_-]?key|token|password|secret)\s*[=:]\s*\S+", r"\1=[REDACTED]", line)


def _public_base(status: str, *, code: str | None = None) -> dict[str, Any]:
    reason, next_action = _PUBLIC_STATUS_COPY[status]
    result: dict[str, Any] = {
        "status": status,
        "reason": reason,
        "next_action": next_action,
        "execution": "not_run",
        "dry_run": True,
    }
    if code is not None:
        result["code"] = code
    return result


def _public_failure() -> dict[str, Any]:
    return _public_base(UNKNOWN, code="diagnostic_projection_unavailable")


def _bounded_int(value: object) -> int:
    if type(value) is not int or isinstance(value, bool) or value < 0 or value > _PUBLIC_MAX_COUNT:
        raise ValueError("bounded diagnostic count unavailable")
    return value


def _optional_int(source: dict[str, Any], key: str) -> int | None:
    if key not in source:
        return None
    return _bounded_int(source[key])


def _optional_bytes(source: dict[str, Any], key: str) -> int | None:
    if key not in source:
        return None
    value = source[key]
    if type(value) is not int or isinstance(value, bool) or value < 0 or value > _PUBLIC_MAX_BYTES:
        raise ValueError("diagnostic byte count unavailable")
    return value


def _optional_bool(source: dict[str, Any], key: str) -> bool | None:
    if key not in source:
        return None
    value = source[key]
    if type(value) is not bool:
        raise ValueError("diagnostic boolean unavailable")
    return value


def _bounded_scalar(value: object) -> bool:
    if type(value) is str:
        return len(value) <= _PUBLIC_MAX_TEXT
    if type(value) is bool:
        return True
    if type(value) is int:
        return not isinstance(value, bool) and 0 <= value <= _PUBLIC_MAX_COUNT
    return value is None


def _optional_mapping_count(source: dict[str, Any], key: str, *, value_kind: str = "scalar") -> int | None:
    if key not in source:
        return None
    value = source[key]
    if type(value) is not dict or len(value) > _PUBLIC_MAX_COUNT:
        raise ValueError("diagnostic mapping unavailable")
    for item_key, item_value in value.items():
        if type(item_key) is not str or len(item_key) > _PUBLIC_MAX_TEXT:
            raise ValueError("diagnostic mapping key unavailable")
        if value_kind == "scalar" and not _bounded_scalar(item_value):
            raise ValueError("diagnostic mapping value unavailable")
        if value_kind == "count":
            _bounded_int(item_value)
        if value_kind == "bool" and type(item_value) is not bool:
            raise ValueError("diagnostic boolean mapping unavailable")
        if value_kind == "record" and type(item_value) is not dict:
            raise ValueError("diagnostic record mapping unavailable")
        if value_kind == "schema" and (item_key not in _PUBLIC_CONFIG_KEYS or type(item_value) is not str or item_value not in _PUBLIC_SCHEMA_VALUES):
            raise ValueError("diagnostic schema mapping unavailable")
    return len(value)


def _optional_text_list_count(source: dict[str, Any], key: str) -> int | None:
    if key not in source:
        return None
    value = source[key]
    if type(value) is not list or len(value) > _PUBLIC_MAX_COUNT:
        raise ValueError("diagnostic list unavailable")
    if any(type(item) is not str or len(item) > _PUBLIC_MAX_TEXT for item in value):
        raise ValueError("diagnostic text unavailable")
    return len(value)


def _recovery_categories(source: dict[str, Any]) -> tuple[int | None, dict[str, bool] | None]:
    if "files" not in source:
        return None, None
    value = source["files"]
    if type(value) is not list or len(value) > _PUBLIC_MAX_COUNT:
        raise ValueError("recovery list unavailable")
    categories = {"node_studio": False, "project": False, "temporary": False}
    for item in value:
        if type(item) is not str or len(item) > _PUBLIC_MAX_TEXT:
            raise ValueError("recovery filename unavailable")
        if item.startswith("node_studio_draft_") and item.endswith(".json"):
            categories["node_studio"] = True
        elif item.startswith("draft_") and item.endswith(".json"):
            categories["project"] = True
        elif item.startswith(".") and item.endswith(".tmp"):
            categories["temporary"] = True
        else:
            raise ValueError("unknown recovery category")
    return len(value), categories


def _public_subsystem(key: str, raw: object) -> dict[str, Any]:
    """Project one raw collector result to a bounded public diagnostic row."""

    if type(raw) is not dict:
        return _public_failure()
    status = raw.get("status")
    if type(status) is not str or status not in _PUBLIC_STATUS_VALUES:
        return _public_failure()
    result = _public_base(status)
    try:
        if key == "git_integrity":
            for field in ("root_verified", "inside_work_tree"):
                value = _optional_bool(raw, field)
                if value is not None:
                    result[field] = value
        elif key == "config_registry":
            value = _optional_mapping_count(raw, "schema_versions", value_kind="schema")
            if value is not None:
                result["record_count"] = value
        elif key == "jobs_store":
            value = _optional_mapping_count(raw, "counts", value_kind="count")
            if value is not None:
                result["status_bucket_count"] = value
        elif key == "artifact_store":
            value = _optional_int(raw, "artifact_count")
            if value is not None:
                result["artifact_count"] = value
        elif key == "workflow_store":
            value = _optional_int(raw, "workflow_count")
            if value is not None:
                result["workflow_count"] = value
        elif key == "models_inventory":
            value = _optional_mapping_count(raw, "models", value_kind="record")
            if value is not None:
                result["record_count"] = value
            value = _optional_bytes(raw, "total_size_bytes")
            if value is not None:
                result["total_size_bytes"] = value
        elif key == "environments_inventory":
            value = _optional_text_list_count(raw, "environments")
            if value is not None:
                result["environment_count"] = value
        elif key == "runtime_inventory":
            value = _optional_mapping_count(raw, "runtimes", value_kind="bool")
            if value is not None:
                result["record_count"] = value
        elif key == "storage":
            value = _optional_mapping_count(raw, "drives", value_kind="record")
            if value is not None:
                result["drive_count"] = value
        elif key == "gpu":
            value = _optional_text_list_count(raw, "gpus")
            if value is not None:
                result["gpu_count"] = value
        elif key == "latest_app_errors":
            value = _optional_text_list_count(raw, "lines")
            if value is not None:
                result["error_count"] = value
                result["has_errors"] = value > 0
        elif key == "recovery_forensic":
            count, categories = _recovery_categories(raw)
            if count is not None and categories is not None:
                result["file_count"] = count
                result["has_recovery_files"] = count > 0
                result["category_flags"] = categories
    except (TypeError, ValueError, RecursionError):
        return _public_failure()
    return result


def is_public_snapshot(value: object) -> bool:
    """Validate the already-projected snapshot before a route selects rows."""

    if type(value) is not dict or set(value) != set(PUBLIC_SUBSYSTEM_KEYS):
        return False
    for row in value.values():
        if type(row) is not dict or not set(row).issubset(_PUBLIC_ROW_FIELDS):
            return False
        status = row.get("status")
        if type(status) is not str or status not in _PUBLIC_STATUS_VALUES:
            return False
        if row.get("reason") != _PUBLIC_STATUS_COPY[status][0] or row.get("next_action") != _PUBLIC_STATUS_COPY[status][1]:
            return False
        if row.get("execution") != "not_run" or row.get("dry_run") is not True:
            return False
        if "code" in row and row["code"] != "diagnostic_projection_unavailable":
            return False
        for key in _PUBLIC_COUNT_FIELDS:
            if key in row:
                try:
                    _bounded_int(row[key])
                except (TypeError, ValueError):
                    return False
        if "total_size_bytes" in row:
            value = row["total_size_bytes"]
            if type(value) is not int or isinstance(value, bool) or value < 0 or value > _PUBLIC_MAX_BYTES:
                return False
        for key in _PUBLIC_BOOL_FIELDS:
            if key in row and type(row[key]) is not bool:
                return False
        if "category_flags" in row:
            flags = row["category_flags"]
            if type(flags) is not dict or set(flags) != {"node_studio", "project", "temporary"} or any(type(item) is not bool for item in flags.values()):
                return False
    return True


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
                if type(data) is not dict:
                    schema_versions[name] = "invalid"
                    issues.append("config_record_invalid")
                    continue
                version = data.get("schema_version")
                if "schema_version" not in data:
                    schema_versions[name] = "unknown"
                elif (type(version) is int and not isinstance(version, bool) and 0 <= version <= _PUBLIC_MAX_COUNT) or (type(version) is str and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", version)):
                    schema_versions[name] = "present"
                else:
                    schema_versions[name] = "invalid"
                    issues.append("config_schema_invalid")
                recovery_required = data.get("recovery_required")
                if recovery_required is not None and type(recovery_required) is not bool:
                    issues.append("config_recovery_flag_invalid")
                if recovery_required is True or data.get("status") == "recovery_required":
                    issues.append("config_recovery_required")
            except (OSError, json.JSONDecodeError, TypeError, ValueError, RecursionError):
                issues.append("config_record_unavailable")
                schema_versions[name] = "corrupt"

        if issues:
            return {
                **_status(NEEDS_ATTENTION, "Config registry requires manual review.", "Review the bounded configuration summary."),
                "schema_versions": schema_versions,
            }
        return {
            **_status(HEALTHY, "Config registry is readable.", "No action required."),
            "schema_versions": schema_versions,
        }

    def jobs_store_state(self) -> dict[str, Any]:
        """Report jobs store health (count by status)."""
        try:
            from src.services.job_manager import manager as jm
            jobs = jm.job_manager.list_jobs(statuses=None, limit=1000)
            counts: dict[str, int] = {}
            for job in (jobs.get("jobs") or []):
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
    # Bounded public snapshot
    # ------------------------------------------------------------------

    def _raw_snapshot(self) -> dict[str, Any]:
        """Collect server-owned diagnostics before the public projection."""

        collectors = {
            "git_integrity": self.git_integrity_state,
            "config_registry": self.config_registry_state,
            "jobs_store": self.jobs_store_state,
            "artifact_store": self.artifact_store_state,
            "workflow_store": self.workflow_store_state,
            "models_inventory": self.models_inventory,
            "environments_inventory": self.environments_inventory,
            "runtime_inventory": self.runtime_inventory,
            "storage": self.storage_state,
            "gpu": self.gpu_detection,
            "latest_app_errors": self.latest_app_errors,
            "recovery_forensic": self.recovery_forensic_state,
        }
        raw: dict[str, Any] = {}
        for key in PUBLIC_SUBSYSTEM_KEYS:
            collector = collectors[key]
            try:
                raw[key] = collector()
            except Exception:
                raw[key] = None
        return raw

    def snapshot(self) -> dict[str, Any]:
        """Return only the bounded public diagnostic projection."""

        with self._lock:
            raw = self._raw_snapshot()
        return {key: _public_subsystem(key, raw.get(key)) for key in PUBLIC_SUBSYSTEM_KEYS}

    def export_diagnostics_bundle(self) -> dict[str, Any]:
        """Return the same bounded projection used by all public diagnostics routes."""

        return {"bundle": self.snapshot(), "sanitized": True}


diagnostics_center = DiagnosticsCenter()
