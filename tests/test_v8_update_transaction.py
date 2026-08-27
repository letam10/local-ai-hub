from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from src.app.stable_shell import INSTALLATION_SCHEMA, POINTER_SCHEMA, PRODUCT_SCHEMA, VERSION_MANIFEST_SCHEMA, atomic_activate_pointer, load_current_pointer
from src.services.app_update import PENDING_HEALTH_SCHEMA, STAGED_UPDATE_SCHEMA, AppUpdateService, _record_pending_health, mark_startup_health
from src.shared.runtime_identity import API_PROTOCOL_VERSION, api_identity
from src.shared.version import PRODUCT_VERSION


class V8UpdateTransactionTests(unittest.TestCase):
    def _payload(self, root: Path, version: str, commit: str) -> str:
        payload = root / "versions" / version
        payload.mkdir(parents=True)
        manifest = {
            "schema_version": VERSION_MANIFEST_SCHEMA,
            "product_id": "LocalAIHub",
            "version": version,
            "app_relative": "app",
            "runtime_relative": "runtime/Python312/pythonw.exe",
            "entrypoint": "src.app.launcher",
        }
        raw = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
        (payload / "manifest.json").write_bytes(raw)
        (payload / "build.json").write_text(json.dumps({"schema_version": "local-ai-hub-build-info.v1", "source_commit": commit}), encoding="utf-8")
        (payload / "app").mkdir()
        (payload / "runtime" / "Python312").mkdir(parents=True)
        (payload / "runtime" / "Python312" / "pythonw.exe").write_bytes(b"runtime")
        return hashlib.sha256(raw).hexdigest()

    def _installation(self, root: Path) -> tuple[str, str]:
        old_commit = "a" * 40
        new_commit = "b" * 40
        old_hash = self._payload(root, "old", old_commit)
        new_hash = self._payload(root, "new", new_commit)
        (root / "current.json").write_text(json.dumps({"schema_version": POINTER_SCHEMA, "version": "old", "payload_relative": "versions/old", "manifest_sha256": old_hash}), encoding="utf-8")
        atomic_activate_pointer(root, version="new", manifest_sha256=new_hash)
        return old_commit, new_commit

    def test_pending_update_is_committed_only_after_matching_health(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "install"
            root.mkdir()
            old_commit, new_commit = self._installation(root)
            previous = {"schema_version": POINTER_SCHEMA, "version": "old", "payload_relative": "versions/old", "manifest_sha256": hashlib.sha256((root / "versions" / "old" / "manifest.json").read_bytes()).hexdigest()}
            _record_pending_health(root, previous=previous, payload_id="new", source_commit=new_commit)
            result = mark_startup_health(root, health={"product_id": "LocalAIHub", "product_version": "8.0.1", "api_protocol_version": "v8-api.v1", "app_user_model_id": "LocalAIHub.Desktop", "installation_id": "c" * 32, "build_source_commit": new_commit, "build_payload_id": "new"}, frontend_ready=True)
            self.assertEqual(result["status"], "healthy")
            self.assertFalse((root / "update-state" / "pending-health.json").exists())
            self.assertEqual(load_current_pointer(root)["version"], "new")
            self.assertEqual(old_commit, "a" * 40)

    def test_api_health_without_frontend_ready_keeps_pending_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "install"
            root.mkdir()
            _old_commit, new_commit = self._installation(root)
            previous = {"schema_version": POINTER_SCHEMA, "version": "old", "payload_relative": "versions/old", "manifest_sha256": hashlib.sha256((root / "versions" / "old" / "manifest.json").read_bytes()).hexdigest()}
            _record_pending_health(root, previous=previous, payload_id="new", source_commit=new_commit)
            result = mark_startup_health(root, health={"product_id": "LocalAIHub", "product_version": "8.0.1", "api_protocol_version": "v8-api.v1", "app_user_model_id": "LocalAIHub.Desktop", "installation_id": "c" * 32, "build_source_commit": new_commit, "build_payload_id": "new"})
            self.assertEqual(result["status"], "frontend_pending")
            self.assertTrue((root / "update-state" / "pending-health.json").is_file())
            self.assertEqual(load_current_pointer(root)["version"], "new")

    def test_failed_health_rolls_back_pointer_without_touching_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "install"
            root.mkdir()
            _old_commit, new_commit = self._installation(root)
            old_manifest = root / "versions" / "old" / "manifest.json"
            previous = {"schema_version": POINTER_SCHEMA, "version": "old", "payload_relative": "versions/old", "manifest_sha256": hashlib.sha256(old_manifest.read_bytes()).hexdigest()}
            _record_pending_health(root, previous=previous, payload_id="new", source_commit=new_commit)
            result = mark_startup_health(root, health={"product_id": "wrong"}, frontend_ready=True)
            self.assertEqual(result["status"], "rollback")
            self.assertEqual(load_current_pointer(root)["version"], "old")
            self.assertTrue((root / "versions" / "new" / "build.json").is_file())
            self.assertEqual(json.loads((root / "update-state" / "last-rollback.json").read_text())["reason"], "POST_RESTART_HEALTH_FAILED")

    def test_staged_candidate_does_not_switch_pointer_until_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "install"
            data = root / "data"
            data.mkdir(parents=True)
            old_hash = self._payload(root, "old", "a" * 40)
            new_hash = self._payload(root, "main-bbbbbbbbbbbb", "b" * 40)
            (root / "installation.json").write_text(json.dumps({
                "schema_version": INSTALLATION_SCHEMA, "product_id": "LocalAIHub", "app_root": str(root),
                "data_root": str(data), "app_user_model_id": "LocalAIHub.Desktop", "launcher": "LocalAIHub.exe",
            }), encoding="utf-8")
            (root / "product.json").write_text(json.dumps({
                "schema_version": PRODUCT_SCHEMA, "product_id": "LocalAIHub", "version": "8.0.1",
                "launcher": "LocalAIHub.exe", "icon": "local-ai-hub.ico", "current_pointer": "current.json",
            }), encoding="utf-8")
            (root / "current.json").write_text(json.dumps({
                "schema_version": POINTER_SCHEMA, "version": "old", "payload_relative": "versions/old", "manifest_sha256": old_hash,
            }), encoding="utf-8")
            (root / "update-state").mkdir()
            previous = load_current_pointer(root)
            (root / "update-state" / "staged-update.json").write_text(json.dumps({
                "schema_version": STAGED_UPDATE_SCHEMA, "payload_id": "main-bbbbbbbbbbbb", "source_commit": "b" * 40,
                "payload_relative": "versions/main-bbbbbbbbbbbb", "manifest_sha256": new_hash, "previous": previous,
                "staged_at": "2026-08-27T00:00:00+00:00", "update_kind": "APP_ONLY",
            }), encoding="utf-8")
            old_env = os.environ.get("LOCALAIHUB_INSTALL_ROOT")
            os.environ["LOCALAIHUB_INSTALL_ROOT"] = str(root)
            try:
                service = AppUpdateService(allow_test_root=True)
                staged = service.staged_update()
                self.assertEqual(staged["payload_id"], "main-bbbbbbbbbbbb")
                self.assertEqual(load_current_pointer(root)["version"], "old")
                committed = service.commit_staged_restart()
            finally:
                if old_env is None:
                    os.environ.pop("LOCALAIHUB_INSTALL_ROOT", None)
                else:
                    os.environ["LOCALAIHUB_INSTALL_ROOT"] = old_env
            self.assertEqual(committed["status"], "activated")
            self.assertEqual(load_current_pointer(root)["version"], "main-bbbbbbbbbbbb")
            self.assertTrue((root / "update-state" / "pending-health.json").is_file())

    def test_three_consecutive_lightweight_commit_health_cycles_retain_rollback_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "install"
            data = root / "data"
            data.mkdir(parents=True)
            (root / "installation.json").write_text(json.dumps({
                "schema_version": INSTALLATION_SCHEMA, "product_id": "LocalAIHub", "app_root": str(root),
                "data_root": str(data), "app_user_model_id": "LocalAIHub.Desktop", "launcher": "LocalAIHub.exe",
            }), encoding="utf-8")
            (root / "product.json").write_text(json.dumps({
                "schema_version": PRODUCT_SCHEMA, "product_id": "LocalAIHub", "version": PRODUCT_VERSION,
                "launcher": "LocalAIHub.exe", "icon": "local-ai-hub.ico", "current_pointer": "current.json",
            }), encoding="utf-8")
            old_hash = self._payload(root, "main-aaaaaaaaaaaa", "a" * 40)
            (root / "current.json").write_text(json.dumps({
                "schema_version": POINTER_SCHEMA, "version": "main-aaaaaaaaaaaa", "payload_relative": "versions/main-aaaaaaaaaaaa", "manifest_sha256": old_hash,
            }), encoding="utf-8")
            state = root / "update-state"
            state.mkdir()
            old_env = os.environ.get("LOCALAIHUB_INSTALL_ROOT")
            os.environ["LOCALAIHUB_INSTALL_ROOT"] = str(root)
            try:
                service = AppUpdateService(allow_test_root=True)
                for letter in ("b", "c", "d"):
                    commit = letter * 40
                    version = f"main-{letter * 12}"
                    digest = self._payload(root, version, commit)
                    previous = load_current_pointer(root)
                    (state / "staged-update.json").write_text(json.dumps({
                        "schema_version": STAGED_UPDATE_SCHEMA, "payload_id": version, "source_commit": commit,
                        "payload_relative": f"versions/{version}", "manifest_sha256": digest, "previous": previous,
                        "staged_at": "2026-08-27T00:00:00+00:00", "update_kind": "APP_ONLY",
                    }), encoding="utf-8")
                    self.assertEqual(service.commit_staged_restart()["status"], "activated")
                    health = {"status": "healthy", **api_identity(product_version=PRODUCT_VERSION, installation_root=root, data_root=data), "api_protocol_version": API_PROTOCOL_VERSION, "build_source_commit": commit, "build_payload_id": version}
                    self.assertEqual(mark_startup_health(root, health=health, frontend_ready=True)["status"], "healthy")
                    self.assertEqual(load_current_pointer(root)["version"], version)
            finally:
                if old_env is None:
                    os.environ.pop("LOCALAIHUB_INSTALL_ROOT", None)
                else:
                    os.environ["LOCALAIHUB_INSTALL_ROOT"] = old_env
            self.assertTrue((root / "versions" / "main-aaaaaaaaaaaa" / "manifest.json").is_file())
            self.assertFalse((state / "pending-health.json").exists())


if __name__ == "__main__":
    unittest.main()
