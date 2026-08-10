"""Validate only repository-managed asset intelligence descriptors.

The CLI intentionally has no arbitrary input/output path.  It reads the fixed
tracked ``asset_catalog/`` root, writes static JSON or Markdown to stdout, and
never opens an asset, invokes a provider, or changes retention state.
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

from src.services.asset_intelligence import (  # noqa: E402
    build_asset_qa_report,
    build_exact_duplicate_groups,
    discover_managed_asset_catalogs,
    load_managed_asset_catalog,
)


def _report(catalog_id: str | None) -> dict[str, Any]:
    discovery = discover_managed_asset_catalogs()
    catalog_records = list(discovery["catalogs"])
    if catalog_id is not None:
        catalog_records = [record for record in catalog_records if record["id"] == catalog_id]
        if not catalog_records:
            return {
                "contract": "asset-intelligence-static-validation.v1",
                "status": "unavailable",
                "reason": "No matching managed asset catalog passed static validation.",
                "action": "Use a declared managed catalog ID and rerun the static validator.",
                "catalogs": [],
                "lineages": [],
                "collections": [],
                "errors": [{"code": "catalog_not_found"}],
                "execution": "not_run",
            }
    catalogs: list[dict[str, Any]] = []
    for record in catalog_records:
        loaded = load_managed_asset_catalog(record["id"])
        if not loaded.get("found"):
            catalogs.append({"id": record["id"], "status": "unavailable", "errors": [{"code": "managed_catalog_unavailable"}]})
            continue
        qa = build_asset_qa_report(loaded)
        duplicates = build_exact_duplicate_groups(loaded)
        catalogs.append(
            {
                "id": record["id"],
                "fingerprint": record["fingerprint"],
                "availability": record["availability"],
                "qa": {"status": qa["status"], "counts": qa["counts"]},
                "exact_duplicates": {"status": duplicates["status"], "group_count": len(duplicates["groups"])},
            }
        )
    return {
        "contract": "asset-intelligence-static-validation.v1",
        "status": discovery["status"] if catalogs else "unavailable",
        "reason": "All catalog, lineage, collection, duplicate, and provider checks are static; no runtime work was attempted.",
        "action": "Use only server-owned CLI output for future integration planning.",
        "catalogs": catalogs,
        "lineages": discovery["lineages"],
        "collections": discovery["collections"],
        "errors": discovery["errors"],
        "execution": "not_run",
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Asset Intelligence Static Validation",
        "",
        f"Status: `{report['status']}`",
        "",
        "Execution: `not_run`",
        "",
        "## Catalogs",
        "",
    ]
    if report["catalogs"]:
        for catalog in report["catalogs"]:
            if "qa" in catalog:
                lines.append(f"- `{catalog['id']}` - availability `{catalog['availability']['status']}`, assets {catalog['qa']['counts']['assets']}, exact groups {catalog['exact_duplicates']['group_count']}")
            else:
                lines.append(f"- `{catalog['id']}` - unavailable")
    else:
        lines.append("- No managed asset catalog passed static validation.")
    lines.extend(["", "## Provenance lineages", ""])
    if report["lineages"]:
        for lineage in report["lineages"]:
            lines.append(f"- `{lineage['id']}` - execution `{lineage['execution']}`")
    else:
        lines.append("- None discovered.")
    lines.extend(["", "## Smart collections", ""])
    if report["collections"]:
        for collection in report["collections"]:
            lines.append(f"- `{collection['id']}` - execution `{collection['execution']}`")
    else:
        lines.append("- None discovered.")
    if report["errors"]:
        lines.extend(["", "## Static error codes", ""])
        for error in report["errors"]:
            lines.append(f"- `{error['code']}`")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate repository-managed static asset intelligence descriptors.")
    parser.add_argument("--format", choices=("json", "markdown"), default="json", help="Write a static report to stdout.")
    parser.add_argument("--catalog-id", help="Optional opaque managed catalog ID; filesystem paths are not accepted.")
    args = parser.parse_args(argv)
    report = _report(args.catalog_id)
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(_markdown(report), end="")
    return 0 if report["status"] == "partial" and not report["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
