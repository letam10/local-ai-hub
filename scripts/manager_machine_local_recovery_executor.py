"""Manager-only inspect/plan/preflight entrypoint.

The production command intentionally has no apply mode.  A separately managed
controller may call ``inspect_plan_preflight`` with already-attested metadata;
this script never accepts client rows, paths, commands, or recovery payloads.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from src.shared.machine_local_recovery_executor import (
    ExecutionAuthorization,
    inspect,
    issue_authorization,
    plan,
    preflight,
)


def inspect_plan_preflight(
    *,
    canonical_root: Path,
    config_root: Path,
    task_root: Path,
    preservation_manifest: Sequence[Mapping[str, Any]],
    consumer_hashes: Mapping[str, Mapping[str, Any]],
    executor_head: str,
    executor_tree: str,
    executor_script_sha256: str,
    guard_code: str,
    guard_dirty: bool,
    active_hub: bool,
    owned_processes: int | None,
    lock_held: bool,
    free_bytes: int | None,
    git_runner: Callable[..., str] | None = None,
    manager_issuer: object | None = None,
) -> dict[str, Any]:
    """Run the read-only inspect -> plan -> preflight sequence.

    The supplied roots and metadata belong to the manager controller.  This
    function only reads them through the fixed allowlists in the executor and
    returns a finite sanitized result; it never performs Config recovery.
    """

    snapshot = inspect(
        canonical_root=canonical_root,
        config_root=config_root,
        preservation_manifest=preservation_manifest,
        consumer_hashes=consumer_hashes,
        git_runner=git_runner,
    )
    planned = plan(
        snapshot,
        executor_head=executor_head,
        executor_tree=executor_tree,
        executor_script_sha256=executor_script_sha256,
    )
    if planned.get("status") != "planned":
        return {"snapshot": snapshot, "plan": planned, "preflight": {"status": "preflight_blocked", "execution": "not_run", "error": "plan_not_ready"}}
    if manager_issuer is None:
        return {"snapshot": snapshot, "plan": planned, "preflight": {"status": "preflight_blocked", "execution": "not_run", "error": "manager_issuer_required"}}
    try:
        capability: ExecutionAuthorization = issue_authorization(planned, manager_issuer=manager_issuer)
    except Exception:
        return {"snapshot": snapshot, "plan": planned, "preflight": {"status": "preflight_blocked", "execution": "not_run", "error": "authorization_refused"}}
    result = preflight(
        planned,
        capability,
        task_root=task_root,
        guard_code=guard_code,
        guard_dirty=guard_dirty,
        active_hub=active_hub,
        owned_processes=owned_processes,
        lock_held=lock_held,
        free_bytes=free_bytes,
    )
    return {"snapshot": snapshot, "plan": planned, "preflight": result}


def main() -> int:
    """Refuse an unbound invocation without touching machine state."""

    print("inspect_plan_preflight_only: manager authorization and attested inputs required; recovery not_run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
