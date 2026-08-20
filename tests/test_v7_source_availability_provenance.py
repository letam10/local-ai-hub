"""Static contract tests for V7 source-availability cache provenance."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.platform.paths import HubPaths
from src.services.operational_closure.source_availability import (
    SourceAvailabilityService,
    source_binding_fingerprint,
    source_status_projection,
)
from src.services.operational_closure.update_service import UpdateResolver
from src.services.productization.catalog import ProductionCatalog


class _CapturingSourceService:
    def __init__(self) -> None:
        self.bindings: list[dict[str, object] | None] = []

    def check(self, component_id: str, record: dict[str, object], **kwargs: object) -> dict[str, object]:
        binding = kwargs.get("binding")
        self.bindings.append(binding if isinstance(binding, dict) else None)
        return {
            "status": "UNKNOWN",
            "source_identity": None,
            "selected_source": None,
            "checked_at": None,
            "expires_at": None,
            "reason_code": "not_checked",
            "retry_after_seconds": None,
        }


class V7SourceAvailabilityProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.app = root / "app"
        self.data = root / "data"
        (self.app / "Config").mkdir(parents=True)
        self.paths = HubPaths(app_root=self.app, data_root=self.data)
        self.record: dict[str, object] = {
            "model_id": "demo-model",
            "provider": "fixture-provider",
            "kind": "model",
            "revision": "r1",
            "install_strategy": "portable_archive",
            "source_identity": "demo-artifact-r1",
            "primary_source": {
                "provider": "fixture-provider",
                "kind": "https",
                "url": "https://example.invalid/demo-r1.zip",
                "canonical_identity": "demo-artifact-r1",
            },
            "trusted_fallback_sources": [{
                "provider": "fixture-mirror",
                "kind": "https",
                "url": "https://mirror.invalid/demo-r1.zip",
                "canonical_identity": "demo-artifact-r1",
            }],
        }
        self.binding: dict[str, object] = {
            "catalog_schema": "v7-production-catalog.v2",
            "catalog_version": "2026.08.01",
            "catalog_revision": "2026.08.1",
            "catalog_fingerprint": "a" * 64,
            "source_identity": "demo-artifact-r1",
            "component_id": "demo-model",
            "component_type": "model",
            "record_revision": "r1",
            "install_strategy": "portable_archive",
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _service(self) -> SourceAvailabilityService:
        return SourceAvailabilityService(paths=self.paths, ttl_seconds=600)

    def _seed_available(self, service: SourceAvailabilityService) -> dict[str, object]:
        return service.check(
            "demo-model",
            self.record,
            force=True,
            now=100,
            binding=self.binding,
            probe=lambda _entry: ("AVAILABLE", "fixture-only", None),
        )

    def test_exact_binding_reuses_unexpired_cache_without_probe(self) -> None:
        service = self._service()
        first = self._seed_available(service)
        self.assertEqual(first["status"], "AVAILABLE")
        reused = service.check(
            "demo-model",
            self.record,
            force=False,
            now=101,
            binding=self.binding,
            probe=lambda _entry: (_ for _ in ()).throw(AssertionError("non-force cache called probe")),
        )
        self.assertEqual(reused["status"], "AVAILABLE")
        self.assertEqual(reused["source_identity"], first["source_identity"])
        raw_cache = service.cache_path.read_text(encoding="utf-8")
        self.assertNotIn("https://", raw_cache)
        self.assertNotIn("demo-r1.zip", raw_cache)
        self.assertNotIn(str(self.temp.name), raw_cache)

    def test_missing_current_context_never_reuses_component_only_cache(self) -> None:
        service = self._service()
        self._seed_available(service)
        self.assertEqual(SourceAvailabilityService(paths=self.paths).cached("demo-model")["status"], "UNKNOWN")
        self.assertEqual(service.cached("demo-model")["status"], "AVAILABLE")
        self.assertEqual(service.cached("demo-model", self.record, binding=self.binding)["status"], "AVAILABLE")

    def test_each_source_and_catalog_binding_mutation_invalidates_old_record(self) -> None:
        service = self._service()
        self._seed_available(service)
        cases: list[tuple[dict[str, object], dict[str, object]]] = []
        changed_binding = copy.deepcopy(self.binding)
        changed_binding["catalog_schema"] = "v7-production-catalog.v1"
        cases.append((copy.deepcopy(self.record), changed_binding))
        for key, value in (("catalog_version", "2026.08.2"), ("catalog_revision", "rev-2"), ("catalog_fingerprint", "b" * 64), ("source_identity", "demo-artifact-r2")):
            candidate = copy.deepcopy(self.binding)
            candidate[key] = value
            cases.append((copy.deepcopy(self.record), candidate))
        for key, value in (("revision", "r2"), ("install_strategy", "manual_import"), ("source_identity", None)):
            candidate_record = copy.deepcopy(self.record)
            candidate_record[key] = value
            cases.append((candidate_record, copy.deepcopy(self.binding)))
        candidate_record = copy.deepcopy(self.record)
        candidate_record["primary_source"]["url"] = "https://example.invalid/demo-r2.zip"  # type: ignore[index]
        cases.append((candidate_record, copy.deepcopy(self.binding)))
        for source_key, source_value in (("provider", "other-provider"), ("kind", "internal_resolver")):
            candidate_record = copy.deepcopy(self.record)
            candidate_record["primary_source"][source_key] = source_value  # type: ignore[index]
            if source_key == "kind":
                candidate_record["primary_source"].pop("url", None)  # type: ignore[union-attr]
            cases.append((candidate_record, copy.deepcopy(self.binding)))
        candidate_record = copy.deepcopy(self.record)
        candidate_record["trusted_fallback_sources"].append({  # type: ignore[union-attr]
            "provider": "fixture-mirror-2",
            "kind": "https",
            "url": "https://mirror.invalid/demo-r1-2.zip",
            "canonical_identity": "demo-artifact-r1",
        })
        cases.append((candidate_record, copy.deepcopy(self.binding)))
        wrong_component = copy.deepcopy(self.record)
        wrong_component["model_id"] = "other-model"
        cases.append((wrong_component, copy.deepcopy(self.binding)))

        for record, binding in cases:
            with self.subTest(record=record, binding=binding):
                self.assertEqual(service.cached("demo-model", record, binding=binding)["status"], "UNKNOWN")
                self.assertEqual(service.check("demo-model", record, force=False, now=101, binding=binding)["status"], "UNKNOWN")

    def test_force_refresh_uses_only_current_binding_and_keeps_components_isolated(self) -> None:
        service = self._service()
        calls: list[str] = []
        result = self._seed_available(service)
        self.assertEqual(result["status"], "AVAILABLE")
        refreshed = copy.deepcopy(self.record)
        refreshed["model_id"] = "other-model"
        refreshed_binding = copy.deepcopy(self.binding)
        refreshed_binding["component_id"] = "other-model"
        refreshed["source_identity"] = "other-artifact"
        refreshed["record_revision"] = "r9"
        refreshed["catalog_fingerprint"] = "c" * 64
        refreshed["primary_source"]["canonical_identity"] = "other-artifact"  # type: ignore[index]
        refreshed["source_identity"] = "other-artifact"
        other = service.check("other-model", refreshed, force=True, now=102, binding=refreshed_binding, probe=lambda entry: (calls.append(str(entry["identity_digest"])) or ("AVAILABLE", "fixture", None)))
        self.assertEqual(other["status"], "AVAILABLE")
        self.assertEqual(len(calls), 1)
        self.assertEqual(service.cached("demo-model", self.record, binding=self.binding)["status"], "AVAILABLE")
        self.assertEqual(service.cached("other-model", refreshed, binding=refreshed_binding)["status"], "AVAILABLE")

    def test_check_all_requires_a_current_binding_for_each_component(self) -> None:
        service = self._service()
        without_context = service.check_all({"demo-model": self.record}, force=False, now=100)
        self.assertEqual(without_context["records"]["demo-model"]["status"], "UNKNOWN")
        with_context = service.check_all({"demo-model": self.record}, force=False, now=100, bindings={"demo-model": self.binding})
        self.assertEqual(with_context["records"]["demo-model"]["status"], "UNKNOWN")
        self.assertEqual(with_context["records"]["demo-model"]["reason_code"], "not_checked")

    def test_malformed_duplicate_unknown_nonfinite_and_unsafe_cache_fail_closed(self) -> None:
        service = self._service()
        cache = service.cache_path
        cache.parent.mkdir(parents=True, exist_ok=True)
        valid = {
            "binding_fingerprint": "a" * 64,
            "source_identity": "b" * 64,
            "status": "AVAILABLE",
            "selected_source": "primary",
            "checked_at": 1,
            "expires_at": 2,
            "reason_code": "source_reachable",
            "retry_after_seconds": None,
        }
        malformed = [
            "{\"schema_version\":\"source-availability-cache.v1\",\"schema_version\":\"source-availability-cache.v1\",\"records\":{}}",
            json.dumps({"schema_version": "source-availability-cache.v1", "records": {"demo-model": {**valid, "unknown": "secret"}}}),
            json.dumps({"schema_version": "source-availability-cache.v1", "records": {"demo-model": {**valid, "status": []}}}),
            json.dumps({"schema_version": "source-availability-cache.v1", "records": {"demo-model": {**valid, "checked_at": float("nan")}}}),
            json.dumps({"schema_version": "source-availability-cache.v1", "records": {"demo-model": {**valid, "binding_fingerprint": "short"}}}),
            json.dumps({"schema_version": "source-availability-cache.v1", "records": {"demo-model": {**valid, "expires_at": 0}}}),
        ]
        for raw in malformed:
            with self.subTest(raw=raw):
                cache.write_text(raw, encoding="utf-8")
                projection = service.cached("demo-model", self.record, binding=self.binding)
                self.assertEqual(projection["status"], "UNKNOWN")
                self.assertNotIn("secret", json.dumps(projection))

        for unsafe in ("https://user:secret@example.invalid/source.zip", r"C:\\Users\\secret\\source.zip"):
            record = copy.deepcopy(self.record)
            record["primary_source"]["url"] = unsafe  # type: ignore[index]
            calls: list[object] = []
            result = service.check("demo-model", record, force=True, binding=self.binding, probe=lambda entry: (calls.append(entry), ("AVAILABLE", "unsafe", None))[1])
            self.assertEqual(result["status"], "UNKNOWN")
            self.assertEqual(calls, [])
            self.assertNotIn("secret", json.dumps(result))

    def test_source_status_projection_type_checks_nested_values(self) -> None:
        for value in (
            {"status": [], "source_identity": {}, "selected_source": {}, "checked_at": [], "expires_at": {}, "reason_code": [], "binding_fingerprint": "a" * 64},
            {"status": {}, "source_identity": "C:\\secret", "selected_source": "primary", "checked_at": 1, "expires_at": 2, "reason_code": "source_reachable", "binding_fingerprint": "a" * 64},
        ):
            self.assertEqual(source_status_projection(value)["status"], "UNKNOWN")
        self.assertIsNone(source_binding_fingerprint({"model_id": "demo-model", "primary_source": {"url": "https://user:secret@example.invalid/a", "canonical_identity": "x"}}))
        service = self._service()
        public = self._seed_available(service)
        self.assertEqual(source_status_projection(public)["status"], "UNKNOWN")
        self.assertEqual(source_status_projection(public, allow_public=True)["status"], "AVAILABLE")

    def test_catalog_inspection_uses_current_catalog_binding(self) -> None:
        catalog_path = self.app / "Config" / "catalog.json"
        catalog_path.write_text(json.dumps({
            "schema_version": "v7-production-catalog.v1",
            "models": [{
                "model_id": "demo-model",
                "display_name": "Demo",
                "provider": "fixture",
                "revision": "r1",
                "official_source": {"provider": "fixture", "url": "https://example.invalid/demo", "canonical_identity": "demo-artifact-r1"},
                "disposition": "AUTO_INSTALL_READY",
                "files": [{"relative_path": "demo.bin", "size_bytes": 1}],
                "install_strategy": "portable_archive",
            }],
            "runtimes": [],
        }), encoding="utf-8")
        catalog = ProductionCatalog(paths=self.paths, catalog_path=catalog_path)
        service = self._service()
        record = catalog.models["demo-model"]
        binding = catalog._source_availability_binding("demo-model", record, component_type="model")
        self.assertEqual(service.check("demo-model", record, force=True, binding=binding, probe=lambda _entry: ("AVAILABLE", "fixture", None))["status"], "AVAILABLE")
        catalog.source_availability = service
        self.assertEqual(catalog.inspect_model("demo-model")["source_availability"]["status"], "AVAILABLE")
        catalog.fingerprint = "d" * 64
        self.assertEqual(catalog.inspect_model("demo-model")["source_availability"]["status"], "UNKNOWN")

    def test_update_resolver_check_and_check_all_pass_current_binding(self) -> None:
        from types import SimpleNamespace

        record = copy.deepcopy(self.record)
        catalog = SimpleNamespace(
            catalog_schema_version="v7-production-catalog.v2",
            catalog_version="2026.08.01",
            fingerprint="e" * 64,
            models={"demo-model": record},
            runtimes={},
        )
        source = _CapturingSourceService()
        resolver = UpdateResolver(paths=self.paths, catalog=catalog, source_service=source)  # type: ignore[arg-type]
        with patch.object(resolver, "_local", return_value={"status": "INSTALLED"}):
            resolver.check_component("demo-model")
            resolver.check_all(force_source_check=False)
        self.assertGreaterEqual(len(source.bindings), 2)
        binding = source.bindings[0]
        self.assertIsNotNone(binding)
        assert binding is not None
        self.assertEqual(binding["catalog_schema"], "v7-production-catalog.v2")
        self.assertEqual(binding["catalog_version"], "2026.08.01")
        self.assertEqual(binding["component_id"], "demo-model")
        self.assertEqual(binding["component_type"], "model")
        self.assertEqual(binding["catalog_fingerprint"], catalog.fingerprint)


if __name__ == "__main__":
    unittest.main()
