"""Explicit composition of all currently migrated domain route adapters."""

from __future__ import annotations

from .router import Router
from .routes import backups, components, creative, diagnostics, health, jobs, lifecycle, models, node_studio, projects, runtimes, settings, bootstrap, artifacts, workflows, image_mask, media, vision, voice, video, image, compatibility, comfyui


def build_router() -> Router:
    router = Router()
    for module in (health, bootstrap, settings, diagnostics, backups, components, models, runtimes, projects, creative, workflows, node_studio, jobs, artifacts, lifecycle, image_mask, media, vision, voice, video, image, compatibility, comfyui):
        module.register(router)
    return router


__all__ = ["build_router"]
