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
import src.shared.paths.registry as paths_mod
import src.services.backup_manager as bm_mod


class TestV6ApiBackupSurface(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.config_dir = Path(self.temp.name) / "Config"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir = self.config_dir / "backups"

        # Create a sample settings file to backup
        (self.config_dir / "settings.json").write_text(
            json.dumps({"schema_version": 2, "settings_revision": 1, "ui": {"language": "vi"}}),
            encoding="utf-8"
        )

        self.patches = [
            patch.object(paths_mod, "CONFIG_ROOT", self.config_dir),
            patch.object(bm_mod, "CONFIG_ROOT", self.config_dir),
            patch.object(bm_mod, "_BACKUP_DIR", self.backup_dir),
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

    def test_backup_create_and_inspect_flow(self):
        # 1. Create backup
        status, payload = self._request("POST", "/api/backup/create")
        self.assertEqual(status, 201)
        self.assertTrue(payload.get("accepted"))
        backup_path = payload.get("backup_path")
        self.assertTrue(Path(backup_path).exists())

        # 2. Inspect backup
        i_status, i_payload = self._request("POST", "/api/backup/inspect", {"backup_path": backup_path})
        self.assertEqual(i_status, 200)
        self.assertTrue(i_payload.get("valid"))
        self.assertEqual(i_payload.get("errors"), [])

        # 3. Plan restore
        p_status, p_payload = self._request("POST", "/api/backup/plan", {"backup_path": backup_path})
        self.assertEqual(p_status, 200)
        self.assertTrue(p_payload.get("accepted"))
        self.assertIn("preview", p_payload)

        # 4. Apply unconfirmed -> rejected (400)
        a_status, a_payload = self._request("POST", "/api/backup/apply", {
            "backup_path": backup_path,
            "plan": p_payload,
            "confirmed": False,
        })
        self.assertEqual(a_status, 400)
        self.assertFalse(a_payload.get("accepted"))

        # 5. Apply confirmed -> success (200)
        c_status, c_payload = self._request("POST", "/api/backup/apply", {
            "backup_path": backup_path,
            "plan": p_payload,
            "confirmed": True,
        })
        self.assertEqual(c_status, 200)
        self.assertTrue(c_payload.get("accepted"))
        self.assertIn("settings.json", c_payload.get("applied", []))


if __name__ == "__main__":
    unittest.main()
