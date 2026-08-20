"""Canonical route ownership metadata and transitional compatibility entries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class RouteMetadata:
    route_id: str
    method: str
    path: str
    domain: str
    owner: str
    service: str
    streaming: bool = False
    body_limit: str = "json_default"
    status_codes: tuple[int, ...] = (200, 400, 404, 409, 500)
    transport: str = "router"

    def as_dict(self) -> dict[str, object]:
        return {
            "route_id": self.route_id, "method": self.method, "path": self.path,
            "domain": self.domain, "owner": self.owner, "service": self.service,
            "streaming": self.streaming, "body_limit": self.body_limit,
            "status_codes": list(self.status_codes), "transport": self.transport,
        }


def legacy_routes() -> tuple[RouteMetadata, ...]:
    """Routes intentionally retained in HubHandler during the strangler phase."""

    rows: list[tuple[str, str, str, str, bool, str]] = [
        ("ui.redirect", "GET", "/ui", "static", False, "static_transport"),
        ("ui.static", "GET", "/ui/{asset}", "static", False, "static_transport"),
        ("artifacts.get", "GET", "/api/artifacts/{artifact_id}", "artifacts", True, "artifact_store"),
        ("artifacts.head", "HEAD", "/api/artifacts/{artifact_id}", "artifacts", True, "artifact_store"),
        ("uploads.stream", "POST", "/api/uploads", "uploads", True, "artifact_store"),
        ("storage.legacy", "GET", "/api/storage", "storage", False, "storage_manager"),
        ("storage.models_compat", "GET", "/models", "models", False, "storage_manager"),
        ("jobs.compat_list", "GET", "/jobs", "jobs", False, "job_manager"),
        ("jobs.cancel", "POST", "/jobs/{job_id}/cancel", "jobs", False, "job_manager"),
        ("jobs.resume", "POST", "/jobs/{job_id}/resume", "jobs", False, "job_manager"),
        ("jobs.tool_submit", "POST", "/api/jobs/{tool}", "jobs", False, "job_manager"),
        ("applications.list", "GET", "/api/applications", "applications", False, "runtime_registry"),
        ("applications.launch", "POST", "/api/applications/{application_id}/launch", "applications", False, "runtime_registry"),
        ("storage.scan", "POST", "/api/storage/scan", "storage", False, "storage_manager"),
        ("comfy.health", "GET", "/api/comfyui/advanced", "components", False, "comfyui"),
        ("comfy.start", "POST", "/api/comfyui/advanced/start", "components", False, "comfyui"),
        ("comfy.workflows", "GET", "/api/comfyui/workflows", "components", False, "comfyui"),
        ("comfy.workflow", "GET", "/api/comfyui/workflows/{workflow_id}", "components", False, "comfyui"),
        ("image_mask.overview", "GET", "/api/image-mask-studio/overview", "image_mask_studio", False, "image_mask_studio"),
        ("image_mask.preflight", "GET", "/api/image-mask-studio/preflight", "image_mask_studio", False, "image_mask_studio"),
        ("image_mask.sessions", "GET", "/api/image-mask-studio/sessions", "image_mask_studio", False, "image_mask_studio"),
        ("image_mask.session", "GET", "/api/image-mask-studio/sessions/{session_id}", "image_mask_studio", False, "image_mask_studio"),
        ("image_mask.compare", "GET", "/api/image-mask-studio/sessions/{session_id}/compare", "image_mask_studio", False, "image_mask_studio"),
        ("image_mask.export", "GET", "/api/image-mask-studio/sessions/{session_id}/layers/{layer_id}/export", "image_mask_studio", True, "image_mask_studio"),
        ("image_mask.create", "POST", "/api/image-mask-studio/sessions", "image_mask_studio", False, "image_mask_studio"),
        ("image_mask.mutate", "POST", "/api/image-mask-studio/sessions/{session_id}/{action}", "image_mask_studio", False, "image_mask_studio"),
        ("image_mask.layer_mutate", "POST", "/api/image-mask-studio/sessions/{session_id}/layers/{layer_id}/{action}", "image_mask_studio", False, "image_mask_studio"),
        ("image_mask.put_session", "PUT", "/api/image-mask-studio/sessions/{session_id}", "image_mask_studio", False, "image_mask_studio"),
        ("image_mask.put_layer", "PUT", "/api/image-mask-studio/sessions/{session_id}/layers/{layer_id}", "image_mask_studio", False, "image_mask_studio"),
        ("projects.compare", "GET", "/api/projects/{project_id}/compare", "projects", False, "project_manager"),
        ("recipes.detail", "GET", "/api/recipes/{recipe_id}", "creative", False, "project_manager"),
        ("assets.search", "GET", "/api/assets/search", "creative", False, "project_manager"),
        ("node.runs", "GET", "/api/node-studio/runs/{run_id}", "node_studio", False, "node_studio"),
        ("node.drafts", "GET", "/api/node-studio/drafts/{scope}", "node_studio", False, "node_studio"),
        ("node.drafts.save", "POST", "/api/node-studio/drafts/{scope}", "node_studio", False, "node_studio"),
        ("node.drafts.delete", "DELETE", "/api/node-studio/drafts/{scope}", "node_studio", False, "node_studio"),
        ("recipes.export_pack", "GET", "/api/recipes/export-pack", "creative", True, "project_manager"),
        ("projects.archive", "POST", "/api/projects/{project_id}/archive", "projects", False, "project_manager"),
        ("projects.restore", "POST", "/api/projects/{project_id}/restore", "projects", False, "project_manager"),
        ("projects.assets.add", "POST", "/api/projects/{project_id}/assets", "projects", False, "project_manager"),
        ("projects.compare.update", "POST", "/api/projects/{project_id}/compare", "projects", False, "project_manager"),
        ("collections.create", "POST", "/api/collections", "creative", False, "project_manager"),
        ("recipes.create", "POST", "/api/recipes", "creative", False, "project_manager"),
        ("recipes.import_pack", "POST", "/api/recipes/import-pack", "creative", False, "project_manager"),
        ("recipes.apply", "POST", "/api/recipes/{recipe_id}/apply", "creative", False, "project_manager"),
        ("projects.update", "PUT", "/api/projects/{project_id}", "projects", False, "project_manager"),
        ("assets.update", "PUT", "/api/assets/{asset_id}", "creative", False, "project_manager"),
        ("collections.update", "PUT", "/api/collections/{collection_id}", "creative", False, "project_manager"),
        ("recipes.update", "PUT", "/api/recipes/{recipe_id}", "creative", False, "project_manager"),
        ("comfy.workflow.save", "PUT", "/api/comfyui/workflows/{workflow_id}", "components", False, "comfyui"),
        ("creative.project_delete", "DELETE", "/api/projects/{project_id}", "projects", False, "project_manager"),
        ("media.probe", "POST", "/media/probe", "media", False, "job_manager"),
        ("media.probe_compat", "POST", "/probe_media", "media", False, "job_manager"),
        ("media.run", "POST", "/api/media/run", "media", False, "job_manager"),
        ("vision.parse", "POST", "/vision/ui/parse", "vision", False, "job_manager"),
        ("vision.detect", "POST", "/vision/detect", "vision", False, "job_manager"),
        ("vision.ground", "POST", "/vision/ground", "vision", False, "job_manager"),
        ("vision.segment", "POST", "/vision/segment", "vision", False, "job_manager"),
        ("vision.segment_box", "POST", "/vision/segment-box", "vision", False, "job_manager"),
        ("vision.segment_points", "POST", "/vision/segment-points", "vision", False, "job_manager"),
        ("vision.track", "POST", "/vision/track", "vision", False, "job_manager"),
        ("ocr.parse", "POST", "/ocr/parse", "vision", False, "job_manager"),
        ("speech.transcribe", "POST", "/speech/transcribe", "speech", False, "job_manager"),
        ("video.subtitle", "POST", "/video/subtitle", "video", False, "job_manager"),
        ("video.anime", "POST", "/video/upscale/anime", "video", False, "job_manager"),
        ("image.flux", "POST", "/api/image/flux", "image", False, "job_manager"),
        ("image.qwen", "POST", "/api/image/qwen", "image", False, "job_manager"),
        ("voice.tts", "POST", "/voice/tts", "voice", False, "job_manager"),
        ("voice.design", "POST", "/voice/design", "voice", False, "job_manager"),
        ("voice.clone", "POST", "/voice/clone", "voice", False, "job_manager"),
        ("voice.convert", "POST", "/voice/convert", "voice", False, "job_manager"),
    ]
    return tuple(RouteMetadata(
        route_id=route_id, method=method, path=path, domain=domain,
        owner="src/services/api/api_server.py", service=service,
        streaming=streaming, body_limit="upload_stream" if streaming else "json_default", transport="legacy",
    ) for route_id, method, path, domain, streaming, service in rows)


def validate_metadata(rows: Iterable[RouteMetadata]) -> None:
    seen_route: set[str] = set()
    seen_key: set[tuple[str, str]] = set()
    for row in rows:
        if row.route_id in seen_route:
            raise ValueError(f"duplicate_route_id:{row.route_id}")
        key = (row.method.upper(), row.path)
        if key in seen_key:
            raise ValueError(f"duplicate_route:{row.method}:{row.path}")
        seen_route.add(row.route_id)
        seen_key.add(key)


__all__ = ["RouteMetadata", "legacy_routes", "validate_metadata"]
