from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.platform.paths import HubPaths
from src.services.operational_closure import evidence
from src.services.productization import catalog as catalog_module
from src.services.productization.catalog import ProductionCatalog


ROOT = Path(__file__).resolve().parents[1]


class RuntimeEvidenceCatalogBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.paths = HubPaths(app_root=root / "app", data_root=root / "data")
        self.paths.config_root.mkdir(parents=True, exist_ok=True)
        self.paths.runtime_root.mkdir(parents=True, exist_ok=True)
        self.record = {
            "runtime_id": "demo-runtime",
            "revision": "runtime-r1",
            "install_strategy": "reference_existing",
            "source_identity": "catalog:demo-runtime",
            "required_leaves": ["bin/demo.exe"],
        }
        target = self.paths.runtime_root / "bin" / "demo.exe"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"runtime")
        self.binding = self._binding()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _binding(
        self,
        *,
        schema: str = "v7-production-catalog.v2",
        version: str | None = "2026.08.21",
        revision: str | None = "2026.08.21",
        fingerprint: str = "a" * 64,
        source: str | None = "catalog:demo-runtime",
        runtime_id: str = "demo-runtime",
        record_revision: str = "runtime-r1",
        install_strategy: str = "reference_existing",
    ) -> dict[str, object]:
        binding = evidence.runtime_catalog_binding(
            catalog_schema=schema,
            catalog_version=version,
            catalog_revision=revision,
            catalog_fingerprint=fingerprint,
            source_identity=source,
            runtime_id=runtime_id,
            record_revision=record_revision,
            install_strategy=install_strategy,
        )
        self.assertIsNotNone(binding)
        assert binding is not None
        return binding

    def _valid_record(self, *, timestamp: int = 100) -> dict[str, object]:
        value = {
            "schema_version": evidence.EVIDENCE_SCHEMA_V2,
            "component_id": "demo-runtime",
            "runtime_id": "demo-runtime",
            "status": "completed",
            "execution": "completed",
            **self.binding,
            "runtime_fingerprint": evidence.runtime_fingerprint(self.paths, self.record, binding=self.binding),
            "smoke_id": "fixture-smoke-v2",
            "smoked_at": timestamp,
            "details": {"exit_code": 0, "artifact_verified": True},
        }
        return value

    def _write_v2(self, record: object) -> None:
        (self.paths.config_root / "component_runtime_evidence.v2.json").write_text(
            json.dumps({"schema_version": evidence.EVIDENCE_SCHEMA_V2, "records": {"demo-runtime": record}}, ensure_ascii=True),
            encoding="utf-8",
        )

    def test_exact_v2_binding_and_current_leaves_are_required_for_promotion(self) -> None:
        self._write_v2(self._valid_record())
        self.assertTrue(evidence.runtime_evidence_passed(self.paths, "demo-runtime", self.record, binding=self.binding, now=100))
        self.assertFalse(evidence.runtime_evidence_passed(self.paths, "other-runtime", self.record, binding=self.binding, now=100))
        self.assertFalse(evidence.runtime_evidence_passed(self.paths, "demo-runtime", self.record))
        self.assertNotIn(str(self.paths.runtime_root), json.dumps(evidence.read_runtime_evidence(self.paths), ensure_ascii=False))

    def test_catalog_binding_mutations_never_promote(self) -> None:
        mutations = {
            "catalog_schema": "v7-production-catalog.v1",
            "catalog_version": "2026.08.22",
            "catalog_revision": "2026.08.22",
            "catalog_fingerprint": "b" * 64,
            "source_identity": "catalog:other-runtime",
            "runtime_id": "other-runtime",
            "record_revision": "runtime-r2",
            "install_strategy": "manual_install",
        }
        for field, replacement in mutations.items():
            with self.subTest(field=field):
                candidate = self._valid_record()
                candidate[field] = replacement
                self._write_v2(candidate)
                self.assertFalse(evidence.runtime_evidence_passed(self.paths, "demo-runtime", self.record, binding=self.binding, now=100))

    def test_record_and_leaf_drift_and_stale_timestamp_never_promote(self) -> None:
        self._write_v2(self._valid_record())
        forged_binding = self._binding(record_revision="runtime-r2")
        forged_evidence = {
            **self._valid_record(),
            **forged_binding,
            "runtime_fingerprint": evidence.runtime_fingerprint(self.paths, {**self.record, "revision": "runtime-r2"}, binding=forged_binding),
        }
        self._write_v2(forged_evidence)
        self.assertFalse(evidence.runtime_evidence_passed(self.paths, "demo-runtime", self.record, binding=forged_binding, now=100))
        self._write_v2(self._valid_record())
        self.assertFalse(
            evidence.runtime_evidence_passed(
                self.paths,
                "demo-runtime",
                {**self.record, "revision": "runtime-r2"},
                binding=self._binding(record_revision="runtime-r2"),
                now=100,
            )
        )
        target = self.paths.runtime_root / "bin" / "demo.exe"
        target.write_bytes(b"runtime-drift")
        self.assertFalse(evidence.runtime_evidence_passed(self.paths, "demo-runtime", self.record, binding=self.binding, now=100))
        target.write_bytes(b"runtime")
        with patch.object(evidence, "_is_reparse", return_value=True):
            self.assertFalse(evidence.runtime_evidence_passed(self.paths, "demo-runtime", self.record, binding=self.binding, now=100))
        self._write_v2(self._valid_record(timestamp=100))
        self.assertFalse(evidence.runtime_evidence_passed(self.paths, "demo-runtime", self.record, binding=self.binding, now=100 + 24 * 60 * 60 + 1))

    def test_explicit_null_source_identity_is_valid_but_digest_to_null_drift_is_not(self) -> None:
        null_record = {**self.record, "source_identity": None}
        null_binding = self._binding(source=None)
        null_evidence = {
            **self._valid_record(),
            **null_binding,
            "runtime_fingerprint": evidence.runtime_fingerprint(self.paths, null_record, binding=null_binding),
        }
        self._write_v2(null_evidence)
        self.assertTrue(evidence.runtime_evidence_passed(self.paths, "demo-runtime", null_record, binding=null_binding, now=100))
        self._write_v2({**null_evidence, **self.binding})
        self.assertFalse(evidence.runtime_evidence_passed(self.paths, "demo-runtime", self.record, binding=self.binding, now=100))

    def test_legacy_v1_is_readable_but_never_operational(self) -> None:
        saved = evidence.record_runtime_smoke(
            self.paths,
            "demo-runtime",
            self.record,
            outcome="completed",
            smoke_id="legacy-v1",
            timestamp=100,
        )
        self.assertEqual(saved["status"], "saved")
        self.assertEqual(evidence.read_runtime_evidence(self.paths, schema=evidence.EVIDENCE_SCHEMA_V1)["schema_version"], evidence.EVIDENCE_SCHEMA_V1)
        self.assertFalse(evidence.runtime_evidence_passed(self.paths, "demo-runtime", self.record, binding=self.binding, now=100))

    def test_malformed_duplicate_unknown_and_unsafe_v2_records_fail_closed(self) -> None:
        malformed = [
            {"status": "completed"},
            {**self._valid_record(), "unexpected": "ignored"},
            {**self._valid_record(), "source_identity": r"C:\private\runtime"},
            {**self._valid_record(), "details": {"unknown": "value"}},
        ]
        for candidate in malformed:
            with self.subTest(candidate=candidate):
                self._write_v2(candidate)
                self.assertFalse(evidence.runtime_evidence_passed(self.paths, "demo-runtime", self.record, binding=self.binding, now=100))
        (self.paths.config_root / "component_runtime_evidence.v2.json").write_text(
            json.dumps({"schema_version": evidence.EVIDENCE_SCHEMA_V2, "records": {"demo-runtime": self._valid_record()}, "unknown": "ignored"}),
            encoding="utf-8",
        )
        self.assertFalse(evidence.runtime_evidence_passed(self.paths, "demo-runtime", self.record, binding=self.binding, now=100))
        duplicate = '{"schema_version":"component-runtime-evidence.v2","records":{"demo-runtime":{"status":"completed","status":"completed"}}}'
        (self.paths.config_root / "component_runtime_evidence.v2.json").write_text(duplicate, encoding="utf-8")
        self.assertFalse(evidence.runtime_evidence_passed(self.paths, "demo-runtime", self.record, binding=self.binding, now=100))
        self.assertNotIn("private", json.dumps(evidence.read_runtime_evidence(self.paths), ensure_ascii=False))

    def test_v1_catalog_identity_can_bind_v2_evidence_without_v1_promotion(self) -> None:
        v1_binding = self._binding(
            schema="v7-production-catalog.v1",
            version=None,
            revision=None,
            source=None,
        )
        record = {**self.record, "source_identity": None}
        candidate = {
            **self._valid_record(),
            **v1_binding,
            "runtime_fingerprint": evidence.runtime_fingerprint(self.paths, record, binding=v1_binding),
        }
        self._write_v2(candidate)
        self.assertTrue(evidence.runtime_evidence_passed(self.paths, "demo-runtime", record, binding=v1_binding, now=100))

    def test_catalog_inspect_passes_current_binding_and_preserves_static_projection(self) -> None:
        catalog = ProductionCatalog(
            paths=self.paths,
            catalog_path=ROOT / "Config" / "v7_production_catalog.example.json",
        )
        runtime_id = sorted(catalog.runtimes)[0]
        record = catalog.runtimes[runtime_id]
        root = catalog._root_for_runtime(record)
        for relative in record["required_leaves"]:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"fixture")
        with patch.object(catalog_module, "runtime_evidence_passed", return_value=True) as passed, patch.object(catalog_module, "runtime_fingerprint", return_value="f" * 64):
            projected = catalog.inspect_runtime(runtime_id)
        passed.assert_called_once()
        binding = passed.call_args.kwargs["binding"]
        self.assertEqual(binding["runtime_id"], runtime_id)
        self.assertEqual(binding["catalog_schema"], catalog.catalog_schema_version)
        self.assertEqual(binding["catalog_version"], catalog.catalog_version)
        self.assertEqual(binding["catalog_revision"], catalog.catalog_version)
        self.assertEqual(binding["catalog_fingerprint"], catalog.fingerprint)
        self.assertEqual(binding["record_revision"], record["revision"])
        self.assertEqual(binding["install_strategy"], record["install_strategy"])
        self.assertEqual(projected["status"], "OPERATIONAL")
        self.assertTrue(projected["operational"])
        self.assertEqual(projected["execution"], "not_run")
        self.assertTrue(projected["dry_run"])
        self.assertEqual(projected["runtime_fingerprint"], "f" * 64)

        with patch.object(catalog_module, "runtime_evidence_passed", return_value=False), patch.object(catalog_module, "runtime_fingerprint", return_value="e" * 64):
            stale = catalog.inspect_runtime(runtime_id)
        self.assertEqual(stale["status"], "INSTALLED_UNVERIFIED")
        self.assertFalse(stale["operational"])
        self.assertEqual(stale["execution"], "not_run")
        self.assertTrue(stale["dry_run"])
        self.assertNotIn(str(self.paths.runtime_root), json.dumps(stale, ensure_ascii=False))

    def test_evidence_module_has_no_runtime_or_network_invocation_boundary(self) -> None:
        source = (ROOT / "src" / "services" / "operational_closure" / "evidence.py").read_text(encoding="utf-8")
        self.assertNotIn("subprocess", source)
        self.assertNotIn("urlopen", source)
        self.assertNotIn("run_hidden", source)
        self.assertNotIn("Popen", source)


if __name__ == "__main__":
    unittest.main()
