from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.platform.paths import HubPaths
from src.services.component_installer.receipts import CatalogBindingContext, read_receipts
from src.services.component_installer.maintenance_executor import MaintenanceExecutor
from src.services.operational_closure.update_executor import ComponentUpdateExecutor
from src.services.operational_closure.update_service import UpdateResolver


class _SourceService:
    def check(self, _component_id: str, _record: dict[str, object], *, force: bool, binding: dict[str, object] | None = None) -> dict[str, object]:
        del force, binding
        return {
            "status": "AVAILABLE",
            "source_identity": "a" * 64,
            "selected_source": "primary",
            "checked_at": 100,
            "expires_at": 1000,
            "reason_code": "source_reachable",
            "retry_after_seconds": None,
        }


class _Catalog:
    catalog_schema_version = "v7-production-catalog.v2"
    catalog_version = "2026.08.21"
    fingerprint = "b" * 64

    def __init__(self, paths: HubPaths) -> None:
        self.paths = paths
        self.models: dict[str, dict[str, object]] = {}
        self.runtimes: dict[str, dict[str, object]] = {}

    def inspect_model(self, component_id: str) -> dict[str, object]:
        return {"status": "INSTALLED", "component_id": component_id, "execution": "not_run", "dry_run": True}

    def inspect_runtime(self, component_id: str) -> dict[str, object]:
        return {"status": "INSTALLED", "component_id": component_id, "execution": "not_run", "dry_run": True}


