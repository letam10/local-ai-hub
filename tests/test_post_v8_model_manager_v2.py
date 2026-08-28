"""Post-V8 Phase 3 tests for the path-free Model Manager V2 contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.services.api.context import ApiContext, build_default_context
from src.services.api.router import ApiRequest
from src.services.api.router_registry import build_router
from src.services.capability_graph import CapabilityGraph
from src.services.component_lifecycle_engine import ComponentLifecycleEngine
from src.services.model_manager_v2 import MODEL_MANAGER_V2_SCHEMA_VERSION, MODEL_V2_ACTIONS, ModelManagerV2


ROOT = Path(__file__).resolve().parents[1]


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _capability(capability_id: str, *, dependencies: list[str] | None = None, state: str = "DEGRADED") -> dict[str, object]:
    return {
        "capability_id": capability_id,
        "provider": "hub",
        "component": "whisper",
        "runtime": "faster-whisper",
        "model": "faster-whisper-large-v3",
        "dependencies": dependencies or [],
        "version": "large-v3",
        "install_state": "DISCOVERED",
        "runtime_state": "UNAVAILABLE",
        "verification_state": "NOT_VERIFIED",
        "operational_state": state,
        "last_verified": None,
        "evidence": {"state": "static", "tier": "NOT_VERIFIED", "fingerprint": None, "observed_at": None},
        "reason": "The exact capability is not yet verified.",
        "next_action": "Review the exact dependency before requesting execution.",
        "safe_actions": ["inspect", "review_dependency"],
    }


def _model(*, status: str = "NOT_INSTALLED", installed_size: int | None = None, raw_purpose: str = "Speech model for transcription.") -> dict[str, object]:
    return {
        "model_id": "faster-whisper-large-v3",
        "display_name": "Faster-Whisper large-v3",
        "category": "Audio",
        "provider": "SYSTRAN",
        "version": "large-v3",
        "modules": ["whisper"],
        "runtime_id": "faster-whisper",
        "minimum_vram_mb": 2048,
        "recommended_vram_mb": 4096,
        "status": status,
        "operational": False,
        "leaves": [{"relative_leaf": "Speech/Whisper/large-v3/model.bin", "present": status != "NOT_INSTALLED", "size_bytes": 4096, "size_matches": True, "verification": "verified"}],
        "installed_size_bytes": installed_size,
        "expected_download_size_bytes": 4096,
        "expected_disk_size_bytes": 4096,
        "source_availability": {"status": "UNKNOWN", "source_identity": "catalog:faster-whisper-large-v3"},
        "license": {"state": "review_required", "spdx_id": None},
        "reason": raw_purpose,
        "next_action": "Use a native import plan after review.",
    }


class _Planner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    @staticmethod
    def _result(component_id: str, action: str) -> dict[str, object]:
        plan_id = f"phase3_{action}_{component_id}".replace("-", "_")
        return {
            "operation_id": "compop_" + "b" * 32,
            "plan_id": plan_id,
            "plan_fingerprint": _fingerprint(plan_id),
            "component_id": component_id,
            "component_type": "model",
            "action": action,
            "status": "planned",
            "operation_state": "planned",
            "execution": "not_run",
            "dry_run": True,
        }

    def plan_install(self, component_id: str, *, component_type: str) -> dict[str, object]:
        self.calls.append(("install", component_id))
        return self._result(component_id, "install")

    def plan_verify(self, component_id: str, *, component_type: str) -> dict[str, object]:
        self.calls.append(("verify", component_id))
        return self._result(component_id, "verify")

    def plan_maintenance(self, component_id: str, *, action: str) -> dict[str, object]:
        self.calls.append((action, component_id))
        return self._result(component_id, action)

    def plan_reuse(self, component_id: str, *, component_type: str) -> dict[str, object]:
        self.calls.append(("reuse", component_id))
        return self._result(component_id, "reuse")

    def plan_import(self, selection_id: str, *, mode: str) -> dict[str, object]:
        self.calls.append(("import", selection_id))
        return self._result("faster-whisper-large-v3", "import")


class ModelManagerV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        graph = CapabilityGraph([
            _capability("model:faster-whisper-large-v3", dependencies=["runtime:faster-whisper", "worker:whisper"]),
            _capability("runtime:faster-whisper", state="REGISTERED"),
            _capability("worker:whisper", state="UNAVAILABLE"),
        ])
        self.lifecycle = ComponentLifecycleEngine(graph=graph)
        self.planner = _Planner()

    def manager(self, *, model: dict[str, object] | None = None, observations: list[dict[str, object]] | None = None) -> ModelManagerV2:
        return ModelManagerV2(
            catalog_snapshot={"models": [model or _model()]},
            lifecycle_engine=self.lifecycle,
            observations=observations,
        )

    def test_record_has_typed_fields_without_public_path_or_secret(self) -> None:
        manager = self.manager(model=_model(raw_purpose=r"C:\\private\\secret.txt"))
        record = manager.detail("faster-whisper-large-v3")
        self.assertEqual(record["schema_version"], MODEL_MANAGER_V2_SCHEMA_VERSION)
        for field in ("model_id", "display_name", "family", "provider", "purpose", "source", "license", "format", "precision", "size_bytes", "files", "canonical_location", "installed", "verified", "runtime_compatibility", "vram_estimate_mb", "ram_estimate_mb", "last_verified", "checksum", "update_available"):
            self.assertIn(field, record)
        self.assertEqual(record["category"], "Audio")
        self.assertEqual(record["format"], "bin")
        self.assertEqual(record["canonical_location"], {"location_class": "models_root", "location_id": "model:faster-whisper-large-v3"})
        self.assertFalse(record["installed"])
        self.assertFalse(record["verified"])
        encoded = json.dumps(record, ensure_ascii=True).lower()
        self.assertNotIn("c:\\", encoded)
        self.assertNotIn("secret", encoded)

    def test_bounded_observations_detect_duplicate_and_possible_move_without_scan(self) -> None:
        manager = self.manager(observations=[
            {"model_id": "faster-whisper-large-v3", "location_id": "external:managed-copy", "size_bytes": 4096, "sha256": None, "present": True},
        ])
        detail = manager.detail("faster-whisper-large-v3")
        self.assertEqual(detail["duplicate_analysis"]["state"], "candidate_detected")
        self.assertEqual(detail["moved_analysis"]["state"], "possible_moved")
        self.assertEqual(detail["duplicate_analysis"]["candidates"][0]["location_id"], "external:managed-copy")
        self.assertNotIn("path", json.dumps(detail).lower())

    def test_download_preflight_refuses_duplicate_or_existing_model_bytes(self) -> None:
        existing = self.manager(model=_model(status="INSTALLED", installed_size=4096))
        preflight = existing.preflight("faster-whisper-large-v3")
        self.assertTrue(preflight["download_blocked"])
        self.assertFalse(preflight["download_eligible"])
        duplicate = self.manager(observations=[
            {"model_id": "faster-whisper-large-v3", "location_id": "external:managed-copy", "size_bytes": 4096, "sha256": None, "present": True},
        ]).preflight("faster-whisper-large-v3")
        self.assertTrue(duplicate["download_blocked"])
        self.assertIn("duplicate", duplicate["reason"].lower())

    def test_actions_delegate_only_to_durable_v8_plans_or_safe_metadata_views(self) -> None:
        manager = self.manager()
        install = manager.plan("faster-whisper-large-v3", "PLAN_INSTALL", planner=self.planner)
        verify = manager.plan("faster-whisper-large-v3", "VERIFY_CHECKSUM", planner=self.planner)
        remove = manager.plan("faster-whisper-large-v3", "SAFE_REMOVE", planner=self.planner)
        reuse = manager.plan("faster-whisper-large-v3", "REGISTER_EXISTING", planner=self.planner)
        import_missing = manager.plan("faster-whisper-large-v3", "IMPORT_EXISTING", planner=self.planner)
        import_plan = manager.plan("faster-whisper-large-v3", "IMPORT_EXISTING", planner=self.planner, selection_id="selection_" + "c" * 32)
        review = manager.plan("faster-whisper-large-v3", "REVIEW_LICENSE", planner=self.planner)
        update = manager.plan("faster-whisper-large-v3", "UPDATE_METADATA", planner=self.planner)
        for result in (install, remove, reuse, import_plan):
            self.assertEqual(result["status"], "planned")
            self.assertEqual(result["execution"], "not_run")
            self.assertTrue(result["dry_run"])
            self.assertRegex(result["plan"]["operation_id"], r"^compop_[a-f0-9]{32}$")
        self.assertEqual(verify["status"], "completed")
        self.assertEqual(verify["checksum"], {"state": "checksum_not_declared", "verification": "not_verified"})
        self.assertEqual(import_missing["code"], "native_selection_required")
        self.assertEqual(review["status"], "completed")
        self.assertEqual(update["code"], "metadata_update_adapter_unavailable")
        self.assertEqual(self.planner.calls, [
            ("install", "faster-whisper-large-v3"),
            ("uninstall", "faster-whisper-large-v3"),
            ("reuse", "faster-whisper-large-v3"),
            ("import", "selection_" + "c" * 32),
        ])

    def test_content_digest_is_distinct_from_metadata_identity_and_strong_duplicate_requires_content_evidence(self) -> None:
        # The identity digest is a JSON metadata fingerprint.  A bounded file
        # observation that happens to equal it must never become a content
        # checksum match.
        baseline = self.manager()
        identity = baseline.detail("faster-whisper-large-v3")["checksum"]["identity_digest"]
        candidate = self.manager(observations=[{
            "model_id": "faster-whisper-large-v3",
            "location_id": "external:matching-size",
            "size_bytes": 4096,
            "sha256": identity,
            "present": True,
        }]).detail("faster-whisper-large-v3")
        duplicate = candidate["duplicate_analysis"]["candidates"][0]
        self.assertEqual(duplicate["match"], "size_and_identity_candidate")
        self.assertEqual(duplicate["strength"], "candidate")
        self.assertNotEqual(duplicate["match"], "content_sha256")

        content = _fingerprint("actual-model-content")
        declared = _model()
        declared["content_sha256"] = content
        verified = self.manager(model=declared, observations=[{
            "model_id": "faster-whisper-large-v3",
            "location_id": "model:faster-whisper-large-v3",
            "size_bytes": 4096,
            "sha256": content,
            "present": True,
        }])
        detail = verified.detail("faster-whisper-large-v3")
        self.assertEqual(detail["checksum"]["verification"], "verified_evidence_available")
        result = verified.plan("faster-whisper-large-v3", "VERIFY_CHECKSUM", planner=self.planner)
        self.assertEqual(result["status"], "planned")
        self.assertEqual(result["checksum"]["verification"], "verified_evidence_available")

    def test_unknown_and_unsafe_inputs_remain_non_reflecting(self) -> None:
        manager = self.manager()
        self.assertIsNone(manager.detail(r"C:\\private\\secret"))
        self.assertIsNone(manager.plan(r"C:\\private\\secret", "PLAN_INSTALL", planner=self.planner))
        invalid = manager.plan("faster-whisper-large-v3", "DELETE_ALL", planner=self.planner)
        self.assertEqual(invalid["status"], "invalid")
        self.assertEqual(invalid["execution"], "not_run")
        self.assertEqual(self.planner.calls, [])

    def test_tracked_catalog_maps_required_user_categories(self) -> None:
        catalog = json.loads((ROOT / "Config" / "v7_production_catalog.example.json").read_text(encoding="utf-8"))
        manager = ModelManagerV2(catalog_snapshot={"models": catalog["models"]}, lifecycle_engine=self.lifecycle)
        categories = {item["category"] for item in manager.snapshot()["records"]}
        self.assertTrue(categories.issubset({"Image", "Video", "Audio", "Vision", "LLM", "Utility"}))
        self.assertIn("Image", categories)
        self.assertIn("Video", categories)
        self.assertIn("Audio", categories)
        self.assertIn("Vision", categories)


class ModelManagerV2ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        graph = CapabilityGraph([_capability("model:faster-whisper-large-v3")])
        lifecycle = ComponentLifecycleEngine(graph=graph)
        self.planner = _Planner()
        self.manager = ModelManagerV2(catalog_snapshot={"models": [_model()]}, lifecycle_engine=lifecycle)
        self.context = ApiContext({
            "model_manager_v2_snapshot": self.manager.snapshot,
            "model_manager_v2_detail": self.manager.detail,
            "model_manager_v2_preflight": self.manager.preflight,
            "model_manager_v2_plan": lambda model_id, action, selection_id=None: self.manager.plan(model_id, action, planner=self.planner, selection_id=selection_id),
        })

    def _dispatch(self, method: str, path: str, body: dict[str, object] | None = None):
        request = ApiRequest(method=method, path=path, query={}, headers={}, _body_reader=(lambda strict: dict(body or {})))
        response = build_router().dispatch(request, self.context)
        self.assertIsNotNone(response)
        return response

    def test_routes_keep_model_plan_input_closed_and_truthful(self) -> None:
        root = "/api/model-manager/v2/faster-whisper-large-v3"
        snapshot = self._dispatch("GET", "/api/model-manager/v2")
        detail = self._dispatch("GET", root)
        preflight = self._dispatch("GET", root + "/preflight")
        plan = self._dispatch("POST", root + "/plans", {"action": "PLAN_INSTALL"})
        malformed = self._dispatch("POST", root + "/plans", {"action": "PLAN_INSTALL", "path": "C:/private"})
        self.assertEqual(snapshot.status, 200)
        self.assertEqual(snapshot.payload["schema_version"], MODEL_MANAGER_V2_SCHEMA_VERSION)
        self.assertEqual(detail.payload["model_id"], "faster-whisper-large-v3")
        self.assertFalse(preflight.payload["download_eligible"])
        self.assertEqual(plan.status, 200)
        self.assertEqual(plan.payload["execution"], "not_run")
        self.assertEqual(malformed.status, 400)
        self.assertEqual(malformed.payload["error"], "model_v2_plan_payload_invalid")

    def test_unknown_or_unsafe_model_id_returns_fixed_not_found(self) -> None:
        response = self._dispatch("GET", "/api/model-manager/v2/C:%5Cprivate%5Csecret")
        self.assertEqual(response.status, 404)
        self.assertEqual(response.payload["error"], "model_v2_not_found")
        self.assertNotIn("private", json.dumps(response.payload).lower())

    def test_default_context_composes_model_v2_from_bounded_catalog_only(self) -> None:
        catalog = {"models": [_model(raw_purpose=r"C:\\private\\secret.txt")], "runtimes": []}
        lifecycle = SimpleNamespace(catalog=SimpleNamespace(snapshot=lambda: catalog))
        bindings = {
            "component_statuses": lambda: [],
            "tool_catalog": lambda items: [],
            "project_manager": SimpleNamespace(get_artifact_status=lambda artifact_id: None),
        }
        with patch("src.services.productization.ComponentLifecycle", return_value=lifecycle):
            context = build_default_context(bindings)
            snapshot = context.call("model_manager_v2_snapshot")
        self.assertEqual(snapshot["schema_version"], MODEL_MANAGER_V2_SCHEMA_VERSION)
        self.assertEqual(snapshot["records"][0]["model_id"], "faster-whisper-large-v3")
        self.assertNotIn("C:\\private", json.dumps(snapshot))
        self.assertTrue(snapshot["dry_run"])


class ModelManagerV2ArchitectureTests(unittest.TestCase):
    def test_models_ui_exposes_typed_v2_panel_and_plan_only_actions(self) -> None:
        renderer = (ROOT / "src/ui/features/models/models.js").read_text(encoding="utf-8")
        controller = (ROOT / "src/ui/features/models/v2_inventory.js").read_text(encoding="utf-8")
        document = (ROOT / "src/ui/index.html").read_text(encoding="utf-8")
        self.assertIn("data-model-manager-v2", renderer)
        self.assertIn("data-model-manager-v2-list", renderer)
        self.assertIn("/api/model-manager/v2", controller)
        self.assertIn("data-model-v2-plan", controller)
        self.assertIn("MODEL_ACTION", controller)
        self.assertIn("v2_inventory.js", document)
        self.assertNotIn("input type=\"file\"", controller)
        self.assertNotIn("setInterval", controller)

    def test_tracked_route_inventory_lists_v2_model_routes(self) -> None:
        inventory = json.loads((ROOT / "architecture/api_routes.yaml").read_text(encoding="utf-8"))
        route_ids = {item["route_id"] for item in inventory["routes"]}
        self.assertTrue({
            "model_manager.v2_snapshot",
            "model_manager.v2_detail",
            "model_manager.v2_preflight",
            "model_manager.v2_plan",
        }.issubset(route_ids))


if __name__ == "__main__":
    unittest.main()
