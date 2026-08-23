"""Synthetic V8 Wave 3 component-enablement and Wave 2 hardening tests.

The suite is metadata/filesystem only: no provider request, download, model,
GPU, browser, server, FFmpeg workload or external application is started.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from src.platform.paths import HubPaths
from src.services import artifact_store
from src.services.api.router_registry import build_router
from src.services.artifact_access_v8 import (
    install_v8_artifact_compatibility,
    uninstall_v8_artifact_compatibility_for_tests,
)
from src.services.component_enablement_v8 import ComponentEnablementService
from src.services.component_lifecycle_v8 import ComponentLifecycleCoordinator
from src.services.output_authority_v8 import ProductionOutputAuthority
from src.services.productization.catalog import load_production_catalog
from src.services.v8_output_bridge import (
    V8OutputBridge,
    reset_default_bridge_for_tests,
    set_default_bridge_for_tests,
)
from src.services.v8_transaction_extensions import V8ProductionTransactionStore


def _job(char: str) -> str:
    return "jobv5_" + char * 32


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _provenance(job_id: str) -> dict[str, object]:
    return {
        "job_id": job_id,
        "job_spec_fingerprint": _fingerprint(job_id),
        "adapter_id": "test.synthetic.v8",
        "attempt": 1,
        "status": "completed",
    }


class _FakeInstaller:
    def __init__(self) -> None:
        self.plans: dict[str, dict[str, object]] = {}
        self.import_calls = 0

    def plan_import(self, selection_id: str, *, mode: str) -> dict[str, object]:
        plan_id = f"import_plan_{len(self.plans) + 1:032x}"
        body = {
            "schema_version": "model-import-plan.v1",
            "plan_id": plan_id,
            "plan_fingerprint": _fingerprint(plan_id),
            "component_id": "sam2.1-hiera-small",
            "component_type": "model",
            "selection_id": selection_id,
            "mode": mode,
            "status": "planned",
            "execution": "not_run",
            "dry_run": True,
        }
        self.plans[plan_id] = dict(body)
        return dict(body)

    def lookup_plan(self, plan_id: str) -> dict[str, object] | None:
        value = self.plans.get(plan_id)
        return dict(value) if value is not None else None

    def confirm_import(self, plan_id: str, *, confirmed: bool) -> dict[str, object]:
        self.import_calls += 1
        return {
            "status": "completed",
            "component_id": "sam2.1-hiera-small",
            "component_type": "model",
            "state": "INSTALLED_UNVERIFIED",
            "execution": "completed",
            "operational": False,
        }


class _FakeBundle:
    def __init__(self) -> None:
        self.plans: dict[str, dict[str, object]] = {}
        self.confirm_calls = 0

    def plan(self, component_id: str, *, component_type: str, variant: str) -> dict[str, object]:
        plan_id = f"bundle_plan_{len(self.plans) + 1:032x}"
        body = {
            "schema_version": "component-bundle-plan.v1",
            "plan_id": plan_id,
            "plan_fingerprint": _fingerprint(plan_id),
            "component_id": component_id,
            "component_type": component_type,
            "variant": variant,
            "steps": [{
                "step_index": 0,
                "component_id": component_id,
                "component_type": component_type,
                "action": "install",
                "state_fingerprint": _fingerprint(f"{component_type}:{component_id}:state"),
            }],
            "preserve_existing_dependencies": True,
            "shared_dependency_policy": "deduplicate_and_preserve_referenced",
            "status": "planned",
            "execution": "not_run",
            "dry_run": True,
        }
        self.plans[plan_id] = dict(body)
        return dict(body)

    def lookup(self, plan_id: str) -> dict[str, object] | None:
        value = self.plans.get(plan_id)
        return dict(value) if value is not None else None

    def confirm(self, plan_id: str, *, confirmed: bool, progress: object = None) -> dict[str, object]:
        del progress
        self.confirm_calls += 1
        return {
            "status": "completed",
            "component_id": "sam2.1-hiera-small",
            "component_type": "model",
            "state": "INSTALLED_UNVERIFIED",
            "execution": "completed",
            "steps": [{"component_id": "sam2.1-hiera-small", "status": "completed"}],
        }


class V8Wave3ComponentEnablementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.paths = HubPaths(app_root=root / "app", data_root=root / "data")
        self.paths.app_root.mkdir()
        self.paths.data_root.mkdir()
        self.store = V8ProductionTransactionStore(root / "control.sqlite3")

    def tearDown(self) -> None:
        uninstall_v8_artifact_compatibility_for_tests()
        reset_default_bridge_for_tests()
        self.temp.cleanup()

    def test_wave2_publish_boundary_refuses_baseline_candidate(self) -> None:
        output = self.paths.output_root
        output.mkdir(parents=True, exist_ok=True)
        legacy_index = self.paths.config_root / "artifacts.json"
        legacy_index.parent.mkdir(parents=True, exist_ok=True)
        authority = ProductionOutputAuthority(paths=self.paths, store=self.store)
        set_default_bridge_for_tests(V8OutputBridge(authority))
        install_v8_artifact_compatibility()

        job_id = _job("c")
        baseline = output / "preexisting.bin"
        baseline.write_bytes(b"foreign-before-reservation")
        with patch.object(artifact_store, "OUTPUT_ROOT", output), patch.object(artifact_store, "INDEX_PATH", legacy_index):
            self.assertIsNotNone(artifact_store.begin_job_output_scope(job_id))
            published = artifact_store.register_worker_outputs([baseline], provenance=_provenance(job_id))
            self.assertIsNone(published)
            self.assertEqual(authority.list_public(), [])
            finalized = artifact_store.finalize_job_output_scope(
                job_id,
                {"status": "failed", "output": str(baseline)},
                terminal_state="failed",
            )
            self.assertEqual(finalized["status"], "manual_review")
            self.assertTrue(baseline.is_file())

    def test_wave2_atomic_bytes_preserve_public_name_and_media_type(self) -> None:
        authority = ProductionOutputAuthority(paths=self.paths, store=self.store)
        bridge = V8OutputBridge(authority)
        job_id = _job("d")
        artifact = bridge.publish_bytes(
            job_id=job_id,
            content=b"synthetic-image-bytes",
            name="preview.bin",
            media_type="image/png",
            provenance=_provenance(job_id),
        )
        self.assertIsNotNone(artifact)
        assert artifact is not None
        self.assertEqual(artifact["name"], "preview.bin")
        self.assertEqual(artifact["media_type"], "image/png")
        described = authority.describe(str(artifact["id"]))
        self.assertIsNotNone(described)
        assert described is not None
        self.assertEqual(described["name"], "preview.bin")
        self.assertEqual(described["media_type"], "image/png")

    def test_import_and_bundle_are_durable_operations(self) -> None:
        installer = _FakeInstaller()
        bundle = _FakeBundle()
        lifecycle = ComponentLifecycleCoordinator(installer=installer, store=self.store, bundle_service=bundle)

        imported = lifecycle.plan_import("selection_0123456789abcdef0123456789abcdef", mode="COPY_INTO_MANAGED_MODELS")
        import_operation = str(imported["operation_id"])
        self.assertEqual(lifecycle.inspect_operation(import_operation)["action"], "import")
        self.assertNotIn("path", str(imported).lower())
        result = lifecycle.confirm_operation(import_operation, confirmed=True)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(lifecycle.inspect_operation(import_operation)["state"], "committed")
        self.assertEqual(installer.import_calls, 1)
        repeated = lifecycle.confirm_operation(import_operation, confirmed=True)
        self.assertEqual(repeated["status"], "conflict")
        self.assertEqual(installer.import_calls, 1)

        bundled = lifecycle.plan_bundle("sam2.1-hiera-small", component_type="model", variant="default")
        bundle_operation = str(bundled["operation_id"])
        self.assertEqual(lifecycle.inspect_operation(bundle_operation)["action"], "bundle")
        result = lifecycle.confirm_operation(bundle_operation, confirmed=True)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(lifecycle.inspect_operation(bundle_operation)["state"], "committed")
        self.assertEqual(bundle.confirm_calls, 1)

    def test_planned_operation_can_cancel_without_executor(self) -> None:
        installer = _FakeInstaller()
        lifecycle = ComponentLifecycleCoordinator(installer=installer, store=self.store, bundle_service=_FakeBundle())
        planned = lifecycle.plan_import("selection_abcdefabcdefabcdefabcdefabcdefab", mode="COPY_INTO_MANAGED_MODELS")
        operation_id = str(planned["operation_id"])
        cancelled = lifecycle.cancel_operation(operation_id)
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(lifecycle.inspect_operation(operation_id)["state"], "cancelled")
        self.assertEqual(installer.import_calls, 0)

    def test_tracked_catalog_never_promotes_model_implicitly(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        loaded = load_production_catalog(repo / "Config" / "v7_production_catalog.example.json")
        catalog = SimpleNamespace(
            models={item["model_id"]: item for item in loaded["models"]},
            runtimes={item["runtime_id"]: item for item in loaded["runtimes"]},
        )
        service = ComponentEnablementService(catalog=catalog)
        snapshot = service.snapshot()
        models = [item for item in snapshot["records"] if item["component_type"] == "model"]
        self.assertTrue(models)
        self.assertFalse(any(item["auto_install_eligible"] for item in models))

        sam = service.assess("sam2.1-hiera-small")
        self.assertTrue(sam["requirements"]["source_verified"])
        self.assertTrue(sam["requirements"]["integrity_ready"])
        self.assertTrue(sam["requirements"]["size_ready"])
        self.assertEqual(sam["acceptance_state"], "MANUAL_REVIEW_REQUIRED")
        self.assertFalse(sam["auto_install_eligible"])

        flux = service.assess("flux-2-klein-base-4b-fp8")
        self.assertEqual(flux["acceptance_state"], "AUTH_REQUIRED")
        self.assertFalse(flux["auto_install_eligible"])

    def test_wave3_routes_are_registered(self) -> None:
        route_ids = {route.route_id for route in build_router().routes()}
        self.assertIn("components.v8_operations", route_ids)
        self.assertIn("components.v8_operation_confirm", route_ids)
        self.assertIn("components.v8_operation_cancel", route_ids)
        self.assertIn("components.v8_source_acceptance", route_ids)
        self.assertIn("components.v8_source_acceptance_detail", route_ids)


if __name__ == "__main__":
    unittest.main()
