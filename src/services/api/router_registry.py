"""Explicit composition of all currently migrated domain route adapters."""

from __future__ import annotations

from .router import Router
from .routes import app_updates, backups, bootstrap, artifacts, capability_graph, comfyui, compatibility, component_lifecycle_v2, component_v8, components, creative, diagnostics, durable_job_engine_v2, feature_discovery_v2, health, image, image_mask, jobs, lifecycle, media, model_manager_v2, models, node_studio, productization, projects, resource_scheduler_v2, runtimes, settings, updates, video, vision, voice, workflows


def build_router() -> Router:
    router = Router()
    for module in (health, bootstrap, settings, diagnostics, backups, capability_graph, feature_discovery_v2, component_lifecycle_v2, component_v8, components, model_manager_v2, resource_scheduler_v2, durable_job_engine_v2, models, runtimes, projects, creative, workflows, node_studio, jobs, artifacts, lifecycle, image_mask, media, vision, voice, video, image, compatibility, comfyui, productization, updates, app_updates):
        module.register(router)
    return router


__all__ = ["build_router"]
