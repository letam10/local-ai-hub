"""Architecture guards for the final V7 productization contract."""

from __future__ import annotations

import json
from pathlib import Path
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
        self.assertEqual(raw["schema_version"], "v7-production-catalog.v1")
        self.assertGreaterEqual(len(raw["models"]), 13)
        self.assertGreaterEqual(len(raw["runtimes"]), 14)
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

    def test_release_config_excludes_machine_data(self) -> None:
        script = (ROOT / "scripts/build_installer.py").read_text(encoding="utf-8")
        for name in ("Models", "Environments", "runtime", "Output", "Reports"):
            self.assertIn(name, script)
        self.assertNotIn("rglob('*')", script)


if __name__ == "__main__":
    unittest.main()
