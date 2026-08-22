"""Controlled V8 SQLite backup/restore authority tests.

These checks use only task-owned temporary journals.  They exercise SQLite's
backup API rather than copying a potentially torn control database file.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest

from src.services.transaction_store import (
    SCHEMA_VERSION,
    TransactionStoreError,
    V8TransactionStore,
    decode_file_identity_from_sqlite,
)
from src.services.v8_transaction_extensions import V8ProductionTransactionStore


def _job(char: str) -> str:
    return "jobv5_" + char * 32


def _provenance(job_id: str) -> dict[str, object]:
    return {
        "job_id": job_id,
        "job_spec_fingerprint": hashlib.sha256(job_id.encode("utf-8")).hexdigest(),
        "adapter_id": "test.synthetic.v8",
        "attempt": 1,
        "status": "completed",
    }


class V8TransactionBackupRestoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.live = self.root / "v8_control.sqlite3"
        self.store = V8TransactionStore(self.live)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _published_artifact(self, char: str = "a") -> tuple[str, str]:
        job_id = _job(char)
        reservation = self.store.create_output_reservation(job_id)
        transaction = self.store.create_output_transaction(reservation, job_id)
        artifact_id = self.store.stage_artifact(
            transaction_id=transaction,
            reservation_id=reservation,
            job_id=job_id,
            object_id="obj_" + char * 32,
            object_key=f".hub-v8/objects/{char}{char}/obj_{char * 32}",
            name="result.bin",
            media_type="application/octet-stream",
            size_bytes=7,
            sha256=hashlib.sha256(b"payload").hexdigest(),
            file_device=(1 << 64) - 1,
            file_inode=(1 << 63) + 17,
            file_mtime_ns=19,
            provenance=_provenance(job_id),
        )
        self.assertTrue(self.store.authorize_output_transaction(transaction, reservation, job_id))
        self.assertIsNotNone(self.store.commit_output_transaction(transaction, reservation, job_id))
        return job_id, artifact_id

    def test_sqlite_backup_and_clean_restore_preserve_public_artifact_identity(self) -> None:
        _job_id, artifact_id = self._published_artifact("a")
        snapshot = self.root / "accepted.sqlite3"
        backed_up = self.store.backup_to(snapshot)
        self.assertEqual(backed_up["status"], "completed")
        self.assertNotIn("path", backed_up)
        self.assertTrue(snapshot.is_file())

        self.store.create_output_reservation(_job("b"))
        restored = self.store.restore_from(snapshot)
        self.assertEqual(restored["status"], "completed")
        self.assertNotIn("path", restored)

        reopened = V8ProductionTransactionStore(self.live)
        public = reopened.public_artifact(artifact_id)
        self.assertIsNotNone(public)
        assert public is not None
        self.assertEqual(public["id"], artifact_id)
        self.assertIsNone(reopened.reservation_for_job(_job("b")))
        internal = reopened.internal_artifact(artifact_id)
        self.assertIsNotNone(internal)
        assert internal is not None
        self.assertEqual(decode_file_identity_from_sqlite(internal["file_device"]), (1 << 64) - 1)
        self.assertEqual(decode_file_identity_from_sqlite(internal["file_inode"]), (1 << 63) + 17)

    def test_corrupt_or_schema_mismatched_snapshot_never_overwrites_live_journal(self) -> None:
        self._published_artifact("c")
        before = self.live.read_bytes()

        corrupt = self.root / "corrupt.sqlite3"
        corrupt.write_bytes(b"not a sqlite database")
        with self.assertRaises(TransactionStoreError):
            self.store.restore_from(corrupt)
        self.assertEqual(self.live.read_bytes(), before)

        mismatch = self.root / "mismatch.sqlite3"
        connection = sqlite3.connect(mismatch)
        try:
            connection.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute("INSERT INTO meta(key,value) VALUES('schema_version', ?)", (SCHEMA_VERSION + ".other",))
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(TransactionStoreError):
            self.store.restore_from(mismatch)
        self.assertEqual(self.live.read_bytes(), before)

    def test_backup_refuses_existing_or_reparse_destination_without_overwrite(self) -> None:
        self._published_artifact("d")
        existing = self.root / "existing.sqlite3"
        existing.write_bytes(b"user backup bytes")
        with self.assertRaises(TransactionStoreError):
            self.store.backup_to(existing)
        self.assertEqual(existing.read_bytes(), b"user backup bytes")

        external = self.root / "external.sqlite3"
        external.write_bytes(b"outside bytes")
        link = self.root / "backup-link.sqlite3"
        try:
            link.symlink_to(external)
        except (OSError, NotImplementedError):
            self.skipTest("File symlinks are unavailable on this test host.")
        with self.assertRaises(TransactionStoreError):
            self.store.backup_to(link)
        self.assertEqual(external.read_bytes(), b"outside bytes")

    def test_backup_snapshot_remains_consistent_while_later_writer_commits(self) -> None:
        _job_id, first_artifact = self._published_artifact("e")
        snapshot = self.root / "consistent.sqlite3"
        self.store.backup_to(snapshot)
        self._published_artifact("f")

        snap = V8TransactionStore(snapshot)
        self.assertIsNotNone(snap.public_artifact(first_artifact))
        self.assertEqual(len(snap.list_public_artifacts()), 1)
        self.assertEqual(len(self.store.list_public_artifacts()), 2)


if __name__ == "__main__":
    unittest.main()
