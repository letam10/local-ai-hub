"""Bounded manager-parent protocol entrypoint.

Direct invocation is intentionally an inspect/plan-only no-op. It accepts no
authority, token, path, mode, command, or client payload.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    print("manager_recovery_controller_noop: manager broker required; execution not_run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
