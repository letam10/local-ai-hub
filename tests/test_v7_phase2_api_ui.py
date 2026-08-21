"""Contract checks for Phase 2 typed API and Components UI wiring."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.platform.paths import HubPaths
from src.services.api import components as component_api
from src.services.component_installer import ComponentInstaller


ROOT = Path(__file__).resolve().parents[1]


class Phase2ApiUiTests(unittest.TestCase):
    def test_api_facade_returns_safe_snapshot_and_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "app"
            config = app / "Config"
            config.mkdir(parents=True)
            for name in ("model_catalog.example.json", "runtime_catalog.example.json"):
                (config / name).write_bytes((ROOT / "Config" / name).read_bytes())
            manager = ComponentInstaller(paths=HubPaths(app_root=app, data_root=root / "data"))
            with patch.object(component_api, "_manager", manager):
                snapshot = component_api.snapshot()
                self.assertEqual(snapshot["execution"], "not_run")
                plan = component_api.plan_install("sam2.1-hiera-small", component_type="model")
                self.assertTrue(plan["plan_id"].startswith("install_plan_"))
                encoded = json.dumps(plan, ensure_ascii=True)
                self.assertNotIn("\\", encoded)
                self.assertNotIn("official_source", encoded)

    def test_public_api_and_ui_are_component_owned_and_path_free(self) -> None:
        server = (ROOT / "src/services/api/api_server.py").read_text(encoding="utf-8")
        client = (ROOT / "src/ui/api.js").read_text(encoding="utf-8")
        pages = (ROOT / "src/ui/pages.js").read_text(encoding="utf-8") + (ROOT / "src/ui/shared/rendering.js").read_text(encoding="utf-8")
        app = (ROOT / "src/ui/app.js").read_text(encoding="utf-8")
        for route in ("/api/components", "/api/components/install/plan", "/api/components/import/plan", "/api/components/verify/plan", "/api/components/maintenance/plan"):
            self.assertIn(route, server)
        for helper in ("getComponents", "createComponentPlan", "confirmComponentPlan", "createComponentMaintenancePlan"):
            self.assertIn(helper, client)
        self.assertIn('"components"', pages)
        self.assertIn("renderComponents", pages)
        self.assertIn('route === "components"', app)
        self.assertNotIn("download_url", server)
        self.assertNotIn("command", client)

    def test_component_ui_uses_static_i18n_markers_only(self) -> None:
        pages = (ROOT / "src/ui/pages.js").read_text(encoding="utf-8") + (ROOT / "src/ui/features/components/render.js").read_text(encoding="utf-8")
        i18n = (ROOT / "src/ui/i18n.js").read_text(encoding="utf-8")
        for label in ("Module state", "Runtime state", "Model state", "Lập kế hoạch", "Lập kế hoạch sửa"):
            self.assertIn(f'data-i18n="{label}"', pages)
        self.assertIn("COMPONENT_LABEL_DICTIONARIES", i18n)
        self.assertIn('"Components / AI Setup"', i18n)
        # Server-owned names, ids, reasons and actions remain escaped data;
        # the renderer must not add a dynamic translation marker to them.
        self.assertIn('escapeHtml(item.display_name || id)', pages)
        self.assertIn('escapeHtml(item.reason || uiText("Server snapshot chưa có thêm lý do."))', pages)

    def test_api_error_projection_never_echoes_path_text(self) -> None:
        status, payload = component_api.handle_error(ValueError("C:\\private\\secret\\payload"))
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "invalid_component_request")
        self.assertNotIn("private", json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
