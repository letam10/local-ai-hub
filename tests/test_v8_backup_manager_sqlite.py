"""V8 control-journal coverage for the bounded Config backup boundary.

Every case builds a private Config root beneath the caller's task-owned
temporary directory.  The live ``D:\\LocalAIHub\\Config`` journal is never a
test input or restore target.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile
from unittest.mock import patch

import src.services.backup_manager as bm
from src.services.backup_manager import BackupManager, V8_DATA_CLASSES
from src.services.transaction_store import V8TransactionStore


def _job(char: str) -> str:
    return "jobv5_" + char * 32


class V8BackupManagerSQLiteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = self.root / "Config"
        self.config.mkdir()
        self.backups = self.config / "backups"
        self.backups.mkdir()
        (self.config / "settings.json").write_text('{"schema_version":2,"settings_revision":1}', encoding="utf-8")
        (self.config / "creative_workspace.json").write_text('{"schema_version":1,"projects":[]}', encoding="utf-8")
        self.control = self.config / "v8_control.sqlite3"
        self.store = V8TransactionStore(self.control)
        self.config_patch = patch.object(bm, "CONFIG_ROOT", self.config)
        self.config_patch.start()
        with bm._PLANS_LOCK:
            bm._RESTORE_PLANS.clear()
        self.addCleanup(self.config_patch.stop)
        self.addCleanup(self.temp.cleanup)

    def _manager(self) -> BackupManager:
        return BackupManager(backup_dir=self.backups)

    def _reservation_count(self) -> int:
        connection = sqlite3.connect(self.control)
        try:
            return int(connection.execute("SELECT COUNT(*) FROM output_reservations").fetchone()[0])
        finally:
            connection.close()

    def _archive_path(self, backup_id: str) -> Path:
        path = self._manager()._resolve_backup_target(backup_id)
        self.assertIsNotNone(path)
        return path if path is not None else self.backups / "missing.zip"

    def test_backup_uses_consistent_v8_snapshot_and_copy_only_restore(self) -> None:
        self.store.create_output_reservation(_job("a"))
        created = self._manager().create_backup()
        self.assertTrue(created["accepted"], created)
        self.assertEqual(created["manifest"]["included_data_classes"], list(V8_DATA_CLASSES))
        self.assertNotIn(str(self.config), json.dumps(created, ensure_ascii=False))

        archive_path = self._archive_path(created["backup_id"])
        with zipfile.ZipFile(archive_path, "r") as archive:
            manifest = json.loads(archive.read("manifest.json"))
            self.assertIn("v8/v8_control.sqlite3", archive.namelist())
            self.assertEqual(manifest["included_data_classes"], list(V8_DATA_CLASSES))
            self.assertEqual(
                hashlib.sha256(archive.read("v8/v8_control.sqlite3")).hexdigest(),
                manifest["files"]["v8/v8_control.sqlite3"]["sha256"],
            )

        self.store.create_output_reservation(_job("b"))
        (self.config / "settings.json").write_text('{"schema_version":2,"settings_revision":9}', encoding="utf-8")
        planned = self._manager().plan_restore(created["backup_id"])
        self.assertTrue(planned["accepted"], planned)
        self.assertIn("v8_transaction_store", [item["member"] for item in planned["changes"]])
        restored = self._manager().apply_restore(planned["plan_id"], confirmed=True)
        self.assertTrue(restored["accepted"], restored)
        self.assertIn("v8_transaction_store", restored["applied"])
        self.assertEqual(self._reservation_count(), 1)
        self.assertEqual(json.loads((self.config / "settings.json").read_text(encoding="utf-8"))["settings_revision"], 1)
        self.assertTrue(self._manager().verify_restore(restored)["valid"])
        self.assertEqual(list(self.config.glob(".backup-restore-*")), [])
        self.assertEqual(list(self.backups.glob(".v8-backup-*")), [])

    def test_v8_state_change_after_plan_refuses_without_restore(self) -> None:
        self.store.create_output_reservation(_job("c"))
        created = self._manager().create_backup()
        self.assertTrue(created["accepted"], created)
        planned = self._manager().plan_restore(created["backup_id"])
        self.assertTrue(planned["accepted"], planned)
        self.store.create_output_reservation(_job("d"))
        before = self.control.read_bytes()
        result = self._manager().apply_restore(planned["plan_id"], confirmed=True)
        self.assertFalse(result["accepted"], result)
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(self.control.read_bytes(), before)
        self.assertEqual(self._reservation_count(), 2)

    def test_invalid_v8_member_refuses_and_preserves_live_journal(self) -> None:
        self.store.create_output_reservation(_job("e"))
        created = self._manager().create_backup()
        self.assertTrue(created["accepted"], created)
        original = self._archive_path(created["backup_id"])
        with zipfile.ZipFile(original, "r") as archive:
            contents = {name: archive.read(name) for name in archive.namelist() if name != "manifest.json"}
            manifest = json.loads(archive.read("manifest.json"))
        contents["v8/v8_control.sqlite3"] = b"not a sqlite snapshot"
        manifest["files"]["v8/v8_control.sqlite3"] = {
            "sha256": hashlib.sha256(contents["v8/v8_control.sqlite3"]).hexdigest(),
            "size_bytes": len(contents["v8/v8_control.sqlite3"]),
        }
        original.unlink()
        with zipfile.ZipFile(original, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in contents.items():
                archive.writestr(name, content)
            archive.writestr("manifest.json", json.dumps(manifest, separators=(",", ":")).encode("utf-8"))

        before = self.control.read_bytes()
        planned = self._manager().plan_restore(created["backup_id"])
        self.assertTrue(planned["accepted"], planned)
        result = self._manager().apply_restore(planned["plan_id"], confirmed=True)
        self.assertFalse(result["accepted"], result)
        self.assertEqual(self.control.read_bytes(), before)

    def test_reparse_backed_v8_source_refuses_without_external_read(self) -> None:
        outside = self.root / "outside-control.sqlite3"
        outside.write_bytes(b"outside sentinel")
        self.control.unlink()
        try:
            self.control.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("File symlinks are unavailable on this test host.")
        result = self._manager().create_backup()
        self.assertFalse(result["accepted"], result)
        self.assertEqual(outside.read_bytes(), b"outside sentinel")
        self.assertEqual(list(self.backups.glob("*.zip")), [])


if __name__ == "__main__":
    unittest.main()
