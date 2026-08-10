"""Validate repository-managed privacy policies without probing the machine."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.privacy_diagnostics.policy_catalog import discover_managed_privacy_policies, load_managed_privacy_policy


def _json_report(policy_id: str | None = None) -> dict[str, Any]:
    if policy_id is None:
        discovery = discover_managed_privacy_policies()
        return {
            "contract": "privacy-diagnostics-static-validation.v1",
            "status": discovery["status"],
            "policies": discovery["policies"],
            "errors": discovery["errors"],
            "execution": "not_run",
        }
    loaded = load_managed_privacy_policy(policy_id)
    if not loaded.get("found"):
        return {
            "contract": "privacy-diagnostics-static-validation.v1",
            "status": "unavailable",
            "policies": [],
            "errors": [{"code": "managed_policy_not_found"}],
            "execution": "not_run",
        }
    policy = loaded["policy"]
    assert isinstance(policy, dict)
    return {
        "contract": "privacy-diagnostics-static-validation.v1",
        "status": loaded["status"],
        "policies": [{"id": policy["id"], "fingerprint": loaded["fingerprint"], "revision": policy["revision"], "redaction_profile": policy["redaction_profile"]}],
        "errors": [],
        "execution": "not_run",
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Local AI Hub privacy diagnostics validation",
        "",
        f"- Contract: `{report['contract']}`",
        f"- Status: `{report['status']}`",
        "- Execution: `not_run`",
        "",
        "| Policy | Revision | Redaction | Fingerprint |",
        "| --- | ---: | --- | --- |",
    ]
    for policy in report["policies"]:
        lines.append(f"| `{policy['id']}` | {int(policy['revision'])} | `{policy['redaction_profile']}` | `{policy['fingerprint']}` |")
    if report["errors"]:
        lines.extend(["", "| Error code |", "| --- |"])
        for error in report["errors"]:
            lines.append(f"| `{error['code']}` |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate fixed-root managed privacy policies")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--policy-id", default=None, help="Optional declared opaque policy ID")
    args = parser.parse_args(argv)
    report = _json_report(args.policy_id)
    if args.format == "markdown":
        print(_markdown(report), end="")
    else:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
