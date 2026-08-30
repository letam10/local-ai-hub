"""Post-V8 API Feature Discovery V2 contract tests."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from src.services.api.context import ApiContext, build_default_context
from src.services.api.router import ApiRequest, Router
from src.services.api.router_registry import build_router
from src.services.feature_discovery_v2 import FEATURE_DISCOVERY_V2_SCHEMA_VERSION, detail, snapshot


ROOT = Path(__file__).resolve().parents[1]
_FEATURES = {
    "capability_graph_v2": "READ_ONLY",
    "component_lifecycle_engine_v2": "PLAN_ONLY",
    "model_manager_v2": "PLAN_ONLY",
    "resource_scheduler_v2": "READ_ONLY",
    "durable_job_engine_v2": "OWNER_REQUIRED",
    "workflow_runtime_v2": "PLAN_ONLY",
    "project_workspace_v2": "METADATA_ONLY",
    "artifact_library_v2": "READ_ONLY",
    "media_pipeline_v2": "PLAN_ONLY",
    "provider_adapters_v2": "PLAN_ONLY",
    "external_integrations_v2": "READ_ONLY",
    "product_experience_v2": "READ_ONLY",
    "platform_hardening_v2": "READ_ONLY",
    "platform_extensibility_v2": "READ_ONLY",
}


class FeatureDiscoveryV2Tests(unittest.TestCase):
    def test_snapshot_advertises_protocol_surfaces_without_claiming_execution(self) -> None:
        value = snapshot()
        self.assertEqual(value["schema_version"], FEATURE_DISCOVERY_V2_SCHEMA_VERSION)
        self.assertEqual(value["status"], "completed")
        self.assertEqual(value["execution"], "not_run")
        self.assertTrue(value["dry_run"])
        rows = {item["feature_id"]: item for item in value["features"]}
        self.assertEqual({key: rows[key]["feature_state"] for key in _FEATURES}, _FEATURES)
        self.assertTrue(all(item["execution"] == "not_run" and item["dry_run"] is True for item in rows.values()))
        durable_routes = rows["durable_job_engine_v2"]["routes"]
        admitted = next(item for item in durable_routes if item["route_id"] == "durable_job_engine.v2_admit")
        self.assertEqual(admitted, {
            "route_id": "durable_job_engine.v2_admit",
            "method": "POST",
            "path": "/api/durable-job-engine/v2",
            "contract_version": "v2",
            "execution_mode": "owner_required",
        })
        self.assertTrue(all({"route_id", "method", "path", "contract_version", "execution_mode"}.issubset(route) for item in rows.values() for route in item["routes"]))
        encoded = json.dumps(value).lower()
        self.assertNotIn("c:/", encoded)
        self.assertNotIn("\\\\", encoded)
        self.assertNotIn("api_key", encoded)
        self.assertNotIn("password", encoded)

    def test_snapshot_is_detached_and_detail_rejects_unsafe_or_unknown_identifier(self) -> None:
        value = snapshot()
        value["features"][0]["feature_state"] = "BROKEN"
        self.assertEqual(detail("capability_graph_v2")["feature"]["feature_state"], "READ_ONLY")
        self.assertIsNone(detail("C:/private"))
        self.assertIsNone(detail("unknown_feature"))

    def test_router_mismatch_or_missing_required_route_is_never_advertised(self) -> None:
        empty = snapshot(Router())
        self.assertEqual(empty["status"], "unavailable")
        self.assertEqual(empty["features"], [])
        self.assertTrue(empty["missing_required_routes"])
        self.assertIsNone(detail("capability_graph_v2", Router()))

        router = Router()
        router.register(
            route_id="capabilities.v2_snapshot",
            method="POST",
            path="/api/capabilities/v2",
            domain="capabilities",
            owner="test",
            handler=lambda request, context, params: None,
        )
        mismatch = snapshot(router)
        self.assertEqual(mismatch["status"], "unavailable")
        self.assertNotIn("capability_graph_v2", {item["feature_id"] for item in mismatch["features"]})
        self.assertTrue(any(item["route_id"] == "capabilities.v2_snapshot" and item["method"] == "GET" for item in mismatch["missing_required_routes"]))


class FeatureDiscoveryV2ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = ApiContext({
            "feature_discovery_v2_snapshot": snapshot,
            "feature_discovery_v2_detail": detail,
        })

    def _dispatch(self, path: str):
        response = build_router().dispatch(ApiRequest(method="GET", path=path, query={}, headers={}), self.context)
        self.assertIsNotNone(response)
        return response

    def test_snapshot_detail_and_default_context_are_explicit(self) -> None:
        listing = self._dispatch("/api/features/v2")
        selected = self._dispatch("/api/features/v2/durable_job_engine_v2")
        missing = self._dispatch("/api/features/v2/C:%2Fprivate")
        self.assertEqual(listing.status, 200)
        self.assertEqual(selected.status, 200)
        self.assertEqual(selected.payload["feature"]["feature_state"], "OWNER_REQUIRED")
        self.assertEqual(missing.status, 404)
        context = build_default_context({"project_manager": SimpleNamespace(get_artifact_status=lambda artifact_id: None)})
        self.assertEqual(context.call("feature_discovery_v2_snapshot")["schema_version"], FEATURE_DISCOVERY_V2_SCHEMA_VERSION)


class FeatureDiscoveryV2ArchitectureTests(unittest.TestCase):
    def test_route_inventory_lists_only_get_discovery_routes(self) -> None:
        inventory = json.loads((ROOT / "architecture/api_routes.yaml").read_text(encoding="utf-8"))
        rows = {item["route_id"]: item for item in inventory["routes"]}
        self.assertEqual(rows["feature_discovery.v2_snapshot"]["method"], "GET")
        self.assertEqual(rows["feature_discovery.v2_detail"]["method"], "GET")


if __name__ == "__main__":
    unittest.main()
