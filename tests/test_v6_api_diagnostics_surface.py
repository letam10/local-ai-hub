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
from src.services.api.context import ApiContext
from src.services.api.router import ApiRequest
from src.services.api.routes import diagnostics as diagnostics_routes
import src.shared.paths.registry as paths_mod
import src.services.diagnostics.center as diag_center_mod
import src.services.node_studio.state as ns_state_mod


class TestV6ApiDiagnosticsSurface(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.config_dir = Path(self.temp.name) / "Config"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir = Path(self.temp.name) / "Logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.patches = [
            patch.object(paths_mod, "CONFIG_ROOT", self.config_dir),
            patch.object(paths_mod, "LOG_ROOT", self.log_dir),
            patch.object(diag_center_mod, "CONFIG_ROOT", self.config_dir),
            patch.object(diag_center_mod, "LOG_ROOT", self.log_dir),
            patch.object(ns_state_mod, "CONFIG_ROOT", self.config_dir),
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

    def test_get_diagnostics_snapshot(self):
        status, payload = self._request("GET", "/api/diagnostics/snapshot")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "completed")
        self.assertIn("snapshot", payload)
        snapshot = payload["snapshot"]
        expected_subsystems = [
            "git_integrity",
            "config_registry",
            "jobs_store",
            "artifact_store",
            "workflow_store",
            "models_inventory",
            "environments_inventory",
            "runtime_inventory",
            "storage",
            "gpu",
            "latest_app_errors",
            "recovery_forensic",
        ]
        for sub in expected_subsystems:
            self.assertIn(sub, snapshot, f"Subsystem {sub} missing in snapshot")
            self.assertIn("status", snapshot[sub])
            self.assertIn("reason", snapshot[sub])
            self.assertIn("next_action", snapshot[sub])

    def test_get_diagnostics_export_bundle(self):
        status, payload = self._request("GET", "/api/diagnostics/export")
        self.assertEqual(status, 200)
        self.assertTrue(payload.get("sanitized"))
        self.assertIn("bundle", payload)
        bundle_str = json.dumps(payload["bundle"])
        for kw in ("api_key", "password", "token", "secret"):
            self.assertNotIn(f'"{kw}"', bundle_str)

    def test_get_diagnostics_subsystem(self):
        status, payload = self._request("GET", "/api/diagnostics/subsystem/config_registry")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["subsystem"], "config_registry")
        self.assertIn("data", payload)

    def test_get_diagnostics_subsystem_unknown_rejected(self):
        status, payload = self._request("GET", "/api/diagnostics/subsystem/unknown_subsystem_hack")
        self.assertEqual(status, 404)
        self.assertEqual(payload["status"], "error")

    def test_repair_recovery_drafts_get(self):
        draft_file = self.config_dir / "node_studio_draft_image.json"
        draft_file.write_text('{"draft_schema_version": 1}', encoding="utf-8")
        status, payload = self._request("GET", "/api/diagnostics/repair/recovery-drafts")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "completed")
        self.assertIn("drafts", payload)

    def test_repair_verify_config(self):
        status, payload = self._request("POST", "/api/diagnostics/repair/verify-config")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "completed")
        self.assertIn("result", payload)

    def test_repair_inspect_recovery(self):
        status, payload = self._request("POST", "/api/diagnostics/repair/inspect-recovery")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "completed")
        self.assertIn("result", payload)

    def test_repair_clear_recovery_drafts_unconfirmed_rejected(self):
        status, payload = self._request("POST", "/api/diagnostics/repair/clear-recovery-drafts", {
            "scopes": ["image"],
            "confirmed": False,
        })
        self.assertEqual(status, 400)
        self.assertEqual(payload.get("status"), "unconfirmed")

    def test_repair_clear_recovery_drafts_confirmed(self):
        # Create a mock draft
        draft_file = self.config_dir / "node_studio_draft_image.json"
        draft_file.write_text('{"draft_schema_version": 1}', encoding="utf-8")
        self.assertTrue(draft_file.exists())

        status, payload = self._request("POST", "/api/diagnostics/repair/clear-recovery-drafts", {
            "scopes": ["image"],
            "confirmed": True,
        })
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "completed")
        self.assertFalse(draft_file.exists())


