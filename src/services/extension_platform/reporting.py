"""JSON and Markdown compatibility reports for static extension findings."""

from __future__ import annotations

import json
from typing import Any, Mapping


COMPATIBILITY_REPORT_VERSION = "extension-compatibility-report.v1"
_STATUS_RANK = {"operational": 0, "planned": 1, "partial": 2, "unavailable": 3}


def _combine(current: str, candidate: str) -> str:
    return candidate if _STATUS_RANK.get(candidate, 3) > _STATUS_RANK.get(current, 3) else current


def _preflight_index(preflight: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    if not isinstance(preflight, Mapping) or not isinstance(preflight.get("extensions"), list):
        return {}
    return {
        item["extension_id"]: item
        for item in preflight["extensions"]
        if isinstance(item, Mapping) and isinstance(item.get("extension_id"), str)
    }


def build_compatibility_report(
    discovery: Mapping[str, Any],
    *,
    preflight: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a public report without machine-local descriptor paths or secrets."""

    records = discovery.get("extensions", []) if isinstance(discovery.get("extensions"), list) else []
    preflights = _preflight_index(preflight)
    extensions: list[dict[str, Any]] = []
    status = "operational"
    for record in records:
        if not isinstance(record, Mapping):
            continue
        extension_id = record.get("extension_id")
        assessed = preflights.get(extension_id) if isinstance(extension_id, str) else None
        item_status = assessed.get("status") if isinstance(assessed, Mapping) else record.get("status", "unavailable")
        item_reason = assessed.get("reason") if isinstance(assessed, Mapping) else record.get("reason", "No discovery reason was supplied.")
        item_action = assessed.get("action") if isinstance(assessed, Mapping) else record.get("action", "Run static validation.")
        if item_status not in _STATUS_RANK:
            item_status = "unavailable"
        status = _combine(status, item_status)
        extensions.append(
            {
                "extension_id": extension_id,
                "display_name": record.get("display_name", "Unidentified extension"),
                "status": item_status,
                "reason": item_reason,
                "action": item_action,
                "capabilities": list(record.get("capabilities", [])),
                "required_components": [
                    {key: item[key] for key in ("id", "version", "optional") if key in item}
                    for item in record.get("required_components", [])
                    if isinstance(item, Mapping)
                ],
                "required_models": [
                    {key: item[key] for key in ("id", "version", "optional") if key in item}
                    for item in record.get("required_models", [])
                    if isinstance(item, Mapping)
                ],
                "resource_profile": record.get("resource_profile"),
                "card_counts": {
                    "model_cards": len(record.get("descriptors", {}).get("model_cards", [])) if isinstance(record.get("descriptors"), Mapping) else 0,
                    "runtime_cards": len(record.get("descriptors", {}).get("runtime_cards", [])) if isinstance(record.get("descriptors"), Mapping) else 0,
                },
            }
        )
    resource_plan = preflight.get("resource_plan") if isinstance(preflight, Mapping) else None
    if isinstance(resource_plan, Mapping) and resource_plan.get("status") in _STATUS_RANK:
        status = _combine(status, resource_plan["status"])
    counts = {item: sum(1 for extension in extensions if extension["status"] == item) for item in ("operational", "partial", "unavailable", "planned")}
    return {
        "report_version": COMPATIBILITY_REPORT_VERSION,
        "status": status,
        "discovery_contract": discovery.get("contract_version") if isinstance(discovery, Mapping) else None,
        "preflight_contract": preflight.get("contract_version") if isinstance(preflight, Mapping) else None,
        "counts": counts,
        "extensions": extensions,
        "resource_plan": resource_plan,
        "issues": [
            {key: issue[key] for key in ("extension_id", "code", "message") if key in issue}
            for issue in discovery.get("issues", [])
            if isinstance(issue, Mapping)
        ],
    }


def compatibility_report_json(report: Mapping[str, Any]) -> str:
    """Serialize a report with stable, UTF-8-safe formatting for CLI consumers."""

    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _escape_markdown(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def render_compatibility_markdown(report: Mapping[str, Any]) -> str:
    """Render a concise Markdown companion to the JSON compatibility report."""

    lines = [
        "# Local AI Hub Extension Compatibility Report",
        "",
        f"Overall status: **{_escape_markdown(report.get('status', 'unavailable'))}**",
        "",
        "| Extension | Status | Capabilities | Model cards | Runtime cards |",
        "| --- | --- | --- | ---: | ---: |",
    ]
    for item in report.get("extensions", []):
        if not isinstance(item, Mapping):
            continue
        cards = item.get("card_counts", {}) if isinstance(item.get("card_counts"), Mapping) else {}
        lines.append(
            "| {name} | {status} | {capabilities} | {models} | {runtimes} |".format(
                name=_escape_markdown(item.get("display_name", item.get("extension_id", "Unidentified extension"))),
                status=_escape_markdown(item.get("status", "unavailable")),
                capabilities=_escape_markdown(", ".join(str(value) for value in item.get("capabilities", []))),
                models=_escape_markdown(cards.get("model_cards", 0)),
                runtimes=_escape_markdown(cards.get("runtime_cards", 0)),
            )
        )
    for item in report.get("extensions", []):
        if not isinstance(item, Mapping):
            continue
        lines.extend(
            [
                "",
                f"## {_escape_markdown(item.get('display_name', item.get('extension_id', 'Unidentified extension')))}",
                "",
                f"- Reason: {_escape_markdown(item.get('reason', 'No reason supplied.'))}",
                f"- Next action: {_escape_markdown(item.get('action', 'Run static validation.'))}",
            ]
        )
    resource_plan = report.get("resource_plan")
    if isinstance(resource_plan, Mapping):
        lines.extend(
            [
                "",
                "## Resource plan",
                "",
                f"- Dry run: `{bool(resource_plan.get('dry_run', True))}`",
                f"- Status: `{_escape_markdown(resource_plan.get('status', 'unavailable'))}`",
                f"- Conflicts: `{len(resource_plan.get('conflicts', []))}`",
            ]
        )
    if report.get("issues"):
        lines.extend(["", "## Static discovery findings", ""])
        for issue in report["issues"]:
            if isinstance(issue, Mapping):
                lines.append(f"- {_escape_markdown(issue.get('message', 'Static discovery finding.'))}")
    return "\n".join(lines) + "\n"
