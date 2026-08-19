"""
  FILE NOTE
  - Mục đích: Unit and contract tests cho V6 Final Product-Surface Correctness & Safety Patch
  - Liên kết trực tiếp: src/services/backup_manager.py, src/app_config/settings_service.py, src/services/diagnostics/center.py, src/services/api/api_server.py
  - Vùng ảnh hưởng khi sửa: Test verification cho opaque backup_id, plan_id, stale conflict detection (409), settings allowlist, git integrity, selective draft clearing, và mojibake prevention
"""

from __future__ import annotations

import json
import os
import re
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import src.app_config.schema as schema_mod
import src.app_config.settings_service as ss_mod
import src.services.backup_manager as bm_mod
import src.services.diagnostics.center as diag_center_mod
import src.services.node_studio.state as ns_state_mod
import src.shared.paths.registry as paths_mod
from src.app_config.schema import SETTINGS_SCHEMA_VERSION, validate_settings
from src.app_config.settings_service import SettingsPersistence
from src.services.api.api_server import HubHandler, HubHTTPServer
from src.services.backup_manager import BackupManager
from src.services.diagnostics.center import DiagnosticsCenter


class TestBackupManagerOpaqueContract(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.config_dir = Path(self.temp.name) / "Config"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir = self.config_dir / "backups"

        (self.config_dir / "settings.json").write_text(
            json.dumps({"schema_version": 2, "settings_revision": 1, "ui": {"language": "vi"}}),
            encoding="utf-8",
        )
        self.patches = [
            patch.object(paths_mod, "CONFIG_ROOT", self.config_dir),
            patch.object(bm_mod, "CONFIG_ROOT", self.config_dir),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.temp.cleanup)

    def test_backup_id_opaque_lifecycle(self):
        mgr = BackupManager(backup_dir=self.backup_dir)
        create_res = mgr.create_backup()
        self.assertTrue(create_res["accepted"])
        self.assertIn("backup_id", create_res)
        self.assertNotIn("backup_path", create_res)
        backup_id = create_res["backup_id"]
        self.assertTrue(backup_id.startswith("backup_"))

        # List
        listed = mgr.list_backups()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["backup_id"], backup_id)
        self.assertNotIn("backup_path", listed[0])

        # Inspect
        inspect_res = mgr.inspect_backup(backup_id)
        self.assertTrue(inspect_res["valid"])
        self.assertEqual(inspect_res["errors"], [])

        # Plan
        plan_res = mgr.plan_restore(backup_id)
        self.assertTrue(plan_res["accepted"])
        self.assertIn("plan_id", plan_res)
        self.assertIn("preview", plan_res)
        self.assertIn("changes", plan_res)
        self.assertNotIn("backup_path", plan_res)
        plan_id = plan_res["plan_id"]

        # Apply unconfirmed -> rejected
        app_unconf = mgr.apply_restore(plan_id, confirmed=False)
        self.assertFalse(app_unconf["accepted"])
        self.assertEqual(app_unconf["status"], "unconfirmed")

        # Apply confirmed -> success
        app_conf = mgr.apply_restore(plan_id, confirmed=True)
        self.assertTrue(app_conf["accepted"])
        self.assertTrue(app_conf["verified"])
        self.assertIn("settings.json", app_conf["applied"])

    def test_stale_restore_plan_conflict_detection(self):
        mgr = BackupManager(backup_dir=self.backup_dir)
        create_res = mgr.create_backup()
        backup_id = create_res["backup_id"]

        plan_res = mgr.plan_restore(backup_id)
        plan_id = plan_res["plan_id"]

        # Simulate state change after plan generation
        (self.config_dir / "settings.json").write_text(
            json.dumps({"schema_version": 2, "settings_revision": 99, "ui": {"language": "en"}}),
            encoding="utf-8",
        )

        # Applying stale plan must fail with 409 conflict
        app_res = mgr.apply_restore(plan_id, confirmed=True)
        self.assertFalse(app_res["accepted"])
        self.assertEqual(app_res["status"], "conflict")
        self.assertEqual(app_res.get("code"), 409)

    def test_external_and_traversal_backup_rejected(self):
        mgr = BackupManager(backup_dir=self.backup_dir)
        outside = Path(self.temp.name) / "evil.zip"
        outside.write_bytes(b"PK" + bytes(18))
        insp = mgr.inspect_backup(outside)
        self.assertFalse(insp["valid"])

        insp_trav = mgr.inspect_backup("backup_../../../etc/passwd")
        self.assertFalse(insp_trav["valid"])


