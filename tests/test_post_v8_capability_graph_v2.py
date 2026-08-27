"""Post-V8 Phase 1 contracts for the read-only Capability Graph V2."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.services.api.context import ApiContext, build_default_context
from src.services.api.router import ApiRequest
from src.services.api.router_registry import build_router
from src.services.capability_graph import (
    CAPABILITY_GRAPH_SCHEMA_VERSION,
    CapabilityGraph,
    CapabilityGraphError,
    build_component_capability_graph,
)


ROOT = Path(__file__).resolve().parents[1]


def _descriptor(
    capability_id: str,
    *,
    dependencies: list[str] | None = None,
    operational_state: str = "DEGRADED",
    verification_state: str = "NOT_VERIFIED",
    runtime_state: str = "UNAVAILABLE",
    install_state: str = "DISCOVERED",
    evidence_state: str = "static",
    fingerprint: str | None = None,
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
        "evidence": {
            "state": evidence_state,
            "tier": verification_state,
            "fingerprint": fingerprint,
            "observed_at": None,
        },
        "reason": "The exact worker is not yet verified.",
        "next_action": "Verify the exact dependency before execution.",
        "safe_actions": ["inspect", "review_dependency"],
    }


class CapabilityGraphV2Tests(unittest.TestCase):
    def _operational(self, capability_id: str, *, dependencies: list[str] | None = None) -> dict[str, object]:
        record = _descriptor(
            capability_id,
            dependencies=dependencies,
            operational_state="OPERATIONAL",
            verification_state="BOUNDED_SMOKE",
            runtime_state="STARTABLE",
            install_state="INSTALLED",
            evidence_state="completed",
            fingerprint="a" * 64,
        )
        record["safe_actions"] = ["inspect", "review_evidence"]
        return record

    def test_explicit_state_dimensions_and_operational_evidence_are_enforced(self) -> None:
        with self.assertRaisesRegex(CapabilityGraphError, "operational_evidence_required"):
            CapabilityGraph([_descriptor("tool:unsafe", operational_state="OPERATIONAL")])

        mismatched = self._operational("tool:mismatched")
        mismatched["evidence"] = {**mismatched["evidence"], "tier": "FILESYSTEM"}
        with self.assertRaisesRegex(CapabilityGraphError, "verification_evidence_tier_mismatch"):
            CapabilityGraph([mismatched])

        invalid_action = _descriptor("tool:invalid-action")
        invalid_action["safe_actions"] = ["inspect", 7]
        with self.assertRaisesRegex(CapabilityGraphError, "invalid_safe_actions"):
            CapabilityGraph([invalid_action])

        graph = CapabilityGraph([self._operational("tool:verified")])
        detail = graph.capability("tool:verified")
        self.assertEqual(detail["install_state"], "INSTALLED")
        self.assertEqual(detail["runtime_state"], "STARTABLE")
        self.assertEqual(detail["verification_state"], "BOUNDED_SMOKE")
        self.assertEqual(detail["operational_state"], "OPERATIONAL")
        self.assertEqual(detail["evidence"]["state"], "completed")

    def test_missing_dependency_is_an_exact_blocker_not_a_generic_partial_label(self) -> None:
        graph = CapabilityGraph([self._operational("tool:transcribe_media", dependencies=["worker:faster-whisper"])])
        blockers = graph.blockers("tool:transcribe_media")
        self.assertEqual(blockers["execution"], "not_run")
        self.assertTrue(blockers["dry_run"])
        self.assertEqual(blockers["blockers"], [{
            "code": "dependency_missing",
            "capability_id": "worker:faster-whisper",
            "operational_state": "UNAVAILABLE",
            "reason": "The required dependency is not registered in the capability graph.",
            "next_action": "Register or restore the exact dependency before requesting execution.",
        }])
        tree = graph.dependency_tree("tool:transcribe_media")
        self.assertEqual(tree["tree"]["dependencies"][0]["capability_id"], "worker:faster-whisper")
        self.assertEqual(tree["tree"]["dependencies"][0]["node_state"], "MISSING")

    def test_cycle_is_bounded_and_never_recurses_indefinitely(self) -> None:
        left = _descriptor("component:left", dependencies=["component:right"])
        right = _descriptor("component:right", dependencies=["component:left"])
        graph = CapabilityGraph([left, right])
        blockers = graph.blockers("component:left")["blockers"]
        self.assertTrue(any(item["code"] == "dependency_cycle" for item in blockers))
        tree = graph.dependency_tree("component:left")["tree"]
        self.assertEqual(tree["dependencies"][0]["dependencies"][0]["node_state"], "CYCLE")

    def test_server_owned_component_catalog_builds_tool_runtime_model_and_worker_edges(self) -> None:
        graph = build_component_capability_graph(
            component_statuses=[{
                "id": "whisper",
                "version": "large-v3",
                "adapter": "faster_whisper_worker",
                "component_status": "partial",
                "runtime_fingerprint": "b" * 64,
                "last_smoke": {"status": "not_run", "execution": "not_run", "fresh": False, "runtime_fingerprint_match": False},
            }],
            tool_records=[{"name": "transcribe_media", "component": "whisper", "tool_status": "partial"}],
            catalog_snapshot={
                "models": [{
                    "model_id": "faster-whisper-large-v3",
                    "runtime_id": "faster-whisper",
                    "provider": "systran",
                    "version": "large-v3",
                "modules": ["whisper"],
                "status": "NOT_INSTALLED",
                "disposition": "MANUAL_IMPORT_ONLY",
                "minimum_vram_mb": 2048,
                }],
                "runtimes": [{
                "runtime_id": "faster-whisper",
                "provider": "systran",
                "kind": "python",
                "version": "1.0.0",
                    "modules": ["whisper"],
                    "status": "PARTIAL",
                    "disposition": "MANUAL_INSTALL",
                }],
            },
        )
        tool = graph.capability("tool:transcribe_media")
        component = graph.capability("component:whisper")
        runtime = graph.capability("runtime:faster-whisper")
        model = graph.capability("model:faster-whisper-large-v3")
        worker = graph.capability("worker:whisper")
        self.assertEqual(tool["dependencies"], ["component:whisper"])
        self.assertEqual(component["runtime"], "faster-whisper")
        self.assertEqual(component["model"], "faster-whisper-large-v3")
        self.assertEqual(component["dependencies"], [
            "runtime:faster-whisper",
            "worker:whisper",
            "model:faster-whisper-large-v3",
        ])
        self.assertEqual(runtime["dependencies"], ["engine:python"])
        self.assertEqual(model["dependencies"], ["runtime:faster-whisper", "resource:gpu"])
        self.assertEqual(worker["dependencies"], ["resource:gpu"])
        blocker_ids = {item["capability_id"] for item in graph.blockers("tool:transcribe_media")["blockers"]}
        self.assertIn("engine:python", blocker_ids)
        self.assertIn("worker:whisper", blocker_ids)
        self.assertIn("runtime:faster-whisper", blocker_ids)
        self.assertIn("model:faster-whisper-large-v3", blocker_ids)
        self.assertIn("resource:gpu", blocker_ids)

    def test_public_graph_never_echoes_path_or_secret_like_text(self) -> None:
        hostile = _descriptor("component:hostile")
        hostile["reason"] = r"C:\Users\TAM\secret.txt"
        hostile["next_action"] = "Bearer token must be copied"
        snapshot = CapabilityGraph([hostile]).snapshot()
        encoded = json.dumps(snapshot, ensure_ascii=True)
        self.assertNotIn("C:\\", encoded)
        self.assertNotIn("secret", encoded.lower())
        self.assertNotIn("bearer", encoded.lower())


class CapabilityGraphV2ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.graph = CapabilityGraph([
            self._record("component:whisper", dependencies=["worker:whisper"]),
            _descriptor("worker:whisper", operational_state="UNAVAILABLE"),
        ])
        self.context = ApiContext({
            "capability_graph_snapshot": self.graph.snapshot,
            "capability_graph_capability": self.graph.capability,
            "capability_graph_dependency_tree": self.graph.dependency_tree,
            "capability_graph_blockers": self.graph.blockers,
            "capability_graph_safe_actions": self.graph.safe_actions,
            "capability_graph_verification_evidence": self.graph.verification_evidence,
        })

    @staticmethod
    def _record(capability_id: str, *, dependencies: list[str]) -> dict[str, object]:
        return _descriptor(capability_id, dependencies=dependencies)

    def _dispatch(self, path: str):
        request = ApiRequest(method="GET", path=path, query={}, headers={})
        response = build_router().dispatch(request, self.context)
        self.assertIsNotNone(response)
        return response

    def test_all_required_graph_get_routes_are_registered_and_path_free(self) -> None:
        root = "/api/capabilities/v2/component:whisper"
        snapshot = self._dispatch("/api/capabilities/v2")
        detail = self._dispatch(root)
        tree = self._dispatch(root + "/dependency-tree")
        blockers = self._dispatch(root + "/blockers")
        actions = self._dispatch(root + "/safe-actions")
        evidence = self._dispatch(root + "/verification-evidence")
        self.assertEqual(snapshot.status, 200)
        self.assertEqual(snapshot.payload["schema_version"], CAPABILITY_GRAPH_SCHEMA_VERSION)
        self.assertEqual(detail.payload["capability_id"], "component:whisper")
        self.assertEqual(tree.payload["tree"]["capability_id"], "component:whisper")
        self.assertEqual(blockers.payload["blockers"][0]["capability_id"], "component:whisper")
        self.assertEqual(actions.payload["execution"], "not_run")
        self.assertEqual(evidence.payload["verification_state"], "NOT_VERIFIED")

    def test_unknown_or_unsafe_capability_id_is_not_reflected(self) -> None:
        response = self._dispatch("/api/capabilities/v2/C:%5Cprivate%5Csecret")
        self.assertEqual(response.status, 404)
        self.assertEqual(response.payload["error"], "capability_not_found")
        self.assertNotIn("private", json.dumps(response.payload).lower())

    def test_default_api_context_composes_only_bounded_server_owned_snapshots(self) -> None:
        components = [{
            "id": "whisper",
            "version": "large-v3",
            "adapter": "faster_whisper_worker",
            "component_status": "partial",
            "runtime_fingerprint": "c" * 64,
            "last_smoke": {"status": "not_run", "execution": "not_run", "fresh": False, "runtime_fingerprint_match": False},
            "source": r"C:\\private\\must-not-be-projected",
        }]
        catalog = {
            "models": [{"model_id": "faster-whisper-large-v3", "runtime_id": "faster-whisper", "provider": "systran", "version": "large-v3", "modules": ["whisper"], "status": "NOT_INSTALLED", "minimum_vram_mb": 2048}],
            "runtimes": [{"runtime_id": "faster-whisper", "provider": "systran", "kind": "python", "version": "1", "modules": ["whisper"], "status": "PARTIAL"}],
        }
        lifecycle = SimpleNamespace(catalog=SimpleNamespace(snapshot=lambda: catalog))
        bindings = {
            "component_statuses": lambda: components,
            "tool_catalog": lambda items: [{"name": "transcribe_media", "component": "whisper", "tool_status": "partial"}],
            "project_manager": SimpleNamespace(get_artifact_status=lambda artifact_id: None),
        }
        with patch("src.services.productization.ComponentLifecycle", return_value=lifecycle):
            context = build_default_context(bindings)
            snapshot = context.call("capability_graph_snapshot")
        self.assertEqual(snapshot["schema_version"], CAPABILITY_GRAPH_SCHEMA_VERSION)
        self.assertIn("tool:transcribe_media", {item["capability_id"] for item in snapshot["capabilities"]})
        self.assertNotIn("C:\\private", json.dumps(snapshot))
        self.assertTrue(snapshot["dry_run"])


class CapabilityGraphV2UiContractTests(unittest.TestCase):
    def test_components_ui_renders_exact_blockers_without_polling_or_raw_paths(self) -> None:
        renderer = (ROOT / "src/ui/features/components/render.js").read_text(encoding="utf-8")
        control = (ROOT / "src/ui/features/components/v8_control_plane.js").read_text(encoding="utf-8")
        self.assertIn("data-capability-graph", renderer)
        self.assertIn("data-capability-graph-list", renderer)
        self.assertIn("/api/capabilities/v2", control)
        self.assertIn("data-capability-graph-record", control)
        self.assertIn("blocker.capability_id", control)
        self.assertIn("UNSAFE_GRAPH_TEXT", control)
        self.assertIn("refreshWhenPanelEnters", control)
        self.assertNotIn("new MutationObserver(() => { if (view.querySelector", control)
        self.assertNotIn("setInterval", control)

    def test_tracked_route_inventory_lists_all_v2_capability_graph_endpoints(self) -> None:
        inventory = json.loads((ROOT / "architecture/api_routes.yaml").read_text(encoding="utf-8"))
        route_ids = {item["route_id"] for item in inventory["routes"]}
        self.assertTrue({
            "capabilities.v2_snapshot",
            "capabilities.v2_detail",
            "capabilities.v2_dependency_tree",
            "capabilities.v2_blockers",
            "capabilities.v2_safe_actions",
            "capabilities.v2_verification_evidence",
        }.issubset(route_ids))


if __name__ == "__main__":
    unittest.main()
