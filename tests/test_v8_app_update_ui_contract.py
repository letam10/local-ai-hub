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
        self.assertIn('const existing = dashboard.querySelector("[data-app-update-card]")', module)
        self.assertIn("if (card !== bootstrapCard)", module)
        self.assertIn("bootstrapFlow?.stop()", module)
        self.assertIn("check(false);", module)
        self.assertEqual(module.count("createBootstrapFlow"), 2)
        self.assertNotIn("bootstrapPollCount", module)
        self.assertIn("bootstrapFlow.observe", module)
        self.assertIn("bootstrap_restart_authorized !== true", module)
        self.assertNotIn("git pull", module.lower().replace("không git pull", ""))
        self.assertNotIn("window.confirm", module)
        self.assertIn("showConfirmModal", module)
        self.assertIn("current pointer chưa đổi", module)
        self.assertIn('title: "Bản cập nhật đã sẵn sàng"', module)
        self.assertIn('confirmLabel: "Khởi động lại và cập nhật"', module)
        self.assertIn('cancelLabel: "Để sau"', module)
        self.assertIn("if (confirmed) await restart(card);", module)
        self.assertNotIn("data-app-update-rollback", module)
        self.assertNotIn("API.rollback", module)
        self.assertNotIn('title: "Xác nhận cập nhật Local AI Hub"', module)

    def test_restart_bridge_is_opt_in_and_does_not_replace_close_contract(self) -> None:
        class FixtureBridge:
            pass

        install_update_bridge(FixtureBridge)
        self.assertTrue(callable(getattr(FixtureBridge, "restart_after_update", None)))


if __name__ == "__main__":
    unittest.main()
