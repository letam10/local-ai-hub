from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.services.api.api_server import HubHTTPServer, HubHandler
import src.app_config.settings_service as ss_mod
import src.shared.paths.registry as paths_mod


class TestV6ApiSettingsSurface(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.config_dir = Path(self.temp.name) / "Config"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.config_dir / "settings.json"

        self.patches = [
            patch.object(paths_mod, "CONFIG_ROOT", self.config_dir),
            patch.object(ss_mod, "CONFIG_ROOT", self.config_dir),
            patch.object(ss_mod, "DEFAULT_SETTINGS_PATH", self.settings_path),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.temp.cleanup)

        self.server = HubHTTPServer(("127.0.0.1", 0), HubHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)

    def _stop_server(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2.0)

    def _request(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                status = resp.status
                raw = resp.read().decode("utf-8")
                return status, json.loads(raw)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            try:
                payload = json.loads(raw)
            except Exception:
                payload = {"error": raw}
            return exc.code, payload

    def test_get_settings_initial_defaults(self):
        status, payload = self._request("GET", "/api/settings")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "completed")
        self.assertIn("settings", payload)
        self.assertIn("schema_version", payload)
        self.assertIn("settings_revision", payload)
        self.assertEqual(payload["settings"]["language"], "vi")

    def test_get_settings_schema(self):
        status, payload = self._request("GET", "/api/settings/schema")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "completed")
        self.assertIn("schema_version", payload)
        self.assertIn("defaults", payload)

    def test_patch_settings_success(self):
        patch_body = {
            "ui": {"language": "en", "theme": "dark"},
            "window": {"start_maximized": False},
        }
        status, payload = self._request("PATCH", "/api/settings", patch_body)
        self.assertEqual(status, 200)
        self.assertTrue(payload.get("accepted"))
        self.assertEqual(payload.get("settings_revision"), 1)

        # Verify on GET
        g_status, g_payload = self._request("GET", "/api/settings")
        self.assertEqual(g_status, 200)
        self.assertEqual(g_payload["settings"]["language"], "en")
        self.assertEqual(g_payload["settings"]["theme"], "dark")
        self.assertEqual(g_payload["settings"]["start_maximized"], False)

    def test_patch_settings_conflict_protection(self):
        # Save once -> rev 1
        self._request("PATCH", "/api/settings", {"ui": {"language": "ja"}})
        # Save with expected_revision=0 -> 409 Conflict
        status, payload = self._request("PATCH", "/api/settings", {
            "expected_revision": 0,
            "ui": {"language": "ko"},
        })
        self.assertEqual(status, 409)
        self.assertFalse(payload.get("accepted"))
        self.assertEqual(payload.get("status"), "conflict")

    def test_patch_settings_post_alias(self):
        # POST /api/settings should also work for environments where PATCH is routed via POST
        patch_body = {"ui": {"language": "zh"}}
        status, payload = self._request("POST", "/api/settings", patch_body)
        self.assertEqual(status, 200)
        self.assertTrue(payload.get("accepted"))

    def test_reset_section(self):
        # First modify UI
        self._request("PATCH", "/api/settings", {"ui": {"language": "ko"}})
        # Reset UI section
        status, payload = self._request("POST", "/api/settings/reset", {"section": "ui"})
        self.assertEqual(status, 200)
        self.assertTrue(payload.get("accepted"))
        self.assertEqual(payload["settings"]["ui"]["language"], "vi")


if __name__ == "__main__":
    unittest.main()
