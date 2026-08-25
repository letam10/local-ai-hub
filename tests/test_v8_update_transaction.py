from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from src.app.stable_shell import POINTER_SCHEMA, VERSION_MANIFEST_SCHEMA, atomic_activate_pointer, load_current_pointer
from src.services.app_update import PENDING_HEALTH_SCHEMA, _record_pending_health, mark_startup_health


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
            result = mark_startup_health(root, health={"product_id": "LocalAIHub", "product_version": "8.0.1", "api_protocol_version": "v8-api.v1", "app_user_model_id": "LocalAIHub.Desktop", "installation_id": "c" * 32})
            self.assertEqual(result["status"], "healthy")
            self.assertFalse((root / "update-state" / "pending-health.json").exists())
            self.assertEqual(load_current_pointer(root)["version"], "new")
            self.assertEqual(old_commit, "a" * 40)

    def test_failed_health_rolls_back_pointer_without_touching_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "install"
            root.mkdir()
            _old_commit, new_commit = self._installation(root)
            old_manifest = root / "versions" / "old" / "manifest.json"
            previous = {"schema_version": POINTER_SCHEMA, "version": "old", "payload_relative": "versions/old", "manifest_sha256": hashlib.sha256(old_manifest.read_bytes()).hexdigest()}
            _record_pending_health(root, previous=previous, payload_id="new", source_commit=new_commit)
            result = mark_startup_health(root, health={"product_id": "wrong"})
            self.assertEqual(result["status"], "rollback")
            self.assertEqual(load_current_pointer(root)["version"], "old")
            self.assertTrue((root / "versions" / "new" / "build.json").is_file())
            self.assertEqual(json.loads((root / "update-state" / "last-rollback.json").read_text())["reason"], "POST_RESTART_HEALTH_FAILED")


if __name__ == "__main__":
    unittest.main()