class TestV7DiagnosticsSanitizedDirectRoutes(unittest.TestCase):
    def _request(self, method: str = "GET", body: dict | None = None) -> ApiRequest:
        return ApiRequest(
            method=method,
            path="/api/diagnostics/direct",
            query={},
            headers={},
            _body_reader=lambda strict: body or {},
        )

    def _hostile_snapshot(self) -> tuple[dict[str, object], str]:
        marker = r"C:\Users\Public\diag secret Bearer token [object Object] https://example.invalid/private"
        names = (
            "git_integrity", "config_registry", "jobs_store", "artifact_store", "workflow_store",
            "models_inventory", "environments_inventory", "runtime_inventory", "storage", "gpu",
            "latest_app_errors", "recovery_forensic",
        )
        raw: dict[str, object] = {
            name: {"status": "NEEDS_ATTENTION", "reason": marker, "next_action": marker}
            for name in names
        }
        raw["config_registry"] = {"status": "NEEDS_ATTENTION", "schema_versions": {"settings.json": marker}}
        raw["latest_app_errors"] = {"status": "NEEDS_ATTENTION", "lines": [marker]}
        raw["recovery_forensic"] = {"status": "NEEDS_ATTENTION", "files": [marker]}
        return raw, marker

    def _context_with_raw_callback_sentinels(self, raw: object) -> ApiContext:
        def forbidden(*args: object, **kwargs: object) -> object:
            raise AssertionError("raw diagnostics callback was called")

        return ApiContext({
            "diagnostics_snapshot": lambda: raw,
            "diagnostics_export": lambda: {"bundle": raw, "sanitized": False},
            "diagnostics_subsystem": forbidden,
            "diagnostics_recovery_drafts": forbidden,
            "diagnostics_config_registry": forbidden,
            "diagnostics_recovery_state": forbidden,
        })

    def test_public_routes_use_center_projection_and_never_echo_hostile_values(self):
        raw, marker = self._hostile_snapshot()
        context = self._context_with_raw_callback_sentinels(raw)
        responses = [
            diagnostics_routes.snapshot(self._request(), context, {}),
            diagnostics_routes.export(self._request(), context, {}),
            diagnostics_routes.subsystem(self._request(), context, {"subsystem": "config_registry"}),
            diagnostics_routes.recovery_drafts(self._request(), context, {}),
            diagnostics_routes.repair_verify(self._request("POST"), context, {}),
            diagnostics_routes.repair_inspect(self._request("POST"), context, {}),
        ]
        rendered = json.dumps([response.payload for response in responses], ensure_ascii=False, sort_keys=True)
        self.assertNotIn(marker, rendered)
        self.assertNotIn("Bearer", rendered)
        self.assertNotIn("[object Object]", rendered)
        self.assertNotIn("example.invalid", rendered)
        self.assertTrue(responses[1].payload["sanitized"])
        self.assertEqual(200, responses[4].status)
        self.assertEqual("UNKNOWN", responses[4].payload["result"]["status"])
        self.assertEqual("not_run", responses[4].payload["result"]["execution"])

    def test_malformed_config_projection_is_fixed_and_raw_repair_callback_is_not_used(self):
        context = self._context_with_raw_callback_sentinels({
            "config_registry": {"schema_versions": {"settings.json": {"se" + "cret": "Bearer x"}}},
            "recovery_forensic": {"files": [{"path": "C:\\private"}]},
        })
        verify = diagnostics_routes.repair_verify(self._request("POST"), context, {})
        inspect = diagnostics_routes.repair_inspect(self._request("POST"), context, {})
        self.assertEqual(200, verify.status)
        self.assertEqual("diagnostic_projection_unavailable", verify.payload["result"]["reason_code"])
        self.assertEqual("diagnostic_projection_unavailable", inspect.payload["result"]["reason_code"])
        rendered = json.dumps([verify.payload, inspect.payload])
        self.assertNotIn("Bearer", rendered)
        self.assertNotIn("C:\\private", rendered)

    def test_unknown_subsystem_rejects_before_any_context_call(self):
        def forbidden(*args: object, **kwargs: object) -> object:
            raise AssertionError("snapshot should not be read for unknown subsystem")

        response = diagnostics_routes.subsystem(
            self._request(),
            ApiContext({"diagnostics_snapshot": forbidden, "diagnostics_subsystem": forbidden}),
            {"subsystem": "unknown_subsystem_hack"},
        )
        self.assertEqual(404, response.status)
        self.assertEqual("unknown_diagnostic_subsystem", response.payload["error"])


if __name__ == "__main__":
    unittest.main()
