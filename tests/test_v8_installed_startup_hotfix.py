"""P0 regressions for split-install startup, bundled runtime selection and cleanup."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.app import main as desktop
from src.services.api import core
from src.shared.runtime_identity import api_identity


class _Process:
    def __init__(self, returncode: int | None = None) -> None:
        self.returncode = returncode
        self.pid = 43210

    def poll(self) -> int | None:
        return self.returncode


class InstalledStartupHotfixTests(unittest.TestCase):
    def _split_roots(self) -> tuple[Path, Path, Path, Path]:
        root = Path(tempfile.mkdtemp(prefix="lah-v801-startup-"))
        install = root / "install"
        payload = install / "versions" / "8.0.1" / "app"
        data = root / "data"
        bundled = install / "versions" / "8.0.1" / "runtime" / "Python312" / "pythonw.exe"
        payload.mkdir(parents=True)
        data.mkdir()
        bundled.parent.mkdir(parents=True)
        bundled.write_bytes(b"bundled-runtime")
        return install, payload, data, bundled

    def _env(self, install: Path, payload: Path, data: Path) -> dict[str, str]:
        return {
            "LOCALAIHUB_INSTALL_ROOT": str(install),
            "LOCALAIHUB_APP_ROOT": str(payload),
            "LOCALAIHUB_DATA_ROOT": str(data),
            "LOCALAIHUB_PORT": "8765",
            "LOCALAIHUB_BIND_HOST": "127.0.0.1",
        }

    def _plan(self, install: Path, payload: Path, data: Path, bundled: Path) -> SimpleNamespace:
        return SimpleNamespace(
            app_payload=payload,
            runtime_pythonw=bundled,
            environment={
                "LOCALAIHUB_INSTALL_ROOT": str(install),
                "LOCALAIHUB_APP_ROOT": str(payload),
                "LOCALAIHUB_DATA_ROOT": str(data),
                "PYTHONPATH": str(payload),
                "PYTHONNOUSERSITE": "1",
            },
        )

    def test_split_installation_identity_uses_stable_install_root_for_desktop_and_api(self) -> None:
        install, payload, data, _bundled = self._split_roots()
        env = self._env(install, payload, data)
        with patch.object(desktop, "ROOT", payload), patch.dict("os.environ", env, clear=False), patch.object(core, "hub_config", return_value={}), patch.object(core, "query_gpu", return_value={"available": False}), patch.object(core, "list_jobs", return_value=[]):
            expected = desktop._expected_api_identity()
            health = core.health()
        expected_id = api_identity(product_version="8.0.1", installation_root=install, app_root=payload, data_root=data)["installation_id"]
        self.assertEqual(expected["installation_id"], expected_id)
        self.assertEqual(health["installation_id"], expected["installation_id"])
        self.assertNotEqual(expected["installation_id"], api_identity(product_version="8.0.1", app_root=payload, data_root=data)["installation_id"])

    def test_installed_mode_ignores_legacy_environments_and_spawns_bundled_runtime(self) -> None:
        install, payload, data, bundled = self._split_roots()
        for name in ("core", "hub"):
            legacy = data / "Environments" / name / "Scripts"
            legacy.mkdir(parents=True)
            (legacy / "python.exe").write_bytes(b"legacy-runtime")
        process = _Process()
        startup = MagicMock()
        startup.__enter__.return_value = True
        startup.__exit__.return_value = None
        with patch.object(desktop, "ROOT", payload), patch.dict("os.environ", self._env(install, payload, data), clear=False), patch.object(desktop, "_select_api_port", return_value=(55432, desktop.API_PROBE_LOCAL_INCOMPATIBLE)), patch.object(desktop, "_probe_api", return_value=(desktop.API_PROBE_ABSENT, {})), patch.object(desktop, "_scoped_mutex", return_value="mutex"), patch.object(desktop, "startup_mutex", return_value=startup), patch.object(desktop, "resolve_launch_plan", return_value=self._plan(install, payload, data, bundled)), patch.object(desktop, "CoreRuntimeResolver") as resolver, patch.object(desktop, "popen_hidden", return_value=process) as spawn, patch.object(desktop, "_wait_for_api", return_value=True), patch.object(desktop, "_startup_log_path", return_value=data / "Logs" / "api_startup.log"):
            selected = desktop.ensure_api(timeout_seconds=1.0)
        self.assertIs(selected, process)
        resolver.assert_not_called()
        self.assertEqual(spawn.call_args.args[0][0], str(bundled))
        self.assertEqual(spawn.call_args.args[0][1], "-c")
        self.assertIn("sys.path.insert(0", spawn.call_args.args[0][2])
        self.assertIn(json.dumps(str(payload)), spawn.call_args.args[0][2])
        self.assertIn("src.services.api.api_server", spawn.call_args.args[0][2])
        self.assertEqual(spawn.call_args.kwargs["cwd"], payload)
        child_env = spawn.call_args.kwargs["env"]
        self.assertEqual(child_env["LOCALAIHUB_INSTALL_ROOT"], str(install))
        self.assertEqual(child_env["LOCALAIHUB_APP_ROOT"], str(payload))
        self.assertEqual(child_env["LOCALAIHUB_DATA_ROOT"], str(data))
        self.assertNotIn("LOCALAIHUB_ROOT", child_env)

    def test_failed_readiness_terminates_only_owned_child_and_records_code(self) -> None:
        install, payload, data, bundled = self._split_roots()
        process = _Process()
        startup = MagicMock()
        startup.__enter__.return_value = True
        startup.__exit__.return_value = None
        with patch.object(desktop, "ROOT", payload), patch.dict("os.environ", self._env(install, payload, data), clear=False), patch.object(desktop, "_select_api_port", return_value=(55432, desktop.API_PROBE_LOCAL_INCOMPATIBLE)), patch.object(desktop, "_probe_api", return_value=(desktop.API_PROBE_ABSENT, {})), patch.object(desktop, "_scoped_mutex", return_value="mutex"), patch.object(desktop, "startup_mutex", return_value=startup), patch.object(desktop, "resolve_launch_plan", return_value=self._plan(install, payload, data, bundled)), patch.object(desktop, "popen_hidden", return_value=process), patch.object(desktop, "_wait_for_api", return_value=False), patch.object(desktop, "terminate_owned_process") as terminate, patch.object(desktop, "_startup_log_path", return_value=data / "Logs" / "api_startup.log"):
            with self.assertRaisesRegex(RuntimeError, desktop.API_STARTUP_TIMEOUT):
                desktop.ensure_api(timeout_seconds=0.1)
        terminate.assert_called_once_with(process)
        diagnostic = data / "Logs" / "api_startup.log"
        self.assertTrue(diagnostic.is_file())
        self.assertIn(desktop.API_STARTUP_TIMEOUT, diagnostic.read_text(encoding="utf-8"))
        self.assertFalse((payload / "Logs" / "api_startup.log").exists())

    def test_exited_child_fails_fast_and_uses_stable_error_code(self) -> None:
        install, payload, data, bundled = self._split_roots()
        process = _Process(returncode=23)
        startup = MagicMock()
        startup.__enter__.return_value = True
        startup.__exit__.return_value = None
        with patch.object(desktop, "ROOT", payload), patch.dict("os.environ", self._env(install, payload, data), clear=False), patch.object(desktop, "_select_api_port", return_value=(55432, desktop.API_PROBE_LOCAL_INCOMPATIBLE)), patch.object(desktop, "_probe_api", return_value=(desktop.API_PROBE_ABSENT, {})), patch.object(desktop, "_scoped_mutex", return_value="mutex"), patch.object(desktop, "startup_mutex", return_value=startup), patch.object(desktop, "resolve_launch_plan", return_value=self._plan(install, payload, data, bundled)), patch.object(desktop, "popen_hidden", return_value=process), patch.object(desktop, "_wait_for_api", return_value=False), patch.object(desktop, "terminate_owned_process") as terminate, patch.object(desktop, "_startup_log_path", return_value=data / "Logs" / "api_startup.log"):
            with self.assertRaisesRegex(RuntimeError, desktop.API_STARTUP_EXITED):
                desktop.ensure_api(timeout_seconds=20.0)
        terminate.assert_called_once_with(process)

    def test_error_page_never_echoes_raw_path_or_exception(self) -> None:
        page = desktop._error_html(r"C:\Users\secret\payload\traceback.txt")
        self.assertIn(desktop.API_STARTUP_FAILED, page)
        self.assertNotIn("traceback.txt", page)
        self.assertNotIn("C:\\Users", page)


if __name__ == "__main__":
    unittest.main()
