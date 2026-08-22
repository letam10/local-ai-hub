"""Controlled Windows V8 storage-authority adversarial acceptance."""

from __future__ import annotations

import errno
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

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

    @unittest.skipUnless(os.name == "nt", "Windows sharing semantics are required")
    def test_exclusive_windows_producer_handle_refuses_publication_without_delete(self) -> None:
        """An owned share-denied fixture handle must not become a partial artifact."""

        import ctypes
        from ctypes import wintypes

        job_id = _job("d")
        reservation = self.authority.begin_reservation(job_id)
        producer = self.paths.output_root / "share-denied.bin"
        payload = b"task-owned sharing fixture"
        producer.write_bytes(payload)

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.CreateFileW(
            str(producer),
            0x80000000,  # GENERIC_READ
            0,  # deny all sharing for this task-owned fixture only
            None,
            3,  # OPEN_EXISTING
            0x80,  # FILE_ATTRIBUTE_NORMAL
            None,
        )
        invalid_handle = ctypes.c_void_p(-1).value
        self.assertNotEqual(ctypes.c_void_p(handle).value, invalid_handle)
        try:
            published = self.authority.publish_owned_candidates(
                reservation_id=reservation,
                job_id=job_id,
                candidates=[producer],
                provenance=_provenance(job_id),
            )
            self.assertIsNone(published)
            self.assertEqual(self.authority.list_public(), [])
        finally:
            self.assertTrue(kernel32.CloseHandle(handle))
        self.assertEqual(producer.read_bytes(), payload)

    @unittest.skipUnless(os.name == "nt", "Windows sharing semantics are required")
    def test_task_owned_child_process_share_denial_refuses_publication(self) -> None:
        """A separate producer process holding a zero-share handle remains authoritative."""

        import json

        job_id = _job("c")
        reservation = self.authority.begin_reservation(job_id)
        producer = self.paths.output_root / "child-share-denied.bin"
        signal = self.root / "child-share-denied.ready"
        payload = b"child-process producer bytes"
        producer.write_bytes(payload)
        child_code = (
            "import ctypes, json, pathlib, sys, time; "
            "from ctypes import wintypes; "
            "k=ctypes.windll.kernel32; "
            "k.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]; "
            "k.CreateFileW.restype=wintypes.HANDLE; "
            "h=k.CreateFileW(sys.argv[1],0x80000000,0,None,3,0x80,None); "
            "assert ctypes.c_void_p(h).value != ctypes.c_void_p(-1).value; "
            "pathlib.Path(sys.argv[2]).write_text(json.dumps({'ready': True}), encoding='ascii'); "
            "time.sleep(30)"
        )
        child = subprocess.Popen(
            [sys.executable, "-c", child_code, str(producer), str(signal)],
            cwd=str(Path(__file__).resolve().parents[1]),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not signal.exists():
                time.sleep(0.05)
            self.assertTrue(signal.exists(), "child did not publish readiness")
            published = self.authority.publish_owned_candidates(
                reservation_id=reservation,
                job_id=job_id,
                candidates=[producer],
                provenance=_provenance(job_id),
            )
            self.assertIsNone(published)
            self.assertEqual(self.authority.list_public(), [])
        finally:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=5)
            signal.unlink(missing_ok=True)
        self.assertEqual(producer.read_bytes(), payload)

    def test_injected_no_space_during_managed_copy_preserves_producer_and_hides_output(self) -> None:
        """A bounded ENOSPC injection must not publish or delete task input."""

        job_id = _job("e")
        reservation = self.authority.begin_reservation(job_id)
        producer = self.paths.output_root / "no-space.bin"
        payload = b"task-owned no-space fixture"
        producer.write_bytes(payload)
        original_open = Path.open

        def fail_managed_create(path: Path, mode: str = "r", buffering: int = -1, encoding: str | None = None, errors: str | None = None, newline: str | None = None):
            if "x" in mode and path.name.startswith("obj_"):
                raise OSError(errno.ENOSPC, "task-owned injected no space")
            return original_open(path, mode, buffering, encoding, errors, newline)

        with patch.object(Path, "open", new=fail_managed_create):
            published = self.authority.publish_owned_candidates(
                reservation_id=reservation,
                job_id=job_id,
                candidates=[producer],
                provenance=_provenance(job_id),
            )
        self.assertIsNone(published)
        self.assertEqual(self.authority.list_public(), [])
        self.assertEqual(producer.read_bytes(), payload)


if __name__ == "__main__":
    unittest.main()
