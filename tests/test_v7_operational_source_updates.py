"""Static/fixture acceptance for V7 source resilience and update metadata."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

from src.platform.paths import HubPaths
from src.services.operational_closure.source_availability import SourceAvailabilityService
from src.services.operational_closure.update_service import UpdateResolver, UpdateSchedule
from src.services.operational_closure.evidence import record_runtime_smoke
from src.services.productization.catalog import ProductionCatalog
from src.services.component_installer import ComponentInstaller
from src.services.model_manager import ModelManager


class V7SourceAndUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.app = root / "app"
        self.data = root / "data"
        (self.app / "Config").mkdir(parents=True)
        self.paths = HubPaths(app_root=self.app, data_root=self.data)
        self.catalog_path = self.app / "Config" / "catalog.json"
        self.catalog_path.write_text(json.dumps({
            "schema_version": "v7-production-catalog.v1",
            "models": [{
                "model_id": "demo-model",
                "display_name": "Demo model",
                "category": "Test",
                "provider": "fixture",
                "version": "1",
                "revision": "r1",
                "latest_upstream_revision": "r2",
                "latest_supported_revision": "r2",
                "official_source": {
                    "provider": "fixture",
                    "url": "https://example.invalid/demo",
                    "canonical_identity": "demo-artifact-r1",
                },
                "trusted_fallback_sources": [{
                    "provider": "fixture-mirror",
                    "url": "https://example.invalid/mirror",
                    "canonical_identity": "demo-artifact-r1",
                }],
                "source_type": "fixture",
                "disposition": "AUTO_INSTALL_READY",
                "modules": ["demo"],
                "runtime_id": "demo-runtime",
                "files": [{"relative_path": "demo.bin", "size_bytes": 0}],
                "estimated_download_size": 1,
                "estimated_disk_size": 1,
                "update_parts": ["backend", "model"],
            }],
            "runtimes": [{
                "runtime_id": "demo-runtime",
                "display_name": "Demo runtime",
                "kind": "tool",
                "version": "1",
                "revision": "r1",
                "required_leaves": ["demo.exe"],
                "root_class": "runtime_root",
                "modules": ["demo"],
                "official_source": "https://example.invalid/runtime",
                "disposition": "REFERENCE_EXISTING",
                "install_strategy": "reference_existing",
            }],
        }), encoding="utf-8")
        self.catalog = ProductionCatalog(paths=self.paths, catalog_path=self.catalog_path)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_cache_is_used_without_network_probe(self) -> None:
        service = SourceAvailabilityService(paths=self.paths, ttl_seconds=600)
        calls: list[str] = []
        record = self.catalog.models["demo-model"]
        first = service.check("demo-model", record, force=True, now=100, probe=lambda entry: (calls.append(entry["fingerprint"]) or ("AVAILABLE", "ok", None)))
        self.assertEqual(first["status"], "AVAILABLE")
        self.assertEqual(len(calls), 1)
        second = service.check("demo-model", record, force=False, now=101, probe=lambda _entry: (_ for _ in ()).throw(AssertionError("cache bypassed")))
        self.assertEqual(second["status"], "AVAILABLE")
        self.assertEqual(second["source_identity"], first["source_identity"])

    def test_fallback_requires_same_identity_and_is_degraded(self) -> None:
        service = SourceAvailabilityService(paths=self.paths)
        record = self.catalog.models["demo-model"]
        result = service.check("demo-model", record, force=True, now=100, probe=lambda entry: ("UNAVAILABLE", "primary_missing", None) if not entry["fallback"] else ("AVAILABLE", "ok", None))
        self.assertEqual(result["status"], "DEGRADED")
        self.assertEqual(result["selected_source"], "fallback")

        mismatched = dict(record)
        mismatched["trusted_fallback_sources"] = [{"url": "https://example.invalid/wrong", "canonical_identity": "other-artifact"}]
        result = service.check("demo-mismatch", mismatched, force=True, now=100, probe=lambda entry: ("UNAVAILABLE", "gone", None))
        self.assertEqual(result["status"], "UNAVAILABLE")
        self.assertIsNone(result["selected_source"])

    def test_source_unavailable_does_not_turn_installed_local_component_into_not_installed(self) -> None:
        model_root = self.paths.models_root / "demo-model"
        model_root.mkdir(parents=True)
        (model_root / "demo.bin").write_bytes(b"local")
        service = SourceAvailabilityService(paths=self.paths)
        service.check("demo-model", self.catalog.models["demo-model"], force=True, now=100, probe=lambda _entry: ("UNAVAILABLE", "gone", None))
        resolver = UpdateResolver(paths=self.paths, catalog=self.catalog, source_service=service)
        report = resolver.check_component("demo-model")
        self.assertEqual(report["local_status"], "INSTALLED")
        self.assertEqual(report["status"], "SOURCE_UNAVAILABLE")

    def test_update_report_separates_supported_revision_and_changed_parts(self) -> None:
        model_root = self.paths.models_root / "demo-model"
        model_root.mkdir(parents=True)
        (model_root / "demo.bin").write_bytes(b"local")
        receipt = self.paths.config_root / "component_install_receipts.json"
        receipt.parent.mkdir(parents=True)
        receipt.write_text(json.dumps({"schema_version": "component-install-receipts.v2", "records": {"demo-model": {"bundle_revision": "r1"}}}), encoding="utf-8")
        service = SourceAvailabilityService(paths=self.paths)
        service.check("demo-model", self.catalog.models["demo-model"], force=True, now=int(__import__("time").time()), probe=lambda _entry: ("AVAILABLE", "ok", None))
        report = UpdateResolver(paths=self.paths, catalog=self.catalog, source_service=service).check_component("demo-model")
        self.assertEqual(report["status"], "UPDATE_AVAILABLE")
        self.assertEqual(report["changed_parts"], ["backend", "model"])
        self.assertTrue(report["download_required"])

    def test_update_routes_are_manual_and_server_owned(self) -> None:
        from src.services.api.router_registry import build_router
        routes = {(route.method, route.path) for route in build_router().routes()}
        self.assertIn(("POST", "/api/updates/check"), routes)
        self.assertIn(("POST", "/api/updates/check-all"), routes)
        self.assertIn(("POST", "/api/updates/rollback"), routes)
        source = (Path(__file__).resolve().parents[1] / "src/services/api/routes/updates.py").read_text(encoding="utf-8")
        self.assertIn("refresh_source", source)
        self.assertNotIn("download_url", source)

    def test_model_update_candidate_switch_and_rollback_are_atomic_and_path_free(self) -> None:
        import hashlib
        current_root = self.paths.models_root / "demo-model"
        current_root.mkdir(parents=True)
        (current_root / "demo.bin").write_bytes(b"old")
        candidate = self.paths.temp_root / "candidates" / "demo.bin"
        candidate.parent.mkdir(parents=True)
        candidate.write_bytes(b"new")
        self.catalog.models["demo-model"]["update_candidate"] = {
            "staged_relative_path": "candidates/demo.bin",
            "relative_path": "demo.bin",
            "size_bytes": 3,
            "sha256": hashlib.sha256(b"new").hexdigest(),
            "source_identity": "demo-artifact-r2",
        }
        receipt = self.paths.config_root / "component_install_receipts.json"
        receipt.parent.mkdir(parents=True)
        receipt.write_text(json.dumps({"schema_version": "component-install-receipts.v2", "records": {"demo-model": {"bundle_revision": "r1"}}}), encoding="utf-8")
        service = SourceAvailabilityService(paths=self.paths)
        service.check("demo-model", self.catalog.models["demo-model"], force=True, now=int(__import__("time").time()), probe=lambda _entry: ("AVAILABLE", "ok", None))
        resolver = UpdateResolver(paths=self.paths, catalog=self.catalog, source_service=service)
        plan = resolver.plan_update("demo-model")
        self.assertEqual(plan["status"], "planned")
        applied = resolver.apply_update(plan["plan_id"], confirmed=True)
        self.assertEqual(applied["status"], "completed")
        self.assertEqual((current_root / "demo.bin").read_bytes(), b"new")
        rolled_back = resolver.rollback("demo-model")
        self.assertEqual(rolled_back["status"], "completed")
        self.assertEqual((current_root / "demo.bin").read_bytes(), b"old")
        self.assertNotIn(str(self.temp.name), json.dumps(applied))

    def test_public_lifecycle_runtime_plan_uses_archive_executor(self) -> None:
        archive = Path(self.temp.name) / "ffmpeg.zip"
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr("ffmpeg-1/bin/ffmpeg.exe", b"ffmpeg")
            handle.writestr("ffmpeg-1/bin/ffprobe.exe", b"ffprobe")
        catalog_path = self.app / "Config" / "runtime-only.json"
        catalog_path.write_text(json.dumps({
            "schema_version": "v7-production-catalog.v1",
            "models": [],
            "runtimes": [{
                "runtime_id": "ffmpeg",
                "display_name": "FFmpeg",
                "kind": "tool",
                "version": "1",
                "revision": "r1",
                "root_class": "runtime_root",
                "required_leaves": ["tools/ffmpeg/ffmpeg.exe", "tools/ffmpeg/ffprobe.exe"],
                "modules": ["video"],
                "official_source": "https://github.com/example/ffmpeg.zip",
                "disposition": "AUTO_INSTALL_READY",
                "install_strategy": "portable_archive",
                "archive_format": "zip",
                "archive_prefix": "ffmpeg-1/bin",
                "archive_leaves": {"tools/ffmpeg/ffmpeg.exe": "ffmpeg.exe", "tools/ffmpeg/ffprobe.exe": "ffprobe.exe"},
                "sha256": "a" * 64,
                "estimated_download_size": 7,
                "estimated_disk_size": 13,
            }],
        }), encoding="utf-8")
        catalog = ProductionCatalog(paths=self.paths, catalog_path=catalog_path)
        from src.services.productization import ComponentLifecycle
        self.data.mkdir(parents=True, exist_ok=True)
        lifecycle = ComponentLifecycle(paths=self.paths, catalog=catalog)
        plan = lifecycle.plan_one_click("ffmpeg")

        class _Downloader:
            def __init__(self, **_kwargs):
                pass

            def download(self, *_args, **_kwargs):
                return SimpleNamespace(staged_path=archive)

        with patch("src.services.productization.lifecycle.TrustedDownloader", _Downloader), patch("src.services.productization.lifecycle.trusted_source", return_value=True):
            result = lifecycle.confirm(plan["plan_id"], confirmed=True)
        self.assertEqual(result["status"], "completed")
        self.assertTrue((self.paths.runtime_root / "tools" / "ffmpeg" / "ffmpeg.exe").is_file())

    def test_catalog_projection_redacts_source_urls_and_candidate_metadata(self) -> None:
        payload = self.catalog.snapshot()
        encoded = json.dumps(payload, ensure_ascii=True)
        self.assertNotIn("official_source", encoded)
        self.assertNotIn("https://", encoded)
        self.assertNotIn("update_candidate", encoded)

    def test_runtime_evidence_promotes_only_matching_fresh_runtime(self) -> None:
        runtime_root = self.paths.runtime_root
        runtime_root.mkdir(parents=True)
        (runtime_root / "demo.exe").write_bytes(b"runtime")
        record = self.catalog.runtimes["demo-runtime"]
        saved = record_runtime_smoke(self.paths, "demo-runtime", record, outcome="completed", smoke_id="demo-smoke-v1", details={"exit_code": 0})
        self.assertEqual(saved["status"], "saved")
        self.assertEqual(self.catalog.inspect_runtime("demo-runtime")["status"], "OPERATIONAL")
        (runtime_root / "demo.exe").write_bytes(b"drift")
        self.assertEqual(self.catalog.inspect_runtime("demo-runtime")["status"], "INSTALLED_UNVERIFIED")

    def test_check_all_never_auto_applies_and_schedule_defaults_manual(self) -> None:
        schedule = UpdateSchedule(paths=self.paths)
        self.assertEqual(schedule.get()["policy"], "manual")
        self.assertFalse(schedule.due(now=100))
        self.assertEqual(schedule.set_policy("daily")["policy"], "daily")
        resolver = UpdateResolver(paths=self.paths, catalog=self.catalog, source_service=SourceAvailabilityService(paths=self.paths))
        result = resolver.check_all(force_source_check=False)
        self.assertFalse(result["auto_apply"])
        self.assertTrue(result["dry_run"])

    def test_manual_import_publishes_catalog_bound_receipt_without_overwrite(self) -> None:
        model_catalog = self.app / "Config" / "model_catalog.json"
        model_catalog.write_text(json.dumps({
            "schema_version": "model-catalog.v1",
            "models": [{
                "model_id": "demo-model",
                "display_name": "Demo model",
                "provider": "fixture",
                "files": [{"relative_path": "demo.bin", "size_bytes": 0}],
                "official_source": "https://example.invalid/demo",
                "install_supported": False,
                "modules_using_model": ["demo"],
            }],
        }), encoding="utf-8")
        runtime_catalog = self.app / "Config" / "runtime_catalog.example.json"
        runtime_catalog.write_text(json.dumps({
            "schema_version": "runtime-catalog.v1",
            "runtimes": [{
                "runtime_id": "demo-runtime",
                "display_name": "Demo runtime",
                "root_class": "runtime_root",
                "required_leaves": ["demo.exe"],
                "modules": ["demo"],
                "official_source": "local",
                "install_strategy": "reference_existing",
            }],
        }), encoding="utf-8")
        from src.services.runtime_manager import RuntimeManager
        manager = ComponentInstaller(
            paths=self.paths,
            model_manager=ModelManager(paths=self.paths, catalog_path=model_catalog),
            runtime_manager=RuntimeManager(paths=self.paths, catalog_path=runtime_catalog),
        )
        source = Path(self.temp.name) / "selection.bin"
        source.write_bytes(b"trusted manual artifact")
        selection = manager.issue_selection("demo-model", source)
        plan = manager.plan_import(selection["selection_id"], mode="COPY_INTO_MANAGED_MODELS")
        result = manager.confirm_import(plan["plan_id"], confirmed=True)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["state"], "INSTALLED_UNVERIFIED")
        self.assertTrue((self.paths.models_root / "demo-model" / "demo.bin").is_file())
        receipt = json.loads((self.paths.config_root / "component_install_receipts.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["records"]["demo-model"]["source"], "manual_import")
        self.assertNotIn(str(self.temp.name), json.dumps(result))
        self.assertEqual(manager.confirm_import(plan["plan_id"], confirmed=True)["code"], "target_exists_manual_review")

    def test_repair_refreshes_receipt_and_uninstall_removes_only_known_leaf(self) -> None:
        model_catalog = self.app / "Config" / "model_catalog.json"
        model_catalog.write_text(json.dumps({
            "schema_version": "model-catalog.v1",
            "models": [{
                "model_id": "demo-model",
                "display_name": "Demo model",
                "provider": "fixture",
                "files": [{"relative_path": "demo.bin", "size_bytes": 0}],
                "official_source": "https://example.invalid/demo",
                "modules_using_model": ["demo"],
            }],
        }), encoding="utf-8")
        runtime_catalog = self.app / "Config" / "runtime_catalog.example.json"
        runtime_catalog.write_text(json.dumps({"schema_version": "runtime-catalog.v1", "runtimes": [{"runtime_id": "demo-runtime", "display_name": "Demo", "root_class": "runtime_root", "required_leaves": ["demo.exe"], "modules": ["demo"], "official_source": "local", "install_strategy": "reference_existing"}]}), encoding="utf-8")
        from src.services.runtime_manager import RuntimeManager
        manager = ComponentInstaller(paths=self.paths, model_manager=ModelManager(paths=self.paths, catalog_path=model_catalog), runtime_manager=RuntimeManager(paths=self.paths, catalog_path=runtime_catalog))
        root = self.paths.models_root / "demo-model"
        root.mkdir(parents=True)
        (root / "demo.bin").write_bytes(b"existing")
        (root / "user-note.txt").write_text("keep", encoding="utf-8")
        repair = manager.plan_maintenance("demo-model", action="repair")
        self.assertEqual(manager.confirm_maintenance(repair["plan_id"], confirmed=True)["status"], "completed")
        uninstall = manager.plan_maintenance("demo-model", action="uninstall")
        result = manager.confirm_maintenance(uninstall["plan_id"], confirmed=True)
        self.assertEqual(result["status"], "completed")
        self.assertFalse((root / "demo.bin").exists())
        self.assertTrue((root / "user-note.txt").exists())


if __name__ == "__main__":
    unittest.main()
