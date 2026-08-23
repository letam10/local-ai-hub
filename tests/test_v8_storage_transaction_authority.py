"""Synthetic V8 Wave 0 storage/transaction authority regressions.

No network, runtime, model, GPU or external application is used.
"""

from __future__ import annotations

import errno
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.platform.paths import HubPaths
from src.platform.storage_authority import StorageAuthority, StorageAuthorityError
from src.services.output_authority import OutputAuthority, OutputAuthorityError
from src.services.transaction_store import (
    V8TransactionStore,
    decode_file_identity_from_sqlite,
)


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


class V8StorageTransactionAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = self.root / "app"
        self.data = self.root / "data"
        self.app.mkdir()
        self.data.mkdir()
        self.paths = HubPaths(app_root=self.app, data_root=self.data)
        self.store_path = self.root / "journal.sqlite3"
        self.store = V8TransactionStore(self.store_path)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _authority(self) -> OutputAuthority:
        return OutputAuthority(paths=self.paths, store=self.store)

    def test_output_root_reparse_is_refused_before_reservation(self) -> None:
        external = self.root / "external"
        external.mkdir()
        output = self.data / "Output"
        try:
            output.symlink_to(external, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Directory symlinks are unavailable on this test host.")
        authority = self._authority()
        with self.assertRaises((StorageAuthorityError, OutputAuthorityError)):
            authority.begin_reservation(_job("a"))
        self.assertEqual(list(external.iterdir()), [])
        self.assertEqual(self.store.list_public_artifacts(), [])

    def test_transaction_cannot_publish_under_another_reservation_or_job(self) -> None:
        job_a = _job("a")
        job_b = _job("b")
        reservation_a = self.store.create_output_reservation(job_a)
        reservation_b = self.store.create_output_reservation(job_b)
        transaction = self.store.create_output_transaction(reservation_a, job_a)
        digest = hashlib.sha256(b"payload").hexdigest()
        artifact_id = self.store.stage_artifact(
            transaction_id=transaction,
            reservation_id=reservation_a,
            job_id=job_a,
            object_id="obj_" + "c" * 32,
            object_key=".hub-v8/objects/cc/obj_" + "c" * 32,
            name="result.bin",
            media_type="application/octet-stream",
            size_bytes=7,
            sha256=digest,
            file_device=1,
            file_inode=2,
            file_mtime_ns=3,
            provenance=_provenance(job_a),
        )
        self.assertIsNone(self.store.commit_output_transaction(transaction, reservation_b, job_b))
        self.assertIsNone(self.store.public_artifact(artifact_id))
        self.assertEqual(self.store.list_public_artifacts(), [])
        self.assertTrue(self.store.authorize_output_transaction(transaction, reservation_a, job_a))
        published = self.store.commit_output_transaction(transaction, reservation_a, job_a)
        self.assertIsNotNone(published)
        assert published is not None
        self.assertEqual([item["id"] for item in published], [artifact_id])

    def test_staged_artifact_is_not_public_before_final_commit(self) -> None:
        job_id = _job("d")
        reservation = self.store.create_output_reservation(job_id)
        transaction = self.store.create_output_transaction(reservation, job_id)
        artifact_id = self.store.stage_artifact(
            transaction_id=transaction,
            reservation_id=reservation,
            job_id=job_id,
            object_id="obj_" + "d" * 32,
            object_key=".hub-v8/objects/dd/obj_" + "d" * 32,
            name="result.bin",
            media_type="application/octet-stream",
            size_bytes=1,
            sha256=hashlib.sha256(b"x").hexdigest(),
            file_device=1,
            file_inode=2,
            file_mtime_ns=3,
            provenance=_provenance(job_id),
        )
        self.assertIsNone(self.store.public_artifact(artifact_id))
        self.assertEqual(self.store.list_public_artifacts(), [])
        self.assertTrue(self.store.authorize_output_transaction(transaction, reservation, job_id))
        self.assertIsNone(self.store.public_artifact(artifact_id))
        self.assertEqual(self.store.list_public_artifacts(), [])

    def test_unsigned_windows_file_identity_round_trips_through_sqlite(self) -> None:
        job_id = _job("c")
        reservation = self.store.create_output_reservation(job_id)
        transaction = self.store.create_output_transaction(reservation, job_id)
        artifact_id = self.store.stage_artifact(
            transaction_id=transaction,
            reservation_id=reservation,
            job_id=job_id,
            object_id="obj_" + "e" * 32,
            object_key=".hub-v8/objects/ee/obj_" + "e" * 32,
            name="result.bin",
            media_type="application/octet-stream",
            size_bytes=1,
            sha256=hashlib.sha256(b"x").hexdigest(),
            file_device=(1 << 64) - 1,
            file_inode=(1 << 63) + 9,
            file_mtime_ns=3,
            provenance=_provenance(job_id),
        )
        row = self.store.internal_artifact(artifact_id)
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(decode_file_identity_from_sqlite(row["file_device"]), (1 << 64) - 1)
        self.assertEqual(decode_file_identity_from_sqlite(row["file_inode"]), (1 << 63) + 9)

    def test_public_artifact_is_detached_from_mutable_producer_path(self) -> None:
        authority = self._authority()
        job_id = _job("e")
        reservation = authority.begin_reservation(job_id)
        producer = self.data / "Output" / "producer.bin"
        producer.write_bytes(b"original producer bytes")
        published = authority.publish_owned_candidates(
            reservation_id=reservation,
            job_id=job_id,
            candidates=[producer],
            provenance=_provenance(job_id),
        )
        self.assertIsNotNone(published)
        assert published is not None
        artifact_id = str(published[0]["id"])
        resolved = authority.resolve(artifact_id)
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertNotEqual(resolved, producer)
        self.assertEqual(resolved.read_bytes(), b"original producer bytes")

        producer.unlink()
        producer.write_bytes(b"foreign replacement")
        resolved_after = authority.resolve(artifact_id)
        self.assertIsNotNone(resolved_after)
        assert resolved_after is not None
        self.assertEqual(resolved_after.read_bytes(), b"original producer bytes")

    def test_managed_object_replacement_fails_closed(self) -> None:
        authority = self._authority()
        job_id = _job("f")
        reservation = authority.begin_reservation(job_id)
        producer = self.data / "Output" / "producer.bin"
        producer.write_bytes(b"immutable bytes")
        published = authority.publish_owned_candidates(
            reservation_id=reservation,
            job_id=job_id,
            candidates=[producer],
            provenance=_provenance(job_id),
        )
        self.assertIsNotNone(published)
        assert published is not None
        artifact_id = str(published[0]["id"])
        managed = authority.resolve(artifact_id)
        self.assertIsNotNone(managed)
        assert managed is not None
        managed.unlink()
        managed.write_bytes(b"foreign bytes")
        self.assertIsNone(authority.resolve(artifact_id))
        self.assertIsNone(authority.describe(artifact_id))
        self.assertEqual(authority.list_public(), [])

    def test_identity_attested_cleanup_preserves_a_replaced_foreign_leaf(self) -> None:
        storage = StorageAuthority(self.paths)
        lease = storage.lease("output", create=True)
        relative = "owned.bin"
        target = storage.resolve_relative(lease, relative, require_exists=False)
        target.write_bytes(b"owned bytes")
        expected = storage.file_identity(lease, relative)
        replacement = target.with_name("foreign-replacement.bin")
        replacement.write_bytes(b"foreign bytes")
        replacement.replace(target)

        self.assertFalse(storage.unlink_if_identity(lease, relative, expected))
        self.assertEqual(target.read_bytes(), b"foreign bytes")

    def test_foreign_job_cannot_use_owner_reservation(self) -> None:
        authority = self._authority()
        owner = _job("1")
        foreign = _job("2")
        reservation = authority.begin_reservation(owner)
        producer = self.data / "Output" / "foreign-attempt.bin"
        producer.write_bytes(b"data")
        result = authority.publish_owned_candidates(
            reservation_id=reservation,
            job_id=foreign,
            candidates=[producer],
            provenance=_provenance(foreign),
        )
        self.assertIsNone(result)
        self.assertEqual(authority.list_public(), [])

    def test_replacement_immediately_before_final_commit_is_not_published(self) -> None:
        authority = self._authority()
        job_id = _job("3")
        reservation = authority.begin_reservation(job_id)
        producer = self.data / "Output" / "late-replacement.bin"
        producer.write_bytes(b"owned bytes")
        original_commit = self.store.commit_output_transaction

        def replace_then_commit(transaction_id: str, reservation_id: str, committing_job_id: str):
            staged = self.store.incomplete_artifacts()
            self.assertEqual(len(staged), 1)
            lease = authority.storage.lease("output", create=True)
            managed = authority.storage.resolve_relative(lease, staged[0]["object_key"], require_exists=True)
            replacement = managed.with_name("foreign-replacement.bin")
            replacement.write_bytes(b"foreign replacement bytes")
            replacement.replace(managed)
            return original_commit(transaction_id, reservation_id, committing_job_id)

        with patch.object(self.store, "commit_output_transaction", side_effect=replace_then_commit):
            published = authority.publish_owned_candidates(
                reservation_id=reservation,
                job_id=job_id,
                candidates=[producer],
                provenance=_provenance(job_id),
            )

        self.assertIsNone(published)
        self.assertEqual(self.store.list_public_artifacts(), [])

    def test_replacement_after_commit_callback_is_revoked_without_deleting_foreign_bytes(self) -> None:
        authority = self._authority()
        job_id = _job("5")
        reservation = authority.begin_reservation(job_id)
        producer = self.data / "Output" / "post-commit-replacement.bin"
        producer.write_bytes(b"owned bytes")
        original_commit = self.store.commit_output_transaction
        managed_holder: list[tuple[Path, Path]] = []

        def commit_then_replace(transaction_id: str, reservation_id: str, committing_job_id: str):
            published = original_commit(transaction_id, reservation_id, committing_job_id)
            self.assertIsNotNone(published)
            assert published is not None
            lease = authority.storage.lease("output", create=True)
            managed = authority.storage.resolve_relative(
                lease,
                self.store.internal_artifact(str(published[0]["id"]))["object_key"],
                require_exists=True,
            )
            replacement = managed.with_name("foreign-post-commit.bin")
            managed_holder.append((managed, replacement))
            replacement.write_bytes(b"foreign post-commit bytes")
            replacement.replace(managed)
            return published

        with patch.object(self.store, "commit_output_transaction", side_effect=commit_then_replace):
            published = authority.publish_owned_candidates(
                reservation_id=reservation,
                job_id=job_id,
                candidates=[producer],
                provenance=_provenance(job_id),
            )

        self.assertIsNone(published)
        self.assertEqual(self.store.list_public_artifacts(), [])
        self.assertEqual(len(managed_holder), 1)
        managed, replacement = managed_holder[0]
        survivors = [
            path
            for path in (managed, replacement)
            if path.exists() and path.read_bytes() == b"foreign post-commit bytes"
        ]
        self.assertEqual(len(survivors), 1)

    def test_publication_rollback_is_idempotent_and_removes_index_rows(self) -> None:
        job_id = _job("6")
        reservation = self.store.create_output_reservation(job_id)
        transaction = self.store.create_output_transaction(reservation, job_id)
        artifact_id = self.store.stage_artifact(
            transaction_id=transaction,
            reservation_id=reservation,
            job_id=job_id,
            object_id="obj_" + "6" * 32,
            object_key=".hub-v8/objects/66/obj_" + "6" * 32,
            name="result.bin",
            media_type="application/octet-stream",
            size_bytes=1,
            sha256=hashlib.sha256(b"x").hexdigest(),
            file_device=1,
            file_inode=2,
            file_mtime_ns=3,
            provenance=_provenance(job_id),
        )

        self.assertTrue(self.store.rollback_output_transaction(transaction, reservation, job_id))
        self.assertTrue(self.store.rollback_output_transaction(transaction, reservation, job_id))
        self.assertIsNone(self.store.internal_artifact(artifact_id))
        self.assertIsNone(self.store.public_artifact(artifact_id))
        self.assertEqual(self.store.list_public_artifacts(), [])

    def test_disk_full_during_managed_copy_aborts_without_public_artifact(self) -> None:
        authority = self._authority()
        job_id = _job("4")
        reservation = authority.begin_reservation(job_id)
        producer = self.data / "Output" / "disk-full.bin"
        producer.write_bytes(b"producer bytes remain intact")

        with patch("src.services.output_authority.os.fsync", side_effect=OSError(errno.ENOSPC, "disk full")):
            published = authority.publish_owned_candidates(
                reservation_id=reservation,
                job_id=job_id,
                candidates=[producer],
                provenance=_provenance(job_id),
            )

        self.assertIsNone(published)
        self.assertEqual(self.store.list_public_artifacts(), [])
        self.assertEqual(self.store.incomplete_artifacts(), [])
        self.assertEqual(producer.read_bytes(), b"producer bytes remain intact")


if __name__ == "__main__":
    unittest.main()
