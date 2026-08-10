"""Validate fixed-root Creative Recipe Intelligence catalogs without execution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.creative_recipe_intelligence.catalog import discover_managed_recipe_catalogs, load_managed_recipe_catalog


def _json_report(catalog_id: str | None = None, version: str | None = None) -> dict[str, Any]:
    if catalog_id is None:
        discovery = discover_managed_recipe_catalogs()
        return {"contract": "creative-recipe-intelligence-static-validation.v1", "status": discovery["status"], "catalogs": discovery["catalogs"], "recipes": discovery["recipes"], "style_packs": discovery["style_packs"], "targets": discovery["targets"], "errors": discovery["errors"], "execution": "not_run"}
    loaded = load_managed_recipe_catalog(catalog_id, version)
    if not loaded.get("found"):
        return {"contract": "creative-recipe-intelligence-static-validation.v1", "status": "unavailable", "catalogs": [], "recipes": [], "style_packs": [], "targets": [], "errors": loaded.get("errors", [{"code": "managed_catalog_not_found"}]), "execution": "not_run"}
    catalog = loaded["catalog"]
    assert isinstance(catalog, dict)
    return {"contract": "creative-recipe-intelligence-static-validation.v1", "status": loaded["status"], "catalogs": [{"id": catalog["id"], "version": catalog["version"], "fingerprint": loaded["fingerprint"], "recipe_count": len(catalog["recipes"]), "style_pack_count": len(catalog["style_packs"]), "target_count": len(catalog["targets"])}], "recipes": [{"id": item["id"], "version": item["version"], "media_kind": item["media_kind"]} for item in catalog["recipes"]], "style_packs": [{"id": item["id"], "version": item["version"]} for item in catalog["style_packs"]], "targets": [{"id": item["id"], "version": item["version"], "media_kind": item["media_kind"], "status": item["status"]} for item in catalog["targets"]], "errors": [], "execution": "not_run"}


def _md_safe(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ").replace("`", "\\`").replace("<", "&lt;").replace(">", "&gt;")


def _markdown(report: dict[str, Any]) -> str:
    lines = ["# Local AI Hub creative recipe validation", "", f"- Contract: `{_md_safe(report['contract'])}`", f"- Status: `{_md_safe(report['status'])}`", "- Execution: `not_run`", "", "| Catalog | Version | Recipes | Styles | Targets | Fingerprint |", "| --- | --- | ---: | ---: | ---: | --- |"]
    for item in report.get("catalogs", []):
        lines.append(f"| `{_md_safe(item['id'])}` | `{_md_safe(item['version'])}` | {int(item.get('recipe_count', 0))} | {int(item.get('style_pack_count', 0))} | {int(item.get('target_count', 0))} | `{_md_safe(item.get('fingerprint', ''))}` |")
    if report.get("recipes"):
        lines.extend(["", "| Recipe | Version | Media |", "| --- | --- | --- |"])
        for item in report["recipes"]:
            lines.append(f"| `{_md_safe(item['id'])}` | `{_md_safe(item['version'])}` | `{_md_safe(item.get('media_kind', ''))}` |")
    if report.get("errors"):
        lines.extend(["", "| Error code |", "| --- |"])
        for error in report["errors"]:
            lines.append(f"| `{_md_safe(error.get('code', 'invalid'))}` |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate fixed-root managed creative recipe catalogs")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--catalog-id", default=None, help="Optional declared opaque catalog ID")
    parser.add_argument("--version", default=None, help="Optional SemVer catalog version")
    args = parser.parse_args(argv)
    report = _json_report(args.catalog_id, args.version)
    if args.format == "markdown":
        print(_markdown(report), end="")
    else:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
