from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.services.api.v5_productization import (
    PRODUCT_SURFACE_SCHEMA_VERSION,
    durable_jobs_snapshot,
    project_media_operation_scope,
    project_job_recovery,
    project_product_surface,
    project_runtime_evidence,
    project_storage_projection,
    project_workflow_library,
)


ROOT = Path(__file__).resolve().parents[1]


def control_plane() -> dict[str, object]:
    return {
        "status": "partial",
        "reason": "Static capability evidence is bounded.",
        "next_action": "Review the module plan before runtime work.",
        "registry": {
            "status": "partial",
            "records": [
                {
                    "id": "core:workflow",
                    "provider": "workflow_packages",
                    "component": "workflow_packages",
                    "status": "partial",
                    "version": "workflow-package.v1",
                    "reason": "Static package metadata only.",
                    "next_action": "Review package evidence.",
                },
            ],
        },
        "module_manager": {
            "status": "not_published",
            "reason": "No install manifest is published.",
            "next_action": "Publish a verified HTTPS manifest before planning installation.",
        },
    }


class V5EndToEndProductizationTests(unittest.TestCase):
    def test_runtime_evidence_product_projection_is_allowlisted_and_truthful(self) -> None:
        marker = "client-private-runtime-marker"
        failed = project_runtime_evidence({
            "schema_version": "runtime-evidence-projection.v1",
            "subject": "media_overlay_cpu_acceptance",
            "status": "partial",
            "outcome": "error",
            "execution": "attempted",
            "failure_class": "unknown",
            "invocation_count": 1,
            "cleanup": {"processes_remaining": 0, "temp_cleaned": True},
            "artifact_published": False,
            "source_overwrite_checked": True,
            "source_overwritten": False,
            "reason": marker,
            "next_action": marker,
            "source": marker,
            "fingerprint": marker,
            "command": marker,
        })
        self.assertEqual(failed["status"], "unavailable")
        self.assertEqual(failed["outcome"], "error")
        self.assertEqual(failed["execution"], "attempted")
        self.assertTrue(failed["cleanup"]["temp_cleaned"])
        self.assertFalse(failed["artifact_published"])
        self.assertTrue(failed["source_overwrite_checked"])
        self.assertFalse(failed["source_overwritten"])
        self.assertNotIn(marker, json.dumps(failed))
        self.assertNotIn("source", failed)
        checked_false = {**failed, "source_overwrite_checked": True, "source_overwritten": False}
        self.assertEqual(project_runtime_evidence(checked_false)["source_overwritten"], False)
        checked_true = {**failed, "source_overwrite_checked": True, "source_overwritten": True}
        true_projection = project_runtime_evidence(checked_true)
        self.assertEqual(true_projection["source_overwritten"], True)
        self.assertEqual(true_projection["status"], "unavailable")
        self.assertNotEqual(true_projection["status"], "operational")
        malformed = {**failed, "source_overwrite_checked": False, "source_overwritten": False}
        malformed_projection = project_runtime_evidence(malformed)
        self.assertIsNone(malformed_projection["source_overwritten"])
        for outcome, expected_reason in (
            ("blocked", "The bounded media acceptance was blocked before execution."),
            ("not_run", "No bounded media acceptance invocation was recorded."),
        ):
            safe_not_run = project_runtime_evidence({
                "schema_version": "runtime-evidence-projection.v1",
                "subject": "media_overlay_cpu_acceptance",
                "status": "unavailable",
                "outcome": outcome,
                "execution": "not_run",
                "failure_class": None,
                "invocation_count": 0,
                "cleanup": {"processes_remaining": 0, "temp_cleaned": True},
                "artifact_published": False,
                "source_overwrite_checked": False,
                "source_overwritten": None,
            })
            self.assertEqual(safe_not_run["outcome"], outcome)
            self.assertEqual(safe_not_run["execution"], "not_run")
            self.assertEqual(safe_not_run["status"], "unavailable")
            self.assertEqual(safe_not_run["reason"], expected_reason)
        fallback = project_runtime_evidence({"status": "operational", "reason": marker})
        self.assertEqual(fallback["status"], "unavailable")
        self.assertEqual(fallback["execution"], "not_run")

        surface = project_product_surface(
            control_plane={**control_plane(), "runtime_evidence": failed},
            health={"status": "healthy", "gpu": {"status": "unavailable"}, "disk": {}},
            jobs=[],
            workflow_library={},
        )
        self.assertEqual(surface["execution"], "not_run")
        self.assertTrue(surface["dry_run"])
        self.assertEqual(surface["capabilities"]["runtime_evidence"]["outcome"], "error")
        self.assertEqual(surface["capabilities"]["media_operation_scope"]["operations"], ["video_grade", "logo_overlay", "encode"])
        self.assertFalse(surface["capabilities"]["media_operation_scope"]["evidence_verified"])
        self.assertNotIn(marker, json.dumps(surface))

    def test_runtime_evidence_malformed_nested_values_fail_closed_without_type_errors(self) -> None:
        safe = {
            "schema_version": "runtime-evidence-projection.v1",
            "subject": "media_overlay_cpu_acceptance",
            "status": "unavailable",
            "outcome": "error",
            "execution": "attempted",
            "failure_class": "unknown",
            "invocation_count": 1,
            "cleanup": {"processes_remaining": 0, "temp_cleaned": True},
            "artifact_published": False,
            "source_overwrite_checked": False,
            "source_overwritten": None,
        }
        marker = "malformed-runtime-marker"
        cases = [
            ("status", []),
            ("status", {}),
            ("outcome", []),
            ("outcome", {}),
            ("failure_class", []),
            ("failure_class", {}),
            ("cleanup", []),
            ("cleanup", {}),
            ("cleanup", {"processes_remaining": [], "temp_cleaned": True}),
            ("cleanup", {"processes_remaining": 0, "temp_cleaned": {}}),
        ]
        for field, value in cases:
            malformed = {**safe, field: value}
            projection = project_runtime_evidence(malformed)
            self.assertEqual(projection["status"], "unavailable", field)
            self.assertEqual(projection["outcome"], "not_run", field)
            self.assertEqual(projection["execution"], "not_run", field)
            self.assertNotIn(marker, json.dumps(projection))

        surface = project_product_surface(
            control_plane={**control_plane(), "runtime_evidence": {**safe, "status": []}},
            health={"status": "healthy", "gpu": {"status": "unavailable"}, "disk": {}},
            jobs=[],
            workflow_library={},
        )
        self.assertEqual(surface["capabilities"]["runtime_evidence"]["outcome"], "not_run")
        self.assertEqual(surface["execution"], "not_run")
        self.assertTrue(surface["dry_run"])

        scope = project_media_operation_scope({
            "schema_version": "runtime-operation-scope.v1",
            "subject": "media_overlay_cpu_acceptance",
            "status": "operational",
            "execution": "completed",
            "evidence_verified": True,
            "operations": ["video_grade", "logo_overlay", "encode"],
            "available_operations": ["video_grade", "logo_overlay", "encode"],
            "operation_status": {"video_grade": "operational", "logo_overlay": "operational", "encode": "operational"},
        })
        self.assertEqual(scope["available_operations"], ["video_grade", "logo_overlay", "encode"])
        malformed_scope = dict(scope)
        malformed_scope["operation_status"] = {"video_grade": [], "logo_overlay": "operational", "encode": "operational"}
        self.assertFalse(project_media_operation_scope(malformed_scope)["evidence_verified"])
        malformed_scope["operation_status"] = []
        self.assertFalse(project_media_operation_scope(malformed_scope)["evidence_verified"])

    def test_storage_projection_is_fixed_bounded_and_truthful(self) -> None:
        marker = "C:/private/secret"
        projection = project_storage_projection({
            "volumes": [
                {"id": "c", "label": marker, "status": "available", "total_bytes": 1000, "free_bytes": 10, "used_bytes": 990, "reason": marker},
                {"id": "d", "status": "unavailable", "total_bytes": 999, "free_bytes": 1, "used_bytes": 998, "reason": marker},
            ],
        })

        self.assertEqual([item["id"] for item in projection["volumes"]], ["c", "d"])
        self.assertEqual([item["label"] for item in projection["volumes"]], ["C:", "D:"])
        self.assertEqual(projection["volumes"][0]["total_bytes"], 1000)
        self.assertTrue(projection["volumes"][0]["low_space"])
        self.assertEqual(projection["volumes"][1]["status"], "unavailable")
        self.assertIsNone(projection["volumes"][1]["total_bytes"])
        self.assertNotIn(marker, json.dumps(projection))

    def test_dashboard_projection_is_server_owned_and_deterministic(self) -> None:
        marker = "client-private-marker"
        first = project_product_surface(
            control_plane=control_plane(),
            health={"status": "healthy", "gpu": {"status": "unavailable"}, "disk": {}},
            jobs=[{"id": "job_1", "tool": "image", "status": "interrupted", "next_action": "Create a new task."}],
            workflow_library={"status": "ready", "library_revision": 2, "workflows": []},
            storage={"volumes": [{"id": "c", "status": "available", "total_bytes": 100, "free_bytes": 80, "used_bytes": 20}]},
        )
        second = project_product_surface(
            control_plane=control_plane(),
            health={"status": "healthy", "gpu": {"status": "unavailable"}, "disk": {}},
            jobs=[{"id": "job_1", "tool": "image", "status": "interrupted", "next_action": "Create a new task."}],
            workflow_library={"status": "ready", "library_revision": 2, "workflows": []},
            storage={"volumes": [{"id": "c", "status": "available", "total_bytes": 100, "free_bytes": 80, "used_bytes": 20}]},
        )
        self.assertEqual(first, second)
        self.assertEqual(first["schema_version"], PRODUCT_SURFACE_SCHEMA_VERSION)
        self.assertEqual(first["execution"], "not_run")
        self.assertTrue(first["dry_run"])
        self.assertEqual(first["capabilities"]["module_plan_status"], "not_published")
        self.assertNotIn(marker, json.dumps(first))

    def test_job_recovery_projection_never_elevates_resume_state(self) -> None:
        records = project_job_recovery([
            {"id": "job-interrupted", "tool": "image", "status": "interrupted", "resumable": False, "input": "client-private-marker"},
            {"id": "job-failed", "tool": "image", "status": "failed", "resumable": True, "next_action": "Retry only with the existing runner."},
            {"id": "job-completed", "tool": "image", "status": "completed", "resumable": True},
        ])
        self.assertEqual(records["counts"]["interrupted"], 1)
        self.assertEqual(records["counts"]["recoverable"], 1)
        self.assertFalse(records["records"][0]["resumable"])
        self.assertNotIn("client-private-marker", json.dumps(records))

    def test_durable_store_projection_is_read_only_and_adapter_bound(self) -> None:
        from src.services.api.jobs import DurableJobStore

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "durable-jobs.json"
            store = DurableJobStore(path)
            store.put({"id": "jobv5_" + "a" * 32, "status": "interrupted", "retry_available": True, "execution": "not_run", "dry_run": True})
            store.close()
            snapshot = durable_jobs_snapshot(path)
        self.assertEqual(snapshot["execution"], "not_run")
        self.assertFalse(snapshot["records"][0]["resumable"])
        self.assertNotIn("retry_available", json.dumps(snapshot))

    def test_module_preflight_remains_not_published_and_non_mutating(self) -> None:
        before = json.dumps(control_plane(), sort_keys=True)
        surface = project_product_surface(
            control_plane=control_plane(),
            health={"status": "healthy", "gpu": {"status": "healthy", "name": "RTX 4060"}, "disk": {"free_bytes": 1}},
            jobs=[],
            workflow_library={},
        )
        self.assertEqual(surface["capabilities"]["module_plan_status"], "not_published")
        self.assertEqual(surface["execution"], "not_run")
        self.assertEqual(json.dumps(control_plane(), sort_keys=True), before)

    def test_workflow_library_recovery_and_conflict_are_visible(self) -> None:
        recovery = project_workflow_library({
            "status": "partial",
            "library_revision": 4,
            "workflows": [{"id": "safe-workflow", "title": "Safe", "scope": "image", "revision": 2, "status": "draft", "source": "local", "graph": {"client_private": "client-private-marker"}}],
            "recovery": {"status": "recovery_required", "reason": "Library bytes are invalid.", "action": "Use validated user-mediated recovery."},
        })
        self.assertEqual(recovery["status"], "recovery_required")
        self.assertEqual(recovery["workflow_count"], 1)
        self.assertNotIn("client-private-marker", json.dumps(recovery))

    def test_api_ui_and_workspace_use_the_composed_contract(self) -> None:
        api = (ROOT / "src" / "services" / "api" / "api_server.py").read_text(encoding="utf-8")
        adapter = (ROOT / "src" / "services" / "api" / "v5_productization.py").read_text(encoding="utf-8")
        ui = (ROOT / "src" / "ui" / "app.js").read_text(encoding="utf-8")
        pages = (ROOT / "src" / "ui" / "pages.js").read_text(encoding="utf-8")
        shared = (ROOT / "src" / "ui" / "shared" / "rendering.js").read_text(encoding="utf-8")
        renderers = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "src" / "ui" / "features").rglob("*.js"))
        for marker in ("/api/workflow-library", "/api/durable-jobs", "capability_control_plane", "project_product_surface", "storage_summary", '"storage": product_surface["storage"]'):
            self.assertIn(marker, api)
        bootstrap = api[api.index("def _bootstrap_payload"):api.index("\ndef _preset_summaries", api.index("def _bootstrap_payload"))]
        self.assertIn("dashboard_volume_snapshot()", bootstrap)
        self.assertNotIn("storage_summary", bootstrap)
        for marker in ("project_job_recovery", "project_storage_projection", "project_workflow_library", "execution", "not_run", "dry_run"):
            self.assertIn(marker, adapter)
        for marker in ("getCapabilities", "getWorkflowLibrary", "state.capabilities", "workflowLibraryAdapter"):
            self.assertIn(marker, ui)
        for marker in ("source.capabilities", "module_manager", "dashboard-page", "nextAction"):
            self.assertIn(marker, pages + shared + renderers)
        self.assertNotIn("subprocess", adapter)
        self.assertNotIn("fetch(", adapter)

    def test_job_recovery_actions_keep_durable_endpoint_separate(self) -> None:
        pages = (ROOT / "src" / "ui" / "pages.js").read_text(encoding="utf-8")
        shared = (ROOT / "src" / "ui" / "shared" / "rendering.js").read_text(encoding="utf-8")
        renderers = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "src" / "ui" / "features").rglob("*.js"))
        app = (ROOT / "src" / "ui" / "app.js").read_text(encoding="utf-8")
        self.assertIn('const durable = job.source === "durable";', renderers)
        self.assertIn('data-resume-durable-job="${escapeHtml(job.id)}"', renderers)
        self.assertIn('data-resume-job="${escapeHtml(job.id)}"', renderers)
        self.assertIn("[data-resume-durable-job]", app)
        self.assertIn("resumeDurableJob", app)
        durable_branch = renderers[renderers.index('data-resume-durable-job='):renderers.index('data-resume-job=')]
        self.assertNotIn("data-resume-job", durable_branch)


if __name__ == "__main__":
    unittest.main()
