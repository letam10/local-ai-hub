"""Final cross-milestone regressions for PR #113 (no runtime workload)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import unittest

from src.services.platform_extensibility_v2 import snapshot as extensibility_snapshot
from src.services.platform_hardening_v2 import snapshot as hardening_snapshot
from src.services.feature_discovery_v2 import snapshot as feature_snapshot
from src.services.product_experience_v2 import SEARCH_CATEGORY_ROUTES, search
from src.services.provider_adapters_v2.registry import ProviderAdapterRegistry
from src.services.resource_scheduler import ResourceScheduler, server_owned_resource_profiles
from src.services.workflow_runtime_v2 import WorkflowRuntimeV2


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = "artifact_" + "a" * 32


def job(char: str) -> str:
    return "jobv5_" + char * 32


def inventory(*, gpus: int = 1) -> dict[str, object]:
    return {
        "cpu_slots": 8,
        "ram_mb": 16384,
        "disk_mb": 32768,
        "gpus": [
            {"id": f"gpu-{index}", "vendor": "nvidia", "device_class": "discrete", "model": "fixture", "vram_mb": 8192}
            for index in range(gpus)
        ],
        "runtime_slots": {"vision": 2, "faster-whisper": 2, "animesr": 2, "ffmpeg": 2},
        "provider_slots": {"vision": 2, "whisper": 2, "video": 2},
    }


def capability_snapshot(*ids: str) -> dict[str, object]:
    return {"capabilities": [{"capability_id": item, "operational_state": "OPERATIONAL", "reason": "fixture", "next_action": "continue"} for item in ids]}


def anime_graph() -> dict[str, object]:
    return {
        "schema_version": 1,
        "id": "workflow_fixture",
        "title": "fixture",
        "scope": "video",
        "groups": [],
        "nodes": [
            {"id": "load", "type": "load_video", "data": {"asset_id": ARTIFACT}},
            {"id": "upscale", "type": "animesr_upscale", "data": {"scale": 2}},
        ],
        "edges": [{"id": "edge_1", "source": {"node": "load", "port": "video"}, "target": {"node": "upscale", "port": "video"}}],
    }


class ResourceContractIntegrationTests(unittest.TestCase):
    def test_public_scheduler_classification_drives_heavy_slots_and_release(self) -> None:
        scheduler = ResourceScheduler(hardware_snapshot=inventory())
        submitted = scheduler.submit(job("a"), "worker:vision", "vision_gpu_2gb")
        self.assertEqual(submitted["state"], "PREPARING")
        self.assertEqual(submitted["resource_profile"], {
            "profile_id": "vision_gpu_2gb", "resource_requirement": "gpu.vision", "execution_class": "gpu",
            "gpu_required": True, "exclusive_gpu": True, "estimated_vram_mb": 2048,
        })
        runtime = WorkflowRuntimeV2(
            artifact_describer=lambda _id: {"id": ARTIFACT, "media_type": "video/mp4"},
            capability_snapshot=lambda: capability_snapshot("runtime:animesr", "model:animesr-v2", "resource:gpu"),
            resource_snapshot=scheduler.snapshot,
        )
        blocked = runtime.preflight({"graph": anime_graph()})
        self.assertEqual(blocked["resource_plan"]["available_heavy_slots"], 0)
        self.assertIn("heavy_gpu_slot_unavailable", {item["code"] for item in blocked["resource_plan"]["blockers"]})
        running = scheduler.claim_running(job("a"), "worker:vision", submitted["reservation"]["reservation_id"])
        released = scheduler.finish(job("a"), "worker:vision", submitted["reservation"]["reservation_id"], succeeded=True, lease_id=running["lease"]["lease_id"])
        self.assertEqual(released["state"], "SUCCEEDED")
        ready = runtime.preflight({"graph": anime_graph()})
        self.assertEqual(ready["resource_plan"]["available_heavy_slots"], 1)

    def test_waiting_is_not_active_and_expired_lease_releases_heavy_slot(self) -> None:
        scheduler = ResourceScheduler(hardware_snapshot=inventory(), lease_ttl_seconds=10)
        first = scheduler.submit(job("b"), "worker:vision", "vision_gpu_2gb")
        second = scheduler.submit(job("c"), "worker:whisper", "whisper_gpu_2gb")
        self.assertEqual(second["state"], "WAITING_RESOURCE")
        self.assertEqual(WorkflowRuntimeV2._active_heavy({"jobs": [scheduler.job(job("b")), scheduler.job(job("c"))]}), 1)
        expiry = ResourceScheduler(hardware_snapshot=inventory(), lease_ttl_seconds=10)
        expiring = expiry.submit(job("f"), "worker:vision", "vision_gpu_2gb", now=1.0)
        running = expiry.claim_running(job("f"), "worker:vision", expiring["reservation"]["reservation_id"], now=1.0)
        expiry.reconcile(now=12.0)
        self.assertEqual(WorkflowRuntimeV2._active_heavy({"jobs": [expiry.job(job("f"))]}), 0)
        self.assertTrue(running["lease"]["lease_id"].startswith("lease_"))

    def test_candidate_gpu_exclusivity_is_not_machine_wide(self) -> None:
        profiles = server_owned_resource_profiles()
        profiles["vision_gpu_2gb"]["runtime_slot"] = None
        profiles["vision_gpu_2gb"]["provider_slot"] = None
        scheduler = ResourceScheduler(hardware_snapshot=inventory(gpus=2), profiles=profiles, max_heavy_gpu_jobs=2)
        first = scheduler.submit(job("d"), "worker:vision", "vision_gpu_2gb")
        second = scheduler.submit(job("e"), "worker:vision2", "vision_gpu_2gb")
        self.assertEqual(first["state"], "PREPARING")
        self.assertEqual(second["state"], "PREPARING")
        self.assertNotEqual(first["reservation"]["gpu_id"], second["reservation"]["gpu_id"])

    def test_stale_free_vram_is_not_a_current_fit(self) -> None:
        value = inventory()
        value["gpus"][0]["free_vram_mb"] = 4096
        value["gpus"][0]["observed_at"] = "2000-01-01T00:00:00+00:00"
        scheduler = ResourceScheduler(hardware_snapshot=value)
        result = scheduler.submit(job("f"), "worker:vision", "vision_gpu_2gb")
        self.assertEqual(result["state"], "WAITING_RESOURCE")
        self.assertEqual(result["reason_code"], "resource_inventory_stale")
        self.assertEqual(scheduler.snapshot()["inventory"]["fit_state"], "stale")


class ProviderAndWorkflowTaxonomyTests(unittest.TestCase):
    def test_all_adapters_declare_recognized_requirement_and_generation_has_no_fake_fit(self) -> None:
        registry = ProviderAdapterRegistry(resource_snapshot=lambda: {"profiles": list(server_owned_resource_profiles().values())})
        adapters = registry.snapshot()["adapters"]
        self.assertEqual(len(adapters), 13)
        self.assertTrue(all(item["contract_state"] == "READY" and item["runtime_state"] == "UNBOUND" for item in adapters))
        self.assertTrue(all(item["resource_estimate"]["code"] != "resource_requirement_unknown" for item in adapters))
        for adapter_id in ("image.comfyui", "image.flux", "image.qwen_image"):
            estimate = registry.discover(adapter_id)["resource_estimate"]
            self.assertEqual(estimate["requirement_id"], "gpu.image_generation")
            self.assertEqual(estimate["state"], "unavailable")
            self.assertEqual(estimate["estimated_vram_mb"], None)
            self.assertFalse(estimate["dispatchable"])

    def test_multi_profile_workflow_does_not_collapse_to_one_profile(self) -> None:
        runtime = WorkflowRuntimeV2(resource_snapshot=lambda: {"inventory": {"status": "available"}, "policy": {"max_heavy_gpu_jobs": 1}, "jobs": [], "profiles": list(server_owned_resource_profiles().values())})
        plan = runtime._resource_plan([
            {"node_id": "cpu", "resource_requirements": {"requirement_id": "media.ffmpeg"}},
            {"node_id": "gpu", "resource_requirements": {"requirement_id": "gpu.vision"}},
        ])
        self.assertTrue(plan["phased_resource_plan"])
        self.assertIn("workflow_multi_profile_coordinator_required", {item["code"] for item in plan["blockers"]})


class ProductTruthfulnessIntegrationTests(unittest.TestCase):
    def test_search_categories_have_one_routable_target_and_correct_truncation(self) -> None:
        frontend = (ROOT / "src" / "ui" / "shared" / "rendering.js").read_text(encoding="utf-8")
        for route in set(SEARCH_CATEGORY_ROUTES.values()):
            self.assertIn(f'["{route}",', frontend)
        records8 = {"models": [{"model_id": f"model-{index}", "display_name": f"fixture {index}", "purpose": "fixture"} for index in range(8)]}
        records9 = {"models": [*records8["models"], {"model_id": "model-9", "display_name": "fixture 9", "purpose": "fixture"}]}
        exact = search("fixture", sources=records8)
        overflow = search("fixture", sources=records9)
        self.assertEqual(len(exact["results"]), 8)
        self.assertFalse(exact["truncated"])
        self.assertTrue(overflow["truncated"])
        self.assertTrue(all(item["route"] in set(SEARCH_CATEGORY_ROUTES.values()) for item in overflow["results"]))

    def test_contract_readiness_is_not_presented_as_runtime_execution(self) -> None:
        hardening = hardening_snapshot()
        extensibility = extensibility_snapshot()
        self.assertEqual(hardening["contract_state"], "READY")
        self.assertEqual(hardening["execution"], "not_run")
        self.assertEqual(extensibility["plugin_sdk"]["runtime_state"], "NOT_IMPLEMENTED")
        self.assertEqual(extensibility["remote_worker"]["connection_state"], "NOT_CONFIGURED")
        feature = next(item for item in feature_snapshot()["features"] if item["feature_id"] == "product_experience_v2")
        self.assertIn("bounded server-owned", feature["reason"])
        self.assertIn("arbitrary filesystem", feature["reason"])
        diagnostics = (ROOT / "src" / "ui" / "features" / "diagnostics" / "render.js").read_text(encoding="utf-8")
        self.assertIn("Contract sẵn sàng · chưa thực thi", diagnostics)
        self.assertIn("chưa cấu hình kết nối", diagnostics)


if __name__ == "__main__":
    unittest.main()
