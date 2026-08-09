from __future__ import annotations

import json
import logging
import mimetypes
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from src.modules.image_generation.backend.comfyui import health as comfy_health
from src.modules.image_generation.backend.comfyui import shutdown_owned_idle
from src.services.artifact_store import describe as describe_artifact
from src.services.artifact_store import open_artifact, resolve as resolve_artifact, stage_upload
from src.services.job_manager.manager import job_manager
from src.services.runtime_registry import applications, launch
from src.services.storage_manager.overview import model_summary, storage_summary
from src.shared.paths.registry import ROOT

from .config import hub_config
from .core import component_statuses, get_job_or_error, health, submit_tool, tool_catalog
from .jobs import get_job, list_jobs


LOG = logging.getLogger("local-ai-hub")
UI_ROOT = (ROOT / "src" / "ui").resolve()


class HubHandler(BaseHTTPRequestHandler):
    server_version = "LocalAIHub/3.0"

    def log_message(self, format: str, *args: object) -> None:
        LOG.info("%s - %s", self.address_string(), format % args)

    def _write(self, status: int, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _write_file(self, path: Path, name: str, media_type: str) -> None:
        try:
            body = path.read_bytes()
        except OSError:
            self._write(404, {"status": "error", "error": "Artifact Hub không còn tồn tại."})
            return
        self.send_response(200)
        self.send_header("Content-Type", media_type or mimetypes.guess_type(name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        safe_name = name.replace('"', "")
        self.send_header("Content-Disposition", f'inline; filename="{safe_name}"')
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _write_static(self, path: str) -> None:
        relative = unquote(path[len("/ui/") :]) or "index.html"
        candidate = (UI_ROOT / relative).resolve()
        try:
            candidate.relative_to(UI_ROOT)
        except ValueError:
            self._write(403, {"status": "error", "error": "UI path is outside the bundled frontend."})
            return
        if not candidate.is_file():
            self._write(404, {"status": "error", "error": "UI asset not found."})
            return
        try:
            body = candidate.read_bytes()
        except OSError as exc:
            self._write(500, {"status": "error", "error": str(exc)})
            return
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 2 * 1024 * 1024:
                return {}
            raw = self.rfile.read(length) if length else b"{}"
            value = json.loads(raw.decode("utf-8"))
            return value if isinstance(value, dict) else {}
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return {}

    def _upload(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._write(400, {"status": "error", "error": "Content-Length upload không hợp lệ."})
            return
        if length <= 0 or length > 512 * 1024 * 1024:
            self._write(413, {"status": "error", "error": "Upload phải lớn hơn 0 và không vượt 512 MiB."})
            return
        filename = unquote(self.headers.get("X-File-Name", "upload.bin"))
        try:
            artifact = stage_upload(filename, self.rfile.read(length), self.headers.get("Content-Type"))
        except (OSError, ValueError) as exc:
            self._write(400, {"status": "error", "error": str(exc)})
            return
        self._write(201, {"status": "completed", "artifact": artifact})

    def do_GET(self) -> None:  # noqa: N802
        path = unquote(urlparse(self.path).path)
        normalized = path.rstrip("/") or "/"
        if path == "/ui":
            self.send_response(302)
            self.send_header("Location", "/ui/")
            self.end_headers()
        elif path.startswith("/ui/"):
            self._write_static(path)
        elif normalized.startswith("/api/artifacts/"):
            artifact_id = normalized.rsplit("/", 1)[-1]
            path_value = resolve_artifact(artifact_id)
            artifact = describe_artifact(artifact_id)
            if path_value is None or artifact is None:
                self._write(404, {"status": "error", "error": "Không tìm thấy artifact Hub."})
            else:
                self._write_file(path_value, str(artifact["name"]), str(artifact["media_type"]))
        elif normalized in {"/", "/health"}:
            self._write(200, health())
        elif normalized == "/tools":
            self._write(200, {"status": "completed", "tools": tool_catalog()})
        elif normalized == "/models":
            self._write(200, {"status": "completed", "models": model_summary()})
        elif normalized == "/components":
            self._write(200, {"status": "completed", "components": component_statuses()})
        elif normalized in {"/jobs", "/api/jobs"}:
            self._write(200, {"status": "completed", "jobs": list_jobs()})
        elif normalized.startswith("/jobs/"):
            self._write(*get_job_or_error(normalized.split("/", 2)[2]))
        elif normalized == "/api/dashboard":
            self._write(200, {"status": "completed", "health": health(), "components": component_statuses(), "applications": applications(), "jobs": list_jobs()[:8]})
        elif normalized == "/api/storage":
            self._write(200, storage_summary())
        elif normalized == "/api/models":
            self._write(200, {"status": "completed", "models": model_summary()})
        elif normalized == "/api/applications":
            self._write(200, {"status": "completed", "applications": applications()})
        elif normalized == "/api/lifecycle":
            self._write(200, {"status": "completed", "comfyui": comfy_health()})
        elif normalized == "/api/settings":
            config = hub_config()
            self._write(200, {
                "status": "completed",
                "settings": {
                    "start_maximized": bool(config.get("start_maximized", True)),
                    "minimum_width": int(config.get("minimum_width", 1280)),
                    "minimum_height": int(config.get("minimum_height", 720)),
                    "model_load_policy": config.get("model_load_policy", "on_demand"),
                    "max_heavy_gpu_jobs": int(config.get("max_heavy_gpu_jobs", 1)),
                    "api_bind": str(config.get("bind_host", "127.0.0.1")),
                    "api_port": int(config.get("api_port", 8765)),
                    "mcp_transport": config.get("mcp_transport", "stdio"),
                    "comfyui_port": int(config.get("comfyui_port", 8188)),
                },
            })
        else:
            self._write(404, {"status": "error", "error": "Route not found."})

    def do_POST(self) -> None:  # noqa: N802
        path = unquote(urlparse(self.path).path.rstrip("/") or "/")
        if path == "/api/uploads":
            self._upload()
            return
        if path.startswith("/api/artifacts/") and path.endswith("/open"):
            artifact_id = path[len("/api/artifacts/") : -len("/open")].strip("/")
            ok, message = open_artifact(artifact_id)
            self._write(202 if ok else 404, {"status": "completed" if ok else "error", "message": message})
            return
        if path.startswith("/api/applications/") and path.endswith("/launch"):
            application_id = path[len("/api/applications/") : -len("/launch")].strip("/")
            status, payload = launch(application_id)
            self._write(status, payload)
            return
        if path.startswith("/jobs/") and path.endswith("/cancel"):
            job_id = path[len("/jobs/") : -len("/cancel")].strip("/")
            ok, message = job_manager.cancel(job_id)
            self._write(202 if ok else 400, {"status": "cancelling" if ok else "error", "message": message, "job": get_job(job_id)})
            return
        if path.startswith("/jobs/") and path.endswith("/resume"):
            job_id = path[len("/jobs/") : -len("/resume")].strip("/")
            ok, value = job_manager.resume(job_id)
            if not ok:
                self._write(400, {"status": "error", "error": str(value)})
            else:
                record = value if isinstance(value, dict) else {}
                self._write(202, {"status": "queued", "job": get_job(str(record.get("id", "")))})
            return
        if path == "/api/lifecycle/close":
            self._write(200, shutdown_owned_idle())
            return
        if path == "/api/storage/scan":
            self._write(200, storage_summary())
            return
        aliases = {
            "/media/probe": "probe_media",
            "/probe_media": "probe_media",
            "/api/media/run": "run_media_operation",
            "/vision/ui/parse": "parse_screen",
            "/vision/detect": "detect_objects",
            "/vision/ground": "ground_objects",
            "/vision/segment": "segment_image",
            "/vision/segment-box": "segment_from_box",
            "/vision/segment-points": "segment_from_points",
            "/vision/track": "track_video_object",
            "/ocr/parse": "ocr_document",
            "/speech/transcribe": "transcribe_media",
            "/video/subtitle": "create_subtitled_video",
            "/voice/tts": "text_to_speech",
            "/voice/design": "design_voice",
            "/voice/clone": "clone_voice",
            "/voice/convert": "convert_voice",
            "/video/upscale/anime": "upscale_anime_video",
            "/api/image/flux": "generate_flux",
            "/api/image/qwen": "generate_qwen_image",
        }
        if path.startswith("/api/jobs/"):
            tool = path[len("/api/jobs/") :].strip("/")
        else:
            tool = aliases.get(path)
        if not tool:
            self._write(404, {"status": "error", "error": "Route not found."})
            return
        status, payload = submit_tool(tool, self._read_json())
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
        shutdown_owned_idle()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
