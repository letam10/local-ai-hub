"""Static contract checks for the dense, responsive desktop shell."""

from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "src" / "ui" / "index.html"
CSS = ROOT / "src" / "ui" / "styles.css"


class UiShellRedesignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = HTML.read_text(encoding="utf-8")
        cls.css = CSS.read_text(encoding="utf-8")

    def test_desktop_shell_is_compact_and_module_view_is_left_aligned(self) -> None:
        self.assertRegex(self.css, r"\.workspace\s*\{[^}]*grid-template-columns:\s*minmax\(176px,\s*var\(--shell-rail\)\)\s+minmax\(0,\s*1fr\)", re.S)
        module_rule = re.search(r"#module-view\s*\{([^}]*)\}", self.css, re.S)
        self.assertIsNotNone(module_rule)
        assert module_rule is not None
        self.assertIn("margin: 0;", module_rule.group(1))
        self.assertNotRegex(module_rule.group(1), r"margin\s*:\s*0\s+auto")
        self.assertRegex(module_rule.group(1), r"max-width:\s*var\(--content-max\)")
        self.assertIn("clamp(", self.css)
        self.assertIn("--shell-gutter:", self.css)
        self.assertIn("--content-max:", self.css)

    def test_dashboard_shared_contract_is_fully_styled(self) -> None:
        selectors = (
            ".dashboard-page",
            ".dashboard-hero",
            ".dashboard-metric-grid",
            ".dashboard-main-grid",
            ".dashboard-primary",
            ".dashboard-aside",
            ".dashboard-module-list",
            ".dashboard-module-row",
            ".dashboard-attention-list",
            ".dashboard-quick-actions",
        )
        for selector in selectors:
            self.assertIn(selector, self.css)
        self.assertRegex(self.css, r"\.dashboard-main-grid\s*\{[^}]*65fr[^}]*35fr", re.S)
        aside_rules = re.findall(r"\.dashboard-aside\s*\{([^}]*)\}", self.css, re.S)
        self.assertTrue(aside_rules)
        self.assertTrue(any("min-height: 0" in rule for rule in aside_rules))

    def test_collapsed_sidebar_keeps_accessible_labels_and_reclaims_track(self) -> None:
        self.assertIn(".workspace:has(.sidebar.is-collapsed)", self.css)
        self.assertRegex(self.css, r"\.sidebar\.is-collapsed\s*\{[^}]*width:\s*72px", re.S)
        self.assertIn(".sidebar.is-collapsed .nav-item > span:not(.nav-icon)", self.css)
        self.assertIn("clip-path: inset(50%)", self.css)
        self.assertIn('id="sidebar"', self.html)
        self.assertIn('aria-controls="sidebar"', self.html)

    def test_breakpoints_and_motion_contract_are_present(self) -> None:
        for breakpoint in (1300, 980, 760):
            self.assertIn(f"@media (max-width: {breakpoint}px)", self.css)
        reduced = re.search(r"@media \(prefers-reduced-motion: reduce\)\s*\{([^}]*)\}", self.css, re.S)
        self.assertIsNotNone(reduced)
        assert reduced is not None
        self.assertIn("transition-duration", reduced.group(1))
        self.assertIn("animation-duration", reduced.group(1))

    def test_tokens_existing_workspaces_and_local_assets_are_preserved(self) -> None:
        for token in ("--bg:", "--panel:", "--accent:", ':root[data-theme="light"]'):
            self.assertIn(token, self.css)
        for existing_selector in (".node-studio", ".image-workflow-rail", ".video-workflow-rail", ".image-mask-session"):
            self.assertIn(existing_selector, self.css)
        for contract in ("data-node-palette", "data-node-inspector", "data-node-canvas-focus", "data-node-preview", "--node-palette-width", "--node-inspector-width"):
            self.assertIn(contract, self.css)
        self.assertIn('href="/ui/styles.css"', self.html)
        self.assertIn('href="/ui/vendor/litegraph.css"', self.html)
        self.assertIn('src="/ui/vendor/litegraph.js"', self.html)
        self.assertIn('src="/ui/app.js"', self.html)

    def test_shell_html_is_local_and_minimally_semantic(self) -> None:
        self.assertIn('<main class="main-panel" id="main-content"', self.html)
        self.assertIn('id="module-view"', self.html)
        self.assertIn('id="sidebar-nav"', self.html)
        self.assertIn('aria-label="Điều hướng module"', self.html)
        external_urls = re.findall(r"(?:href|src)=\"([^\"]+)\"", self.html)
        self.assertTrue(external_urls)
        self.assertTrue(all(not value.startswith(("http://", "https://", "//")) for value in external_urls))


if __name__ == "__main__":
    unittest.main()
