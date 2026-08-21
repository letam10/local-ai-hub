"""Strict, source-only regressions for the V7 production catalog v2 contract."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from src.services.productization.catalog import (
    ProductionCatalog,
    ProductionCatalogError,
    V2_MODEL_IDS,
    V2_RUNTIME_IDS,
    load_production_catalog,
)


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "Config" / "v7_production_catalog.example.json"


class V7ProductionCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _raw(self) -> dict:
        return json.loads(EXAMPLE.read_text(encoding="utf-8"))

    def _path(self, name: str = "catalog.json") -> Path:
        return self.root / name

    def _write(self, value: object, name: str = "catalog.json") -> Path:
        path = self._path(name)
        path.write_text(json.dumps(value, ensure_ascii=True, allow_nan=False), encoding="utf-8")
        return path

    def _assert_rejected(self, value: object, code_fragment: str | None = None) -> None:
        with self.assertRaises(ProductionCatalogError) as context:
            load_production_catalog(self._write(value))
        if code_fragment:
            self.assertIn(code_fragment, str(context.exception))

    def test_example_has_exact_closed_counts_refs_and_no_model_auto_install(self) -> None:
        raw = self._raw()
        self.assertEqual(set(raw), {"schema_version", "catalog_version", "models", "runtimes"})
        self.assertEqual(raw["schema_version"], "v7-production-catalog.v2")
        self.assertEqual(raw["catalog_version"], "2026.08.21")
        self.assertEqual(len(raw["models"]), 14)
        self.assertEqual(len(raw["runtimes"]), 15)
        self.assertEqual({item["model_id"] for item in raw["models"]}, V2_MODEL_IDS)
        self.assertEqual({item["runtime_id"] for item in raw["runtimes"]}, V2_RUNTIME_IDS)
        self.assertEqual([item["runtime_id"] for item in raw["runtimes"] if item["disposition"] == "AUTO_INSTALL_READY"], ["ffmpeg"])
        self.assertFalse(any(item["disposition"] == "AUTO_INSTALL_READY" for item in raw["models"]))
        runtime_ids = {item["runtime_id"] for item in raw["runtimes"]}
        for item in raw["models"]:
            self.assertEqual(item["kind"], "model")
            self.assertIn(item["runtime_id"], runtime_ids)
            self.assertIn("primary_source", item)
            self.assertIn("trusted_fallback_sources", item)
            self.assertIn("source_identity", item)
            self.assertIn("source_verification", item)
            self.assertIn("latest_upstream_revision", item)
            self.assertIn("latest_supported_revision", item)
            for leaf in item["files"]:
                self.assertNotEqual(leaf.get("size_bytes"), 0)
                if leaf["verification"] == "unverified":
                    self.assertNotIn("size_bytes", leaf)
                    self.assertNotIn("sha256", leaf)
        catalog = ProductionCatalog(catalog_path=EXAMPLE)
        snapshot = catalog.snapshot()
        self.assertEqual(snapshot["counts"]["models"], 14)
        self.assertEqual(snapshot["counts"]["runtimes"], 15)
        self.assertEqual(snapshot["execution"], "not_run")
        self.assertTrue(snapshot["dry_run"])
        self.assertEqual(snapshot["catalog_version"], "2026.08.21")

    def test_schema_is_closed_and_declares_exact_counts(self) -> None:
        schema = json.loads((ROOT / "Config" / "v7_production_catalog.v2.schema.json").read_text(encoding="utf-8"))
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["models"]["minItems"], 14)
        self.assertEqual(schema["properties"]["models"]["maxItems"], 14)
        self.assertEqual(schema["properties"]["runtimes"]["minItems"], 15)
        self.assertEqual(schema["properties"]["runtimes"]["maxItems"], 15)
        self.assertFalse(schema["$defs"]["model"]["additionalProperties"])
        self.assertFalse(schema["$defs"]["runtime"]["additionalProperties"])
        self.assertFalse(schema["$defs"]["source"]["additionalProperties"])

    def test_duplicate_keys_and_nonfinite_numbers_are_rejected(self) -> None:
        duplicate = '{"schema_version":"v7-production-catalog.v2","schema_version":"v7-production-catalog.v2","catalog_version":"2026.08.21","models":[],"runtimes":[]}'
        duplicate_path = self._path("duplicate.json")
        duplicate_path.write_text(duplicate, encoding="utf-8")
        with self.assertRaisesRegex(ProductionCatalogError, "duplicate_json_key"):
            load_production_catalog(duplicate_path)
        nonfinite = '{"schema_version":"v7-production-catalog.v2","catalog_version":"2026.08.21","models":[],"runtimes":[],"marker":NaN}'
        nonfinite_path = self._path("nonfinite.json")
        nonfinite_path.write_text(nonfinite, encoding="utf-8")
        with self.assertRaisesRegex(ProductionCatalogError, "nonfinite_json_number"):
            load_production_catalog(nonfinite_path)
        overflow = '{"schema_version":"v7-production-catalog.v2","catalog_version":"2026.08.21","models":[],"runtimes":[],"marker":1e999}'
        overflow_path = self._path("overflow.json")
        overflow_path.write_text(overflow, encoding="utf-8")
        with self.assertRaisesRegex(ProductionCatalogError, "nonfinite_json_number"):
            load_production_catalog(overflow_path)

    def test_unknown_fields_duplicate_ids_and_dangling_runtime_are_rejected(self) -> None:
        unknown = self._raw()
        unknown["models"][0]["unknown_field"] = "reject"
        self._assert_rejected(unknown, "invalid_model_record_fields")
        duplicate = self._raw()
        duplicate["models"][1]["model_id"] = duplicate["models"][0]["model_id"]
        self._assert_rejected(duplicate, "catalog_id_set_mismatch")
        dangling = self._raw()
        dangling["models"][0]["runtime_id"] = "missing-runtime"
        self._assert_rejected(dangling, "catalog_runtime_reference_missing")

    def test_unsafe_leaves_credentials_and_source_identity_mismatch_are_rejected(self) -> None:
        unsafe_leaf = self._raw()
        unsafe_leaf["models"][0]["files"][0]["relative_path"] = "../escape.bin"
        self._assert_rejected(unsafe_leaf, "unsafe_catalog_leaf")
        credential_url = self._raw()
        credential_url["models"][1]["primary_source"]["url"] = "https://user:password@example.invalid/model"
        self._assert_rejected(credential_url, "unsafe_source_url")
        mismatch = self._raw()
        mismatch["models"][1]["primary_source"]["canonical_identity"] = "different-identity"
        self._assert_rejected(mismatch, "source_identity_mismatch")

    def test_integrity_zero_sentinel_uppercase_hash_and_unverified_fields_fail_closed(self) -> None:
        zero = self._raw()
        zero["models"][1]["files"][0]["size_bytes"] = 0
        self._assert_rejected(zero, "invalid_model_file_integrity")
        uppercase = self._raw()
        uppercase["models"][1]["files"][0]["sha256"] = uppercase["models"][1]["files"][0]["sha256"].upper()
        self._assert_rejected(uppercase, "invalid_model_file_integrity")
        unverified = self._raw()
        unverified["models"][0]["files"][0]["size_bytes"] = 123
        self._assert_rejected(unverified, "unverified_model_file_must_omit_integrity")
        runtime_integrity = self._raw()
        runtime_integrity["runtimes"][1]["integrity"]["size_bytes"] = 0
        self._assert_rejected(runtime_integrity, "unverified_runtime_must_omit_integrity")

    def test_unknown_schema_does_not_silently_load_as_v2_and_v1_stays_explicit(self) -> None:
        unknown = self._raw()
        unknown["schema_version"] = "v7-production-catalog.v3"
        self._assert_rejected(unknown, "unsupported_catalog_schema")
        legacy = {
            "schema_version": "v7-production-catalog.v1",
            "models": [{
                "model_id": "legacy-model", "display_name": "Legacy", "category": "Other", "provider": "fixture",
                "version": "1", "revision": "1", "official_source": "local", "source_type": "fixture",
                "disposition": "MANUAL_IMPORT_ONLY", "modules": ["fixture"], "runtime_id": "legacy-runtime",
                "files": [{"relative_path": "model.bin", "size_bytes": 0}], "estimated_download_size": 0,
                "estimated_disk_size": 0,
            }],
            "runtimes": [{
                "runtime_id": "legacy-runtime", "display_name": "Legacy runtime", "kind": "python",
                "version": "1", "revision": "1", "root_class": "environments_root",
                "required_leaves": ["legacy/Scripts/python.exe"], "modules": ["fixture"],
                "disposition": "REFERENCE_EXISTING", "install_strategy": "reference_existing",
            }],
        }
        loaded = load_production_catalog(self._write(legacy, "legacy.json"))
        self.assertEqual(loaded["schema_version"], "v7-production-catalog.v1")
        self.assertEqual(loaded["models"][0]["model_id"], "legacy-model")


if __name__ == "__main__":
    unittest.main()
