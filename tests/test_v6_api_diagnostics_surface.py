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
from src.services.api.router_registry import build_router
import src.shared.paths.registry as paths_mod
import src.services.diagnostics.center as diag_center_mod
import src.services.node_studio.state as ns_state_mod


_PUBLIC_SUBSYSTEM_KEYS = (
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
)


def _safe_snapshot_fixture():
    return {
        key: {
            "status": "UNKNOWN",
            "reason": "Diagnostic snapshot unavailable or ambiguous.",
            "next_action": "Review the diagnostic source manually.",
            "execution": "not_run",
            "dry_run": True,
            "code": "diagnostic_projection_unavailable",
        }
        for key in _PUBLIC_SUBSYSTEM_KEYS
    }


def _direct_request(router, method: str, path: str, context: ApiContext, body: dict | None = None):
    encoded = json.dumps(body or {}).encode("utf-8")
    request = ApiRequest(method, path, {}, {"content-length": str(len(encoded))}, lambda strict: body or {})
    return router.dispatch(request, context)


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
            self.assertEqual(snapshot[sub]["execution"], "not_run")
            self.assertTrue(snapshot[sub]["dry_run"])
            self.assertNotIn("lines", snapshot[sub])
            self.assertNotIn("files", snapshot[sub])
            self.assertNotIn("origin", snapshot[sub])
            self.assertNotIn("head_sha", snapshot[sub])
            self.assertNotIn("branch", snapshot[sub])

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


class TestV6DiagnosticsPublicRouteProjection(unittest.TestCase):
    def _context(self, snapshot=None):
        safe_snapshot = _safe_snapshot_fixture() if snapshot is None else snapshot

        def fail_raw(*args, **kwargs):
            raise AssertionError("raw diagnostics callback was called")

        return ApiContext({
            "diagnostics_snapshot": lambda: safe_snapshot,
            "diagnostics_subsystem": fail_raw,
            "diagnostics_recovery_drafts": fail_raw,
            "diagnostics_recovery_state": fail_raw,
            "diagnostics_config_registry": fail_raw,
        })

    def test_public_routes_select_center_projection_only(self):
        router = build_router()
        context = self._context()
        paths = (
            "/api/diagnostics/snapshot",
            "/api/diagnostics/subsystem/config_registry",
            "/api/diagnostics/repair/recovery-drafts",
            "/api/diagnostics/repair/inspect-recovery",
            "/api/diagnostics/repair/verify-config",
        )
        for path in paths:
            response = _direct_request(router, "GET" if "snapshot" in path or "subsystem" in path or "recovery-drafts" in path else "POST", path, context)
            self.assertEqual(response.status, 200, path)
            serialized = json.dumps(response.payload, ensure_ascii=True)
            self.assertNotIn("raw diagnostics callback", serialized)
            self.assertNotIn("[object Object]", serialized)

    def test_unknown_subsystem_keeps_404_without_raw_callback(self):
        router = build_router()
        response = _direct_request(router, "GET", "/api/diagnostics/subsystem/not-allowlisted", self._context())
        self.assertEqual(response.status, 404)
        self.assertEqual(response.payload["error"], "unknown_diagnostic_subsystem")

    def test_malformed_center_snapshot_falls_back_without_echo(self):
        marker = "C:" + r"\Users\diagnostic marker\private.txt"
        malformed = {"git_integrity": {"status": "HEALTHY", "origin": marker}, "jobs_store": object()}
        router = build_router()
        response = _direct_request(router, "GET", "/api/diagnostics/snapshot", self._context(malformed))
        self.assertEqual(response.status, 200)
        serialized = json.dumps(response.payload, ensure_ascii=True)
        self.assertNotIn(marker, serialized)
        for key in _PUBLIC_SUBSYSTEM_KEYS:
            row = response.payload["snapshot"][key]
            self.assertEqual(row["status"], "UNKNOWN")
            self.assertEqual(row["execution"], "not_run")
            self.assertTrue(row["dry_run"])

    def test_repair_verify_rejects_hostile_config_projection(self):
        marker_path = "C:" + r"\Users\Alice Smith\private.json"
        marker_bearer = "Authorization: Bearer " + "diagnostic-marker"
        malformed = _safe_snapshot_fixture()
        malformed["config_registry"] = {
            "status": "NEEDS_ATTENTION",
            "reason": marker_path,
            "next_action": marker_bearer,
            "execution": "not_run",
            "dry_run": True,
            "object_marker": "[object Object]",
            "schema_versions": {"settings.json": marker_path},
        }
        router = build_router()
        response = _direct_request(router, "POST", "/api/diagnostics/repair/verify-config", self._context(malformed))
        self.assertEqual(response.status, 200)
        serialized = json.dumps(response.payload, ensure_ascii=True)
        for marker in (marker_path, marker_bearer, "[object Object]"):
            self.assertNotIn(marker, serialized)
        self.assertEqual(response.payload["result"]["status"], "UNKNOWN")
        self.assertEqual(response.payload["result"]["code"], "diagnostic_projection_unavailable")
        self.assertEqual(response.payload["result"]["execution"], "not_run")
        self.assertTrue(response.payload["result"]["dry_run"])


if __name__ == "__main__":
    unittest.main()
