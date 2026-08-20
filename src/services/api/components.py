"""COMPATIBILITY façade for the Phase 2 Component Manager routes.

The modular route adapter imports this module instead of exposing ModelManager or
RuntimeManager internals.  All plans are server-owned and browser payloads
contain only fixed IDs, variants and opaque plan/selection identifiers.

This module remains intentionally small and keeps existing imports stable;
new transport registration lives in ``src.services.api.routes.components``.
"""

from __future__ import annotations

from collections.abc import Mapping
import re
import threading
from typing import Any

from src.services.component_installer import ComponentInstaller, InstallPlanError, SelectionError
from src.services.component_installer.bundle import ComponentBundleService


_lock = threading.RLock()
_manager: ComponentInstaller | None = None
_bundle: ComponentBundleService | None = None


def component_installer() -> ComponentInstaller:
    global _manager
    with _lock:
        if _manager is None:
            _manager = ComponentInstaller()
        return _manager


def component_bundle_service() -> ComponentBundleService:
    global _bundle
    with _lock:
        if _bundle is None:
            _bundle = ComponentBundleService(component_installer())
        return _bundle


def _public_error(code: str, *, plan_id: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "invalid",
        "execution": "not_run",
        "dry_run": True,
        "error": code,
    }
    if plan_id:
        result["plan_id"] = plan_id
    return result


def snapshot() -> dict[str, Any]:
    return component_installer().snapshot()


def detail(component_id: str) -> dict[str, Any]:
    manager = component_installer()
    value = manager.detail(component_id)
    return value


def plan_install(component_id: str, *, component_type: str, variant: str | None = None) -> dict[str, Any]:
    return component_installer().plan_install(component_id, component_type=component_type, variant=variant)


def confirm_install(plan_id: str, *, confirmed: bool) -> dict[str, Any]:
    return component_installer().confirm_plan(plan_id, confirmed=confirmed)


def plan_import(selection_id: str, *, mode: str) -> dict[str, Any]:
    return component_installer().plan_import(selection_id, mode=mode)


def confirm_import(plan_id: str, *, confirmed: bool) -> dict[str, Any]:
    return component_installer().confirm_import(plan_id, confirmed=confirmed)


def plan_bundle(component_id: str, *, component_type: str, variant: str = "default") -> dict[str, Any]:
    return component_bundle_service().plan(component_id, component_type=component_type, variant=variant)


def lookup_bundle(plan_id: str) -> dict[str, Any] | None:
    return component_bundle_service().lookup(plan_id)


def confirm_bundle(plan_id: str, *, confirmed: bool) -> dict[str, Any]:
    return component_bundle_service().confirm(plan_id, confirmed=confirmed)


def plan_reuse(component_id: str, *, component_type: str = "model") -> dict[str, Any]:
    return component_installer().plan_reuse(component_id, component_type=component_type)


def confirm_reuse(plan_id: str, *, confirmed: bool) -> dict[str, Any]:
    return component_installer().confirm_reuse(plan_id, confirmed=confirmed)


def plan_verify(component_id: str, *, component_type: str) -> dict[str, Any]:
    return component_installer().plan_verify(component_id, component_type=component_type)


def plan_maintenance(component_id: str, *, action: str) -> dict[str, Any]:
    return component_installer().plan_maintenance(component_id, action=action)


def confirm_maintenance(plan_id: str, *, confirmed: bool) -> dict[str, Any]:
    return component_installer().confirm_maintenance(plan_id, confirmed=confirmed)


def lookup_plan(plan_id: str) -> dict[str, Any] | None:
    return component_installer().lookup_plan(plan_id)


def lookup_job(job_id: str) -> dict[str, Any] | None:
    return component_installer().lookup_job(job_id)


def handle_error(exc: Exception, *, plan_id: str | None = None) -> tuple[int, dict[str, Any]]:
    if isinstance(exc, (InstallPlanError, SelectionError, ValueError)):
        candidate = str(exc)
        code = candidate if re.fullmatch(r"[a-z][a-z0-9_.-]{0,79}", candidate) else "invalid_component_request"
        return 400, _public_error(code, plan_id=plan_id)
    return 500, _public_error("component_manager_unavailable", plan_id=plan_id)


__all__ = [
    "component_installer", "confirm_import", "confirm_install", "confirm_maintenance", "detail",
    "component_bundle_service", "confirm_bundle", "confirm_reuse", "handle_error", "lookup_bundle", "lookup_job", "lookup_plan", "plan_bundle", "plan_import", "plan_install", "plan_maintenance", "plan_reuse",
    "plan_verify", "snapshot",
]
