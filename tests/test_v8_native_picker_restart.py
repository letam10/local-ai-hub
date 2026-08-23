"""Controlled V8 native-selection/restart safety acceptance.

The browser/native picker contributes only an opaque selection token.  The
server keeps the selected path process-local, validates it again at execution,
and never rediscovers a stale client-visible location after restart.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from src.platform.paths import HubPaths
from src.services.component_installer import ComponentInstaller, SelectionError
from src.services.model_manager import ModelManager
from src.services.runtime_manager import RuntimeManager


class V8NativePickerRestartTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = self.root / "app"
        self.data = self.root / "data"
        (self.app / "Config").mkdir(parents=True)
        self.paths = HubPaths(app_root=self.app, data_root=self.data)
        payload = b"model"
        self.model_catalog = self.app / "Config" / "model_catalog.json"
        self.model_catalog.write_text(
            json.dumps(
                {
                    "schema_version": "model-catalog.v1",
                    "models": [
                        {
                            "model_id": "demo-model",
                            "display_name": "Demo model",
                            "provider": "fixture",
                            "version": "1",
                            "revision": "model-r1",
                            "official_source": "https://example.invalid/demo-model",
                            "install_supported": True,
                            "modules_using_model": ["demo"],
                            "runtime_id": "demo-runtime",
                            "files": [
                                {
                                    "relative_path": "demo.bin",
                                    "size_bytes": len(payload),
                                    "sha256": hashlib.sha256(payload).hexdigest(),
                                }
                            ],
                            "estimated_download_size": len(payload),
                            "estimated_disk_size": len(payload),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.runtime_catalog = self.app / "Config" / "runtime_catalog.json"
        self.runtime_catalog.write_text(
            json.dumps(
                {
                    "schema_version": "runtime-catalog.v1",
                    "runtimes": [
                        {
                            "runtime_id": "demo-runtime",
                            "display_name": "Demo runtime",
                            "kind": "tool",
                            "version": "1",
                            "revision": "runtime-r1",
                            "root_class": "runtime_root",
                            "required_leaves": ["bin/demo.exe"],
                            "modules": ["demo"],
                            "official_source": "https://example.invalid/demo-runtime.zip",
                            "install_supported": True,
                            "install_strategy": "portable_archive",
                            "sha256": "a" * 64,
                            "estimated_download_size": 1,
                            "estimated_disk_size": 1,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.models = ModelManager(paths=self.paths, catalog_path=self.model_catalog)
        self.runtimes = RuntimeManager(paths=self.paths, catalog_path=self.runtime_catalog)
        self.installer = ComponentInstaller(paths=self.paths, model_manager=self.models, runtime_manager=self.runtimes)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _selection_root(self) -> Path:
        source = self.paths.temp_root / "native-selection"
        source.mkdir(parents=True)
        (source / "demo.bin").write_bytes(b"model")
        return source

    def test_missing_cancelled_or_wrong_type_selection_never_creates_a_plan(self) -> None:
        with self.assertRaises(SelectionError):
            self.installer.plan_import("selection_" + "a" * 32)
        with self.assertRaises(SelectionError):
            self.installer.issue_selection("demo-runtime", self._selection_root(), component_type="runtime")
        with self.assertRaises(SelectionError):
            self.installer.issue_selection("demo-model", self.paths.temp_root / "cancelled")
        self.assertFalse((self.paths.models_root / "demo-model").exists())

    def test_expired_or_restart_stale_selection_never_rediscovers_the_path(self) -> None:
        source = self._selection_root()
        selection = self.installer.issue_selection("demo-model", source)
        selection_id = str(selection["selection_id"])
        self.assertNotIn(str(source), json.dumps(selection))

        self.installer._selections[selection_id]["expires_at"] = 0
        with self.assertRaises(SelectionError):
            self.installer.plan_import(selection_id)

        fresh = ComponentInstaller(paths=self.paths, model_manager=self.models, runtime_manager=self.runtimes)
        with self.assertRaises(SelectionError):
            fresh.plan_import(selection_id)
        self.assertTrue((source / "demo.bin").is_file())
        self.assertFalse((self.paths.models_root / "demo-model").exists())

    def test_reparse_or_changed_selected_content_is_refused_before_managed_copy(self) -> None:
        external = self.paths.temp_root / "external-selection"
        external.mkdir(parents=True)
        (external / "demo.bin").write_bytes(b"model")
        reparse = self.paths.temp_root / "selection-link"
        try:
            reparse.symlink_to(external, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Directory symlinks are unavailable on this test host.")
        with self.assertRaises(SelectionError):
            self.installer.issue_selection("demo-model", reparse)
        self.assertTrue((external / "demo.bin").is_file())

        source = self._selection_root()
        selection = self.installer.issue_selection("demo-model", source)
        plan = self.installer.plan_import(str(selection["selection_id"]))
        (source / "demo.bin").write_bytes(b"changed-after-selection")
        result = self.installer.confirm_import(str(plan["plan_id"]), confirmed=True)
        self.assertEqual(result["status"], "failed")
        self.assertIn(result["code"], {"import_size_mismatch", "import_checksum_mismatch"})
        self.assertFalse((self.paths.models_root / "demo-model").exists())
        self.assertEqual((source / "demo.bin").read_bytes(), b"changed-after-selection")


if __name__ == "__main__":
    unittest.main()
