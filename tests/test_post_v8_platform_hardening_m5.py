"""Milestone 5 platform-hardening contract regressions."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from src.services.api.context import ApiContext
from src.services.api.router import ApiRequest
from src.services.api.router_registry import build_router
from src.services.platform_hardening_v2 import PLATFORM_HARDENING_V2_SCHEMA, detail, snapshot


ROOT = Path(__file__).resolve().parents[1]


class PlatformHardeningV2Tests(unittest.TestCase):
    def test_snapshot_covers_five_hardening_areas_without_execution(self) -> None:
        value = snapshot()
        self.assertEqual(value["schema_version"], PLATFORM_HARDENING_V2_SCHEMA)
        self.assertEqual(value["status"], "completed")
        self.assertEqual(value["overall_state"], "READ_ONLY_CONTRACT")
        self.assertEqual(value["execution"], "not_run")
        self.assertTrue(value["dry_run"])
        self.assertEqual({area["id"] for area in value["areas"]}, {"updater_v3", "backup_recovery", "process_supervisor", "security", "performance"})
        self.assertTrue(all(area["persistent_state"] and area["api"] and area["failure_modes"] and area["recovery"] for area in value["areas"]))

    def test_detail_rejects_unknown_and_preserves_explicit_read_only_truth(self) -> None:
        updater = detail("updater_v3")
        self.assertEqual(updater["area"]["state"], "PLAN_ONLY")
        self.assertEqual(updater["execution"], "not_run")
        self.assertIsNone(detail("C:/private"))
        encoded = json.dumps(snapshot()).lower()
        self.assertNotIn("api_key", encoded)
        self.assertNotIn("password", encoded)
        self.assertNotIn("\\\\", encoded)
        self.assertNotIn("c:/", encoded)

    def test_router_and_inventory_bind_snapshot_and_detail(self) -> None:
        context = ApiContext({})
        router = build_router()
        listing = router.dispatch(ApiRequest(method="GET", path="/api/platform-hardening/v2", query={}, headers={}), context)
        selected = router.dispatch(ApiRequest(method="GET", path="/api/platform-hardening/v2/security", query={}, headers={}), context)
        missing = router.dispatch(ApiRequest(method="GET", path="/api/platform-hardening/v2/C:%2Fprivate", query={}, headers={}), context)
        self.assertEqual((listing.status, selected.status, missing.status), (200, 200, 404))
        self.assertEqual(selected.payload["area"]["id"], "security")
        inventory = json.loads((ROOT / "architecture/api_routes.yaml").read_text(encoding="utf-8"))
        route_ids = {item["route_id"] for item in inventory["routes"]}
        self.assertTrue({"platform_hardening.v2_snapshot", "platform_hardening.v2_detail"}.issubset(route_ids))

    def test_architecture_and_diagnostics_reference_the_hardening_contract(self) -> None:
        rules = (ROOT / "architecture/dependency_rules.yaml").read_text(encoding="utf-8")
        ownership = (ROOT / "architecture/module_ownership.yaml").read_text(encoding="utf-8")
        diagnostics = (ROOT / "src/ui/features/diagnostics/render.js").read_text(encoding="utf-8")
        self.assertIn("product-experience-v2-finite-catalog", rules)
        self.assertIn("product_experience_v2", ownership)
        self.assertIn("PLATFORM HARDENING V2", diagnostics)


if __name__ == "__main__":
    unittest.main()
