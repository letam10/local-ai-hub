"""Static/unit coverage for the V7 managed-component verification contract."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from typing import Any

from src.platform.paths import ComponentPathError, HubPaths, resolve_component_leaf, resolve_component_root
from src.services.component_installer import ComponentInstaller
from src.services.component_installer.receipts import CatalogBindingContext, ReceiptConflict, ReceiptError, read_receipts, write_component_receipt
from src.services.component_installer.verification import DeepComponentVerifier, FastComponentInspector, stream_sha256
from src.services.model_manager import ModelManager
from src.services.runtime_manager import RuntimeManager


class V7ComponentVerificationReuseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.paths = HubPaths(app_root=root / "app", data_root=root / "data")
        self.paths.app_root.joinpath("Config").mkdir(parents=True)
        self.paths.data_root.mkdir(parents=True)
        self.paths.config_root.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _record(*, digest: str | None, size: int | None, revision: str = "demo-r1") -> dict[str, object]:
        item: dict[str, object] = {"relative_path": "weights/demo.bin"}
        if size is not None:
            item["size_bytes"] = size
        if digest is not None:
            item["sha256"] = digest
        return {
            "model_id": "demo-model",
            "model_id_for_test": "demo-model",
            "display_name": "Demo",
            "revision": revision,
            "official_source": "local",
            "files": [item],
        }

    def _model_file(self, payload: bytes = b"demo-bytes") -> tuple[Path, dict[str, object], str]:
        target = self.paths.models_root / "demo-model" / "weights" / "demo.bin"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        return target, self._record(digest=digest, size=len(payload)), digest

    def _v2_installer(self) -> tuple[ComponentInstaller, dict[str, str]]:
        model_catalog = self.paths.app_root / "Config" / "model_catalog.json"
        model_catalog.write_text(json.dumps({
            "schema_version": "model-catalog.v1",
            "models": [{
                "model_id": "demo-model", "display_name": "Demo model", "provider": "fixture", "version": "1", "revision": "model-r1",
                "official_source": "local", "install_supported": False, "modules_using_model": ["demo"], "runtime_id": "demo-runtime",
                "files": [{"relative_path": "demo.bin", "size_bytes": 5, "sha256": hashlib.sha256(b"model").hexdigest()}],
                "estimated_download_size": 5, "estimated_disk_size": 5,
            }],
        }), encoding="utf-8")
        runtime_catalog = self.paths.app_root / "Config" / "runtime_catalog.json"
        runtime_catalog.write_text(json.dumps({
            "schema_version": "runtime-catalog.v1",
            "runtimes": [{
                "runtime_id": "demo-runtime", "display_name": "Demo runtime", "kind": "tool", "version": "1", "revision": "runtime-r1",
                "root_class": "runtime_root", "required_leaves": ["bin/demo.exe"], "modules": ["demo"], "official_source": "local",
                "install_supported": False, "install_strategy": "reference_existing", "estimated_download_size": 0, "estimated_disk_size": 0,
            }],
        }), encoding="utf-8")
        models = ModelManager(paths=self.paths, catalog_path=model_catalog)
        runtimes = RuntimeManager(paths=self.paths, catalog_path=runtime_catalog)
        models._records[0]["source_identity"] = "catalog:demo-model"
        models._by_id["demo-model"] = models._records[0]
        runtimes._records[0]["source_identity"] = None
        current: dict[str, object] = {"version": "2026.08.21", "fingerprint": "a" * 64, "model_source": "catalog:demo-model", "runtime_source": None}

        def provider(component_type: str, _record: Mapping[str, Any]) -> CatalogBindingContext:
            return CatalogBindingContext.for_v2(
                catalog_version=current["version"],
                catalog_fingerprint=current["fingerprint"],
                source_identity=current["model_source"] if component_type == "model" else current["runtime_source"],
            )

        installer = ComponentInstaller(paths=self.paths, model_manager=models, runtime_manager=runtimes, catalog_binding_provider=provider)
        return installer, current

    def test_canonical_resolver_maps_all_roots_and_rejects_caller_paths(self) -> None:
        model_root = resolve_component_root(self.paths, "demo-model", "model", "models_root", require_exists=False)
        runtime_root = resolve_component_root(self.paths, "demo-runtime", "runtime", "runtime_root", require_exists=False)
        environment_root = resolve_component_root(self.paths, "demo-runtime", "runtime", "environments_root", require_exists=False)
        external_root = resolve_component_root(self.paths, "airi", "runtime", "external_managed", require_exists=False)
        self.assertEqual(model_root, self.paths.models_root / "demo-model")
        self.assertEqual(runtime_root, self.paths.runtime_root)
        self.assertEqual(environment_root, self.paths.environments_root)
        self.assertEqual(external_root, self.paths.runtime_root / "external")
        for args in (("demo", "unknown", None), ("demo", "model", "runtime_root"), ("demo", "runtime", "models_root")):
            with self.assertRaises(ComponentPathError):
                resolve_component_root(self.paths, args[0], args[1], args[2], require_exists=False)
        with self.assertRaises(ComponentPathError):
            resolve_component_root(self.paths, "demo-model", "model", "models_root", caller_root=self.temp.name, require_exists=False)
        self.paths.models_root.mkdir(parents=True)
        with self.assertRaises(ComponentPathError):
            resolve_component_leaf(self.paths.models_root / "demo-model", "../outside.bin", require_exists=False)
        with self.assertRaises(ComponentPathError):
            resolve_component_leaf(self.paths.models_root / "demo-model", "missing.bin", require_exists=True)

    def test_canonical_resolver_refuses_reparse_ancestors(self) -> None:
        self.paths.models_root.mkdir(parents=True, exist_ok=True)
        with patch("src.platform.paths.is_reparse_point", side_effect=lambda path: Path(path).name == "Models"):
            with self.assertRaises(ComponentPathError):
                resolve_component_root(self.paths, "demo-model", "model", "models_root", require_exists=False)

    def test_fast_sparse_large_leaf_never_hashes(self) -> None:
        target = self.paths.models_root / "demo-model" / "large.bin"
        target.parent.mkdir(parents=True, exist_ok=True)
        sparse_size = 5 * 1024 * 1024 * 1024
        with target.open("wb") as stream:
            stream.seek(sparse_size - 1)
            stream.write(b"\0")
        record = self._record(digest=None, size=sparse_size)
        record["files"] = [{"relative_path": "large.bin", "size_bytes": sparse_size}]
        with patch("src.services.component_installer.verification.stream_sha256", side_effect=AssertionError("fast inspection hashed")):
            result = FastComponentInspector().inspect(paths=self.paths, component_id="demo-model", component_type="model", record=record, catalog_fingerprint="a" * 64)
        self.assertEqual(result["status"], "DISCOVERED")
        self.assertEqual(result["leaves"][0]["observed_size_bytes"], sparse_size)
        self.assertEqual(result["execution"], "not_run")

    def test_deep_verification_streams_large_leaf_and_requires_size_plus_digest(self) -> None:
        target = self.paths.models_root / "demo-model" / "large.bin"
        target.parent.mkdir(parents=True, exist_ok=True)
        total = 65 * 1024 * 1024 + 17
        digest = hashlib.sha256()
        block = b"v7-component-verification\n" * 4096
        remaining = total
        with target.open("wb") as stream:
            while remaining:
                chunk = block if remaining >= len(block) else block[:remaining]
                stream.write(chunk)
                digest.update(chunk)
                remaining -= len(chunk)
        record = self._record(digest=digest.hexdigest(), size=total)
        record["files"] = [{"relative_path": "large.bin", "size_bytes": total, "sha256": digest.hexdigest()}]
        with patch.object(Path, "read_bytes", side_effect=AssertionError("full-buffer read")):
            result = DeepComponentVerifier().verify(paths=self.paths, component_id="demo-model", component_type="model", record=record, catalog_fingerprint="b" * 64)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["state"], "INSTALLED_VERIFIED")
        self.assertTrue(result["leaves"][0]["verification_level"] == "verified")
        self.assertEqual(result["execution"], "not_run")
        self.assertFalse(result["operational"])

        unpinned = self._record(digest=None, size=total)
        unpinned["files"] = [{"relative_path": "large.bin", "size_bytes": total}]
        measured = DeepComponentVerifier().verify(paths=self.paths, component_id="demo-model", component_type="model", record=unpinned, catalog_fingerprint="c" * 64)
        self.assertEqual(measured["state"], "INSTALLED_UNVERIFIED")
        self.assertEqual(measured["leaves"][0]["verification_level"], "measured_only")

    def test_hash_mismatch_and_race_do_not_write_or_mutate_receipt(self) -> None:
        target, record, _digest = self._model_file()
        receipt = self.paths.config_root / "component_install_receipts.json"
        prior = {
            "schema_version": "component-install-receipts.v2",
            "records": {"demo-model": {"component_id": "demo-model", "component_type": "model", "source": "legacy"}},
        }
        receipt.write_text(json.dumps(prior), encoding="utf-8")
        original = receipt.read_bytes()
        wrong = dict(record)
        wrong["files"] = [{"relative_path": "weights/demo.bin", "size_bytes": target.stat().st_size, "sha256": "0" * 64}]
        mismatch = DeepComponentVerifier().verify(paths=self.paths, component_id="demo-model", component_type="model", record=wrong, catalog_fingerprint="d" * 64)
        self.assertEqual(mismatch["code"], "component_checksum_mismatch")
        self.assertEqual(receipt.read_bytes(), original)

        def mutate(path: Path, **_kwargs: object) -> str:
            target.write_bytes(b"changed-after-read")
            return stream_sha256(path)

        race = DeepComponentVerifier().verify(paths=self.paths, component_id="demo-model", component_type="model", record=record, catalog_fingerprint="e" * 64)
        self.assertEqual(race["status"], "completed")
        with patch("src.services.component_installer.verification.stream_sha256", side_effect=mutate):
            race = DeepComponentVerifier().verify(paths=self.paths, component_id="demo-model", component_type="model", record=record, catalog_fingerprint="e" * 64)
        self.assertEqual(race["code"], "component_leaf_changed_during_verification")
        self.assertEqual(receipt.read_bytes(), original)

    def test_receipt_v3_is_strict_atomic_and_v2_is_unverified(self) -> None:
        _target, record, digest = self._model_file()
        result = DeepComponentVerifier().verify(paths=self.paths, component_id="demo-model", component_type="model", record=record, catalog_fingerprint="f" * 64)
        write_component_receipt(self.paths.config_root, "demo-model", result["receipt"])
        value = json.loads((self.paths.config_root / "component_install_receipts.json").read_text(encoding="utf-8"))
        self.assertEqual(value["schema_version"], "component-install-receipts.v3")
        self.assertEqual(value["records"]["demo-model"]["state"], "INSTALLED_VERIFIED")
        encoded = json.dumps(value, ensure_ascii=True)
        self.assertNotIn(str(self.temp.name), encoded)
        self.assertNotIn("https://", encoded)
        self.assertEqual(value["records"]["demo-model"]["leaves"][0]["verified_sha256"], digest)

        raw = (self.paths.config_root / "component_install_receipts.json").read_bytes()
        with patch("src.services.component_installer.receipts.os.replace", side_effect=OSError("replace denied")):
            with self.assertRaises(OSError):
                write_component_receipt(self.paths.config_root, "demo-model", result["receipt"])
        self.assertEqual((self.paths.config_root / "component_install_receipts.json").read_bytes(), raw)
        self.assertFalse(any(path.name.startswith(".component-receipts-") for path in self.paths.config_root.iterdir()))

        legacy = {"schema_version": "component-install-receipts.v2", "records": {"demo-model": {"state": "INSTALLED_VERIFIED", "path": str(_target)}}}
        (self.paths.config_root / "component_install_receipts.json").write_text(json.dumps(legacy), encoding="utf-8")
        loaded = read_receipts(self.paths.config_root)
        self.assertEqual(loaded["schema_version"], "component-install-receipts.v2")
        self.assertNotIn("path", json.dumps(loaded))
        self.assertEqual(loaded["records"]["demo-model"]["state"], "INSTALLED_UNVERIFIED")

    def test_malformed_duplicate_and_oversized_receipts_fail_closed(self) -> None:
        receipt = self.paths.config_root / "component_install_receipts.json"
        receipt.write_text('{"schema_version":"component-install-receipts.v3","schema_version":"bad","records":{}}', encoding="utf-8")
        loaded = read_receipts(self.paths.config_root)
        self.assertEqual(loaded, {"schema_version": "component-install-receipts.v3", "records": {}})
        receipt.write_bytes(b"{" + b"x" * (512 * 1024 + 1))
        loaded = read_receipts(self.paths.config_root)
        self.assertEqual(loaded, {"schema_version": "component-install-receipts.v3", "records": {}})
        receipt.write_text(json.dumps({"schema_version": "component-install-receipts.v3", "records": {"demo-model": {"state": []}}}), encoding="utf-8")
        loaded = read_receipts(self.paths.config_root)
        self.assertEqual(loaded, {"schema_version": "component-install-receipts.v3", "records": {}})

    def test_unknown_receipt_fields_and_downgrade_fail_without_replacing_previous_state(self) -> None:
        _target, record, _digest = self._model_file()
        result = DeepComponentVerifier().verify(paths=self.paths, component_id="demo-model", component_type="model", record=record, catalog_fingerprint="1" * 64)
        candidate = dict(result["receipt"])
        candidate["client_command"] = "do-not-echo"
        with self.assertRaises(ReceiptError):
            write_component_receipt(self.paths.config_root, "demo-model", candidate)
        write_component_receipt(self.paths.config_root, "demo-model", result["receipt"])
        weaker = dict(result["receipt"])
        weaker["state"] = "INSTALLED_UNVERIFIED"
        weaker["leaves"] = [{key: value for key, value in item.items() if key in {"relative_path", "observed_size_bytes", "observed_mtime_ns"} } | {"verification_level": "measured_only"} for item in result["receipt"]["leaves"]]
        with self.assertRaises(ReceiptConflict):
            write_component_receipt(self.paths.config_root, "demo-model", weaker)
        current = json.loads((self.paths.config_root / "component_install_receipts.json").read_text(encoding="utf-8"))
        self.assertEqual(current["records"]["demo-model"]["state"], "INSTALLED_VERIFIED")

    def test_operational_state_is_never_created_by_reuse_or_receipt(self) -> None:
        _target, record, _digest = self._model_file()
        result = DeepComponentVerifier().verify(paths=self.paths, component_id="demo-model", component_type="model", record=record, catalog_fingerprint="2" * 64)
        self.assertFalse(result["operational"])
        self.assertNotEqual(result["state"], "OPERATIONAL")
        bad = dict(result["receipt"])
        bad["state"] = "OPERATIONAL"
        with self.assertRaises(ReceiptError):
            write_component_receipt(self.paths.config_root, "demo-model", bad)

    def test_v2_model_binding_uses_catalog_version_fingerprint_and_source_identity(self) -> None:
        target, _record, digest = self._model_file(b"v2-model")
        record: dict[str, object] = {
            "model_id": "demo-model", "display_name": "V2 demo model", "kind": "model", "category": "fixture",
            "provider": "fixture", "version": "component-v9", "revision": "component-r9", "runtime_id": "demo-runtime",
            "modules": ["demo"], "disposition": "MANUAL_IMPORT_ONLY", "install_strategy": "manual_import",
            "primary_source": {"provider": "fixture", "kind": "metadata", "canonical_identity": "catalog:demo-model-v2", "revision": "component-r9", "verification_state": "manual", "url": None, "authentication_required": False, "license_required": False},
            "trusted_fallback_sources": [], "source_verification": "manual", "latest_upstream_revision": None,
            "latest_supported_revision": None, "license": {"state": "review_required", "spdx_id": None, "url": None},
            "authentication": {"required": False, "state": "not_required"}, "update_parts": ["model"],
            "estimated_download_size": target.stat().st_size, "estimated_disk_size": target.stat().st_size,
            "minimum_vram_mb": 0, "recommended_vram_mb": 0, "notes": "fixture",
            "source_identity": "catalog:demo-model-v2",
            "files": [{"relative_path": "weights/demo.bin", "verification": "verified", "size_bytes": target.stat().st_size, "sha256": digest}],
        }
        context = CatalogBindingContext.for_v2(catalog_version="2026.08.21", catalog_fingerprint="3" * 64, source_identity="catalog:demo-model-v2")
        result = DeepComponentVerifier().verify(paths=self.paths, component_id="demo-model", component_type="model", record=record, catalog_binding=context)
        self.assertEqual(result["state"], "INSTALLED_VERIFIED")
        self.assertEqual(result["receipt"]["catalog_schema"], "v7-production-catalog.v2")
        self.assertEqual(result["receipt"]["catalog_revision"], "2026.08.21")
        self.assertEqual(result["receipt"]["catalog_fingerprint"], "3" * 64)
        self.assertEqual(result["receipt"]["source_identity"], "catalog:demo-model-v2")
        with self.assertRaises(ReceiptError):
            write_component_receipt(self.paths.config_root, "demo-model", result["receipt"])
        write_component_receipt(self.paths.config_root, "demo-model", result["receipt"], catalog_binding=context)
        inspected = FastComponentInspector().inspect(paths=self.paths, component_id="demo-model", component_type="model", record=record, catalog_binding=context)
        self.assertEqual(inspected["status"], "INSTALLED_VERIFIED")
        self.assertEqual(inspected["catalog_schema"], "v7-production-catalog.v2")
        stale = CatalogBindingContext.for_v2(catalog_version="2026.08.22", catalog_fingerprint="3" * 64, source_identity="catalog:demo-model-v2")
        self.assertNotEqual(FastComponentInspector().inspect(paths=self.paths, component_id="demo-model", component_type="model", record=record, catalog_binding=stale)["status"], "INSTALLED_VERIFIED")
        wrong_source = CatalogBindingContext.for_v2(catalog_version="2026.08.21", catalog_fingerprint="3" * 64, source_identity="catalog:other")
        rejected = DeepComponentVerifier().verify(paths=self.paths, component_id="demo-model", component_type="model", record=record, catalog_binding=wrong_source)
        self.assertEqual(rejected["code"], "catalog_source_identity_mismatch")
        self.assertNotIn("catalog:demo-model-v2", json.dumps(rejected.get("reason", "")))

    def test_v2_runtime_binding_and_explicit_null_source_identity_are_path_free(self) -> None:
        target = self.paths.runtime_root / "tools" / "demo.bin"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"v2-runtime")
        record: dict[str, object] = {
            "runtime_id": "demo-runtime", "display_name": "V2 demo runtime", "kind": "tool", "provider": "fixture",
            "version": "runtime-component-r4", "revision": "runtime-component-r4", "root_class": "runtime_root",
            "required_leaves": ["tools/demo.bin"], "modules": ["demo"], "disposition": "REFERENCE_EXISTING", "install_strategy": "reference_existing",
            "primary_source": None, "trusted_fallback_sources": [], "source_identity": None, "source_verification": "manual",
            "latest_upstream_revision": None, "latest_supported_revision": None,
            "license": {"state": "review_required", "spdx_id": None, "url": None},
            "authentication": {"required": False, "state": "not_required"}, "update_parts": ["runtime"],
            "integrity": {"verification": "unverified"}, "estimated_download_size": 0, "estimated_disk_size": 0,
            "notes": "fixture",
        }
        context = CatalogBindingContext.for_v2(catalog_version="2026.08.21", catalog_fingerprint="4" * 64, source_identity=None)
        result = DeepComponentVerifier().verify(paths=self.paths, component_id="demo-runtime", component_type="runtime", record=record, catalog_binding=context)
        self.assertEqual(result["state"], "INSTALLED_UNVERIFIED")
        self.assertEqual(result["receipt"]["catalog_schema"], "v7-production-catalog.v2")
        self.assertIsNone(result["receipt"]["source_identity"])
        self.assertNotIn(str(self.temp.name), json.dumps(result))
        self.assertNotIn("https://", json.dumps(result))

    def test_v2_lifecycle_uses_component_specific_source_bindings(self) -> None:
        from src.services.productization.lifecycle import ComponentLifecycle

        model = {
            "model_id": "demo-model", "display_name": "V2 demo model", "kind": "model", "category": "fixture",
            "provider": "fixture", "version": "1", "revision": "model-r1", "runtime_id": "demo-runtime",
            "modules": ["demo"], "files": [{"relative_path": "weights/demo.bin", "verification": "unverified"}],
            "source_identity": "catalog:demo-model", "root_class": "models_root",
            "disposition": "MANUAL_IMPORT_ONLY", "install_strategy": "manual_import",
        }
        runtime = {
            "runtime_id": "demo-runtime", "display_name": "V2 demo runtime", "kind": "tool", "provider": "fixture",
            "version": "1", "revision": "runtime-r1", "root_class": "runtime_root", "required_leaves": ["bin/demo.exe"],
            "modules": ["demo"], "source_identity": None, "disposition": "REFERENCE_EXISTING", "install_strategy": "reference_existing",
        }
        catalog = SimpleNamespace(
            catalog_schema_version="v7-production-catalog.v2", catalog_version="2026.08.21", fingerprint="5" * 64,
            models={"demo-model": model}, runtimes={"demo-runtime": runtime},
        )
        lifecycle = ComponentLifecycle(paths=self.paths, catalog=catalog)
        plan = lifecycle.plan_one_click("demo-model")
        self.assertEqual(plan["dependencies"][0], {"kind": "runtime", "id": "demo-runtime", "status": "NOT_INSTALLED"})
        self.assertNotIn(str(self.temp.name), json.dumps(plan))
        binding = lifecycle._plans[plan["plan_id"]]["_catalog_binding"]
        self.assertEqual(binding.catalog_schema, "v7-production-catalog.v2")
        self.assertEqual(binding.catalog_revision, "2026.08.21")
        self.assertEqual(binding.source_identity, "catalog:demo-model")

    def test_v2_manager_confirm_boundaries_reject_stale_binding_before_verify_or_write(self) -> None:
        from src.services.component_installer.reuse_executor import ExistingInstallReuseExecutor

        target = self.paths.models_root / "demo-model" / "demo.bin"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"model")

        for field, changed in (("version", "2026.08.22"), ("fingerprint", "b" * 64), ("model_source", "catalog:changed-model")):
            installer, current = self._v2_installer()
            initial = CatalogBindingContext.for_v2(catalog_version=current["version"], catalog_fingerprint=current["fingerprint"], source_identity=current["model_source"])
            plan = installer.plan_reuse("demo-model", component_type="model", catalog_binding=initial)
            receipt_path = self.paths.config_root / "component_install_receipts.json"
            receipt_path.write_bytes(b"prior-receipt\n")
            before = receipt_path.read_bytes()
            current[field] = changed
            with patch.object(DeepComponentVerifier, "verify", side_effect=AssertionError("stale binding reached deep verifier")):
                result = installer.confirm_reuse(plan["plan_id"], confirmed=True)
            self.assertEqual(result["status"], "conflict")
            self.assertEqual(result["code"], "stale_binding")
            self.assertNotIn(changed, json.dumps(result))
            self.assertEqual(receipt_path.read_bytes(), before)

        installer, current = self._v2_installer()
        initial = CatalogBindingContext.for_v2(catalog_version=current["version"], catalog_fingerprint=current["fingerprint"], source_identity=current["model_source"])
        verify_plan = installer.plan_verify("demo-model", component_type="model", catalog_binding=initial)
        current["fingerprint"] = "c" * 64
        with patch.object(DeepComponentVerifier, "verify", side_effect=AssertionError("stale binding reached deep verifier")):
            verify_result = installer.confirm_verify(verify_plan["plan_id"], confirmed=True)
        self.assertEqual(verify_result["code"], "stale_binding")

        installer, current = self._v2_installer()
        initial = CatalogBindingContext.for_v2(catalog_version=current["version"], catalog_fingerprint=current["fingerprint"], source_identity=current["model_source"])
        maintenance_plan = installer.plan_maintenance("demo-model", action="repair", catalog_binding=initial)
        current["version"] = "2026.08.23"
        with patch.object(DeepComponentVerifier, "verify", side_effect=AssertionError("stale binding reached deep verifier")):
            maintenance_result = installer.confirm_maintenance(maintenance_plan["plan_id"], confirmed=True)
        self.assertEqual(maintenance_result["code"], "stale_binding")

        installer, current = self._v2_installer()
        initial = CatalogBindingContext.for_v2(catalog_version=current["version"], catalog_fingerprint=current["fingerprint"], source_identity=current["model_source"])
        install_plan = installer.plan_install("demo-model", component_type="model", catalog_binding=initial)
        current["fingerprint"] = "d" * 64
        with patch("src.services.component_installer.manager.TrustedDownloader", side_effect=AssertionError("stale binding reached downloader")):
            apply_result = installer.apply_plan(install_plan["plan_id"], confirmed=True)
        self.assertEqual(apply_result["code"], "stale_binding")

        installer, current = self._v2_installer()
        initial = CatalogBindingContext.for_v2(catalog_version=current["version"], catalog_fingerprint=current["fingerprint"], source_identity=current["model_source"])
        reuse_plan = installer.plan_reuse("demo-model", component_type="model", catalog_binding=initial)
        current["model_source"] = "catalog:executor-stale"
        with patch.object(DeepComponentVerifier, "verify", side_effect=AssertionError("stale binding reached deep verifier")):
            direct_result = ExistingInstallReuseExecutor(paths=self.paths, manager=installer).apply(reuse_plan, confirmed=True)
        self.assertEqual(direct_result["code"], "stale_binding")

        installer, current = self._v2_installer()
        initial = CatalogBindingContext.for_v2(catalog_version=current["version"], catalog_fingerprint=current["fingerprint"], source_identity=current["model_source"])
        missing_context_plan = installer.plan_reuse("demo-model", component_type="model", catalog_binding=initial)
        installer._catalog_binding_provider = None
        missing_context_result = installer.confirm_reuse(missing_context_plan["plan_id"], confirmed=True)
        self.assertEqual(missing_context_result["code"], "stale_binding")

    def test_direct_model_and_runtime_verify_revalidate_current_binding(self) -> None:
        installer, current = self._v2_installer()
        model = installer.model_manager
        runtime = installer.runtime_manager
        model_target = self.paths.models_root / "demo-model" / "demo.bin"
        model_target.parent.mkdir(parents=True, exist_ok=True)
        model_target.write_bytes(b"model")
        runtime_target = self.paths.runtime_root / "bin" / "demo.exe"
        runtime_target.parent.mkdir(parents=True, exist_ok=True)
        runtime_target.write_bytes(b"runtime")
        model_context = CatalogBindingContext.for_v2(catalog_version=current["version"], catalog_fingerprint=current["fingerprint"], source_identity=current["model_source"])
        runtime_context = CatalogBindingContext.for_v2(catalog_version=current["version"], catalog_fingerprint=current["fingerprint"], source_identity=current["runtime_source"])

        valid_model = model.verify("demo-model", catalog_binding=model_context)
        valid_runtime = runtime.verify("demo-runtime", catalog_binding=runtime_context)
        self.assertEqual(valid_model["status"], "completed")
        self.assertEqual(valid_runtime["status"], "completed")
        self.assertFalse(valid_model["operational"])
        self.assertFalse(valid_runtime["operational"])

        stale_cases = (
            (model, "demo-model", model_context, "version", "2026.08.22"),
            (model, "demo-model", model_context, "fingerprint", "b" * 64),
            (model, "demo-model", model_context, "model_source", "catalog:changed-model"),
            (runtime, "demo-runtime", runtime_context, "runtime_source", "catalog:runtime-digest"),
        )
        for manager, component_id, supplied, field, changed in stale_cases:
            receipt_path = self.paths.config_root / "component_install_receipts.json"
            receipt_path.write_bytes(b"direct-manager-prior\n")
            before = receipt_path.read_bytes()
            current[field] = changed
            with patch.object(DeepComponentVerifier, "verify", side_effect=AssertionError("stale binding reached deep verifier")), patch("src.services.component_installer.receipts.write_component_receipt", side_effect=AssertionError("stale binding reached receipt writer")):
                result = manager.verify(component_id, catalog_binding=supplied)
            self.assertEqual(result["status"], "conflict")
            self.assertEqual(result["code"], "stale_binding")
            self.assertNotIn(str(changed), json.dumps(result))
            self.assertNotIn(str(self.temp.name), json.dumps(result))
            self.assertEqual(receipt_path.read_bytes(), before)
            current[field] = "2026.08.21" if field == "version" else "a" * 64 if field == "fingerprint" else "catalog:demo-model" if field == "model_source" else None

        v1_context = CatalogBindingContext.for_v1(component_type="model", record=model._record("demo-model"), catalog_fingerprint=model.catalog_fingerprint)
        receipt_path = self.paths.config_root / "component_install_receipts.json"
        receipt_path.write_bytes(b"schema-family-prior\n")
        with patch.object(DeepComponentVerifier, "verify", side_effect=AssertionError("schema mismatch reached deep verifier")):
            schema_result = model.verify("demo-model", catalog_binding=v1_context)
        self.assertEqual(schema_result["code"], "stale_binding")
        self.assertEqual(receipt_path.read_bytes(), b"schema-family-prior\n")

        model._catalog_binding_provider = None
        with patch.object(DeepComponentVerifier, "verify", side_effect=AssertionError("missing context reached deep verifier")):
            missing_result = model.verify("demo-model", catalog_binding=model_context)
        self.assertEqual(missing_result["code"], "stale_binding")

    def test_direct_model_and_runtime_inspect_revalidate_current_binding(self) -> None:
        installer, current = self._v2_installer()
        model = installer.model_manager
        runtime = installer.runtime_manager
        model_target = self.paths.models_root / "demo-model" / "demo.bin"
        model_target.parent.mkdir(parents=True, exist_ok=True)
        model_target.write_bytes(b"model")
        runtime_target = self.paths.runtime_root / "bin" / "demo.exe"
        runtime_target.parent.mkdir(parents=True, exist_ok=True)
        runtime_target.write_bytes(b"runtime")
        model_context = CatalogBindingContext.for_v2(catalog_version=current["version"], catalog_fingerprint=current["fingerprint"], source_identity=current["model_source"])
        runtime_context = CatalogBindingContext.for_v2(catalog_version=current["version"], catalog_fingerprint=current["fingerprint"], source_identity=current["runtime_source"])

        self.assertEqual(model.verify("demo-model", catalog_binding=model_context)["status"], "completed")
        self.assertEqual(runtime.verify("demo-runtime", catalog_binding=runtime_context)["status"], "completed")
        inspected_model = model.inspect("demo-model", catalog_binding=model_context)
        inspected_runtime = runtime.inspect("demo-runtime", catalog_binding=runtime_context)
        self.assertEqual(inspected_model["status"], "INSTALLED_VERIFIED")
        self.assertEqual(inspected_runtime["status"], "INSTALLED_UNVERIFIED")
        self.assertEqual(inspected_model["catalog_schema"], "v7-production-catalog.v2")
        self.assertEqual(inspected_runtime["catalog_schema"], "v7-production-catalog.v2")
        self.assertFalse(inspected_model["operational"])
        self.assertFalse(inspected_runtime["operational"])
        self.assertEqual(model.snapshot()["records"][0]["catalog_schema"], "v7-production-catalog.v2")
        runtime_snapshot = runtime.snapshot()
        self.assertEqual(runtime_snapshot["records"][0]["catalog_schema"], "v7-production-catalog.v2")

        original = dict(current)
        receipt_path = self.paths.config_root / "component_install_receipts.json"
        for manager, component_id, supplied, field, changed in (
            (model, "demo-model", model_context, "version", "2026.08.22"),
            (model, "demo-model", model_context, "fingerprint", "b" * 64),
            (model, "demo-model", model_context, "model_source", None),
            (runtime, "demo-runtime", runtime_context, "runtime_source", "catalog:runtime-digest"),
        ):
            before = receipt_path.read_bytes()
            current[field] = changed
            with patch.object(FastComponentInspector, "inspect", side_effect=AssertionError("stale binding reached fast inspector")):
                result = manager.inspect(component_id, catalog_binding=supplied)
            self.assertEqual(result["status"], "conflict")
            self.assertEqual(result["code"], "stale_binding")
            self.assertEqual(result["execution"], "not_run")
            self.assertTrue(result["dry_run"])
            self.assertNotEqual(result.get("state"), "INSTALLED_VERIFIED")
            self.assertNotIn(str(self.temp.name), json.dumps(result))
            if changed is not None:
                self.assertNotIn(changed, json.dumps(result))
            self.assertEqual(receipt_path.read_bytes(), before)
            current.update(original)

        provider = model._catalog_binding_provider
        model._catalog_binding_provider = None
        before = receipt_path.read_bytes()
        with patch.object(FastComponentInspector, "inspect", side_effect=AssertionError("unsupported V2 reached fast inspector")):
            unsupported = model.inspect("demo-model", catalog_binding=model_context)
        self.assertEqual(unsupported["status"], "conflict")
        self.assertEqual(unsupported["code"], "catalog_schema_unsupported")
        self.assertEqual(unsupported["state"], "UNAVAILABLE")
        self.assertEqual(unsupported["execution"], "not_run")
        self.assertTrue(unsupported["dry_run"])
        self.assertEqual(receipt_path.read_bytes(), before)
        self.assertNotIn(str(self.temp.name), json.dumps(unsupported))
        model._catalog_binding_provider = provider

        legacy_model = ModelManager(paths=self.paths, catalog_path=model.catalog_path)
        legacy_runtime = RuntimeManager(paths=self.paths, catalog_path=runtime.catalog_path)
        legacy_model_result = legacy_model.inspect("demo-model")
        legacy_runtime_result = legacy_runtime.inspect("demo-runtime")
        self.assertEqual(legacy_model_result["catalog_schema"], "model-catalog.v1")
        self.assertEqual(legacy_runtime_result["catalog_schema"], "runtime-catalog.v1")
        self.assertNotEqual(legacy_model_result["status"], "conflict")
        self.assertNotEqual(legacy_runtime_result["status"], "conflict")
        self.assertEqual(legacy_model_result["execution"], "not_run")
        self.assertEqual(legacy_runtime_result["execution"], "not_run")

        legacy_model_binding = CatalogBindingContext.for_v1(
            component_type="model", record=legacy_model._record("demo-model"), catalog_fingerprint=legacy_model.catalog_fingerprint
        )
        legacy_runtime_record = next(item for item in legacy_runtime._records if item["runtime_id"] == "demo-runtime")
        legacy_runtime_binding = CatalogBindingContext.for_v1(
            component_type="runtime", record=legacy_runtime_record, catalog_fingerprint=legacy_runtime._catalog_fingerprint()
        )
        for manager, component_id, binding in (
            (legacy_model, "demo-model", legacy_model_binding),
            (legacy_runtime, "demo-runtime", legacy_runtime_binding),
        ):
            stale = CatalogBindingContext(
                catalog_schema=binding.catalog_schema,
                catalog_revision=binding.catalog_revision,
                catalog_fingerprint="c" * 64,
                source_identity=binding.source_identity,
            )
            with patch.object(FastComponentInspector, "inspect", side_effect=AssertionError("stale V1 reached fast inspector")):
                result = manager.inspect(component_id, catalog_binding=stale)
            self.assertEqual(result["status"], "conflict")
            self.assertEqual(result["code"], "stale_binding")
            self.assertEqual(result["state"], "UNAVAILABLE")
            self.assertEqual(result["execution"], "not_run")
            self.assertTrue(result["dry_run"])

    def test_v2_lifecycle_confirmations_reject_current_catalog_drift(self) -> None:
        from src.services.productization.lifecycle import ComponentLifecycle

        model = {
            "model_id": "demo-model", "display_name": "V2 demo model", "kind": "model", "category": "fixture",
            "provider": "fixture", "version": "1", "revision": "model-r1", "runtime_id": "demo-runtime",
            "modules": ["demo"], "files": [{"relative_path": "weights/demo.bin", "verification": "unverified"}],
            "source_identity": "catalog:demo-model", "root_class": "models_root",
            "disposition": "MANUAL_IMPORT_ONLY", "install_strategy": "manual_import",
        }
        runtime = {
            "runtime_id": "demo-runtime", "display_name": "V2 demo runtime", "kind": "tool", "provider": "fixture",
            "version": "1", "revision": "runtime-r1", "root_class": "runtime_root", "required_leaves": ["bin/demo.exe"],
            "modules": ["demo"], "source_identity": None, "disposition": "REFERENCE_EXISTING", "install_strategy": "reference_existing",
        }
        catalog = SimpleNamespace(
            catalog_schema_version="v7-production-catalog.v2", catalog_version="2026.08.21", fingerprint="e" * 64,
            models={"demo-model": model}, runtimes={"demo-runtime": runtime},
        )
        lifecycle = ComponentLifecycle(paths=self.paths, catalog=catalog)
        one_click = lifecycle.plan_one_click("demo-model")
        catalog.catalog_version = "2026.08.22"
        result = lifecycle.confirm(one_click["plan_id"], confirmed=True)
        self.assertEqual(result["code"], "stale_binding")
        self.assertNotIn("2026.08.22", json.dumps(result))

        catalog.catalog_version = "2026.08.21"
        maintenance = lifecycle.plan_maintenance("demo-model", "repair")
        catalog.fingerprint = "f" * 64
        result = lifecycle.confirm(maintenance["plan_id"], confirmed=True)
        self.assertEqual(result["code"], "stale_binding")

        catalog.fingerprint = "e" * 64
        maintenance_source = lifecycle.plan_maintenance("demo-model", "repair")
        catalog.models["demo-model"]["source_identity"] = "catalog:changed-model"
        result = lifecycle.confirm(maintenance_source["plan_id"], confirmed=True)
        self.assertEqual(result["code"], "stale_binding")


if __name__ == "__main__":
    unittest.main()
