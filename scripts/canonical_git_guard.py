"""CLI for read-only canonical Git identity inspection and preflight."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


EXIT_OK = 0
EXIT_REFUSAL = 2


if __package__ in {None, ""}:
    _repo_root = Path(__file__).resolve().parents[1]
    if str(_repo_root) not in sys.path:
        sys.path.insert(0, str(_repo_root))

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
    return EXIT_OK if result.get("ok") is True else EXIT_REFUSAL


if __name__ == "__main__":
    sys.exit(main())
