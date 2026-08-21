"""Phase 3.5 strangler and transport ownership guards."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from scripts.generate_api_route_inventory import rows
from src.services.api.route_inventory import legacy_forensic_routes, legacy_routes
from src.services.api.router_registry import build_router


ROOT = Path(__file__).resolve().parents[2]


class V7Phase35Guards(unittest.TestCase):
    def test_no_generic_legacy_routes_remain(self) -> None:
        transport = legacy_routes()
        self.assertEqual(len(transport), 5)
        self.assertEqual({row.transport_class for row in transport}, {"STATIC", "STREAM", "UPLOAD"})
        self.assertNotIn("legacy", {row.transport for row in transport})

    def test_all_71_legacy_rows_have_finite_audit_categories(self) -> None:
        self.assertEqual(len(legacy_forensic_routes()), 71)
        report = ROOT / "Reports" / "V7_PHASE35_LEGACY_API_AUDIT.local.md"
        if report.is_file():
            text = report.read_text(encoding="utf-8")
            categories = {"DOMAIN_ROUTE_TO_MIGRATE", "CANONICAL_ALIAS", "STREAMING_TRANSPORT", "STATIC_TRANSPORT", "SPECIAL_PROTOCOL_TRANSPORT", "COMPATIBILITY_ONLY", "DEAD_CANDIDATE", "NEEDS_REVIEW"}
            self.assertTrue(categories & set(text.split()))
            self.assertIn("Initial legacy rows audited: 71", text)
        else:
            # The forensic report is intentionally ignored and is absent from
            # clean clones.  The tracked route inventory remains the source of
            # truth for this architecture guard.
            self.assertEqual(len(legacy_forensic_routes()), 71)

    def test_alias_registry_is_unique_and_canonical(self) -> None:
        data = json.loads((ROOT / "architecture" / "api_aliases.yaml").read_text(encoding="utf-8"))
        aliases = data["aliases"]
        self.assertEqual(len(aliases), len({(item["method"], item["path"]) for item in aliases}))
        keys = {(item.method, item.path) for item in build_router().routes()}
        for item in aliases:
            target = item["canonical"]
            self.assertIn((target["method"], target["path"]), keys)

    def test_api_server_has_only_low_level_transport_branches(self) -> None:
        source = (ROOT / "src/services/api/api_server.py").read_text(encoding="utf-8")
        self.assertNotIn("elif normalized", source)
        self.assertNotIn('path.startswith("/api/components', source)
        self.assertNotIn('path.startswith("/api/projects', source)
        self.assertNotIn('path.startswith("/api/jobs', source)
        self.assertIn("self._upload()", source)
        self.assertIn("self._write_static(path)", source)
        self.assertIn("self._serve_artifact", source)

    def test_product_surface_facade_and_transport_contract_exist(self) -> None:
        self.assertTrue((ROOT / "src/services/product_surface.py").is_file())
        self.assertTrue((ROOT / "src/services/api/streaming.py").is_file())
        self.assertIn("TRANSPORT_REGISTRY", (ROOT / "src/services/api/streaming.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
