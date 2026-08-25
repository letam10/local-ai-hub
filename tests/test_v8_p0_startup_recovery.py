from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.app.main import _error_html, _verified_previous_pointer_available
from src.app.stable_shell import POINTER_SCHEMA, PRODUCT_SCHEMA, VERSION_MANIFEST_SCHEMA, atomic_activate_pointer, load_current_pointer, resolve_launch_plan
from src.app.update_watchdog import _rollback_previous
from src.services.app_update import AppUpdateError, AppUpdateService, UPDATE_SCHEMA
from src.services.app_update import UpdateCandidate
from src.shared.runtime_identity import API_PROTOCOL_VERSION, APP_USER_MODEL_ID, PRODUCT_ID, api_identity
from src.shared.version import PRODUCT_VERSION


TASK_TEMP = Path(os.environ.get("LOCALAIHUB_TEST_TEMP") or tempfile.gettempdir())


class _ReadyTransport:
    def auth_state(self):
        return SimpleNamespace(status="ready", code=None, transport="fixture")


class _HealthResponse:
    def __init__(self, value: dict[str, object]):
        self._value = value
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int = 0) -> bytes:
        return json.dumps(self._value).encode("utf-8")


class _Child:
    pid = 31337

    def __init__(self):
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def kill(self):
        self.returncode = -9


