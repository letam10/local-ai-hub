from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.app.main import DesktopBridge, FRONTEND_BOOTSTRAP_TIMEOUT, WEBVIEW_NAVIGATION_FAILED, _error_html, _load_ui_when_ready, _verified_previous_pointer_available
from src.app.stable_shell import POINTER_SCHEMA, PRODUCT_SCHEMA, VERSION_MANIFEST_SCHEMA, StableShellError, atomic_activate_pointer, load_current_pointer, resolve_launch_plan, resolve_verified_running_plan
from src.app.update_bridge import _restart_after_update
from src.app.update_watchdog import _rollback_previous, _session_snapshot, run as run_watchdog
from src.app.readiness import record_event
from src.services.app_update import (
    AppUpdateError,
    AppUpdateService,
    CANDIDATE_API_PREFLIGHT_TIMEOUT_SECONDS,
    CANDIDATE_BOOTSTRAP_PREFLIGHT_TIMEOUT_SECONDS,
    UPDATE_SCHEMA,
)
from src.services.app_update import UpdateCandidate
from src.shared.runtime_identity import API_PROTOCOL_VERSION, APP_USER_MODEL_ID, PRODUCT_ID, api_identity
from src.shared.version import PRODUCT_VERSION


TASK_TEMP = Path(os.environ.get("LOCALAIHUB_TEST_TEMP") or tempfile.gettempdir())


class _ReadyTransport:
    def auth_state(self):
        return SimpleNamespace(status="ready", code=None, transport="fixture")

    def api_json(self, _endpoint, _fields=None):
        return {"status": "ahead"}


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


