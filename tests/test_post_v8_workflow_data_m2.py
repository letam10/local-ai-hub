"""Milestone 2 Workflow & Data foundation regression tests."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.services.api.context import ApiContext, build_default_context
from src.services.api.router import ApiRequest
from src.services.api.router_registry import build_router
from src.services.workflow_runtime_v2 import ArtifactLibraryV2, MediaPipelineV2, ProjectWorkspaceV2, WorkflowRuntimeV2
from src.services.project_manager.manager import CreativeProjectManager, _same_file_identity


ARTIFACT_VIDEO = "artifact_" + "a" * 32
ARTIFACT_IMAGE = "artifact_" + "b" * 32
PROJECT_ID = "project_" + "c" * 32


def graph(*nodes: dict, edges: list[dict] | None = None) -> dict:
    return {
        "schema_version": 1,
        "id": "workflow_test",
        "title": "Workflow test",
        "scope": "video",
        "nodes": list(nodes),
        "edges": list(edges or []),
        "groups": [],
    }


def capability_snapshot(*ids: str) -> dict:
    return {"capabilities": [{"capability_id": item, "operational_state": "OPERATIONAL", "reason": "verified", "next_action": "continue"} for item in ids]}


class WorkflowRuntimeV2Tests(unittest.TestCase):
    def test_valid_typed_graph_produces_ready_plan_without_execution(self) -> None:
        runtime = WorkflowRuntimeV2()
        value = runtime.preflight({"graph": graph({"id": "text", "type": "prompt_text", "data": {"text": "hello"}})})
        self.assertEqual(value["status"], "completed")
        self.assertEqual(value["workflow_state"], "READY")
        self.assertTrue(value["valid"])
        self.assertEqual(value["execution"], "not_run")
        self.assertTrue(value["dry_run"])
        self.assertEqual(value["dispatch"]["status"], "unavailable")
        self.assertNotIn("path", json.dumps(value).lower())

    def test_unknown_node_is_preserved_but_never_treated_as_executable(self) -> None:
        runtime = WorkflowRuntimeV2()
        value = runtime.preflight({"graph": graph({"id": "future", "type": "future_private_node", "data": {}})})
        self.assertEqual(value["workflow_state"], "DRAFT")
        self.assertFalse(value["valid"])
        self.assertEqual(value["migration"]["status"], "manual_review")
        self.assertEqual(value["migration"]["unknown_nodes"], [{"node_id": "future", "node_type": "future_private_node", "state": "preserved_unexecutable"}])

    def test_v1_to_v2_migration_is_detached_and_v2_is_backward_compatible(self) -> None:
        runtime = WorkflowRuntimeV2()
        source = graph({"id": "text", "type": "prompt_text", "data": {"text": "hello"}})
        migrated = runtime.preflight({"graph": source})
        self.assertEqual(migrated["migration"]["source_schema_version"], 1)
        self.assertEqual(migrated["migration"]["target_schema_version"], 2)
        self.assertEqual(migrated["migration"]["actions"], ["add_empty_metadata"])
        self.assertEqual(source["schema_version"], 1)
        v2 = {**source, "schema_version": 2, "metadata": {"favorite": True, "tags": ["demo"]}}
        compatible = runtime.preflight({"graph": v2})
        self.assertEqual(compatible["workflow_state"], "READY")
        self.assertEqual(compatible["migration"]["source_schema_version"], 2)
        self.assertEqual(compatible["migration"]["actions"], [])
        invalid = runtime.preflight({"graph": {**v2, "path": "C:/private"}})
        self.assertEqual(invalid["status"], "invalid")

    def test_library_compatible_workflow_reference_is_preserved_in_preflight(self) -> None:
        runtime = WorkflowRuntimeV2()
        value = runtime.preflight({"workflow_id": "workflow-demo", "graph": graph({"id": "text", "type": "prompt_text", "data": {"text": "hello"}})})
        self.assertEqual(value["workflow_id"], "workflow-demo")
        self.assertEqual(value["workflow_state"], "READY")

    def test_artifact_type_and_capability_resource_blockers_are_truthful(self) -> None:
        records = {
            ARTIFACT_VIDEO: {"id": ARTIFACT_VIDEO, "media_type": "video/mp4", "path": "C:/private/video.mp4"},
        }
        runtime = WorkflowRuntimeV2(
            artifact_describer=records.get,
            capability_snapshot=lambda: capability_snapshot("runtime:animesr", "model:animesr-v2", "resource:gpu"),
            resource_snapshot=lambda: {
                "inventory": {"status": "available", "fit_state": "current", "gpus": [{"free_vram_evidence_state": "current", "vram_available_for_reservation_mb": 6144}]},
                "policy": {"max_heavy_gpu_jobs": 1},
                "jobs": [],
                "profiles": [{"profile_id": "video_gpu_plan", "resource_requirement": "gpu.video", "gpu_required": True, "estimated_vram_mb": 4096, "exclusive": True, "runtime_slot": "animesr", "provider_slot": "video"}],
            },
        )
        value = runtime.preflight({"graph": graph(
            {"id": "load", "type": "load_video", "data": {"asset_id": ARTIFACT_VIDEO}},
            {"id": "upscale", "type": "animesr_upscale", "data": {"scale": 2}},
            edges=[{"id": "edge_1", "source": {"node": "load", "port": "video"}, "target": {"node": "upscale", "port": "video"}}],
        )})
        self.assertEqual(value["workflow_state"], "READY")
        self.assertEqual(value["artifact_plan"]["artifacts"][0]["state"], "ready")
        self.assertEqual(value["resource_plan"]["reservations"][0]["state"], "not_reserved")
        self.assertEqual(value["resource_plan"]["reservations"][0]["profile_id"], "video_gpu_plan")
        self.assertNotIn("c:/", json.dumps(value).lower())

        incompatible = WorkflowRuntimeV2(artifact_describer=lambda _id: {"id": ARTIFACT_VIDEO, "media_type": "image/png"})
        mismatch = incompatible.preflight({"graph": graph({"id": "load", "type": "load_video", "data": {"asset_id": ARTIFACT_VIDEO}})})
        self.assertEqual(mismatch["workflow_state"], "VALIDATED")
        self.assertEqual(mismatch["artifact_plan"]["blockers"][0]["code"], "artifact_type_mismatch")

    def test_heavy_plan_uses_scheduler_records_not_browser_capacity_claims(self) -> None:
        runtime = WorkflowRuntimeV2(
            artifact_describer=lambda _id: {"id": ARTIFACT_VIDEO, "media_type": "video/mp4"},
            capability_snapshot=lambda: capability_snapshot("runtime:animesr", "model:animesr-v2", "resource:gpu"),
            resource_snapshot=lambda: {
                "inventory": {"status": "available", "fit_state": "current", "gpus": [{"free_vram_evidence_state": "current", "vram_available_for_reservation_mb": 6144}]},
                "policy": {"max_heavy_gpu_jobs": 1},
                "jobs": [{"state": "RUNNING", "resource_profile": {"gpu_required": True, "heavy_gpu": True, "exclusive_gpu": True}}],
                "profiles": [{"profile_id": "video_gpu_plan", "resource_requirement": "gpu.video", "gpu_required": True, "estimated_vram_mb": 4096, "exclusive": True, "runtime_slot": "animesr", "provider_slot": "video"}],
            },
        )
        value = runtime.preflight({"graph": graph(
            {"id": "load", "type": "load_video", "data": {"asset_id": ARTIFACT_VIDEO}},
            {"id": "upscale", "type": "animesr_upscale", "data": {"scale": 2}},
            edges=[{"id": "edge_1", "source": {"node": "load", "port": "video"}, "target": {"node": "upscale", "port": "video"}}],
        )})
        self.assertEqual(value["workflow_state"], "VALIDATED")
        self.assertEqual(value["resource_plan"]["available_heavy_slots"], 0)
        self.assertEqual(value["resource_plan"]["blockers"][0]["code"], "heavy_gpu_slot_unavailable")

    def test_payload_shape_and_invalid_graph_never_create_a_plan(self) -> None:
        runtime = WorkflowRuntimeV2()
        self.assertEqual(runtime.preflight({"graph": graph(), "path": "C:/private"})["status"], "invalid")
        invalid = runtime.preflight({"graph": graph({"id": "bad", "type": "prompt_text", "data": {"unknown": "value"}})})
        self.assertEqual(invalid["workflow_state"], "DRAFT")
        self.assertTrue(any(row["code"] == "unknown_node_property" for row in invalid["validation"]["errors"]))

    def test_dispatch_never_creates_job_without_server_owned_binding(self) -> None:
        calls = []
        runtime = WorkflowRuntimeV2(durable_admit=lambda request: calls.append(request) or {"status": "accepted"})
        payload = {"graph": graph({"id": "text", "type": "prompt_text", "data": {"text": "hello"}})}
        blocked = runtime.dispatch(payload)
        self.assertEqual(blocked["status"], "unavailable")
        self.assertEqual(blocked["code"], "workflow_runtime_execution_owner_unavailable")
        self.assertEqual(calls, [])

    def test_bound_dispatch_uses_only_server_owned_owner_worker_and_profile(self) -> None:
        calls = []

        def admit(request):
            calls.append(request)
            return {"status": "accepted", "execution": "not_run", "dry_run": True, "job": {"job_id": "jobv2_" + "d" * 32, "state": "QUEUED", "actual_execution": False}}

        runtime = WorkflowRuntimeV2(
            durable_admit=admit,
            dispatch_binding={"execution_owner": "workflow.runtime.v2", "worker_id": "workflow.runtime.worker", "light_profile_id": "cpu_light"},
        )
        result = runtime.dispatch({"graph": graph({"id": "text", "type": "prompt_text", "data": {"text": "hello"}})})
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["execution"], "not_run")
        self.assertTrue(result["dry_run"])
        self.assertEqual(calls[0], {
            "workflow_id": result["preflight"]["workflow_id"],
            "input_artifact_ids": [],
            "artifact_refs": [],
            "execution_owner": "workflow.runtime.v2",
            "worker_id": "workflow.runtime.worker",
            "resource_profile_id": "cpu_light",
        })
        self.assertNotIn("path", json.dumps(calls[0]).lower())


class WorkflowDataProjectionTests(unittest.TestCase):
    def test_atomic_write_identity_ignores_its_own_mutable_timestamp_changes(self) -> None:
        """POSIX writes update ctime; that is not a temp-file replacement."""

        initial = (10, 20, 0, 100, 200, 0o100600, 0)
        after_write = (10, 20, 128, 300, 400, 0o100600, 0)
        replacement = (10, 21, 128, 300, 400, 0o100600, 0)
        reparse = (10, 20, 128, 300, 400, 0o100600, 0x400)
        self.assertTrue(_same_file_identity(after_write, initial))
        self.assertFalse(_same_file_identity(replacement, initial))
        self.assertFalse(_same_file_identity(reparse, initial))

    def test_default_context_resolves_the_existing_library_owner_not_its_factory(self) -> None:
        from src.services.workflow_library import WorkflowLibraryStore

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = CreativeProjectManager(root / "creative_workspace.json")
            library = WorkflowLibraryStore(root / "workflow_library.json")
            saved = library.save_workflow({
                "schema_version": "workflow-entry.v1",
                "id": "workflow-demo",
                "title": "Demo workflow",
                "description": "Metadata only.",
                "scope": "video",
                "graph": graph({"id": "text", "type": "prompt_text", "data": {"text": "hello"}}),
                "revision": 1,
                "status": "draft",
                "source": "local",
                "tags": ["demo"],
                "created_at": "",
                "updated_at": "",
            }, expected_revision=0)
            self.assertTrue(saved["accepted"])
            context = build_default_context({
                "project_manager": manager,
                "workflow_library_store": lambda: library,
                "list_artifacts": lambda **_kwargs: [],
                "get_artifact": lambda _id: None,
            })
            snapshot = context.call("project_workspace_v2_snapshot")
            self.assertEqual(snapshot["workflow_library"][0]["workflow_id"], "workflow-demo")
            self.assertEqual(snapshot["execution"], "not_run")

    def test_project_and_artifact_projections_are_path_free(self) -> None:
        class Projects:
            def list_projects(self):
                return {"projects": [{"id": PROJECT_ID, "title": "Demo", "status": "active", "asset_ids": [ARTIFACT_VIDEO], "recipe_ids": ["recipe_example"], "workflow_preset": "video_creative_pipeline", "tags": ["video"]}]}

            def get_project(self, project_id):
                return {"id": project_id, "title": "Demo", "status": "active", "asset_ids": [ARTIFACT_VIDEO], "assets": [{"id": ARTIFACT_VIDEO, "media_type": "video/mp4", "path": "C:/private/video.mp4"}]}

            def get_artifact_status(self, artifact_id):
                return {"found": True, "tags": ["video"], "project_ids": [PROJECT_ID], "favorite": True}

            def workflow_gallery(self):
                return {"gallery": [{"id": "video_template", "title": "Video template", "scope": "video", "status": "partial", "node_count": 3, "path": "C:/private/template.json"}]}

        class Library:
            def list_workflows(self):
                workflow = {"id": "workflow_demo", "title": "Demo workflow", "scope": "video", "revision": 2, "status": "draft", "favorite": True, "last_opened_at": "2026-08-28T00:00:00+00:00"}
                return {"workflows": [workflow], "recent_workflows": [workflow]}

        projects = Projects()
        workspace = ProjectWorkspaceV2(projects, Library())
        snapshot = workspace.snapshot()
        self.assertEqual(snapshot["counts"], {"projects": 1, "workflows": 1, "recent_workflows": 1, "favorites": 1, "templates": 1})
        self.assertEqual(snapshot["recent_workflows"][0]["workflow_id"], "workflow_demo")
        self.assertEqual(snapshot["templates"][0]["template_id"], "video_template")
        self.assertEqual(workspace.detail(PROJECT_ID)["assets"][0]["artifact_id"], ARTIFACT_VIDEO)

        artifacts = ArtifactLibraryV2(
            artifact_lister=lambda **_kwargs: [{"id": ARTIFACT_VIDEO, "name": "clip.mp4", "media_type": "video/mp4", "size_bytes": 12, "path": "C:/private/clip.mp4"}],
            artifact_describer=lambda _id: {"id": ARTIFACT_VIDEO, "name": "clip.mp4", "media_type": "video/mp4", "path": "C:/private/clip.mp4"},
            project_manager=projects,
        ).snapshot()
        self.assertEqual(artifacts["artifacts"][0]["preview"]["url"], f"/api/artifacts/{ARTIFACT_VIDEO}")
        encoded = json.dumps({"workspace": snapshot, "artifacts": artifacts}).lower()
        self.assertNotIn("c:/", encoded)
        self.assertNotIn("path", encoded)

    def test_media_pipeline_is_closed_preflight_and_never_claims_ai_fallback(self) -> None:
        pipeline = MediaPipelineV2(lambda artifact_id: {"id": artifact_id, "media_type": "video/mp4"} if artifact_id == ARTIFACT_VIDEO else None)
        contract = pipeline.contract()
        self.assertEqual(contract["execution"], "not_run")
        self.assertIn("resize", contract["allowed_operations"])
        fallback = pipeline.preflight({"artifact_id": ARTIFACT_VIDEO, "operation": "video_upscale", "backend": "ffmpeg_scale"})
        self.assertEqual(fallback["status"], "partial")
        self.assertEqual(fallback["metadata"], {"ai_upscaler": False})
        self.assertEqual(fallback["execution_descriptor"]["input_identity"], "opaque_artifact_ids_only")
        animesr = pipeline.preflight({"artifact_id": ARTIFACT_VIDEO, "operation": "video_upscale", "backend": "animesr"})
        self.assertEqual(animesr["status"], "unavailable")
        trim = pipeline.preflight({"artifact_id": ARTIFACT_VIDEO, "operation": "trim", "options": {"start_seconds": 1, "end_seconds": 2.5}})
        self.assertEqual(trim["options"], {"start_seconds": 1.0, "end_seconds": 2.5})
        self.assertEqual(pipeline.preflight({"artifact_id": ARTIFACT_VIDEO, "operation": "resize", "options": {"width": 1279, "height": -2}})["status"], "invalid")
        self.assertEqual(pipeline.preflight({"artifact_id": ARTIFACT_VIDEO, "operation": "concat"})["status"], "invalid")
        self.assertEqual(pipeline.preflight({"artifact_id": ARTIFACT_VIDEO, "operation": "resize", "path": "C:/private"})["status"], "invalid")

    def test_project_can_attach_existing_workflow_and_job_without_copying_or_running(self) -> None:
        artifact = {"id": ARTIFACT_VIDEO, "name": "clip.mp4", "media_type": "video/mp4", "size_bytes": 12, "url": f"/api/artifacts/{ARTIFACT_VIDEO}"}
        with TemporaryDirectory() as temporary:
            manager = CreativeProjectManager(
                Path(temporary) / "creative_workspace.json",
                artifact_describer=lambda artifact_id: artifact if artifact_id == ARTIFACT_VIDEO else None,
                artifact_lister=lambda **_kwargs: [artifact],
            )
            project = manager.create_project({"title": "Workflow project"})["project"]
            manager.add_project_asset(project["id"], {"artifact_id": ARTIFACT_VIDEO})

            class Library:
                def get_workflow(self, workflow_id):
                    if workflow_id != "workflow_demo":
                        return {"status": "not_found"}
                    return {"status": "ready", "workflow": {"id": workflow_id, "title": "Demo", "revision": 1, "status": "draft", "graph": graph({"id": "text", "type": "prompt_text", "data": {"text": "hello"}})}}

                def list_workflows(self):
                    return {"workflows": [self.get_workflow("workflow_demo")["workflow"]]}

            workspace = ProjectWorkspaceV2(
                manager,
                Library(),
                durable_job_lookup=lambda job_id: {"job_id": job_id, "state": "FAILED"} if job_id == "jobv2_" + "d" * 32 else None,
            )
            attached_workflow = workspace.attach_workflow(project["id"], "workflow_demo")
            attached_job = workspace.attach_job(project["id"], "jobv2_" + "d" * 32)
            self.assertEqual(attached_workflow["status"], "completed")
            self.assertEqual(attached_job["status"], "completed")
            detail = workspace.detail(project["id"])
            self.assertEqual(detail["workflow_count"], 1)
            self.assertEqual(detail["job_count"], 1)
            exported = workspace.export_manifest(project["id"])
            self.assertEqual(exported["workflows"][0]["workflow_id"], "workflow_demo")
            self.assertEqual(exported["assets"][0]["artifact_id"], ARTIFACT_VIDEO)
            self.assertNotIn("c:/", json.dumps(exported).lower())
            self.assertNotIn("\\\\", json.dumps(exported))
            self.assertEqual(workspace.attach_workflow(project["id"], "workflow_missing")["status"], "invalid")
            self.assertEqual(workspace.attach_job(project["id"], "jobv2_" + "e" * 32)["status"], "invalid")
            self.assertEqual(manager.get_artifact_status(ARTIFACT_VIDEO)["project_ids"], [project["id"]])


class WorkflowDataApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = WorkflowRuntimeV2()
        self.pipeline = MediaPipelineV2(lambda _id: None)
        self.context = ApiContext({
            "workflow_runtime_v2_contract": self.runtime.contract,
            "workflow_runtime_v2_preflight": self.runtime.preflight,
            "workflow_runtime_v2_dispatch": self.runtime.dispatch,
            "project_workspace_v2_snapshot": lambda: {"status": "completed"},
            "project_workspace_v2_detail": lambda _id: None,
            "project_workspace_v2_export_manifest": lambda project_id: {"status": "completed", "project_id": project_id} if project_id == PROJECT_ID else None,
            "project_workspace_v2_attach_workflow": lambda project_id, workflow_id: {"status": "completed", "project_id": project_id, "workflow_id": workflow_id} if project_id == PROJECT_ID and workflow_id == "workflow_demo" else {"status": "invalid", "error": "project_workspace_v2_attachment_invalid"},
            "project_workspace_v2_attach_job": lambda project_id, job_id: {"status": "completed", "project_id": project_id, "job_id": job_id} if project_id == PROJECT_ID and job_id == "jobv2_" + "d" * 32 else {"status": "invalid", "error": "project_workspace_v2_attachment_invalid"},
            "artifact_library_v2_snapshot": lambda limit=120: {"status": "completed", "limit": limit},
            "artifact_library_v2_detail": lambda _id: None,
            "media_pipeline_v2_contract": self.pipeline.contract,
            "media_pipeline_v2_preflight": self.pipeline.preflight,
        })

    def dispatch(self, method: str, path: str, body: dict | None = None):
        return build_router().dispatch(ApiRequest(method=method, path=path, query={}, headers={}, _body_reader=lambda _strict: body or {}), self.context)

    def test_v2_router_surfaces_are_explicit_and_plan_only(self) -> None:
        contract = self.dispatch("GET", "/api/workflow-runtime/v2")
        preflight = self.dispatch("POST", "/api/workflow-runtime/v2/preflight", {"graph": graph({"id": "text", "type": "prompt_text", "data": {"text": "x"}})})
        dispatch = self.dispatch("POST", "/api/workflow-runtime/v2/dispatch", {"graph": graph({"id": "text", "type": "prompt_text", "data": {"text": "x"}})})
        artifact = self.dispatch("GET", "/api/artifact-library/v2")
        media = self.dispatch("GET", "/api/media-pipeline/v2")
        manifest = self.dispatch("GET", f"/api/project-workspace/v2/{PROJECT_ID}/manifest")
        attach_workflow = self.dispatch("POST", f"/api/project-workspace/v2/{PROJECT_ID}/workflows", {"workflow_id": "workflow_demo"})
        attach_job = self.dispatch("POST", f"/api/project-workspace/v2/{PROJECT_ID}/jobs", {"job_id": "jobv2_" + "d" * 32})
        self.assertEqual(contract.status, 200)
        self.assertEqual(preflight.status, 200)
        self.assertEqual(preflight.payload["execution"], "not_run")
        self.assertEqual(dispatch.status, 503)
        self.assertEqual(dispatch.payload["code"], "workflow_runtime_execution_owner_unavailable")
        self.assertEqual(artifact.status, 200)
        self.assertEqual(media.status, 200)
        self.assertEqual(manifest.status, 200)
        self.assertEqual(attach_workflow.status, 200)
        self.assertEqual(attach_job.status, 200)
        self.assertIsNotNone(build_router().resolve(ApiRequest(method="POST", path="/api/media-pipeline/v2/preflight", query={}, headers={})))
        favorite_route = build_router().resolve(ApiRequest(method="POST", path="/api/workflow-library/workflow_demo/favorite", query={}, headers={}))
        opened_route = build_router().resolve(ApiRequest(method="POST", path="/api/workflow-library/workflow_demo/opened", query={}, headers={}))
        self.assertEqual(favorite_route[0].route_id, "workflows.favorite")
        self.assertEqual(opened_route[0].route_id, "workflows.opened")


class WorkflowDataUiContractTests(unittest.TestCase):
    def test_projects_route_refreshes_m2_projections_before_rendering_workspace(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "src" / "ui" / "app.js").read_text(encoding="utf-8")
        self.assertIn('if (route === "projects")', source)
        self.assertIn('refreshCreative({ renderView: false })', source)
        self.assertIn('if (routeId() === "projects") render();', source)


if __name__ == "__main__":
    unittest.main()
