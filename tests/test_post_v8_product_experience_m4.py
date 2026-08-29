"""Milestone 4 product-experience contract regressions."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from src.services.api.context import ApiContext
from src.services.api.router import ApiRequest
from src.services.api.router_registry import build_router
from src.services.product_experience_v2 import PRODUCT_EXPERIENCE_V2_SCHEMA, onboarding, search, snapshot


ROOT = Path(__file__).resolve().parents[1]


class ProductExperienceV2Tests(unittest.TestCase):
    def test_snapshot_has_real_existing_surfaces_and_nonpersistent_onboarding(self) -> None:
        value = snapshot()
        self.assertEqual(value["schema_version"], PRODUCT_EXPERIENCE_V2_SCHEMA)
        self.assertEqual(value["status"], "completed")
        self.assertEqual(value["execution"], "not_run")
        self.assertTrue(value["dry_run"])
        rows = {item["id"]: item for item in value["surfaces"]}
        self.assertTrue({"dashboard", "settings", "diagnostics", "jobs", "projects"}.issubset(rows))
        self.assertEqual(rows["settings"]["api"], "/api/settings")
        self.assertEqual(rows["diagnostics"]["api"], "/api/diagnostics/snapshot")
        guide = onboarding()
        self.assertEqual(guide["persistent_state"], "none")
        self.assertEqual([step["route"] for step in guide["steps"]], ["dashboard", "vision", "jobs", "diagnostics"])
        self.assertEqual(value["settings_behavior"]["theme"], "applied_after_explicit_save")

    def test_search_is_finite_path_free_and_never_searches_user_data(self) -> None:
        found = search("diagnostics")
        self.assertEqual(found["status"], "completed")
        self.assertEqual([item["id"] for item in found["results"]], ["diagnostics"])
        default = search("")
        self.assertLessEqual(len(default["results"]), 8)
        encoded = json.dumps({"found": found, "default": default}).lower()
        self.assertNotIn("c:/", encoded)
        self.assertNotIn("\\\\", encoded)
        self.assertNotIn("api_key", encoded)
        self.assertIn("finite server-owned product surface catalog", found["reason"])

    def test_router_and_frontend_use_hub_api_only(self) -> None:
        context = ApiContext({})
        router = build_router()
        listing = router.dispatch(ApiRequest(method="GET", path="/api/product-experience/v2", query={}, headers={}), context)
        guide = router.dispatch(ApiRequest(method="GET", path="/api/product-experience/v2/onboarding", query={}, headers={}), context)
        matched = router.dispatch(ApiRequest(method="GET", path="/api/product-experience/v2/search", query={"q": ["settings"]}, headers={}), context)
        self.assertEqual((listing.status, guide.status, matched.status), (200, 200, 200))
        self.assertEqual(matched.payload["results"][0]["id"], "settings")
        inventory = json.loads((ROOT / "architecture/api_routes.yaml").read_text(encoding="utf-8"))
        route_ids = {item["route_id"] for item in inventory["routes"]}
        self.assertTrue({"product_experience.v2_snapshot", "product_experience.v2_onboarding", "product_experience.v2_search"}.issubset(route_ids))
        app = (ROOT / "src/ui/app.js").read_text(encoding="utf-8")
        dashboard = (ROOT / "src/ui/features/dashboard/render.js").read_text(encoding="utf-8")
        self.assertIn("getProductExperienceV2", app)
        self.assertIn("searchProductExperienceV2", app)
        self.assertIn("PRODUCT EXPERIENCE V2", dashboard)


if __name__ == "__main__":
    unittest.main()
