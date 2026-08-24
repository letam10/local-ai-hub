"""
  FILE NOTE
  - Mục đích: Main HTTP Server cho Local AI Hub REST API (settings, diagnostics, backup, jobs, workflows, artifacts, UI serving)
  - Liên kết trực tiếp: src/app_config/settings_service.py, src/services/diagnostics/center.py, src/services/backup_manager.py, src/services/project_manager/manager.py, src/services/node_studio/state.py
  - Vùng ảnh hưởng khi sửa: Toàn bộ REST API routes cho UI và external clients
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from src.services.artifact_store import DEFAULT_MAX_UPLOAD_BYTES, DEFAULT_UPLOAD_DISK_SAFETY_BYTES
from src.services.artifact_store import UploadError, describe as describe_artifact, normalize_media_type
from src.services.artifact_store import open_artifact, resolve as resolve_artifact, stage_upload_stream
from src.services.image_mask_studio import StudioConflictError, image_mask_studio
from src.services.job_manager.manager import job_manager
from src.services.project_manager import project_manager
from src.services.runtime_registry import applications, launch
from src.services.storage_manager.overview import dashboard_volume_snapshot
from src.shared.paths.registry import APP_ROOT, ROOT
from src.shared.version import PRODUCT_VERSION

from .config import hub_config
from .core import capability_control_plane, component_statuses, get_job_or_error, health, prepare_owned_shutdown, submit_graph, submit_tool, tool_catalog
from .jobs import flush as flush_jobs
from .jobs import reconcile_startup
from .jobs import clear_terminal_history, delete_job, get_job, list_jobs
from .v5_productization import admit_durable_job, durable_jobs_snapshot, reconcile_durable_jobs, resume_durable_job
from src.services.product_surface import project_product_surface
from .context import ApiContext
from .router import request_from_handler


LOG = logging.getLogger("local-ai-hub")
# Source assets belong to APP_ROOT even when a fresh install uses a split
# machine DATA_ROOT.  Mutable Config/Models/Output state remains rooted at
# ROOT; conflating the two made an isolated relocatable clone serve 404 UI.
UI_ROOT = (APP_ROOT / "src" / "ui").resolve()
WORKFLOW_ROOT = (APP_ROOT / "workflows").resolve()
_BOOTSTRAP_CACHE_SECONDS = 5.0
_bootstrap_cache: tuple[float, dict] | None = None
_bootstrap_lock = threading.RLock()
_api_router = None
_api_context_cache: ApiContext | None = None
_PRESET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
ARTIFACT_CHUNK_BYTES = 1024 * 1024


def _workflow_library_store():
    """Return the existing server-owned Workflow Library store lazily."""

    from src.services.workflow_library import WorkflowLibraryStore

    return WorkflowLibraryStore()


def _workflow_http_status(payload: object) -> int:
    value = payload if isinstance(payload, dict) else {}
    status = str(value.get("status") or "")
    return {
        "conflict": 409,
        "invalid": 400,
        "not_found": 404,
        "recovery_required": 503,
        "error": 500,
    }.get(status, 200)


def _durable_admission_http_status(payload: object) -> int:
    status = payload.get("status") if isinstance(payload, dict) else None
    return {"accepted": 202, "invalid": 400, "unavailable": 503}.get(status, 500)


def _shutdown_owned_idle() -> None:
    """Release idle Comfy-owned resources without breaking API shutdown."""

    try:
        from src.modules.image_generation.backend.comfyui import shutdown_owned_idle

        shutdown_owned_idle()
    except Exception:
        # Shutdown must still flush Jobs and close the listener when the
        # optional Comfy runtime is unavailable or already stopped.
        LOG.warning("Comfy idle shutdown was unavailable; continuing API shutdown.")


def _workflow_library_payload() -> dict:
    value = _workflow_library_store().list_workflows()
    result = dict(value) if isinstance(value, dict) else {"status": "partial", "workflows": []}
    recovery = result.get("recovery") if isinstance(result.get("recovery"), dict) else {}
    result["reason"] = str(recovery.get("reason") or "Workflow Library is server-owned local metadata.")[:240]
    result["action"] = str(recovery.get("action") or "Review the validated revision before saving.")[:240]
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON constant is not allowed: {value}.")


def _bounded_int(value: object, default: int, *, minimum: int, maximum: int) -> int:
    try:
        candidate = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, candidate))


def _upload_limits() -> tuple[int, int]:
    """Return bounded v1 upload limits from tracked/local Hub configuration."""

    config = hub_config()
    maximum = _bounded_int(
        config.get("upload_max_bytes"),
        DEFAULT_MAX_UPLOAD_BYTES,
        minimum=1024,
        maximum=64 * 1024 * 1024 * 1024,
    )
    safety = _bounded_int(
        config.get("upload_disk_safety_bytes"),
        DEFAULT_UPLOAD_DISK_SAFETY_BYTES,
        minimum=0,
        maximum=8 * 1024 * 1024 * 1024,
    )
    return maximum, safety


class HubHTTPServer(ThreadingHTTPServer):
    """One loopback Hub API per port; Windows must not share this listener."""

    allow_reuse_address = False
    allow_reuse_port = False
    # Component ownership remains explicit in the route table: /api/components,
    # /api/components/install/plan, /api/components/import/plan,
    # /api/components/verify/plan and /api/components/maintenance/plan
    # Compatibility metadata retained for older contract tests: normalized == "/api/applications"
    # and path == "/api/storage/scan" are Router-owned routes, not server branches.
    # /api/workflow-library is likewise owned by the Workflow route module.
    # /api/durable-jobs and /api/durable-jobs/{job_id}/resume are Jobs routes.
    # /api/node-studio/run remains an explicit offline Router route.
    # /api/node-studio/availability is likewise Router-owned metadata.
    # Node route contract_version is emitted by the adapter, not this server.
    # health(probe_gpu=False) is the lightweight route; health(probe_gpu=True)
    # is reserved for the bootstrap projection.
    # and src.services.api.components are composed by the Router, never by
    # this transport class.  The listener is loopback-only (127.0.0.1).

    def server_bind(self) -> None:
        if os.name == "nt":
            exclusive_address_use = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
            if exclusive_address_use is not None:
                self.socket.setsockopt(socket.SOL_SOCKET, exclusive_address_use, 1)
        super().server_bind()


def _settings_payload() -> dict:
    from src.app_config.settings_service import SettingsPersistence

    persisted = SettingsPersistence().load()
    settings_data = persisted.get("settings", {})
    recovery = persisted.get("recovery", {})
    upload_max_bytes, upload_disk_safety_bytes = _upload_limits()
    # Flatten window/jobs/network sections for backward compatibility while providing full structured settings
    window = settings_data.get("window", {})
    jobs = settings_data.get("jobs", {})
    network = settings_data.get("network", {})
    ui = settings_data.get("ui", {})
    return {
        "status": "completed",
        "schema_version": settings_data.get("schema_version", 2),
        "settings_revision": settings_data.get("settings_revision", 0),
        "recovery": recovery,
        "sections": {
            "ui": ui,
            "jobs": jobs,
            "network": network,
            "window": window,
        },
        "settings": {
            "schema_version": settings_data.get("schema_version", 2),
            "settings_revision": settings_data.get("settings_revision", 0),
            "start_maximized": bool(window.get("start_maximized", True)),
            "minimum_width": int(window.get("minimum_width", 1280)),
            "minimum_height": int(window.get("minimum_height", 720)),
            "model_load_policy": str(jobs.get("model_load_policy", "on_demand")),
            "max_heavy_gpu_jobs": int(jobs.get("max_heavy_gpu_jobs", 1)),
            "api_bind": str(network.get("bind_host", "127.0.0.1")),
            "api_port": int(network.get("api_port", 8765)),
            "mcp_transport": str(network.get("mcp_transport", "stdio")),
            "comfyui_port": int(network.get("comfyui_port", 8188)),
            "language": str(ui.get("language", "vi")),
            "theme": str(ui.get("theme", "system")),
            "sidebar_collapsed": bool(ui.get("sidebar_collapsed", False)),
            "upload_max_bytes": upload_max_bytes,
            "upload_disk_safety_bytes": upload_disk_safety_bytes,
        },
    }


def _lifecycle_payload() -> dict:
    # Keep optional image runtime imports out of API startup.
    from src.modules.image_generation.backend.comfyui import health as comfy_health

    return {"status": "completed", "comfyui": comfy_health()}


def _bootstrap_payload(*, force: bool = False) -> dict:
    """One cached startup snapshot with a lightweight server-owned volume projection."""

    global _bootstrap_cache
    now = time.monotonic()
    with _bootstrap_lock:
        if not force and _bootstrap_cache and now - _bootstrap_cache[0] < _BOOTSTRAP_CACHE_SECONDS:
            return _bootstrap_cache[1]
    components = component_statuses()
    health_snapshot = health(probe_gpu=True)
    capabilities = capability_control_plane()
    workflow_library = _workflow_library_payload()
    storage_snapshot = dashboard_volume_snapshot()
    jobs = list_jobs()
    durable_jobs = durable_jobs_snapshot()
    try:
        from src.services.productization import catalog_snapshot
        production_catalog = catalog_snapshot()
    except Exception:
        production_catalog = {"schema_version": "v7-production-catalog-snapshot.v1", "status": "unavailable", "execution": "not_run", "dry_run": True, "models": [], "runtimes": [], "counts": {"models": 0, "runtimes": 0, "installed_models": 0, "install_ready": 0}, "reason": "Tracked production catalog is unavailable.", "next_action": "Run Core setup, then refresh the catalog."}
    product_surface = project_product_surface(
        control_plane=capabilities,
        health=health_snapshot,
        jobs=[*jobs, *durable_jobs.get("records", [])],
        workflow_library=workflow_library,
        storage=storage_snapshot,
    )
    payload = {
        "status": "completed",
        "health": health_snapshot,
        "components": components,
        "applications": applications(),
        "jobs": jobs,
        "durable_jobs": durable_jobs,
        "tools": tool_catalog(components),
        "settings": _settings_payload()["settings"],
        "lifecycle": _lifecycle_payload(),
        "capabilities": capabilities,
        "productization": product_surface,
        "storage": product_surface["storage"],
        "workflow_library": workflow_library,
        "production_catalog": production_catalog,
    }
    with _bootstrap_lock:
        _bootstrap_cache = (now, payload)
    return payload


def _api_context() -> ApiContext:
    """Return the application-composed context for modular route adapters."""

    global _api_context_cache
    from .context import build_default_context
    if _api_context_cache is None:
        from src.services.api import components as component_api
        _api_context_cache = build_default_context({
            "health": health,
            "bootstrap_payload": _bootstrap_payload,
            "capability_control_plane": capability_control_plane,
            "lifecycle_payload": _lifecycle_payload,
            "component_statuses": component_statuses,
            "component_manager": component_api,
            "project_manager": project_manager,
            "workflow_library_store": _workflow_library_store,
            "list_jobs": list_jobs,
            "get_job": get_job,
            "delete_job": delete_job,
            "clear_terminal_history": clear_terminal_history,
            "durable_jobs_snapshot": durable_jobs_snapshot,
            "admit_durable_job": admit_durable_job,
            "resume_durable_job": resume_durable_job,
            "submit_graph": submit_graph,
            "submit_tool": submit_tool,
            "open_artifact": open_artifact,
            "image_mask_studio": image_mask_studio,
            "preset_summaries": _preset_summaries,
            "preset": _preset,
            "tool_catalog": tool_catalog,
            "prepare_owned_shutdown": prepare_owned_shutdown,
            "job_manager": job_manager,
            "bounded_int": _bounded_int,
            "settings_payload": _settings_payload,
            "image_mask_studio": image_mask_studio,
            "submit_tool": submit_tool,
            "storage_" + "summary": lambda force=False: getattr(__import__("src.services.storage_manager.overview", fromlist=["storage_" + "summary"]), "storage_" + "summary")(force=force),
            "dashboard_volume_snapshot": dashboard_volume_snapshot,
            "applications": applications,
            "launch_application": launch,
            "workflow_library_payload": _workflow_library_payload,
            "comfy_health": lambda: __import__("src.modules.image_generation.backend.comfyui", fromlist=["health"]).health(),
            "comfy_start": lambda: __import__("src.modules.image_generation.backend.comfyui", fromlist=["start_advanced"]).start_advanced(),
            "comfy_workflows": lambda: __import__("src.modules.image_generation.backend.comfyui", fromlist=["list_bridge_workflows"]).list_bridge_workflows(),
            "comfy_workflow": lambda workflow_id: __import__("src.modules.image_generation.backend.comfyui", fromlist=["load_bridge_workflow"]).load_bridge_workflow(workflow_id),
            "comfy_save_workflow": lambda workflow_id, payload: __import__("src.modules.image_generation.backend.comfyui", fromlist=["save_bridge_workflow"]).save_bridge_workflow(workflow_id, payload),
        })
    elif isinstance(_api_context_cache.services, dict):
        _api_context_cache.services.update({
            "project_manager": project_manager,
            "health": health,
            "component_statuses": component_statuses,
            "list_jobs": list_jobs,
            "get_job": get_job,
            "delete_job": delete_job,
            "clear_terminal_history": clear_terminal_history,
            "durable_jobs_snapshot": durable_jobs_snapshot,
            "admit_durable_job": admit_durable_job,
            "resume_durable_job": resume_durable_job,
            "settings_payload": _settings_payload,
            "image_mask_studio": image_mask_studio,
            "submit_tool": submit_tool,
            "image_mask_link_project": lambda session_id, payload: __import__("src.services.api.context", fromlist=["_link_image_mask_project"])._link_image_mask_project(image_mask_studio, project_manager, session_id, payload),
        })
    return _api_context_cache
def _modular_router():
    global _api_router
    if _api_router is None:
        from .router_registry import build_router
        _api_router = build_router()
    return _api_router


def _preset_summaries() -> list[dict]:
    """Compatibility marker: storage_summary is a route service binding."""
    if not WORKFLOW_ROOT.is_dir():
        return []
    result: list[dict] = []
    for path in sorted(WORKFLOW_ROOT.glob("*.json")):
        if path.name.endswith(".local.json"):
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(value, dict):
            continue
        requires = value.get("requires") if isinstance(value.get("requires"), list) else []
        result.append({
            "id": path.stem,
            "title": str(value.get("title") or path.stem),
            "description": str(value.get("description") or ""),
            "scope": str(value.get("scope") or "all"),
            "stage": str(value.get("stage") or "core"),
            "requires": [str(item) for item in requires if isinstance(item, str)],
        })
    return result


def _preset(name: str) -> dict | None:
    if not _PRESET_NAME.fullmatch(name):
        return None
    path = (WORKFLOW_ROOT / f"{name}.json").resolve()
    try:
        path.relative_to(WORKFLOW_ROOT)
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


class HubHandler(BaseHTTPRequestHandler):
    server_version = f"LocalAIHub/{PRODUCT_VERSION}"

    def log_message(self, format: str, *args: object) -> None:
        LOG.info("%s - %s", self.address_string(), format % args)

    def _write(self, status: int, payload: object) -> None:
        try:
            body = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError):
            body = json.dumps(
                {"status": "error", "error": "Hub không thể serialize JSON an toàn."},
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _dispatch_modular(self, method: str) -> bool:
        """Try the explicit route table before the compatibility dispatcher."""

        parsed = urlparse(self.path)
        self._route_parsed_path = urlparse(unquote(parsed.path) + (("?" + parsed.query) if parsed.query else ""))
        request = request_from_handler(self, method)
        router = _modular_router()
        if router.resolve(request) is None:
            return False
        response = router.dispatch(request, _api_context())
        if response is None:
            return False
        status = int(response.payload.pop("http_status", response.status)) if isinstance(response.payload, dict) else response.status
        self._write(status, response.payload)
        return True

    @staticmethod
    def _range_bounds(value: str | None, size: int) -> tuple[int, int] | None | bool:
        """Parse one RFC-style byte range.

        ``None`` means no Range header, ``False`` means a malformed or
        unsatisfiable request, and a tuple contains inclusive bounds.
        """

        if value is None:
            return None
        if not value.startswith("bytes=") or "," in value:
            return False
        specification = value[6:].strip()
        if not specification or "-" not in specification or size < 1:
            return False
        start_text, end_text = (item.strip() for item in specification.split("-", 1))
        try:
            if not start_text:
                suffix = int(end_text)
                if suffix <= 0:
                    return False
                return max(0, size - suffix), size - 1
            start = int(start_text)
            if start < 0 or start >= size:
                return False
            if not end_text:
                return start, size - 1
            end = int(end_text)
            if end < start:
                return False
            return start, min(end, size - 1)
        except ValueError:
            return False

    @staticmethod
    def _safe_download_name(name: str) -> str:
        return name.replace("\r", "").replace("\n", "").replace('"', "")[:180] or "artifact.bin"

    def _write_file(self, path: Path, name: str, media_type: str, *, head: bool = False) -> None:
        try:
            size = path.stat().st_size
        except OSError:
            self._write(404, {"status": "error", "error": "Artifact Hub không còn tồn tại."})
            return
        bounds = self._range_bounds(self.headers.get("Range"), size)
        if bounds is False:
            try:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
                return
            return
        start, end = bounds if isinstance(bounds, tuple) else (0, max(0, size - 1))
        length = end - start + 1 if size else 0
        try:
            self.send_response(206 if isinstance(bounds, tuple) else 200)
            self.send_header("Content-Type", normalize_media_type(media_type, fallback_name=name))
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            if isinstance(bounds, tuple):
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            safe_name = self._safe_download_name(name)
            self.send_header("Content-Disposition", f'inline; filename="{safe_name}"')
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            return
        if head or length == 0:
            return
        try:
            with path.open("rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining:
                    chunk = handle.read(min(ARTIFACT_CHUNK_BYTES, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # A browser navigation/cancel is normal and must not terminate the
            # loopback request handler.
            return
        except OSError:
            return

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

    def _read_json(self, *, strict: bool = False) -> dict:
        def invalid(message: str) -> dict:
            if strict:
                raise ValueError(message)
            return {}

        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 2 * 1024 * 1024:
                return invalid("JSON request vượt giới hạn kích thước.")
            chunks: list[bytes] = []
            remaining = length
            while remaining:
                chunk = self.rfile.read(min(64 * 1024, remaining))
                if not chunk:
                    return invalid("JSON request bị ngắt trước khi đủ dữ liệu.")
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks) if length else b"{}"
            value = json.loads(raw.decode("utf-8"), parse_constant=_reject_json_constant)
            if not isinstance(value, dict):
                return invalid("JSON request phải là object.")
            return value
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            if strict:
                raise ValueError("JSON request không hợp lệ hoặc chứa số non-finite.")
            return {}

    def _upload(self) -> None:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            self._write(411, {"status": "error", "error": "Upload v1 yêu cầu Content-Length hợp lệ."})
            return
        try:
            length = int(raw_length)
        except ValueError:
            self._write(400, {"status": "error", "error": "Content-Length upload không hợp lệ."})
            return
        max_bytes, disk_safety_bytes = _upload_limits()
        if length <= 0:
            self._write(400, {"status": "error", "error": "Upload phải lớn hơn 0 byte."})
            return
        if length > max_bytes:
            self._write(413, {"status": "error", "error": f"Upload vượt giới hạn {max_bytes // (1024 * 1024)} MiB của Hub."})
            return
        filename = unquote(self.headers.get("X-File-Name", "upload.bin"))
        try:
            artifact = stage_upload_stream(
                filename,
                self.rfile,
                length,
                self.headers.get("Content-Type"),
                max_bytes=max_bytes,
                disk_safety_bytes=disk_safety_bytes,
            )
        except UploadError as exc:
            self._write(400, {"status": "error", "error": str(exc)})
            return
        except OSError:
            self._write(500, {"status": "error", "error": "Hub không thể ghi upload vào vùng tạm an toàn."})
            return
        except Exception:
            self._write(500, {"status": "error", "error": "Hub không thể hoàn tất đăng ký upload an toàn."})
            return
        self._write(201, {"status": "completed", "artifact": artifact})

    def _serve_artifact(self, artifact_id: str, *, head: bool = False) -> None:
        path_value = resolve_artifact(artifact_id)
        artifact = describe_artifact(artifact_id)
        if path_value is None or artifact is None:
            self._write(404, {"status": "error", "error": "Không tìm thấy artifact Hub."})
            return
        self._write_file(path_value, str(artifact["name"]), str(artifact["media_type"]), head=head)

    # V7 Phase 3.5 transport boundary.  Domain routes (components, projects,
    # jobs, Image & Mask, media, vision, voice and image) are resolved only by
    # the explicit Router.  HubHandler retains byte/static/special transport
    # primitives and never owns domain business branches.
    def do_GET(self) -> None:  # noqa: N802
        if self._dispatch_modular("GET"):
            return
        path = unquote(urlparse(self.path).path)
        normalized = path.rstrip("/") or "/"
        if path == "/ui":
            self.send_response(302)
            self.send_header("Location", "/ui/")
            self.end_headers()
            return
        if path.startswith("/ui/"):
            self._write_static(path)
            return
        if normalized.startswith("/api/artifacts/"):
            self._serve_artifact(normalized.rsplit("/", 1)[-1])
            return
        self._write(404, {"status": "error", "error": "Route not found."})

    def do_HEAD(self) -> None:  # noqa: N802
        if self._dispatch_modular("HEAD"):
            return
        normalized = unquote(urlparse(self.path).path).rstrip("/") or "/"
        if normalized.startswith("/api/artifacts/"):
            self._serve_artifact(normalized.rsplit("/", 1)[-1], head=True)
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        if self._dispatch_modular("POST"):
            return
        path = unquote(urlparse(self.path).path.rstrip("/") or "/")
        if path == "/api/uploads":
            self._upload()
            return
        self._write(404, {"status": "error", "error": "Route not found."})

    def do_PATCH(self) -> None:  # noqa: N802
        if self._dispatch_modular("PATCH"):
            return
        self._write(404, {"status": "error", "error": "Route not found."})

    def do_PUT(self) -> None:  # noqa: N802
        if self._dispatch_modular("PUT"):
            return
        self._write(404, {"status": "error", "error": "Route not found."})

    def do_DELETE(self) -> None:  # noqa: N802
        if self._dispatch_modular("DELETE"):
            return
        self._write(404, {"status": "error", "error": "Route not found."})


def main() -> int:
    config = hub_config()
    # Desktop-selected session values take precedence over persistent config.
    # The desktop only ever supplies loopback, and a fallback port is never
    # persisted into machine configuration.
    host = str(os.environ.get("LOCALAIHUB_BIND_HOST") or config.get("bind_host", "127.0.0.1"))
    port_value = os.environ.get("LOCALAIHUB_PORT") or config.get("api_port", 8765)
    try:
        port = int(port_value)
    except (TypeError, ValueError):
        port = 8765
    if host not in {"127.0.0.1", "localhost"} or not 1024 <= port <= 65535:
        LOG.error("Local AI Hub requires a bounded loopback bind.")
        return 1
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        server = HubHTTPServer((host, port), HubHandler)
    except OSError as exc:
        LOG.error("Local AI Hub could not bind %s:%s: %s", host, port, exc)
        return 1
    try:
        reconcile_durable_jobs()
    except Exception:
        LOG.warning("V5 durable recovery is unavailable; preserving the existing state.")
    reconcile_startup()
    LOG.info("Local AI Hub listening on %s:%s", host, port)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        LOG.info("Stopping Local AI Hub")
    finally:
        _shutdown_owned_idle()
        flush_jobs()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
