"""Detached, scrubbed projections for Creative Recipe Intelligence DTOs.

The projection helpers intentionally accept only already validated contracts
and render opaque IDs, statuses, fixed issue codes and bounded counts.  They
never include labels, prompt text, paths, commands or caller-supplied error
messages in Markdown.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from itertools import islice
from typing import Any

from src.shared.schemas.creative_recipes import (
    MAX_FINDINGS,
    validate_compatibility_report,
    validate_generation_intent,
    validate_lint_finding,
)

from .catalog import discover_managed_recipe_catalogs


def _escape(value: object) -> str:
    """Escape Markdown table/control characters without echoing raw markup."""

    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", " ")
        .replace("`", "\\`")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )


def _error_codes(errors: object) -> list[str]:
    if not isinstance(errors, list):
        return []
    return sorted({item.get("code", "invalid") for item in errors if isinstance(item, Mapping) and isinstance(item.get("code", "invalid"), str)})[:16]


def export_compatibility_markdown(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping) and isinstance(value.get("report"), Mapping):
        value = value["report"]
    validation = validate_compatibility_report(value)
    if not validation.get("valid"):
        return {"ready": False, "errors": [{"code": code} for code in _error_codes(validation.get("errors"))] or [{"code": "report_invalid"}], "content": None, "execution": "not_run"}
    report = validation["report"]
    assert isinstance(report, dict)
    lines = [
        "# Creative recipe compatibility",
        "",
        f"- Status: `{_escape(report['status'])}`",
        f"- Recipe: `{_escape(report['recipe_id'])}`",
        f"- Target: `{_escape(report['target_id'])}`",
        f"- Checks: `{len(report['checks'])}`",
        f"- Missing capabilities: `{len(report['missing_capabilities'])}`",
        "- Execution: `not_run`",
    ]
    codes = sorted({item["reason_code"] for item in report["checks"]})
    if codes:
        lines.extend(["", "| Check reason |", "| --- |"])
        lines.extend(f"| `{_escape(code)}` |" for code in codes)
    return {"ready": True, "errors": [], "content": ("\n".join(lines) + "\n").encode("utf-8"), "fingerprint": validation["fingerprint"], "execution": "not_run"}


def export_lint_markdown(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping) and isinstance(value.get("findings"), list):
        value = value["findings"]
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, Mapping)):
        return {"ready": False, "errors": [{"code": "finding_list_required"}], "content": None, "execution": "not_run"}
    normalized: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    items = list(islice(value, MAX_FINDINGS + 1))
    if len(items) > MAX_FINDINGS:
        return {"ready": False, "errors": [{"code": "finding_limit"}], "content": None, "execution": "not_run"}
    for item in items:
        validation = validate_lint_finding(item)
        if not validation.get("valid"):
            errors.extend({"code": code} for code in _error_codes(validation.get("errors")) or ["finding_invalid"])
            continue
        finding = validation["finding"]
        assert isinstance(finding, dict)
        normalized.append(finding)
    if errors:
        return {"ready": False, "errors": errors[:16], "content": None, "execution": "not_run"}
    normalized.sort(key=lambda item: item["id"])
    lines = ["# Creative recipe lint", "", f"- Findings: `{len(normalized)}`", "- Execution: `not_run`"]
    if normalized:
        lines.extend(["", "| Finding | Code | Severity | Status |", "| --- | --- | --- | --- |"])
        lines.extend(f"| `{_escape(item['id'])}` | `{_escape(item['code'])}` | `{_escape(item['severity'])}` | `{_escape(item['status'])}` |" for item in normalized)
    return {"ready": True, "errors": [], "content": ("\n".join(lines) + "\n").encode("utf-8"), "execution": "not_run"}


def export_generation_intent_markdown(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping) and isinstance(value.get("intent"), Mapping):
        value = value["intent"]
    validation = validate_generation_intent(value)
    if not validation.get("valid"):
        return {"ready": False, "errors": [{"code": code} for code in _error_codes(validation.get("errors"))] or [{"code": "intent_invalid"}], "content": None, "execution": "not_run"}
    intent = validation["intent"]
    assert isinstance(intent, dict)
    lines = [
        "# Creative generation intent",
        "",
        f"- Status: `{_escape(intent['status'])}`",
        f"- Recipe: `{_escape(intent['recipe_id'])}`",
        f"- Target: `{_escape(intent['target_id'])}`",
        f"- Slots: `{len(intent['slot_values'])}`",
        f"- Style packs: `{len(intent['style_pack_ids'])}`",
        "- Dry run: `true`",
        "- Execution: `not_run`",
    ]
    return {"ready": True, "errors": [], "content": ("\n".join(lines) + "\n").encode("utf-8"), "fingerprint": validation["fingerprint"], "execution": "not_run"}


def export_discovery_markdown() -> dict[str, Any]:
    """Project server-owned fixed-root discovery without accepting a mapping."""

    report = discover_managed_recipe_catalogs()
    lines = [
        "# Creative recipe catalog discovery",
        "",
        f"- Status: `{_escape(report['status'])}`",
        f"- Catalogs: `{len(report['catalogs'])}`",
        f"- Recipes: `{len(report['recipes'])}`",
        f"- Style packs: `{len(report['style_packs'])}`",
        f"- Targets: `{len(report['targets'])}`",
        "- Execution: `not_run`",
    ]
    if report.get("errors"):
        lines.extend(["", "| Error code |", "| --- |"])
        lines.extend(f"| `{_escape(item['code'])}` |" for item in report["errors"] if isinstance(item, Mapping) and isinstance(item.get("code"), str))
    return {"ready": True, "errors": [], "content": ("\n".join(lines) + "\n").encode("utf-8"), "execution": "not_run"}


__all__ = ["export_compatibility_markdown", "export_lint_markdown", "export_generation_intent_markdown", "export_discovery_markdown"]
