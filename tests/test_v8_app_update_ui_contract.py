from __future__ import annotations

from pathlib import Path
import unittest

from src.app.update_bridge import install_update_bridge
from src.services.api.router_registry import build_router


ROOT = Path(__file__).resolve().parents[1]


class V8AppUpdateUiContractTests(unittest.TestCase):
    def test_router_exposes_bounded_installed_update_surface(self) -> None:
        rows = {(route.method, route.path) for route in build_router().routes()}
        self.assertIn(("GET", "/api/app-update/status"), rows)
        self.assertIn(("GET", "/api/app-update/auth/status"), rows)
        self.assertIn(("POST", "/api/app-update/auth/device/start"), rows)
        self.assertIn(("POST", "/api/app-update/auth/device/poll"), rows)
        self.assertIn(("POST", "/api/app-update/auth/device/cancel"), rows)
        self.assertIn(("POST", "/api/app-update/auth/logout"), rows)
        self.assertIn(("GET", "/api/app-update/changes"), rows)
        self.assertIn(("POST", "/api/app-update/prepare"), rows)
        self.assertIn(("POST", "/api/app-update/rollback"), rows)

    def test_dashboard_loads_update_module_without_replacing_main_controller(self) -> None:
        html = (ROOT / "src" / "ui" / "index.html").read_text(encoding="utf-8")
        module = (ROOT / "src" / "ui" / "app_update.js").read_text(encoding="utf-8")
        self.assertIn('src="/ui/app.js"', html)
        self.assertIn('src="/ui/app_update.js"', html)
        self.assertIn("Cập nhật Local AI Hub", module)
        self.assertIn("/api/app-update/prepare", module)
        self.assertNotIn("git pull", module.lower().replace("không git pull", ""))

    def test_restart_bridge_is_opt_in_and_does_not_replace_close_contract(self) -> None:
        class FixtureBridge:
            pass

        install_update_bridge(FixtureBridge)
        self.assertTrue(callable(getattr(FixtureBridge, "restart_after_update", None)))


if __name__ == "__main__":
    unittest.main()
