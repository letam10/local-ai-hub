"""Static/fixture coverage for V7 catalog-bound product executors."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

from src.platform.paths import HubPaths
from src.services.component_installer.receipts import CatalogBindingContext, read_receipts
from src.services.productization.lifecycle import ComponentLifecycle
from src.services.productization.model_executor import ModelArchiveExecutor
from src.services.productization.runtime_executor import RuntimeArchiveExecutor


class V7CatalogExecutionBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.paths = HubPaths(app_root=root / "app", data_root=root / "data")
        self.paths.app_root.joinpath("Config").mkdir(parents=True)
        self.paths.config_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _v2_binding(*, fingerprint: str = "a" * 64, revision: str = "2026.08.21", source: str | None = "catalog:demo") -> CatalogBindingContext:
        return CatalogBindingContext.for_v2(catalog_version=revision, catalog_fingerprint=fingerprint, source_identity=source)

    def _model_record(self, *, source: str | None = "catalog:demo-model") -> dict[str, object]:
        payload = b"tiny-model"
        return {
            "model_id": "demo-model",
            "revision": "model-r1",
            "official_source": "local",
            "source_identity": source,
            "files": [{"relative_path": "weights/demo.bin", "size_bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}],
        }

    def _runtime_record(self, *, source: str | None = "catalog:demo-runtime") -> dict[str, object]:
        return {
            "runtime_id": "demo-runtime",
            "revision": "runtime-r1",
            "root_class": "runtime_root",
            "required_leaves": ["bin/demo.exe"],
            "archive_prefix": "package",
            "archive_leaves": {"bin/demo.exe": "demo.exe"},
            "install_strategy": "portable_archive",
            "disposition": "AUTO_INSTALL_READY",
            "source_identity": source,
            "license": {"state": "apache-2.0", "spdx_id": "Apache-2.0", "url": "https://example.invalid/license"},
            "primary_source": {"url": "https://github.com/example/demo/archive.zip"},
            "integrity": {"verification": "verified", "size_bytes": 128, "sha256": "b" * 64},
            "estimated_disk_size": 128,
        }

    def _archive(self) -> Path:
        archive = Path(self.temp.name) / "runtime.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as handle:
            handle.writestr("package/demo.exe", b"runtime")
        return archive

    def test_v2_model_executor_writes_exact_bound_receipt(self) -> None:
        record = self._model_record()
        binding = self._v2_binding(source="catalog:demo-model")
        staged = Path(self.temp.name) / "model.payload"
        staged.write_bytes(b"tiny-model")

        result = ModelArchiveExecutor(paths=self.paths).apply(
            record,
            staged,
            catalog_binding=binding,
            catalog_fingerprint=binding.catalog_fingerprint,
            catalog_revision=binding.catalog_revision,
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["state"], "INSTALLED_UNVERIFIED")
        self.assertEqual(result["execution"], "completed")
        receipt = read_receipts(self.paths.config_root)["records"]["demo-model"]
        self.assertEqual(receipt["catalog_schema"], "v7-production-catalog.v2")
        self.assertEqual(receipt["catalog_revision"], "2026.08.21")
        self.assertEqual(receipt["catalog_fingerprint"], "a" * 64)
        self.assertEqual(receipt["source_identity"], "catalog:demo-model")
        self.assertFalse(receipt["operational"])
        self.assertNotIn(str(self.temp.name), json.dumps(receipt))

    def test_v2_runtime_executor_writes_exact_bound_receipt(self) -> None:
        record = self._runtime_record()
        binding = self._v2_binding(source="catalog:demo-runtime")

        result = RuntimeArchiveExecutor(paths=self.paths).apply(
            record,
            self._archive(),
            catalog_binding=binding,
            catalog_fingerprint=binding.catalog_fingerprint,
            catalog_revision=binding.catalog_revision,
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["state"], "INSTALLED_UNVERIFIED")
        receipt = read_receipts(self.paths.config_root)["records"]["demo-runtime"]
        self.assertEqual(receipt["catalog_schema"], "v7-production-catalog.v2")
        self.assertEqual(receipt["catalog_revision"], "2026.08.21")
        self.assertEqual(receipt["catalog_fingerprint"], "a" * 64)
        self.assertEqual(receipt["source_identity"], "catalog:demo-runtime")
        self.assertFalse(receipt["operational"])
        self.assertNotIn(str(self.temp.name), json.dumps(receipt))

    def test_v2_null_source_identity_is_preserved_in_runtime_receipt(self) -> None:
        record = self._runtime_record(source=None)
        binding = self._v2_binding(source=None)

        result = RuntimeArchiveExecutor(paths=self.paths).apply(
            record,
            self._archive(),
            catalog_binding=binding,
            catalog_fingerprint=binding.catalog_fingerprint,
            catalog_revision=binding.catalog_revision,
        )

        self.assertEqual(result["status"], "completed")
        receipt = read_receipts(self.paths.config_root)["records"]["demo-runtime"]
        self.assertIsNone(receipt["source_identity"])
        self.assertFalse(receipt["operational"])

    def test_explicit_v1_binding_remains_supported_without_v2_coercion(self) -> None:
        record = self._model_record(source=None)
        binding = CatalogBindingContext.for_v1(component_type="model", record=record, catalog_fingerprint="c" * 64)
        staged = Path(self.temp.name) / "legacy.payload"
        staged.write_bytes(b"tiny-model")

        result = ModelArchiveExecutor(paths=self.paths).apply(
            record,
            staged,
            catalog_binding=binding,
            catalog_fingerprint=binding.catalog_fingerprint,
            catalog_revision=binding.catalog_revision,
        )

        self.assertEqual(result["status"], "completed")
        receipt = read_receipts(self.paths.config_root)["records"]["demo-model"]
        self.assertEqual(receipt["catalog_schema"], "model-catalog.v1")
        self.assertEqual(receipt["catalog_revision"], "model-r1")
        self.assertEqual(receipt["catalog_fingerprint"], "c" * 64)
        self.assertNotEqual(receipt["source_identity"], "catalog:demo-model")

    def test_missing_or_wrong_binding_refuses_before_model_side_effects(self) -> None:
        record = self._model_record()
        staged = Path(self.temp.name) / "model.payload"
        staged.write_bytes(b"tiny-model")
        writer = Mock(side_effect=AssertionError("receipt writer reached binding refusal"))
        copier = Mock(side_effect=AssertionError("copy reached binding refusal"))
        with patch("src.services.productization.model_executor.write_component_receipt", writer), patch("src.services.productization.model_executor.shutil.copy2", copier):
            result = ModelArchiveExecutor(paths=self.paths).apply(record, staged)
        self.assertEqual(result, {"status": "conflict", "code": "catalog_binding_stale", "execution": "not_run", "dry_run": True})
        self.assertFalse(copier.called)
        self.assertFalse(writer.called)
        self.assertFalse((self.paths.models_root / "demo-model").exists())
        self.assertFalse((self.paths.config_root / "component_install_receipts.json").exists())

        wrong_family = CatalogBindingContext.for_v1(component_type="model", record=record, catalog_fingerprint="c" * 64)
        v2_record = {**record, "source_verification": "verified"}
        with patch("src.services.productization.model_executor.shutil.copy2", copier), patch("src.services.productization.model_executor.write_component_receipt", writer):
            result = ModelArchiveExecutor(paths=self.paths).apply(v2_record, staged, catalog_binding=wrong_family, catalog_fingerprint="c" * 64, catalog_revision=wrong_family.catalog_revision)
        self.assertEqual(result["code"], "catalog_schema_unsupported")
        self.assertFalse(copier.called)
        self.assertFalse(writer.called)

        runtime_record = self._runtime_record()
        runtime_binding = CatalogBindingContext.for_v1(component_type="runtime", record=runtime_record, catalog_fingerprint="c" * 64)
        extractor = Mock(side_effect=AssertionError("V2 runtime reached legacy binding"))
        with patch("src.services.productization.runtime_executor.safe_extract_archive", extractor):
            result = RuntimeArchiveExecutor(paths=self.paths).apply(
                runtime_record,
                self._archive(),
                catalog_binding=runtime_binding,
                catalog_fingerprint=runtime_binding.catalog_fingerprint,
                catalog_revision=runtime_binding.catalog_revision,
            )
        self.assertEqual(result["code"], "catalog_schema_unsupported")
        self.assertFalse(extractor.called)

    def test_malformed_executor_record_refuses_without_archive_or_receipt_side_effects(self) -> None:
        binding = self._v2_binding()
        extractor = Mock(side_effect=AssertionError("malformed record reached extraction"))
        writer = Mock(side_effect=AssertionError("malformed record reached receipt writer"))
        with patch("src.services.productization.runtime_executor.safe_extract_archive", extractor), patch("src.services.productization.runtime_executor.write_component_receipt", writer):
            result = RuntimeArchiveExecutor(paths=self.paths).apply(
                [],  # type: ignore[arg-type]
                self._archive(),
                catalog_binding=binding,
                catalog_fingerprint=binding.catalog_fingerprint,
                catalog_revision=binding.catalog_revision,
            )
        self.assertEqual(result, {"status": "conflict", "code": "catalog_binding_stale", "execution": "not_run", "dry_run": True})
        self.assertFalse(extractor.called)
        self.assertFalse(writer.called)

    def test_stale_v2_fields_refuse_before_runtime_extraction(self) -> None:
        record = self._runtime_record()
        archive = self._archive()
        cases = (
            self._v2_binding(fingerprint="c" * 64),
            self._v2_binding(revision="2026.08.22"),
            self._v2_binding(source="catalog:other-runtime"),
        )
        for binding in cases:
            extractor = Mock(side_effect=AssertionError("archive extraction reached stale binding"))
            writer = Mock(side_effect=AssertionError("receipt writer reached stale binding"))
            with patch("src.services.productization.runtime_executor.safe_extract_archive", extractor), patch("src.services.productization.runtime_executor.write_component_receipt", writer):
                result = RuntimeArchiveExecutor(paths=self.paths).apply(record, archive, catalog_binding=binding, catalog_fingerprint="a" * 64, catalog_revision="2026.08.21")
            self.assertEqual(result["status"], "conflict")
            self.assertEqual(result["code"], "catalog_binding_stale")
            self.assertEqual(result["execution"], "not_run")
            self.assertTrue(result["dry_run"])
            self.assertFalse(extractor.called)
            self.assertFalse(writer.called)
        self.assertFalse((self.paths.runtime_root / "bin" / "demo.exe").exists())
        self.assertFalse((self.paths.config_root / "component_install_receipts.json").exists())

    def test_late_binding_drift_cleans_model_target_before_receipt(self) -> None:
        record = self._model_record()
        binding = self._v2_binding(source="catalog:demo-model")
        staged = Path(self.temp.name) / "model.payload"
        staged.write_bytes(b"tiny-model")
        original_copy = shutil.copy2
        writer = Mock(side_effect=AssertionError("receipt writer reached late drift"))

        def copy_and_drift(source: Path, target: Path) -> object:
            result = original_copy(source, target)
            record["source_identity"] = "catalog:changed-model"
            return result

        with patch("src.services.productization.model_executor.shutil.copy2", side_effect=copy_and_drift), patch("src.services.productization.model_executor.write_component_receipt", writer):
            result = ModelArchiveExecutor(paths=self.paths).apply(record, staged, catalog_binding=binding, catalog_fingerprint=binding.catalog_fingerprint, catalog_revision=binding.catalog_revision)
        self.assertEqual(result["code"], "catalog_binding_stale")
        self.assertFalse(writer.called)
        self.assertFalse((self.paths.models_root / "demo-model").exists())

    def test_lifecycle_passes_current_binding_and_rechecks_before_executor(self) -> None:
        record = self._runtime_record()
        catalog = SimpleNamespace(
            catalog_schema_version="v7-production-catalog.v2",
            catalog_version="2026.08.21",
            fingerprint="a" * 64,
            models={},
            runtimes={"demo-runtime": record},
        )
        lifecycle = ComponentLifecycle(paths=self.paths, catalog=catalog)
        plan = lifecycle.plan_one_click("demo-runtime")
        self.assertEqual(plan["status"], "planned")
        staged = Path(self.temp.name) / "downloaded.zip"
        staged.write_bytes(b"fixture")
        captured: dict[str, object] = {}

        class _Downloader:
            def __init__(self, **_kwargs: object) -> None:
                pass

            def download(self, *_args: object, **_kwargs: object) -> SimpleNamespace:
                return SimpleNamespace(staged_path=staged)

        def fake_apply(_record: Mapping[str, object], _archive: Path, **kwargs: object) -> dict[str, object]:
            captured.update(kwargs)
            return {"status": "completed", "state": "INSTALLED_UNVERIFIED", "execution": "completed", "receipt": "written"}

        with patch("src.services.productization.lifecycle.TrustedDownloader", _Downloader), patch("src.services.productization.lifecycle.RuntimeArchiveExecutor.apply", side_effect=fake_apply), patch("src.services.productization.lifecycle.shutil.disk_usage", return_value=SimpleNamespace(free=10**12)):
            result = lifecycle.confirm(plan["plan_id"], confirmed=True)
        self.assertEqual(result["status"], "completed")
        binding = captured["catalog_binding"]
        self.assertIsInstance(binding, CatalogBindingContext)
        assert isinstance(binding, CatalogBindingContext)
        self.assertEqual(binding.catalog_schema, "v7-production-catalog.v2")
        self.assertEqual(binding.catalog_revision, "2026.08.21")
        self.assertEqual(binding.catalog_fingerprint, "a" * 64)
        self.assertEqual(binding.source_identity, "catalog:demo-runtime")

        class _RejectingExecutor:
            def apply(self, *_args: object, **_kwargs: object) -> dict[str, object]:
                return {"status": "conflict", "code": "catalog_binding_stale", "execution": "not_run", "dry_run": True}

        with patch("src.services.productization.lifecycle.RuntimeArchiveExecutor", return_value=_RejectingExecutor()), patch("src.services.productization.lifecycle.TrustedDownloader", _Downloader), patch("src.services.productization.lifecycle.shutil.disk_usage", return_value=SimpleNamespace(free=10**12)):
            refused = lifecycle.confirm(plan["plan_id"], confirmed=True)
        self.assertEqual(refused["code"], "catalog_binding_stale")
        self.assertEqual(refused["execution"], "not_run")
        self.assertTrue(refused["dry_run"])

        with patch("src.services.model_manager.ModelManager", side_effect=AssertionError("V2 reached V1 fixture manager")):
            fixture_refused = lifecycle.apply_fixture(plan["plan_id"], runtime_source=staged, confirmed=True)
        self.assertEqual(fixture_refused["code"], "catalog_schema_unsupported")
        self.assertTrue(fixture_refused["dry_run"])

        maintenance = lifecycle.plan_maintenance("demo-runtime", "repair")
        maintenance_refused = lifecycle.apply_fixture_maintenance(maintenance["plan_id"], confirmed=True)
        self.assertEqual(maintenance_refused["code"], "catalog_schema_unsupported")
        self.assertTrue(maintenance_refused["dry_run"])

        stale_plan = lifecycle.plan_one_click("demo-runtime")

        class _DriftingDownloader:
            def __init__(self, **_kwargs: object) -> None:
                pass

            def download(self, *_args: object, **_kwargs: object) -> SimpleNamespace:
                catalog.catalog_version = "2026.08.22"
                return SimpleNamespace(staged_path=staged)

        executor = Mock(side_effect=AssertionError("stale lifecycle binding reached executor"))
        with patch("src.services.productization.lifecycle.TrustedDownloader", _DriftingDownloader), patch("src.services.productization.lifecycle.RuntimeArchiveExecutor.apply", executor), patch("src.services.productization.lifecycle.shutil.disk_usage", return_value=SimpleNamespace(free=10**12)):
            result = lifecycle.confirm(stale_plan["plan_id"], confirmed=True)
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(result["code"], "stale_binding")
        self.assertFalse(executor.called)

    def test_v2_review_required_license_refuses_before_downloader(self) -> None:
        record = self._runtime_record()
        record["license"] = {"state": "review_required", "spdx_id": None, "url": None}
        catalog = SimpleNamespace(
            catalog_schema_version="v7-production-catalog.v2",
            catalog_version="2026.08.21",
            fingerprint="a" * 64,
            models={},
            runtimes={"demo-runtime": record},
        )
        lifecycle = ComponentLifecycle(paths=self.paths, catalog=catalog)
        plan = lifecycle.plan_one_click("demo-runtime")
        self.assertEqual(plan["status"], "manual_review")
        with patch("src.services.productization.lifecycle.TrustedDownloader", side_effect=AssertionError("license refusal reached downloader")):
            refused = lifecycle.confirm(plan["plan_id"], confirmed=True)
        self.assertEqual(refused["status"], "unavailable")
        self.assertEqual(refused["code"], "license_review_required")
        self.assertEqual(refused["execution"], "not_run")

    def test_v2_existing_runtime_leaf_refuses_before_archive_download(self) -> None:
        record = self._runtime_record()
        target = self.paths.runtime_root / "bin" / "demo.exe"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"pre-existing runtime")
        catalog = SimpleNamespace(
            catalog_schema_version="v7-production-catalog.v2",
            catalog_version="2026.08.21",
            fingerprint="a" * 64,
            models={},
            runtimes={"demo-runtime": record},
        )
        lifecycle = ComponentLifecycle(paths=self.paths, catalog=catalog)
        plan = lifecycle.plan_one_click("demo-runtime")
        self.assertEqual(plan["status"], "manual_review")
        with patch("src.services.productization.lifecycle.TrustedDownloader", side_effect=AssertionError("existing target reached downloader")):
            refused = lifecycle.confirm(plan["plan_id"], confirmed=True)
        self.assertEqual(refused["status"], "unavailable")
        self.assertEqual(refused["code"], "runtime_target_exists_manual_review")
        self.assertEqual(target.read_bytes(), b"pre-existing runtime")


if __name__ == "__main__":
    unittest.main()
