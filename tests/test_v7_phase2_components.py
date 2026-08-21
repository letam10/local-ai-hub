"""Synthetic, source-only acceptance tests for V7 Phase 2 component flows."""

from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
import zipfile

from src.platform.paths import HubPaths
from src.services.bootstrap_core import apply_core_bootstrap, inspect_core, plan_core_bootstrap, verify_core
from src.services.component_installer import ComponentInstaller, InstallPlanError
from src.services.component_installer.archive import ArchiveSafetyError, safe_extract_archive
from src.services.component_installer.downloader import DownloadError, TrustedDownloader
from src.services.model_manager import ModelManager
from src.services.runtime_manager import CoreRuntimeResolver, RuntimeManager


ROOT = Path(__file__).resolve().parents[1]


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _copy_core_examples(app: Path) -> None:
    target = app / "Config"
    target.mkdir(parents=True, exist_ok=True)
    for source in (ROOT / "Config").glob("*.example.json"):
        shutil.copy2(source, target / source.name)


def _fake_core(data: Path) -> None:
    env = data / "Environments" / "core"
    (env / "Scripts").mkdir(parents=True, exist_ok=True)
    (env / "Scripts" / "python.exe").write_bytes(b"synthetic-python")
    (env / "pyvenv.cfg").write_text("version = 3.12.0\n", encoding="utf-8")


