"""
  FILE NOTE
  - Mục đích: Comprehensive unit and contract tests cho V6 Desktop Productization, Packaging & Release Readiness
  - Liên kết trực tiếp: src/app/main.py, src/app/launcher.py, src/services/updater/engine.py, src/services/diagnostics/repair_engine.py, scripts/uninstall_hub.py, scripts/build_installer.py
  - Vùng ảnh hưởng khi sửa: Toàn bộ validation cho Desktop packaging, installer manifest, updater preservation, repair 5-step, safe uninstall, và release readiness
"""

from __future__ import annotations

import json
import os
import re
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.app.main import APP_INSTANCE_MUTEX, _load_window_settings
from src.services.diagnostics.repair_engine import DesktopRepairEngine
from src.services.updater.engine import (
    PROTECTED_FORENSIC_FILES,
    PROTECTED_MUTABLE_ROOTS,
    UpdateEngine,
)
import scripts.uninstall_hub as uninstall_mod
import scripts.build_installer as build_mod


class TestDesktopShellPackaging(unittest.TestCase):
    def test_load_window_settings_defaults(self):
        with TemporaryDirectory() as tmpdir:
            from unittest.mock import patch
            import src.shared.paths.registry as paths_mod
            with patch.object(paths_mod, "CONFIG_ROOT", Path(tmpdir)):
                min_w, min_h, maximized = _load_window_settings()
                self.assertEqual(min_w, 1280)
                self.assertEqual(min_h, 720)
                self.assertTrue(maximized)

    def test_load_window_settings_custom_bounded(self):
        with TemporaryDirectory() as tmpdir:
            import src.shared.paths.registry as paths_mod
            cfg_dir = Path(tmpdir)
            settings_path = cfg_dir / "settings.json"
            settings_path.write_text(json.dumps({
                "schema_version": 2,
                "window": {
                    "start_maximized": False,
                    "minimum_width": 1600,
                    "minimum_height": 900,
                }
            }), encoding="utf-8")
            with patch.object(paths_mod, "CONFIG_ROOT", cfg_dir):
                min_w, min_h, maximized = _load_window_settings()
                self.assertEqual(min_w, 1600)
                self.assertEqual(min_h, 900)
                self.assertFalse(maximized)

    def test_app_instance_mutex_constant(self):
        self.assertTrue(APP_INSTANCE_MUTEX.startswith("Local\\"))
        self.assertIn("AppInstance", APP_INSTANCE_MUTEX)


