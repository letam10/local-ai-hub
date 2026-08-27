"""Post-V8 Phase 4 Resource Scheduler contract tests (no GPU workload)."""

from __future__ import annotations

import json
from pathlib import Path
import time
from types import SimpleNamespace
import unittest

from src.services.api.context import ApiContext, build_default_context
from src.services.api.router import ApiRequest
from src.services.api.router_registry import build_router
from src.services.resource_scheduler import (
    RESOURCE_SCHEDULER_SCHEMA_VERSION,
    ResourceScheduler,
)


ROOT = Path(__file__).resolve().parents[1]


def _job(char: str) -> str:
    return "jobv5_" + char * 32


def _hardware(*, vram_mb: int = 8192) -> dict[str, object]:
    return {
        "cpu_slots": 8,
        "ram_mb": 16384,
        "disk_mb": 32768,
        "gpus": [{"id": "gpu-rtx4060", "vendor": "nvidia", "device_class": "discrete", "model": "RTX-4060", "vram_mb": vram_mb}],
        "runtime_slots": {"faster-whisper": 1, "vision": 1, "animesr": 1, "comfyui": 1, "ffmpeg": 2},
        "provider_slots": {"whisper": 1, "vision": 1, "video": 1, "image": 1},
    }


class ResourceSchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scheduler = ResourceScheduler(hardware_snapshot=_hardware())

    def test_one_heavy_gpu_job_is_reserved_and_second_heavy_waits(self) -> None:
        first = self.scheduler.submit(_job("a"), "worker:whisper", "whisper_gpu_2gb")
        second = self.scheduler.submit(_job("b"), "worker:vision", "vision_gpu_2gb")
        self.assertEqual(first["state"], "PREPARING")
        self.assertRegex(first["reservation"]["reservation_id"], r"^resv_[a-f0-9]{32}$")
        self.assertEqual(second["state"], "WAITING_RESOURCE")
        self.assertEqual(second["reason_code"], "heavy_gpu_limit")
        snapshot = self.scheduler.snapshot()
        self.assertEqual(snapshot["policy"]["max_heavy_gpu_jobs"], 1)
        self.assertEqual(snapshot["inventory"]["gpus"][0]["vram_reserved_mb"], 2048)
        self.assertIsNone(snapshot["inventory"]["gpus"][0]["vram_used_by_processes_mb"])

    def test_lightweight_cpu_profile_can_be_prepared_in_parallel(self) -> None:
        heavy = self.scheduler.submit(_job("a"), "worker:whisper", "whisper_gpu_2gb")
        light = self.scheduler.submit(_job("c"), "worker:ffmpeg", "ffmpeg_probe")
        self.assertEqual(heavy["state"], "PREPARING")
        self.assertEqual(light["state"], "PREPARING")
        self.assertIsNone(light["reservation"]["gpu_id"])
        self.assertEqual(self.scheduler.snapshot()["counts"]["PREPARING"], 2)

    def test_exact_reservation_identity_is_required_for_running_and_finish(self) -> None:
        prepared = self.scheduler.submit(_job("d"), "worker:whisper", "whisper_gpu_2gb")
        reservation = prepared["reservation"]["reservation_id"]
        wrong_worker = self.scheduler.claim_running(_job("d"), "worker:vision", reservation)
        self.assertEqual(wrong_worker["code"], "reservation_identity_mismatch")
        running = self.scheduler.claim_running(_job("d"), "worker:whisper", reservation)
        self.assertEqual(running["state"], "RUNNING")
        wrong_reservation = self.scheduler.finish(_job("d"), "worker:whisper", "resv_" + "f" * 32, succeeded=True)
        self.assertEqual(wrong_reservation["code"], "reservation_identity_mismatch")
        completed = self.scheduler.finish(_job("d"), "worker:whisper", reservation, succeeded=True)
        self.assertEqual(completed["state"], "SUCCEEDED")
        self.assertIsNone(completed["reservation"])
        self.assertEqual(self.scheduler.snapshot()["inventory"]["gpus"][0]["vram_reserved_mb"], 0)

    def test_waiting_heavy_job_is_prepared_after_owner_releases_reservation(self) -> None:
        first = self.scheduler.submit(_job("7"), "worker:whisper", "whisper_gpu_2gb")
        waiting = self.scheduler.submit(_job("8"), "worker:vision", "vision_gpu_2gb")
        self.assertEqual(waiting["state"], "WAITING_RESOURCE")
        reservation = first["reservation"]["reservation_id"]
        self.assertEqual(self.scheduler.claim_running(_job("7"), "worker:whisper", reservation)["state"], "RUNNING")
        self.assertEqual(self.scheduler.finish(_job("7"), "worker:whisper", reservation, succeeded=True)["state"], "SUCCEEDED")
        promoted = self.scheduler.job(_job("8"))
        self.assertEqual(promoted["state"], "PREPARING")
        self.assertIsNotNone(promoted["reservation"])

    def test_expired_preparing_reservation_returns_to_waiting_without_fake_run(self) -> None:
        start = time.monotonic()
        prepared = self.scheduler.submit(_job("e"), "worker:whisper", "whisper_gpu_2gb", now=start)
        self.assertEqual(prepared["state"], "PREPARING")
        reconciliation = self.scheduler.reconcile(now=start + 121)
        state = self.scheduler.job(_job("e"))
        self.assertEqual(reconciliation["expired"], 1)
        self.assertEqual(state["state"], "WAITING_RESOURCE")
        self.assertEqual(state["reason_code"], "reservation_expired")
        self.assertIsNone(state["reservation"])
        self.assertEqual(state["execution"], "not_run")

    def test_cancel_requires_the_owning_worker_and_acknowledgement_releases_capacity(self) -> None:
        prepared = self.scheduler.submit(_job("f"), "worker:whisper", "whisper_gpu_2gb")
        reservation = prepared["reservation"]["reservation_id"]
        invalid = self.scheduler.cancel(_job("f"), "worker:vision")
        self.assertEqual(invalid["code"], "scheduler_cancellation_invalid")
        cancelling = self.scheduler.cancel(_job("f"), "worker:whisper")
        self.assertEqual(cancelling["state"], "CANCELLING")
        cancelled = self.scheduler.acknowledge_cancel(_job("f"), "worker:whisper", reservation)
        self.assertEqual(cancelled["state"], "CANCELLED")
        self.assertEqual(self.scheduler.snapshot()["counts"]["CANCELLED"], 1)

    def test_unknown_inventory_waits_instead_of_claiming_gpu_capacity(self) -> None:
        scheduler = ResourceScheduler(hardware_snapshot=None)
        result = scheduler.submit(_job("1"), "worker:whisper", "whisper_gpu_2gb")
        self.assertEqual(result["state"], "WAITING_RESOURCE")
        self.assertEqual(result["reason_code"], "resource_inventory_unavailable")
        cpu = scheduler.submit(_job("3"), "worker:ffmpeg", "cpu_light")
        self.assertEqual(cpu["state"], "WAITING_RESOURCE")
        snapshot = scheduler.snapshot()
        self.assertEqual(snapshot["status"], "unavailable")
        self.assertEqual(snapshot["queue"]["estimated_next_start"], None)

    def test_snapshot_is_path_free_and_restricts_state_vocabulary(self) -> None:
        self.scheduler.submit(_job("2"), "worker:ffmpeg", "ffmpeg_probe")
        snapshot = self.scheduler.snapshot()
        self.assertEqual(snapshot["schema_version"], RESOURCE_SCHEDULER_SCHEMA_VERSION)
        self.assertTrue(all(item["state"] in {
            "QUEUED", "WAITING_RESOURCE", "PREPARING", "RUNNING", "PAUSED", "CANCELLING", "CANCELLED", "SUCCEEDED", "FAILED",
        } for item in snapshot["jobs"]))
        encoded = json.dumps(snapshot).lower()
        self.assertNotIn("path", encoded)
        self.assertNotIn("secret", encoded)


class ResourceSchedulerApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scheduler = ResourceScheduler(hardware_snapshot=_hardware())
        self.context = ApiContext({
            "resource_scheduler_v2_snapshot": self.scheduler.snapshot,
            "resource_scheduler_v2_job": self.scheduler.job,
        })

    def _dispatch(self, path: str):
        request = ApiRequest(method="GET", path=path, query={}, headers={})
        response = build_router().dispatch(request, self.context)
        self.assertIsNotNone(response)
        return response

    def test_snapshot_profiles_and_unknown_job_are_read_only(self) -> None:
        snapshot = self._dispatch("/api/resource-scheduler/v2")
        profiles = self._dispatch("/api/resource-scheduler/v2/profiles")
        missing = self._dispatch("/api/resource-scheduler/v2/jobs/jobv5_" + "0" * 32)
        self.assertEqual(snapshot.status, 200)
        self.assertEqual(snapshot.payload["schema_version"], RESOURCE_SCHEDULER_SCHEMA_VERSION)
        self.assertIn("whisper_gpu_2gb", {item["profile_id"] for item in profiles.payload["profiles"]})
        self.assertEqual(missing.status, 404)
        self.assertEqual(missing.payload["error"], "scheduler_job_not_found")

    def test_default_context_uses_only_bound_server_inventory_without_gpu_probe(self) -> None:
        bindings = {
            "resource_scheduler_hardware": lambda: _hardware(),
            "project_manager": SimpleNamespace(get_artifact_status=lambda artifact_id: None),
        }
        context = build_default_context(bindings)
        snapshot = context.call("resource_scheduler_v2_snapshot")
        self.assertEqual(snapshot["schema_version"], RESOURCE_SCHEDULER_SCHEMA_VERSION)
        self.assertEqual(snapshot["inventory"]["gpus"][0]["gpu_id"], "gpu-rtx4060")
        self.assertIsNone(snapshot["inventory"]["gpus"][0]["vram_used_by_processes_mb"])


class ResourceSchedulerArchitectureTests(unittest.TestCase):
    def test_dashboard_card_is_read_only_and_has_no_gpu_probe_or_polling_loop(self) -> None:
        controller = (ROOT / "src/ui/features/dashboard/resource_scheduler_v2.js").read_text(encoding="utf-8")
        document = (ROOT / "src/ui/index.html").read_text(encoding="utf-8")
        self.assertIn("/api/resource-scheduler/v2", controller)
        self.assertIn("data-resource-scheduler-card", controller)
        self.assertIn("vram_reserved_mb", controller)
        self.assertIn("estimated_next_start", controller)
        self.assertIn("resource_scheduler_v2.js", document)
        self.assertNotIn("nvidia-smi", controller.lower())
        self.assertNotIn("setInterval", controller)
        self.assertNotIn("method: \"POST\"", controller)

    def test_route_inventory_lists_read_only_scheduler_routes(self) -> None:
        inventory = json.loads((ROOT / "architecture/api_routes.yaml").read_text(encoding="utf-8"))
        rows = {item["route_id"]: item for item in inventory["routes"]}
        self.assertTrue({
            "resource_scheduler.v2_snapshot",
            "resource_scheduler.v2_profiles",
            "resource_scheduler.v2_job",
        }.issubset(rows))
        self.assertTrue(all(rows[key]["method"] == "GET" for key in rows if key.startswith("resource_scheduler.v2_")))


if __name__ == "__main__":
    unittest.main()
