"""Static navigation semantics and responsive sidebar contract tests."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "src" / "ui" / "app.js"
PAGES_PATH = ROOT / "src" / "ui" / "pages.js"


class NavigationUxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = APP_PATH.read_text(encoding="utf-8")
        cls.pages = PAGES_PATH.read_text(encoding="utf-8")

    def test_navigation_renders_semantic_groups_and_named_controls(self) -> None:
        self.assertIn('nav.setAttribute("aria-label", "Module navigation")', self.app)
        self.assertIn('class="nav-group-section" role="group" aria-labelledby=', self.app)
        self.assertIn('class="nav-group" id="' + "$" + "{groupId}" + '"', self.app)
        self.assertIn('class="nav-label"', self.app)
        self.assertIn('aria-label="' + "$" + "{escapeHtml(label)}" + '"', self.app)
        self.assertIn('aria-hidden="true"', self.app)
        self.assertIn('aria-current="page"', self.app)
        self.assertIn('title="' + "$" + "{escapeHtml(label)}" + '"', self.app)

    def test_all_navigation_routes_remain_data_driven_and_reachable(self) -> None:
        route_ids = re.findall(r'\["([a-z][a-z0-9_-]*)",\s*"[^"]+",\s*"[^"]+"\]', self.pages)
        self.assertGreaterEqual(len(route_ids), 14)
        self.assertIn("NAVIGATION.flatMap((group) => group.items)", self.app)
        self.assertIn('data-route="' + "$" + "{escapeHtml(id)}" + '"', self.app)
        self.assertIn("window.location.hash =", self.app)
        self.assertEqual(len(route_ids), len(set(route_ids)))

    def test_versioned_storage_preference_is_failure_safe(self) -> None:
        self.assertIn('const SIDEBAR_PREFERENCE_KEY = "local-ai-hub-sidebar-v1"', self.app)
        self.assertIn("const SIDEBAR_PREFERENCE_VERSION = 1", self.app)
        self.assertIn("JSON.parse(raw)", self.app)
        self.assertIn("preference?.version === SIDEBAR_PREFERENCE_VERSION", self.app)
        self.assertIn("try { return window.localStorage?.getItem(key)", self.app)
        self.assertIn("try { window.localStorage?.setItem(key, value)", self.app)
        self.assertIn("catch { return null; }", self.app)
        self.assertIn("catch { /* Storage can be disabled or unavailable. */ }", self.app)

    def test_desktop_collapse_and_mobile_drawer_have_separate_state(self) -> None:
        self.assertIn("sidebarState.desktopCollapsed", self.app)
        self.assertIn("sidebarState.mobileOpen", self.app)
        self.assertIn('sidebar.classList.toggle("is-collapsed", sidebarState.desktopCollapsed)', self.app)
        self.assertIn('sidebar.classList.toggle("is-open", sidebarState.mobileOpen)', self.app)
        self.assertIn('sidebar.classList.remove("is-collapsed")', self.app)
        self.assertIn('sidebar.classList.remove("is-open")', self.app)
        self.assertIn("persistSidebarPreference()", self.app)
        self.assertIn("const MOBILE_NAV_MAX_WIDTH = 900", self.app)

    def test_aria_state_and_resize_are_synchronized_without_focus_trap(self) -> None:
        self.assertIn("const syncSidebarState = () =>", self.app)
        self.assertIn('sidebarToggle.setAttribute("aria-expanded", String(sidebarState.mobileOpen))', self.app)
        self.assertIn('sidebarToggle.setAttribute("aria-expanded", String(!sidebarState.desktopCollapsed))', self.app)
        self.assertIn('sidebarToggle.setAttribute("aria-label", sidebarState.mobileOpen ? "Close navigation" : "Open navigation")', self.app)
        self.assertIn('sidebarToggle.setAttribute("aria-label", sidebarState.desktopCollapsed ? "Expand navigation" : "Collapse navigation")', self.app)
        self.assertIn('window.addEventListener("resize", syncSidebarState)', self.app)
        self.assertIn("syncSidebarState();", self.app)
        self.assertNotIn("aria-modal", self.app[self.app.index("const syncSidebarState") : self.app.index("const toggleSidebar")])

    def test_route_selection_closes_only_mobile_drawer(self) -> None:
        self.assertIn("const closeMobileSidebar = () =>", self.app)
        self.assertIn("closeMobileSidebar(); window.location.hash", self.app)
        self.assertNotIn('sidebar?.classList.remove("is-open"); sidebarToggle?.setAttribute("aria-expanded", "false")', self.app)


if __name__ == "__main__":
    unittest.main()
