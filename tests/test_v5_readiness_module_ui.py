from __future__ import annotations

import inspect
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

from tests import v5_acceptance_ui_fixture as fixture


ROOT = Path(__file__).resolve().parents[1]


class V5ReadinessModuleUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pages = (ROOT / "src" / "ui" / "pages.js").read_text(encoding="utf-8")
        cls.shared_renderer = (ROOT / "src" / "ui" / "shared" / "rendering.js").read_text(encoding="utf-8")
        cls.settings_renderer = (ROOT / "src" / "ui" / "features" / "settings" / "render.js").read_text(encoding="utf-8")
        cls.app = (ROOT / "src" / "ui" / "app.js").read_text(encoding="utf-8")

    def setUp(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), fixture.AcceptanceHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.assertFalse(self.thread.is_alive())

    def _json(self, path: str) -> dict[str, object]:
        with urlopen(Request(self.base_url + path), timeout=5) as response:
            self.assertEqual(response.status, 200)
            return json.loads(response.read())

    def test_fixture_publishes_all_readiness_states_and_allowlisted_resource_fit(self) -> None:
        payload = self._json("/api/bootstrap")
        productization = payload["productization"]
        modules = productization["capabilities"]["modules"]
        self.assertEqual(
            [item["status"] for item in modules],
            ["operational", "partial", "unavailable", "not_published", "not_run"],
        )
        self.assertEqual(productization["execution"], "not_run")
        self.assertTrue(productization["dry_run"])
        resource_plan = payload["capabilities"]["module_manager"]["resource_plan"]
        self.assertEqual(resource_plan["mode"], "parallel")
        self.assertEqual(resource_plan["status"], "partial")
        self.assertTrue(resource_plan["physical"][0]["physical_fit"])
        self.assertTrue(resource_plan["concurrent"][0]["concurrent_fit"])
        self.assertIn("unknown_object", resource_plan)
        encoded = json.dumps(payload)
        self.assertNotRegex(encoded, r"(?i)([a-z]:[\\/]|api[_-]?key|password|secret|token)\s*[:=]")

    def test_readiness_projection_uses_product_surface_and_explicit_resource_fields(self) -> None:
        module_slice = self.shared_renderer[self.shared_renderer.index("const safeReadinessModules"):self.shared_renderer.index("const safeStorageVolumes")]
        self.assertIn("productization.capabilities", module_slice)
        self.assertIn("record.component", module_slice)
        self.assertIn("record.provider", module_slice)
        self.assertNotIn("registry.records", module_slice)
        resource_slice = self.shared_renderer[self.shared_renderer.index("const safeResourcePlan"):self.shared_renderer.index("const readinessSnapshot")]
        for marker in ("resource_plan", "target_gpu", "physical", "concurrent", "errors", "actions"):
            self.assertIn(marker, resource_slice)
        self.assertNotIn("JSON.stringify", resource_slice)

    def test_settings_is_server_snapshot_without_install_or_runtime_controls(self) -> None:
        settings_slice = self.settings_renderer
        for marker in ("Readiness & Module Plan", 'data-readiness-source="server-snapshot"', "dry_run", "Install / repair / uninstall", "Reason", "Next action"):
            self.assertIn(marker, settings_slice)
        self.assertNotRegex(settings_slice, r"data-(?:install|repair|uninstall)")
        self.assertNotIn("JSON.stringify", settings_slice)

    def test_fast_refresh_rerenders_settings_without_productization_fetch(self) -> None:
        refresh_slice = self.app[self.app.index("const refreshFast"):self.app.index("const refreshCreative")]
        self.assertIn('"settings"', refresh_slice)
        self.assertIn("state.capabilities = capabilities.value || {}", refresh_slice)
        self.assertNotIn("getProductization", self.app)
        self.assertNotIn("getStorage()", refresh_slice)

    def test_fixture_handler_has_no_full_buffer_artifact_conversion(self) -> None:
        source = inspect.getsource(fixture.AcceptanceHandler._serve_artifact)
        for forbidden in ("read_bytes(", "arrayBuffer(", "FileReader", "createObjectURL"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
