from __future__ import annotations

import json
import logging
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .config import hub_config, models
from .core import component_statuses, dispatch_tool, get_job_or_error, health, tool_catalog
from .jobs import list_jobs


LOG = logging.getLogger("local-ai-hub")


class HubHandler(BaseHTTPRequestHandler):
    server_version = "LocalAIHub/0.1"

    def log_message(self, format: str, *args: object) -> None:
        LOG.info("%s - %s", self.address_string(), format % args)

    def _write(self, status: int, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            value = json.loads(raw.decode("utf-8"))
            return value if isinstance(value, dict) else {}
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return {}

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path in {"/", "/health"}:
            self._write(200, health())
        elif path == "/tools":
            self._write(200, {"status": "completed", "tools": tool_catalog()})
        elif path == "/models":
            self._write(200, {"status": "completed", "models": models()})
        elif path == "/components":
            self._write(200, {"status": "completed", "components": component_statuses()})
        elif path == "/jobs":
            self._write(200, {"status": "completed", "jobs": list_jobs()})
        elif path.startswith("/jobs/"):
            self._write(*get_job_or_error(path.split("/", 2)[2]))
        else:
            self._write(404, {"status": "error", "error": "Route not found."})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        aliases = {
            "/media/probe": "probe_media",
            "/probe_media": "probe_media",
            "/vision/ui/parse": "parse_screen",
            "/vision/detect": "detect_objects",
            "/vision/ground": "ground_objects",
            "/vision/segment": "segment_image",
            "/vision/track": "track_video_object",
            "/ocr/parse": "ocr_document",
            "/speech/transcribe": "transcribe_media",
            "/video/subtitle": "create_subtitled_video",
            "/voice/tts": "text_to_speech",
            "/voice/design": "design_voice",
            "/voice/clone": "clone_voice",
            "/voice/convert": "convert_voice",
            "/video/upscale/anime": "upscale_anime_video",
        }
        tool = aliases.get(path)
        if tool is None:
            self._write(404, {"status": "error", "error": "Route not found."})
            return
        status, payload = dispatch_tool(tool, self._read_json())
        self._write(status, payload)


def main() -> int:
    config = hub_config()
    host = str(config.get("bind_host", "127.0.0.1"))
    port = int(config.get("api_port", 8765))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = ThreadingHTTPServer((host, port), HubHandler)
    LOG.info("Local AI Hub listening on %s:%s", host, port)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        LOG.info("Stopping Local AI Hub")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
