"""Validate only repository-managed workflow package descriptors.

This CLI has no input-path or output-path option by design.  It discovers JSON
under the fixed tracked ``workflow_packages/`` root and writes a static report
to stdout; it never executes, imports, saves, or mutates a graph.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.workflow_packages import discover_managed_packages, load_managed_package, lint_workflow_package  # noqa: E402


def _report(package_id: str | None) -> dict[str, Any]:
    catalog = discover_managed_packages()
    records = list(catalog["records"])
    if package_id is not None:
        records = [item for item in records if item["id"] == package_id]
        if not records:
            return {
                "contract": "workflow-packages-validation.v1",
                "status": "unavailable",
                "reason": "No matching managed package passed static validation.",
                "action": "Use a declared managed package ID and rerun the static validator.",
                "packages": [],
                "scenarios": [],
                "errors": [{"code": "package_not_found"}],
                "execution": "not_run",
            }
    packages: list[dict[str, Any]] = []
    for record in records:
        loaded = load_managed_package(record["id"], record["version"])
        lint = lint_workflow_package(loaded) if loaded.get("found") else {"status": "invalid", "findings": [{"code": "managed_package_unavailable", "severity": "error"}]}
        packages.append(
            {
                "id": record["id"],
                "version": record["version"],
                "fingerprint": record["fingerprint"],
                "availability": record["availability"],
                "lint": {"status": lint["status"], "findings": lint["findings"]},
            }
        )
    return {
        "contract": "workflow-packages-validation.v1",
        "status": catalog["status"] if packages else "unavailable",
        "reason": "All reported package checks are static; no workflow execution was attempted.",
        "action": "Use server-owned validation output for future integration preflight.",
        "packages": packages,
        "scenarios": catalog["scenarios"],
        "errors": catalog["errors"],
        "execution": "not_run",
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Workflow Package Static Validation",
        "",
        f"Status: `{report['status']}`",
        "",
        "Execution: `not_run`",
        "",
        "## Packages",
        "",
    ]
    if report["packages"]:
        for package in report["packages"]:
            lines.append(f"- `{package['id']}` `{package['version']}` — availability `{package['availability']['status']}`, lint `{package['lint']['status']}`")
    else:
        lines.append("- No managed package passed static validation.")
    lines.extend(["", "## Human evaluation scenarios", ""])
    if report["scenarios"]:
        for scenario in report["scenarios"]:
            lines.append(f"- `{scenario['id']}` — execution `{scenario['execution']}`")
    else:
        lines.append("- None discovered.")
    if report["errors"]:
        lines.extend(["", "## Static error codes", ""])
        for error in report["errors"]:
            lines.append(f"- `{error['code']}`")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate only repository-managed static workflow packages.")
    parser.add_argument("--format", choices=("json", "markdown"), default="json", help="Write a report to stdout.")
    parser.add_argument("--package-id", help="Optional opaque managed package ID; filesystem paths are not accepted.")
    args = parser.parse_args(argv)
    report = _report(args.package_id)
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(_markdown(report), end="")
    return 0 if report["status"] in {"partial", "planned"} and not report["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
