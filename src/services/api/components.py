"""Compatibility façade for Component Manager routes with V8 lifecycle authority.

Wave 3 keeps the existing V7 ComponentInstaller and bundle service as execution
implementations while journalling install/verify/reuse/maintenance/import and
bundle plans through durable V8 operations. Source acceptance is metadata-only
and never performs a provider request or component download.
"""

from __future__ import annotations

import re
import threading
from typing import Any

from src.services.component_enablement_v8 import ComponentEnablementService
from src.services.component_installer import ComponentInstaller, InstallPlanError, SelectionError
from src.services.component_installer.bundle import ComponentBundleService
from src.services.component_lifecycle_v8 import ComponentLifecycleCoordinator, ComponentLifecycleError
from src.services.v8_transaction_extensions import V8ProductionTransactionStore


_lock = threading.RLock()
_manager: ComponentInstaller | None = None
_bundle: ComponentBundleService | None = None
_lifecycle: ComponentLifecycleCoordinator | None = None
_enablement: ComponentEnablementService | None = None


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


def component_lifecycle() -> ComponentLifecycleCoordinator:
    global _lifecycle
    with _lock:
        if _lifecycle is None:
            manager = component_installer()
            store = V8ProductionTransactionStore.for_paths(manager.paths)
            _lifecycle = ComponentLifecycleCoordinator(
                installer=manager,
                store=store,
                bundle_service=component_bundle_service(),
            )
        return _lifecycle


def component_enablement() -> ComponentEnablementService:
    global _enablement
    with _lock:
        if _enablement is None:
            _enablement = ComponentEnablementService()
        return _enablement


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


def _operation_for(reference: str) -> dict[str, Any] | None:
    lifecycle = component_lifecycle()
    direct = lifecycle.inspect_operation(reference)
    if direct is not None:
        return direct
    lookup = getattr(lifecycle.store, "component_operation_for_plan", None)
    return lookup(reference) if callable(lookup) else None


def _confirm_reference(reference: str, *, confirmed: bool) -> dict[str, Any]:
    operation = _operation_for(reference)
    if operation is None:
        return {
            "status": "conflict",
            "code": "component_operation_required",
            "plan_id": reference,
            "execution": "not_run",
            "dry_run": True,
            "next_action": "Create a fresh V8 component plan before confirmation.",
        }
    return component_lifecycle().confirm_operation(
        str(operation["operation_id"]),
        confirmed=confirmed,
    )


def snapshot() -> dict[str, Any]:
    return component_installer().snapshot()


def detail(component_id: str) -> dict[str, Any]:
    return component_installer().detail(component_id)


def plan_install(component_id: str, *, component_type: str, variant: str | None = None) -> dict[str, Any]:
    return component_lifecycle().plan_install(
        component_id,
        component_type=component_type,
        variant=variant,
    )


def confirm_install(plan_id: str, *, confirmed: bool) -> dict[str, Any]:
    return _confirm_reference(plan_id, confirmed=confirmed)


def plan_import(selection_id: str, *, mode: str) -> dict[str, Any]:
    return component_lifecycle().plan_import(selection_id, mode=mode)


def confirm_import(plan_id: str, *, confirmed: bool) -> dict[str, Any]:
    return _confirm_reference(plan_id, confirmed=confirmed)


def plan_bundle(component_id: str, *, component_type: str, variant: str = "default") -> dict[str, Any]:
    return component_lifecycle().plan_bundle(
        component_id,
        component_type=component_type,
        variant=variant,
    )


def lookup_bundle(plan_id: str) -> dict[str, Any] | None:
    plan = component_bundle_service().lookup(plan_id)
    operation = _operation_for(plan_id)
    if plan is None and operation is None:
        return None
    result = dict(plan or {})
    if operation is not None:
        result["operation_id"] = operation["operation_id"]
        result["operation_state"] = operation["state"]
        result["operation_result_code"] = operation.get("result_code")
    return result


