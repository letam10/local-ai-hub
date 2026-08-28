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


def _numbered_job(value: int) -> str:
    return "jobv5_" + f"{value:032x}"


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
        lease = running["lease"]["lease_id"]
        wrong_reservation = self.scheduler.finish(_job("d"), "worker:whisper", "resv_" + "f" * 32, succeeded=True, lease_id=lease)
        self.assertEqual(wrong_reservation["code"], "reservation_identity_mismatch")
        completed = self.scheduler.finish(_job("d"), "worker:whisper", reservation, succeeded=True, lease_id=lease)
        self.assertEqual(completed["state"], "SUCCEEDED")
        self.assertIsNone(completed["reservation"])
        self.assertEqual(self.scheduler.snapshot()["inventory"]["gpus"][0]["vram_reserved_mb"], 0)

    def test_waiting_heavy_job_is_prepared_after_owner_releases_reservation(self) -> None:
        first = self.scheduler.submit(_job("7"), "worker:whisper", "whisper_gpu_2gb")
        waiting = self.scheduler.submit(_job("8"), "worker:vision", "vision_gpu_2gb")
        self.assertEqual(waiting["state"], "WAITING_RESOURCE")
        reservation = first["reservation"]["reservation_id"]
        running = self.scheduler.claim_running(_job("7"), "worker:whisper", reservation)
        self.assertEqual(running["state"], "RUNNING")
        self.assertEqual(self.scheduler.finish(_job("7"), "worker:whisper", reservation, succeeded=True, lease_id=running["lease"]["lease_id"])["state"], "SUCCEEDED")
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

    def test_worker_lease_is_required_and_expiry_releases_reservation_without_a_worker(self) -> None:
        scheduler = ResourceScheduler(hardware_snapshot=_hardware(), lease_ttl_seconds=10)
        start = time.monotonic()
        prepared = scheduler.submit(_job("a"), "worker:whisper", "whisper_gpu_2gb", now=start)
        reservation = prepared["reservation"]["reservation_id"]
        running = scheduler.claim_running(_job("a"), "worker:whisper", reservation, now=start)
        self.assertRegex(running["lease"]["lease_id"], r"^lease_[a-f0-9]{32}$")
        missing_lease = scheduler.finish(_job("a"), "worker:whisper", reservation, succeeded=True)
        self.assertEqual(missing_lease["code"], "worker_lease_required")
        reconciliation = scheduler.reconcile(now=start + 11)
        self.assertEqual(reconciliation["lease_expired"], 1)
        self.assertEqual(scheduler.job(_job("a"))["state"], "FAILED")
        self.assertEqual(scheduler.job(_job("a"))["reason_code"], "worker_lease_expired")
        self.assertIsNone(scheduler.job(_job("a"))["reservation"])

    def test_terminal_records_are_pruned_so_more_than_512_sequential_jobs_remain_admissible(self) -> None:
        scheduler = ResourceScheduler(hardware_snapshot=_hardware())
        start = time.monotonic()
        for index in range(600):
            admitted = scheduler.submit(_numbered_job(index), "worker:ffmpeg", "ffmpeg_probe", now=start)
            self.assertEqual(admitted["state"], "PREPARING")
            reservation = admitted["reservation"]["reservation_id"]
            running = scheduler.claim_running(_numbered_job(index), "worker:ffmpeg", reservation, now=start)
            completed = scheduler.finish(
                _numbered_job(index), "worker:ffmpeg", reservation,
                succeeded=True, lease_id=running["lease"]["lease_id"],
            )
            self.assertEqual(completed["state"], "SUCCEEDED")
        snapshot = scheduler.snapshot()
        self.assertLessEqual(len(snapshot["jobs"]), 128)
        later = scheduler.submit(_numbered_job(601), "worker:ffmpeg", "ffmpeg_probe", now=start)
        self.assertEqual(later["state"], "PREPARING")

    def test_gpu_exclusivity_is_enforced_for_both_existing_and_new_reservations(self) -> None:
        common = {
            "estimated_vram_mb": 1024, "estimated_ram_mb": 256, "gpu_required": True,
            "cpu_fallback": False, "priority": 50, "interruptible": True, "batchable": True,
            "cpu_slots": 1, "disk_mb": 64, "runtime_slot": None, "provider_slot": None,
        }
        profiles = {
            "shared_gpu": {"profile_id": "shared_gpu", "exclusive": False, **common},
            "exclusive_gpu": {"profile_id": "exclusive_gpu", "exclusive": True, **common},
        }
        forward = ResourceScheduler(hardware_snapshot=_hardware(), profiles=profiles)
        self.assertEqual(forward.submit(_job("1"), "worker:ffmpeg", "shared_gpu")["state"], "PREPARING")
        blocked_new = forward.submit(_job("2"), "worker:whisper", "exclusive_gpu")
        self.assertEqual(blocked_new["reason_code"], "gpu_exclusive_conflict")
        reverse = ResourceScheduler(hardware_snapshot=_hardware(), profiles=profiles)
        self.assertEqual(reverse.submit(_job("3"), "worker:ffmpeg", "exclusive_gpu")["state"], "PREPARING")
        blocked_existing = reverse.submit(_job("4"), "worker:whisper", "shared_gpu")
        self.assertEqual(blocked_existing["reason_code"], "gpu_exclusive_conflict")

    def test_server_owned_current_free_vram_and_safety_margin_bound_admission(self) -> None:
        hardware = _hardware(vram_mb=8192)
        hardware["gpu_safety_margin_mb"] = 512
        hardware["gpus"][0]["free_vram_mb"] = 2600
        hardware["gpus"][0]["observed_at"] = "2026-08-28T00:00:00+00:00"
        hardware["gpus"][0]["source_fingerprint"] = "a" * 64
        scheduler = ResourceScheduler(hardware_snapshot=hardware)
        allowed = scheduler.submit(_job("5"), "worker:whisper", "whisper_gpu_2gb")
        self.assertEqual(allowed["state"], "PREPARING")
        # The 2 GiB reservation plus the fixed 512 MiB margin means no
        # additional GPU profile can be admitted from the observed free value.
        snapshot = scheduler.snapshot()
        self.assertEqual(snapshot["inventory"]["gpus"][0]["vram_free_observed_mb"], 2600)
        self.assertEqual(snapshot["inventory"]["gpus"][0]["vram_available_for_reservation_mb"], 40)


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
