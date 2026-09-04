"""Source-only acceptance for the V7 final catalog/lifecycle/setup boundary."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from src.platform.paths import HubPaths
from src.services.api.router_registry import build_router
from src.services.productization import ComponentLifecycle, ProductionCatalog
from src.services.productization.catalog import ProductionCatalogError
from src.services.component_installer import ComponentInstaller
from scripts.setup_local_ai_hub import inspect_setup, plan_setup


ROOT = Path(__file__).resolve().parents[1]


class FinalProductizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = self.root / "app"
        self.data = self.root / "data"
        (self.app / "Config").mkdir(parents=True)
        for name in ("v7_production_catalog.example.json", "model_catalog.example.json", "runtime_catalog.example.json", "app.example.json", "hub_config.example.json", "components.example.json", "model_registry.example.json"):
            shutil.copy2(ROOT / "Config" / name, self.app / "Config" / name)
        self.paths = HubPaths(app_root=self.app, data_root=self.data)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_production_catalog_lists_all_supported_records_and_no_raw_paths(self) -> None:
        catalog = ProductionCatalog(paths=self.paths)
        snapshot = catalog.snapshot()
        self.assertEqual(snapshot["counts"]["models"], 14)
        self.assertEqual(snapshot["counts"]["runtimes"], 15)
        encoded = json.dumps(snapshot, ensure_ascii=True)
        self.assertNotIn(str(self.data), encoded)
        self.assertNotIn("official_source", encoded)
        self.assertTrue(all(item["status"] in {"INSTALLED", "NOT_INSTALLED", "PARTIAL", "UNAVAILABLE", "OPERATIONAL"} for item in snapshot["models"]))

    def test_unknown_size_is_not_fabricated_and_refresh_is_explicit(self) -> None:
        catalog = ProductionCatalog(paths=self.paths)
        before = catalog.inspect_model("sam2.1-hiera-small")
        self.assertIn("Download:", before["size_label"])
        self.assertEqual(catalog.refresh_model_size("sam2.1-hiera-small")["status"], "unavailable")
        self.assertFalse((self.paths.config_root / "model_size_cache.json").exists())

    def test_manual_catalog_entry_requires_review_and_does_not_download(self) -> None:
        lifecycle = ComponentLifecycle(paths=self.paths)
        plan = lifecycle.plan_one_click("flux-2-klein-base-4b-fp8")
        self.assertEqual(plan["status"], "manual_review")
        result = lifecycle.confirm(plan["plan_id"], confirmed=True)
        self.assertEqual(result["code"], "manual_review_required")
        self.assertFalse(self.paths.models_root.exists())

    def test_synthetic_one_click_uses_same_model_manager_path(self) -> None:
        production_path = self.app / "Config" / "synthetic-production.json"
        production_path.write_text(json.dumps({
            "schema_version": "v7-production-catalog.v1",
            "models": [{"model_id": "sam2.1-hiera-small", "display_name": "Synthetic SAM2", "category": "Vision", "provider": "fixture", "version": "1", "revision": "1", "official_source": "local", "source_type": "fixture", "disposition": "AUTO_INSTALL_READY", "modules": ["sam2"], "runtime_id": "sam2", "files": [{"relative_path": "Vision/SAM2/sam2.1_hiera_small.pt", "size_bytes": 0}], "estimated_download_size": 0, "estimated_disk_size": 0}],
            "runtimes": [{"runtime_id": "sam2", "display_name": "Synthetic runtime", "kind": "python", "version": "1", "revision": "1", "root_class": "environments_root", "required_leaves": ["sam2/Scripts/python.exe"], "modules": ["sam2"], "official_source": "local", "disposition": "REFERENCE_EXISTING", "install_strategy": "reference_existing"}],
        }, ensure_ascii=True), encoding="utf-8")
        catalog = ProductionCatalog(paths=self.paths, catalog_path=production_path)
        lifecycle = ComponentLifecycle(paths=self.paths, catalog=catalog)
        plan = lifecycle.plan_one_click("sam2.1-hiera-small")
        self.assertEqual(plan["status"], "planned")
        source = self.root / "fixture-source" / "Vision" / "SAM2"
        source.mkdir(parents=True)
        (source / "sam2.1_hiera_small.pt").write_bytes(b"")
        result = lifecycle.apply_fixture(plan["plan_id"], model_source=self.root / "fixture-source", confirmed=True)
        self.assertEqual(result["status"], "completed")
        self.assertTrue((self.paths.models_root / "sam2.1-hiera-small" / "Vision" / "SAM2" / "sam2.1_hiera_small.pt").is_file())
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_fixture_repair_update_uninstall_are_explicit_and_owned(self) -> None:
        production_path = self.app / "Config" / "synthetic-maintenance.json"
        production_path.write_text(json.dumps({
            "schema_version": "v7-production-catalog.v1",
            "models": [{"model_id": "sam2.1-hiera-small", "display_name": "Synthetic SAM2", "category": "Vision", "provider": "fixture", "version": "1", "revision": "1", "official_source": "local", "source_type": "fixture", "disposition": "AUTO_INSTALL_READY", "modules": ["sam2"], "runtime_id": "sam2", "files": [{"relative_path": "Vision/SAM2/sam2.1_hiera_small.pt", "size_bytes": 0}], "estimated_download_size": 0, "estimated_disk_size": 0}],
            "runtimes": [],
        }), encoding="utf-8")
        lifecycle = ComponentLifecycle(paths=self.paths, catalog=ProductionCatalog(paths=self.paths, catalog_path=production_path))
        install = lifecycle.plan_one_click("sam2.1-hiera-small")
        source = self.root / "fixture-source" / "Vision" / "SAM2"
        source.mkdir(parents=True)
        (source / "sam2.1_hiera_small.pt").write_bytes(b"")
        self.assertEqual(lifecycle.apply_fixture(install["plan_id"], model_source=self.root / "fixture-source", confirmed=True)["status"], "completed")
        for action in ("repair", "update"):
            plan = lifecycle.plan_maintenance("sam2.1-hiera-small", action)
            self.assertEqual(lifecycle.apply_fixture_maintenance(plan["plan_id"], confirmed=True)["status"], "completed")
        plan = lifecycle.plan_maintenance("sam2.1-hiera-small", "uninstall")
        self.assertEqual(lifecycle.apply_fixture_maintenance(plan["plan_id"], confirmed=True)["status"], "completed")
        self.assertFalse((self.paths.models_root / "sam2.1-hiera-small" / "Vision" / "SAM2" / "sam2.1_hiera_small.pt").exists())

    def test_setup_is_plan_only_by_default_and_router_contains_final_catalog(self) -> None:
        inspection = inspect_setup(app_root=self.app, data_root=self.data)
        plan = plan_setup(app_root=self.app, data_root=self.data)
        self.assertTrue(inspection["dry_run"])
        self.assertTrue(plan["dry_run"])
        self.assertFalse(self.data.exists())
        route_keys = {(route.method, route.path) for route in build_router().routes()}
        self.assertIn(("GET", "/api/productization/catalog"), route_keys)
        self.assertIn(("POST", "/api/productization/plans"), route_keys)

    def test_component_download_history_is_bounded_and_path_free(self) -> None:
        manager = ComponentInstaller(paths=self.paths)
        manager._jobs["component_job_fixture"] = {"job_id": "component_job_fixture", "plan_id": "plan_fixture", "component_id": "sam2.1-hiera-small", "component_type": "model", "category": "component_install", "state": "COMPLETED", "execution": "completed"}
        manager._record_history("component_job_fixture")
        history = manager.download_history()
        self.assertEqual(history[-1]["state"], "COMPLETED")
        self.assertNotIn(str(self.root), json.dumps(history))

    def test_catalog_rejects_unsafe_relative_leaf(self) -> None:
        path = self.app / "Config" / "bad.json"
        path.write_text(json.dumps({"schema_version": "v7-production-catalog.v1", "models": [{"model_id": "bad-model", "display_name": "Bad", "files": [{"relative_path": "../escape", "size_bytes": 0}]}], "runtimes": []}), encoding="utf-8")
        with self.assertRaises(ProductionCatalogError):
            ProductionCatalog(paths=self.paths, catalog_path=path)

    def test_models_surface_has_bounded_search_and_install_state_filters(self) -> None:
        source = (ROOT / "src" / "ui" / "features" / "models" / "models.js").read_text(encoding="utf-8")
        for marker in ("data-model-manager-v2", "data-model-filters", "data-model-search", "data-model-category", "data-model-installed", "data-model-count", "data-model-v2-count"):
            self.assertIn(marker, source)
        self.assertNotIn("catalogRows", source)
        self.assertNotIn("legacyRows", source)
        self.assertNotIn("<table", source)


if __name__ == "__main__":
    unittest.main()
