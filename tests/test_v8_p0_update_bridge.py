from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from src.app.update_bridge import _restart_after_update


class _Service:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.rollback_reason = None
        self.session = None

    def staged_update(self):
        return {
            "payload_id": "main-bbbbbbbbbbbb",
            "source_commit": "b" * 40,
            "previous": {"version": "main-aaaaaaaaaaaa"},
        }

    def commit_staged_restart(self):
        return {"status": "activated", "payload_id": "main-bbbbbbbbbbbb"}

    def create_restart_session(self, **kwargs):
        self.session = kwargs
        return {"status": "awaiting_candidate"}

    def rollback_pending_restart(self, *, reason):
        self.rollback_reason = reason
        return {"status": "rolled_back"}


class V8P0UpdateBridgeTests(unittest.TestCase):
    def test_commit_authorizes_one_close_and_binds_watchdog_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "LocalAIHub.exe").write_bytes(b"launcher")
            old_app = root / "old-app"
            (old_app / "src" / "app").mkdir(parents=True)
            (old_app / "src" / "app" / "update_watchdog.py").write_text("# fixture", encoding="utf-8")
            old_runtime = root / "old-runtime.exe"
            old_runtime.write_bytes(b"runtime")
            old_plan = SimpleNamespace(app_payload=old_app, runtime_pythonw=old_runtime, data_root=root / "data", version="main-aaaaaaaaaaaa")
            candidate_app = root / "candidate-app"
            (candidate_app / "src" / "app").mkdir(parents=True)
            (candidate_app / "src" / "app" / "update_watchdog.py").write_text("# candidate fixture", encoding="utf-8")
            candidate_runtime = root / "candidate-runtime.exe"
            candidate_runtime.write_bytes(b"runtime")
            candidate_plan = SimpleNamespace(
                app_payload=candidate_app,
                runtime_pythonw=candidate_runtime,
                data_root=root / "data",
                version="main-bbbbbbbbbbbb",
                environment={
                    "LOCALAIHUB_BUILD_SHA": "b" * 40,
                    "LOCALAIHUB_BUILD_PAYLOAD": "main-bbbbbbbbbbbb",
                },
            )
            service = _Service(root)
            calls = {"authorize": 0, "destroy": 0, "abort": 0}

            class Bridge:
                def _authorize_update_restart(self):
                    calls["authorize"] += 1
                    return True

                def _abort_update_restart(self):
                    calls["abort"] += 1

                def _destroy_window(self):
                    calls["destroy"] += 1
                    return True

            with patch.dict(os.environ, {"LOCALAIHUB_INSTALL_ROOT": str(root), "LOCALAIHUB_APP_ROOT": str(old_app)}, clear=False), \
                patch("src.app.update_bridge.resolve_verified_running_plan", return_value=old_plan), \
                patch("src.app.update_bridge.resolve_launch_plan", return_value=candidate_plan), \
                patch("src.app.update_bridge.app_update_service", return_value=service), \
                patch("src.app.update_bridge.subprocess.Popen", return_value=SimpleNamespace()) as popen, \
                patch("src.app.update_bridge.importlib.import_module", return_value=SimpleNamespace(_prepare_owned_api_close=lambda: {"verification": "verified", "active_jobs": 0})):
                result = _restart_after_update(Bridge())
            self.assertEqual(result["status"], "completed")
            self.assertEqual(calls, {"authorize": 1, "destroy": 1, "abort": 0})
            self.assertIsNotNone(service.session)
            self.assertEqual(service.session["payload_id"], "main-bbbbbbbbbbbb")
            self.assertEqual(popen.call_count, 1)
            self.assertEqual(popen.call_args.args[0], [str(candidate_runtime), "-m", "src.app.update_watchdog"])
            self.assertEqual(popen.call_args.kwargs["cwd"], str(candidate_app))
            self.assertEqual(popen.call_args.kwargs["env"]["LOCALAIHUB_APP_ROOT"], str(candidate_app))
            self.assertEqual(popen.call_args.kwargs["env"]["LOCALAIHUB_WATCHDOG_APP_ROOT"], str(candidate_app))

    def test_destroy_failure_rolls_pointer_back_and_does_not_leave_authorized_close(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "LocalAIHub.exe").write_bytes(b"launcher")
            old_app = root / "old-app"
            (old_app / "src" / "app").mkdir(parents=True)
            (old_app / "src" / "app" / "update_watchdog.py").write_text("# fixture", encoding="utf-8")
            old_runtime = root / "old-runtime.exe"
            old_runtime.write_bytes(b"runtime")
            old_plan = SimpleNamespace(app_payload=old_app, runtime_pythonw=old_runtime, data_root=root / "data", version="main-aaaaaaaaaaaa")
            candidate_app = root / "candidate-app"
            (candidate_app / "src" / "app").mkdir(parents=True)
            (candidate_app / "src" / "app" / "update_watchdog.py").write_text("# candidate fixture", encoding="utf-8")
            candidate_runtime = root / "candidate-runtime.exe"
            candidate_runtime.write_bytes(b"runtime")
            candidate_plan = SimpleNamespace(
                app_payload=candidate_app,
                runtime_pythonw=candidate_runtime,
                data_root=root / "data",
                version="main-bbbbbbbbbbbb",
                environment={
                    "LOCALAIHUB_BUILD_SHA": "b" * 40,
                    "LOCALAIHUB_BUILD_PAYLOAD": "main-bbbbbbbbbbbb",
                },
            )
            service = _Service(root)
            calls = {"abort": 0}

            class Bridge:
                def _authorize_update_restart(self):
                    return True

                def _abort_update_restart(self):
                    calls["abort"] += 1

                def _destroy_window(self):
                    return False

            child = SimpleNamespace()
            with patch.dict(os.environ, {"LOCALAIHUB_INSTALL_ROOT": str(root), "LOCALAIHUB_APP_ROOT": str(old_app)}, clear=False), \
                patch("src.app.update_bridge.resolve_verified_running_plan", return_value=old_plan), \
                patch("src.app.update_bridge.resolve_launch_plan", return_value=candidate_plan), \
                patch("src.app.update_bridge.app_update_service", return_value=service), \
                patch("src.app.update_bridge.subprocess.Popen", return_value=child), \
                patch("src.app.update_bridge.terminate_owned_process") as terminate, \
                patch("src.app.update_bridge.importlib.import_module", return_value=SimpleNamespace(_prepare_owned_api_close=lambda: {"verification": "verified", "active_jobs": 0})):
                result = _restart_after_update(Bridge())
            self.assertEqual(result["status"], "error")
            self.assertEqual(result["code"], "DESKTOP_DESTROY_FAILED")
            self.assertEqual(service.rollback_reason, "DESKTOP_DESTROY_FAILED")
            self.assertEqual(calls["abort"], 1)
            terminate.assert_called_once_with(child)


if __name__ == "__main__":
    unittest.main()
