from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class Milestone3ContractTests(unittest.TestCase):
    def test_unified_state_and_artifact_actions_are_in_hub(self) -> None:
        pages = (ROOT / "src" / "ui" / "pages.js").read_text(encoding="utf-8")
        app = (ROOT / "src" / "ui" / "app.js").read_text(encoding="utf-8")
        node = (ROOT / "src" / "ui" / "node_studio.js").read_text(encoding="utf-8")
        css = (ROOT / "src" / "ui" / "styles.css").read_text(encoding="utf-8")
        for token in (
            "workspaceState",
            "workspace-state",
            "data-preview-artifact",
            "data-job-filter",
            "provenanceList",
            "next_action",
        ):
            self.assertIn(token, pages)
        for token in ("global-state", "data-refresh-api", "artifact-preview-dialog", "sidebar-toggle"):
            self.assertIn(token, app)
        for token in (
            "local-ai-hub-workflows-v1",
            "duplicateWorkflow",
            "renameWorkflow",
            "loadRecent",
            "beforeunload",
            "validateNodeGraph",
            "data-graph-save-state",
        ):
            self.assertIn(token, node)
        for token in ("workspace-state", "artifact-preview-dialog", "@media (max-width: 980px)", "prefers-reduced-motion"):
            self.assertIn(token, css)
        self.assertNotIn('target="_blank"', pages + node)

    def test_capability_catalog_exposes_truthful_action_contract(self) -> None:
        from src.services.api.core import TOOL_COMPONENTS, tool_catalog

        statuses = [{"id": component, "component_status": "installed", "name": component} for component in set(TOOL_COMPONENTS.values())]
        catalog = tool_catalog(statuses)
        self.assertTrue(catalog)
        for item in catalog:
            self.assertIn("tool_status", item)
            self.assertIn("reason", item)
            self.assertIn("action", item)
            self.assertTrue(item["action"])

    def test_public_job_is_session_truthful_and_keeps_provenance(self) -> None:
        from src.services.api.jobs import public_job

        record = {
            "id": "job_contract",
            "contract_version": "job.v2",
            "tool": "generate_flux",
            "status": "unavailable",
            "input": {"path": r"D:\private\secret.png"},
            "resume_data": {"path": r"D:\private\secret.png"},
            "result": {"provenance": [{"artifact_id": "artifact_0123456789abcdef0123456789abcdef", "url": "/api/artifacts/artifact_0123456789abcdef0123456789abcdef", "node_type": "flux_generate"}]},
        }
        stale = public_job(record)
        self.assertNotIn("input", stale)
        self.assertNotIn("resume_data", stale)
        self.assertFalse(stale["resumable"])
        self.assertIn("workspace", stale["next_action"])
        in_session = public_job({**record, "resume_available": True})
        self.assertTrue(in_session["resumable"])
        self.assertNotIn("next_action", in_session)
        self.assertEqual(in_session["result"]["provenance"][0]["node_type"], "flux_generate")

    def test_restart_clears_durable_runner_availability(self) -> None:
        from src.services.api import jobs

        durable = {
            "job_restart": {
                "id": "job_restart",
                "tool": "probe_media",
                "status": "unavailable",
                "resume_data": {"asset_id": "asset_restart"},
                "resume_available": True,
            }
        }
        original_path = jobs.JOBS_PATH
        with tempfile.TemporaryDirectory() as temporary:
            jobs.JOBS_PATH = Path(temporary) / "jobs.json"
            jobs.JOBS_PATH.write_text(json.dumps(durable), encoding="utf-8")
            with jobs._lock:
                snapshot = dict(jobs._jobs)
                jobs._jobs.clear()
            try:
                jobs._load()
                public = jobs.get_job("job_restart")
            finally:
                with jobs._lock:
                    jobs._jobs.clear()
                    jobs._jobs.update(snapshot)
                jobs.JOBS_PATH = original_path
        assert public is not None
        self.assertFalse(public["resumable"])
        self.assertIn("workspace", public["next_action"])

    def test_retry_preserves_light_runner_weight_and_device(self) -> None:
        from src.services.job_manager import manager as manager_module

        manager = manager_module.HubJobManager()
        runner = lambda _payload, _context: {"status": "unavailable"}
        manager._runners["job_light"] = runner
        manager._runner_specs["job_light"] = manager_module.RunnerSpec(runner, "cpu", False, 1.0)
        record = {"id": "job_light", "tool": "probe_media", "status": "unavailable", "resume_data": {"asset_id": "asset_light"}, "device": "cpu"}
        with patch.object(manager_module, "get_job_internal", return_value=record), patch.object(manager, "submit", return_value={"id": "job_retry"}) as submit:
            ok, result = manager.resume("job_light")
        self.assertTrue(ok)
        self.assertEqual(result["id"], "job_retry")
        self.assertFalse(submit.call_args.kwargs["heavy"])
        self.assertEqual(submit.call_args.kwargs["device"], "cpu")

    def test_runner_retry_cache_is_bounded(self) -> None:
        from src.services.job_manager import manager as manager_module

        manager = manager_module.HubJobManager()
        runner = lambda _payload, _context: {"status": "unavailable"}
        for index in range(manager_module.MAX_RUNNER_SPECS + 4):
            job_id = f"job_{index}"
            manager._runners[job_id] = runner
            manager._runner_specs[job_id] = manager_module.RunnerSpec(runner, None, False, float(index))
        with patch.object(manager_module, "update_job") as update_job:
            with manager._lock:
                manager._trim_runner_specs_locked()
        self.assertEqual(len(manager._runner_specs), manager_module.MAX_RUNNER_SPECS)
        self.assertEqual(len(manager._runners), manager_module.MAX_RUNNER_SPECS)
        self.assertEqual(update_job.call_count, 4)

    def test_recent_catalog_updates_title_selects_copy_and_caps_at_twelve(self) -> None:
        script = """
        const module = await import('./src/ui/node_studio.js');
        const index = [{id: 'copy', title: 'QA Milestone 3 Copy', source: 'duplicate'},
          ...Array.from({length: 14}, (_, i) => ({id: `wf-${i}`, title: `Workflow ${i}`, source: 'autosave'}))];
        const options = module.buildRecentWorkflowOptions(index, 'copy');
        if (options.length !== 12) throw new Error(`expected 12 options, got ${options.length}`);
        if (options[0].id !== 'copy' || options[0].title !== 'QA Milestone 3 Copy' || !options[0].selected) throw new Error('copy is not selected with updated title');
        if (options.some((item) => item.id === 'wf-11')) throw new Error('catalog exceeded twelve entries');
        """
        result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_milestone_documentation_maps_source_and_safety_gate(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        architecture = (ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
        milestone = (ROOT / "docs" / "MILESTONE_3_UNIFIED_CREATIVE_UX.md").read_text(encoding="utf-8")
        for document in (readme, architecture, milestone):
            self.assertIn("milestone 3", document.lower())
            self.assertIn("resource", document.lower())
            self.assertIn("partial", document.lower())
        self.assertIn("Config/*.example.json", readme)
        self.assertIn("local-ai-hub-workflows-v1", milestone)
        self.assertIn("deferred due GPU/resource contention", milestone)

    def test_video_unavailable_template_remains_honest(self) -> None:
        graph = json.loads((ROOT / "workflows" / "video_generation_unavailable.json").read_text(encoding="utf-8"))
        serialized = json.dumps(graph, ensure_ascii=False)
        self.assertIn("unavailable", serialized)
        from src.services.node_studio.registry import get_definition

        definition = get_definition("video_generate")
        self.assertIsNotNone(definition)
        self.assertEqual(definition.status, "unavailable")
        self.assertTrue(definition.status_action)


if __name__ == "__main__":
    unittest.main()
