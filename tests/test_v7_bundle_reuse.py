"""UI-facing composite bundle and existing-install reuse acceptance."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

from src.platform.paths import HubPaths
from src.services.component_installer import ComponentBundleService, ComponentInstaller
from src.services.model_manager import ModelManager
from src.services.runtime_manager import RuntimeManager


class V7BundleReuseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.app = root / "app"
        self.data = root / "data"
        (self.app / "Config").mkdir(parents=True)
        self.paths = HubPaths(app_root=self.app, data_root=self.data)
        model_bytes = b"model"
        self.model_hash = hashlib.sha256(model_bytes).hexdigest()
        self.model_catalog = self.app / "Config" / "model_catalog.json"
        self.model_catalog.write_text(json.dumps({
            "schema_version": "model-catalog.v1",
            "models": [{
                "model_id": "demo-model", "display_name": "Demo model", "provider": "fixture",
                "version": "1", "revision": "model-r1", "official_source": "https://github.com/example/demo-model",
                "install_supported": True, "modules_using_model": ["demo"], "runtime_id": "demo-runtime",
                "files": [{"relative_path": "demo.bin", "size_bytes": len(model_bytes), "sha256": self.model_hash}],
                "estimated_download_size": len(model_bytes), "estimated_disk_size": len(model_bytes),
            }],
        }), encoding="utf-8")
        self.runtime_catalog = self.app / "Config" / "runtime_catalog.json"
        self.runtime_catalog.write_text(json.dumps({
            "schema_version": "runtime-catalog.v1",
            "runtimes": [{
                "runtime_id": "demo-runtime", "display_name": "Demo runtime", "kind": "tool", "version": "1",
                "revision": "runtime-r1", "root_class": "runtime_root", "required_leaves": ["bin/demo.exe"],
                "modules": ["demo"], "official_source": "https://github.com/example/demo-runtime.zip",
                "install_supported": True, "install_strategy": "portable_archive", "sha256": "a" * 64,
                "estimated_download_size": 1, "estimated_disk_size": 1,
            }],
        }), encoding="utf-8")
        self.models = ModelManager(paths=self.paths, catalog_path=self.model_catalog)
        self.runtimes = RuntimeManager(paths=self.paths, catalog_path=self.runtime_catalog)
        self.installer = ComponentInstaller(paths=self.paths, model_manager=self.models, runtime_manager=self.runtimes)
        self.bundle = ComponentBundleService(self.installer)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_bundle_plan_is_ordered_and_path_free(self) -> None:
        plan = self.bundle.plan("demo-model", component_type="model")
        self.assertEqual(plan["schema_version"], "component-bundle-plan.v1")
        self.assertEqual([(item["component_type"], item["component_id"]) for item in plan["steps"]], [("runtime", "demo-runtime"), ("model", "demo-model")])
        self.assertEqual(plan["execution"], "not_run")
        self.assertNotIn(str(self.temp.name), json.dumps(plan))
        self.assertNotIn("official_source", json.dumps(plan))

    def test_bundle_confirm_runs_runtime_then_model_through_installer(self) -> None:
        archive = self.paths.temp_root / "fixture-runtime.zip"
        archive.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr("bin/demo.exe", b"x")
        model_stage = self.paths.temp_root / "fixture-model.bin"
        model_stage.write_bytes(b"model")
        calls: list[str] = []

        class FakeDownloader:
            def __init__(self, **_kwargs):
                pass

            def download(self, source, destination, **_kwargs):
                calls.append(str(destination))
                staged = archive if str(destination).endswith(".package") else model_stage
                return SimpleNamespace(staged_path=staged)

        plan = self.bundle.plan("demo-model", component_type="model")
        with patch("src.services.component_installer.manager.TrustedDownloader", FakeDownloader), patch("src.services.component_installer.manager.shutil.disk_usage", return_value=SimpleNamespace(free=10**12)):
            result = self.bundle.confirm(plan["plan_id"], confirmed=True)
        self.assertEqual(result["status"], "completed")
        self.assertEqual([item["component_id"] for item in result["steps"]], ["demo-runtime", "demo-model"])
        self.assertTrue((self.paths.runtime_root / "bin" / "demo.exe").is_file())
        self.assertTrue((self.paths.models_root / "demo-model" / "demo.bin").is_file())
        self.assertEqual(len(calls), 2)
        self.assertNotIn(str(self.temp.name), json.dumps(result))

    def test_existing_reuse_writes_receipt_without_copying(self) -> None:
        root = self.paths.models_root / "demo-model"
        root.mkdir(parents=True)
        leaf = root / "demo.bin"
        leaf.write_bytes(b"model")
        plan = self.installer.plan_reuse("demo-model", component_type="model")
        result = self.installer.confirm_reuse(plan["plan_id"], confirmed=True)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["source"], "existing_install_reuse")
        self.assertEqual(leaf.read_bytes(), b"model")
        receipts = json.loads((self.paths.config_root / "component_install_receipts.json").read_text(encoding="utf-8"))
        self.assertEqual(receipts["records"]["demo-model"]["source"], "existing_install_reuse")

    def test_reuse_refuses_partial_or_reparse_without_receipt(self) -> None:
        root = self.paths.models_root / "demo-model"
        root.mkdir(parents=True)
        plan = self.installer.plan_reuse("demo-model", component_type="model")
        result = self.installer.confirm_reuse(plan["plan_id"], confirmed=True)
        self.assertEqual(result["code"], "existing_install_incomplete")
        self.assertFalse((self.paths.config_root / "component_install_receipts.json").exists())

    def test_confirm_maintenance_update_uses_immutable_candidate_executor(self) -> None:
        root = self.paths.models_root / "demo-model"
        root.mkdir(parents=True)
        (root / "demo.bin").write_bytes(b"model")
        candidate = self.paths.temp_root / "candidates" / "demo-new.bin"
        candidate.parent.mkdir(parents=True)
        candidate.write_bytes(b"new!!")
        self.models._records[0]["update_candidate"] = {
            "staged_relative_path": "candidates/demo-new.bin", "relative_path": "demo.bin",
            "size_bytes": 5, "sha256": hashlib.sha256(b"new!!").hexdigest(), "source_identity": "demo-model-r2",
        }
        self.models._records[0]["latest_supported_revision"] = "model-r2"
        receipt = self.paths.config_root / "component_install_receipts.json"
        receipt.parent.mkdir(parents=True)
        receipt.write_text(json.dumps({"schema_version": "component-install-receipts.v2", "records": {"demo-model": {"bundle_revision": "model-r1"}}}), encoding="utf-8")
        plan = self.installer.plan_maintenance("demo-model", action="update")
        result = self.installer.confirm_maintenance(plan["plan_id"], confirmed=True)
        self.assertEqual(result["status"], "completed")
        self.assertEqual((root / "demo.bin").read_bytes(), b"new!!")
        self.assertTrue((self.paths.models_root / ".versions" / "demo-model" / "model-r1" / "demo.bin").is_file())


if __name__ == "__main__":
    unittest.main()
