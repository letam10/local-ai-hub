"""Application-owned dependencies passed into domain route adapters."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
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
    resource_scheduler_service: Any | None = None
    durable_job_engine_v2_service: Any | None = None
    workflow_runtime_v2_service: Any | None = None
    project_workspace_v2_service: Any | None = None
    artifact_library_v2_service: Any | None = None
    media_pipeline_v2_service: Any | None = None
    provider_adapter_registry_v2_service: Any | None = None
    external_integration_registry_v2_service: Any | None = None
    from src.services.projection_cache import BoundedProjectionCache

    projection_cache = BoundedProjectionCache()
    get = bindings.get

    def projection_fingerprint(value: object) -> str:
        """Fingerprint already-owned bounded metadata without public output."""

        try:
            encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (TypeError, ValueError):
            # An uncanonicalizable binding is never cached.  The fixed
            # fingerprint keeps the builder path deterministic without
            # reflecting a potentially unsafe value in any API result.
            return "uncacheable"
        return hashlib.sha256(encoded).hexdigest()

    def bound_value(name: str, default: object) -> object:
        value = get(name)
        return value() if callable(value) else default if value is None else value

    def shortcut_migration_snapshot() -> dict[str, Any]:
        """Expose only sanitized shortcut ownership state to Diagnostics."""

        raw = bound_value("shortcut_migration_snapshot", {})
        if not isinstance(raw, Mapping):
            raw = {}
        status = str(raw.get("status") or "not_run")
        if status not in {"not_run", "completed", "partial", "blocked"}:
            status = "not_run"
        return {
            "status": status,
            "execution": "not_run" if status == "not_run" else "completed",
            "ambiguous_count": max(0, int(raw.get("ambiguous_count", 0) or 0)) if isinstance(raw.get("ambiguous_count", 0), int) else 0,
            "owned_stale_count": max(0, int(raw.get("owned_stale_count", 0) or 0)) if isinstance(raw.get("owned_stale_count", 0), int) else 0,
            "admin_required_count": max(0, int(raw.get("admin_required_count", 0) or 0)) if isinstance(raw.get("admin_required_count", 0), int) else 0,
            "real_shortcuts_mutated": False,
            "next_action": "Review shortcut ownership before any repair; ambiguous links are never changed automatically.",
        }

    def catalog_snapshot() -> tuple[dict[str, Any], str]:
        raw = productization().catalog.snapshot()
        catalog = dict(raw) if isinstance(raw, Mapping) else {"models": [], "runtimes": []}
        fingerprint = projection_fingerprint(catalog)
        return projection_cache.get_or_build("post_v8.catalog", fingerprint, lambda: catalog, cacheable=fingerprint != "uncacheable"), fingerprint

    def has_live_execution(items: object) -> bool:
        if not isinstance(items, list):
            return False
        for item in items:
            if not isinstance(item, Mapping):
                continue
            if str(item.get("component_status") or item.get("status") or "").casefold() == "running":
                return True
            smoke = item.get("last_smoke")
            if isinstance(smoke, Mapping) and smoke.get("execution") in {"running", "cancelling", "paused"}:
                return True
        return False

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

    def capability_graph_bundle() -> tuple[Any, str, bool]:
        """Compose V2 from existing server-owned, bounded snapshots only."""

        from src.services.capability_graph import build_component_capability_graph

        component_items = get("component_statuses")()
        tool_items = get("tool_catalog")(component_items)
        catalog, catalog_fingerprint = catalog_snapshot()
        engine_evidence = bound_value("capability_engine_evidence", {})
        resource_evidence = bound_value("capability_resource_evidence", {})
        if not resource_evidence:
            # A scheduler/hardware-fit observation is a bounded server-owned
            # source for resource:gpu.  It is accepted only when the snapshot
            # itself carries a current observed-free value and fingerprint;
            # declared total VRAM never promotes the dependency on its own.
            scheduler_snapshot = resource_scheduler().snapshot()
            inventory = scheduler_snapshot.get("inventory") if isinstance(scheduler_snapshot, Mapping) else {}
            gpus = inventory.get("gpus") if isinstance(inventory, Mapping) else []
            gpu = next((item for item in gpus if isinstance(item, Mapping) and isinstance(item.get("vram_free_observed_mb"), int) and isinstance(item.get("observed_at"), str) and isinstance(item.get("source_fingerprint"), str)), None)
            if gpu is not None:
                resource_evidence = {
                    "gpu": {
                        "status": "available",
                        "fingerprint": gpu["source_fingerprint"],
                        "source_fingerprint": gpu["source_fingerprint"],
                        "source_revision": str(scheduler_snapshot.get("schema_version") or "resource-scheduler"),
                        "observed_at": gpu["observed_at"],
                        "freshness_seconds": 300,
                    },
                }
        inputs = {
            "components": component_items,
            "tools": tool_items,
            "catalog_fingerprint": catalog_fingerprint,
            "engine_evidence": engine_evidence,
            "resource_evidence": resource_evidence,
        }
        fingerprint = projection_fingerprint(inputs)
        dynamic_evidence = bool(engine_evidence) or bool(resource_evidence)
        cacheable = fingerprint != "uncacheable" and not has_live_execution(component_items) and not dynamic_evidence
        graph = projection_cache.get_or_build(
            "post_v8.capability_graph",
            fingerprint,
            lambda: build_component_capability_graph(
                component_statuses=component_items,
                tool_records=tool_items,
                catalog_snapshot=catalog,
                engine_evidence=engine_evidence if isinstance(engine_evidence, Mapping) else None,
                resource_evidence=resource_evidence if isinstance(resource_evidence, Mapping) else None,
            ),
            cacheable=cacheable,
        )
        return graph, fingerprint, cacheable

    def capability_graph() -> Any:
        return capability_graph_bundle()[0]

    def component_lifecycle_engine() -> Any:
        """Compose the V2 lifecycle planning facade from the V2 graph only."""

        from src.services.component_lifecycle_engine import ComponentLifecycleEngine

        graph, fingerprint, cacheable = capability_graph_bundle()
        return projection_cache.get_or_build(
            "post_v8.component_lifecycle",
            fingerprint,
            lambda: ComponentLifecycleEngine(graph=graph),
            cacheable=cacheable,
        )

    def model_manager_v2() -> Any:
        """Compose the V2 model inventory from the bounded production catalog."""

        from src.services.model_manager_v2 import ModelManagerV2

        catalog, catalog_fingerprint = catalog_snapshot()
        graph, graph_fingerprint, cacheable = capability_graph_bundle()
        observations = bound_value("model_manager_v2_observations", [])
        fingerprint = projection_fingerprint({"catalog": catalog_fingerprint, "graph": graph_fingerprint, "observations": observations})
        return projection_cache.get_or_build(
            "post_v8.model_manager",
            fingerprint,
            lambda: ModelManagerV2(
                catalog_snapshot=catalog,
                lifecycle_engine=component_lifecycle_engine(),
                observations=observations if isinstance(observations, list) else None,
            ),
            cacheable=cacheable and fingerprint != "uncacheable",
        )

    def resource_scheduler() -> Any:
        """Create one in-process scheduler from a configuration snapshot only."""

        nonlocal resource_scheduler_service
        if resource_scheduler_service is None:
            from src.services.resource_scheduler import ResourceScheduler

            hardware_provider = get("resource_scheduler_hardware")
            hardware_snapshot = hardware_provider() if callable(hardware_provider) else None
            profile_provider = get("resource_scheduler_profiles")
            profiles = profile_provider() if callable(profile_provider) else None
            resource_scheduler_service = ResourceScheduler(
                hardware_snapshot=hardware_snapshot,
                profiles=profiles if isinstance(profiles, Mapping) else None,
            )
        return resource_scheduler_service

    def durable_job_engine_v2() -> Any:
        """Compose one server-owned Durable Job Engine V2 per API process.

        Construction is read-only: the SQLite metadata leaf is created only
        when a separately authorized durable-job mutation is admitted. No
        execution owner is registered by default, so browser requests cannot
        launch a worker, provider, model, or GPU workload.
        """

        nonlocal durable_job_engine_v2_service
        if durable_job_engine_v2_service is None:
            from src.platform.paths import get_paths
            from src.services.durable_job_engine_v2 import DurableJobEngineV2, DurableJobStoreV2, ExecutionOwnerRegistry

            store_path = get("durable_job_v2_store_path") or (get_paths().config_root / "durable_jobs_v2.sqlite3")
            owner_binding = get("durable_job_v2_owners")
            owners = owner_binding if isinstance(owner_binding, ExecutionOwnerRegistry) else ExecutionOwnerRegistry(owner_binding if isinstance(owner_binding, Mapping) else None)
            durable_job_engine_v2_service = DurableJobEngineV2(
                store=DurableJobStoreV2(store_path),
                scheduler=resource_scheduler(),
                owners=owners,
                readmission_preflight=(get("durable_job_v2_readmission_preflight") if callable(get("durable_job_v2_readmission_preflight")) else None),
            )
        return durable_job_engine_v2_service

    def reconcile_durable_job_v2_startup() -> dict[str, int]:
        """Run one conservative metadata reconciliation during API startup.

        The default application owns no V2 worker liveness bridge. Therefore a
        pre-existing V2 RUNNING record is never adopted or reported as running
        after restart; the engine records an explicit bounded failure instead.
        Integrations can bind path-free worker/artifact predicates later.
        """

        worker_alive = get("durable_job_v2_worker_alive")
        artifact_complete = get("durable_job_v2_artifact_complete")
        return durable_job_engine_v2().reconcile_startup(
            worker_alive=worker_alive if callable(worker_alive) else None,
            artifact_complete=artifact_complete if callable(artifact_complete) else None,
        )

    def artifact_describer(artifact_id: str) -> Mapping[str, Any] | None:
        """Read an existing opaque artifact projection without creating a store."""

        from src.services.artifact_access_v8 import describe

        value = describe(artifact_id)
        return value if isinstance(value, Mapping) else None

    def artifact_lister(*, limit: int = 120) -> list[dict[str, Any]]:
        from src.services.artifact_access_v8 import list_artifacts

        value = list_artifacts(limit=limit)
        return value if isinstance(value, list) else []

    def workflow_runtime_v2() -> Any:
        """Compose the plan-only M2 workflow facade from existing read models."""

        nonlocal workflow_runtime_v2_service
        if workflow_runtime_v2_service is None:
            from src.services.workflow_runtime_v2 import WorkflowRuntimeV2

            workflow_runtime_v2_service = WorkflowRuntimeV2(
                capability_snapshot=lambda: capability_graph().snapshot(),
                resource_snapshot=lambda: resource_scheduler().snapshot(),
                artifact_describer=artifact_describer,
                durable_admit=lambda request: durable_job_engine_v2().admit(request),
                dispatch_binding=(get("workflow_runtime_v2_execution_binding") if isinstance(get("workflow_runtime_v2_execution_binding"), Mapping) else None),
            )
        return workflow_runtime_v2_service

    def workflow_library_store() -> Any | None:
        """Resolve the existing Library owner once without changing its API.

        API routes intentionally receive the factory and call it per request.
        The M2 read projection needs the resulting store object, not the
        callable itself.  Keeping that distinction here avoids a second store
        and preserves the V5 no-follow/CAS implementation.
        """

        value = get("workflow_library_store")
        try:
            return value() if callable(value) else value
        except Exception:
            return None

    def project_workspace_v2() -> Any:
        nonlocal project_workspace_v2_service
        if project_workspace_v2_service is None:
            from src.services.workflow_runtime_v2 import ProjectWorkspaceV2

            project_workspace_v2_service = ProjectWorkspaceV2(
                project,
                workflow_library_store(),
                durable_job_lookup=lambda job_id: durable_job_engine_v2().get(job_id),
            )
        return project_workspace_v2_service

    def artifact_library_v2() -> Any:
        nonlocal artifact_library_v2_service
        if artifact_library_v2_service is None:
            from src.services.workflow_runtime_v2 import ArtifactLibraryV2

            artifact_library_v2_service = ArtifactLibraryV2(
                artifact_lister=artifact_lister,
                artifact_describer=artifact_describer,
                project_manager=project,
            )
        return artifact_library_v2_service

    def media_pipeline_v2() -> Any:
        nonlocal media_pipeline_v2_service
        if media_pipeline_v2_service is None:
            from src.services.workflow_runtime_v2 import MediaPipelineV2

            media_pipeline_v2_service = MediaPipelineV2(artifact_describer)
        return media_pipeline_v2_service

    def provider_adapters_v2() -> Any:
        """Compose the closed M3 provider contract without loading modules."""

        nonlocal provider_adapter_registry_v2_service
        if provider_adapter_registry_v2_service is None:
            from src.services.provider_adapters_v2 import ProviderAdapterRegistry

            def provider_capability_snapshot() -> Mapping[str, Any]:
                # Minimal/test compositions legitimately omit the legacy V8
                # component bindings.  They must project exact missing
                # dependencies (UNAVAILABLE), never fail the whole route or
                # synthesize readiness.
                try:
                    value = capability_graph().snapshot()
                except Exception:
                    return {"capabilities": []}
                return value if isinstance(value, Mapping) else {"capabilities": []}

            def provider_resource_snapshot() -> Mapping[str, Any]:
                try:
                    value = resource_scheduler().snapshot()
                except Exception:
                    return {"profiles": []}
                return value if isinstance(value, Mapping) else {"profiles": []}

            provider_adapter_registry_v2_service = ProviderAdapterRegistry(
                capability_snapshot=provider_capability_snapshot,
                resource_snapshot=provider_resource_snapshot,
            )
        return provider_adapter_registry_v2_service

    def external_integrations_v2() -> Any:
        """Project only existing sanitized application registry information."""

        nonlocal external_integration_registry_v2_service
        if external_integration_registry_v2_service is None:
            from src.services.provider_adapters_v2 import ExternalIntegrationRegistry

            applications = get("applications")
            observations = get("external_integrations_v2_observations")
            external_integration_registry_v2_service = ExternalIntegrationRegistry(
                applications_snapshot=(lambda: applications()) if callable(applications) else (lambda: []),
                observations_snapshot=(lambda: observations()) if callable(observations) else (lambda: observations if isinstance(observations, Mapping) else {}),
            )
        return external_integration_registry_v2_service

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
        "capability_graph_snapshot": lambda: capability_graph().snapshot(),
        "capability_graph_capability": lambda capability_id: capability_graph().capability(capability_id),
        "capability_graph_dependency_tree": lambda capability_id: capability_graph().dependency_tree(capability_id),
        "capability_graph_blockers": lambda capability_id: capability_graph().blockers(capability_id),
        "capability_graph_safe_actions": lambda capability_id: capability_graph().safe_actions(capability_id),
        "capability_graph_verification_evidence": lambda capability_id: capability_graph().verification_evidence(capability_id),
        "feature_discovery_v2_snapshot": lambda: __import__("src.services.feature_discovery_v2", fromlist=["snapshot"]).snapshot(__import__("src.services.api.router_registry", fromlist=["build_router"]).build_router()),
        "feature_discovery_v2_detail": lambda feature_id: __import__("src.services.feature_discovery_v2", fromlist=["detail"]).detail(feature_id, __import__("src.services.api.router_registry", fromlist=["build_router"]).build_router()),
        "component_lifecycle_v2_snapshot": lambda: component_lifecycle_engine().snapshot(),
        "component_lifecycle_v2_detail": lambda capability_id: component_lifecycle_engine().inspect(capability_id),
        "component_lifecycle_v2_plan": lambda capability_id, action: component_lifecycle_engine().plan(capability_id, action, planner=component_api.component_lifecycle),
        "model_manager_v2_snapshot": lambda: model_manager_v2().snapshot(),
        "model_manager_v2_detail": lambda model_id: model_manager_v2().detail(model_id),
        "model_manager_v2_preflight": lambda model_id: model_manager_v2().preflight(model_id),
        "model_manager_v2_plan": lambda model_id, action, selection_id=None: model_manager_v2().plan(model_id, action, planner=component_api.component_lifecycle, selection_id=selection_id),
        "resource_scheduler_v2_snapshot": lambda: resource_scheduler().snapshot(),
        "resource_scheduler_v2_job": lambda job_id: resource_scheduler().job(job_id),
        "durable_job_v2_snapshot": lambda status=None, query=None, archived=False: durable_job_engine_v2().snapshot(status=status, query=query, archived=archived),
        "durable_job_v2_detail": lambda job_id: durable_job_engine_v2().get(job_id),
        "durable_job_v2_admit": lambda request: durable_job_engine_v2().admit(request),
        "durable_job_v2_retry": lambda job_id, mode: durable_job_engine_v2().retry(job_id, mode=mode),
        "durable_job_v2_readmit_after_restart": lambda job_id: durable_job_engine_v2().readmit_after_restart(job_id),
        "durable_job_v2_cancel": lambda job_id: durable_job_engine_v2().cancel(job_id),
        "durable_job_v2_archive": lambda job_id: durable_job_engine_v2().archive(job_id),
        "durable_job_v2_delete_history": lambda job_ids: durable_job_engine_v2().delete_history(job_ids),
        "durable_job_v2_reconcile_startup": reconcile_durable_job_v2_startup,
        "workflow_runtime_v2_contract": lambda: workflow_runtime_v2().contract(),
        "workflow_runtime_v2_preflight": lambda payload: workflow_runtime_v2().preflight(payload),
        "workflow_runtime_v2_dispatch": lambda payload: workflow_runtime_v2().dispatch(payload),
        "project_workspace_v2_snapshot": lambda: project_workspace_v2().snapshot(),
        "project_workspace_v2_detail": lambda project_id: project_workspace_v2().detail(project_id),
        "project_workspace_v2_attach_workflow": lambda project_id, workflow_id: project_workspace_v2().attach_workflow(project_id, workflow_id),
        "project_workspace_v2_attach_job": lambda project_id, job_id: project_workspace_v2().attach_job(project_id, job_id),
        "project_workspace_v2_export_manifest": lambda project_id: project_workspace_v2().export_manifest(project_id),
        "artifact_library_v2_snapshot": lambda limit=120: artifact_library_v2().snapshot(limit=limit),
        "artifact_library_v2_detail": lambda artifact_id: artifact_library_v2().detail(artifact_id),
        "media_pipeline_v2_contract": lambda: media_pipeline_v2().contract(),
        "media_pipeline_v2_preflight": lambda payload: media_pipeline_v2().preflight(payload),
        "provider_adapters_v2_snapshot": lambda: provider_adapters_v2().snapshot(),
        "provider_adapters_v2_detail": lambda adapter_id: provider_adapters_v2().discover(adapter_id),
        "provider_adapters_v2_preflight": lambda adapter_id, payload: provider_adapters_v2().preflight(adapter_id, payload),
        "external_integrations_v2_snapshot": lambda: external_integrations_v2().snapshot(),
        "external_integrations_v2_detail": lambda integration_id: external_integrations_v2().detail(integration_id),
        "external_integrations_v2_launch_plan": lambda integration_id: external_integrations_v2().launch_plan(integration_id),
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
        "component_operations": component_api.operations, "component_operation": component_api.operation,
        "component_confirm_operation": component_api.confirm_operation, "component_cancel_operation": component_api.cancel_operation,
        "component_source_acceptance_snapshot": component_api.source_acceptance_snapshot,
        "component_source_acceptance": component_api.source_acceptance,
        "project_manager": project, "workflow_library_store": get("workflow_library_store"),
        "list_jobs": get("list_jobs"), "get_job": get("get_job"), "delete_job": get("delete_job"),
        "clear_terminal_history": get("clear_terminal_history"), "durable_jobs_snapshot": get("durable_jobs_snapshot"),
        "admit_durable_job": get("admit_durable_job"), "resume_durable_job": get("resume_durable_job"),
        "retry_durable_job": get("retry_durable_job"),
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
        "start_storage_scan": get("start_storage_scan"), "storage_scan_snapshot": get("storage_scan_snapshot"),
        "cancel_storage_scan": get("cancel_storage_scan"),
        "applications": get("applications"), "launch_application": get("launch_application"), "close_application": get("close_application"),
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
        "shortcut_migration_snapshot": shortcut_migration_snapshot,
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
