import json
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGES = ROOT / "src" / "ui" / "pages.js"
DASHBOARD_FEATURE = ROOT / "src" / "ui" / "features" / "dashboard" / "render.js"


def dashboard_source():
    return DASHBOARD_FEATURE.read_text(encoding="utf-8")


def render_dashboard(state):
    serialized = json.dumps(state, ensure_ascii=True)
    script = f"""
import {{ renderPage }} from './src/ui/pages.js';
const state = {serialized};
const before = JSON.stringify(state);
const html = renderPage('dashboard', state);
if (JSON.stringify(state) !== before) throw new Error('dashboard state mutated');
process.stdout.write(html);
"""
    result = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=False,
    )
    if result.returncode:
        raise AssertionError(f"dashboard render failed: {result.stderr}")
    return result.stdout


class DashboardInformationLayoutTests(unittest.TestCase):
    def test_dashboard_information_hierarchy_uses_shared_classes(self):
        source = dashboard_source()
        for class_name in (
            "dashboard-page",
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
            "dashboard-storage-warning",
        ):
            self.assertIn(class_name, source)
        self.assertNotRegex(source, r"\bstyle\s*=")
        self.assertNotIn('class="metric-grid"', source)
        self.assertNotIn('workspace-grid workspace-grid--two', source)

    def test_dashboard_module_priority_is_deterministic_and_does_not_mutate_components(self):
        state = {
            "health": {"status": "healthy"},
            "components": [
                {"id": "operational", "name": "Omega operational", "status": "operational"},
                {"id": "partial", "name": "Alpha partial", "status": "partial"},
                {"id": "unavailable", "name": "Zeta unavailable", "status": "unavailable"},
            ],
        }
        first = render_dashboard(state)
        second = render_dashboard(state)
        self.assertEqual(first, second)
        self.assertLess(first.index("Zeta unavailable"), first.index("Alpha partial"))
        self.assertLess(first.index("Alpha partial"), first.index("Omega operational"))

        source = dashboard_source()
        self.assertIn("components.map", source)
        self.assertIn("modules.sort", source)
        self.assertIn("statusRank", source)
        self.assertNotIn("components.sort(", source)

    def test_dashboard_dynamic_values_are_escaped_and_empty_safe(self):
        html = render_dashboard(
            {
                "health": {"status": "healthy"},
                "components": [
                    {
                        "id": "unsafe",
                        "name": '<img src=x onerror=alert(1)>',
                        "kind": "<script>alert(1)</script>",
                        "status": "partial",
                    }
                ],
                "jobs": [{"id": "<job>", "status": "failed", "message": "<script>bad()</script>"}],
            }
        )
        self.assertNotIn("<img src=x", html)
        self.assertNotIn("<script>bad()", html)
        self.assertIn("&lt;img src=x", html)
        self.assertIn("&lt;script&gt;bad()&lt;/script&gt;", html)

        empty = render_dashboard({"health": {"status": "healthy"}})
        self.assertIn("Chưa có module", empty)
        self.assertIn("Không có hạng mục cần chú ý", empty)
        self.assertNotIn("undefined", empty)
        self.assertNotIn("[object Object]", empty)

    def test_dashboard_renders_server_owned_c_and_d_storage_with_low_space_action(self):
        html = render_dashboard({
            "health": {"status": "healthy"},
            "storage": {
                "status": "partial",
                "execution": "not_run",
                "volumes": [
                    {"id": "c", "label": "C:", "status": "available", "total_bytes": 100 * 1024**3, "free_bytes": 10 * 1024**3, "used_bytes": 90 * 1024**3, "low_space": True, "reason": "Low space", "next_action": "Review cache."},
                    {"id": "d", "label": "D:", "status": "unavailable", "total_bytes": None, "free_bytes": None, "used_bytes": None, "low_space": None, "reason": "Unavailable", "next_action": "Mount volume."},
                ],
            },
        })
        self.assertIn("C:", html)
        self.assertIn("D:", html)
        self.assertIn("Total", html)
        self.assertIn("Free", html)
        self.assertIn("Used", html)
        self.assertIn("Low-space warning", html)
        self.assertIn("Review cache.", html)
        self.assertNotIn("undefined", html)
        self.assertNotIn("[object Object]", html)

    def test_dashboard_routes_are_existing_and_no_new_api(self):
        source = dashboard_source()
        full_source = PAGES.read_text(encoding="utf-8") + (ROOT / "src" / "ui" / "shared" / "rendering.js").read_text(encoding="utf-8")
        navigation_source = full_source.split("function renderDashboardLegacy", 1)[0]
        existing_routes = set(re.findall(r'\["([a-z0-9-]+)",\s*"[^"]+"', navigation_source))
        rendered_routes = set(re.findall(r'data-route="([^"]+)"', render_dashboard({})))
        self.assertEqual(rendered_routes, {"image", "media", "jobs", "models"})
        self.assertTrue(rendered_routes.issubset(existing_routes))
        self.assertNotIn("fetch(", source)
        self.assertNotIn("request(", source)
        self.assertNotIn("getDashboard", source)
        self.assertNotIn("getHealth", source)
        self.assertNotIn("/api/", source)
        self.assertNotIn("submitJob", source)
        self.assertNotIn("launchApplication", source)


if __name__ == "__main__":
    unittest.main()