class V8P0StartupRecoveryTests(unittest.TestCase):
    def _temp(self):
        return tempfile.TemporaryDirectory(prefix="v8-p0-startup-", dir=str(TASK_TEMP))

    @staticmethod
    def _install(root: Path, *, current: str, current_commit: str, previous: str | None = None) -> None:
        data = root / "data"
        data.mkdir(parents=True, exist_ok=True)
        (root / "installation.json").write_text(json.dumps({
            "schema_version": "v8.0.1-installation.v1", "product_id": PRODUCT_ID,
            "app_root": str(root), "data_root": str(data), "app_user_model_id": APP_USER_MODEL_ID,
            "launcher": "LocalAIHub.exe",
        }), encoding="utf-8")
        (root / "product.json").write_text(json.dumps({
            "schema_version": PRODUCT_SCHEMA, "product_id": PRODUCT_ID, "version": PRODUCT_VERSION,
            "launcher": "LocalAIHub.exe", "icon": "local-ai-hub.ico", "current_pointer": "current.json",
        }), encoding="utf-8")
        (root / "LocalAIHub.exe").write_bytes(b"stable")
        (root / "local-ai-hub.ico").write_bytes(b"icon")
        for version, commit in [(current, current_commit)] + ([(previous, "b" * 40)] if previous else []):
            payload = root / "versions" / version
            (payload / "app" / "src" / "app").mkdir(parents=True, exist_ok=True)
            (payload / "runtime" / "Python312").mkdir(parents=True, exist_ok=True)
            (payload / "app" / "src" / "app" / "launcher.py").write_text("# fixture\n", encoding="utf-8")
            (payload / "runtime" / "Python312" / "pythonw.exe").write_bytes(b"runtime")
            manifest = {
                "schema_version": VERSION_MANIFEST_SCHEMA, "product_id": PRODUCT_ID, "version": version,
                "app_relative": "app", "runtime_relative": "runtime/Python312/pythonw.exe", "entrypoint": "src.app.launcher",
            }
            manifest_path = payload / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
            (payload / "build.json").write_text(json.dumps({"schema_version": "local-ai-hub-build-info.v1", "source_commit": commit}), encoding="utf-8")
        current_manifest = root / "versions" / current / "manifest.json"
        pointer = {"schema_version": POINTER_SCHEMA, "version": current, "payload_relative": f"versions/{current}", "manifest_sha256": hashlib.sha256(current_manifest.read_bytes()).hexdigest()}
        (root / "current.json").write_text(json.dumps(pointer, sort_keys=True), encoding="utf-8")
        if previous:
            previous_manifest = root / "versions" / previous / "manifest.json"
            previous_pointer = {"schema_version": POINTER_SCHEMA, "version": previous, "payload_relative": f"versions/{previous}", "manifest_sha256": hashlib.sha256(previous_manifest.read_bytes()).hexdigest()}
            (root / "update-state").mkdir(parents=True, exist_ok=True)
            (root / "update-state" / "previous-current.json").write_text(json.dumps(previous_pointer), encoding="utf-8")

    def test_candidate_api_preflight_uses_ephemeral_port_and_exact_identity(self):
        with self._temp() as temporary:
            root = Path(temporary) / "install"
            app = root / "candidate" / "app"
            runtime = root / "candidate" / "runtime" / "Python312" / "pythonw.exe"
            app.mkdir(parents=True)
            runtime.parent.mkdir(parents=True)
            runtime.write_bytes(b"runtime")
            child = _Child()
            source = "a" * 40
            payload_id = "main-aaaaaaaaaaaa"
            data_root = root / "work" / "candidate-data"
            expected = api_identity(product_version=PRODUCT_VERSION, installation_root=root, app_root=app, data_root=data_root)
            body = {"status": "healthy", **expected, "api_protocol_version": API_PROTOCOL_VERSION, "build_source_commit": source, "build_payload_id": payload_id}
            with patch("src.services.app_update.subprocess.Popen", return_value=child) as popen, patch("src.services.app_update.urllib.request.urlopen", return_value=_HealthResponse(body)), patch("src.services.app_update.terminate_owned_process", side_effect=lambda process: process.terminate()):
                result = AppUpdateService(transport=_ReadyTransport(), allow_test_root=True)._preflight_candidate_api(
                    install_root=root, app_root=app, runtime_pythonw=runtime, payload_id=payload_id,
                    source_commit=source, work_root=root / "work",
                )
            self.assertEqual(result["status"], "passed")
            command = popen.call_args.args[0]
            environment = popen.call_args.kwargs["env"]
            self.assertEqual(command[-2:], ["-m", "src.services.api.api_server"])
            self.assertNotEqual(environment["LOCALAIHUB_PORT"], "8765")
            self.assertEqual(environment["LOCALAIHUB_BUILD_SHA"], source)
            self.assertEqual(child.returncode, 0)

    def test_import_pass_api_start_failure_keeps_current_pointer_and_preserves_staging(self):
        with self._temp() as temporary:
            root = Path(temporary) / "install"
            old = "old"
            old_commit = "c" * 40
            self._install(root, current=old, current_commit=old_commit)
            candidate_commit = "d" * 40
            candidate = UpdateCandidate(55, candidate_commit, 77, "local-ai-hub-main-update")
            manifest = {
                "schema_version": UPDATE_SCHEMA, "product_id": PRODUCT_ID, "product_version": PRODUCT_VERSION,
                "channel": "main", "source_commit": candidate_commit, "payload_id": f"main-{candidate_commit[:12]}",
                "runtime_strategy": "reuse-current", "archive": "update.zip", "archive_sha256": "e" * 64, "file_count": 1,
            }
            contract = {"schema_version": "local-ai-hub-update-contract.v1", "update_kind": "APP_ONLY", "app_protocol": "v8-api.v1", "runtime_contract": "reuse-current", "runtime_version": None, "runtime_hash": None, "minimum_launcher_version": "v8.0.1", "source_commit": candidate_commit}
            archive = root / "fixture.zip"
            archive.write_bytes(b"zip")

            def extract(_archive, destination, **_kwargs):
                (destination / "app" / "src").mkdir(parents=True)
                (destination / "app" / "src" / "marker.py").write_text("# candidate\n", encoding="utf-8")
                return 1

            with patch.dict(os.environ, {"LOCALAIHUB_INSTALL_ROOT": str(root)}, clear=False), patch.object(AppUpdateService, "_latest_candidate", return_value=candidate), patch.object(AppUpdateService, "_download_candidate"), patch.object(AppUpdateService, "_validate_download", return_value=(manifest, contract, archive)), patch("src.services.app_update._safe_extract_app_archive", side_effect=extract), patch.object(AppUpdateService, "_validate_staged_imports"), patch.object(AppUpdateService, "_preflight_candidate_api", side_effect=AppUpdateError("UPDATE_CANDIDATE_API_EXITED")):
                with self.assertRaisesRegex(AppUpdateError, "UPDATE_CANDIDATE_API_EXITED"):
                    AppUpdateService(transport=_ReadyTransport(), allow_test_root=True).prepare()
            self.assertEqual(load_current_pointer(root)["version"], old)
            self.assertFalse((root / "update-state" / "pending-health.json").exists())
            failures = list((root / "staging" / "failures").glob("main-*"))
            self.assertTrue(failures)
            self.assertTrue(any(path.rglob("candidate-api-preflight.log") for path in failures))

    def test_watchdog_rollback_is_atomic_and_idempotent(self):
        with self._temp() as temporary:
            root = Path(temporary) / "install"
            self._install(root, current="main-aaaaaaaaaaaa", current_commit="a" * 40, previous="8.0.1")
            marker = root / "update-state" / "pending-health.json"
            marker.write_text(json.dumps({"schema_version": "local-ai-hub-pending-health.v1"}), encoding="utf-8")
            self.assertTrue(_rollback_previous(root, reason="fixture"))
            self.assertEqual(load_current_pointer(root)["version"], "8.0.1")
            self.assertFalse(marker.exists())
            self.assertTrue(_rollback_previous(root, reason="fixture-again"))
            self.assertEqual(load_current_pointer(root)["version"], "8.0.1")

    def test_recovery_screen_is_bounded_vietnamese_and_no_paths(self):
        html = _error_html("API_STARTUP_TIMEOUT")
        self.assertIn("Không thể kết nối Local AI Hub API", html)
        self.assertIn("Thử lại", html)
        self.assertIn("data-error-code=\"API_STARTUP_TIMEOUT\"", html)
        self.assertNotIn("C:\\", html)
        self.assertNotIn("D:\\", html)

    def test_verified_previous_pointer_requires_manifest_hash(self):
        with self._temp() as temporary:
            root = Path(temporary) / "install"
            self._install(root, current="main-aaaaaaaaaaaa", current_commit="a" * 40, previous="8.0.1")
            with patch.dict(os.environ, {"LOCALAIHUB_INSTALL_ROOT": str(root)}, clear=False):
                self.assertTrue(_verified_previous_pointer_available())
                previous = root / "update-state" / "previous-current.json"
                value = json.loads(previous.read_text())
                value["manifest_sha256"] = "0" * 64
                previous.write_text(json.dumps(value), encoding="utf-8")
                self.assertFalse(_verified_previous_pointer_available())

    def test_launch_plan_propagates_build_identity_without_paths(self):
        with self._temp() as temporary:
            root = Path(temporary) / "install"
            self._install(root, current="main-aaaaaaaaaaaa", current_commit="a" * 40)
            plan = resolve_launch_plan(root, allow_test_root=True)
            self.assertEqual(plan.environment["LOCALAIHUB_BUILD_SHA"], "a" * 40)
            self.assertEqual(plan.environment["LOCALAIHUB_BUILD_PAYLOAD"], "main-aaaaaaaaaaaa")


if __name__ == "__main__":
    unittest.main()
