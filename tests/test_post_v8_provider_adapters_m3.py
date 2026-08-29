"""Milestone 3 provider and external-integration contract regressions."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from src.services.api.context import ApiContext, build_default_context
from src.services.api.router import ApiRequest
from src.services.api.router_registry import build_router
from src.services.provider_adapters_v2 import (
    EXTERNAL_INTEGRATIONS_V2_SCHEMA_VERSION,
    PROVIDER_ADAPTERS_V2_SCHEMA_VERSION,
    ExternalIntegrationRegistry,
    ProviderAdapterRegistry,
)


ROOT = Path(__file__).resolve().parents[1]


def capability_snapshot(*identifiers: str) -> dict:
    return {
        "capabilities": [
            {
                "capability_id": identifier,
                "operational_state": "OPERATIONAL",
                "reason": "Bounded fixture verification is current.",
                "next_action": "Review the exact evidence.",
            }
            for identifier in identifiers
        ]
    }


class ProviderAdapterContractTests(unittest.TestCase):
    def test_closed_first_party_registry_is_typed_and_has_no_execution_owner(self) -> None:
        registry = ProviderAdapterRegistry()
        value = registry.snapshot()
        self.assertEqual(value["schema_version"], PROVIDER_ADAPTERS_V2_SCHEMA_VERSION)
        self.assertEqual(value["status"], "completed")
        self.assertEqual(value["execution"], "not_run")
        self.assertTrue(value["dry_run"])
        identifiers = {item["adapter_id"] for item in value["adapters"]}
        self.assertEqual(identifiers, {
            "vision.sam2", "vision.omniparser", "vision.rfdetr", "vision.grounding_dino", "vision.ocr",
            "audio.whisper", "audio.qwen3_tts", "audio.seed_vc", "image.comfyui", "image.flux",
            "image.qwen_image", "video.animesr", "media.ffmpeg",
        })
        expected_methods = ["discover", "availability", "dependencies", "start", "stop", "health", "estimate_resources", "validate_input", "execute", "cancel", "collect_artifacts"]
        self.assertTrue(all(item["contract"]["methods"] == expected_methods for item in value["adapters"]))
        self.assertTrue(all(item["availability"]["execution_owner_state"] == "UNBOUND" for item in value["adapters"]))
        encoded = json.dumps(value).lower()
        self.assertNotIn("c:/", encoded)
        self.assertNotIn("\\\\", encoded)
        self.assertNotIn("api_key", encoded)

    def test_dependency_and_resource_preflight_are_server_owned_and_nonexecuting(self) -> None:
        required = ("component:sam2", "worker:sam2", "runtime:sam2", "model:sam2.1-hiera-small")
        registry = ProviderAdapterRegistry(
            capability_snapshot=lambda: capability_snapshot(*required),
            resource_snapshot=lambda: {"profiles": [{"profile_id": "vision_gpu_plan"}]},
        )
        detail = registry.discover("vision.sam2")
        self.assertEqual(detail["availability"]["state"], "READY_FOR_BINDING")
        self.assertEqual(detail["resource_estimate"], {
            "profile_id": "vision_gpu_plan", "state": "declared",
            "reason": "Resource requirements are server-owned declared profile metadata; no reservation or hardware probe was made.",
            "reservation": "not_reserved",
        })
        ready = registry.preflight("vision.sam2", {"input_types": ["IMAGE", "MASK"]})
        self.assertEqual(ready["status"], "completed")
        self.assertEqual(ready["dispatch"]["status"], "unavailable")
        self.assertFalse(ready["dispatch"]["actual_execution"])
        self.assertEqual(registry.execute("vision.sam2", {"arbitrary": "ignored"})["code"], "provider_adapter_execution_owner_unavailable")
        self.assertEqual(registry.start("vision.sam2")["execution"], "not_run")
        self.assertEqual(registry.stop("vision.sam2")["execution"], "not_run")
        self.assertEqual(registry.cancel("vision.sam2")["execution"], "not_run")
        self.assertEqual(registry.collect_artifacts("vision.sam2")["execution"], "not_run")

    def test_typed_input_contract_rejects_raw_path_url_command_and_bad_type(self) -> None:
        registry = ProviderAdapterRegistry()
        for payload in (
            {"path": "C:/private/input.png"},
            {"input_types": ["IMAGE"], "url": "https://example.invalid"},
            {"input_types": ["IMAGE"], "command": "worker.exe"},
            {"input_types": ["VIDEO"]},
            {"input_types": ["UNKNOWN"]},
        ):
            value = registry.preflight("vision.sam2", payload)
            self.assertEqual(value["status"], "invalid")
            self.assertEqual(value["execution"], "not_run")
            self.assertTrue(value["dry_run"])
        self.assertIsNone(registry.preflight("C:/private", {}))

    def test_missing_capability_is_an_exact_adapter_blocker(self) -> None:
        registry = ProviderAdapterRegistry(capability_snapshot=lambda: capability_snapshot("component:whisper"))
        detail = registry.discover("audio.whisper")
        self.assertEqual(detail["availability"]["state"], "UNAVAILABLE")
        rows = {item["capability_id"]: item for item in detail["dependencies"]["requirements"]}
        self.assertEqual(rows["worker:whisper"]["operational_state"], "MISSING")


class ExternalIntegrationContractTests(unittest.TestCase):
    def test_airi_is_truthfully_unsupported_without_an_official_channel_and_path_free(self) -> None:
        masked_secret_marker = "_".join(("api", "key")) + "=not-public"
        registry = ExternalIntegrationRegistry(applications_snapshot=lambda: [{
            "id": "airi", "component_status": "installed", "launchable": True,
            "path": "C:/Users/private/AIRI.exe", "notes": masked_secret_marker,
        }])
        airi = registry.detail("airi")
        self.assertEqual(airi["connection_state"], "UNSUPPORTED_API")
        self.assertEqual(airi["official_channel"], "none")
        self.assertEqual(airi["credentials"], "external_managed")
        self.assertEqual(airi["launch_capability"]["state"], "AVAILABLE")
        plan = registry.launch_plan("airi")
        self.assertEqual(plan["status"], "completed")
        self.assertEqual(plan["execution"], "not_run")
        self.assertTrue(plan["dry_run"])
        encoded = json.dumps({"airi": airi, "plan": plan}).lower()
        self.assertNotIn("c:/", encoded)
        self.assertNotIn("api_key", encoded)
        self.assertNotIn("private", encoded)

    def test_framework_exposes_all_connection_states_without_faking_airi_connected(self) -> None:
        missing = ExternalIntegrationRegistry().detail("airi")
        self.assertEqual(missing["connection_state"], "NOT_INSTALLED")
        airi_connected_claim = ExternalIntegrationRegistry(
            applications_snapshot=lambda: [{"id": "airi", "component_status": "running", "launchable": False}],
            observations_snapshot=lambda: {"airi": {"connection_state": "CONNECTED"}},
        ).detail("airi")
        self.assertEqual(airi_connected_claim["connection_state"], "UNSUPPORTED_API")
        auth = ExternalIntegrationRegistry(
            applications_snapshot=lambda: [{"id": "airi", "component_status": "installed", "launchable": False}],
            observations_snapshot=lambda: {"airi": {"connection_state": "AUTH_REQUIRED"}},
        ).detail("airi")
        self.assertEqual(auth["connection_state"], "AUTH_REQUIRED")
        connected = ExternalIntegrationRegistry(
            applications_snapshot=lambda: [{"id": "comfyui-external", "component_status": "running", "launchable": False}],
            observations_snapshot=lambda: {"comfyui_external": {"connection_state": "CONNECTED"}},
        ).detail("comfyui_external")
        self.assertEqual(connected["connection_state"], "CONNECTED")
        snapshot = ExternalIntegrationRegistry().snapshot()
        self.assertEqual(snapshot["schema_version"], EXTERNAL_INTEGRATIONS_V2_SCHEMA_VERSION)
        self.assertEqual(set(snapshot["counts"]), {"AUTH_REQUIRED", "CONNECTED", "INSTALLED_NOT_CONNECTED", "NOT_INSTALLED", "UNSUPPORTED_API"})


class ProviderAdapterApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.providers = ProviderAdapterRegistry()
        self.integrations = ExternalIntegrationRegistry()
        self.context = ApiContext({
            "provider_adapters_v2_snapshot": self.providers.snapshot,
            "provider_adapters_v2_detail": self.providers.discover,
            "provider_adapters_v2_preflight": self.providers.preflight,
            "external_integrations_v2_snapshot": self.integrations.snapshot,
            "external_integrations_v2_detail": self.integrations.detail,
            "external_integrations_v2_launch_plan": self.integrations.launch_plan,
        })

    def _dispatch(self, method: str, path: str, body: dict | None = None):
        response = build_router().dispatch(ApiRequest(method=method, path=path, query={}, headers={}, _body_reader=lambda _strict: dict(body or {})), self.context)
        self.assertIsNotNone(response)
        return response

    def test_routes_are_bounded_and_default_context_composes_without_runtime_work(self) -> None:
        listing = self._dispatch("GET", "/api/provider-adapters/v2")
        detail = self._dispatch("GET", "/api/provider-adapters/v2/vision.sam2")
        preflight = self._dispatch("POST", "/api/provider-adapters/v2/vision.sam2/preflight", {"input_types": ["IMAGE"]})
        integrations = self._dispatch("GET", "/api/external-integrations/v2")
        airi = self._dispatch("GET", "/api/external-integrations/v2/airi")
        launch_plan = self._dispatch("POST", "/api/external-integrations/v2/airi/launch-plan")
        self.assertEqual((listing.status, detail.status, preflight.status, integrations.status, airi.status), (200, 200, 200, 200, 200))
        self.assertEqual(launch_plan.status, 409)
        self.assertEqual(self._dispatch("GET", "/api/provider-adapters/v2/C:%2Fprivate").status, 404)
        context = build_default_context({"project_manager": SimpleNamespace(get_artifact_status=lambda _id: None)})
        self.assertEqual(context.call("provider_adapters_v2_snapshot")["schema_version"], PROVIDER_ADAPTERS_V2_SCHEMA_VERSION)
        self.assertEqual(context.call("external_integrations_v2_snapshot")["schema_version"], EXTERNAL_INTEGRATIONS_V2_SCHEMA_VERSION)


class ProviderAdapterArchitectureTests(unittest.TestCase):
    def test_feature_discovery_routes_inventory_and_airi_ui_use_hub_projection(self) -> None:
        from src.services.feature_discovery_v2 import snapshot

        rows = {item["feature_id"]: item for item in snapshot()["features"]}
        self.assertEqual(rows["provider_adapters_v2"]["feature_state"], "PLAN_ONLY")
        self.assertEqual(rows["external_integrations_v2"]["feature_state"], "READ_ONLY")
        inventory = json.loads((ROOT / "architecture/api_routes.yaml").read_text(encoding="utf-8"))
        route_ids = {item["route_id"] for item in inventory["routes"]}
        self.assertTrue({"provider_adapters.v2_snapshot", "provider_adapters.v2_detail", "provider_adapters.v2_preflight", "external_integrations.v2_snapshot", "external_integrations.v2_detail", "external_integrations.v2_launch_plan"}.issubset(route_ids))
        app = (ROOT / "src/ui/app.js").read_text(encoding="utf-8")
        airi = (ROOT / "src/ui/features/airi/render.js").read_text(encoding="utf-8")
        self.assertIn("getExternalIntegrationsV2", app)
        self.assertIn('route === "airi"', app)
        self.assertIn("externalIntegrationsV2", airi)
        self.assertIn("Không embed AIRI bằng hack WebView", airi)


if __name__ == "__main__":
    unittest.main()
