"""V8 retry contract: terminal history is immutable and retries are new jobs."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src.services.api.jobs import DurableJobStore
from src.services.job_manager.contracts import JobContractError
from src.services.job_manager.durable import DurableWorkEngine, ServerOwnedAdapterRegistry


ROOT = Path(__file__).resolve().parents[1]


def retry_spec(*, reconstructable: bool = True) -> dict[str, object]:
    return {
        "schema_version": "job-spec.v1",
        "tool": "unit_retryable",
        "descriptor": {
            "schema_version": "execution-descriptor.v1",
            "adapter_id": "unit.retryable",
            "operation": "run",
            "arguments": {"quality": "preview"},
            "reconstructable": reconstructable,
            "resources": {"cpu_slots": 1, "gpu_slots": 0, "ram_mb": 0, "disk_mb": 0, "exclusive_group": None},
        },
    }


class NewJobRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ServerOwnedAdapterRegistry()
        self.registry.register("unit.retryable", lambda _descriptor, _context: {"status": "failed"})

    def _engine(self, root: Path) -> tuple[DurableJobStore, DurableWorkEngine]:
        store = DurableJobStore(root / "durable-jobs.json")
        engine = DurableWorkEngine(store, self.registry, gpu_slots=0)
        return store, engine

    def test_eligible_retry_creates_new_record_and_preserves_failed_history(self) -> None:
        with TemporaryDirectory(dir=ROOT.parent) as temporary:
            store, engine = self._engine(Path(temporary))
            try:
                original = engine.submit(retry_spec())
                old_id = original["id"]
                artifact_id = "artifact_" + "a" * 32
                store.update(old_id, {
                    "status": "failed",
                    "finished_at": "2026-08-26T00:00:00+00:00",
                    "state_history": ["queued", "failed"],
                    "reason_code": "ADAPTER_FAILED",
                    "action_code": "RETRY_IF_RECONSTRUCTABLE",
                    "artifacts": [artifact_id],
                })
                store.flush()
                before = store.get(old_id)
                retried = engine.retry(old_id)
                after = store.get(old_id)
                created = store.get(retried["id"])

                self.assertNotEqual(retried["id"], old_id)
                self.assertEqual(retried["retry_of"], old_id)
                self.assertEqual(retried["attempt"], 2)
                self.assertEqual(retried["status"], "queued")
                self.assertEqual(after["status"], "failed")
                self.assertEqual(after["artifacts"], [artifact_id])
                self.assertEqual(after, before)
                self.assertEqual(created["retry_of"], old_id)
                self.assertEqual(created["job_spec"], before["job_spec"])
            finally:
                engine.close()

    def test_active_job_is_rejected_without_creating_record(self) -> None:
        with TemporaryDirectory(dir=ROOT.parent) as temporary:
            store, engine = self._engine(Path(temporary))
            try:
                original = engine.submit(retry_spec())
                with self.assertRaises(JobContractError) as caught:
                    engine.retry(original["id"])
                self.assertEqual(caught.exception.code, "JOB_NOT_RETRYABLE")
                self.assertEqual(len(store.records()), 1)
            finally:
                engine.close()

    def test_non_reconstructable_or_missing_spec_is_ineligible(self) -> None:
        with TemporaryDirectory(dir=ROOT.parent) as temporary:
            store, engine = self._engine(Path(temporary))
            try:
                original = engine.submit(retry_spec(reconstructable=False))
                store.update(original["id"], {"status": "failed", "state_history": ["queued", "failed"]})
                store.flush()
                with self.assertRaises(JobContractError) as caught:
                    engine.retry(original["id"])
                self.assertEqual(caught.exception.code, "JOB_NOT_RECONSTRUCTABLE")
                self.assertEqual(len(store.records()), 1)
            finally:
                engine.close()

    def test_legacy_same_session_resume_also_records_retry_provenance(self) -> None:
        from src.services.job_manager import manager as manager_module

        manager = manager_module.HubJobManager()
        runner = lambda _payload, _context: {"status": "unavailable"}
        old_id = "job_20260826_000000_ab12cd34"
        manager._runners[old_id] = runner
        manager._runner_specs[old_id] = manager_module.RunnerSpec(runner, "cpu", False, 1.0)
        record = {
            "id": old_id,
            "tool": "probe_media",
            "status": "failed",
            "resume_data": {"asset_id": "asset_local"},
            "device": "cpu",
            "attempt": 1,
        }
        with patch.object(manager_module, "get_job_internal", return_value=record), patch.object(manager, "submit", return_value={"id": "job_retry"}) as submit:
            ok, result = manager.resume(old_id)
        self.assertTrue(ok)
        self.assertEqual(result["id"], "job_retry")
        self.assertEqual(submit.call_args.kwargs["retry_of"], old_id)
        self.assertEqual(submit.call_args.kwargs["attempt"], 2)


if __name__ == "__main__":
    unittest.main()
