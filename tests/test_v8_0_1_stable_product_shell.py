"""Bounded V8.0.1 stable-product shell and post-tag contract tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import os

from src.app.desktop_lifecycle import DesktopCloseController
from src.app.stable_shell import (
    APP_USER_MODEL_ID,
    POINTER_SCHEMA,
    StableShellError,
    atomic_activate_pointer,
    resolve_launch_plan,
)
from scripts.stage_stable_product import StableProductBuildError, stage_product
from scripts.v8_release_provenance import release_policy_snapshot


class StableProductShellTests(unittest.TestCase):
    def _fixture(self) -> tuple[Path, Path, dict[str, object]]:
        root = Path(tempfile.mkdtemp(prefix="lah-801-install-"))
        data = Path(tempfile.mkdtemp(prefix="lah-801-data-"))
        version = "8.0.1"
        payload = root / "versions" / version
        app = payload / "app"
        runtime = payload / "runtime" / "Python312"
        app.mkdir(parents=True)
        runtime.mkdir(parents=True)
        (runtime / "pythonw.exe").write_bytes(b"bundled-pythonw")
        (app / "src").mkdir()
        (app / "src" / "app" ).mkdir()
        (app / "src" / "app" / "launcher.py").write_text("# fixture\n", encoding="utf-8")
        (root / "LocalAIHub.exe").write_bytes(b"stable-launcher")
        (root / "local-ai-hub.ico").write_bytes(b"canonical-icon")
        (root / "installation.json").write_text(json.dumps({
            "schema_version": "v8.0.1-installation.v1", "product_id": "LocalAIHub",
            "app_root": str(root), "data_root": str(data),
            "app_user_model_id": APP_USER_MODEL_ID, "launcher": "LocalAIHub.exe",
        }), encoding="utf-8")
        (root / "product.json").write_text(json.dumps({
            "schema_version": "v8.0.1-product.v1", "product_id": "LocalAIHub",
            "version": version, "launcher": "LocalAIHub.exe", "icon": "local-ai-hub.ico",
            "current_pointer": "current.json",
        }), encoding="utf-8")
        manifest = {
            "schema_version": "v8.0.1-version-manifest.v1", "product_id": "LocalAIHub",
            "version": version, "app_relative": "app", "runtime_relative": "runtime/Python312/pythonw.exe",
            "entrypoint": "src.app.launcher",
        }
        manifest_path = payload / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
        pointer = {
            "schema_version": POINTER_SCHEMA, "version": version, "payload_relative": f"versions/{version}",
            "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        }
        (root / "current.json").write_text(json.dumps(pointer, sort_keys=True) + "\n", encoding="utf-8")
        return root, data, pointer

    def test_stable_launcher_resolves_bundled_runtime_and_data_root(self) -> None:
        root, data, _pointer = self._fixture()
        plan = resolve_launch_plan(root, allow_test_root=True)
        self.assertEqual(plan.version, "8.0.1")
        self.assertEqual(plan.runtime_pythonw.name, "pythonw.exe")
        self.assertEqual(plan.command[-2:], ("-m", "src.app.launcher"))
        self.assertEqual(plan.environment["LOCALAIHUB_DATA_ROOT"], str(data))
        self.assertEqual(plan.environment["LOCALAIHUB_INSTALL_ROOT"], str(root))

    def test_product_shell_version_may_retain_stable_identity_during_payload_update(self) -> None:
        root, _data, _pointer = self._fixture()
        payload = root / "versions" / "8.0.2-testpayload"
        (payload / "app" / "src" / "app").mkdir(parents=True)
        (payload / "runtime" / "Python312").mkdir(parents=True)
        (payload / "app" / "src" / "app" / "launcher.py").write_text("# update\n", encoding="utf-8")
        (payload / "runtime" / "Python312" / "pythonw.exe").write_bytes(b"updated-runtime")
        manifest = {
            "schema_version": "v8.0.1-version-manifest.v1", "product_id": "LocalAIHub",
            "version": "8.0.2-testpayload", "app_relative": "app", "runtime_relative": "runtime/Python312/pythonw.exe",
            "entrypoint": "src.app.launcher",
        }
        manifest_path = payload / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
        atomic_activate_pointer(root, version="8.0.2-testpayload", manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest())
        plan = resolve_launch_plan(root, allow_test_root=True)
        self.assertEqual(plan.version, "8.0.2-testpayload")
        self.assertEqual(json.loads((root / "product.json").read_text(encoding="utf-8"))["version"], "8.0.1")
        self.assertEqual(plan.environment["PYTHONNOUSERSITE"], "1")

    def test_stable_candidate_staging_builds_versioned_payload_without_source_root_install(self) -> None:
        with tempfile.TemporaryDirectory(prefix="lah-801-stage-") as temp:
            root = Path(temp)
            source = root / "source"
            (source / "src" / "app").mkdir(parents=True)
            (source / "src" / "app" / "launcher.py").write_text("# fixture\n", encoding="utf-8")
            (source / "distribution" / "assets").mkdir(parents=True)
            (source / "distribution" / "assets" / "local-ai-hub.ico").write_bytes(b"icon")
            (source / "README.md").write_text("fixture\n", encoding="utf-8")
            subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(source), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(source), "config", "user.name", "test"], check=True)
            subprocess.run(["git", "-C", str(source), "add", "."], check=True)
            subprocess.run(["git", "-C", str(source), "commit", "-m", "fixture"], check=True, capture_output=True)
            runtime = root / "pythonw.exe"
            runtime.write_bytes(b"runtime")
            runtime_root = root / "runtime-root"
            (runtime_root / "Lib" / "site-packages").mkdir(parents=True)
            (runtime_root / "pythonw.exe").write_bytes(b"runtime")
            (runtime_root / "python312.zip").write_bytes(b"stdlib")
            launcher = root / "LocalAIHub.exe"
            launcher.write_bytes(b"launcher")
            install = root / "Temp" / "stable-product"
            data = root / "data"
            result = stage_product(install, runtime_pythonw=runtime, runtime_root=runtime_root, launcher=launcher, data_root=data, source_root=source, allow_test_root=True)
            self.assertEqual(result["version"], "8.0.1")
            self.assertTrue((install / "LocalAIHub.exe").is_file())
            self.assertTrue((install / "versions" / "8.0.1" / "app" / "src" / "app" / "launcher.py").is_file())
            runtime_manifest = json.loads((install / "versions" / "8.0.1" / "runtime" / "Python312" / "runtime-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(runtime_manifest["provider"], "python.org")
            self.assertEqual({item["name"] for item in runtime_manifest["files"]}, {"pythonw.exe", "python312.zip"})
            self.assertFalse((install / "src").exists())
            plan = resolve_launch_plan(install, allow_test_root=True)
            self.assertEqual(plan.version, "8.0.1")

    def test_candidate_staging_requires_bundled_runtime_and_never_uses_system_python(self) -> None:
        with tempfile.TemporaryDirectory(prefix="lah-801-stage-") as temp:
            root = Path(temp)
            with self.assertRaises(StableProductBuildError) as caught:
                stage_product(root / "Temp" / "stable-product", runtime_pythonw=root / "python.exe", launcher=root / "LocalAIHub.exe", data_root=root / "data", source_root=root, allow_test_root=True)
            self.assertEqual(caught.exception.code, "RUNTIME_REPARSE" if (root / "python.exe").is_symlink() else "BUNDLED_RUNTIME_REQUIRED")

    def test_storage_summary_uses_installed_data_root_not_payload_checkout(self) -> None:
        from src.services.storage_manager import overview

        with tempfile.TemporaryDirectory(prefix="lah-801-data-") as temp:
            data = Path(temp)
            (data / "Models").mkdir(parents=True)
            (data / "Models" / "sentinel.bin").write_bytes(b"sentinel")
            with patch.dict(os.environ, {"LOCALAIHUB_INSTALL_ROOT": str(data / "app"), "LOCALAIHUB_DATA_ROOT": str(data)}, clear=False):
                overview._size_cache = None
                result = overview.storage_summary(force=True)
            self.assertEqual(result["areas"]["Models"]["bytes"], len(b"sentinel"))
            self.assertEqual(result["data_location_class"], "persistent_configured")

    def test_production_api_default_port_remains_8765(self) -> None:
        from src.app.main import PORT

        self.assertEqual(PORT, 8765)

    def test_corrupt_pointer_fails_closed_without_python_fallback(self) -> None:
        root, _data, _pointer = self._fixture()
        (root / "current.json").write_text("{\"version\":\"8.0.1\",\"payload_relative\":\"C:/dev\"}", encoding="utf-8")
        with self.assertRaises(StableShellError) as caught:
            resolve_launch_plan(root, allow_test_root=True)
        self.assertEqual(caught.exception.code, "CURRENT_POINTER_INVALID")

    def test_pointer_activation_is_atomic_and_version_bounded(self) -> None:
        root, _data, _pointer = self._fixture()
        payload = root / "versions" / "8.0.2"
        payload.mkdir(parents=True)
        manifest = {
            "schema_version": "v8.0.1-version-manifest.v1", "product_id": "LocalAIHub",
            "version": "8.0.2", "app_relative": "app", "runtime_relative": "runtime/Python312/pythonw.exe",
            "entrypoint": "src.app.launcher",
        }
        manifest_path = payload / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
        result = atomic_activate_pointer(root, version="8.0.2", manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest())
        self.assertEqual(result["schema_version"], POINTER_SCHEMA)
        self.assertEqual(json.loads((root / "current.json").read_text(encoding="utf-8"))["version"], "8.0.2")
        self.assertFalse(list(root.glob(".current.json.*.tmp")))

    def test_close_unknown_never_fakes_active_job_or_cancel_action(self) -> None:
        prompts: list[dict[str, object]] = []
        controller = DesktopCloseController(
            lambda: 0,
            lambda _timeout: (True, "ok"),
            prompts.append,
            prepare_close=lambda: {"status": "unknown", "verification": "unknown", "active_jobs": None, "can_cancel": False, "message": "Không thể xác minh trạng thái tác vụ; Hub chưa đóng để đảm bảo an toàn."},
        )
        self.assertFalse(controller.request_window_close())
        self.assertIsNone(prompts[-1]["active_jobs"])
        self.assertFalse(prompts[-1]["can_cancel"])
        self.assertIn("Không thể xác minh", str(prompts[-1]["message"]))

    def test_close_verified_active_job_allows_cancel_only_when_owned(self) -> None:
        prompts: list[dict[str, object]] = []
        controller = DesktopCloseController(
            lambda: 0,
            lambda _timeout: (True, "ok"),
            prompts.append,
            prepare_close=lambda: {"status": "active_jobs", "verification": "verified", "active_jobs": 2, "can_cancel": True, "message": "active"},
        )
        self.assertFalse(controller.request_window_close())
        self.assertEqual(prompts[-1]["active_jobs"], 2)
        self.assertTrue(prompts[-1]["can_cancel"])

    def test_shortcut_repair_is_stable_executable_only(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "scripts" / "update_managed_shortcuts.ps1").read_text(encoding="utf-8")
        self.assertIn("INSTALLED_PRODUCT_MANIFEST_REQUIRED", source)
        self.assertIn("LocalAIHub.exe", source)
        self.assertIn("IconLocation", source)
        self.assertNotIn("Get-Command pythonw", source)
        self.assertNotIn("wscript.exe", source)
        self.assertNotIn("LocalAIHub.vbs", source)
        self.assertNotIn("LocalAIHub.cmd", source)

    def test_installer_consumes_stable_candidate_and_preserves_data_root(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "distribution" / "installer.iss").read_text(encoding="utf-8")
        self.assertIn("dist\\stable-product\\LocalAIHub.exe", source)
        self.assertIn("versions\\*", source)
        self.assertIn("WriteInstallationConfig", source)
        self.assertNotIn('Source: "..\\Models', source)
        self.assertNotIn('Source: "..\\Output', source)
        self.assertNotIn("wscript.exe", source)

    def test_brand_assets_match_la_identity(self) -> None:
        root = Path(__file__).resolve().parents[1]
        svg = (root / "assets" / "branding" / "local-ai-hub.svg").read_text(encoding="utf-8")
        self.assertIn("#80aaff", svg)
        self.assertIn("#4d7dff", svg)
        self.assertIn(">LA</text>", svg)
        self.assertTrue((root / "distribution" / "assets" / "local-ai-hub.ico").is_file())
        main = (root / "src" / "app" / "main.py").read_text(encoding="utf-8")
        tray = (root / "src" / "app" / "tray.py").read_text(encoding="utf-8")
        self.assertIn("_canonical_icon_path", main)
        self.assertIn("local-ai-hub.ico", tray)
        self.assertNotIn("IDI_APPLICATION", tray)


class PostTagProvenanceTests(unittest.TestCase):
    def _repo(self) -> tuple[Path, str]:
        root = Path(tempfile.mkdtemp(prefix="lah-801-posttag-"))
        (root / "architecture").mkdir()
        source = Path(__file__).resolve().parents[1]
        policy = json.loads((source / "architecture" / "v8_release_policy.json").read_text(encoding="utf-8"))
        policy["approval"]["identity"] = "approved"
        (root / "architecture" / "v8_release_policy.json").write_text(json.dumps(policy), encoding="utf-8")
        subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "test"], check=True)
        (root / "marker.txt").write_text("release", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-m", "fixture"], check=True, capture_output=True)
        commit = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        return root, commit

    def test_post_tag_accepts_existing_exact_tag_while_pre_tag_refuses_it(self) -> None:
        root, commit = self._repo()
        subprocess.run(["git", "-C", str(root), "tag", "v8.0.1"], check=True)
        pre = release_policy_snapshot(root, phase="pre_tag", expected_commit=commit)
        post = release_policy_snapshot(root, phase="post_tag", expected_commit=commit)
        self.assertFalse(pre["tag_available"])
        self.assertIn("V8_RELEASE_TAG_UNAVAILABLE", pre["blockers"])
        self.assertTrue(post["tag_available"])
        self.assertTrue(post["tag_verified"])
        self.assertTrue(post["activation_ready"])
        self.assertEqual(post["blockers"], [])

    def test_post_tag_rejects_wrong_target(self) -> None:
        root, commit = self._repo()
        (root / "later.txt").write_text("later", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-m", "later"], check=True, capture_output=True)
        wrong = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        subprocess.run(["git", "-C", str(root), "tag", "v8.0.1", wrong], check=True)
        post = release_policy_snapshot(root, phase="post_tag", expected_commit=commit)
        self.assertFalse(post["tag_verified"])
        self.assertIn("V8_RELEASE_TAG_MISMATCH", post["blockers"])


if __name__ == "__main__":
    unittest.main()
