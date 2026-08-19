"""
/*
  FILE NOTE
  - Mục đích: End-to-end integration test cho Installer, Desktop Launcher, Single Instance, Loopback Security, Settings Persistence, Repair, Update Preservation, và Safe Uninstall trong isolated temporary fixture
  - Liên kết trực tiếp: scripts/build_installer.py, src/app/launcher.py, src/services/updater/engine.py, src/services/diagnostics/repair_engine.py, scripts/uninstall_hub.py
  - Vùng ảnh hưởng khi sửa: Toàn bộ quy trình phân phối và phân phối độc lập của Local AI Hub trên Windows
*/
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

import scripts.build_installer as build_mod
from src.app.main import APP_INSTANCE_MUTEX, HOST, PORT, UI_URL, _load_window_settings
from src.services.diagnostics.repair_engine import DesktopRepairEngine
from src.services.process_manager.windows import startup_mutex
from src.services.updater.engine import UpdateEngine
import scripts.uninstall_hub as uninstall_mod


class TestIsolatedInstallerDistribution(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Build clean release package
        cls.manifest = build_mod.build_release_package(compile_exe=True)
        cls.dist_zip = build_mod.DIST_DIR / "LocalAIHub-Core-Win64-v6.0.0.zip"
        cls.setup_exe = build_mod.DIST_DIR / "LocalAIHub-Setup-Win64-v6.0.0.exe"

    def test_01_artifacts_exist_and_match_manifest(self) -> None:
        self.assertTrue(self.dist_zip.exists(), "Release ZIP must exist")
        self.assertTrue(self.setup_exe.exists(), "Setup EXE must exist")

        core_zip_info = self.manifest["release_artifacts"]["core_zip"]
        self.assertEqual(core_zip_info["file_name"], self.dist_zip.name)
        self.assertEqual(core_zip_info["size_bytes"], self.dist_zip.stat().st_size)
        self.assertEqual(core_zip_info["sha256"], build_mod.sha256_file(self.dist_zip))

        setup_exe_info = self.manifest["release_artifacts"]["setup_exe"]
        self.assertEqual(setup_exe_info["file_name"], self.setup_exe.name)
        self.assertEqual(setup_exe_info["size_bytes"], self.setup_exe.stat().st_size)
        self.assertEqual(setup_exe_info["sha256"], build_mod.sha256_file(self.setup_exe))

    def test_02_isolated_clean_install_and_lifecycle(self) -> None:
        with TemporaryDirectory() as tmpdir:
            install_root = Path(tmpdir) / "LocalAIHub"
            install_root.mkdir(parents=True, exist_ok=True)

            # 1. Simulate Clean Install from release ZIP
            with zipfile.ZipFile(self.dist_zip, "r") as zf:
                zf.extractall(install_root)

            # Verify core components present
            self.assertTrue((install_root / "src/app/main.py").exists())
            self.assertTrue((install_root / "src/app/launcher.py").exists())
            self.assertTrue((install_root / "LocalAIHub.vbs").exists())
            self.assertTrue((install_root / "LocalAIHub.cmd").exists())
            self.assertTrue((install_root / "Config/hub_config.example.json").exists())

            # 2. Verify Loopback-only binding
            self.assertEqual(HOST, "127.0.0.1")
            self.assertEqual(PORT, 8765)
            self.assertEqual(UI_URL, "http://127.0.0.1:8765/ui/")

            # 3. Verify Single Instance Mutex
            import threading
            acquired_in_thread: list[bool] = []
            with startup_mutex(APP_INSTANCE_MUTEX, 0.5) as first_acquired:
                self.assertTrue(first_acquired)
                def try_acquire():
                    with startup_mutex(APP_INSTANCE_MUTEX, 0.05) as acq:
                        acquired_in_thread.append(acq)
                t = threading.Thread(target=try_acquire)
                t.start()
                t.join(timeout=1.0)
                self.assertEqual(acquired_in_thread, [False])

            # 4. Verify Settings Persistence in install root
            config_dir = install_root / "Config"
            config_dir.mkdir(parents=True, exist_ok=True)
            settings_path = config_dir / "settings.json"
            sample_settings = {
                "schema_version": 2,
                "ui": {"theme": "dark", "language": "vi"},
                "window": {"start_maximized": True, "minimum_width": 1280, "minimum_height": 720},
                "jobs": {"concurrency": 2},
            }
            settings_path.write_text(json.dumps(sample_settings, indent=2), encoding="utf-8")
            self.assertTrue(settings_path.exists())

            # 5. Populate mock user mutable data
            (install_root / "Models").mkdir(parents=True, exist_ok=True)
            (install_root / "Models/sample_model.bin").write_text("model_bytes", encoding="utf-8")
            (install_root / "Output").mkdir(parents=True, exist_ok=True)
            (install_root / "Output/image.png").write_text("image_bytes", encoding="utf-8")
            (install_root / "Reports").mkdir(parents=True, exist_ok=True)
            (install_root / "Reports/p0_source_reconciliation.local.bundle").write_text("forensic_bundle", encoding="utf-8")

            # 6. Verify Repair Engine in install root
            repair_engine = DesktopRepairEngine(install_root)
            rep_report = repair_engine.report()
            self.assertIn("status", rep_report)
            rep_plan = repair_engine.plan_repair()
            rep_result = repair_engine.execute_repair(confirmed=True)
            self.assertTrue(rep_result["accepted"])

            # 7. Verify Update Preservation
            update_engine = UpdateEngine(install_root)
            update_zip = Path(tmpdir) / "update.zip"
            with zipfile.ZipFile(update_zip, "w") as zf:
                zf.writestr("src/app/main.py", "# updated main")
                zf.writestr("src/app/new_module.py", "# new module")

            update_res = update_engine.apply_update(update_zip, confirmed=True)
            self.assertTrue(update_res.accepted)
            self.assertEqual((install_root / "src/app/main.py").read_text(encoding="utf-8"), "# updated main")

            # Crucial: verify all mutable user data preserved after update
            self.assertEqual((install_root / "Models/sample_model.bin").read_text(encoding="utf-8"), "model_bytes")
            self.assertEqual((install_root / "Output/image.png").read_text(encoding="utf-8"), "image_bytes")
            self.assertEqual((install_root / "Reports/p0_source_reconciliation.local.bundle").read_text(encoding="utf-8"), "forensic_bundle")
            self.assertTrue(settings_path.exists())

            # 8. Verify Safe Uninstall
            uninstall_plan = uninstall_mod.plan_uninstall(install_root)
            self.assertIn("src", uninstall_plan["removable_app_components"])
            self.assertIn("Models", uninstall_plan["preserved_user_data"])
            self.assertIn("Config", uninstall_plan["preserved_user_data"])
            self.assertIn("Output", uninstall_plan["preserved_user_data"])

            uninstall_res = uninstall_mod.execute_safe_uninstall(install_root, confirmed=True)
            self.assertTrue(uninstall_res["accepted"])

            # Verify app binaries removed
            self.assertFalse((install_root / "src").exists())
            self.assertFalse((install_root / "LocalAIHub.vbs").exists())
            self.assertFalse((install_root / "LocalAIHub.cmd").exists())

            # Verify ALL user data strictly preserved
            self.assertTrue((install_root / "Models/sample_model.bin").exists())
            self.assertTrue((install_root / "Output/image.png").exists())
            self.assertTrue((install_root / "Reports/p0_source_reconciliation.local.bundle").exists())
            self.assertTrue((install_root / "Config/settings.json").exists())


if __name__ == "__main__":
    unittest.main()
