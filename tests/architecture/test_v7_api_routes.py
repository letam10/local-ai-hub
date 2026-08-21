"""Machine-readable API ownership and strangler-router guards."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from scripts.generate_api_route_inventory import rows
from src.services.api.router_registry import build_router


ROOT = Path(__file__).resolve().parents[2]


class V7ApiRouteArchitectureTests(unittest.TestCase):
    def test_inventory_is_generated_and_unique(self) -> None:
        inventory = json.loads((ROOT / "architecture" / "api_routes.yaml").read_text(encoding="utf-8"))
        self.assertEqual(inventory["schema_version"], "api-routes.v1")
        entries = inventory["routes"]
        self.assertEqual(entries, rows())
        self.assertEqual(len(entries), len({item["route_id"] for item in entries}))
        self.assertEqual(len(entries), len({(item["method"], item["path"]) for item in entries}))
        for item in entries:
            self.assertTrue((ROOT / item["owner"]).is_file(), item["route_id"])
            self.assertRegex(item["domain"], r"^[a-z][a-z0-9_]{1,63}$")

    def test_registered_routes_have_one_inventory_owner(self) -> None:
        inventory = json.loads((ROOT / "architecture" / "api_routes.yaml").read_text(encoding="utf-8"))
        by_key = {(item["method"], item["path"]): item for item in inventory["routes"]}
        registered = build_router().routes()
        self.assertEqual(len(registered), len({(item.method, item.path) for item in registered}))
        for item in registered:
            metadata = by_key[(item.method, item.path)]
            self.assertEqual(metadata["transport"], "router")
            self.assertEqual(metadata["owner"], item.owner)

    def test_route_modules_are_transport_only(self) -> None:
        route_root = ROOT / "src/services/api/routes"
        for path in route_root.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("src.ui", source, path.name)
            self.assertNotIn("subprocess", source, path.name)
            self.assertNotIn("ModelManager(", source, path.name)
            self.assertNotIn("RuntimeManager(", source, path.name)

    def test_loopback_and_phase2_boundaries_remain_explicit(self) -> None:
        server = (ROOT / "src/services/api/api_server.py").read_text(encoding="utf-8")
        self.assertIn("127.0.0.1", server)
        self.assertNotIn('"0.0.0.0"', server)
        self.assertIn("src.services.api.components", server)
        self.assertNotIn("PLAN.md", json.dumps(json.loads((ROOT / "architecture" / "api_routes.yaml").read_text(encoding="utf-8"))))


if __name__ == "__main__":
    unittest.main()
