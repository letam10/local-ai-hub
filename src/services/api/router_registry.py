"""Explicit composition of all currently migrated domain route adapters."""

from __future__ import annotations

from .router import Router
from .routes import app_updates, backups, bootstrap, artifacts, comfyui, compatibility, component_v8, components, creative, diagnostics, health, image, image_mask, jobs, lifecycle, media, models, node_studio, productization, projects, runtimes, settings, updates, video, vision, voice, workflows


def build_router() -> Router:
    router = Router()
    for module in (health, bootstrap, settings, diagnostics, backups, component_v8, components, models, runtimes, projects, creative, workflows, node_studio, jobs, artifacts, lifecycle, image_mask, media, vision, voice, video, image, compatibility, comfyui, productization, updates, app_updates):
        module.register(router)
    return router


__all__ = ["build_router"]
