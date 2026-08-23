"""Process-boundary V8 output crash recovery acceptance.

Child helpers deliberately terminate through ``os._exit`` after selected
transaction phases.  The parent opens the same controlled V8 root only after
the child is gone and verifies reconciliation/publication semantics.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.platform.paths import HubPaths
from src.services.output_authority_v8 import ProductionOutputAuthority
from src.services.v8_output_bridge import V8OutputBridge
from src.services.v8_transaction_extensions import V8ProductionTransactionStore


def _job(char: str) -> str:
    return "jobv5_" + char * 32


def _provenance(job_id: str) -> dict[str, object]:
    return {
        "job_id": job_id,
        "job_spec_fingerprint": hashlib.sha256(job_id.encode("ascii")).hexdigest(),
        "adapter_id": "test.crash.v8",
        "attempt": 1,
        "status": "completed",
    }


def _child(root_text: str, phase: str) -> int:
    root = Path(root_text)
    paths = HubPaths(app_root=root / "app", data_root=root / "data")
    store = V8ProductionTransactionStore(paths.config_root / "v8_control.sqlite3")
    authority = ProductionOutputAuthority(paths=paths, store=store)
    job_id = _job({"reserve": "a", "producer": "b", "staged": "c", "authorized": "d", "committed": "e", "copy": "f"}[phase])
    reservation = authority.begin_reservation(job_id)
    marker = root / f"{phase}.json"
    marker.write_text(json.dumps({"job_id": job_id, "reservation": reservation}), encoding="ascii")
    if phase == "reserve":
        os._exit(17)

    producer = paths.output_root / f"producer-{phase}.bin"
    producer.parent.mkdir(parents=True, exist_ok=True)
    producer.write_bytes(f"producer-{phase}".encode("ascii"))
    if phase == "producer":
        os._exit(17)

    if phase == "copy":
        original_copy = authority._copy_managed_object

        def crash_after_copy(*args: object, **kwargs: object):
            copied = original_copy(*args, **kwargs)
            marker.write_text(
                json.dumps(
                    {
                        "job_id": job_id,
                        "reservation": reservation,
                        "object_key": copied[1],
                    }
                ),
                encoding="ascii",
            )
            os._exit(17)

        authority._copy_managed_object = crash_after_copy  # type: ignore[method-assign]
        authority.publish_owned_candidates(
            reservation_id=reservation,
            job_id=job_id,
            candidates=[producer],
            provenance=_provenance(job_id),
        )
        return 5

    lease = authority.storage.lease("output", create=True)
    transaction = store.create_output_transaction(reservation, job_id)
    object_marker = {"staged": "c", "authorized": "d", "committed": "e"}[phase]
    object_id = "obj_" + object_marker * 32
    object_key = f".hub-v8/objects/{object_marker * 2}/{object_id}"
    managed = authority.storage.resolve_relative(lease, object_key, require_exists=False)
    managed.parent.mkdir(parents=True, exist_ok=True)
    managed.write_bytes(producer.read_bytes())
    identity = authority.storage.file_identity(lease, object_key)
    artifact_id = store.stage_artifact(
        transaction_id=transaction,
        reservation_id=reservation,
        job_id=job_id,
        object_id=object_id,
        object_key=object_key,
        name="result.bin",
        media_type="application/octet-stream",
        size_bytes=managed.stat().st_size,
        sha256=hashlib.sha256(managed.read_bytes()).hexdigest(),
        file_device=identity.device,
        file_inode=identity.inode,
        file_mtime_ns=identity.mtime_ns,
        provenance=_provenance(job_id),
    )
    marker.write_text(json.dumps({"job_id": job_id, "reservation": reservation, "transaction": transaction, "artifact": artifact_id, "object_key": object_key}), encoding="ascii")
    if phase == "staged":
        os._exit(17)
    if not store.authorize_output_transaction(transaction, reservation, job_id):
        return 2
    if phase == "authorized":
        os._exit(17)
    if phase == "committed":
        if store.commit_output_transaction(transaction, reservation, job_id) is None:
            return 3
        os._exit(17)
    return 4


class V8CrashRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.paths = HubPaths(app_root=self.root / "app", data_root=self.root / "data")
        self.paths.app_root.mkdir()
        self.paths.data_root.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _run_child(self, phase: str) -> dict[str, str]:
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--child", str(self.root), phase],
            cwd=ROOT,
            check=False,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 17, result.stderr.decode("utf-8", errors="replace"))
        return json.loads((self.root / f"{phase}.json").read_text(encoding="ascii"))

    def _bridge(self) -> V8OutputBridge:
        store = V8ProductionTransactionStore(self.paths.config_root / "v8_control.sqlite3")
        return V8OutputBridge(ProductionOutputAuthority(paths=self.paths, store=store))

    def test_reserve_and_producer_crashes_abort_reservation_without_deleting_producer(self) -> None:
        reserve = self._run_child("reserve")
        bridge = self._bridge()
        recovered = bridge.reconcile(active_job_ids=set())
        self.assertEqual(recovered["aborted_reservations"], 1)
        self.assertEqual(bridge.list_public(), [])

        producer = self._run_child("producer")
        producer_path = self.paths.output_root / "producer-producer.bin"
        self.assertEqual(producer_path.read_bytes(), b"producer-producer")
        bridge = self._bridge()
        recovered = bridge.reconcile(active_job_ids=set())
        self.assertEqual(recovered["aborted_reservations"], 1)
        self.assertEqual(producer_path.read_bytes(), b"producer-producer")
        self.assertEqual(bridge.list_public(), [])
        self.assertNotEqual(reserve["job_id"], producer["job_id"])

    def test_staged_or_authorized_crash_reconciles_private_object_and_rows(self) -> None:
        for phase in ("staged", "authorized"):
            with self.subTest(phase=phase):
                marker = self._run_child(phase)
                bridge = self._bridge()
                recovered = bridge.reconcile(active_job_ids=set())
                self.assertGreaterEqual(recovered["aborted_transactions"], 1)
                self.assertGreaterEqual(recovered["dropped_rows"], 1)
                self.assertEqual(bridge.list_public(), [])
                lease = bridge.authority.storage.lease("output", create=True)
                object_path = bridge.authority.storage.resolve_relative(lease, marker["object_key"], require_exists=False)
                self.assertFalse(object_path.exists())

    def test_crash_after_atomic_public_commit_keeps_one_public_artifact(self) -> None:
        marker = self._run_child("committed")
        bridge = self._bridge()
        recovered = bridge.reconcile(active_job_ids=set())
        self.assertEqual(recovered["aborted_transactions"], 0)
        public = bridge.list_public()
        self.assertEqual([item["id"] for item in public], [marker["artifact"]])
        self.assertIsNotNone(bridge.resolve(marker["artifact"]))

    def test_copy_phase_crash_preserves_untracked_object_for_manual_review(self) -> None:
        marker = self._run_child("copy")
        bridge = self._bridge()
        recovered = bridge.reconcile(active_job_ids=set())
        self.assertEqual(recovered["aborted_reservations"], 1)
        self.assertGreaterEqual(recovered["manual_review"], 1)
        self.assertEqual(bridge.list_public(), [])
        lease = bridge.authority.storage.lease("output", create=True)
        orphan = bridge.authority.storage.resolve_relative(lease, marker["object_key"], require_exists=True)
        self.assertTrue(orphan.is_file())


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--child":
        raise SystemExit(_child(sys.argv[2], sys.argv[3]))
    unittest.main()
