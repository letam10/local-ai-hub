"""Platform Hardening V2 contract.

This module is a read-only projection of the hardening boundaries already
owned by the updater, backup and process services.  It intentionally does not
probe the machine, run a child process, read user data or mutate any state.
The real update/restore operations remain behind their existing explicit
confirmation and ownership gates.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


PLATFORM_HARDENING_V2_SCHEMA = "platform-hardening.v2"

_AREAS: tuple[dict[str, Any], ...] = (
    {
        "id": "updater_v3",
        "label": "Updater V3",
        "owner": "src/services/app_update.py + src/app/update_watchdog.py",
        "state": "PLAN_ONLY",
        "persistent_state": ["staged_update", "pending_health", "restart_session", "current_pointer"],
        "api": ["/api/app-update/status", "/api/app-update/prepare", "/api/app-update/rollback"],
        "failure_modes": ["artifact_invalid", "channel_diverged", "restart_health_timeout"],
        "recovery": "Side-by-side candidate, identity-bound health gate and rollback to the previous verified pointer.",
        "reason": "The updater contract is available for explicit preflight/prepare; activation still requires the native close and restart ownership gate.",
    },
    {
        "id": "backup_recovery",
        "label": "Backup & Recovery",
        "owner": "src/services/backup_manager.py + src/services/transaction_store.py",
        "state": "PLAN_ONLY",
        "persistent_state": ["opaque_backup_archive", "restore_plan", "transaction_snapshot"],
        "api": ["/api/backup", "/api/backup/create", "/api/backup/inspect", "/api/backup/plan", "/api/backup/apply"],
        "failure_modes": ["invalid_archive", "restore_conflict", "transaction_failed"],
        "recovery": "Inspect first, apply only an identity-bound plan, and preserve the prior snapshot on failure.",
        "reason": "Backup/restore is server-owned and confirmation-gated; this projection performs no archive or restore operation.",
    },
    {
        "id": "process_supervisor",
        "label": "Process Supervisor",
        "owner": "src/services/process_manager/managed.py + src/services/process_manager/windows.py",
        "state": "READ_ONLY",
        "persistent_state": ["owned_process_handles", "worker_logs"],
        "api": ["/api/jobs", "/api/lifecycle", "/api/comfyui/advanced"],
        "failure_modes": ["owned_process_exit", "cancel_timeout", "loopback_owner_lost"],
        "recovery": "Stop only a process handle created by Hub and reconcile its owned job; never terminate a generic same-name process.",
        "reason": "Process ownership is explicit in the service boundary; no process enumeration or termination runs from this snapshot.",
    },
    {
        "id": "security",
        "label": "Security boundaries",
        "owner": "architecture/dependency_rules.yaml + src/services/api",
        "state": "READ_ONLY",
        "persistent_state": ["sanitized_diagnostics", "opaque_artifact_ids"],
        "api": ["/health", "/api/diagnostics/snapshot", "/api/features/v2"],
        "failure_modes": ["invalid_input", "path_or_secret_rejected", "integrity_mismatch"],
        "recovery": "Fail closed, keep values opaque, and route recovery through sanitized Diagnostics rather than exposing internals.",
        "reason": "API, path and secret boundaries are documented and validated by source/contract tests; no security setting is changed.",
    },
    {
        "id": "performance",
        "label": "Performance & responsiveness",
        "owner": "src/app + src/services/api + src/ui",
        "state": "READ_ONLY",
        "persistent_state": ["bounded_snapshot_cache", "lightweight_poll_state"],
        "api": ["/api/bootstrap", "/api/storage/scan", "/api/product-experience/v2"],
        "failure_modes": ["slow_snapshot", "polling_timeout", "stale_cache"],
        "recovery": "Keep startup snapshots bounded, poll long work without replacing the page, and show stale/partial state explicitly.",
        "reason": "Responsiveness uses bounded snapshots and background polling contracts; no benchmark or long workload is run by this projection.",
    },
)


def snapshot() -> dict[str, Any]:
    """Return the finite hardening contract without probing or changing state."""

    return {
        "schema_version": PLATFORM_HARDENING_V2_SCHEMA,
        "status": "completed",
        "areas": deepcopy(list(_AREAS)),
        "execution": "not_run",
        "dry_run": True,
        "overall_state": "READ_ONLY_CONTRACT",
        "reason": "Platform Hardening V2 describes existing updater, backup, process, security and performance boundaries; it does not execute an update, restore, process action, benchmark or machine probe.",
        "next_action": "Use the owning area API and its explicit confirmation/ownership gate for any future operation.",
    }


def detail(area_id: object) -> dict[str, Any] | None:
    if not isinstance(area_id, str):
        return None
    item = next((entry for entry in _AREAS if entry["id"] == area_id), None)
    if item is None:
        return None
    return {
        "schema_version": PLATFORM_HARDENING_V2_SCHEMA,
        "status": "completed",
        "area": deepcopy(item),
        "execution": "not_run",
        "dry_run": True,
        "reason": item["reason"],
        "next_action": item["recovery"],
    }


__all__ = ["PLATFORM_HARDENING_V2_SCHEMA", "detail", "snapshot"]
