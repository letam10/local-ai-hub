"""Deterministic API identity and incompatible-listener regressions."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from src.app import main as desktop
from src.services.api import core
from src.shared.runtime_identity import api_identity
from src.shared.version import PRODUCT_VERSION


class _Response:
    status = 200

    def __init__(self, value: object) -> None:
        self._raw = json.dumps(value).encode("utf-8")

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _limit: int = -1) -> bytes:
        return self._raw


class _OwnedProcess:
    def poll(self) -> None:
        return None


class ApiIdentityCollisionTests(unittest.TestCase):
    def test_health_exposes_bounded_identity_without_raw_paths(self) -> None:
        with patch.dict("os.environ", {"LOCALAIHUB_APP_ROOT": r"C:\\installed\\app", "LOCALAIHUB_DATA_ROOT": r"D:\\LocalAIHub", "LOCALAIHUB_PORT": "55431", "LOCALAIHUB_BIND_HOST": "127.0.0.1"}, clear=False), patch.object(core, "hub_config", return_value={"bind_host": "127.0.0.1", "api_port": 8765}), patch.object(core, "query_gpu", return_value={"available": False}), patch.object(core, "list_jobs", return_value=[]):
            value = core.health()
        self.assertEqual(value["product_id"], "LocalAIHub")
        self.assertEqual(value["product_version"], PRODUCT_VERSION)
        self.assertEqual(value["api_protocol_version"], "v8-api.v1")
        self.assertEqual(value["app_user_model_id"], "LocalAIHub.Desktop")
        self.assertEqual(value["process_owner"], "localaihub.api")
        self.assertEqual(value["bind"], "127.0.0.1:55431")
        self.assertNotIn("C:\\installed", json.dumps(value))
        self.assertNotIn("D:\\LocalAIHub", json.dumps(value))

    def test_http_200_foreign_and_old_local_api_are_not_compatible(self) -> None:
        expected = desktop._expected_api_identity()
        foreign = {"status": "healthy", "version": "7.1.0", "product_id": "OtherService"}
        old_local = {**expected, "product_version": "7.1.0"}
        with patch("urllib.request.urlopen", return_value=_Response(foreign)):
            self.assertEqual(desktop._probe_api(8765)[0], desktop.API_PROBE_FOREIGN)
        with patch("urllib.request.urlopen", return_value=_Response(old_local)):
            self.assertEqual(desktop._probe_api(8765)[0], desktop.API_PROBE_LOCAL_INCOMPATIBLE)

    def test_same_install_identity_is_reusable(self) -> None:
        expected = desktop._expected_api_identity()
        with patch.object(desktop, "_select_api_port", return_value=(8765, desktop.API_PROBE_COMPATIBLE)), patch.object(desktop, "popen_hidden") as popen:
            self.assertIsNone(desktop.ensure_api(timeout_seconds=0.2))
        popen.assert_not_called()

    def test_incompatible_listener_selects_fallback_and_preserves_external_owner(self) -> None:
        with tempfile.TemporaryDirectory(prefix="lah-v801-api-identity-") as temp:
            app_root = Path(temp) / "app"
            app_root.mkdir()
            fake_runtime = app_root / "runtime" / "Python312" / "python.exe"
            fake_runtime.parent.mkdir(parents=True)
            fake_runtime.write_bytes(b"runtime")
            process = _OwnedProcess()
            startup = MagicMock()
            startup.__enter__.return_value = True
            startup.__exit__.return_value = None
            with patch.object(desktop, "ROOT", app_root), patch.dict("os.environ", {"LOCALAIHUB_DATA_ROOT": str(Path(temp) / "data")}, clear=False), patch.object(desktop, "_probe_api", side_effect=[(desktop.API_PROBE_LOCAL_INCOMPATIBLE, {}), (desktop.API_PROBE_ABSENT, {}), (desktop.API_PROBE_COMPATIBLE, {})]), patch.object(desktop, "_free_loopback_port", return_value=55432), patch.object(desktop, "startup_mutex", return_value=startup), patch.object(desktop, "CoreRuntimeResolver") as resolver, patch.object(desktop, "popen_hidden", return_value=process) as spawn, patch.object(desktop, "_wait_for_api", return_value=True):
                resolver.return_value.resolve_python.return_value = fake_runtime
                result = desktop.ensure_api(timeout_seconds=1.0)
                self.assertEqual(desktop._configured_port(), 55432)
            self.assertIs(result, process)
            startup_name = startup
            child_env = spawn.call_args.kwargs["env"]
            self.assertEqual(child_env["LOCALAIHUB_PORT"], "55432")
            self.assertEqual(child_env["LOCALAIHUB_BIND_HOST"], "127.0.0.1")
            self.assertEqual(child_env["PYTHONNOUSERSITE"], "1")
            self.assertEqual(spawn.call_args.args[0][0], str(fake_runtime))
            self.assertIsNotNone(startup_name)

    def test_ui_endpoint_uses_selected_loopback_host(self) -> None:
        root = Path(__file__).resolve().parents[1]
        html = (root / "src" / "ui" / "index.html").read_text(encoding="utf-8")
        app = (root / "src" / "ui" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="api-endpoint">API đang khởi động', html)
        self.assertIn("window.location.host", app)
        self.assertNotIn("API 127.0.0.1:8765", html)


if __name__ == "__main__":
    unittest.main()
