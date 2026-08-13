"""Fail-closed boundary for the future manager-owned recovery broker.

This package deliberately contains no session transport, authority verifier,
machine probe, or recovery writer.  The public preflight entrypoint is a
sanitized refusal before it dereferences any caller input.  A future external
broker may own those responsibilities after a separately authorized design.
"""

from __future__ import annotations

from typing import Any


SCHEMA_VERSION = "machine-recovery-controller.v3"
EXPECTED_CANONICAL_HEAD = "ca998106fe2319da6b41fe1c73c6df834d65b2c8"
EXPECTED_CANONICAL_TREE = "0d6852a20d78701b948cedcd0f970b4d3bb5e27c8"
# de12 is the executor provenance baseline, not a controller checkout pin.
EXPECTED_EXECUTOR_PROVENANCE_HEAD = "de12ff153750756ab9e49375750ea3398293943a"
EXPECTED_MANIFEST_DIGEST = "4436315c32f76d469ca86095adb1c8fab968f285347fc0b0395e035c4e037936"
EXPECTED_GUARD_CODE = "CANONICAL_PRESERVATION_REQUIRED"
TARGETS = (
    "components.json",
    "model_registry.json",
    "hub_config.json",
    "application_registry.local.json",
)


class ControllerError(ValueError):
    """Finite internal validation error for future broker implementations."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _blocked(reason: str = "manager_broker_required") -> dict[str, Any]:
    """Return the only projection this package is allowed to publish."""

    return {
        "status": "preflight_blocked",
        "execution": "not_run",
        "dry_run": True,
        "apply_allowed": False,
        "reason": reason,
        "next_action": "manager_broker_required",
    }


def run_parent_preflight(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    """Refuse before reading arguments, callbacks, pipes, or machine state.

    The positional/keyword sink is intentional: legacy callers receive a
    stable finite refusal, while hostile objects cannot be dereferenced.  No
    caller can obtain a successful or execution-ready state here.
    """

    return _blocked()


def main() -> int:
    print("manager_recovery_controller_noop: manager broker required; execution not_run")
    return 0


__all__ = [
    "run_parent_preflight",
    "ControllerError",
    "EXPECTED_CANONICAL_HEAD",
    "EXPECTED_CANONICAL_TREE",
    "EXPECTED_EXECUTOR_PROVENANCE_HEAD",
    "EXPECTED_MANIFEST_DIGEST",
    "EXPECTED_GUARD_CODE",
    "TARGETS",
]


if __name__ == "__main__":
    raise SystemExit(main())
