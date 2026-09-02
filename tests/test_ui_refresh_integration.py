"""Static composition checks for the responsive UI refresh lanes."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "src" / "ui" / "index.html"
CSS_PATH = ROOT / "src" / "ui" / "styles.css"
APP_PATH = ROOT / "src" / "ui" / "app.js"
PAGES_PATH = ROOT / "src" / "ui" / "pages.js"
DASHBOARD_PATH = ROOT / "src" / "ui" / "features" / "dashboard" / "render.js"


class UiRefreshIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = HTML_PATH.read_text(encoding="utf-8")
        cls.css = CSS_PATH.read_text(encoding="utf-8")
        cls.app = APP_PATH.read_text(encoding="utf-8")
        cls.pages = PAGES_PATH.read_text(encoding="utf-8")
        cls.pages += "\n" + (ROOT / "src" / "ui" / "shared" / "rendering.js").read_text(encoding="utf-8")
        cls.dashboard = DASHBOARD_PATH.read_text(encoding="utf-8")

    def test_sidebar_toggle_targets_the_owned_aside(self) -> None:
        self.assertRegex(self.html, r'<button[^>]*aria-controls="sidebar"[^>]*>')
        self.assertRegex(self.html, r'<aside[^>]*id="sidebar"[^>]*>')
        self.assertEqual(len(re.findall(r'aria-controls="sidebar"', self.html)), 1)
        self.assertNotRegex(self.html, r'class="[^"]*\bsidebar-nav\b')
        self.assertNotIn(".sidebar-nav", self.css)

    def test_mobile_threshold_is_shared_and_old_900px_contract_is_absent(self) -> None:
        self.assertIn("const MOBILE_NAV_MAX_WIDTH = 980;", self.app)
        self.assertIn("MOBILE_NAV_MAX_WIDTH}px", self.app)
        self.assertIn("@media (max-width: 980px)", self.css)
        self.assertNotIn("MOBILE_NAV_MAX_WIDTH = 900", self.app)
        self.assertNotIn("@media (max-width: 900px)", self.css)

    def test_dashboard_emitted_classes_are_all_styled(self) -> None:
        expected = {
            "dashboard-page",
            "dashboard-onboarding",
            "dashboard-hero",
            "dashboard-metric-grid",
            "dashboard-main-grid",
            "dashboard-primary",
            "dashboard-aside",
            "dashboard-module-list",
            "dashboard-module-row",
            "dashboard-attention-list",
            "dashboard-quick-actions",
            "dashboard-storage",
            "dashboard-storage-grid",
            "dashboard-storage-volume",
            "dashboard-storage-values",
            "dashboard-storage-reason",
            "dashboard-storage-action",
            "dashboard-storage-warning",
        }
        emitted = set()
        for value in re.findall(r'class="([^"]+)"', self.dashboard):
            emitted.update(item for item in value.split() if item.startswith("dashboard-"))
        styled = set(re.findall(r"\.(dashboard-[a-z0-9_-]+)", self.css))
        self.assertEqual(expected, emitted)
        self.assertTrue(emitted.issubset(styled))

    def test_desktop_ratio_and_existing_responsive_reflow_are_preserved(self) -> None:
        main_rule = re.search(r"\.dashboard-main-grid\s*\{([^}]*)\}", self.css, re.S)
        self.assertIsNotNone(main_rule)
        assert main_rule is not None
        self.assertRegex(main_rule.group(1), r"minmax\(0,\s*65fr\)")
        self.assertRegex(main_rule.group(1), r"minmax\(0,\s*35fr\)")
        self.assertIn("@media (max-width: 1300px)", self.css)
        self.assertIn("@media (max-width: 980px)", self.css)
        self.assertIn("@media (max-width: 760px)", self.css)
        self.assertIn(".dashboard-main-grid { grid-template-columns: 1fr; }", self.css)
        self.assertIn(".dashboard-metric-grid, .dashboard-quick-actions { grid-template-columns: 1fr; }", self.css)
        self.assertIn(".dashboard-module-row { align-items: flex-start; flex-direction: column;", self.css)

    def test_left_aligned_content_does_not_regress_to_centered_wide_layout(self) -> None:
        module_rule = re.search(r"#module-view\s*\{([^}]*)\}", self.css, re.S)
        self.assertIsNotNone(module_rule)
        assert module_rule is not None
        self.assertIn("margin: 0;", module_rule.group(1))
        self.assertNotRegex(module_rule.group(1), r"margin\s*:\s*0\s+auto")
        self.assertNotIn("sidebar-nav", re.findall(r'class="[^"]+"', self.html)[0:])

    def test_bootstrap_storage_and_artifact_preview_contract_is_composed(self) -> None:
        self.assertIn("state.storage = payload.storage", self.app)
        self.assertIn("preload = \"metadata\"", self.app)
        self.assertIn("safeArtifactPreviewUrl", self.app)
        self.assertIn("data-artifact-meta", self.pages)
        self.assertIn("data-artifact-provenance", self.pages)
        self.assertIn("data-artifact-mask", self.pages)
        self.assertNotIn("arrayBuffer()", self.app)
        self.assertNotIn("FileReader", self.app)


if __name__ == "__main__":
    unittest.main()