def confirm_bundle(plan_id: str, *, confirmed: bool) -> dict[str, Any]:
    return _confirm_reference(plan_id, confirmed=confirmed)


def plan_reuse(component_id: str, *, component_type: str = "model") -> dict[str, Any]:
    return component_lifecycle().plan_reuse(component_id, component_type=component_type)


def confirm_reuse(plan_id: str, *, confirmed: bool) -> dict[str, Any]:
    return _confirm_reference(plan_id, confirmed=confirmed)


def plan_verify(component_id: str, *, component_type: str) -> dict[str, Any]:
    return component_lifecycle().plan_verify(component_id, component_type=component_type)


def plan_maintenance(component_id: str, *, action: str) -> dict[str, Any]:
    return component_lifecycle().plan_maintenance(component_id, action=action)


def confirm_maintenance(plan_id: str, *, confirmed: bool) -> dict[str, Any]:
    return _confirm_reference(plan_id, confirmed=confirmed)


def lookup_plan(plan_id: str) -> dict[str, Any] | None:
    plan = component_installer().lookup_plan(plan_id)
    operation = _operation_for(plan_id)
    if plan is None and operation is None:
        return None
    result = dict(plan or {})
    if operation is not None:
        result.update({
            "operation_id": operation["operation_id"],
            "operation_state": operation["state"],
            "operation_result_code": operation.get("result_code"),
            "component_id": result.get("component_id") or operation.get("component_id"),
            "component_type": result.get("component_type") or operation.get("component_type"),
            "action": result.get("action") or operation.get("action"),
        })
    return result


def lookup_job(job_id: str) -> dict[str, Any] | None:
    return component_installer().lookup_job(job_id)


def operations(*, limit: int = 100) -> dict[str, Any]:
    bounded = max(1, min(500, int(limit)))
    return {
        "status": "completed",
        "execution": "not_run",
        "dry_run": True,
        "operations": component_lifecycle().list_operations(limit=bounded),
    }


def operation(operation_id: str) -> dict[str, Any] | None:
    return component_lifecycle().inspect_operation(operation_id)


def confirm_operation(operation_id: str, *, confirmed: bool) -> dict[str, Any]:
    return component_lifecycle().confirm_operation(operation_id, confirmed=confirmed)


def cancel_operation(operation_id: str) -> dict[str, Any]:
    return component_lifecycle().cancel_operation(operation_id)


def source_acceptance_snapshot() -> dict[str, Any]:
    return component_enablement().snapshot()


def source_acceptance(component_id: str) -> dict[str, Any]:
    return component_enablement().assess(component_id)


def handle_error(exc: Exception, *, plan_id: str | None = None) -> tuple[int, dict[str, Any]]:
    if isinstance(exc, (InstallPlanError, SelectionError, ComponentLifecycleError, ValueError)):
        candidate = str(exc)
        code = candidate if re.fullmatch(r"[a-z][a-z0-9_.-]{0,79}", candidate) else "invalid_component_request"
        return 400, _public_error(code, plan_id=plan_id)
    return 500, _public_error("component_manager_unavailable", plan_id=plan_id)


def reset_v8_component_services_for_tests() -> None:
    global _manager, _bundle, _lifecycle, _enablement
    with _lock:
        _manager = None
        _bundle = None
        _lifecycle = None
        _enablement = None


__all__ = [
    "cancel_operation",
    "component_bundle_service",
    "component_enablement",
    "component_installer",
    "component_lifecycle",
    "confirm_bundle",
    "confirm_import",
    "confirm_install",
    "confirm_maintenance",
    "confirm_operation",
    "confirm_reuse",
    "detail",
    "handle_error",
    "lookup_bundle",
    "lookup_job",
    "lookup_plan",
    "operation",
    "operations",
    "plan_bundle",
    "plan_import",
    "plan_install",
    "plan_maintenance",
    "plan_reuse",
    "plan_verify",
    "reset_v8_component_services_for_tests",
    "snapshot",
    "source_acceptance",
    "source_acceptance_snapshot",
]
