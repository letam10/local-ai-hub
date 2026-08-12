"""Task-owned loopback fixture for UXNW-V5-ACCEPT-UI-001.

The fixture serves the tracked UI plus deterministic, server-owned payloads. It
does not import the Hub API, start a backend, probe a model, or touch user data.
All artifact bodies are small in-memory synthetic bytes; no fixture media files
are created on disk.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import re
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parents[1]
UI_ROOT = ROOT / "src" / "ui"
_ARTIFACT_PATH = re.compile(r"^/api/artifacts/(artifact_[a-f0-9]{32})$")
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
_JOB_ACTION_PATH = re.compile(r"^/jobs/([^/]+)/(cancel|resume)$")
_DURABLE_RESUME_PATH = re.compile(r"^/api/durable-jobs/([^/]+)/resume$")
_PNG_BYTES = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
_GIB = 1024**3


def _node_definition(node_type: str, title: str, *, inputs: tuple[dict[str, object], ...] = (), outputs: tuple[dict[str, object], ...] = ()) -> dict[str, object]:
    return {
        "type": node_type,
        "title": title,
        "category": "acceptance",
        "description": "Synthetic acceptance node; no runner is available.",
        "runner": "acceptance_fixture",
        "inputs": list(inputs),
        "outputs": list(outputs),
        "properties": [{"name": "value", "label": "Value", "kind": "number", "default": 1}],
        "status": "partial",
        "availability": {"status": "partial", "reason": "Acceptance fixture only.", "action": "No backend action is available."},
        "heavy": False,
        "annotation": False,
        "version": 1,
    }


def _port(name: str, label: str, port_type: str, *, multi: bool = False) -> dict[str, object]:
    return {"name": name, "label": label, "type": port_type, "required": False, "multi": multi}


NODES = [
    _node_definition("number_source", "Number Source", outputs=(_port("number", "Number", "NUMBER"),)),
    _node_definition("number_source_b", "Number Source B", outputs=(_port("number", "Number", "NUMBER"),)),
    _node_definition("number_sink_a", "Number Sink A", inputs=(_port("number", "Number", "NUMBER"),)),
    _node_definition("number_sink_b", "Number Sink B", inputs=(_port("number", "Number", "NUMBER"),)),
    _node_definition("image_source", "Image Source", outputs=(_port("image", "Image", "IMAGE"),)),
    _node_definition("image_sink", "Image Sink", inputs=(_port("image", "Image", "IMAGE"),)),
    _node_definition("video_sink", "Video Sink", inputs=(_port("video", "Video", "VIDEO"),)),
]
AVAILABILITY = {"counts": {"operational": 0, "partial": len(NODES), "unavailable": 0}, "nodes": []}


GRAPH = {
    "schema_version": 1,
    "id": "acceptance-template",
    "title": "UI acceptance fixture",
    "scope": "image",
    "nodes": [
        {"id": "source-number", "type": "number_source", "position": {"x": 120, "y": 130}, "data": {"value": 1}},
        {"id": "sink-number", "type": "number_sink_a", "position": {"x": 510, "y": 130}, "data": {"value": 2}},
        {"id": "source-image", "type": "image_source", "position": {"x": 120, "y": 360}, "data": {"value": 3}},
    ],
    "edges": [],
    "groups": [],
}


def _artifact(artifact_id: str, name: str, media_type: str, body: bytes, **extra: object) -> dict[str, object]:
    return {
        "id": artifact_id,
        "url": f"/api/artifacts/{artifact_id}",
        "name": name,
        "media_type": media_type,
        "size_bytes": len(body),
        "created_at": "2026-08-12T00:00:00Z",
        "sha256": "a" * 64,
        "job_id": "jobv5_" + "1" * 32,
        "job_spec_fingerprint": "b" * 64,
        "adapter_id": "acceptance.fixture",
        "attempt": 1,
        "status": "completed",
        **extra,
    }


ARTIFACTS: dict[str, dict[str, object]] = {}
_ARTIFACT_BODIES = {
    "artifact_" + "1" * 32: ("fixture-image.png", "image/png", _PNG_BYTES, {}),
    "artifact_" + "2" * 32: ("fixture-video.mp4", "video/mp4", b"fixture-video-range-bytes", {}),
    "artifact_" + "3" * 32: ("fixture-audio.wav", "audio/wav", b"fixture-audio-range-bytes", {}),
    "artifact_" + "4" * 32: ("fixture-mask.png", "image/png", _PNG_BYTES, {"is_mask": True, "artifact_kind": "mask"}),
    "artifact_" + "5" * 32: ("fixture-unknown.bin", "application/octet-stream", b"fixture-unknown-range-bytes", {}),
}
for _artifact_id, (_name, _media_type, _body, _extra) in _ARTIFACT_BODIES.items():
    ARTIFACTS[_artifact_id] = {"record": _artifact(_artifact_id, _name, _media_type, _body, **_extra), "body": _body, "media_type": _media_type}


STORAGE = {
    "status": "partial",
    "execution": "not_run",
    "dry_run": True,
    "allowlist": ["c", "d"],
    "low_space_volumes": ["c"],
    "volumes": [
        {
            "id": "c",
            "label": "C:",
            "status": "available",
            "availability": "available",
            "total_bytes": 100 * _GIB,
            "free_bytes": 10 * _GIB,
            "used_bytes": 90 * _GIB,
            "total_gb": 100.0,
            "free_gb": 10.0,
            "used_gb": 90.0,
            "low_space": True,
            "reason": "Free space is below the 20 GiB low-space threshold.",
            "next_action": "Review output, cache, and temporary data before new writes.",
        },
        {
            "id": "d",
            "label": "D:",
            "status": "unavailable",
            "availability": "unknown",
            "total_bytes": None,
            "free_bytes": None,
            "used_bytes": None,
            "total_gb": None,
            "free_gb": None,
            "used_gb": None,
            "low_space": None,
            "reason": "Volume statistics are unavailable; no figures are shown.",
            "next_action": "Verify that the volume is mounted and readable, then refresh storage.",
        },
    ],
}

_artifact_records = [item["record"] for item in ARTIFACTS.values()]
HOT_ACTIVE_ID = "job_20260812_000001_aaaaaaaa"
HOT_FAILED_ID = "job_20260812_000002_bbbbbbbb"
HOT_INTERRUPTED_ID = "job_20260812_000003_cccccccc"
HOT_UNAVAILABLE_ID = "job_20260812_000004_dddddddd"
HOT_COMPLETED_ID = "jobv5_" + "1" * 32
DURABLE_ID = "jobv5_" + "9" * 32
JOBS = [
    {
        "id": HOT_ACTIVE_ID,
        "tool": "acceptance_active",
        "status": "running",
        "created_at": "2026-08-12T00:01:00Z",
        "started_at": "2026-08-12T00:01:01Z",
        "updated_at": "2026-08-12T00:01:12Z",
        "progress": 42,
        "message": "Synthetic active record; no execution was started.",
        "lifecycle": "waiting_for_authorized_worker",
        "resumable": False,
    },
    {
        "id": HOT_FAILED_ID,
        "tool": "acceptance_failed",
        "status": "failed",
        "created_at": "2026-08-12T00:02:00Z",
        "finished_at": "2026-08-12T00:02:02Z",
        "progress": 18,
        "message": "Synthetic failed recovery record.",
        "resumable": True,
    },
    {
        "id": HOT_INTERRUPTED_ID,
        "tool": "acceptance_interrupted",
        "status": "interrupted",
        "created_at": "2026-08-12T00:03:00Z",
        "finished_at": "2026-08-12T00:03:03Z",
        "progress": 55,
        "message": "Synthetic interrupted record; no resume proof is published.",
        "resumable": False,
    },
    {
        "id": HOT_UNAVAILABLE_ID,
        "tool": "acceptance_unavailable",
        "status": "unavailable",
        "created_at": "2026-08-12T00:04:00Z",
        "progress": 0,
        "message": "Synthetic unavailable record.",
        "resumable": False,
    },
    {
        "id": HOT_COMPLETED_ID,
        "tool": "acceptance_fixture",
        "status": "completed",
        "created_at": "2026-08-12T00:05:00Z",
        "finished_at": "2026-08-12T00:05:05Z",
        "progress": 100,
        "message": "Synthetic preview records; no execution was started.",
        "result": {"artifacts": _artifact_records, "provenance": [{"artifact_id": ARTIFACTS["artifact_" + "1" * 32]["record"]["id"], "node_type": "acceptance_preview", "name": "opaque preview batch"}]},
        "resumable": False,
    },
]
DURABLE_RECORD = {
    "id": DURABLE_ID,
    "tool": "acceptance_durable",
    "source": "durable",
    "status": "interrupted",
    "progress": 0,
    "resumable": False,
    "next_action": "Create a new allowlisted descriptor; durable resume is unavailable in this snapshot.",
}
PRODUCT_JOB_RECORDS = [
    {"id": HOT_ACTIVE_ID, "tool": "acceptance_active", "source": "legacy", "status": "running", "progress": 42, "resumable": False, "reason": "Active lifecycle is published without runtime execution proof.", "next_action": "Monitor the next server snapshot."},
    {"id": HOT_FAILED_ID, "tool": "acceptance_failed", "source": "legacy", "status": "failed", "progress": 18, "resumable": True, "reason": "The server snapshot marks this job failed.", "next_action": "Retry only through the published legacy recovery action."},
    {"id": HOT_INTERRUPTED_ID, "tool": "acceptance_interrupted", "source": "legacy", "status": "interrupted", "progress": 55, "resumable": False, "reason": "No resumable proof is published for this interrupted job.", "next_action": "Create a new allowlisted job descriptor."},
    {"id": HOT_UNAVAILABLE_ID, "tool": "acceptance_unavailable", "source": "legacy", "status": "unavailable", "progress": 0, "resumable": False, "reason": "The source job is unavailable in this snapshot.", "next_action": "Review the job state before creating a new task."},
    {"id": HOT_COMPLETED_ID, "tool": "acceptance_fixture", "source": "legacy", "status": "completed", "progress": 100, "resumable": False, "reason": "Completed record has opaque artifact details from the matching hot snapshot.", "next_action": "Open an opaque artifact preview if needed."},
    {"id": DURABLE_ID, "tool": "acceptance_durable", "source": "durable", "status": "interrupted", "progress": 0, "resumable": False, "reason": "Durable detail is not published in this production-shaped snapshot.", "next_action": "Create a new allowlisted descriptor; durable resume is unavailable."},
]
PRODUCT_JOBS = {
    "status": "partial",
    "execution": "not_run",
    "dry_run": True,
    "counts": {"active": 1, "attention": 4, "interrupted": 2, "recoverable": 1, "total": 6},
    "reason": "The server recovery snapshot mixes active, attention and completed records.",
    "next_action": "Open Jobs to review recovery actions and opaque artifact availability.",
    "records": PRODUCT_JOB_RECORDS,
}
HEALTH = {"status": "healthy", "version": "5.0.0-fixture", "disk": {"free_bytes": 80 * _GIB}, "gpu": {"status": "unavailable"}, "active_jobs": 0}
CAPABILITY_MODULES = [
    {"id": "image-engine", "provider": "fixture.provider", "component": "image", "status": "operational", "version": "1.0", "reason": "Bounded static evidence is present.", "next_action": "Keep any runtime request separately authorized."},
    {"id": "mask-engine", "provider": "fixture.provider", "component": "mask", "status": "partial", "version": "1.0", "reason": "The fixture has no runtime worker proof.", "next_action": "Review the worker contract before runtime work."},
    {"id": "video-engine", "provider": "fixture.provider", "component": "video", "status": "unavailable", "version": None, "reason": "No video provider is available in this fixture.", "next_action": "Keep video work unavailable until separately authorized evidence exists."},
    {"id": "future-engine", "provider": "fixture.provider", "component": "future", "status": "not_published", "version": None, "reason": "No verified publication is present.", "next_action": "Publish a reviewed descriptor before a future plan."},
    {"id": "voice-engine", "provider": "fixture.provider", "component": "voice", "status": "not_run", "version": None, "reason": "No runtime probe was requested.", "next_action": "Do not infer execution from this snapshot."},
]
RESOURCE_PLAN = {
    "status": "partial",
    "execution": "not_run",
    "dry_run": True,
    "mode": "parallel",
    "target_gpu": {"vendor": "nvidia", "device_class": "discrete", "model": "Fixture GPU", "vram_mb": 8192, "unknown_object": {"visible_if_bad": "DO_NOT_RENDER_UNKNOWN"}},
    "physical": [
        {"id": "image-engine", "status": "available", "gpu": "gpu-fixture-1", "physical_fit": True, "unknown_object": {"visible_if_bad": "DO_NOT_RENDER_UNKNOWN"}},
        {"id": "video-engine", "status": "unavailable", "gpu": None, "physical_fit": False},
    ],
    "concurrent": [{"id": "image-engine", "status": "available", "gpu": "gpu-fixture-1", "concurrent_fit": True}],
    "errors": [{"code": "gpu_capacity", "module": "video-engine", "unknown_object": {"visible_if_bad": "DO_NOT_RENDER_UNKNOWN"}}],
    "actions": ["Keep video-engine unavailable until a compatible resource is separately authorized."],
    "unknown_object": {"visible_if_bad": "DO_NOT_RENDER_UNKNOWN"},
}
CAPABILITIES = {
    "status": "partial",
    "schema_version": "capability-control-plane.v1",
    "execution": "not_run",
    "dry_run": True,
    "registry": {"status": "partial", "records": []},
    "module_manager": {
        "status": "partial",
        "execution": "not_run",
        "dry_run": True,
        "reason": "Fixture Module Manager emits a read-only plan.",
        "next_action": "Review the plan; no install, repair or uninstall action is available.",
        "actions": ["Keep video-engine unavailable until a compatible resource is separately authorized."],
        "errors": [{"code": "gpu_capacity", "module": "video-engine", "unknown_object": {"visible_if_bad": "DO_NOT_RENDER_UNKNOWN"}}],
        "resource_plan": RESOURCE_PLAN,
        "unknown_object": {"visible_if_bad": "DO_NOT_RENDER_UNKNOWN"},
    },
}
PRODUCTIZATION = {
    "status": "partial",
    "execution": "not_run",
    "dry_run": True,
    "readiness": {"status": "partial", "reason": "The server snapshot mixes usable and incomplete module evidence.", "next_action": "Review each module and resource constraint before any separately authorized runtime work."},
    "capabilities": {
        "status": "partial",
        "registry_status": "partial",
        "module_plan_status": "partial",
        "reason": "Module preflight is server-owned static metadata.",
        "next_action": "Review the plan; no install, repair or uninstall action is available.",
        "modules": CAPABILITY_MODULES,
    },
    "jobs": PRODUCT_JOBS,
    "storage": STORAGE,
    "warnings": [{"id": "volume-c", "status": "partial", "reason": "C: is below the low-space threshold."}, {"id": "volume-d", "status": "unavailable", "reason": "D: statistics are unavailable."}, {"id": "resource", "status": "partial", "reason": "Resource fit is dry-run evidence only."}],
}
BOOTSTRAP = {
    "status": "completed",
    "health": HEALTH,
    "components": [],
    "applications": [],
    "jobs": JOBS,
    "durable_jobs": {"status": "partial", "records": [DURABLE_RECORD], "execution": "not_run", "dry_run": True},
    "tools": [],
    "settings": {"minimum_width": 1280, "minimum_height": 720, "model_load_policy": "on_demand", "max_heavy_gpu_jobs": 1},
    "lifecycle": {"comfyui": {"status": "partial", "reason": "Not started by the acceptance fixture.", "action": "No runtime action is available."}},
    "capabilities": CAPABILITIES,
    "productization": PRODUCTIZATION,
    "storage": STORAGE,
    "workflow_library": {"status": "partial", "library_revision": 0, "workflows": [], "recovery": {"status": "ready", "reason": "Fixture library is read-only metadata.", "action": "Use local draft controls."}},
}

_REQUESTS: list[dict[str, object]] = []
_REQUEST_LOCK = threading.Lock()


def _json_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class AcceptanceHandler(BaseHTTPRequestHandler):
    server_version = "LocalAIHubUIAcceptance/1.0"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _record(self, path: str, *, status: int, body_length: int = 0) -> None:
        with _REQUEST_LOCK:
            _REQUESTS.append({"method": self.command, "path": path, "status": status, "range": self.headers.get("Range"), "body_length": body_length})
            del _REQUESTS[:-100]

    def _write_bytes(self, status: HTTPStatus, body: bytes, *, content_type: str, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self._record(urlparse(self.path).path, status=int(status), body_length=0 if self.command == "HEAD" else len(body))
        if self.command != "HEAD":
            for offset in range(0, len(body), 64 * 1024):
                self.wfile.write(memoryview(body)[offset:offset + 64 * 1024])

    def _write_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._write_bytes(status, _json_bytes(payload), content_type="application/json; charset=utf-8")

    def _serve_artifact(self, artifact_id: str) -> None:
        item = ARTIFACTS.get(artifact_id)
        if item is None:
            self._write_json({"status": "unavailable", "reason": "Opaque artifact is not in this fixture."}, HTTPStatus.NOT_FOUND)
            return
        body = item["body"]
        media_type = str(item["media_type"])
        range_header = self.headers.get("Range")
        if not range_header:
            self._write_bytes(HTTPStatus.OK, body, content_type=media_type, headers={"Accept-Ranges": "bytes"})
            return
        match = _RANGE.fullmatch(range_header.strip())
        if not match or "," in range_header:
            self._write_bytes(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE, b"", content_type=media_type, headers={"Accept-Ranges": "bytes", "Content-Range": f"bytes */{len(body)}"})
            return
        start_text, end_text = match.groups()
        try:
            if not start_text and not end_text:
                raise ValueError
            if start_text:
                start = int(start_text)
                end = int(end_text) if end_text else len(body) - 1
            else:
                suffix = int(end_text)
                if suffix <= 0:
                    raise ValueError
                start = max(0, len(body) - suffix)
                end = len(body) - 1
        except ValueError:
            start = end = -1
        if start < 0 or start >= len(body) or end < start:
            self._write_bytes(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE, b"", content_type=media_type, headers={"Accept-Ranges": "bytes", "Content-Range": f"bytes */{len(body)}"})
            return
        end = min(end, len(body) - 1)
        chunk = body[start:end + 1]
        self._write_bytes(HTTPStatus.PARTIAL_CONTENT, chunk, content_type=media_type, headers={"Accept-Ranges": "bytes", "Content-Range": f"bytes {start}-{end}/{len(body)}"})

    def _api_payload(self, path: str) -> object | None:
        if path in {"/health", "/api/bootstrap", "/api/dashboard"}:
            return HEALTH if path == "/health" else BOOTSTRAP
        if path in {"/api/jobs", "/jobs"}:
            return {"status": "completed", "jobs": JOBS}
        if path == "/api/durable-jobs":
            return BOOTSTRAP["durable_jobs"]
        if path == "/api/capabilities":
            return CAPABILITIES
        if path == "/api/workflow-library":
            return BOOTSTRAP["workflow_library"]
        if path in {"/api/storage", "/api/storage/scan"}:
            return {"status": "partial", "volumes": STORAGE["volumes"], "areas": {}, "legacy": [], "legacy_counts": {"total": 0}}
        if path == "/api/settings":
            return {"status": "completed", "settings": BOOTSTRAP["settings"]}
        if path == "/api/lifecycle":
            return BOOTSTRAP["lifecycle"]
        if path == "/tools":
            return {"status": "completed", "tools": []}
        if path in {"/api/node-studio/registry", "/api/node-studio/registry?scope=image"} or path.startswith("/api/node-studio/registry?"):
            return {"status": "partial", "nodes": NODES, "availability": AVAILABILITY, "encoder_capabilities": {"status": "not_run", "execution": "not_run", "available": False, "encoders": []}}
        if path.startswith("/api/node-studio/availability"):
            return {"status": "partial", "availability": AVAILABILITY}
        if path == "/api/node-studio/presets":
            return {"status": "completed", "presets": [{"id": "image_create_upscale", "scope": "image", "title": "UI acceptance template", "description": "Synthetic no-runtime template.", "stage": "fixture"}]}
        if path in {"/api/node-studio/presets/image_create_upscale", "/api/node-studio/presets/acceptance-template"}:
            return {"status": "completed", "graph": GRAPH, "validation": {"valid": True, "errors": []}}
        return None

    def _serve_static(self, path: str) -> None:
        relative = path.removeprefix("/ui/") or "index.html"
        candidate = (UI_ROOT / relative).resolve()
        if UI_ROOT not in candidate.parents and candidate != UI_ROOT or not candidate.is_file():
            self._write_bytes(HTTPStatus.NOT_FOUND, b"Not found", content_type="text/plain; charset=utf-8")
            return
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or candidate.suffix in {".js", ".mjs"}:
            content_type += "; charset=utf-8"
        self._write_bytes(HTTPStatus.OK, candidate.read_bytes(), content_type=content_type)

    def do_GET(self) -> None:  # noqa: N802 - HTTP handler API
        path = unquote(urlparse(self.path).path)
        if path == "/__shutdown":
            self._write_json({"status": "stopping"})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if path == "/__state":
            with _REQUEST_LOCK:
                requests = list(_REQUESTS)
            self._write_json({"status": "ready", "requests": requests})
            return
        artifact_match = _ARTIFACT_PATH.fullmatch(path)
        if artifact_match:
            self._serve_artifact(artifact_match.group(1))
            return
        payload = self._api_payload(self.path if "?" in self.path else path)
        if payload is not None:
            self._write_json(payload)
            return
        if path == "/ui" or path.startswith("/ui/"):
            self._serve_static(path)
            return
        self._write_json({"status": "not_found"}, HTTPStatus.NOT_FOUND)

    def do_HEAD(self) -> None:  # noqa: N802 - HTTP handler API
        self.do_GET()

    def do_POST(self) -> None:  # noqa: N802 - HTTP handler API
        path = unquote(urlparse(self.path).path)
        job_action = _JOB_ACTION_PATH.fullmatch(path)
        if job_action:
            job_id, action = job_action.groups()
            known = any(item.get("id") == job_id for item in JOBS)
            if not known:
                self._write_json({"status": "not_found", "next_action": "Use a server-published hot job ID."}, HTTPStatus.NOT_FOUND)
                return
            if action == "resume" and job_id != HOT_FAILED_ID:
                self._write_json({"status": "unavailable", "execution": "not_run", "dry_run": True, "next_action": "This fixture publishes no legacy resume proof for the selected job."})
                return
            self._write_json({"status": "queued" if action == "resume" else "cancelling", "execution": "not_run", "dry_run": True, "message": f"Synthetic {action} response; no execution was started."})
            return
        durable_resume = _DURABLE_RESUME_PATH.fullmatch(path)
        if durable_resume:
            if durable_resume.group(1) != DURABLE_ID:
                self._write_json({"status": "not_found", "execution": "not_run", "dry_run": True}, HTTPStatus.NOT_FOUND)
                return
            self._write_json({"status": "unavailable", "execution": "not_run", "dry_run": True, "next_action": "Durable resume is unavailable in this production-shaped snapshot."})
            return
        if path == "/api/node-studio/validate":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(min(length, 2 * 1024 * 1024))
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = {}
            graph = payload.get("graph") if isinstance(payload, dict) else None
            self._write_json({"status": "completed", "validation": {"valid": True, "errors": [], "graph": graph or GRAPH}})
            return
        if path == "/api/node-studio/dirty":
            self._write_json({"status": "completed", "dirty_node_ids": []})
            return
        if path == "/api/workflow-library":
            self._write_json({"status": "partial", "library_revision": 1, "reason": "Fixture library is intentionally non-operational.", "action": "Keep the local draft."})
            return
        if path == "/api/durable-jobs":
            self._write_json({"status": "not_run", "records": []})
            return
        self._write_json({"status": "not_run", "reason": "No fixture execution endpoint is available."}, HTTPStatus.NOT_FOUND)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--port-file", type=Path, required=True)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), AcceptanceHandler)
    args.port_file.parent.mkdir(parents=True, exist_ok=True)
    args.port_file.write_text(str(server.server_address[1]), encoding="ascii")
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
