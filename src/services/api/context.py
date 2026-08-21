"""Application-owned dependencies passed into domain route adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class ApiContext:
    """Shared application composition; route modules do not create managers."""

    services: Mapping[str, Any]

    def get(self, name: str, default: Any = None) -> Any:
        return self.services.get(name, default)

    def call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        value = self.services.get(name)
        if not callable(value):
            raise RuntimeError(f"api_service_unavailable:{name}")
        return value(*args, **kwargs)

    def factory(self, name: str) -> Callable[..., Any]:
        value = self.services.get(name)
        if not callable(value):
            raise RuntimeError(f"api_factory_unavailable:{name}")
        return value


def build_default_context(bindings: Mapping[str, Any]) -> ApiContext:
    """Compose the shared service graph without importing the HTTP server."""

    from src.app_config.schema import SETTINGS_SECTION_DEFAULTS, SETTINGS_SCHEMA_VERSION
    from src.app_config.settings_service import SettingsPersistence
    from src.services.api import components as component_api
    from src.services.backup_manager import BackupManager
    from src.services.diagnostics.center import diagnostics_center
    from src.services.model_manager import ModelManager
    from src.services.runtime_manager import RuntimeManager
    from src.services.storage_manager.overview import model_summary
    from src.services.node_studio.state import draft_clear

    model_manager: ModelManager | None = None
    runtime_manager: RuntimeManager | None = None
    productization_service: Any | None = None
    update_service: Any | None = None
    get = bindings.get

    def model_service() -> ModelManager:
        nonlocal model_manager
        if model_manager is None:
            model_manager = ModelManager()
        return model_manager

    def runtime_service() -> RuntimeManager:
        nonlocal runtime_manager
        if runtime_manager is None:
            runtime_manager = RuntimeManager()
        return runtime_manager

    def productization() -> Any:
        nonlocal productization_service
        if productization_service is None:
            from src.services.productization import ComponentLifecycle
            productization_service = ComponentLifecycle()
        return productization_service

    def updates() -> Any:
        nonlocal update_service
        if update_service is None:
            from src.services.operational_closure import UpdateResolver
            update_service = UpdateResolver()
        return update_service

    def product_snapshot(**kwargs: Any) -> dict[str, Any]:
        return productization().catalog.snapshot(**kwargs)

    def product_detail(component_id: str) -> dict[str, Any] | None:
        service = productization()
        if component_id in service.catalog.models:
            return service.catalog.inspect_model(component_id)
        if component_id in service.catalog.runtimes:
            return service.catalog.inspect_runtime(component_id)
        return None

    def prepare_shutdown() -> dict[str, Any]:
        status, payload = get("prepare_owned_shutdown")()
        return {**payload, "http_status": status}

    def close_idle() -> dict[str, Any]:
        from src.modules.image_generation.backend.comfyui import shutdown_owned_idle
        return shutdown_owned_idle()

    def cancel_jobs(timeout: object) -> dict[str, Any]:
        value = get("bounded_int")(timeout, 12, minimum=1, maximum=60)
        ok, message = get("job_manager").cancel_all_and_wait(value)
        return {"status": "completed" if ok else "timeout", "message": message, "http_status": 200 if ok else 409}

    def clear_drafts(scopes: list[str]) -> dict[str, Any]:
        cleared: list[str] = []
        for scope in scopes:
            draft_clear(scope)
            cleared.append(scope)
        return {"status": "completed", "cleared": cleared, "message": f"Đã dọn dẹp {len(cleared)} bản nháp phục hồi."}

    project = get("project_manager")
    studio = get("image_mask_studio")
    return ApiContext({
        "health": get("health"), "bootstrap_payload": get("bootstrap_payload"),
        "capability_control_plane": get("capability_control_plane"), "lifecycle_payload": get("lifecycle_payload"),
        "tools_payload": lambda: {"status": "completed", "tools": get("tool_catalog")(get("component_statuses")())},
        "component_statuses": get("component_statuses"), "component_snapshot": component_api.snapshot,
        "component_detail": component_api.detail, "component_plan_lookup": component_api.lookup_plan,
        "component_job_lookup": component_api.lookup_job, "component_plan_install": component_api.plan_install,
        "component_confirm_install": component_api.confirm_install, "component_plan_verify": component_api.plan_verify,
        "component_plan_import": component_api.plan_import, "component_confirm_import": component_api.confirm_import,
        "component_plan_bundle": component_api.plan_bundle, "component_bundle_lookup": component_api.lookup_bundle,
        "component_confirm_bundle": component_api.confirm_bundle,
        "component_plan_reuse": component_api.plan_reuse, "component_confirm_reuse": component_api.confirm_reuse,
        "component_plan_maintenance": component_api.plan_maintenance, "component_confirm_maintenance": component_api.confirm_maintenance,
        "component_cancel_job": lambda job_id: component_api.component_installer().cancel_job(job_id),
        "project_manager": project, "workflow_library_store": get("workflow_library_store"),
        "list_jobs": get("list_jobs"), "get_job": get("get_job"), "durable_jobs_snapshot": get("durable_jobs_snapshot"),
        "admit_durable_job": get("admit_durable_job"), "resume_durable_job": get("resume_durable_job"),
        "submit_graph": get("submit_graph"), "open_artifact": get("open_artifact"),
        "submit_tool": get("submit_tool"), "image_mask_studio": studio,
        "image_mask_link_project": lambda session_id, payload: _link_image_mask_project(studio, project, session_id, payload),
        "artifact_status": project.get_artifact_status, "node_registry_payload": lambda scope=None: __import__("src.services.node_studio.registry", fromlist=["registry_payload"]).registry_payload(scope),
        "node_preset_summaries": get("node_preset_summaries") or get("preset_summaries"), "node_preset": get("node_preset") or get("preset"),
        "node_validate": lambda graph, require_runnable=False: __import__("src.services.node_studio.schema", fromlist=["validate_graph"]).validate_graph(graph, require_runnable=require_runnable),
        "node_downstream": lambda graph, changed: __import__("src.services.node_studio.schema", fromlist=["downstream_nodes"]).downstream_nodes(graph, changed),
        "node_draft_load": lambda scope: __import__("src.services.node_studio.state", fromlist=["draft_load"]).draft_load(scope),
        "node_draft_persist": lambda scope, graph: __import__("src.services.node_studio.state", fromlist=["draft_persist"]).draft_persist(scope, graph),
        "node_draft_clear": lambda scope: __import__("src.services.node_studio.state", fromlist=["draft_clear"]).draft_clear(scope),
        "node_run_snapshot": lambda run_id: __import__("src.services.node_studio.state", fromlist=["graph_runs"]).graph_runs.snapshot(run_id),
        "model_summary": model_summary, "model_manager_inspect": lambda model_id: model_service().inspect(model_id),
        "storage_summary": get("storage_summary"), "dashboard_volume_snapshot": get("dashboard_volume_snapshot"),
        "applications": get("applications"), "launch_application": get("launch_application"),
        "workflow_library_payload": get("workflow_library_payload"),
        "comfy_health": get("comfy_health"), "comfy_start": get("comfy_start"),
        "comfy_workflows": get("comfy_workflows"), "comfy_workflow": get("comfy_workflow"), "comfy_save_workflow": get("comfy_save_workflow"),
        "runtime_manager_snapshot": lambda: runtime_service().snapshot(), "runtime_manager_verify": lambda runtime_id: runtime_service().verify(runtime_id),
        "productization_snapshot": product_snapshot, "productization_detail": product_detail,
        "productization_plan": lambda component_id: productization().plan_one_click(component_id),
        "productization_plan_lookup": lambda plan_id: productization().lookup_plan(plan_id),
        "productization_confirm": lambda plan_id, confirmed=False: productization().confirm(plan_id, confirmed=confirmed),
        "productization_maintenance": lambda component_id, action: productization().plan_maintenance(component_id, action),
        "update_settings_get": lambda: updates().schedule.get(),
        "update_settings_set": lambda policy: updates().schedule.set_policy(policy),
        "update_check": lambda component_id, force_source_check=False: updates().check_component(component_id, force_source_check=force_source_check),
        "update_check_all": lambda force_source_check=False: updates().check_all(force_source_check=force_source_check),
        "update_plan": lambda component_id: updates().plan_update(component_id),
        "update_plan_lookup": lambda plan_id: updates()._plans.get(plan_id),
        "update_apply": lambda plan_id, confirmed=False: updates().apply_update(plan_id, confirmed=confirmed),
        "update_rollback": lambda component_id: updates().rollback(component_id),
        "settings_payload": get("settings_payload"), "settings_schema": lambda: {"status": "completed", "schema_version": SETTINGS_SCHEMA_VERSION, "defaults": SETTINGS_SECTION_DEFAULTS},
        "settings_save": lambda payload, expected_revision=None: SettingsPersistence().save(payload, expected_revision=expected_revision),
        "settings_reset": lambda section: SettingsPersistence().reset_section(section), "settings_reset_all": lambda: SettingsPersistence().save(SETTINGS_SECTION_DEFAULTS),
        "diagnostics_snapshot": diagnostics_center.snapshot, "diagnostics_export": diagnostics_center.export_diagnostics_bundle,
        "diagnostics_config_registry": diagnostics_center.config_registry_state, "diagnostics_recovery_state": diagnostics_center.recovery_forensic_state,
        "diagnostics_recovery_drafts": lambda: {"status": "completed", "drafts": diagnostics_center.recovery_forensic_state().get("files", []), "recovery": diagnostics_center.recovery_forensic_state()},
        "diagnostics_subsystem": lambda subsystem: _diagnostic_subsystem(diagnostics_center, subsystem), "clear_recovery_drafts": clear_drafts,
        "backup_list": lambda: BackupManager().list_backups(), "backup_create": lambda: BackupManager().create_backup(),
        "backup_inspect": lambda backup_id: BackupManager().inspect_backup(backup_id), "backup_plan": lambda backup_id: BackupManager().plan_restore(backup_id),
        "backup_apply": lambda plan_id, confirmed=False: BackupManager().apply_restore(plan_id, confirmed=confirmed),
        "prepare_owned_shutdown": get("prepare_owned_shutdown"), "close_owned_idle": close_idle, "cancel_owned_jobs_and_wait": cancel_jobs,
    })


def _diagnostic_subsystem(center: object, subsystem: str) -> dict[str, Any] | None:
    methods = {
        "git_integrity": "git_integrity_state", "config_registry": "config_registry_state", "jobs_store": "jobs_store_state",
        "artifact_store": "artifact_store_state", "workflow_store": "workflow_store_state", "models_inventory": "models_inventory",
        "environments_inventory": "environments_inventory", "runtime_inventory": "runtime_inventory", "storage": "storage_state",
        "gpu": "gpu_detection", "latest_app_errors": "latest_app_errors", "recovery_forensic": "recovery_forensic_state",
    }
    method = methods.get(str(subsystem))
    if method is None or not hasattr(center, method):
        return None
    return {"status": "completed", "subsystem": subsystem, "data": getattr(center, method)()}


def _link_image_mask_project(studio: Any, project: Any, session_id: str, payload: object) -> dict[str, Any]:
    """Preserve the existing Studio-to-project attachment transaction."""

    from src.services.image_mask_studio import StudioConflictError

    prepared = studio.prepare_project_attachment(session_id, payload)
    attachment = prepared.get("attachment") if isinstance(prepared, dict) else None
    session = prepared.get("session") if isinstance(prepared, dict) else None
    if not isinstance(attachment, dict) or not isinstance(session, dict):
        raise RuntimeError("image_mask_attachment_invalid")
    if prepared.get("status") == "completed":
        return {"status": "completed", "session": session, "project": None, "attachment": attachment, "idempotent": True}
    project_id = attachment.get("project_id")
    artifact_ids = attachment.get("artifacts")
    intent_id = attachment.get("intent_id")
    revision = attachment.get("revision")
    if not isinstance(project_id, str) or not isinstance(artifact_ids, list) or not isinstance(intent_id, str) or not isinstance(revision, int) or isinstance(revision, bool):
        raise RuntimeError("image_mask_attachment_invalid")
    selected_ids = set(artifact_ids)
    mask_ids = [layer.get("artifact_id") for layer in session.get("layers", []) if isinstance(layer, dict) and layer.get("kind") == "mask" and isinstance(layer.get("artifact_id"), str) and layer["artifact_id"] in selected_ids]
    public_attachment = {key: value for key, value in attachment.items() if key != "intent_id"}
    try:
        linked = project.attach_image_mask_studio_revision(project_id, {"studio_id": session_id, "revision": revision, "source_artifact_id": session.get("source_artifact_id"), "artifact_ids": artifact_ids, "mask_artifact_ids": mask_ids})
        completed = studio.complete_project_attachment(session_id, project_id=project_id, artifact_ids=artifact_ids, intent_id=intent_id, expected_revision=revision)
    except StudioConflictError as exc:
        return {"status": "pending_project_attach", "error": str(exc), "session": session, "attachment": public_attachment, "current_revision": exc.current_revision, "action": "Studio changed; reload the server revision and retry the project link."}
    except (KeyError, ValueError) as exc:
        return {"status": "pending_project_attach", "error": str(exc), "session": session, "attachment": public_attachment, "action": "Repair the target project and retry the link; the Studio draft remains durable."}
    return {"status": "completed", "session": completed.get("session"), "project": linked.get("project"), "attachment": public_attachment}


__all__ = ["ApiContext", "build_default_context"]
