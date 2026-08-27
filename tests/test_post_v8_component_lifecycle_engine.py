"""Post-V8 Phase 2 tests for the unified Lifecycle Engine V2 facade."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest

from src.services.api.context import ApiContext
from src.services.api.router import ApiRequest
from src.services.api.router_registry import build_router
from src.services.capability_graph import CapabilityGraph, build_component_capability_graph
from src.services.component_lifecycle_engine import (
    COMPONENT_LIFECYCLE_SCHEMA_VERSION,
    LIFECYCLE_ACTIONS,
    ComponentLifecycleEngine,
)


ROOT = Path(__file__).resolve().parents[1]


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _descriptor(
    capability_id: str,
    *,
    dependencies: list[str] | None = None,
    operational_state: str = "DEGRADED",
    install_state: str = "DISCOVERED",
    runtime_state: str = "UNAVAILABLE",
    verification_state: str = "NOT_VERIFIED",
) -> dict[str, object]:
    return {
        "capability_id": capability_id,
        "provider": "hub",
        "component": "whisper",
        "runtime": "faster-whisper",
        "model": "faster-whisper-large-v3",
        "dependencies": dependencies or [],
        "version": "1.0.0",
        "install_state": install_state,
        "runtime_state": runtime_state,
        "verification_state": verification_state,
        "operational_state": operational_state,
        "last_verified": None,
        "evidence": {"state": "static", "tier": verification_state, "fingerprint": None, "observed_at": None},
        "reason": "The exact capability has not received current bounded evidence.",
        "next_action": "Review the exact dependency before requesting execution.",
        "safe_actions": ["inspect", "review_dependency"],
    }


class _FakePlanner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    @staticmethod
    def _plan(component_id: str, component_type: str, action: str) -> dict[str, object]:
        plan_id = f"phase2_{action}_{component_id}".replace("-", "_")
        return {
            "operation_id": "compop_" + "a" * 32,
            "plan_id": plan_id,
            "plan_fingerprint": _fingerprint(plan_id),
            "component_id": component_id,
            "component_type": component_type,
            "action": action,
            "status": "planned",
            "operation_state": "planned",
            "reason": "A server-owned plan was created.",
            "next_action": "Review the plan before confirmation.",
            "execution": "not_run",
            "dry_run": True,
        }

    def plan_install(self, component_id: str, *, component_type: str) -> dict[str, object]:
        self.calls.append(("install", component_id, component_type))
        return self._plan(component_id, component_type, "install")

    def plan_verify(self, component_id: str, *, component_type: str) -> dict[str, object]:
        self.calls.append(("verify", component_id, component_type))
        return self._plan(component_id, component_type, "verify")

    def plan_maintenance(self, component_id: str, *, action: str) -> dict[str, object]:
        self.calls.append((action, component_id, "model"))
        return self._plan(component_id, "model", action)


class ComponentLifecycleEngineV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.graph = CapabilityGraph([
            _descriptor("component:whisper", dependencies=["runtime:faster-whisper", "worker:whisper"]),
            _descriptor("model:faster-whisper-large-v3", dependencies=["runtime:faster-whisper", "worker:whisper"]),
            _descriptor("runtime:faster-whisper", operational_state="REGISTERED", runtime_state="NOT_STARTED", verification_state="METADATA"),
            _descriptor("worker:whisper", operational_state="UNAVAILABLE"),
        ])
        self.engine = ComponentLifecycleEngine(graph=self.graph)
        self.planner = _FakePlanner()

    def test_snapshot_exposes_one_finite_contract_and_exact_blockers(self) -> None:
        snapshot = self.engine.snapshot()
        self.assertEqual(snapshot["schema_version"], COMPONENT_LIFECYCLE_SCHEMA_VERSION)
        self.assertEqual(snapshot["supported_actions"], list(LIFECYCLE_ACTIONS))
        records = {item["capability_id"]: item for item in snapshot["components"]}
        model = records["model:faster-whisper-large-v3"]
        component = records["component:whisper"]
        self.assertTrue(model["lifecycle_eligible"])
        self.assertFalse(component["lifecycle_eligible"])
        self.assertEqual(model["component_type"], "model")
        self.assertIn("worker:whisper", {item["capability_id"] for item in model["blockers"]})
        actions = {item["action"]: item for item in model["actions"]}
        self.assertEqual(actions["PLAN_INSTALL"]["availability"], "plan_available")
        self.assertEqual(actions["INSTALL"]["availability"], "plan_required")
        self.assertEqual(actions["START"]["availability"], "blocked")
        self.assertIn("worker:whisper", actions["START"]["reason"])
        self.assertEqual(actions["VERIFY_SOURCE"]["availability"], "blocked")

    def test_plan_install_creates_only_existing_v8_plan_without_execution(self) -> None:
        result = self.engine.plan("model:faster-whisper-large-v3", "PLAN_INSTALL", planner=self.planner)
        self.assertEqual(result["status"], "planned")
        self.assertEqual(result["lifecycle_state"], "PLANNED")
        self.assertEqual(result["execution"], "not_run")
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["plan"]["operation_id"], "compop_" + "a" * 32)
        self.assertEqual(self.planner.calls, [("install", "faster-whisper-large-v3", "model")])

    def test_unregistered_or_confirmation_actions_never_execute_or_fake_health(self) -> None:
        discover = self.engine.plan("model:faster-whisper-large-v3", "DISCOVER", planner=self.planner)
        source = self.engine.plan("model:faster-whisper-large-v3", "VERIFY_SOURCE", planner=self.planner)
        install = self.engine.plan("model:faster-whisper-large-v3", "INSTALL", planner=self.planner)
        start = self.engine.plan("model:faster-whisper-large-v3", "START", planner=self.planner)
        self.assertEqual(discover["status"], "unavailable")
        self.assertEqual(discover["code"], "lifecycle_action_not_plannable")
        self.assertEqual(source["status"], "unavailable")
        self.assertEqual(install["status"], "unavailable")
        self.assertEqual(start["status"], "unavailable")
        self.assertEqual(start["lifecycle_state"], "BLOCKED")
        self.assertEqual(start["execution"], "not_run")
        self.assertEqual(self.planner.calls, [])

    def test_unknown_or_invalid_action_stays_non_reflecting_and_non_mutating(self) -> None:
        self.assertIsNone(self.engine.inspect(r"model:C:\\private"))
        self.assertIsNone(self.engine.plan(r"model:C:\\private", "PLAN_INSTALL", planner=self.planner))
        result = self.engine.plan("model:faster-whisper-large-v3", "DELETE_EVERYTHING", planner=self.planner)
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["code"], "invalid_lifecycle_action")
        self.assertEqual(result["execution"], "not_run")
        self.assertEqual(self.planner.calls, [])

    def test_tracked_catalog_maps_core_product_families_through_one_contract(self) -> None:
        catalog = json.loads((ROOT / "Config" / "v7_production_catalog.example.json").read_text(encoding="utf-8"))
        graph = build_component_capability_graph(
            component_statuses=[],
            tool_records=[],
            catalog_snapshot={"models": catalog["models"], "runtimes": catalog["runtimes"]},
        )
        records = {item["capability_id"] for item in ComponentLifecycleEngine(graph=graph).snapshot()["components"]}
        self.assertTrue({
            "model:sam2.1-hiera-small",
            "model:animesr-v2",
            "model:faster-whisper-large-v3",
            "runtime:comfyui",
            "runtime:ffmpeg",
            "runtime:faster-whisper",
        }.issubset(records))


class ComponentLifecycleEngineV2ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        graph = CapabilityGraph([_descriptor("model:faster-whisper-large-v3")])
        self.engine = ComponentLifecycleEngine(graph=graph)
        self.planner = _FakePlanner()
        self.context = ApiContext({
            "component_lifecycle_v2_snapshot": self.engine.snapshot,
            "component_lifecycle_v2_detail": self.engine.inspect,
            "component_lifecycle_v2_plan": lambda capability_id, action: self.engine.plan(capability_id, action, planner=self.planner),
        })

    def _dispatch(self, method: str, path: str, body: dict[str, object] | None = None):
        request = ApiRequest(method=method, path=path, query={}, headers={}, _body_reader=(lambda strict: dict(body or {})))
        response = build_router().dispatch(request, self.context)
        self.assertIsNotNone(response)
        return response

    def test_routes_are_explicit_and_plan_payload_is_closed(self) -> None:
        root = "/api/component-lifecycle/v2/model:faster-whisper-large-v3"
        snapshot = self._dispatch("GET", "/api/component-lifecycle/v2")
        detail = self._dispatch("GET", root)
        planned = self._dispatch("POST", root + "/plans", {"action": "PLAN_INSTALL"})
        malformed = self._dispatch("POST", root + "/plans", {"action": "PLAN_INSTALL", "path": "C:/private"})
        self.assertEqual(snapshot.status, 200)
        self.assertEqual(detail.payload["component_type"], "model")
        self.assertEqual(planned.status, 200)
        self.assertEqual(planned.payload["execution"], "not_run")
        self.assertEqual(malformed.status, 400)
        self.assertEqual(malformed.payload["error"], "lifecycle_plan_payload_invalid")

    def test_unsafe_capability_id_returns_fixed_not_found(self) -> None:
        response = self._dispatch("GET", "/api/component-lifecycle/v2/model:C:%5Cprivate%5Csecret")
        self.assertEqual(response.status, 404)
        self.assertEqual(response.payload["error"], "lifecycle_capability_not_found")
        self.assertNotIn("private", json.dumps(response.payload).lower())


class ComponentLifecycleEngineV2ArchitectureTests(unittest.TestCase):
    def test_components_panel_exposes_action_contract_without_direct_execution(self) -> None:
        renderer = (ROOT / "src/ui/features/components/render.js").read_text(encoding="utf-8")
        control = (ROOT / "src/ui/features/components/v8_control_plane.js").read_text(encoding="utf-8")
        self.assertIn("data-component-lifecycle", renderer)
        self.assertIn("data-component-lifecycle-list", renderer)
        self.assertIn("/api/component-lifecycle/v2", control)
        self.assertIn("LIFECYCLE_AVAILABILITY", control)
        self.assertIn("data-component-lifecycle-record", control)
        self.assertNotIn("/api/component-lifecycle/v2/${", control)
        self.assertNotIn("setInterval", control)

    def test_tracked_route_inventory_has_all_v2_lifecycle_routes(self) -> None:
        inventory = json.loads((ROOT / "architecture/api_routes.yaml").read_text(encoding="utf-8"))
        route_ids = {item["route_id"] for item in inventory["routes"]}
        self.assertTrue({
            "component_lifecycle.v2_snapshot",
            "component_lifecycle.v2_detail",
            "component_lifecycle.v2_plan",
        }.issubset(route_ids))


if __name__ == "__main__":
    unittest.main()
