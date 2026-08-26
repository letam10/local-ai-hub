from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

from src.app.stable_shell import (
    INSTALLATION_SCHEMA,
    POINTER_SCHEMA,
    PRODUCT_SCHEMA,
    VERSION_MANIFEST_SCHEMA,
)
from src.services.app_update import (
    AppUpdateError,
    AppUpdateService,
    UPDATE_SCHEMA,
    _classify_channel_relation,
    _safe_extract_app_archive,
    _safe_update_manifest,
)
from src.shared.version import PRODUCT_VERSION


class V8AppMainUpdateTests(unittest.TestCase):
    def _manifest(self, commit: str = "a" * 40) -> dict[str, object]:
        return {
            "schema_version": UPDATE_SCHEMA,
            "product_id": "LocalAIHub",
            "product_version": PRODUCT_VERSION,
            "channel": "main",
            "source_commit": commit,
            "payload_id": f"main-{commit[:12]}",
            "runtime_strategy": "reuse-current",
            "archive": "LocalAIHub-main-update.zip",
            "archive_sha256": "b" * 64,
            "file_count": 2,
        }

    def test_channel_relation_blocks_ahead_and_diverged_current_payloads(self) -> None:
        self.assertEqual(_classify_channel_relation("identical"), "same")
        self.assertEqual(_classify_channel_relation("ahead"), "forward_update_available")
        self.assertEqual(_classify_channel_relation("behind"), "blocked_current_ahead_of_main")
        self.assertEqual(_classify_channel_relation("diverged"), "blocked_channel_diverged")
        self.assertEqual(_classify_channel_relation("unknown"), "channel_relation_unavailable")

    def test_update_manifest_binds_exact_main_commit(self) -> None:
        value = self._manifest()
        self.assertEqual(_safe_update_manifest(value, expected_commit="a" * 40)["payload_id"], "main-aaaaaaaaaaaa")
        with self.assertRaisesRegex(AppUpdateError, "UPDATE_COMMIT_MISMATCH"):
            _safe_update_manifest(value, expected_commit="c" * 40)

    def test_archive_extraction_is_app_scoped_and_rejects_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "good.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("app/src/__init__.py", "")
                bundle.writestr("app/src/app.py", "VALUE = 1\n")
            target = root / "target"
            target.mkdir()
            self.assertEqual(_safe_extract_app_archive(archive, target, expected_files=2), 2)
            self.assertTrue((target / "app" / "src" / "app.py").is_file())

            bad = root / "bad.zip"
            with zipfile.ZipFile(bad, "w") as bundle:
                bundle.writestr("app/../escape.txt", "bad")
            with self.assertRaisesRegex(AppUpdateError, "UPDATE_ARCHIVE_PATH_INVALID"):
                _safe_extract_app_archive(bad, root / "other", expected_files=1)

    def test_status_uses_successful_main_artifact_and_never_needs_git_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            install = base / "LocalAIHub"
            data = base / "LocalAIHubData"
            payload = install / "versions" / "8.0.1-test"
            app = payload / "app"
            runtime = payload / "runtime" / "Python312"
            app.mkdir(parents=True)
            runtime.mkdir(parents=True)
            data.mkdir()
            (runtime / "pythonw.exe").write_bytes(b"")
            version_manifest = {
                "schema_version": VERSION_MANIFEST_SCHEMA,
                "product_id": "LocalAIHub",
                "version": "8.0.1-test",
                "app_relative": "app",
                "runtime_relative": "runtime/Python312/pythonw.exe",
                "entrypoint": "src.app.launcher",
            }
            raw = (json.dumps(version_manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode()
            (payload / "manifest.json").write_bytes(raw)
            digest = hashlib.sha256(raw).hexdigest()
            (install / "installation.json").write_text(json.dumps({
                "schema_version": INSTALLATION_SCHEMA,
                "product_id": "LocalAIHub",
                "app_root": str(install.absolute()),
                "data_root": str(data.absolute()),
                "app_user_model_id": "LocalAIHub.Desktop",
                "launcher": "LocalAIHub.exe",
            }), encoding="utf-8")
            (install / "product.json").write_text(json.dumps({
                "schema_version": PRODUCT_SCHEMA,
                "product_id": "LocalAIHub",
                "version": PRODUCT_VERSION,
                "launcher": "LocalAIHub.exe",
                "icon": "local-ai-hub.ico",
                "current_pointer": "current.json",
            }), encoding="utf-8")
            (install / "current.json").write_text(json.dumps({
                "schema_version": POINTER_SCHEMA,
                "version": "8.0.1-test",
                "payload_relative": "versions/8.0.1-test",
                "manifest_sha256": digest,
            }), encoding="utf-8")
            gh = base / "gh"
            gh.write_text("fixture", encoding="utf-8")
            commit = "c" * 40

            def runner(command, **kwargs):
                text = ""
                if command[1:3] == ["auth", "status"]:
                    return subprocess.CompletedProcess(command, 0, "", "")
                if "actions/workflows/ci.yml/runs" in command[4]:
                    text = json.dumps({"workflow_runs": [{"id": 55, "head_sha": commit, "head_branch": "main", "conclusion": "success"}]})
                elif "actions/runs/55/artifacts" in command[4]:
                    text = json.dumps({"artifacts": [{"id": 77, "name": "local-ai-hub-main-update", "expired": False}]})
                return subprocess.CompletedProcess(command, 0, text, "")

            previous = __import__("os").environ.get("LOCALAIHUB_INSTALL_ROOT")
            __import__("os").environ["LOCALAIHUB_INSTALL_ROOT"] = str(install)
            try:
                result = AppUpdateService(runner=runner, gh_path=str(gh), allow_test_root=True).status(refresh=True)
            finally:
                if previous is None:
                    __import__("os").environ.pop("LOCALAIHUB_INSTALL_ROOT", None)
                else:
                    __import__("os").environ["LOCALAIHUB_INSTALL_ROOT"] = previous
            self.assertEqual(result["status"], "available")
            self.assertEqual(result["latest_build"], commit)
            self.assertEqual(result["current_build"], "legacy")
            self.assertEqual(result["transport"], "github_cli")


if __name__ == "__main__":
    unittest.main()