class _RawResponse(_HealthResponse):
    def __init__(self, raw: bytes):
        self._raw = raw
        self.status = 200

    def read(self, _limit: int = 0) -> bytes:
        return self._raw


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
            with patch("src.services.app_update.subprocess.Popen", return_value=child) as popen, patch("src.services.app_update.urllib.request.urlopen", side_effect=[_HealthResponse(body), _RawResponse(b'<title>Local AI Hub</title><script src="/ui/app.js"></script>'), _HealthResponse({"health": body})]), patch("src.services.app_update.terminate_owned_process", side_effect=lambda process: process.terminate()):
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

    def test_candidate_bootstrap_preflight_allows_populated_data_root_latency(self):
        self.assertGreaterEqual(CANDIDATE_BOOTSTRAP_PREFLIGHT_TIMEOUT_SECONDS, 10.0)
        self.assertLess(CANDIDATE_BOOTSTRAP_PREFLIGHT_TIMEOUT_SECONDS, CANDIDATE_API_PREFLIGHT_TIMEOUT_SECONDS)

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

    def test_watchdog_parent_timeout_rolls_back_without_killing_parent(self):
        with self._temp() as temporary:
            root = Path(temporary) / "install"
            self._install(root, current="main-aaaaaaaaaaaa", current_commit="a" * 40, previous="8.0.1")
            previous = json.loads((root / "update-state" / "previous-current.json").read_text())
            (root / "update-state" / "pending-health.json").write_text(json.dumps({
                "schema_version": "local-ai-hub-pending-health.v1", "payload_id": "main-aaaaaaaaaaaa",
                "source_commit": "a" * 40, "previous": previous,
            }), encoding="utf-8")
            before = load_current_pointer(root)
            with patch("src.app.update_watchdog._wait_for_pid_exit", return_value=False), patch("src.app.update_watchdog._launch_stable") as launch:
                result = run_watchdog(app_root=root, wait_pid=99999, timeout_seconds=5)
            self.assertEqual(result["status"], "rolled_back")
            self.assertEqual(result["code"], "WATCHDOG_PARENT_TIMEOUT_ROLLBACK")
            self.assertEqual(load_current_pointer(root)["version"], "8.0.1")
            self.assertFalse((root / "update-state" / "pending-health.json").exists())
            launch.assert_not_called()
            self.assertEqual(before["version"], "main-aaaaaaaaaaaa")

    def test_watchdog_session_accepts_only_matching_nonce_identity_and_port(self):
        with self._temp() as temporary:
            root = Path(temporary) / "install"
            state = root / "update-state"
            state.mkdir(parents=True)
            session = state / "restart-session.json"
            nonce = "a" * 32
            session.write_text(json.dumps({
                "schema_version": "local-ai-hub-restart-session.v1", "payload_id": "main-bbbbbbbbbbbb",
                "source_commit": "b" * 40, "nonce": nonce, "parent_pid": 1,
                "api_port": 52943, "api_pid": 1234, "status": "frontend_ready",
            }), encoding="utf-8")
            with patch.dict(os.environ, {"LOCALAIHUB_WATCHDOG_SESSION_PATH": str(session), "LOCALAIHUB_WATCHDOG_SESSION_NONCE": nonce}, clear=False):
                self.assertEqual(_session_snapshot()["api_port"], 52943)
            with patch.dict(os.environ, {"LOCALAIHUB_WATCHDOG_SESSION_PATH": str(session), "LOCALAIHUB_WATCHDOG_SESSION_NONCE": "b" * 32}, clear=False):
                self.assertIsNone(_session_snapshot())

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

    def test_frontend_ready_handshake_clears_pending_only_after_exact_health(self):
        with self._temp() as temporary:
            root = Path(temporary) / "install"
            self._install(root, current="main-aaaaaaaaaaaa", current_commit="a" * 40, previous="8.0.1")
            previous = json.loads((root / "update-state" / "previous-current.json").read_text())
            (root / "update-state" / "pending-health.json").write_text(json.dumps({
                "schema_version": "local-ai-hub-pending-health.v1", "payload_id": "main-aaaaaaaaaaaa",
                "source_commit": "a" * 40, "previous": previous,
            }), encoding="utf-8")
            data_root = root / "data"
            health = {"status": "healthy", **api_identity(product_version=PRODUCT_VERSION, installation_root=root, data_root=data_root), "api_protocol_version": API_PROTOCOL_VERSION, "build_source_commit": "a" * 40, "build_payload_id": "main-aaaaaaaaaaaa"}
            with patch.dict(os.environ, {"LOCALAIHUB_INSTALL_ROOT": str(root), "LOCALAIHUB_PORT": "8765"}, clear=False), patch("src.app.main.urllib.request.urlopen", return_value=_HealthResponse(health)), patch("src.app.main._record_startup_event"):
                bridge = DesktopBridge()
                result = bridge.confirm_frontend_ready()
            self.assertEqual(result["status"], "ready")
            self.assertFalse((root / "update-state" / "pending-health.json").exists())
            self.assertTrue(bridge._frontend_ready_event.is_set())

    def test_nested_frontend_bridge_confirms_ready_without_top_level_method_lookup(self):
        with patch.object(DesktopBridge, "confirm_frontend_ready", return_value={"status": "ready"}) as confirm:
            bridge = DesktopBridge()
            self.assertEqual(bridge.frontend.confirm_frontend_ready(), {"status": "ready"})
            confirm.assert_called_once_with()

    def test_frontend_ready_telemetry_event_commits_native_readiness(self):
        with patch.object(DesktopBridge, "confirm_frontend_ready", return_value={"status": "ready"}) as confirm, patch("src.app.main._record_startup_event"):
            bridge = DesktopBridge()
            result = bridge.frontend.record("frontend_ready")
            self.assertEqual(result["status"], "ready")
            confirm.assert_called_once_with()

    def test_normal_legacy_payload_without_build_metadata_can_confirm_frontend(self):
        with self._temp() as temporary:
            root = Path(temporary) / "install"
            self._install(root, current="old", current_commit="c" * 40)
            (root / "versions" / "old" / "build.json").unlink()
            health = {"status": "healthy", "product_id": PRODUCT_ID, "product_version": PRODUCT_VERSION, "api_protocol_version": API_PROTOCOL_VERSION, "app_user_model_id": APP_USER_MODEL_ID, "installation_id": "c" * 32}
            with patch.dict(os.environ, {"LOCALAIHUB_INSTALL_ROOT": str(root), "LOCALAIHUB_PORT": "8765"}, clear=False), patch("src.app.main.urllib.request.urlopen", return_value=_HealthResponse(health)), patch("src.app.main._record_startup_event"):
                bridge = DesktopBridge()
                result = bridge.confirm_frontend_ready()
            self.assertEqual(result["status"], "ready")
            self.assertTrue(bridge._frontend_ready_event.is_set())

    def test_navigation_failure_and_frontend_timeout_keep_pending_and_show_distinct_ui(self):
        class Window:
            def __init__(self, fail=False):
                self.fail = fail
                self.loaded_html = []
                self.loaded_urls = []
            def load_url(self, url):
                if self.fail:
                    raise RuntimeError("navigation")
                self.loaded_urls.append(url)
            def load_html(self, value):
                self.loaded_html.append(value)

        with patch("src.app.main.ensure_api", return_value=None), patch("src.app.main._probe_api", return_value=("compatible_owned_or_reusable", {})), patch("src.app.main._record_startup_event"):
            navigation_window = Window(fail=True)
            _load_ui_when_ready(navigation_window, DesktopBridge())
            self.assertIn(WEBVIEW_NAVIGATION_FAILED, navigation_window.loaded_html[0])
            timeout_window = Window()
            with patch("src.app.main.FRONTEND_READY_TIMEOUT_SECONDS", 0.01):
                _load_ui_when_ready(timeout_window, DesktopBridge())
            self.assertIn(FRONTEND_BOOTSTRAP_TIMEOUT, timeout_window.loaded_html[0])

    def test_loopback_frontend_ready_event_drives_native_confirmation(self):
        class Window:
            def __init__(self):
                self.loaded_urls = []
                self.loaded_html = []
            def load_url(self, url):
                self.loaded_urls.append(url)
            def load_html(self, value):
                self.loaded_html.append(value)

        with tempfile.TemporaryDirectory(prefix="v8-frontend-ready-") as folder:
            root = Path(folder)
            payload_id = "main-aaaaaaaaaaaa"
            (root / "versions" / payload_id).mkdir(parents=True)
            (root / "current.json").write_text(json.dumps({"version": payload_id}), encoding="utf-8")
            (root / "versions" / payload_id / "build.json").write_text(json.dumps({"source_commit": "a" * 40}), encoding="utf-8")
            pid = os.getpid()
            record_event(root, "desktop_started", status="starting", pid=pid)
            record_event(root, "frontend_bootstrap_completed", status="running", pid=pid)
            record_event(root, "frontend_dom_visible", status="running", route="dashboard", pid=pid)
            record_event(root, "frontend_rendered", status="running", route="dashboard", pid=pid)
            record_event(root, "frontend_ready", status="running", pid=pid)
            bridge = DesktopBridge()
            window = Window()
            with patch.dict(os.environ, {"LOCALAIHUB_INSTALL_ROOT": str(root)}, clear=False), patch("src.app.main.ensure_api", return_value=None), patch("src.app.main._probe_api", return_value=("compatible_owned_or_reusable", {})), patch("src.app.main._record_startup_event"), patch.object(bridge, "confirm_frontend_ready", return_value={"status": "ready"}) as confirm:
                _load_ui_when_ready(window, bridge)
            self.assertTrue(window.loaded_urls)
            self.assertFalse(window.loaded_html)
            confirm.assert_called_once_with()

    def test_watchdog_bridge_requires_a_persisted_staged_candidate_before_restart(self):
        with self._temp() as temporary:
            root = Path(temporary) / "install"
            old_app = root / "versions" / "old" / "app"
            new_app = root / "versions" / "main-bbbbbbbbbbbb" / "app"
            old_app.mkdir(parents=True)
            new_app.mkdir(parents=True)
            (old_app / "src" / "app").mkdir(parents=True)
            (old_app / "src" / "app" / "update_watchdog.py").write_text("# old watchdog\n", encoding="utf-8")
            (root / "LocalAIHub.exe").write_bytes(b"launcher")
            old_runtime = root / "versions" / "old" / "runtime" / "Python312" / "pythonw.exe"
            old_runtime.parent.mkdir(parents=True)
            old_runtime.write_bytes(b"old-runtime")
            new_runtime = root / "versions" / "main-bbbbbbbbbbbb" / "runtime" / "Python312" / "pythonw.exe"  # deliberately missing
            old_plan = SimpleNamespace(app_payload=old_app, runtime_pythonw=old_runtime, data_root=root / "data", version="old")
            new_plan = SimpleNamespace(app_payload=new_app, runtime_pythonw=new_runtime, data_root=root / "data", version="main-bbbbbbbbbbbb")
            captured = {}
            class Bridge:
                def _destroy_window(self):
                    captured["destroyed"] = True
            with patch.dict(os.environ, {"LOCALAIHUB_INSTALL_ROOT": str(root), "LOCALAIHUB_APP_ROOT": str(old_app)}, clear=False), patch("src.app.update_bridge.resolve_launch_plan", return_value=new_plan), patch("src.app.update_bridge.resolve_verified_running_plan", return_value=old_plan), patch("src.app.update_bridge.subprocess.Popen", return_value=SimpleNamespace()) as popen, patch("src.app.update_bridge.importlib.import_module", return_value=SimpleNamespace(_prepare_owned_api_close=lambda: {"verification": "verified", "active_jobs": 0})):
                result = _restart_after_update(Bridge())
            self.assertEqual(result["status"], "blocked")
            # The stable-shell root guard is platform-specific for a synthetic
            # tempfile: Windows rejects the Temp path before reading it,
            # whereas POSIX reaches the deliberately absent manifest. Both
            # outcomes are the same fail-closed staged-candidate refusal.
            self.assertIn(result["code"], {"STAGED_UPDATE_INVALID", "INSTALL_ROOT_NOT_PRODUCTION", "MANIFEST_UNREADABLE"})
            popen.assert_not_called()
            self.assertNotIn("destroyed", captured)

    def test_watchdog_bridge_blocks_old_payload_ownership_mismatch(self):
        with self._temp() as temporary:
            root = Path(temporary) / "install"
            root.mkdir(parents=True)
            class Bridge:
                def _destroy_window(self):
                    raise AssertionError("must not destroy")
            with patch.dict(os.environ, {"LOCALAIHUB_INSTALL_ROOT": str(root), "LOCALAIHUB_APP_ROOT": str(root / "outside" )}, clear=False), patch("src.app.update_bridge.resolve_launch_plan", return_value=SimpleNamespace(app_payload=root / "new", version="main-bbbbbbbbbbbb")), patch("src.app.update_bridge.resolve_verified_running_plan", side_effect=StableShellError("RUNNING_PAYLOAD_OWNERSHIP_INVALID")), patch("src.app.update_bridge.subprocess.Popen") as popen:
                result = _restart_after_update(Bridge())
            self.assertEqual(result["status"], "blocked")
            popen.assert_not_called()

    def test_verified_running_plan_binds_old_app_to_installation_versions_root(self):
        with self._temp() as temporary:
            root = Path(temporary) / "install"
            self._install(root, current="old", current_commit="c" * 40)
            running = root / "versions" / "old" / "app"
            plan = resolve_verified_running_plan(root, running, allow_test_root=True)
            self.assertEqual(plan.app_payload, running)
            self.assertEqual(plan.runtime_pythonw.name, "pythonw.exe")
            with self.assertRaises(StableShellError):
                resolve_verified_running_plan(root, root / "outside" / "app", allow_test_root=True)

    def test_first_watchdog_bootstrap_is_explicit_and_exact_commit_bound(self):
        script = (Path(__file__).resolve().parents[1] / "scripts" / "bootstrap_first_watchdog_payload.py").read_text(encoding="utf-8")
        self.assertIn("--expected-commit", script)
        self.assertIn("--activate", script)
        self.assertIn("EXACT_MAIN_ARTIFACT_UNAVAILABLE", script)
        self.assertIn("BOOTSTRAP_FAILURE_POINTER_CHANGED", script)

    def test_first_watchdog_bootstrap_failure_preserves_current_pointer(self):
        from scripts import bootstrap_first_watchdog_payload as bootstrap

        with self._temp() as temporary:
            root = Path(temporary) / "install"
            self._install(root, current="old", current_commit="c" * 40)
            before = load_current_pointer(root)
            expected = "d" * 40
            class Candidate:
                source_commit = expected
            class Service:
                def _latest_candidate(self):
                    return Candidate()
                def prepare(self):
                    raise RuntimeError("fixture_preflight_failed")
            with patch.object(bootstrap, "_source_commit", return_value=expected), patch.object(bootstrap, "AppUpdateService", return_value=Service()):
                with self.assertRaisesRegex(RuntimeError, "fixture_preflight_failed"):
                    bootstrap.run(source_root=root, install_root=root, expected_commit=expected, activate=True)
            self.assertEqual(load_current_pointer(root), before)


if __name__ == "__main__":
    unittest.main()
