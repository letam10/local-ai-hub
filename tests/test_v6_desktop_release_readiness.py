from __future__ import annotations

import json
import threading
import unittest
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory

from src.app.desktop_lifecycle import (
    StartupRaceGuard,
    GracefulShutdownCoordinator,
    startup_failure_payload,
)
from src.app_config.settings_service import SettingsPersistence
from src.services.backup_manager import BackupManager
from src.services.diagnostics.center import DiagnosticsCenter
from src.services.node_studio.state import draft_persist, draft_load, draft_clear


class TestV6DesktopReleaseReadiness(unittest.TestCase):
    def test_startup_failure_payload_structure(self):
        payload = startup_failure_payload("API_BIND_CONFLICT", "Port 8765 is already in use.")
        self.assertEqual(payload["status"], "startup_failure")
        self.assertEqual(payload["reason"], "API_BIND_CONFLICT")
        self.assertEqual(payload["next_action"], "Port 8765 is already in use.")

    def test_startup_race_guard_lifecycle(self):
        with TemporaryDirectory() as tmpdir:
            pid_file = Path(tmpdir) / "app.pid"
            guard = StartupRaceGuard(pid_file)
            self.assertFalse(guard.is_stale())
            self.assertIsNone(guard.read_stale_pid())

            guard.write_current_pid()
            self.assertTrue(guard.is_stale())
            self.assertIsInstance(guard.read_stale_pid(), int)

            guard.clear()
            self.assertFalse(guard.is_stale())
            self.assertIsNone(guard.read_stale_pid())

    def test_graceful_shutdown_coordinator(self):
        with TemporaryDirectory() as tmpdir:
            coord = GracefulShutdownCoordinator()
            flushed = []

            coord.register("settings", lambda: flushed.append("settings"))
            coord.register("jobs", lambda: flushed.append("jobs"))
            # Faulty flusher should not prevent others from running
            def _bad_flusher():
                raise RuntimeError("Disk full")
            coord.register("bad", _bad_flusher)
            coord.register("drafts", lambda: flushed.append("drafts"))

            results = coord.save_checkpoint()
            self.assertIn("settings", flushed)
            self.assertIn("jobs", flushed)
            self.assertIn("drafts", flushed)
            self.assertIn("bad", results.get("errors", {}))

    def test_end_to_end_data_safety_flow(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cfg_dir = root / "Config"
            cfg_dir.mkdir(parents=True, exist_ok=True)
            settings_file = cfg_dir / "settings.json"

            # 1. Save settings
            sp = SettingsPersistence(settings_file)
            res = sp.save({"ui": {"language": "vi", "theme": "dark"}})
            self.assertTrue(res["accepted"])

            # 2. Node draft
            import src.services.node_studio.state as ns_state_mod
            with patch.object(ns_state_mod, "CONFIG_ROOT", cfg_dir):
                d_res = draft_persist("image", {"nodes": [{"id": "1"}], "edges": []})
                self.assertTrue(d_res["accepted"])
                loaded = draft_load("image")
                self.assertIsNotNone(loaded)
                draft_clear("image")
                self.assertIsNone(draft_load("image"))

            # 3. Diagnostics
            import src.services.diagnostics.center as diag_mod
            with patch.object(diag_mod, "CONFIG_ROOT", cfg_dir):
                dc = DiagnosticsCenter()
                snap = dc.snapshot()
                self.assertIn("config_registry", snap)

            # 4. Backup
            bm = BackupManager(cfg_dir / "backups")
            import src.services.backup_manager as bm_mod
            with patch.object(bm_mod, "CONFIG_ROOT", cfg_dir):
                b_res = bm.create_backup()
                self.assertTrue(b_res["accepted"])
                self.assertTrue(Path(b_res["backup_path"]).exists())


if __name__ == "__main__":
    unittest.main()
