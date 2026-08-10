"""Redacted deterministic JSON and Markdown projections for evidence packets."""

from __future__ import annotations

from typing import Any

from src.shared.schemas.release_evidence_packet import (
    canonical_release_evidence_packet_json,
    validate_release_evidence_packet,
)


def _error_codes(errors: object) -> list[str]:
    if not isinstance(errors, list):
        return ["packet_invalid"]
    codes = {
        item.get("code", "packet_invalid")
        for item in errors
        if type(item) is dict and type(item.get("code")) is str
    }
    return sorted(codes)[:16] or ["packet_invalid"]


def _escape(value: object) -> str:
    text = str(value)
    return (
        text.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", " ")
        .replace(chr(96), "\\\\" + chr(96))
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )


def project_json(value: object) -> dict[str, Any]:
    """Return a detached canonical JSON projection, never raw input."""

    validation = validate_release_evidence_packet(value)
    if not validation.get("valid"):
        return {
            "ready": False,
            "errors": [{"code": code} for code in _error_codes(validation.get("errors"))],
            "content": None,
            "execution": "not_run",
        }
    packet = validation["packet"]
    assert type(packet) is dict
    content = canonical_release_evidence_packet_json(packet).encode("utf-8")
    return {
        "ready": True,
        "errors": [],
        "content": content,
        "fingerprint": validation["fingerprint"],
        "execution": "not_run",
    }


def project_json_text(value: object) -> dict[str, Any]:
    result = project_json(value)
    if result.get("ready") and isinstance(result.get("content"), bytes):
        result = dict(result)
        result["content"] = result["content"].decode("utf-8")
    return result


def project_markdown(value: object) -> dict[str, Any]:
    """Return a bounded Markdown projection with safe IDs and fixed summaries."""

    validation = validate_release_evidence_packet(value)
    if not validation.get("valid"):
        return {
            "ready": False,
            "errors": [{"code": code} for code in _error_codes(validation.get("errors"))],
            "content": None,
            "execution": "not_run",
        }
    packet = validation["packet"]
    assert type(packet) is dict
    identity = packet["identity"]
    summary = identity["changed_file_summary"]
    verdict = packet["verdict"]
    admission = packet["admission"]
    commands = sorted(packet["commands"], key=lambda item: item["label"])
    lines = [
        "# Release evidence packet",
        "",
        f"- Contract: {_escape(packet['contract'])}",
        f"- Lane or candidate: {_escape(identity['lane_or_candidate'])}",
        f"- Owner: {_escape(identity['owner'])}",
        f"- Candidate branch: {_escape(identity['candidate_branch'])}",
        f"- Candidate ref: {_escape(identity['candidate_ref'])}",
        f"- Static verdict: {_escape(verdict['static'])}",
        f"- Operational status: {_escape(verdict['operational'])}",
        f"- Admission: {_escape(admission['status'])}",
        f"- Changed files: {_escape(summary['total'])}",
        f"- Scope fingerprint: {_escape(summary['scope_fingerprint'])}",
        f"- Packet fingerprint: {_escape(validation['fingerprint'])}",
        "- Execution: not_run",
        "",
        "| Command class | Exit | Duration | Summary |",
        "| --- | ---: | --- | --- |",
    ]
    lines.extend(
        f"| {_escape(item['label'])} | {_escape(item['exit_status'])} | {_escape(item['duration_class'])} | {_escape(item['deterministic_summary'])} |"
        for item in commands
    )
    codes = admission["rejection_codes"]
    if codes:
        lines.extend(["", "## Rejection codes", ""])
        lines.extend(f"- {_escape(code)}" for code in codes)
    return {
        "ready": True,
        "errors": [],
        "content": ("\n".join(lines) + "\n").encode("utf-8"),
        "fingerprint": validation["fingerprint"],
        "execution": "not_run",
    }


export_json_projection = project_json
export_markdown_projection = project_markdown
redacted_json_projection = project_json
redacted_markdown_projection = project_markdown


__all__ = [
    "export_json_projection",
    "export_markdown_projection",
    "project_json",
    "project_json_text",
    "project_markdown",
    "redacted_json_projection",
    "redacted_markdown_projection",
]
