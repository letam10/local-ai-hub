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
from src.shared.paths.registry import ROOT
from src.shared.version import PRODUCT_VERSION

from .config import hub_config
from .core import capability_control_plane, component_statuses, get_job_or_error, health, prepare_owned_shutdown, submit_graph, submit_tool, tool_catalog
from .jobs import flush as flush_jobs
from .jobs import reconcile_startup
from .jobs import get_job, list_jobs
from .v5_productization import admit_durable_job, durable_jobs_snapshot, project_product_surface, reconcile_durable_jobs, resume_durable_job


LOG = logging.getLogger("local-ai-hub")
UI_ROOT = (ROOT / "src" / "ui").resolve()
WORKFLOW_ROOT = (ROOT / "workflows").resolve()
_BOOTSTRAP_CACHE_SECONDS = 5.0
_bootstrap_cache: tuple[float, dict] | None = None
_bootstrap_lock = threading.RLock()
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
    }
    with _bootstrap_lock:
        _bootstrap_cache = (now, payload)
    return payload


def _preset_summaries() -> list[dict]:
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

    def _creative(self, callback: object) -> None:
        """Map local creative-workspace validation errors to safe API replies."""

        try:
            payload = callback()  # type: ignore[operator]
        except KeyError:
            self._write(404, {"status": "error", "error": "Không tìm thấy tài nguyên Creative Workspace."})
        except ValueError as exc:
            self._write(400, {"status": "error", "error": str(exc)})
        else:
            self._write(200, payload)

    def _image_mask_studio(self, callback: object) -> None:
        """Map Studio validation/concurrency errors without exposing state paths."""

        try:
            payload = callback()  # type: ignore[operator]
        except StudioConflictError as exc:
            self._write(409, {
                "status": "conflict",
                "error": str(exc),
                "current_revision": exc.current_revision,
                "action": "Tải lại bản nháp server hoặc chọn snapshot trước khi ghi tiếp.",
            })
        except KeyError:
            self._write(404, {"status": "error", "error": "Không tìm thấy phiên hoặc layer Image & Mask Studio."})
        except ValueError as exc:
            self._write(400, {"status": "error", "error": str(exc)})
        else:
            self._write(200, payload)

    def _link_image_mask_studio_project(self, session_id: str, payload: object) -> None:
        """Complete a persisted Studio-to-project attachment intent safely."""

        try:
            prepared = image_mask_studio.prepare_project_attachment(session_id, payload)
        except StudioConflictError as exc:
            self._write(409, {"status": "conflict", "error": str(exc), "current_revision": exc.current_revision, "action": "Tải lại Studio trước khi liên kết project."})
            return
        except KeyError:
            self._write(404, {"status": "error", "error": "Không tìm thấy phiên Image & Mask Studio."})
            return
        except ValueError as exc:
            self._write(400, {"status": "error", "error": str(exc)})
            return
        attachment = prepared.get("attachment") if isinstance(prepared, dict) else None
        session = prepared.get("session") if isinstance(prepared, dict) else None
        if not isinstance(attachment, dict) or not isinstance(session, dict):
            self._write(500, {"status": "error", "error": "Hub không thể chuẩn bị liên kết project an toàn."})
            return
        if prepared.get("status") == "completed":
            self._write(200, {
                "status": "completed",
                "session": session,
                "project": None,
                "attachment": attachment,
                "idempotent": True,
            })
            return
        project_id = attachment.get("project_id")
        artifact_ids = attachment.get("artifacts")
        intent_id = attachment.get("intent_id")
        attachment_revision = attachment.get("revision")
        if (
            not isinstance(project_id, str)
            or not isinstance(artifact_ids, list)
            or not isinstance(intent_id, str)
            or not isinstance(attachment_revision, int)
            or isinstance(attachment_revision, bool)
        ):
            self._write(500, {"status": "error", "error": "Intent liên kết project không hợp lệ."})
            return
        public_attachment = {key: value for key, value in attachment.items() if key != "intent_id"}
        selected_ids = set(artifact_ids)
        layers = session.get("layers", [])
        mask_ids = [
            layer.get("artifact_id")
            for layer in layers
            if (
                isinstance(layer, dict)
                and layer.get("kind") == "mask"
                and isinstance(layer.get("artifact_id"), str)
                and layer["artifact_id"] in selected_ids
            )
        ]
        try:
            linked = project_manager.attach_image_mask_studio_revision(project_id, {
                "studio_id": session_id,
                "revision": attachment_revision,
                "source_artifact_id": session.get("source_artifact_id"),
                "artifact_ids": artifact_ids,
                "mask_artifact_ids": mask_ids,
            })
            completed = image_mask_studio.complete_project_attachment(
                session_id,
                project_id=project_id,
                artifact_ids=artifact_ids,
                intent_id=intent_id,
                expected_revision=attachment_revision,
            )
        except StudioConflictError as exc:
            self._write(409, {
                "status": "pending_project_attach",
                "error": str(exc),
                "session": session,
                "attachment": public_attachment,
                "current_revision": exc.current_revision,
                "action": "Studio đã thay đổi; tải lại snapshot server và thử lại liên kết project từ revision hiện tại.",
            })
            return
        except (KeyError, ValueError) as exc:
            # The intent is already durable.  Keep it for an explicit retry
            # rather than discarding a valid Studio draft after a project-side
            # limit or recovery error.
            self._write(409, {
                "status": "pending_project_attach",
                "error": str(exc),
                "session": session,
                "attachment": public_attachment,
                "action": "Khắc phục project đích rồi thử liên kết lại; Studio không mất bản nháp.",
            })
            return
        self._write(200, {"status": "completed", "session": completed.get("session"), "project": linked.get("project"), "attachment": public_attachment})

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        normalized = path.rstrip("/") or "/"
        if path == "/ui":
            self.send_response(302)
            self.send_header("Location", "/ui/")
            self.end_headers()
        elif path.startswith("/ui/"):
            self._write_static(path)
        elif normalized.startswith("/api/artifacts/"):
            artifact_id = normalized.rsplit("/", 1)[-1]
            self._serve_artifact(artifact_id)
        elif normalized in {"/", "/health"}:
            self._write(200, health(probe_gpu=False))
        elif normalized == "/api/bootstrap":
            self._write(200, _bootstrap_payload())
        elif normalized == "/api/components":
            from src.services.api.components import snapshot

            self._write(200, snapshot())
        elif normalized == "/api/modules":
            # Module Manager is a server-owned composition; the browser never
            # supplies a manifest or dependency path.
            control = capability_control_plane()
            plan = control.get("module_manager") if isinstance(control, dict) else {}
            self._write(200, plan if isinstance(plan, dict) else {"status": "unavailable", "execution": "not_run", "dry_run": True})
        elif normalized.startswith("/api/modules/"):
            module_id = normalized[len("/api/modules/") :].strip("/")
            control = capability_control_plane()
            plan = control.get("module_manager") if isinstance(control, dict) else {}
            modules = plan.get("modules") if isinstance(plan, dict) else []
            selected = next((item for item in modules if isinstance(item, dict) and item.get("id") == module_id), None)
            self._write(200 if selected else 404, {"status": "completed", "execution": "not_run", "dry_run": True, "module": selected} if selected else {"status": "error", "error": "unknown_module"})
        elif normalized.startswith("/api/components/plans/"):
            from src.services.api.components import lookup_plan

            plan_id = normalized[len("/api/components/plans/") :].strip("/")
            value = lookup_plan(plan_id)
            self._write(200 if value else 404, value or {"status": "error", "error": "unknown_component_plan"})
        elif normalized.startswith("/api/components/jobs/"):
            from src.services.api.components import lookup_job

            job_id = normalized[len("/api/components/jobs/") :].strip("/")
            value = lookup_job(job_id)
            self._write(200 if value else 404, value or {"status": "error", "error": "unknown_component_job"})
        elif normalized.startswith("/api/components/"):
            from src.services.api.components import detail

            component_id = normalized[len("/api/components/") :].strip("/")
            try:
                self._write(200, detail(component_id))
            except Exception:
                self._write(404, {"status": "error", "error": "unknown_component"})
        elif normalized == "/api/runtimes":
            from src.services.runtime_manager import RuntimeManager

            self._write(200, RuntimeManager().snapshot())
        elif normalized.startswith("/api/runtimes/"):
            from src.services.runtime_manager import RuntimeManager

            runtime_id = normalized[len("/api/runtimes/") :].strip("/")
            try:
                self._write(200, {"status": "completed", "runtime": RuntimeManager().verify(runtime_id)})
            except Exception:
                self._write(404, {"status": "error", "error": "unknown_runtime"})
        elif normalized.startswith("/api/models/"):
            from src.services.model_manager import ModelManager

            model_id = normalized[len("/api/models/") :].strip("/")
            try:
                self._write(200, {"status": "completed", "model": ModelManager().inspect(model_id)})
            except Exception:
                self._write(404, {"status": "error", "error": "unknown_model"})
        elif normalized == "/api/capabilities":
            self._write(200, capability_control_plane())
        elif normalized == "/api/workflow-library":
            self._write(200, _workflow_library_payload())
        elif normalized.startswith("/api/workflow-library/"):
            workflow_id = normalized[len("/api/workflow-library/") :].strip("/")
            result = _workflow_library_store().get_workflow(workflow_id)
            self._write(_workflow_http_status(result), result)
        elif normalized == "/tools":
            self._write(200, {"status": "completed", "tools": tool_catalog()})
        elif normalized == "/models":
            from src.services.storage_manager.overview import model_summary

            refresh = parse_qs(parsed.query).get("refresh", [""])[0].casefold() in {"1", "true"}
            self._write(200, {"status": "completed", "models": model_summary(force=refresh)})
        elif normalized == "/components":
            self._write(200, {"status": "completed", "components": component_statuses()})
        elif normalized in {"/jobs", "/api/jobs"}:
            query = parse_qs(parsed.query)
            limit = _bounded_int(query.get("limit", [None])[0], 200, minimum=1, maximum=500)
            self._write(200, {"status": "completed", "jobs": list_jobs(limit=limit)})
        elif normalized == "/api/durable-jobs":
            self._write(200, durable_jobs_snapshot())
        elif normalized.startswith("/jobs/"):
            self._write(*get_job_or_error(normalized.split("/", 2)[2]))
        elif normalized == "/api/dashboard":
            self._write(200, _bootstrap_payload())
        elif normalized == "/api/storage":
            from src.services.storage_manager.overview import storage_summary

            self._write(200, storage_summary())
        elif normalized == "/api/models":
            from src.services.storage_manager.overview import model_summary

            refresh = parse_qs(parsed.query).get("refresh", [""])[0].casefold() in {"1", "true"}
            self._write(200, {"status": "completed", "models": model_summary(force=refresh)})
        elif normalized == "/api/applications":
            self._write(200, {"status": "completed", "applications": applications()})
        elif normalized == "/api/lifecycle":
            self._write(200, _lifecycle_payload())
        elif normalized == "/api/comfyui/advanced":
            from src.modules.image_generation.backend.comfyui import health as comfy_health

            self._write(200, {"status": "completed", "comfyui": comfy_health()})
        elif normalized == "/api/comfyui/workflows":
            from src.modules.image_generation.backend.comfyui import list_bridge_workflows

            self._write(200, {"status": "completed", "workflows": list_bridge_workflows()})
        elif normalized.startswith("/api/comfyui/workflows/"):
            from src.modules.image_generation.backend.comfyui import load_bridge_workflow

            workflow_id = normalized.rsplit("/", 1)[-1]
            workflow = load_bridge_workflow(workflow_id)
            if workflow is None:
                self._write(404, {"status": "error", "error": "Không tìm thấy ComfyUI bridge workflow."})
            else:
                self._write(200, {"status": "completed", "workflow": workflow})
        elif normalized == "/api/settings":
            self._write(200, _settings_payload())
        elif normalized == "/api/creative/overview":
            self._creative(project_manager.overview)
        elif normalized == "/api/image-mask-studio/overview":
            query = parse_qs(parsed.query)
            self._image_mask_studio(lambda: image_mask_studio.overview(project_id=query.get("project", [None])[0]))
        elif normalized == "/api/image-mask-studio/preflight":
            self._image_mask_studio(image_mask_studio.preflight)
        elif normalized == "/api/image-mask-studio/sessions":
            query = parse_qs(parsed.query)
            self._image_mask_studio(lambda: image_mask_studio.overview(project_id=query.get("project", [None])[0]))
        elif normalized.startswith("/api/image-mask-studio/sessions/") and normalized.endswith("/compare"):
            session_id = normalized.split("/")[-2]
            query = parse_qs(parsed.query)
            self._image_mask_studio(lambda: self._creative_or_404(image_mask_studio.compare(
                session_id,
                before_snapshot_id=query.get("before", [None])[0],
                after_snapshot_id=query.get("after", [None])[0],
            )))
        elif normalized.startswith("/api/image-mask-studio/sessions/") and normalized.endswith("/export"):
            parts = normalized.strip("/").split("/")
            if len(parts) != 7 or parts[4] != "layers":
                self._write(404, {"status": "error", "error": "Route Image & Mask Studio không tìm thấy."})
            else:
                self._image_mask_studio(lambda: image_mask_studio.export_mask(parts[3], parts[5]))
        elif normalized.startswith("/api/image-mask-studio/sessions/"):
            session_id = normalized.rsplit("/", 1)[-1]
            self._image_mask_studio(lambda: self._creative_or_404(image_mask_studio.get_session(session_id)))
        elif normalized == "/api/projects":
            self._creative(project_manager.list_projects)
        elif normalized == "/api/assets":
            query = parse_qs(parsed.query)
            self._creative(lambda: project_manager.list_assets(
                query=str(query.get("query", [""])[0]),
                tag=str(query.get("tag", [""])[0]),
                favorite=str(query.get("favorite", [""])[0]).lower() in {"1", "true", "yes"},
                collection_id=str(query.get("collection", [""])[0]),
                project_id=str(query.get("project", [""])[0]),
            ))
        elif normalized == "/api/collections":
            self._creative(project_manager.list_collections)
        elif normalized == "/api/recipes":
            self._creative(project_manager.list_recipes)
        elif normalized == "/api/recipes/export-pack":
            recipe_ids = parse_qs(parsed.query).get("id", [])
            self._creative(lambda: project_manager.export_recipe_pack(recipe_ids or None))
        elif normalized == "/api/workflow-gallery":
            self._creative(project_manager.workflow_gallery)
        elif normalized.startswith("/api/projects/") and normalized.endswith("/compare"):
            self._creative(lambda: self._creative_or_404(project_manager.get_compare(normalized.split("/")[-2])))
        elif normalized.startswith("/api/projects/") and normalized.endswith("/export"):
            self._creative(lambda: project_manager.export_project(normalized.split("/")[-2]))
        elif normalized.startswith("/api/projects/") and normalized.endswith("/manifest"):
            project_id = normalized.split("/")[-2]
            result = project_manager.export_manifest(project_id)
            self._write(200 if result.get("accepted") else 404, result)
        elif normalized.startswith("/api/projects/") and normalized.endswith("/missing-artifacts"):
            project_id = normalized.split("/")[-2]
            result = project_manager.missing_artifact_state(project_id)
            self._write(200 if result.get("accepted") else 404, result)
        elif normalized.startswith("/api/projects/"):
            self._creative(lambda: self._creative_or_404(project_manager.get_project(normalized.rsplit("/", 1)[-1])))
        elif normalized.startswith("/api/recipes/"):
            self._creative(lambda: self._creative_or_404(project_manager.get_recipe(normalized.rsplit("/", 1)[-1])))
        elif normalized == "/api/node-studio/registry":
            from src.services.node_studio.registry import registry_payload

            scope = parse_qs(parsed.query).get("scope", [""])[0]
            self._write(200, registry_payload(scope if isinstance(scope, str) else None))
        elif normalized == "/api/node-studio/availability":
            from src.services.node_studio.registry import registry_payload

            scope = parse_qs(parsed.query).get("scope", [""])[0]
            payload = registry_payload(scope if isinstance(scope, str) else None)
            self._write(200, {
                "status": "completed",
                "contract_version": payload["contract_version"],
                "scope": payload["scope"],
                "availability": payload["availability"],
                "nodes": [
                    {
                        "type": item["type"],
                        "title": item["title"],
                        "status": item["status"],
                        "availability": item["availability"],
                    }
                    for item in payload["nodes"]
                ],
            })
        elif normalized == "/api/node-studio/presets":
            self._write(200, {"status": "completed", "presets": _preset_summaries()})
        elif normalized.startswith("/api/node-studio/presets/"):
            preset = _preset(normalized.rsplit("/", 1)[-1])
            if preset is None:
                self._write(404, {"status": "error", "error": "Không tìm thấy preset workflow Hub."})
            else:
                from src.services.node_studio.schema import validate_graph

                validation = validate_graph(preset)
                self._write(200, {"status": "completed", "graph": validation["graph"], "validation": {"valid": validation["valid"], "errors": validation["errors"]}})
        elif normalized.startswith("/api/node-studio/runs/"):
            from src.services.node_studio.state import graph_runs

            run = graph_runs.snapshot(normalized.rsplit("/", 1)[-1])
            if run is None:
                self._write(404, {"status": "error", "error": "Chưa có trạng thái Node Studio cho job này."})
            else:
                self._write(200, {"status": "completed", "run": run})
        elif normalized == "/api/settings/schema":
            from src.app_config.schema import SETTINGS_SCHEMA_VERSION, SETTINGS_SECTION_DEFAULTS
            self._write(200, {"status": "completed", "schema_version": SETTINGS_SCHEMA_VERSION, "defaults": SETTINGS_SECTION_DEFAULTS})
        elif normalized == "/api/diagnostics/snapshot":
            from src.services.diagnostics.center import diagnostics_center
            self._write(200, {"status": "completed", "snapshot": diagnostics_center.snapshot()})
        elif normalized == "/api/diagnostics/export":
            from src.services.diagnostics.center import diagnostics_center
            self._write(200, diagnostics_center.export_diagnostics_bundle())
        elif normalized in ("/api/backup/list", "/api/backup"):
            from src.services.backup_manager import BackupManager
            self._write(200, {"status": "completed", "backups": BackupManager().list_backups()})
        elif normalized == "/api/diagnostics/repair/recovery-drafts":
            from src.services.diagnostics.center import diagnostics_center
            forensic = diagnostics_center.recovery_forensic_state()
            self._write(200, {"status": "completed", "drafts": forensic.get("files", []), "recovery": forensic})
        elif normalized.startswith("/api/diagnostics/subsystem/"):
            from src.services.diagnostics.center import diagnostics_center
            subsystem = normalized.rsplit("/", 1)[-1]
            _DISPATCH = {
                "git_integrity": lambda: diagnostics_center.git_integrity_state(),
                "config_registry": lambda: diagnostics_center.config_registry_state(),
                "jobs_store": lambda: diagnostics_center.jobs_store_state(),
                "artifact_store": lambda: diagnostics_center.artifact_store_state(),
                "workflow_store": lambda: diagnostics_center.workflow_store_state(),
                "models_inventory": lambda: diagnostics_center.models_inventory(),
                "environments_inventory": lambda: diagnostics_center.environments_inventory(),
                "runtime_inventory": lambda: diagnostics_center.runtime_inventory(),
                "storage": lambda: diagnostics_center.storage_state(),
                "gpu": lambda: diagnostics_center.gpu_detection(),
                "latest_app_errors": lambda: diagnostics_center.latest_app_errors(),
                "recovery_forensic": lambda: diagnostics_center.recovery_forensic_state(),
            }
            if subsystem in _DISPATCH:
                self._write(200, {"status": "completed", "subsystem": subsystem, "data": _DISPATCH[subsystem]()})
            else:
                self._write(404, {"status": "error", "error": f"Không tìm thấy subsystem diagnostics '{subsystem}'."})
        elif normalized.startswith("/api/node-studio/drafts/"):
            from src.services.node_studio.state import draft_load
            scope = normalized.rsplit("/", 1)[-1]
            draft = draft_load(scope)
            if draft is None:
                self._write(404, {"status": "error", "error": "Chưa có draft autosave cho scope này.", "draft": None})
            else:
                self._write(200, {"status": "completed", "draft": draft})
        elif normalized == "/api/assets/search":
            query = parse_qs(parsed.query)
            q = str(query.get("query", [""])[0])
            tags = query.get("tag", [])
            fav_param = query.get("favorite", [None])[0]
            fav = fav_param.lower() in {"1", "true", "yes"} if fav_param is not None else None
            media_prefix = str(query.get("media_type", [""])[0])
            proj_id = str(query.get("project_id", [""])[0])
            sort_by = str(query.get("sort_by", ["created_at"])[0])
            sort_desc = str(query.get("sort_desc", ["true"])[0]).lower() in {"1", "true", "yes"}
            result = project_manager.search_assets(
                query=q,
                tags=tags if tags else None,
                favorite=fav,
                media_type_prefix=media_prefix,
                project_id=proj_id,
                sort_by=sort_by,
                sort_desc=sort_desc,
            )
            self._write(200, result)
        elif normalized.startswith("/api/artifacts/") and normalized.endswith("/status"):
            artifact_id = normalized.split("/")[-2]
            result = project_manager.get_artifact_status(artifact_id)
            self._write(200 if result.get("found") else 404, result)
        else:
            self._write(404, {"status": "error", "error": "Route not found."})

    def do_HEAD(self) -> None:  # noqa: N802
        path = unquote(urlparse(self.path).path)
        normalized = path.rstrip("/") or "/"
        if normalized.startswith("/api/artifacts/"):
            self._serve_artifact(normalized.rsplit("/", 1)[-1], head=True)
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    @staticmethod
    def _creative_or_404(payload: object) -> object:
        if payload is None:
            raise KeyError("creative")
        return payload

    def do_POST(self) -> None:  # noqa: N802
        path = unquote(urlparse(self.path).path.rstrip("/") or "/")
        if path.startswith("/api/components/"):
            self._component_post(path)
            return
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
            from src.modules.image_generation.backend.comfyui import shutdown_owned_idle

            self._write(200, shutdown_owned_idle())
            return
        if path == "/api/lifecycle/prepare-close":
            self._write(*prepare_owned_shutdown())
            return
        if path == "/api/lifecycle/jobs/cancel-and-wait":
            request = self._read_json()
            timeout = _bounded_int(request.get("timeout_seconds"), 12, minimum=1, maximum=60)
            ok, message = job_manager.cancel_all_and_wait(timeout)
            self._write(200 if ok else 409, {"status": "completed" if ok else "timeout", "message": message})
            return
        if path == "/api/comfyui/advanced/start":
            from src.modules.image_generation.backend.comfyui import start_advanced

            self._write(*start_advanced())
            return
        if path == "/api/storage/scan":
            from src.services.storage_manager.overview import storage_summary

            self._write(200, storage_summary(force=True))
            return
        if path == "/api/settings":
            from src.app_config.settings_service import SettingsPersistence
            try:
                patch_data = self._read_json(strict=True)
            except ValueError as exc:
                self._write(400, {"status": "invalid", "error": str(exc)})
                return
            expected_rev = patch_data.pop("expected_revision", None)
            result = SettingsPersistence().save(patch_data, expected_revision=expected_rev)
            status_code = 200 if result.get("accepted") else (409 if result.get("status") == "conflict" else 400)
            self._write(status_code, result)
            return
        if path == "/api/settings/reset":
            from src.app_config.settings_service import SettingsPersistence
            req = self._read_json()
            section = req.get("section")
            if section:
                result = SettingsPersistence().reset_section(str(section))
            else:
                from src.app_config.schema import SETTINGS_SECTION_DEFAULTS
                result = SettingsPersistence().save(SETTINGS_SECTION_DEFAULTS)
            self._write(200 if result.get("accepted") else 400, result)
            return
        if path == "/api/backup/create":
            from src.services.backup_manager import BackupManager
            result = BackupManager().create_backup()
            self._write(201 if result.get("accepted") else 500, result)
            return
        if path == "/api/backup/inspect":
            from src.services.backup_manager import BackupManager
            req = self._read_json()
            backup_id = req.get("backup_id") or req.get("backup_path", "")
            result = BackupManager().inspect_backup(backup_id)
            self._write(200 if result.get("valid") else 400, result)
            return
        if path == "/api/backup/plan":
            from src.services.backup_manager import BackupManager
            req = self._read_json()
            backup_id = req.get("backup_id") or req.get("backup_path", "")
            result = BackupManager().plan_restore(backup_id)
            self._write(200 if result.get("accepted") else 400, result)
            return
        if path == "/api/backup/apply":
            from src.services.backup_manager import BackupManager
            req = self._read_json()
            plan_id = req.get("plan_id") or req.get("plan", {}).get("plan_id", "")
            confirmed = bool(req.get("confirmed", False))
            result = BackupManager().apply_restore(plan_id, confirmed=confirmed)
            status_code = 200 if result.get("accepted") else (409 if result.get("status") == "conflict" or result.get("code") == 409 else 400)
            self._write(status_code, result)
            return
        if path.startswith("/api/node-studio/drafts/"):
            from src.services.node_studio.state import draft_persist
            scope = path.rsplit("/", 1)[-1]
            req = self._read_json()
            graph = req.get("graph", {})
            result = draft_persist(scope, graph)
            self._write(200 if result.get("accepted") else 400, result)
            return
        if path == "/api/diagnostics/repair/verify-config":
            from src.services.diagnostics.center import diagnostics_center
            self._write(200, {"status": "completed", "result": diagnostics_center.config_registry_state()})
            return
        if path == "/api/diagnostics/repair/inspect-recovery":
            from src.services.diagnostics.center import diagnostics_center
            self._write(200, {"status": "completed", "result": diagnostics_center.recovery_forensic_state()})
            return
        if path == "/api/diagnostics/repair/clear-recovery-drafts":
            from src.services.node_studio.state import draft_clear
            req = self._read_json()
            scopes = req.get("scopes")
            confirmed = bool(req.get("confirmed", False))
            if not confirmed:
                self._write(400, {"status": "unconfirmed", "error": "Xóa bản nháp phục hồi yêu cầu xác nhận confirmed=True."})
                return
            if not isinstance(scopes, list) or not scopes:
                self._write(400, {"status": "invalid", "error": "Danh sách scopes cần xóa không hợp lệ hoặc rỗng."})
                return
            cleared = []
            for s in scopes:
                if isinstance(s, str) and s:
                    draft_clear(s)
                    cleared.append(s)
            self._write(200, {"status": "completed", "cleared": cleared, "message": f"Đã dọn dẹp {len(cleared)} bản nháp phục hồi."})
            return
        if path.startswith("/api/workflow-library"):
            try:
                request = self._read_json(strict=True)
            except ValueError as exc:
                self._write(400, {"status": "invalid", "error": str(exc)})
                return
            store = _workflow_library_store()
            if path == "/api/workflow-library":
                expected_revision = request.get("expected_revision")
                if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 0):
                    self._write(400, {"status": "invalid", "error": "expected_revision must be a non-negative integer."})
                    return
                result = store.save_workflow(request.get("workflow"), expected_revision=expected_revision)
            elif path == "/api/workflow-library/import":
                expected_revision = request.get("expected_revision")
                if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 0):
                    self._write(400, {"status": "invalid", "error": "expected_revision must be a non-negative integer."})
                    return
                content = request.get("content", "")
                if not isinstance(content, str) or len(content.encode("utf-8")) > 2 * 1024 * 1024:
                    self._write(400, {"status": "invalid", "error": "Workflow Library import content is bounded UTF-8 text."})
                    return
                result = store.import_json(content, expected_revision=expected_revision)
            elif path == "/api/workflow-library/migration/plan":
                result = store.plan_migration(request.get("entries", []))
            elif path == "/api/workflow-library/migration/confirm":
                expected_revision = request.get("expected_revision")
                if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 0):
                    self._write(400, {"status": "invalid", "error": "expected_revision must be a non-negative integer."})
                    return
                result = store.confirm_migration(request.get("entries", []), expected_revision=expected_revision)
            else:
                self._write(404, {"status": "error", "error": "Workflow Library route not found."})
                return
            self._write(_workflow_http_status(result), result)
            return
        if path.startswith("/api/durable-jobs/") and path.endswith("/resume"):
            job_id = path[len("/api/durable-jobs/") : -len("/resume")].strip("/")
            result = resume_durable_job(job_id)
            self._write(_workflow_http_status(result), result)
            return
        if path == "/api/durable-jobs":
            try:
                request = self._read_json(strict=True)
            except ValueError:
                self._write(400, {
                    "status": "invalid",
                    "execution": "not_run",
                    "dry_run": True,
                    "error": {
                        "code": "INVALID_JOB_SPEC",
                        "action": "Create a new allowlisted media.video_grade.v1 job.",
                    },
                })
                return
            result = admit_durable_job(request)
            self._write(_durable_admission_http_status(result), result)
            return
        if path == "/api/projects":
            self._creative(lambda: project_manager.create_project(self._read_json()))
            return
        if path == "/api/image-mask-studio/sessions":
            self._image_mask_studio(lambda: image_mask_studio.create_session(self._read_json(strict=True)))
            return
        if path.startswith("/api/image-mask-studio/sessions/"):
            parts = path.strip("/").split("/")
            if len(parts) >= 4:
                session_id = parts[3]
                suffix = parts[4:]
                if suffix == ["undo"]:
                    self._image_mask_studio(lambda: image_mask_studio.undo(session_id, self._read_json(strict=True)))
                    return
                if suffix == ["redo"]:
                    self._image_mask_studio(lambda: image_mask_studio.redo(session_id, self._read_json(strict=True)))
                    return
                if suffix == ["save"]:
                    self._image_mask_studio(lambda: image_mask_studio.save(session_id, self._read_json(strict=True)))
                    return
                if suffix == ["link-project"]:
                    try:
                        link_payload = self._read_json(strict=True)
                    except ValueError as exc:
                        self._write(400, {"status": "error", "error": str(exc)})
                    else:
                        self._link_image_mask_studio_project(session_id, link_payload)
                    return
                if suffix == ["layers"]:
                    self._image_mask_studio(lambda: image_mask_studio.add_layer(session_id, self._read_json(strict=True)))
                    return
                if suffix == ["masks", "import"]:
                    self._image_mask_studio(lambda: image_mask_studio.import_mask(session_id, self._read_json(strict=True)))
                    return
                if suffix == ["presets"]:
                    self._image_mask_studio(lambda: image_mask_studio.capture_preset(session_id, self._read_json(strict=True)))
                    return
                if len(suffix) == 3 and suffix[0] == "layers" and suffix[2] == "operations":
                    self._image_mask_studio(lambda: image_mask_studio.apply_mask_operation(session_id, suffix[1], self._read_json(strict=True)))
                    return
                if len(suffix) == 3 and suffix[0] == "layers" and suffix[2] == "move":
                    self._image_mask_studio(lambda: image_mask_studio.move_layer(session_id, suffix[1], self._read_json(strict=True)))
                    return
                if len(suffix) == 3 and suffix[0] == "layers" and suffix[2] == "remove":
                    self._image_mask_studio(lambda: image_mask_studio.remove_layer(session_id, suffix[1], self._read_json(strict=True)))
                    return
                if len(suffix) == 3 and suffix[0] == "snapshots" and suffix[2] == "restore":
                    self._image_mask_studio(lambda: image_mask_studio.restore_snapshot(session_id, suffix[1], self._read_json(strict=True)))
                    return
                if len(suffix) == 3 and suffix[0] == "presets" and suffix[2] == "apply":
                    self._image_mask_studio(lambda: image_mask_studio.apply_preset(session_id, suffix[1], self._read_json(strict=True)))
                    return
        if path == "/api/projects/import":
            self._creative(lambda: project_manager.import_project(self._read_json()))
            return
        if path.startswith("/api/projects/") and path.endswith("/archive"):
            self._creative(lambda: project_manager.archive_project(path.split("/")[-2], archived=True))
            return
        if path.startswith("/api/projects/") and path.endswith("/restore"):
            self._creative(lambda: project_manager.archive_project(path.split("/")[-2], archived=False))
            return
        if path.startswith("/api/projects/") and path.endswith("/assets"):
            self._creative(lambda: project_manager.add_project_asset(path.split("/")[-2], self._read_json()))
            return
        if path.startswith("/api/projects/") and path.endswith("/compare"):
            self._creative(lambda: project_manager.update_compare(path.split("/")[-2], self._read_json()))
            return
        if path == "/api/collections":
            self._creative(lambda: project_manager.create_collection(self._read_json()))
            return
        if path == "/api/recipes":
            self._creative(lambda: project_manager.create_recipe(self._read_json()))
            return
        if path == "/api/recipes/import-pack":
            self._creative(lambda: project_manager.import_recipe_pack(self._read_json()))
            return
        if path.startswith("/api/recipes/") and path.endswith("/apply"):
            self._creative(lambda: project_manager.apply_recipe(path.split("/")[-2], self._read_json()))
            return
        if path == "/api/node-studio/validate":
            from src.services.node_studio.schema import validate_graph

            request = self._read_json()
            validation = validate_graph(request.get("graph"), require_runnable=bool(request.get("require_runnable")))
            self._write(200, {"status": "completed", "validation": validation})
            return
        if path == "/api/node-studio/dirty":
            from src.services.node_studio.schema import downstream_nodes

            request = self._read_json()
            self._write(200, {"status": "completed", **downstream_nodes(request.get("graph"), request.get("changed_node_ids"))})
            return
        if path == "/api/node-studio/run":
            request = self._read_json()
            self._write(*submit_graph(request.get("graph"), draft=bool(request.get("draft"))))
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

    def _component_post(self, path: str) -> None:
        """Handle typed component plans without accepting filesystem input."""

        from src.services.api import components as component_api

        try:
            request = self._read_json(strict=True)
        except ValueError as exc:
            self._write(400, {"status": "invalid", "error": str(exc), "execution": "not_run"})
            return

        try:
            if path == "/api/components/install/plan":
                allowed = {"component_id", "component_type", "variant"}
                if set(request) - allowed or not isinstance(request.get("component_id"), str) or not isinstance(request.get("component_type"), str) or (request.get("variant") is not None and not isinstance(request.get("variant"), str)):
                    raise ValueError("component_plan_payload_invalid")
                result = component_api.plan_install(request["component_id"], component_type=request["component_type"], variant=request.get("variant"))
                self._write(200, result)
                return
            if path.startswith("/api/components/plans/") and path.endswith("/confirm"):
                plan_id = path[len("/api/components/plans/") : -len("/confirm")].strip("/")
                if set(request) - {"confirmed"} or type(request.get("confirmed")) is not bool:
                    raise ValueError("component_confirmation_invalid")
                result = component_api.confirm_install(plan_id, confirmed=request["confirmed"])
                self._write(200 if result.get("status") not in {"invalid", "error", "conflict"} else 409, result)
                return
            if path == "/api/components/install/confirm":
                if set(request) - {"plan_id", "confirmed"} or not isinstance(request.get("plan_id"), str) or type(request.get("confirmed")) is not bool:
                    raise ValueError("component_confirmation_invalid")
                result = component_api.confirm_install(request["plan_id"], confirmed=request["confirmed"])
                self._write(200 if result.get("status") not in {"invalid", "error", "conflict"} else 409, result)
                return
            if path == "/api/components/install/apply":
                if set(request) - {"plan_id", "confirmed"} or not isinstance(request.get("plan_id"), str) or type(request.get("confirmed")) is not bool:
                    raise ValueError("component_apply_payload_invalid")
                result = component_api.confirm_install(request["plan_id"], confirmed=request["confirmed"])
                self._write(200 if result.get("status") not in {"invalid", "error", "conflict"} else 409, result)
                return
            if path == "/api/components/import/plan":
                if set(request) - {"selection_id", "mode"} or not isinstance(request.get("selection_id"), str) or not isinstance(request.get("mode"), str):
                    raise ValueError("component_import_payload_invalid")
                self._write(200, component_api.plan_import(request["selection_id"], mode=request["mode"]))
                return
            if path == "/api/components/import/confirm":
                if set(request) - {"plan_id", "confirmed"} or not isinstance(request.get("plan_id"), str) or type(request.get("confirmed")) is not bool:
                    raise ValueError("component_confirmation_invalid")
                result = component_api.confirm_import(request["plan_id"], confirmed=request["confirmed"])
                self._write(200 if result.get("status") not in {"invalid", "error", "conflict"} else 409, result)
                return
            if path == "/api/components/verify/plan":
                if set(request) - {"component_id", "component_type"} or not isinstance(request.get("component_id"), str) or not isinstance(request.get("component_type"), str):
                    raise ValueError("component_verify_payload_invalid")
                self._write(200, component_api.plan_verify(request["component_id"], component_type=request["component_type"]))
                return
            if path == "/api/components/maintenance/plan":
                if set(request) - {"component_id", "action"} or not isinstance(request.get("component_id"), str) or not isinstance(request.get("action"), str):
                    raise ValueError("component_maintenance_payload_invalid")
                self._write(200, component_api.plan_maintenance(request["component_id"], action=request["action"]))
                return
            for action in ("repair", "update", "uninstall"):
                if path == f"/api/components/{action}/plan":
                    if set(request) - {"component_id"} or not isinstance(request.get("component_id"), str):
                        raise ValueError("component_maintenance_payload_invalid")
                    self._write(200, component_api.plan_maintenance(request["component_id"], action=action))
                    return
            if path.startswith("/api/components/maintenance/") and path.endswith("/confirm"):
                plan_id = path[len("/api/components/maintenance/") : -len("/confirm")].strip("/")
                if set(request) - {"confirmed"} or type(request.get("confirmed")) is not bool:
                    raise ValueError("component_confirmation_invalid")
                result = component_api.confirm_maintenance(plan_id, confirmed=request["confirmed"])
                self._write(200 if result.get("status") not in {"invalid", "error", "conflict"} else 409, result)
                return
            if path.startswith("/api/components/jobs/") and path.endswith("/cancel"):
                if request:
                    raise ValueError("component_cancel_payload_invalid")
                job_id = path[len("/api/components/jobs/") : -len("/cancel")].strip("/")
                result = component_api.component_installer().cancel_job(job_id)
                self._write(202 if result.get("status") == "cancelling" else 409, result)
                return
            self._write(404, {"status": "error", "error": "Component route not found."})
        except Exception as exc:
            status, payload = component_api.handle_error(exc)
            self._write(status, payload)

    def do_DELETE(self) -> None:  # noqa: N802
        path = unquote(urlparse(self.path).path.rstrip("/") or "/")
        if path.startswith("/api/node-studio/drafts/"):
            from src.services.node_studio.state import draft_clear
            scope = path.rsplit("/", 1)[-1]
            draft_clear(scope)
            self._write(200, {"status": "completed", "scope": scope})
            return
        prefix = "/api/workflow-library/"
        if not path.startswith(prefix) or not path[len(prefix) :].strip("/"):
            self._write(404, {"status": "error", "error": "Workflow Library route not found."})
            return
        workflow_id = path[len(prefix) :].strip("/")
        try:
            request = self._read_json(strict=True)
        except ValueError as exc:
            self._write(400, {"status": "invalid", "error": str(exc)})
            return
        expected_revision = request.get("expected_revision")
        if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 0):
            self._write(400, {"status": "invalid", "error": "expected_revision must be a non-negative integer."})
            return
        result = _workflow_library_store().delete_workflow(workflow_id, expected_revision=expected_revision)
        self._write(_workflow_http_status(result), result)

    def do_PATCH(self) -> None:  # noqa: N802
        path = unquote(urlparse(self.path).path.rstrip("/") or "/")
        if path == "/api/settings":
            from src.app_config.settings_service import SettingsPersistence
            try:
                patch_data = self._read_json(strict=True)
            except ValueError as exc:
                self._write(400, {"status": "invalid", "error": str(exc)})
                return
            expected_rev = patch_data.pop("expected_revision", None)
            result = SettingsPersistence().save(patch_data, expected_revision=expected_rev)
            status_code = 200 if result.get("accepted") else (409 if result.get("status") == "conflict" else 400)
            self._write(status_code, result)
            return
        self._write(404, {"status": "error", "error": "Route not found."})

    def do_PUT(self) -> None:  # noqa: N802
        path = unquote(urlparse(self.path).path.rstrip("/") or "/")
        if path.startswith("/api/image-mask-studio/sessions/"):
            parts = path.strip("/").split("/")
            if len(parts) == 4:
                self._image_mask_studio(lambda: image_mask_studio.update_session(parts[3], self._read_json(strict=True)))
                return
            if len(parts) == 6 and parts[4] == "layers":
                self._image_mask_studio(lambda: image_mask_studio.update_layer(parts[3], parts[5], self._read_json(strict=True)))
                return
            self._write(404, {"status": "error", "error": "Route Image & Mask Studio không tìm thấy."})
            return
        if path.startswith("/api/projects/"):
            self._creative(lambda: project_manager.update_project(path.rsplit("/", 1)[-1], self._read_json()))
            return
        if path.startswith("/api/assets/"):
            self._creative(lambda: project_manager.update_asset(path.rsplit("/", 1)[-1], self._read_json()))
            return
        if path.startswith("/api/collections/"):
            self._creative(lambda: project_manager.update_collection(path.rsplit("/", 1)[-1], self._read_json()))
            return
        if path.startswith("/api/recipes/"):
            self._creative(lambda: project_manager.update_recipe(path.rsplit("/", 1)[-1], self._read_json()))
            return
        if not path.startswith("/api/comfyui/workflows/"):
            self._write(404, {"status": "error", "error": "Route not found."})
            return
        from src.modules.image_generation.backend.comfyui import save_bridge_workflow

        workflow_id = path.rsplit("/", 1)[-1]
        self._write(*save_bridge_workflow(workflow_id, self._read_json()))


def main() -> int:
    config = hub_config()
    host = str(config.get("bind_host", "127.0.0.1"))
    port = int(config.get("api_port", 8765))
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