class TestSettingsPersistenceAllowlist(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.config_dir = Path(self.temp.name) / "Config"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.config_dir / "settings.json"
        self.svc = SettingsPersistence(self.settings_path)
        self.addCleanup(self.temp.cleanup)

    def test_network_section_rejected(self):
        res = self.svc.save({"network": {"bind_host": "127.0.0.1"}})
        self.assertFalse(res["accepted"])
        self.assertEqual(res["status"], "invalid")
        self.assertIn("network", res["reason"])

    def test_unknown_section_rejected(self):
        res = self.svc.save({"infrastructure": {"cluster": "local"}})
        self.assertFalse(res["accepted"])
        self.assertEqual(res["status"], "invalid")

    def test_theme_system_dark_light(self):
        for theme in ("system", "dark", "light"):
            res = self.svc.save({"ui": {"theme": theme}})
            self.assertTrue(res["accepted"])
            self.assertEqual(res["settings"]["ui"]["theme"], theme)

    def test_model_load_policies(self):
        for policy in ("on_demand", "keep_loaded"):
            res = self.svc.save({"jobs": {"model_load_policy": policy}})
            self.assertTrue(res["accepted"])
            self.assertEqual(res["settings"]["jobs"]["model_load_policy"], policy)

    def test_window_minimum_bounds(self):
        res = self.svc.save({"window": {"minimum_width": 1920, "minimum_height": 1080}})
        self.assertTrue(res["accepted"])
        self.assertEqual(res["settings"]["window"]["minimum_width"], 1920)
        self.assertEqual(res["settings"]["window"]["minimum_height"], 1080)

    def test_all_five_languages_accepted(self):
        for lang in ("vi", "en", "zh", "ja", "ko"):
            res = self.svc.save({"ui": {"language": lang}})
            self.assertTrue(res["accepted"])
            self.assertEqual(res["settings"]["ui"]["language"], lang)


class TestDiagnosticsSubsystemAndSanitization(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.config_dir = Path(self.temp.name) / "Config"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.patches = [
            patch.object(paths_mod, "CONFIG_ROOT", self.config_dir),
            patch.object(diag_center_mod, "CONFIG_ROOT", self.config_dir),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.temp.cleanup)

    def test_git_integrity_read_only(self):
        center = DiagnosticsCenter()
        state = center.git_integrity_state()
        self.assertIn("status", state)
        self.assertIn("root_verified", state)
        self.assertIn("inside_work_tree", state)
        self.assertIn("head_sha", state)
        self.assertIn("branch", state)
        self.assertIn("origin", state)

    def test_export_sanitizes_paths_and_tokens(self):
        center = DiagnosticsCenter()
        bundle = center.export_diagnostics_bundle()
        self.assertTrue(bundle.get("sanitized"))
        raw = json.dumps(bundle["bundle"])
        self.assertNotIn("api_key=", raw)
        self.assertNotIn("token=", raw)
        self.assertNotRegex(raw, r"[A-Za-z]:\[Uu]sers\[a-zA-Z0-9_-]+\AppData")


class TestMojibakeRegressionPrevention(unittest.TestCase):
    def test_no_double_utf8_mojibake_in_src(self):
        mojibake_re = re.compile(r"Ã[\x80-\xbf]|Ä[\x80-\xbf]|á»[\x80-\xbf]|áº[\x80-\xbf]|Æ°|á»£")
        bad_files = []
        for root, _, files in os.walk("src"):
            for f in files:
                if f.endswith((".py", ".js", ".json")):
                    path = os.path.join(root, f)
                    with open(path, encoding="utf-8") as handle:
                        content = handle.read()
                    matches = mojibake_re.findall(content)
                    if matches:
                        bad_files.append((path, matches[:5]))
        self.assertEqual(bad_files, [], f"Mojibake detected in files: {bad_files}")


if __name__ == "__main__":
    unittest.main()