class V7UpdateExecutionBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.paths = HubPaths(app_root=root / "app", data_root=root / "data")
        self.paths.config_root.mkdir(parents=True, exist_ok=True)
        self.catalog = _Catalog(self.paths)
        payload = b"new model bytes"
        candidate = self.paths.temp_root / "candidates" / "demo-model.bin"
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_bytes(payload)
        self.record: dict[str, object] = {
            "model_id": "demo-model",
            "revision": "model-r1",
            "latest_upstream_revision": "model-r2",
            "latest_supported_revision": "model-r2",
            "install_strategy": "portable_archive",
            "source_identity": "catalog:demo-model",
            "update_candidate": {
                "staged_relative_path": "candidates/demo-model.bin",
                "relative_path": "demo.bin",
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "source_identity": "catalog:demo-model-update",
            },
        }
        self.catalog.models["demo-model"] = self.record

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _resolver(self) -> UpdateResolver:
        return UpdateResolver(paths=self.paths, catalog=self.catalog, source_service=_SourceService())

    def test_v2_update_writes_exact_typed_receipt_without_public_candidate(self) -> None:
        active = self.paths.models_root / "demo-model"
        active.mkdir(parents=True, exist_ok=True)
        (active / "demo.bin").write_bytes(b"old")
        resolver = self._resolver()
        plan = resolver.plan_update("demo-model")
        self.assertEqual(plan["status"], "planned")
        self.assertNotIn("update_candidate", plan)
        self.assertNotIn(str(self.paths.temp_root), json.dumps(plan))

        result = resolver.apply_update(plan["plan_id"], confirmed=True)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["state"], "INSTALLED_UNVERIFIED")
        self.assertFalse(result["dry_run"])
        receipt = read_receipts(self.paths.config_root)["records"]["demo-model"]
        self.assertEqual(receipt["catalog_schema"], "v7-production-catalog.v2")
        self.assertEqual(receipt["catalog_revision"], self.catalog.catalog_version)
        self.assertEqual(receipt["catalog_fingerprint"], self.catalog.fingerprint)
        self.assertEqual(receipt["source_identity"], self.record["source_identity"])
        self.assertFalse(receipt["operational"])
        self.assertNotIn(str(self.paths.temp_root), json.dumps(receipt))

    def test_missing_binding_refuses_before_copy_or_receipt(self) -> None:
        resolver = self._resolver()
        plan = resolver.plan_update("demo-model")
        internal = resolver._plans[plan["plan_id"]]
        copy = Mock(side_effect=AssertionError("copy reached missing binding"))
        writer = Mock(side_effect=AssertionError("receipt reached missing binding"))
        with patch("src.services.operational_closure.update_executor.shutil.copy2", copy), patch("src.services.operational_closure.update_executor.write_component_receipt", writer):
            result = ComponentUpdateExecutor(paths=self.paths).apply(internal, confirmed=True)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["code"], "catalog_binding_unavailable")
        self.assertTrue(result["dry_run"])
        self.assertFalse(copy.called)
        self.assertFalse(writer.called)
        self.assertFalse((self.paths.config_root / "component_install_receipts.json").exists())

    def test_catalog_fingerprint_drift_refuses_without_target_mutation(self) -> None:
        active = self.paths.models_root / "demo-model"
        active.mkdir(parents=True, exist_ok=True)
        (active / "demo.bin").write_bytes(b"old")
        resolver = self._resolver()
        plan = resolver.plan_update("demo-model")
        self.catalog.fingerprint = "c" * 64
        result = resolver.apply_update(plan["plan_id"], confirmed=True)
        self.assertEqual(result["code"], "catalog_binding_stale")
        self.assertEqual(result["execution"], "not_run")
        self.assertTrue(result["dry_run"])
        self.assertEqual((active / "demo.bin").read_bytes(), b"old")
        self.assertFalse((self.paths.config_root / "component_install_receipts.json").exists())

    def test_candidate_revision_hash_size_or_source_drift_refuses_before_staging(self) -> None:
        cases = (
            ("latest_supported_revision", "model-r3"),
            ("revision", "model-r3"),
            ("source_identity", "catalog:changed"),
        )
        for field, value in cases:
            with self.subTest(field=field):
                resolver = self._resolver()
                plan = resolver.plan_update("demo-model")
                if field == "source_identity":
                    self.record[field] = value
                else:
                    self.record[field] = value
                result = resolver.apply_update(plan["plan_id"], confirmed=True)
                self.assertEqual(result["code"], "catalog_binding_stale")
                self.assertFalse((self.paths.config_root / "component_install_receipts.json").exists())
                self.record["revision"] = "model-r1"
                self.record["latest_supported_revision"] = "model-r2"
                self.record["source_identity"] = "catalog:demo-model"

        resolver = self._resolver()
        plan = resolver.plan_update("demo-model")
        candidate = dict(self.record["update_candidate"])
        candidate["size_bytes"] = int(candidate["size_bytes"]) + 1
        self.record["update_candidate"] = candidate
        result = resolver.apply_update(plan["plan_id"], confirmed=True)
        self.assertEqual(result["code"], "catalog_binding_stale")
        self.assertFalse((self.paths.config_root / "component_install_receipts.json").exists())

    def test_binding_drift_between_stage_and_activation_restores_active_root(self) -> None:
        active = self.paths.models_root / "demo-model"
        active.mkdir(parents=True, exist_ok=True)
        (active / "demo.bin").write_bytes(b"old")
        resolver = self._resolver()
        plan = resolver.plan_update("demo-model")
        original_copy = __import__("shutil").copy2

        def copy_and_drift(source: Path, target: Path) -> object:
            result = original_copy(source, target)
            self.catalog.catalog_version = "2026.08.22"
            return result

        with patch("src.services.operational_closure.update_executor.shutil.copy2", side_effect=copy_and_drift):
            result = resolver.apply_update(plan["plan_id"], confirmed=True)
        self.assertEqual(result["code"], "catalog_binding_stale")
        self.assertEqual((active / "demo.bin").read_bytes(), b"old")
        self.assertFalse((self.paths.config_root / "component_install_receipts.json").exists())

    def test_v2_rollback_rechecks_current_binding_before_swap(self) -> None:
        active = self.paths.models_root / "demo-model"
        active.mkdir(parents=True, exist_ok=True)
        (active / "demo.bin").write_bytes(b"old")
        resolver = self._resolver()
        plan = resolver.plan_update("demo-model")
        self.assertEqual(resolver.apply_update(plan["plan_id"], confirmed=True)["status"], "completed")
        self.catalog.catalog_version = "2026.08.22"
        result = resolver.rollback("demo-model")
        self.assertEqual(result["code"], "catalog_binding_stale")
        self.assertEqual((active / "demo.bin").read_bytes(), b"new model bytes")

    def test_maintenance_executor_requires_full_binding_and_never_uses_fingerprint_only(self) -> None:
        record = {**self.record, "runtime_id": "not-used"}
        catalog = SimpleNamespace(models={"demo-model": record}, runtimes={})
        plan = {
            "schema_version": "component-maintenance-plan.v1",
            "component_id": "demo-model",
            "component_type": "model",
            "action": "update",
            "catalog_fingerprint": self.catalog.fingerprint,
            "_catalog_binding": CatalogBindingContext.for_v2(catalog_version=self.catalog.catalog_version, catalog_fingerprint=self.catalog.fingerprint, source_identity=record["source_identity"]),
            "_record": record,
            "_record_revision": record["revision"],
            "_install_strategy": record["install_strategy"],
            "_latest_supported_revision": record["latest_supported_revision"],
            "_candidate_fingerprint": hashlib.sha256(json.dumps(record["update_candidate"], sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
        }
        result = MaintenanceExecutor(paths=self.paths, catalog=catalog).apply(plan, confirmed=True, catalog_binding={"catalog_fingerprint": self.catalog.fingerprint})
        self.assertEqual(result["code"], "catalog_binding_unavailable")
        self.assertTrue(result["dry_run"])
        self.assertFalse((self.paths.config_root / "component_install_receipts.json").exists())

    def test_maintenance_v2_update_passes_typed_binding_to_receipt(self) -> None:
        record = dict(self.record)
        catalog = SimpleNamespace(models={"demo-model": record}, runtimes={})
        binding = CatalogBindingContext.for_v2(
            catalog_version=self.catalog.catalog_version,
            catalog_fingerprint=self.catalog.fingerprint,
            source_identity=record["source_identity"],
        )
        plan = {
            "schema_version": "component-maintenance-plan.v1",
            "component_id": "demo-model",
            "component_type": "model",
            "action": "update",
            "catalog_fingerprint": binding.catalog_fingerprint,
            "plan_fingerprint": None,
            "_catalog_binding": binding,
            "_record": record,
            "_record_revision": record["revision"],
            "_install_strategy": record["install_strategy"],
            "_latest_supported_revision": record["latest_supported_revision"],
            "_candidate_fingerprint": hashlib.sha256(json.dumps(record["update_candidate"], sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
        }
        result = MaintenanceExecutor(paths=self.paths, catalog=catalog).apply(plan, confirmed=True, catalog_binding=binding, current_record=record)
        self.assertEqual(result["status"], "completed")
        receipt = read_receipts(self.paths.config_root)["records"]["demo-model"]
        self.assertEqual(receipt["catalog_schema"], "v7-production-catalog.v2")
        self.assertEqual(receipt["catalog_revision"], self.catalog.catalog_version)
        self.assertEqual(receipt["catalog_fingerprint"], self.catalog.fingerprint)
        self.assertFalse(receipt["operational"])


if __name__ == "__main__":
    unittest.main()
