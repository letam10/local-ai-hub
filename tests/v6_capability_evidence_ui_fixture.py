"""Task-owned loopback fixture for V6 capability-evidence UI checks.

This fixture reuses only the old synthetic transport helper. It replaces the
bootstrap and registry payloads with deterministic server-owned media evidence;
it never imports the Hub API, starts a backend, probes a provider, or creates
media files.
"""

from __future__ import annotations

import argparse
import copy
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from tests import v5_acceptance_ui_fixture as base_fixture


ROOT = Path(__file__).resolve().parents[1]
MEDIA_OPERATIONS = ("video_grade", "logo_overlay", "encode")
ALLOWED_STATES = {"completed", "error", "blocked", "not_run", "malformed"}


def _runtime_evidence(state: str) -> dict[str, object]:
    if state == "completed":
        return {
            "schema_version": "runtime-evidence-projection.v1",
            "subject": "media_overlay_cpu_acceptance",
            "operations": list(MEDIA_OPERATIONS),
            "status": "operational",
            "outcome": "completed",
            "execution": "completed",
            "failure_class": None,
            "invocation_count": 1,
            "cleanup": {"processes_remaining": 0, "temp_cleaned": True},
            "artifact_published": True,
            "source_overwrite_checked": True,
            "source_overwritten": False,
            "reason": "A bounded exact evidence record covers the approved operations.",
            "next_action": "Use only the listed operations with opaque artifacts.",
        }
    if state == "error":
        return {
            "schema_version": "runtime-evidence-projection.v1",
            "subject": "media_overlay_cpu_acceptance",
            "operations": list(MEDIA_OPERATIONS),
            "status": "unavailable",
            "outcome": "error",
            "execution": "attempted",
            "failure_class": "runtime_error",
            "invocation_count": 1,
            "cleanup": {"processes_remaining": 0, "temp_cleaned": True},
            "artifact_published": False,
            "source_overwrite_checked": True,
            "source_overwritten": False,
            "reason": "The bounded evidence stopped before a publishable output.",
            "next_action": "Keep the operations partial and request a separately authorized review.",
        }
    outcome = "blocked" if state == "blocked" else "not_run"
    evidence = {
        "schema_version": "runtime-evidence-projection.v1",
        "subject": "media_overlay_cpu_acceptance",
        "operations": list(MEDIA_OPERATIONS),
        "status": "unavailable",
        "outcome": outcome,
        "execution": "not_run",
        "failure_class": None,
        "invocation_count": 0,
        "cleanup": {"processes_remaining": 0, "temp_cleaned": True},
        "artifact_published": False,
        "source_overwrite_checked": False,
        "source_overwritten": None,
        "reason": "No completed exact evidence is available in this fixture state.",
        "next_action": "Keep the operations partial until a separate approved snapshot exists.",
    }
    if state == "malformed":
        evidence["cleanup"] = {"processes_remaining": {"hidden": "not for display"}, "temp_cleaned": True}
    return evidence


def _operation_scope(state: str) -> dict[str, object]:
    completed = state == "completed"
    return {
        "schema_version": "runtime-operation-scope.v1",
        "subject": "media_overlay_cpu_acceptance",
        "status": "operational" if completed else "unavailable",
        "execution": "completed" if completed else "not_run",
        "evidence_verified": completed,
        "operations": list(MEDIA_OPERATIONS),
        "available_operations": list(MEDIA_OPERATIONS) if completed else [],
        "operation_status": {operation: "operational" if completed else "partial" for operation in MEDIA_OPERATIONS},
        "reason": "A bounded exact scope is published for this fixture state." if completed else "No completed exact scope is available in this fixture state.",
        "next_action": "Use only the listed operations with opaque artifacts." if completed else "Keep the operations partial until separately evidenced.",
    }


def _state_from_path(path: str) -> str:
    requested = parse_qs(urlparse(path).query).get("state", ["completed"])[0]
    return requested if requested in ALLOWED_STATES else "completed"


