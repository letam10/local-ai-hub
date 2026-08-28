"""Post-V8 Phase 5 Durable Job Engine V2 tests (no worker/GPU workload)."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from types import SimpleNamespace

from src.services.api.context import ApiContext, build_default_context
from src.services.api.router import ApiRequest
from src.services.api.router_registry import build_router
from src.services.durable_job_engine_v2 import (
    DURABLE_JOB_ENGINE_V2_SCHEMA_VERSION,
    DurableJobEngineV2,
    DurableJobStoreV2,
    ExecutionOwnerRegistry,
)
from src.services.resource_scheduler import ResourceScheduler


ROOT = Path(__file__).resolve().parents[1]


def _artifact(char: str) -> str:
    return "artifact_" + char * 32


def _hardware() -> dict[str, object]:
    return {
        "cpu_slots": 8,
        "ram_mb": 16384,
        "disk_mb": 32768,
        "gpus": [{"id": "gpu-rtx4060", "vendor": "nvidia", "device_class": "discrete", "model": "RTX-4060", "vram_mb": 8192}],
        "runtime_slots": {"ffmpeg": 2, "faster-whisper": 1},
        "provider_slots": {"whisper": 1},
    }


def _request(*, owner: str = "worker:synthetic", profile: str = "ffmpeg_probe") -> dict[str, object]:
    return {
        "workflow_id": "workflow.fixture",
        "input_artifact_ids": [_artifact("a")],
        "artifact_refs": [_artifact("b")],
        "execution_owner": owner,
        "worker_id": "worker:synthetic",
        "resource_profile_id": profile,
    }


class DurableJobEngineV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store_path = Path(self.temp.name) / "config" / "durable_jobs_v2.sqlite3"
        self.scheduler = ResourceScheduler(hardware_snapshot=_hardware())
        self.owners = ExecutionOwnerRegistry({"worker:synthetic": {"dispatch": True, "pause": False, "resume": False}})
        self.engine = DurableJobEngineV2(store=DurableJobStoreV2(self.store_path), scheduler=self.scheduler, owners=self.owners)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_read_snapshot_does_not_create_store_and_unavailable_owner_creates_no_record(self) -> None:
        self.assertFalse(self.store_path.exists())
        snapshot = self.engine.snapshot()
        self.assertEqual(snapshot["schema_version"], DURABLE_JOB_ENGINE_V2_SCHEMA_VERSION)
        self.assertEqual(snapshot["records"], [])
        self.assertFalse(self.store_path.exists())
        unavailable = DurableJobEngineV2(
            store=DurableJobStoreV2(self.store_path),
            scheduler=ResourceScheduler(hardware_snapshot=_hardware()),
            owners=ExecutionOwnerRegistry(),
        ).admit(_request())
        self.assertEqual(unavailable["code"], "execution_owner_unavailable")
        self.assertFalse(self.store_path.exists())

    def test_store_reparse_refusal_releases_unpersisted_scheduler_capacity(self) -> None:
        self.store_path.parent.mkdir(parents=True)
        with patch("src.services.durable_job_engine_v2.store.is_reparse_point", side_effect=lambda path: Path(path) == self.store_path.parent):
            result = self.engine.admit(_request())
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["code"], "durable_job_v2_store_unavailable")
        self.assertFalse(self.store_path.exists())
        self.assertEqual(self.scheduler.snapshot()["inventory"]["gpus"][0]["vram_reserved_mb"], 0)

    def test_admission_is_dispatchable_but_does_not_claim_execution(self) -> None:
        result = self.engine.admit(_request())
        self.assertEqual(result["status"], "accepted")
        job = result["job"]
        self.assertEqual(job["state"], "PREPARING")
        self.assertTrue(job["dispatchable"])
        self.assertFalse(job["actual_execution"])
        self.assertTrue(job["active"])
        self.assertEqual(job["execution"], "not_run")
        self.assertTrue(job["dry_run"])
        self.assertEqual(job["lifecycle"], "Đã được lập lịch · chờ worker")
        self.assertRegex(job["reservation_id"], r"^resv_[a-f0-9]{32}$")
        self.assertTrue(self.store_path.is_file())
        reopened = DurableJobEngineV2(store=DurableJobStoreV2(self.store_path), scheduler=self.scheduler, owners=self.owners)
        stored = reopened.get(job["job_id"])
        self.assertEqual(stored["workflow_id"], "workflow.fixture")
        self.assertEqual(stored["input_artifact_ids"], [_artifact("a")])
        self.assertNotIn(str(self.temp.name), json.dumps(stored))
        self.assertNotIn("path", json.dumps(stored).lower())

    def test_exact_worker_reservation_controls_progress_and_terminal_artifacts(self) -> None:
        admitted = self.engine.admit(_request())
        job = admitted["job"]
        job_id = job["job_id"]
        reservation = job["reservation_id"]
        wrong = self.engine.claim_running(job_id, "worker:other", reservation)
        self.assertEqual(wrong["code"], "reservation_identity_mismatch")
        running = self.engine.claim_running(job_id, "worker:synthetic", reservation)
        self.assertEqual(running["job"]["state"], "RUNNING")
        self.assertTrue(running["job"]["actual_execution"])
        self.assertEqual(running["execution"], "running")
        self.assertFalse(running["dry_run"])
        progress = self.engine.progress(job_id, "worker:synthetic", reservation, 42)
        self.assertEqual(progress["job"]["progress"], 42)
        complete = self.engine.finish(job_id, "worker:synthetic", reservation, succeeded=True, artifact_refs=[_artifact("b"), _artifact("c")])
        self.assertEqual(complete["job"]["state"], "SUCCEEDED")
        self.assertEqual(complete["job"]["artifact_refs"], [_artifact("b"), _artifact("c")])
        self.assertIsNone(complete["job"]["reservation_id"])
        self.assertEqual(complete["execution"], "completed")
        self.assertFalse(complete["dry_run"])

    def test_pause_resume_are_exposed_only_for_an_owner_that_supports_them(self) -> None:
        owners = ExecutionOwnerRegistry({"worker:synthetic": {"dispatch": True, "pause": True, "resume": True}})
        engine = DurableJobEngineV2(store=DurableJobStoreV2(self.store_path), scheduler=self.scheduler, owners=owners)
        admitted = engine.admit(_request())
        job = admitted["job"]
        reservation = job["reservation_id"]
        running = engine.claim_running(job["job_id"], "worker:synthetic", reservation)
        self.assertTrue(running["job"]["can_pause"])
        paused = engine.pause(job["job_id"], "worker:synthetic", reservation)
        self.assertEqual(paused["job"]["state"], "PAUSED")
        self.assertEqual(paused["execution"], "paused")
        self.assertTrue(paused["job"]["can_resume"])
        resumed = engine.resume(job["job_id"], "worker:synthetic", reservation)
        self.assertEqual(resumed["job"]["state"], "RUNNING")
        self.assertEqual(resumed["execution"], "running")

    def test_pause_and_resume_are_unavailable_when_owner_does_not_support_them(self) -> None:
        admitted = self.engine.admit(_request())
        job = admitted["job"]
        reservation = job["reservation_id"]
        self.engine.claim_running(job["job_id"], "worker:synthetic", reservation)
        self.assertFalse(self.engine.get(job["job_id"])["can_pause"])
        self.assertEqual(self.engine.pause(job["job_id"], "worker:synthetic", reservation)["code"], "durable_job_pause_unavailable")

    def test_reconstruct_only_retry_has_new_lineage_but_is_not_active_or_executing(self) -> None:
        admitted = self.engine.admit(_request())
        job = admitted["job"]
        reservation = job["reservation_id"]
        self.engine.claim_running(job["job_id"], "worker:synthetic", reservation)
        failed = self.engine.finish(job["job_id"], "worker:synthetic", reservation, succeeded=False)["job"]
        actual = self.engine.retry(failed["job_id"], mode="RETRY_EXECUTION")
        reconstructed = self.engine.retry(failed["job_id"], mode="RECONSTRUCT_ONLY")
        self.assertEqual(actual["code"], "actual_retry_execution_not_available")
        self.assertEqual(reconstructed["status"], "queued")
        new_job = reconstructed["job"]
        self.assertNotEqual(new_job["job_id"], failed["job_id"])
        self.assertEqual(new_job["retry_of"], failed["job_id"])
        self.assertEqual(new_job["retry_mode"], "reconstruct_only")
        self.assertFalse(new_job["actual_execution"])
        self.assertFalse(new_job["active"])
        self.assertEqual(new_job["lifecycle"], "Đã tạo · chưa thực thi")
        snapshot = self.engine.snapshot()
        self.assertEqual(snapshot["counts"]["active"], 0)
        self.assertEqual(snapshot["counts"]["reconstruct_only_pending"], 1)

    def test_restart_reconciliation_never_promotes_stale_job_to_running_and_preserves_artifacts(self) -> None:
        admitted = self.engine.admit(_request())
        job = admitted["job"]
        reservation = job["reservation_id"]
        self.engine.claim_running(job["job_id"], "worker:synthetic", reservation)
        restarted = DurableJobEngineV2(
            store=DurableJobStoreV2(self.store_path),
            scheduler=ResourceScheduler(hardware_snapshot=_hardware()),
            owners=self.owners,
        )
        result = restarted.reconcile_startup(worker_alive=lambda job_id, worker_id: False, artifact_complete=lambda refs: False)
        record = restarted.get(job["job_id"])
        self.assertEqual(result["failed"], 1)
        self.assertEqual(record["state"], "FAILED")
        self.assertEqual(record["error_code"], "worker_gone_after_restart")
        self.assertEqual(record["artifact_refs"], [_artifact("b")])
        self.assertNotEqual(record["state"], "RUNNING")

    def test_restart_without_liveness_probe_never_adopts_a_running_worker(self) -> None:
        admitted = self.engine.admit(_request())
        job = admitted["job"]
        reservation = job["reservation_id"]
        self.engine.claim_running(job["job_id"], "worker:synthetic", reservation)
        restarted = DurableJobEngineV2(
            store=DurableJobStoreV2(self.store_path),
            scheduler=ResourceScheduler(hardware_snapshot=_hardware()),
            owners=self.owners,
        )
        result = restarted.reconcile_startup()
        record = restarted.get(job["job_id"])
        self.assertEqual(result["failed"], 1)
        self.assertEqual(record["state"], "FAILED")
        self.assertEqual(record["error_code"], "worker_liveness_unavailable_after_restart")
        self.assertEqual(record["artifact_refs"], [_artifact("b")])

    def test_archive_and_selected_history_delete_never_delete_artifacts(self) -> None:
        admitted = self.engine.admit(_request())
        job = admitted["job"]
        reservation = job["reservation_id"]
        self.engine.claim_running(job["job_id"], "worker:synthetic", reservation)
        terminal = self.engine.finish(job["job_id"], "worker:synthetic", reservation, succeeded=True)["job"]
        archived = self.engine.archive(terminal["job_id"])
        self.assertTrue(archived["job"]["archived"])
        self.assertTrue(archived["artifacts_preserved"])
        deleted = self.engine.delete_history([terminal["job_id"]])
        self.assertEqual(deleted["removed_count"], 1)
        self.assertTrue(deleted["artifacts_preserved"])
        self.assertIsNone(self.engine.get(terminal["job_id"]))

    def test_selected_history_delete_is_atomic_when_any_record_is_missing(self) -> None:
        admitted = self.engine.admit(_request())
        job = admitted["job"]
        reservation = job["reservation_id"]
        self.engine.claim_running(job["job_id"], "worker:synthetic", reservation)
        terminal = self.engine.finish(job["job_id"], "worker:synthetic", reservation, succeeded=True)["job"]
        missing = "jobv2_" + "f" * 32
        deleted = self.engine.delete_history([terminal["job_id"], missing])
        self.assertEqual(deleted["status"], "not_found")
        self.assertIsNotNone(self.engine.get(terminal["job_id"]))

    def test_filter_search_and_status_grouping_are_bounded(self) -> None:
        first = self.engine.admit(_request(profile="whisper_gpu_2gb"))
        second = self.engine.admit(_request(profile="vision_gpu_2gb"))
        snapshot = self.engine.snapshot(query="workflow", archived=False)
        waiting = self.engine.snapshot(status="WAITING_RESOURCE")
        self.assertEqual(snapshot["counts"]["active"], 2)
        self.assertLessEqual(len(snapshot["records"]), 512)
        self.assertEqual([item["state"] for item in waiting["records"]], ["WAITING_RESOURCE"])
        self.assertIn(first["job"]["job_id"], {item["job_id"] for item in snapshot["records"]})
        self.assertIn(second["job"]["job_id"], {item["job_id"] for item in snapshot["records"]})


class DurableJobEngineV2ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store_path = Path(self.temp.name) / "config" / "durable_jobs_v2.sqlite3"
        self.scheduler = ResourceScheduler(hardware_snapshot=_hardware())
        self.engine = DurableJobEngineV2(
            store=DurableJobStoreV2(self.store_path),
            scheduler=self.scheduler,
            owners=ExecutionOwnerRegistry({"worker:synthetic": {"dispatch": True, "pause": False, "resume": False}}),
        )
        self.context = ApiContext({
            "durable_job_v2_snapshot": self.engine.snapshot,
            "durable_job_v2_detail": self.engine.get,
            "durable_job_v2_admit": self.engine.admit,
            "durable_job_v2_retry": self.engine.retry,
            "durable_job_v2_cancel": self.engine.cancel,
            "durable_job_v2_archive": self.engine.archive,
            "durable_job_v2_delete_history": self.engine.delete_history,
        })

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _dispatch(self, method: str, path: str, *, body: dict[str, object] | None = None, query: dict[str, list[str]] | None = None):
        payload = body if body is not None else {}
        request = ApiRequest(method=method, path=path, query=query or {}, headers={}, _body_reader=lambda strict: payload)
        response = build_router().dispatch(request, self.context)
        self.assertIsNotNone(response)
        return response

    def _terminal_job(self) -> dict[str, object]:
        admitted = self._dispatch("POST", "/api/durable-job-engine/v2", body=_request())
        job = admitted.payload["job"]
        reservation = job["reservation_id"]
        self.engine.claim_running(job["job_id"], "worker:synthetic", reservation)
        return self.engine.finish(job["job_id"], "worker:synthetic", reservation, succeeded=False)["job"]

    def test_snapshot_and_admission_are_path_free_and_api_never_claims_execution(self) -> None:
        before = self._dispatch("GET", "/api/durable-job-engine/v2")
        self.assertEqual(before.status, 200)
        self.assertFalse(self.store_path.exists())
        invalid = self._dispatch("POST", "/api/durable-job-engine/v2", body={**_request(), "input_artifact_ids": ["C:/private/input.png"]})
        self.assertEqual(invalid.status, 400)
        self.assertFalse(self.store_path.exists())
        admitted = self._dispatch("POST", "/api/durable-job-engine/v2", body=_request())
        self.assertEqual(admitted.status, 202)
        self.assertEqual(admitted.payload["execution"], "not_run")
        self.assertTrue(admitted.payload["dry_run"])
        self.assertFalse(admitted.payload["job"]["actual_execution"])
        self.assertNotIn(str(self.temp.name), json.dumps(admitted.payload))

    def test_retry_archive_delete_and_filter_keep_history_and_artifacts_truthful(self) -> None:
        terminal = self._terminal_job()
        job_id = terminal["job_id"]
        reconstructed = self._dispatch("POST", f"/api/durable-job-engine/v2/{job_id}/retry", body={"mode": "RECONSTRUCT_ONLY"})
        self.assertEqual(reconstructed.status, 202)
        self.assertFalse(reconstructed.payload["job"]["active"])
        self.assertEqual(reconstructed.payload["job"]["lifecycle"], "Đã tạo · chưa thực thi")
        active = self._dispatch("GET", "/api/durable-job-engine/v2", query={"status": ["QUEUED"]})
        self.assertEqual(active.status, 200)
        self.assertEqual(active.payload["counts"]["active"], 0)
        archived = self._dispatch("POST", f"/api/durable-job-engine/v2/{job_id}/archive", body={})
        self.assertEqual(archived.status, 200)
        self.assertTrue(archived.payload["artifacts_preserved"])
        rejected = self._dispatch("POST", "/api/durable-job-engine/v2/history/delete", body={"job_ids": [job_id], "confirmed": False})
        self.assertEqual(rejected.status, 400)
        deleted = self._dispatch("POST", "/api/durable-job-engine/v2/history/delete", body={"job_ids": [job_id], "confirmed": True})
        self.assertEqual(deleted.status, 200)
        self.assertTrue(deleted.payload["artifacts_preserved"])

    def test_retry_execution_is_truthfully_unavailable_and_active_record_cannot_retry(self) -> None:
        admitted = self._dispatch("POST", "/api/durable-job-engine/v2", body=_request())
        active = self._dispatch("POST", f"/api/durable-job-engine/v2/{admitted.payload['job']['job_id']}/retry", body={"mode": "RECONSTRUCT_ONLY"})
        self.assertEqual(active.status, 409)
        terminal = self._terminal_job()
        execution = self._dispatch("POST", f"/api/durable-job-engine/v2/{terminal['job_id']}/retry", body={"mode": "RETRY_EXECUTION"})
        self.assertEqual(execution.status, 503)
        self.assertEqual(execution.payload["code"], "actual_retry_execution_not_available")


class DurableJobEngineV2CompositionTests(unittest.TestCase):
    def test_default_context_uses_bound_metadata_path_but_creates_no_store_or_execution_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store_path = Path(temporary) / "Config" / "durable_jobs_v2.sqlite3"
            context = build_default_context({
                "resource_scheduler_hardware": lambda: _hardware(),
                "durable_job_v2_store_path": store_path,
                "project_manager": SimpleNamespace(get_artifact_status=lambda artifact_id: None),
            })
            snapshot = context.call("durable_job_v2_snapshot")
            self.assertEqual(snapshot["schema_version"], DURABLE_JOB_ENGINE_V2_SCHEMA_VERSION)
            self.assertEqual(snapshot["records"], [])
            self.assertFalse(store_path.exists())
            result = context.call("durable_job_v2_admit", _request())
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["code"], "execution_owner_unavailable")
            self.assertFalse(store_path.exists())
            self.assertEqual(context.call("durable_job_v2_reconcile_startup"), {"waiting_resource": 0, "failed": 0, "succeeded": 0, "retained": 0})


class DurableJobEngineV2ArchitectureTests(unittest.TestCase):
    def test_jobs_panel_is_path_free_and_has_no_poll_or_browser_execution_bridge(self) -> None:
        panel = (ROOT / "src/ui/features/jobs/durable_job_engine_v2.js").read_text(encoding="utf-8")
        document = (ROOT / "src/ui/index.html").read_text(encoding="utf-8")
        self.assertIn("/api/durable-job-engine/v2", panel)
        self.assertIn("Đã tạo · chưa thực thi", panel)
        self.assertIn("Xóa chỉ metadata history", panel)
        self.assertIn("durable_job_engine_v2.js", document)
        self.assertNotIn("setInterval", panel)
        self.assertNotIn("window.confirm", panel)
        self.assertNotIn("nvidia-smi", panel.lower())
        self.assertNotIn("subprocess", panel.lower())

    def test_route_inventory_declares_finite_v2_job_actions(self) -> None:
        inventory = json.loads((ROOT / "architecture/api_routes.yaml").read_text(encoding="utf-8"))
        rows = {item["route_id"]: item for item in inventory["routes"]}
        expected = {
            "durable_job_engine.v2_snapshot": "GET",
            "durable_job_engine.v2_detail": "GET",
            "durable_job_engine.v2_admit": "POST",
            "durable_job_engine.v2_retry": "POST",
            "durable_job_engine.v2_cancel": "POST",
            "durable_job_engine.v2_archive": "POST",
            "durable_job_engine.v2_history_delete": "POST",
        }
        self.assertTrue(all(rows.get(route_id, {}).get("method") == method for route_id, method in expected.items()))


if __name__ == "__main__":
    unittest.main()
