"""Static/unit coverage for the V7 managed-component verification contract."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.platform.paths import ComponentPathError, HubPaths, resolve_component_leaf, resolve_component_root
from src.services.component_installer.receipts import CatalogBindingContext, ReceiptConflict, ReceiptError, read_receipts, write_component_receipt
from src.services.component_installer.verification import DeepComponentVerifier, FastComponentInspector, stream_sha256


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


if __name__ == "__main__":
    unittest.main()
