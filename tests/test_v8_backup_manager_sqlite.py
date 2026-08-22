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
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from unittest.mock import patch

import src.services.backup_manager as bm
from src.services.backup_manager import BackupManager, V8_DATA_CLASSES
from src.services.api import api_server
from src.services.api.api_server import HubHTTPServer, HubHandler
from src.services.transaction_store import TransactionStoreError, V8TransactionStore


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

    @staticmethod
    def _request(port: int, method: str, path: str, body: dict[str, object] | None = None) -> tuple[int, dict[str, object]]:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            method=method,
        )
        request.add_header("Content-Type", "application/json")
        request.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=5.0) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

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

    def test_loopback_backup_routes_restore_v8_only_after_confirmation(self) -> None:
        self.store.create_output_reservation(_job("f"))
        previous_context = api_server._api_context_cache
        previous_router = api_server._api_router
        server = HubHTTPServer(("127.0.0.1", 0), HubHandler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        try:
            api_server._api_context_cache = None
            api_server._api_router = None
            thread.start()
            port = int(server.server_address[1])

            create_status, created = self._request(port, "POST", "/api/backup/create")
            self.assertEqual(create_status, 201)
            self.assertTrue(created.get("accepted"), created)
            backup_id = created.get("backup_id")
            self.assertIsInstance(backup_id, str)
            self.assertNotIn(str(self.config), json.dumps(created, ensure_ascii=False))

            inspect_status, inspected = self._request(port, "POST", "/api/backup/inspect", {"backup_id": backup_id})
            self.assertEqual(inspect_status, 200)
            self.assertTrue(inspected.get("valid"), inspected)
            self.assertIn("v8_transaction_store", inspected.get("manifest", {}).get("included_data_classes", []))

            self.store.create_output_reservation(_job("a"))
            (self.config / "settings.json").write_text('{"schema_version":2,"settings_revision":33}', encoding="utf-8")
            plan_status, planned = self._request(port, "POST", "/api/backup/plan", {"backup_id": backup_id})
            self.assertEqual(plan_status, 200)
            self.assertTrue(planned.get("accepted"), planned)
            plan_id = planned.get("plan_id")
            self.assertIsInstance(plan_id, str)

            unconfirmed_status, unconfirmed = self._request(port, "POST", "/api/backup/apply", {"plan_id": plan_id, "confirmed": False})
            self.assertEqual(unconfirmed_status, 400)
            self.assertFalse(unconfirmed.get("accepted"), unconfirmed)
            self.assertEqual(self._reservation_count(), 2)

            applied_status, applied = self._request(port, "POST", "/api/backup/apply", {"plan_id": plan_id, "confirmed": True})
            self.assertEqual(applied_status, 200)
            self.assertTrue(applied.get("accepted"), applied)
            self.assertIn("v8_transaction_store", applied.get("applied", []))
            self.assertEqual(self._reservation_count(), 1)
            self.assertEqual(json.loads((self.config / "settings.json").read_text(encoding="utf-8"))["settings_revision"], 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3.0)
            api_server._api_context_cache = previous_context
            api_server._api_router = previous_router

    def test_locked_v8_source_fails_closed_without_publishing_backup(self) -> None:
        self.store.create_output_reservation(_job("b"))
        before = self.control.read_bytes()
        locker = sqlite3.connect(self.control, timeout=0.25, isolation_level=None)
        try:
            locker.execute("BEGIN EXCLUSIVE")
            result = self._manager().create_backup()
        finally:
            locker.rollback()
            locker.close()
        self.assertFalse(result["accepted"], result)
        self.assertEqual(self.control.read_bytes(), before)
        self.assertEqual(list(self.backups.glob("*.zip")), [])
        self.assertEqual(list(self.backups.glob(".v8-backup-*")), [])

    def test_v8_snapshot_failure_does_not_publish_json_only_archive(self) -> None:
        self.store.create_output_reservation(_job("c"))
        before = self.control.read_bytes()
        with patch.object(V8TransactionStore, "backup_to", side_effect=TransactionStoreError("transaction_backup_unavailable")):
            result = self._manager().create_backup()
        self.assertFalse(result["accepted"], result)
        self.assertNotIn(str(self.config), json.dumps(result, ensure_ascii=False))
        self.assertEqual(self.control.read_bytes(), before)
        self.assertEqual(list(self.backups.glob("*.zip")), [])

    def test_sqlite_restore_refusal_rolls_back_applied_json(self) -> None:
        self.store.create_output_reservation(_job("d"))
        created = self._manager().create_backup()
        self.assertTrue(created["accepted"], created)
        (self.config / "settings.json").write_text('{"schema_version":2,"settings_revision":66}', encoding="utf-8")
        before_settings = (self.config / "settings.json").read_bytes()
        before_control = self.control.read_bytes()
        planned = self._manager().plan_restore(created["backup_id"])
        self.assertTrue(planned["accepted"], planned)
        with patch.object(V8TransactionStore, "restore_from", side_effect=TransactionStoreError("transaction_backup_unavailable")):
            result = self._manager().apply_restore(planned["plan_id"], confirmed=True)
        self.assertFalse(result["accepted"], result)
        self.assertEqual(result["code"], "restore_transaction_failed")
        self.assertEqual((self.config / "settings.json").read_bytes(), before_settings)
        self.assertEqual(self.control.read_bytes(), before_control)
        self.assertEqual(list(self.config.glob(".backup-restore-*")), [])


if __name__ == "__main__":
    unittest.main()
