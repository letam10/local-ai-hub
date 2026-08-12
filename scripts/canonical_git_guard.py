"""CLI for read-only canonical Git identity inspection and preflight."""

from __future__ import annotations

import argparse
import json
import sys

from src.shared.canonical_git_integrity import inspect_canonical, preflight_canonical


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect protected LocalAIHub Git integrity without mutation.")
    parser.add_argument("operation", choices=("inspect", "preflight"))
    args = parser.parse_args(argv)
    try:
        result = inspect_canonical() if args.operation == "inspect" else preflight_canonical()
    except Exception:
        result = {
            "schema_version": "canonical-git-integrity.v1",
            "canonical_designation": "localaihub-canonical",
            "operation_code": "CANONICAL_GIT_QUERY_FAILED",
            "ok": False,
            "dirty": False,
        }
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0 if result.get("ok") is True else 2


if __name__ == "__main__":
    sys.exit(main())