class TestUpdateEnginePreservation(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.hub_root = Path(self.temp.name)
        # Create simulated machine-local data that MUST be preserved
        (self.hub_root / "Config").mkdir(parents=True, exist_ok=True)
        (self.hub_root / "Config" / "settings.json").write_text('{"settings": "user_val"}', encoding="utf-8")
        (self.hub_root / "Models").mkdir(parents=True, exist_ok=True)
        (self.hub_root / "Models" / "my_model.bin").write_text("model_data", encoding="utf-8")
        (self.hub_root / "Output").mkdir(parents=True, exist_ok=True)
        (self.hub_root / "Output" / "image.png").write_text("image_data", encoding="utf-8")
        (self.hub_root / "Reports").mkdir(parents=True, exist_ok=True)
        (self.hub_root / "Reports" / "p0_source_reconciliation.local.bundle").write_text("bundle_data", encoding="utf-8")

        # Existing app code
        (self.hub_root / "src").mkdir(parents=True, exist_ok=True)
        (self.hub_root / "src" / "app.py").write_text("old_app_code", encoding="utf-8")

        self.engine = UpdateEngine(self.hub_root)
        self.addCleanup(self.temp.cleanup)

    def test_inspect_rejects_archive_with_protected_roots(self):
        # Create a malicious/broken zip that tries to overwrite Models
        bad_zip = self.hub_root / "bad_update.zip"
        with zipfile.ZipFile(bad_zip, "w") as zf:
            zf.writestr("src/app.py", "new_app_code")
            zf.writestr("Models/evil.bin", "overwrite_model")

        insp = self.engine.inspect_update_package(bad_zip)
        self.assertFalse(insp["valid"])
        self.assertTrue(any("protected mutable targets" in e for e in insp.get("errors", [])))

    def test_apply_update_updates_code_and_preserves_user_data(self):
        # Create a valid update zip containing only app code
        good_zip = self.hub_root / "good_update.zip"
        with zipfile.ZipFile(good_zip, "w") as zf:
            zf.writestr("src/app.py", "new_app_code")
            zf.writestr("src/new_feature.py", "feature_code")

        # Unconfirmed -> rejected
        unconf = self.engine.apply_update(good_zip, confirmed=False)
        self.assertFalse(unconf.accepted)
        self.assertEqual(unconf.status, "unconfirmed")

        # Confirmed -> applied
        result = self.engine.apply_update(good_zip, confirmed=True)
        self.assertTrue(result.accepted)
        self.assertEqual(result.status, "completed")

        # Verify app files were updated
        self.assertEqual((self.hub_root / "src" / "app.py").read_text(encoding="utf-8"), "new_app_code")
        self.assertEqual((self.hub_root / "src" / "new_feature.py").read_text(encoding="utf-8"), "feature_code")

        # Verify ALL machine-local data preserved
        self.assertEqual((self.hub_root / "Config" / "settings.json").read_text(encoding="utf-8"), '{"settings": "user_val"}')
        self.assertEqual((self.hub_root / "Models" / "my_model.bin").read_text(encoding="utf-8"), "model_data")
        self.assertEqual((self.hub_root / "Output" / "image.png").read_text(encoding="utf-8"), "image_data")
        self.assertEqual((self.hub_root / "Reports" / "p0_source_reconciliation.local.bundle").read_text(encoding="utf-8"), "bundle_data")


class TestDesktopRepairEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.hub_root = Path(self.temp.name)
        self.engine = DesktopRepairEngine(self.hub_root)
        self.addCleanup(self.temp.cleanup)

    def test_inspect_and_repair_lifecycle(self):
        # Initial empty temp root -> inspection detects missing items
        insp = self.engine.inspect()
        self.assertGreater(len(insp.missing), 0)

        # Plan repair
        plan = self.engine.plan_repair()
        self.assertTrue(plan["repair_needed"])
        self.assertGreater(plan["action_count"], 0)

        # Unconfirmed repair rejected
        unconf = self.engine.execute_repair(confirmed=False)
        self.assertFalse(unconf["accepted"])

        # Confirmed repair
        res = self.engine.execute_repair(confirmed=True)
        self.assertTrue(res["accepted"])
        self.assertEqual(res["status"], "completed")

        # Verify Config/settings.json created
        settings_file = self.hub_root / "Config" / "settings.json"
        self.assertTrue(settings_file.exists())
        cfg = json.loads(settings_file.read_text(encoding="utf-8"))
        self.assertIn("schema_version", cfg)
        self.assertIn("ui", cfg)

    def test_repair_corrupt_settings_preserves_backup(self):
        cfg_dir = self.hub_root / "Config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        settings_file = cfg_dir / "settings.json"
        settings_file.write_text("{corrupt json", encoding="utf-8")

        res = self.engine.execute_repair(confirmed=True)
        self.assertTrue(res["accepted"])

        # Check backup created
        backup = cfg_dir / "settings.json.corrupt"
        self.assertTrue(backup.exists())
        self.assertEqual(backup.read_text(encoding="utf-8"), "{corrupt json")

        # Check repaired settings is valid JSON
        repaired = json.loads(settings_file.read_text(encoding="utf-8"))
        self.assertEqual(repaired["schema_version"], 2)


class TestSafeUninstallation(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.hub_root = Path(self.temp.name)
        # Create app files
        (self.hub_root / "src").mkdir(parents=True, exist_ok=True)
        (self.hub_root / "src" / "main.py").write_text("app", encoding="utf-8")
        (self.hub_root / "requirements-hub.txt").write_text("reqs", encoding="utf-8")

        # Create user data
        (self.hub_root / "Models").mkdir(parents=True, exist_ok=True)
        (self.hub_root / "Models" / "model.bin").write_text("model", encoding="utf-8")
        (self.hub_root / "Config").mkdir(parents=True, exist_ok=True)
        (self.hub_root / "Config" / "settings.json").write_text("{}", encoding="utf-8")
        (self.hub_root / "Output").mkdir(parents=True, exist_ok=True)
        (self.hub_root / "Output" / "out.png").write_text("img", encoding="utf-8")

        self.addCleanup(self.temp.cleanup)

    def test_uninstall_plan_preserves_user_data(self):
        plan = uninstall_mod.plan_uninstall(self.hub_root)
        self.assertIn("src", plan["removable_app_components"])
        self.assertIn("Models", plan["preserved_user_data"])
        self.assertIn("Config", plan["preserved_user_data"])
        self.assertIn("Output", plan["preserved_user_data"])

    def test_execute_safe_uninstall_removes_app_only(self):
        res = uninstall_mod.execute_safe_uninstall(self.hub_root, confirmed=True)
        self.assertTrue(res["accepted"])
        self.assertFalse((self.hub_root / "src").exists())
        self.assertFalse((self.hub_root / "requirements-hub.txt").exists())

        # User data must still exist
        self.assertTrue((self.hub_root / "Models" / "model.bin").exists())
        self.assertTrue((self.hub_root / "Config" / "settings.json").exists())
        self.assertTrue((self.hub_root / "Output" / "out.png").exists())


class TestReleaseManifestAndPackaging(unittest.TestCase):
    def test_build_release_package_manifest(self):
        manifest = build_mod.build_release_package()
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["application_name"], "Local AI Hub")
        self.assertEqual(manifest["version"], "6.0.0")
        self.assertEqual(manifest["platform"], "windows-x64")
        self.assertIn("release_artifacts", manifest)
        self.assertIn("core_zip", manifest["release_artifacts"])
        self.assertIn("sha256", manifest["release_artifacts"]["core_zip"])
        self.assertIn("setup_exe", manifest["release_artifacts"])
        self.assertIn("sha256", manifest["release_artifacts"]["setup_exe"])
        self.assertEqual(manifest["ai_runtime_validation_status"], "DEFERRED BY USER")
        self.assertIn("excluded_machine_local_data", manifest)


class TestMojibakeAndI18nIntegrity(unittest.TestCase):
    def test_no_mojibake_in_release_components(self):
        mojibake_re = re.compile(r"Ã[\x80-\xbf]|Ä[\x80-\xbf]|á»[\x80-\xbf]|áº[\x80-\xbf]|Æ°|á»£")
        bad_files = []
        for check_dir in ("src", "scripts", "distribution"):
            for root, _, files in os.walk(check_dir):
                for f in files:
                    if f.endswith((".py", ".js", ".json", ".iss", ".ps1")):
                        path = os.path.join(root, f)
                        with open(path, encoding="utf-8") as handle:
                            content = handle.read()
                        matches = mojibake_re.findall(content)
                        if matches:
                            bad_files.append((path, matches[:5]))
        self.assertEqual(bad_files, [], f"Mojibake found: {bad_files}")


if __name__ == "__main__":
    unittest.main()
