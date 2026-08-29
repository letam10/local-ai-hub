"""Milestone 6 Plugin SDK/API versioning/remote-worker regressions."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from src.services.api.context import ApiContext
from src.services.api.router import ApiRequest
from src.services.api.router_registry import build_router
from src.services.platform_extensibility_v2 import PLATFORM_EXTENSIBILITY_V2_SCHEMA, detail, snapshot


ROOT = Path(__file__).resolve().parents[1]


class PlatformExtensibilityV2Tests(unittest.TestCase):
    def test_snapshot_exposes_plugin_sdk_versioning_and_remote_worker_contract(self) -> None:
        value = snapshot()
        self.assertEqual(value["schema_version"], PLATFORM_EXTENSIBILITY_V2_SCHEMA)
        self.assertEqual(value["status"], "completed")
        self.assertEqual(value["overall_state"], "READ_ONLY_CONTRACT")
        self.assertEqual(value["execution"], "not_run")
        self.assertTrue(value["dry_run"])
        self.assertEqual(value["plugin_sdk"]["manifest_schema"], "extension-manifest.v1")
        self.assertIn("read_capability_cards", value["plugin_sdk"]["permissions"])
        self.assertIn("dynamic_import", value["plugin_sdk"]["forbidden"])
        self.assertEqual(value["api_versioning"]["public_contract_line"], "v2")
        self.assertEqual(value["remote_worker"]["connection_state"], "NOT_CONFIGURED")
        self.assertEqual(value["remote_worker"]["endpoint"], "not_exposed")
        self.assertEqual(value["remote_worker"]["credentials"], "not_exposed")

    def test_detail_and_projection_never_echo_paths_secrets_or_client_payloads(self) -> None:
        selected = detail("remote_worker")
        self.assertEqual(selected["area"]["status"], "NOT_CONFIGURED")
        self.assertIsNone(detail("C:/private"))
        encoded = json.dumps(snapshot()).lower()
        self.assertNotIn("c:/", encoded)
        self.assertNotIn("\\\\", encoded)
        self.assertNotIn("api_key", encoded)
        self.assertNotIn("token=", encoded)
        self.assertNotIn("endpoint_url", encoded)

    def test_router_inventory_and_diagnostics_bind_the_contract(self) -> None:
        context = ApiContext({})
        router = build_router()
        listing = router.dispatch(ApiRequest(method="GET", path="/api/extensibility/v2", query={}, headers={}), context)
        plugin = router.dispatch(ApiRequest(method="GET", path="/api/extensibility/v2/plugin_sdk", query={}, headers={}), context)
        missing = router.dispatch(ApiRequest(method="GET", path="/api/extensibility/v2/C:%2Fprivate", query={}, headers={}), context)
        self.assertEqual((listing.status, plugin.status, missing.status), (200, 200, 404))
        self.assertEqual(plugin.payload["area"]["id"], "plugin_sdk")
        inventory = json.loads((ROOT / "architecture/api_routes.yaml").read_text(encoding="utf-8"))
        route_ids = {item["route_id"] for item in inventory["routes"]}
        self.assertTrue({"extensibility.v2_snapshot", "extensibility.v2_detail"}.issubset(route_ids))
        diagnostics = (ROOT / "src/ui/features/diagnostics/render.js").read_text(encoding="utf-8")
        self.assertIn("EXTENSIBILITY V2", diagnostics)
        self.assertIn("getPlatformExtensibilityV2", (ROOT / "src/ui/app.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
