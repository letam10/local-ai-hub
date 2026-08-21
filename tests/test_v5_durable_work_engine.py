from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
import unittest
from collections import namedtuple
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.services import artifact_store
from src.services.api.jobs import DurableJobStore, DurableStoreHealthError
from src.services.api.api_server import HubHandler
from src.services.job_manager import DurableWorkEngine, JobContractError, ServerOwnedAdapterRegistry


ARTIFACT_ID = "artifact_" + "a" * 32


class ShortStream:
    def __init__(self, body: bytes, *, stop_after: int | None = None) -> None:
        self.body = body
        self.stop_after = stop_after
        self.offset = 0

    def read(self, size: int) -> bytes:
        if self.stop_after is not None and self.offset >= self.stop_after:
            return b""
        limit = min(len(self.body), self.offset + size)
        if self.stop_after is not None:
            limit = min(limit, self.stop_after)
        result = self.body[self.offset : limit]
        self.offset += len(result)
        return result


class ManualTimer:
    """Controllable coalescing timer that never creates a background thread."""

    def __init__(self, delay: float, callback: object) -> None:
        self.delay = delay
        self.callback = callback
        self.daemon = False
        self.started = False
        self.cancelled = False

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True


class DurableWorkEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.registry = ServerOwnedAdapterRegistry()
        self.registry.register("unit.adapter", lambda _descriptor, _context: {"status": "completed", "summary": {"count": 1, "artifact_ids": [ARTIFACT_ID], "result_type": "unit"}})
        self.store = DurableJobStore(self.root / "state" / "durable-jobs.json", history_limit=4)
        self.engine = DurableWorkEngine(self.store, self.registry, max_concurrent=2, gpu_slots=1)
        self.addCleanup(self._close)

    def _close(self) -> None:
        self.engine.close()
        self.temp.cleanup()

    @staticmethod
    def spec(*, adapter_id: str = "unit.adapter", reconstructable: bool = True, gpu_slots: int = 0, exclusive_group: str | None = None) -> dict:
        return {
            "schema_version": "job-spec.v1",
            "tool": "unit.work",
            "descriptor": {
                "schema_version": "execution-descriptor.v1",
                "adapter_id": adapter_id,
                "operation": "run",
                "arguments": {"artifact_id": ARTIFACT_ID, "mode": "unit"},
                "reconstructable": reconstructable,
                "resources": {
                    "cpu_slots": 1,
                    "gpu_slots": gpu_slots,
                    "ram_mb": 64,
                    "disk_mb": 8,
                    "exclusive_group": exclusive_group,
                },
            },
        }

    def test_valid_descriptor_is_deterministic_detached_and_static(self) -> None:
        first = self.engine.submit(self.spec())
        reordered = self.spec()
        reordered["descriptor"]["arguments"] = {"mode": "unit", "artifact_id": ARTIFACT_ID}
        second = self.engine.submit(reordered)

        self.assertEqual(first["job_spec_fingerprint"], second["job_spec_fingerprint"])
        self.assertEqual(first["execution"], "not_run")
        self.assertTrue(first["dry_run"])
        self.assertEqual(first["capability_status"], "partial")
        self.assertNotIn("job_spec", first)
        self.assertNotIn("arguments", str(first))
        first["descriptor_summary"]["resources"]["ram_mb"] = 9999
        current = self.engine.get(first["id"])
        self.assertEqual(current["descriptor_summary"]["resources"]["ram_mb"], 64)

    def test_descriptor_rejects_unsafe_fields_without_reflecting_them(self) -> None:
        for field, value in (
            ("path", r"C:\\private\\input.mp4"),
            ("outputPath", r"C:\\private\\output.mp4"),
            ("command", "cmd.exe /c whoami"),
            ("runner", lambda: None),
            ("token", "sk-not-exposed"),
        ):
            with self.subTest(field=field):
                candidate = self.spec()
                candidate["descriptor"]["arguments"][field] = value
                with self.assertRaises(JobContractError) as caught:
                    self.engine.submit(candidate)
                self.assertNotIn("private", str(caught.exception))
                self.assertNotIn("sk-not-exposed", str(caught.exception))

        adapter_mapping = self.spec()
        adapter_mapping["descriptor"]["adapter"] = {"loader": "client"}
        with self.assertRaises(JobContractError) as caught:
            self.engine.submit(adapter_mapping)
        self.assertEqual(caught.exception.code, "INVALID_EXECUTION_DESCRIPTOR")

    def test_unknown_adapter_gpu_policy_and_bounds_fail_closed(self) -> None:
        with self.assertRaises(JobContractError) as caught:
            self.engine.submit(self.spec(adapter_id="unknown.adapter"))
        self.assertEqual(caught.exception.code, "ADAPTER_UNAVAILABLE")

        no_gpu = DurableWorkEngine(DurableJobStore(self.root / "no-gpu.json"), self.registry, gpu_slots=0)
        self.addCleanup(no_gpu.close)
        unavailable = no_gpu.submit(self.spec(gpu_slots=1))
        self.assertEqual(unavailable["status"], "unavailable")
        self.assertEqual(unavailable["reason_code"], "GPU_SLOT_UNAVAILABLE")
        self.assertEqual(unavailable["execution"], "not_run")

        too_many = self.spec()
        too_many["descriptor"]["resources"]["cpu_slots"] = True
        with self.assertRaises(JobContractError) as caught:
            self.engine.submit(too_many)
        self.assertEqual(caught.exception.code, "INVALID_RESOURCE_REQUEST")

    def test_restart_reconciliation_and_retry_only_when_reconstructable(self) -> None:
        initial_store = DurableJobStore(self.root / "restart" / "durable-jobs.json")
        initial = DurableWorkEngine(initial_store, self.registry)
        pending = initial.submit(self.spec())
        initial_store.update(pending["id"], {"status": "running", "state_history": ["queued", "starting", "running"]})
        initial_store.flush()
        self.assertTrue(initial.close())

        restarted_store = DurableJobStore(initial_store.path)
        restarted = DurableWorkEngine(restarted_store, self.registry)
        self.addCleanup(restarted.close)
        report = restarted.reconcile_startup()
        recovered = restarted.get(pending["id"])
        self.assertEqual(report["interrupted"], 1)
        self.assertEqual(recovered["status"], "interrupted")
        self.assertTrue(recovered["retry_available"])
        retry = restarted.retry(pending["id"])
        self.assertEqual(retry["retry_of"], pending["id"])
        self.assertEqual(retry["attempt"], 2)

        self.registry.register("unit.no-retry", lambda _descriptor, _context: {"status": "failed"})
        non_reconstructable = restarted.submit(self.spec(adapter_id="unit.no-retry", reconstructable=False))
        failed = restarted.run(non_reconstructable["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertFalse(failed["retry_available"])
        with self.assertRaises(JobContractError) as caught:
            restarted.retry(non_reconstructable["id"])
        self.assertEqual(caught.exception.code, "JOB_NOT_RECONSTRUCTABLE")

    def test_corrupt_store_fails_closed_without_overwriting_original_bytes(self) -> None:
        for name, raw, expected_code in (
            ("malformed", b'{"jobv5_bad":', "DURABLE_STORE_UNREADABLE"),
            ("non-object", b"[]", "DURABLE_STORE_INVALID_ROOT"),
        ):
            with self.subTest(name=name):
                path = self.root / name / "durable-jobs.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
                with self.assertRaises(DurableStoreHealthError) as caught:
                    DurableJobStore(path)
                self.assertEqual(path.read_bytes(), raw)
                self.assertEqual(caught.exception.code, expected_code)
                self.assertEqual(caught.exception.public()["status"], "unavailable")
                self.assertTrue(caught.exception.public()["action"])
                self.assertNotIn(str(path), str(caught.exception))
                self.assertNotIn(raw.decode("utf-8"), str(caught.exception))

        path = self.root / "changed" / "durable-jobs.json"
        store = DurableJobStore(path)
        store.put({"id": "healthy", "status": "queued"})
        corrupt_bytes = b'{"jobv5_bad":'
        path.write_bytes(corrupt_bytes)
        for mutation in (
            lambda: store.put({"id": "second", "status": "queued"}),
            lambda: store.update("healthy", {"status": "running"}),
            store.flush,
        ):
            with self.assertRaises(DurableStoreHealthError) as caught:
                mutation()
            self.assertEqual(caught.exception.code, "DURABLE_STORE_UNREADABLE")
            self.assertEqual(path.read_bytes(), corrupt_bytes)

    def test_coalesced_progress_failure_discards_pending_state_without_timer_exception(self) -> None:
        path = self.root / "coalesced" / "durable-jobs.json"
        clock = [0.0]
        timers: list[ManualTimer] = []

        def timer_factory(delay: float, callback: object) -> ManualTimer:
            timer = ManualTimer(delay, callback)
            timers.append(timer)
            return timer

        store = DurableJobStore(
            path,
            clock=lambda: clock[0],
            debounce_seconds=0.5,
            timer_factory=timer_factory,  # type: ignore[arg-type]
        )
        store.put({"id": "healthy", "status": "queued", "progress": 0})
        clock[0] = 0.1
        staged = store.update("healthy", {"progress": 50}, progress_only=True)
        self.assertEqual(staged["progress"], 0)
        self.assertEqual(store.get("healthy")["progress"], 0)
        self.assertEqual(store._pending_records["healthy"]["progress"], 50)
        self.assertEqual(len(timers), 1)
        self.assertTrue(timers[0].started)

        corrupt_bytes = b'{"jobv5_bad":'
        path.write_bytes(corrupt_bytes)
        try:
            timers[0].callback()  # type: ignore[operator]
        except Exception as exc:  # pragma: no cover - regression guards daemon path
            self.fail(f"coalesced timer leaked {type(exc).__name__}")

        self.assertEqual(path.read_bytes(), corrupt_bytes)
        self.assertEqual(store._records["healthy"]["progress"], 0)
        self.assertIsNone(store._pending_records)
        health = store.health()
        self.assertEqual(health["status"], "unavailable")
        self.assertEqual(health["code"], "DURABLE_STORE_UNREADABLE")
        self.assertNotIn(str(path), str(health))
        with self.assertRaises(DurableStoreHealthError):
            store.get("healthy")
        projected = DurableWorkEngine(store, self.registry).get("healthy")
        self.assertEqual(projected["status"], "unavailable")
        self.assertEqual(projected["execution"], "not_run")
        self.assertEqual(projected["reason_code"], "DURABLE_STORE_UNREADABLE")

    def test_immediate_persistence_failure_discards_uncommitted_record(self) -> None:
        path = self.root / "immediate" / "durable-jobs.json"
        store = DurableJobStore(path)
        store.put({"id": "healthy", "status": "queued", "progress": 0})
        before = path.read_bytes()
        with patch.object(Path, "replace", side_effect=OSError("blocked")):
            with self.assertRaises(DurableStoreHealthError) as caught:
                store.put({"id": "new-record", "status": "queued", "progress": 0})
        self.assertEqual(caught.exception.code, "DURABLE_STORE_PERSISTENCE_FAILED")
        self.assertEqual(path.read_bytes(), before)
        self.assertNotIn("new-record", store._records)
        self.assertIsNone(store._pending_records)
        self.assertEqual(store.health()["status"], "unavailable")
        self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])

    def test_invalid_persisted_descriptor_becomes_unavailable(self) -> None:
        record = self.engine.submit(self.spec())
        persisted = self.store.get(record["id"])
        persisted["job_spec"]["descriptor"]["arguments"]["shell"] = "cmd.exe"
        self.store.update(record["id"], persisted)
        report = self.engine.reconcile_startup()
        current = self.engine.get(record["id"])
        self.assertEqual(report["unavailable"], 1)
        self.assertEqual(current["status"], "unavailable")
        self.assertEqual(current["reason_code"], "INVALID_PERSISTED_DESCRIPTOR")

    def test_queued_and_running_cancel_do_not_regress_terminal_state(self) -> None:
        queued = self.engine.submit(self.spec())
        accepted, cancelled = self.engine.cancel(queued["id"])
        self.assertTrue(accepted)
        self.assertEqual(cancelled["status"], "interrupted")
        self.assertEqual(self.engine.run(queued["id"])["status"], "interrupted")

        entered = threading.Event()
        release = threading.Event()

        def blocking(_descriptor: object, _context: object) -> dict:
            entered.set()
            release.wait(1)
            return {"status": "completed"}

        self.registry.register("unit.blocking", blocking)
        running = self.engine.submit(self.spec(adapter_id="unit.blocking"), start=True)
        self.assertTrue(entered.wait(1))
        accepted, cancelling = self.engine.cancel(running["id"])
        self.assertTrue(accepted)
        self.assertIn(cancelling["status"], {"cancelling", "interrupted"})
        release.set()
        self.assertTrue(self.engine.wait_for_idle(1))
        self.assertEqual(self.engine.get(running["id"])["status"], "interrupted")
        self.assertFalse(self.engine.cancel(running["id"])[0])

    def test_concurrency_and_exclusive_gpu_policy_are_planning_only(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        def blocking(_descriptor: object, _context: object) -> dict:
            entered.set()
            release.wait(1)
            return {"status": "completed"}

        registry = ServerOwnedAdapterRegistry()
        registry.register("unit.blocking", blocking)
        engine = DurableWorkEngine(DurableJobStore(self.root / "concurrency.json"), registry, max_concurrent=1, gpu_slots=1)
        self.addCleanup(engine.close)
        first = engine.submit(self.spec(adapter_id="unit.blocking", gpu_slots=1, exclusive_group="gpu.shared"), start=True)
        self.assertTrue(entered.wait(1))
        second = engine.submit(self.spec(adapter_id="unit.blocking", gpu_slots=1, exclusive_group="gpu.shared"))
        queued = engine.run(second["id"])
        self.assertEqual(queued["status"], "queued")
        self.assertEqual(queued["reason_code"], "CONCURRENCY_LIMIT")
        self.assertTrue(queued["dry_run"])
        self.assertEqual(queued["execution"], "not_run")
        release.set()
        self.assertTrue(engine.wait_for_idle(1))
        self.assertEqual(engine.get(first["id"])["status"], "completed")

    def test_atomic_output_provenance_public_redaction_and_range_contract(self) -> None:
        output_root = self.root / "Output"
        upload_root = self.root / "Temp" / "uploads"
        archive_root = self.root / "Archive"
        index_path = self.root / "Config" / "artifacts.json"
        record = self.engine.submit(self.spec())
        with (
            patch.object(artifact_store, "OUTPUT_ROOT", output_root),
            patch.object(artifact_store, "UPLOAD_ROOT", upload_root),
            patch.object(artifact_store, "ARCHIVE_ROOT", archive_root),
            patch.object(artifact_store, "INDEX_PATH", index_path),
        ):
            self.assertIsNone(self.engine.persist_output(record["id"], b"durable output", name="result.bin"))
            self.assertEqual(list(output_root.glob("*.part")), [])
            self.assertEqual(HubHandler._range_bounds("bytes=2-6", len(b"durable output")), (2, 6))
            self.assertEqual(HubHandler._range_bounds("bytes=999-", len(b"durable output")), False)

    def test_upload_partial_orphan_cleanup_and_disk_guard_stay_in_test_root(self) -> None:
        upload_root = self.root / "Temp" / "uploads"
        output_root = self.root / "Output"
        index_path = self.root / "Config" / "artifacts.json"
        DiskUsage = namedtuple("DiskUsage", "total used free")
        with (
            patch.object(artifact_store, "UPLOAD_ROOT", upload_root),
            patch.object(artifact_store, "OUTPUT_ROOT", output_root),
            patch.object(artifact_store, "INDEX_PATH", index_path),
        ):
            with self.assertRaises(artifact_store.UploadError):
                artifact_store.stage_upload_stream("short.bin", ShortStream(b"abcdef", stop_after=3), 6, max_bytes=16, disk_safety_bytes=0, chunk_bytes=1024)
            self.assertFalse(upload_root.exists() and any(upload_root.iterdir()))
            upload_root.mkdir(parents=True, exist_ok=True)
            owned = upload_root / "hub-upload-old.part"
            user_file = upload_root / "user-upload.part"
            owned.write_bytes(b"orphan")
            user_file.write_bytes(b"user")
            os.utime(owned, (1, 1))
            cleanup = artifact_store.cleanup_owned_upload_orphans(expiry_seconds=1, now=10)
            self.assertEqual(cleanup["removed"], 1)
            self.assertFalse(owned.exists())
            self.assertTrue(user_file.exists())
            with patch.object(artifact_store.shutil, "disk_usage", return_value=DiskUsage(100, 100, 0)):
                # Durable adapters have no reservation-aware producer yet;
                # the no-scope refusal precedes any generic disk writer.
                self.assertIsNone(self.engine.persist_output(self.engine.submit(self.spec())["id"], b"disk"))

    def test_bounded_history_and_no_runtime_side_effects(self) -> None:
        compact = DurableJobStore(self.root / "compact.json", history_limit=2)
        self.addCleanup(compact.close)
        for index in range(3):
            compact.put({"id": f"history-{index}", "status": "completed", "created_at": f"2026-08-11T00:00:0{index}+00:00"})
        self.assertEqual([record["id"] for record in compact.records()], ["history-1", "history-2"])

        record = self.engine.submit(self.spec())
        with (
            patch.object(subprocess, "Popen", side_effect=AssertionError("subprocess is forbidden")) as process,
            patch.object(socket, "socket", side_effect=AssertionError("socket is forbidden")) as network,
        ):
            finished = self.engine.run(record["id"])
        process.assert_not_called()
        network.assert_not_called()
        self.assertEqual(finished["status"], "completed")
        self.assertEqual(finished["execution"], "not_run")
        self.assertEqual(finished["capability_status"], "partial")


if __name__ == "__main__":
    unittest.main()