class _FixtureHandler(BaseHTTPRequestHandler):
    payload = b"synthetic component payload\n"
    etag = '"v1"'
    truncate_at: int | None = None

    def do_GET(self) -> None:  # noqa: N802
        value = self.payload
        start = 0
        range_header = self.headers.get("Range", "")
        if range_header.startswith("bytes="):
            try:
                start = int(range_header.split("=", 1)[1].split("-", 1)[0])
            except ValueError:
                start = 0
        if start > len(value):
            self.send_response(416)
            self.end_headers()
            return
        body = value[start:]
        self.send_response(206 if start else 200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", self.etag)
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        truncate_at = type(self).truncate_at
        if truncate_at is not None:
            self.wfile.write(body[:truncate_at])
            self.wfile.flush()
            self.connection.shutdown(1)
            return
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return


class Phase2CoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = self.root / "app"
        self.data = self.root / "data"
        _copy_core_examples(self.app)
        _fake_core(self.data)
        self.paths = HubPaths(app_root=self.app, data_root=self.data)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_resolver_prefers_managed_core_without_drive_assumption(self) -> None:
        result = CoreRuntimeResolver(paths=self.paths, allow_system=False).inspect()
        self.assertEqual(result["status"], "AVAILABLE")
        self.assertEqual(result["selected_source"], "managed_core")
        self.assertNotIn("D:\\", json.dumps(result))

    def test_bootstrap_plan_apply_verify_is_idempotent_and_preserves_config(self) -> None:
        existing = self.paths.config_root / "app.json"
        existing.parent.mkdir(parents=True, exist_ok=True)
        existing.write_text('{"user_value":true}\n', encoding="utf-8")
        plan = plan_core_bootstrap(paths=self.paths)
        self.assertEqual(plan["status"], "planned")
        result = apply_core_bootstrap(plan, paths=self.paths, confirmed=True)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(json.loads(existing.read_text(encoding="utf-8")), {"user_value": True})
        receipt = self.paths.config_root / "core_install_receipt.json"
        self.assertTrue(receipt.is_file())
        self.assertEqual(verify_core(paths=self.paths)["status"], "ready")
        second = apply_core_bootstrap(plan_core_bootstrap(paths=self.paths), paths=self.paths, confirmed=True)
        self.assertEqual(second["status"], "ready")

    def test_no_config_plan_is_read_only(self) -> None:
        plan = plan_core_bootstrap(paths=self.paths, initialize_config=False)
        self.assertEqual(plan["required_writes"], [])
        self.assertFalse(self.paths.config_root.exists())


class Phase2InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = self.root / "app"
        self.data = self.root / "data"
        _copy_core_examples(self.app)
        self.paths = HubPaths(app_root=self.app, data_root=self.data)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_local_http_download_checksum_and_fixture_policy(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            source = f"http://127.0.0.1:{server.server_port}/payload.bin"
            staging = self.paths.temp_root / "component-install"
            expected = hashlib.sha256(_FixtureHandler.payload).hexdigest()
            result = TrustedDownloader(staging_root=staging).download(source, "payload.bin", expected_sha256=expected, expected_size=len(_FixtureHandler.payload), fixture_mode=True)
            self.assertEqual(result.sha256, expected)
            self.assertEqual(result.staged_path.read_bytes(), _FixtureHandler.payload)
            with self.assertRaises(DownloadError):
                TrustedDownloader(staging_root=staging).download(source, "bad.bin", expected_sha256="0" * 64, fixture_mode=True)
        finally:
            server.shutdown()
            thread.join(timeout=3)
            server.server_close()

    def test_download_resume_cancel_source_change_size_and_disk_guards(self) -> None:
        original_payload = _FixtureHandler.payload
        original_etag = _FixtureHandler.etag
        original_truncate = _FixtureHandler.truncate_at
        _FixtureHandler.payload = b"x" * (2 * 1024 * 1024 + 17)
        _FixtureHandler.etag = '"v1"'
        _FixtureHandler.truncate_at = 700_000
        server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            source = f"http://127.0.0.1:{server.server_port}/resume.bin"
            expected = hashlib.sha256(_FixtureHandler.payload).hexdigest()
            downloader = TrustedDownloader(staging_root=self.paths.temp_root / "component-install")
            with self.assertRaises(DownloadError):
                downloader.download(source, "resume.bin", expected_sha256=expected, expected_size=len(_FixtureHandler.payload), fixture_mode=True)
            partial = self.paths.temp_root / "component-install" / "resume.bin.partial"
            self.assertTrue(partial.is_file())
            _FixtureHandler.truncate_at = None
            resumed = downloader.download(source, "resume.bin", expected_sha256=expected, expected_size=len(_FixtureHandler.payload), fixture_mode=True)
            self.assertTrue(resumed.resumed)
            self.assertEqual(resumed.staged_path.read_bytes(), _FixtureHandler.payload)

            # A changed ETag must not append bytes from the old object.
            _FixtureHandler.payload = b"y" * (2 * 1024 * 1024 + 17)
            _FixtureHandler.etag = '"v1"'
            _FixtureHandler.truncate_at = 700_000
            changed = self.paths.temp_root / "component-install" / "changed.bin"
            with self.assertRaises(DownloadError):
                downloader.download(source, "changed.bin", expected_size=len(_FixtureHandler.payload), fixture_mode=True)
            _FixtureHandler.etag = '"v2"'
            _FixtureHandler.truncate_at = None
            changed_result = downloader.download(source, "changed.bin", expected_size=len(_FixtureHandler.payload), fixture_mode=True)
            self.assertFalse(changed_result.resumed)
            self.assertEqual(changed_result.staged_path.read_bytes(), _FixtureHandler.payload)

            with self.assertRaisesRegex(DownloadError, "insufficient_disk"):
                downloader.download(source, "disk.bin", expected_size=10, disk_free_bytes=9, fixture_mode=True)
            with self.assertRaisesRegex(DownloadError, "content_length_mismatch"):
                downloader.download(source, "size.bin", expected_size=1, fixture_mode=True)

            cancel = threading.Event()
            cancel_downloader = TrustedDownloader(staging_root=self.paths.temp_root / "component-install-cancel")
            with self.assertRaisesRegex(DownloadError, "cancelled"):
                cancel_downloader.download(
                    source, "cancel.bin", expected_size=len(_FixtureHandler.payload), fixture_mode=True,
                    cancel_event=cancel, progress=lambda received, _total: cancel.set() if received else None,
                )
            self.assertTrue((self.paths.temp_root / "component-install-cancel" / "cancel.bin.partial").is_file())
        finally:
            _FixtureHandler.payload = original_payload
            _FixtureHandler.etag = original_etag
            _FixtureHandler.truncate_at = original_truncate
            server.shutdown()
            thread.join(timeout=3)
            server.server_close()

    def test_archive_traversal_is_rejected(self) -> None:
        archive = self.root / "unsafe.zip"
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr("../escape.txt", "bad")
        with self.assertRaises(ArchiveSafetyError):
            safe_extract_archive(archive, self.paths.temp_root / "extract")

    def test_component_plans_are_opaque_and_source_metadata_incomplete_stays_unavailable(self) -> None:
        manager = ComponentInstaller(paths=self.paths)
        snapshot = manager.snapshot()
        self.assertTrue(snapshot["records"])
        plan = manager.plan_install("sam2.1-hiera-small", component_type="model")
        encoded = json.dumps(plan, ensure_ascii=True)
        self.assertNotIn("D:\\", encoded)
        self.assertNotIn("official_source", plan)
        self.assertFalse(plan["auto_install_supported"])
        result = manager.confirm_plan(plan["plan_id"], confirmed=True)
        self.assertEqual(result["status"], "unavailable")
        with self.assertRaises(InstallPlanError):
            manager.plan_install("sam2.1-hiera-small", component_type="model", variant="arbitrary")

    def test_fake_model_install_and_runtime_install_receipts(self) -> None:
        model_bytes = b"tiny model\n"
        model_hash = hashlib.sha256(model_bytes).hexdigest()
        model_catalog = {
            "schema_version": "model-catalog.v1",
            "models": [{
                "model_id": "fake-model", "display_name": "Fake model", "provider": "fixture", "family": "test", "version": "1", "official_source": "local",
                "files": [{"relative_path": "fake.bin", "size_bytes": len(model_bytes), "sha256": model_hash}], "estimated_download_size": len(model_bytes), "estimated_disk_size": len(model_bytes), "install_supported": False,
            }],
        }
        model_catalog_path = self.app / "Config" / "fake-model.json"
        _write_json(model_catalog_path, model_catalog)
        source_model = self.root / "model-source"
        source_model.mkdir()
        (source_model / "fake.bin").write_bytes(model_bytes)
        manager = ModelManager(paths=self.paths, catalog_path=model_catalog_path)
        self.assertEqual(manager.inspect("fake-model")["status"], "NOT_INSTALLED")
        result = manager.install_fixture("fake-model", source_model)
        self.assertEqual(result["state"], "INSTALLED_UNVERIFIED")
        self.assertTrue((self.paths.models_root / "fake-model" / "fake.bin").is_file())
        self.assertTrue((self.paths.config_root / "model_install_receipts.json").is_file())

        runtime_catalog_path = self.app / "Config" / "fake-runtime.json"
        _write_json(runtime_catalog_path, {"schema_version": "runtime-catalog.v1", "runtimes": [{
            "runtime_id": "fake-runtime", "display_name": "Fake runtime", "kind": "tool", "version": "1", "root_class": "runtime_root", "required_leaves": ["fake/bin/tool.exe"], "modules": ["fixture"], "official_source": "local", "install_supported": False, "install_strategy": "reference_existing",
        }]})
        runtime_source = self.root / "runtime-source"; (runtime_source / "fake/bin").mkdir(parents=True)
        (runtime_source / "fake/bin/tool.exe").write_bytes(b"tool")
        runtime = RuntimeManager(paths=self.paths, catalog_path=runtime_catalog_path)
        self.assertEqual(runtime.inspect("fake-runtime")["status"], "NOT_INSTALLED")
        self.assertEqual(runtime.install_fixture("fake-runtime", runtime_source)["state"], "INSTALLED_UNVERIFIED")
        self.assertTrue((self.paths.runtime_root / "fake/bin/tool.exe").is_file())


if __name__ == "__main__":
    unittest.main()
