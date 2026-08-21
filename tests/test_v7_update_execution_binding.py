from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.platform.paths import HubPaths
from src.services.component_installer.receipts import CatalogBindingContext, ReceiptError, read_receipts
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
        internal = resolver._private_plans[plan["plan_id"]]
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

    def test_apply_receipt_error_restores_roots_and_preserves_receipt_bytes(self) -> None:
        active = self.paths.models_root / "demo-model"
        active.mkdir(parents=True, exist_ok=True)
        (active / "demo.bin").write_bytes(b"old")
        receipt_path = self.paths.config_root / "component_install_receipts.json"
        original_receipt = '{"schema_version":"component-install-receipts.v3","records":{"unrelated":{"state":"INSTALLED_UNVERIFIED"}}}\n'
        receipt_path.write_text(original_receipt, encoding="utf-8")
        resolver = self._resolver()
        plan = resolver.plan_update("demo-model")
        writer = Mock(side_effect=ReceiptError("receipt_write_failed"))
        with patch("src.services.operational_closure.update_executor.write_component_receipt", writer):
            result = resolver.apply_update(plan["plan_id"], confirmed=True)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["code"], "update_activation_failed")
        self.assertTrue(writer.called)
        self.assertEqual((active / "demo.bin").read_bytes(), b"old")
        previous = self.paths.models_root / ".versions" / "demo-model" / str(plan["installed_revision"])
        self.assertFalse(previous.exists())
        self.assertEqual(receipt_path.read_text(encoding="utf-8"), original_receipt)

    def test_rollback_receipt_error_restores_both_roots_and_receipt_bytes(self) -> None:
        active = self.paths.models_root / "demo-model"
        active.mkdir(parents=True, exist_ok=True)
        (active / "demo.bin").write_bytes(b"old")
        resolver = self._resolver()
        plan = resolver.plan_update("demo-model")
        self.assertEqual(resolver.apply_update(plan["plan_id"], confirmed=True)["status"], "completed")
        receipt_path = self.paths.config_root / "component_install_receipts.json"
        original_receipt = receipt_path.read_bytes()
        previous = self.paths.models_root / ".versions" / "demo-model" / str(plan["installed_revision"])
        old_bytes = (previous / "demo.bin").read_bytes()
        active_bytes = (active / "demo.bin").read_bytes()
        writer = Mock(side_effect=ReceiptError("receipt_write_failed"))
        with patch("src.services.operational_closure.update_executor.write_component_receipt", writer):
            result = resolver.rollback("demo-model")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["code"], "rollback_activation_failed")
        self.assertTrue(writer.called)
        self.assertEqual((active / "demo.bin").read_bytes(), active_bytes)
        self.assertEqual((previous / "demo.bin").read_bytes(), old_bytes)
        self.assertEqual(receipt_path.read_bytes(), original_receipt)

    def test_public_lookup_projection_has_no_private_plan_fields(self) -> None:
        resolver = self._resolver()
        plan = resolver.plan_update("demo-model")
        lookup = resolver._plans.get(plan["plan_id"])
        self.assertIsInstance(lookup, dict)
        assert isinstance(lookup, dict)
        encoded = json.dumps(lookup, ensure_ascii=True)
        for private_key in ("_record", "_catalog_binding", "_plan_fingerprint_payload", "update_candidate", "staged_relative_path"):
            self.assertNotIn(private_key, lookup)
            self.assertNotIn(private_key, encoded)
        self.assertNotIn(str(self.paths.temp_root), encoded)
        self.assertNotIn("https://", encoded)
        self.assertNotIn("secret", encoded.casefold())
        self.assertEqual(lookup["plan_id"], plan["plan_id"])
        self.assertEqual(lookup["execution"], "not_run")
        self.assertTrue(lookup["dry_run"])

    def test_v2_uninstall_preserves_v3_receipt_envelope_and_unrelated_record(self) -> None:
        record = {**self.record, "files": [{"relative_path": "demo.bin", "size_bytes": 3}]}
        catalog = SimpleNamespace(models={"demo-model": record}, runtimes={})
        binding = CatalogBindingContext.for_v2(
            catalog_version=self.catalog.catalog_version,
            catalog_fingerprint=self.catalog.fingerprint,
            source_identity=record["source_identity"],
        )
        root = self.paths.models_root / "demo-model"
        root.mkdir(parents=True, exist_ok=True)
        (root / "demo.bin").write_bytes(b"old")
        unrelated = {
            "component_id": "other-model",
            "component_type": "model",
            "catalog_schema": "v7-production-catalog.v2",
            "catalog_revision": self.catalog.catalog_version,
            "catalog_fingerprint": "d" * 64,
            "source_identity": "catalog:other-model",
            "state": "INSTALLED_UNVERIFIED",
            "operational": False,
            "provenance_marker": "preserve-me",
        }
        target = {
            "component_id": "demo-model",
            "component_type": "model",
            "catalog_schema": binding.catalog_schema,
            "catalog_revision": binding.catalog_revision,
            "catalog_fingerprint": binding.catalog_fingerprint,
            "source_identity": binding.source_identity,
            "state": "INSTALLED_UNVERIFIED",
            "operational": False,
        }
        receipt_path = self.paths.config_root / "component_install_receipts.json"
        receipt_path.write_text(json.dumps({"schema_version": "component-install-receipts.v3", "records": {"demo-model": target, "other-model": unrelated}}, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        before = json.loads(receipt_path.read_text(encoding="utf-8"))
        candidate = record["update_candidate"]
        plan = {
            "schema_version": "component-maintenance-plan.v1",
            "component_id": "demo-model",
            "component_type": "model",
            "action": "uninstall",
            "catalog_fingerprint": binding.catalog_fingerprint,
            "_catalog_binding": binding,
            "_record": record,
            "_record_revision": record["revision"],
            "_install_strategy": record["install_strategy"],
            "_latest_supported_revision": record["latest_supported_revision"],
            "_candidate_fingerprint": hashlib.sha256(json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
        }
        result = MaintenanceExecutor(paths=self.paths, catalog=catalog).apply(plan, confirmed=True, catalog_binding=binding, current_record=record)
        self.assertEqual(result["status"], "completed")
        after = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(after["schema_version"], "component-install-receipts.v3")
        self.assertNotIn("demo-model", after["records"])
        self.assertEqual(after["records"]["other-model"], before["records"]["other-model"])
        self.assertFalse((root / "demo.bin").exists())


if __name__ == "__main__":
    unittest.main()
