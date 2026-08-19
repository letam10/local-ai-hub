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
            return _status(UNKNOWN, "Not a git repository or release build.", "No action required.")
        try:
            head_file = git_dir / "HEAD"
            if head_file.exists():
                ref = head_file.read_text(encoding="utf-8").strip()
                return {
                    **_status(HEALTHY, f"Git reference: {ref[:60]}.", "No action required."),
                    "ref": ref,
                }
            return _status(UNKNOWN, "Git HEAD absent.", "No action required.")
        except Exception as exc:
            return _status(UNKNOWN, f"Git inspection error: {exc}", "No action required.")

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
    # Full snapshot
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Return a full diagnostics snapshot across all subsystems."""
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

    def export_diagnostics_bundle(self) -> dict[str, Any]:
        """Return a sanitised snapshot safe for sharing (no secrets, no raw paths)."""
        raw = self.snapshot()
        # Sanitize: replace any value matching secret pattern
        def _clean(obj: Any) -> Any:
            if isinstance(obj, dict):
                return {k: ("[REDACTED]" if _SECRET_KEY_RE.search(k) else _clean(v)) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_clean(item) for item in obj]
            if isinstance(obj, str) and re.search(r"[A-Za-z]:[\\\\/]", obj):
                # Redact full local paths but keep drive letter
                return re.sub(r"([A-Za-z]:)[^\s,;\"']+", r"\1[PATH]", obj)
            return obj
        return {"bundle": _clean(raw), "sanitized": True}


diagnostics_center = DiagnosticsCenter()
