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
from src.services.api.routes import node_studio as node_studio_routes
from src.services.project_manager.manager import CreativeProjectManager
import src.shared.paths.registry as paths_mod
import src.services.node_studio.state as ns_state_mod
import src.services.api.api_server as api_server_mod


class TestV6ApiNodeProjectSurface(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.config_dir = Path(self.temp.name) / "Config"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.workspace_path = self.config_dir / "creative_workspace.json"
        self.pm = CreativeProjectManager(self.workspace_path)

        self.patches = [
            patch.object(paths_mod, "CONFIG_ROOT", self.config_dir),
            patch.object(ns_state_mod, "CONFIG_ROOT", self.config_dir),
            patch.object(api_server_mod, "project_manager", self.pm),
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

    def test_node_studio_draft_lifecycle(self):
        # 1. GET before draft exists -> 404
        status, payload = self._request("GET", "/api/node-studio/drafts/image")
        self.assertEqual(status, 404)

        # 2. POST save draft -> 200
        sample_graph = {"nodes": [{"id": "n1", "type": "image_input"}], "edges": []}
        status, payload = self._request("POST", "/api/node-studio/drafts/image", {"graph": sample_graph})
        self.assertEqual(status, 200)
        self.assertTrue(payload.get("accepted"))

        # 3. GET after save -> 200 with draft
        status, payload = self._request("GET", "/api/node-studio/drafts/image")
        self.assertEqual(status, 200)
        self.assertEqual(payload["draft"]["scope"], "image")
        self.assertEqual(payload["draft"]["graph"], sample_graph)

        # 4. DELETE clear draft -> 200
        status, payload = self._request("DELETE", "/api/node-studio/drafts/image")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "completed")

        # 5. GET after clear -> 404
        status, payload = self._request("GET", "/api/node-studio/drafts/image")
        self.assertEqual(status, 404)

    def test_project_manifest_and_missing_artifacts(self):
        # Create project
        create_res = self.pm.create_project({"title": "Test Alpha"})
        project_id = create_res["project"]["id"]

        # GET manifest
        status, payload = self._request("GET", f"/api/projects/{project_id}/manifest")
        self.assertEqual(status, 200)
        self.assertTrue(payload.get("accepted"))
        self.assertIn("manifest", payload)
        self.assertEqual(payload["manifest"]["project_id"], project_id)

        # GET missing artifacts
        status, payload = self._request("GET", f"/api/projects/{project_id}/missing-artifacts")
        self.assertEqual(status, 200)
        self.assertTrue(payload.get("accepted"))
        self.assertEqual(payload["missing_count"], 0)

    def test_assets_search_and_status(self):
        # Search empty assets
        status, payload = self._request("GET", "/api/assets/search?query=test")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "completed")
        self.assertIn("assets", payload)

        # Artifact status for non-existent artifact -> 404
        status, payload = self._request("GET", f"/api/artifacts/artifact_{'f'*32}/status")
        self.assertEqual(status, 404)
        self.assertFalse(payload.get("found"))


class TestNodeStudioDraftDirectRoutes(unittest.TestCase):
    """Direct route projection checks; loopback coverage remains unchanged."""

    @staticmethod
    def _request(method: str, body: dict | None = None) -> ApiRequest:
        return ApiRequest(
            method=method,
            path="/api/node-studio/drafts/image",
            query={},
            headers={},
            _body_reader=lambda strict: body or {},
        )

    def test_route_projection_does_not_echo_legacy_path_fields(self):
        marker = r"C:\Users\secret https://example.invalid Bearer token"
        context = ApiContext({
            "node_draft_load": lambda scope: {"draft_path": marker, "scope": "image", "graph": {"nodes": [], "edges": []}},
            "node_draft_persist": lambda scope, graph: {"accepted": False, "reason": marker, "draft_path": marker},
            "node_draft_clear": lambda scope: {"accepted": False, "status": "manual_review", "reason": marker, "draft_path": marker},
        })
        responses = [
            node_studio_routes.draft_get(self._request("GET"), context, {"scope": "image"}),
            node_studio_routes.draft_save(self._request("POST", {"graph": {}}), context, {"scope": "image"}),
            node_studio_routes.draft_delete(self._request("DELETE"), context, {"scope": "image"}),
        ]
        rendered = json.dumps([response.payload for response in responses], ensure_ascii=False)
        self.assertNotIn(marker, rendered)
        self.assertNotIn("Bearer", rendered)
        self.assertEqual(responses[0].status, 404)
        self.assertEqual(responses[1].status, 400)
        self.assertEqual(responses[2].status, 409)

    def test_route_clear_propagates_safe_storage_failure(self):
        context = ApiContext({
            "node_draft_clear": lambda scope: {
                "accepted": False,
                "status": "manual_review",
                "reason": "draft_manual_review",
                "execution": "not_run",
                "dry_run": True,
            },
        })
        response = node_studio_routes.draft_delete(self._request("DELETE"), context, {"scope": "image"})
        self.assertEqual(response.status, 409)
        self.assertEqual(response.payload["reason"], "draft_manual_review")
        self.assertEqual(response.payload["execution"], "not_run")


if __name__ == "__main__":
    unittest.main()