def _bootstrap(state: str) -> dict[str, object]:
    payload = copy.deepcopy(base_fixture.BOOTSTRAP)
    evidence = _runtime_evidence(state)
    scope = _operation_scope(state)
    payload["capabilities"]["runtime_evidence"] = copy.deepcopy(evidence)
    payload["capabilities"]["media_operation_scope"] = copy.deepcopy(scope)
    payload["productization"]["capabilities"]["runtime_evidence"] = copy.deepcopy(evidence)
    payload["productization"]["capabilities"]["media_operation_scope"] = copy.deepcopy(scope)
    payload["productization"]["capabilities"]["unknown_object"] = {"visible_if_bad": "DO_NOT_RENDER_UNKNOWN"}
    return payload


def _operation_node(node_type: str, title: str, state: str) -> dict[str, object]:
    completed = state == "completed"
    return {
        "type": node_type,
        "title": title,
        "category": "media",
        "description": "Synthetic scoped operation node; no fixture runner is available.",
        "runner": "acceptance_fixture",
        "inputs": [{"name": "media", "label": "Media", "type": "VIDEO", "required": False, "multi": False}],
        "outputs": [{"name": "artifact", "label": "Artifact", "type": "VIDEO", "required": False, "multi": False}],
        "properties": [],
        "status": "operational" if completed else "partial",
        "availability": {
            "status": "operational" if completed else "partial",
            "reason": "Exact server evidence is published." if completed else "No exact server evidence is published.",
            "action": "Use opaque artifacts only." if completed else "Keep this node partial.",
        },
        "heavy": False,
        "annotation": False,
        "version": 1,
    }


def _registry(state: str) -> dict[str, object]:
    nodes = copy.deepcopy(base_fixture.NODES)
    nodes.extend([
        _operation_node("video_grade", "Video grade", state),
        _operation_node("logo_overlay", "Logo overlay", state),
        _operation_node("encode", "Encode", state),
        _operation_node("generic_media", "Generic media", "not_run"),
    ])
    counts = {status: sum(1 for item in nodes if item.get("status") == status) for status in ("operational", "partial", "unavailable")}
    return {
        "status": "completed",
        "contract_version": "node-studio.v2",
        "schema_version": 1,
        "scope": "media",
        "nodes": nodes,
        "availability": {"counts": counts, "honest_statuses": ["operational", "partial", "unavailable"]},
        "encoder_capabilities": {"status": "not_run", "execution": "not_run", "available": False, "encoders": []},
        "operation_scope": _operation_scope(state),
    }


class CapabilityEvidenceHandler(base_fixture.AcceptanceHandler):
    """Serve only deterministic V6 payloads over the task-owned loopback."""

    def _api_payload(self, path: str) -> object | None:
        route = urlparse(path).path
        state = _state_from_path(path)
        if route in {"/health", "/api/bootstrap", "/api/dashboard"} and route != "/health":
            return _bootstrap(state)
        if route == "/api/capabilities":
            return _bootstrap(state)["capabilities"]
        if route.startswith("/api/node-studio/registry"):
            return _registry(state)
        if route.startswith("/api/node-studio/availability"):
            registry = _registry(state)
            return {"status": "completed", "availability": registry["availability"]}
        if route == "/api/node-studio/presets":
            return {"status": "completed", "presets": [{"id": "video_creative_pipeline", "scope": "media", "title": "Scoped media evidence", "description": "Synthetic no-runtime template.", "stage": "fixture"}]}
        if route in {"/api/node-studio/presets/video_creative_pipeline", "/api/node-studio/presets/acceptance-template"}:
            graph = copy.deepcopy(base_fixture.GRAPH)
            graph["scope"] = "media"
            graph["title"] = "Scoped media evidence fixture"
            return {"status": "completed", "graph": graph, "validation": {"valid": True, "errors": []}}
        return super()._api_payload(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--port-file", type=Path, required=True)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), CapabilityEvidenceHandler)
    args.port_file.parent.mkdir(parents=True, exist_ok=True)
    args.port_file.write_text(str(server.server_address[1]), encoding="ascii")
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()
        args.port_file.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
