"""Controlled Windows V8 storage-authority adversarial acceptance."""

from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import threading
import unittest

from src.platform.paths import HubPaths
from src.platform.storage_authority import StorageAuthority, StorageAuthorityError
from src.services.output_authority_v8 import ProductionOutputAuthority
from src.services.v8_output_bridge import V8OutputBridge
from src.services.v8_transaction_extensions import V8ProductionTransactionStore


def _job(char: str) -> str:
    return "jobv5_" + char * 32


def _provenance(job_id: str) -> dict[str, object]:
    return {
        "job_id": job_id,
        "job_spec_fingerprint": hashlib.sha256(job_id.encode("ascii")).hexdigest(),
        "adapter_id": "test.filesystem.v8",
        "attempt": 1,
        "status": "completed",
    }


class V8WindowsFilesystemAdversarialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.paths = HubPaths(app_root=self.root / "app", data_root=self.root / "data")
        self.paths.app_root.mkdir()
        self.paths.data_root.mkdir()
        self.storage = StorageAuthority(self.paths)
        self.store = V8ProductionTransactionStore(self.paths.config_root / "v8_control.sqlite3")
        self.authority = ProductionOutputAuthority(paths=self.paths, store=self.store)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_root_and_data_ancestor_replacement_invalidate_existing_lease(self) -> None:
        lease = self.storage.lease("output", create=True)
        output = self.paths.output_root
        moved = output.with_name("Output-replaced")
        output.replace(moved)
        output.mkdir()
        with self.assertRaises(StorageAuthorityError):
            lease.assert_current()

        lease = self.storage.lease("output", create=True)
        old_data = self.paths.data_root.with_name("data-replaced")
        self.paths.data_root.replace(old_data)
        self.paths.data_root.mkdir()
        self.paths.output_root.mkdir()
        with self.assertRaises(StorageAuthorityError):
            lease.assert_current()

    def test_nested_reparse_and_nonregular_leaf_are_refused_without_external_write(self) -> None:
        lease = self.storage.lease("output", create=True)
        external = self.root / "external"
        external.mkdir()
        shard = self.paths.output_root / ".hub-v8"
        try:
            shard.symlink_to(external, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Directory symlinks are unavailable on this test host.")
        with self.assertRaises(StorageAuthorityError):
            self.storage.resolve_relative(lease, ".hub-v8/objects/aa/object.bin", require_exists=False)
        self.assertEqual(list(external.iterdir()), [])

        shard.unlink()
        directory_leaf = self.paths.output_root / "directory.bin"
        directory_leaf.mkdir()
        with self.assertRaises(StorageAuthorityError):
            self.storage.file_identity(lease, "directory.bin")

    def test_concurrent_same_name_producers_publish_distinct_detached_objects(self) -> None:
        results: list[dict[str, object]] = []
        failures: list[BaseException] = []
        lock = threading.Lock()

        def publish(char: str) -> None:
            try:
                job_id = _job(char)
                reservation = self.authority.begin_reservation(job_id)
                producer = self.paths.output_root / f"same-name-{char}.bin"
                producer.write_bytes(f"payload-{char}".encode("ascii"))
                published = self.authority.publish_owned_candidates(
                    reservation_id=reservation,
                    job_id=job_id,
                    candidates=[producer],
                    provenance=_provenance(job_id),
                    names=["same-name.bin"],
                )
                if not isinstance(published, list) or len(published) != 1:
                    raise AssertionError("publication failed")
                with lock:
                    results.append(published[0])
            except BaseException as exc:  # pragma: no cover - asserted after join
                with lock:
                    failures.append(exc)

        workers = [threading.Thread(target=publish, args=(char,)) for char in ("a", "b")]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(5)
        self.assertEqual(failures, [])
        self.assertEqual(len(results), 2)
        self.assertEqual({item["name"] for item in results}, {"same-name.bin"})
        self.assertEqual(len({item["id"] for item in results}), 2)
        for item in results:
            resolved = self.authority.resolve(str(item["id"]))
            self.assertIsNotNone(resolved)
            assert resolved is not None
            self.assertNotIn("same-name-", resolved.name)

    def test_abort_reservation_never_deletes_unowned_producer(self) -> None:
        bridge = V8OutputBridge(self.authority)
        job_id = _job("f")
        self.assertIsNotNone(bridge.reserve(job_id))
        producer = self.paths.output_root / "foreign-unowned.bin"
        producer.write_bytes(b"foreign bytes")
        self.assertTrue(bridge.abort_job(job_id))
        self.assertEqual(producer.read_bytes(), b"foreign bytes")
        self.assertEqual(bridge.list_public(), [])


if __name__ == "__main__":
    unittest.main()
