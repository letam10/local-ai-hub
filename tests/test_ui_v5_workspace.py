from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class V5WorkspaceUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = (ROOT / "src" / "ui" / "app.js").read_text(encoding="utf-8")
        cls.pages = (ROOT / "src" / "ui" / "pages.js").read_text(encoding="utf-8")
        cls.shared_renderer = (ROOT / "src" / "ui" / "shared" / "rendering.js").read_text(encoding="utf-8")
        cls.renderers = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "src" / "ui" / "features").rglob("*.js"))
        cls.nodes = (ROOT / "src" / "ui" / "features" / "node_studio" / "studio.js").read_text(encoding="utf-8")
        cls.adapter = (ROOT / "src" / "ui" / "workflow_library.js").read_text(encoding="utf-8")
        cls.styles = (ROOT / "src" / "ui" / "styles.css").read_text(encoding="utf-8")
        cls.version = (ROOT / "src" / "shared" / "version.py").read_text(encoding="utf-8")
        cls.docs = (ROOT / "docs" / "V5_UNIFIED_WORKSPACE.md").read_text(encoding="utf-8")

    def test_dashboard_and_node_studio_show_truthful_library_state(self) -> None:
        self.assertIn("workflowLibrary", self.app)
        self.assertIn("workflowLibraryState", self.pages)
        self.assertIn("data-status=", self.shared_renderer + self.renderers)
        self.assertIn("data-workflow-library-status", self.nodes)
        self.assertIn("V5-D", self.adapter)
        self.assertIn("partial", self.adapter)

    def test_adapter_does_not_invent_fetch_route_or_execute_graph(self) -> None:
        self.assertNotIn("fetch(", self.adapter)
        self.assertIn('dataset.graphAction = "save-library"', self.nodes)
        self.assertIn('data-graph-action="run"', self.nodes)
        self.assertIn("run()", self.nodes)
        self.assertNotIn("this.run({ auto: true })", self.nodes)
        self.assertIn("never schedules a workload", self.nodes)
        self.assertNotIn("execute_graph(", self.adapter)

    def test_accessibility_and_typed_workspace_contract_remain_present(self) -> None:
        for marker in ("aria-label", "data-graph-search", "data-graph-inspector", "data-graph-minimap", "data-graph-action"):
            self.assertIn(marker, self.nodes)
        self.assertIn("workflow-library-state", self.styles)
        self.assertIn("workflowLibraryState", self.shared_renderer)

    def test_product_version_and_docs_are_v5(self) -> None:
        self.assertIn('PRODUCT_VERSION = "7.1.0"', self.version)
        for marker in ("Dashboard", "Project/Workspace", "Capability", "Workflow/Nodes", "Job", "Artifact/Preview", "not_run", "V5-D"):
            self.assertIn(marker, self.docs)


if __name__ == "__main__":
    unittest.main()
