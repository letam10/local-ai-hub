"""Safe loopback fixture for the browser-only Node Studio reliability smoke.

It intentionally serves no production API, model, media or GPU route.  The
fixture only gives the static UI a tiny, deterministic graph so a browser can
exercise LiteGraph pointer selection, group dragging and deletion without
starting an AI backend.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import threading
from http import HTTPStatus
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parents[1]
UI_ROOT = ROOT / "src" / "ui"


def _node(node_type: str, title: str) -> dict[str, object]:
    return {
        "type": node_type,
        "title": title,
        "category": "utility",
        "description": "Deterministic browser-smoke node; it has no runner.",
        "runner": "number",
        "inputs": [],
        "outputs": [{"name": "number", "label": "Number", "type": "NUMBER", "required": False, "multi": False}],
        "properties": [{"name": "value", "label": "Value", "kind": "number", "default": 1}],
        "status": "operational",
        "availability": {"status": "operational", "reason": "Browser smoke fixture only.", "action": "No backend action is available."},
        "heavy": False,
        "annotation": False,
        "supports_draft": False,
        "version": 1,
    }


NODES = [_node("number", "Number A"), _node("number_b", "Number B")]
GRAPH = {
    "schema_version": 1,
    "id": "image_create_upscale",
    "title": "Browser multi-select smoke",
    "scope": "image",
    "nodes": [
        {"id": "node_a", "type": "number", "position": {"x": 100, "y": 100}, "data": {"value": 1}},
        {"id": "node_b", "type": "number_b", "position": {"x": 420, "y": 260}, "data": {"value": 2}},
    ],
    "edges": [],
    "groups": [],
}
AVAILABILITY = {"counts": {"operational": len(NODES), "partial": 0, "unavailable": 0}, "nodes": []}


def _json_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class SmokeHandler(BaseHTTPRequestHandler):
    server_version = "LocalAIHubUISmoke/4.0.0"

    def log_message(self, _format: str, *_args: object) -> None:
        # This fixture must remain quiet so browser assertions are the evidence.
        return

    def _write(self, status: HTTPStatus, payload: object, *, content_type: str = "application/json; charset=utf-8") -> None:
        body = _json_bytes(payload) if content_type.startswith("application/json") else payload
        if not isinstance(body, bytes):
            raise TypeError("static responses must be bytes")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _api_payload(self, path: str) -> object | None:
        health = {"status": "healthy", "version": "4.0.0", "disk": {"free_bytes": 1}, "gpu": {}, "active_jobs": 0}
        bootstrap = {
            "health": health,
            "components": [],
            "applications": [],
            "jobs": [],
            "tools": [
                {"name": "generate_flux", "tool_status": "partial", "reason": "Fixture keeps GPU unavailable.", "action": "No action."},
                {"name": "generate_qwen_image", "tool_status": "partial", "reason": "Fixture keeps GPU unavailable.", "action": "No action."},
                {"name": "run_media_operation", "tool_status": "partial", "reason": "Fixture runs no media work.", "action": "No action."},
                {"name": "upscale_anime_video", "tool_status": "partial", "reason": "Fixture runs no media work.", "action": "No action."},
            ],
            "settings": {},
            "lifecycle": {"comfyui": {"status": "partial", "reason": "Not started by the smoke fixture.", "action": "No action."}},
        }
        if path == "/health":
            return health
        if path == "/api/bootstrap":
            return bootstrap
        if path == "/api/jobs":
            return {"jobs": []}
        if path == "/api/lifecycle":
            return bootstrap["lifecycle"]
        if path == "/api/comfyui/advanced":
            return {"status": "partial", "comfyui": bootstrap["lifecycle"]["comfyui"]}
        if path == "/api/comfyui/workflows":
            return {"workflows": []}
        if path.startswith("/api/node-studio/registry"):
            return {"nodes": NODES, "availability": AVAILABILITY}
        if path.startswith("/api/node-studio/availability"):
            return {"availability": AVAILABILITY}
        if path == "/api/node-studio/presets":
            return {"presets": [{"id": "image_create_upscale", "scope": "image", "title": "Browser multi-select smoke", "description": "No backend", "stage": "fixture"}]}
        if path == "/api/node-studio/presets/image_create_upscale":
            return {"graph": GRAPH, "validation": {"valid": True, "errors": []}}
        return None

    def _serve_static(self, path: str) -> None:
        relative = path.removeprefix("/ui/") or "index.html"
        candidate = (UI_ROOT / relative).resolve()
        if UI_ROOT not in candidate.parents and candidate != UI_ROOT:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not candidate.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or candidate.suffix in {".js", ".mjs"}:
            content_type += "; charset=utf-8"
        self._write(HTTPStatus.OK, candidate.read_bytes(), content_type=content_type)

    def do_GET(self) -> None:  # noqa: N802 - HTTP handler API
        path = unquote(urlparse(self.path).path)
        if path == "/__shutdown":
            self._write(HTTPStatus.OK, {"status": "stopping"})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        payload = self._api_payload(path)
        if payload is not None:
            self._write(HTTPStatus.OK, payload)
            return
        if path == "/ui" or path.startswith("/ui/"):
            self._serve_static(path)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_HEAD(self) -> None:  # noqa: N802 - HTTP handler API
        self.do_GET()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--port-file", type=Path, required=True)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), SmokeHandler)
    args.port_file.parent.mkdir(parents=True, exist_ok=True)
    args.port_file.write_text(str(server.server_address[1]), encoding="ascii")
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
