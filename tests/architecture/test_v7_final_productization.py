"""Architecture guards for the final V7 productization contract."""

from __future__ import annotations

import json
from pathlib import Path
import re
import unittest

from src.services.productization.catalog import ProductionCatalog


ROOT = Path(__file__).resolve().parents[2]


class V7FinalArchitectureTests(unittest.TestCase):
    def test_feature_registry_has_all_product_surfaces(self) -> None:
        source = (ROOT / "src/ui/core/feature_registry.js").read_text(encoding="utf-8")
        for feature in ("dashboard", "projects", "workflow_library", "jobs", "artifacts", "settings", "diagnostics", "components", "models", "runtimes", "node_studio", "image_ai", "image_mask", "sam2", "animesr", "media", "whisper", "vision", "voice", "ocr"):
            self.assertIn(f"{feature}:", source)
            self.assertTrue((ROOT / "src/ui/features" / feature / "index.js").is_file())
        self.assertIn("compatibility_composition_only", source)

    def test_catalog_is_tracked_and_all_entries_have_explicit_disposition(self) -> None:
        config = ROOT / "Config/v7_production_catalog.example.json"
        raw = json.loads(config.read_text(encoding="utf-8"))
        self.assertEqual(raw["schema_version"], "v7-production-catalog.v2")
        self.assertEqual(raw["catalog_version"], "2026.08.21")
        self.assertEqual(len(raw["models"]), 14)
        self.assertEqual(len(raw["runtimes"]), 15)
        for item in [*raw["models"], *raw["runtimes"]]:
            self.assertIn("disposition", item)
            self.assertNotIn("download_url", item)
            self.assertNotIn("command", item)
        catalog = ProductionCatalog(catalog_path=config)
        self.assertTrue(catalog.fingerprint)

    def test_setup_and_productization_are_plan_first(self) -> None:
        setup = (ROOT / "scripts/setup_local_ai_hub.py").read_text(encoding="utf-8")
        lifecycle = (ROOT / "src/services/productization/lifecycle.py").read_text(encoding="utf-8")
        self.assertIn("plan_setup", setup)
        self.assertIn("confirmed", setup)
        self.assertIn("manager_executor_required", lifecycle)
        self.assertIn("preserve_existing", lifecycle)

    def test_ui_facade_and_feature_ownership_boundary(self) -> None:
        pages = ROOT / "src/ui/pages.js"
        shared = ROOT / "src/ui/shared/rendering.js"
        node_facade = ROOT / "src/ui/node_studio.js"
        node_feature = ROOT / "src/ui/features/node_studio/studio.js"
        self.assertTrue(shared.is_file())
        self.assertTrue(node_feature.is_file())
        self.assertLess(pages.stat().st_size, 20_000, "pages.js must remain a thin composition facade")
        self.assertLess(node_facade.stat().st_size, 4_000, "node_studio.js must remain a compatibility facade")
        ownership = (ROOT / "architecture/ui_features.yaml").read_text(encoding="utf-8")
        feature_ids = re.findall(r"^\s*- id: ([a-z0-9_]+)\s*$", ownership, re.MULTILINE)
        self.assertEqual(len(feature_ids), len(set(feature_ids)))
        for required in ("api_dependencies:", "state_owner:", "styles:", "i18n_namespace:", "tests:", "related_services:"):
            self.assertIn(required, ownership)
        for path in (ROOT / "src/ui/features").rglob("*.js"):
            source = path.read_text(encoding="utf-8")
            self.assertNotRegex(source, r"(?i)from\s+[\"'](?:fs|node:fs|child_process|node:child_process)")
            self.assertNotIn("require(\"fs\")", source)
            self.assertNotIn("D:\\", source)
            self.assertNotIn("C:\\Users", source)

    def test_release_config_excludes_machine_data(self) -> None:
        script = (ROOT / "scripts/build_installer.py").read_text(encoding="utf-8")
        self.assertIn("RELEASE_ROOTS", script)
        self.assertIn("release_file_names", script)
        self.assertNotIn("rglob('*')", script)
        installer = (ROOT / "distribution/installer.iss").read_text(encoding="utf-8")
        self.assertIn('{autopf}\\Local AI Hub', installer)
        self.assertNotIn('DefaultInstallDir "D:\\LocalAIHub"', installer)


if __name__ == "__main__":
    unittest.main()
