from __future__ import annotations

import json
import unittest
from pathlib import Path


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
        for token in ("workspace-state", "artifact-preview-dialog", "@media (max-width: 900px)", "prefers-reduced-motion"):
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

    def test_public_job_keeps_provenance_and_allows_safe_unavailable_retry(self) -> None:
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
        public = public_job(record)
        self.assertNotIn("input", public)
        self.assertNotIn("resume_data", public)
        self.assertTrue(public["resumable"])
        self.assertEqual(public["result"]["provenance"][0]["node_type"], "flux_generate")

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
