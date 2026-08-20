"""Normalized contract baseline for the Phase 3 strangler router."""

from __future__ import annotations

import json
from pathlib import Path
import unittest
from unittest.mock import patch

from src.services.api.context import ApiContext
from src.services.api.router_registry import build_router
from src.services.api.router import ApiRequest


ROOT = Path(__file__).resolve().parents[3]


class V7ApiBaselineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.router = build_router()
        self.context = ApiContext({
            "health": lambda probe_gpu=False: {"status": "healthy", "contract_version": "health.v1", "gpu": {}},
            "bootstrap_payload": lambda: {"status": "completed", "contract_version": "bootstrap.v1", "settings": {}},
            "capability_control_plane": lambda: {"status": "completed", "modules": []},
            "lifecycle_payload": lambda: {"status": "completed", "execution": "not_run"},
            "tools_payload": lambda: {"status": "completed", "tools": []},
            "component_statuses": lambda: [],
            "component_snapshot": lambda: {"schema_version": "component-manager.v1", "status": "completed", "execution": "not_run", "dry_run": True, "records": []},
            "settings_payload": lambda: {"status": "completed", "schema_version": 2, "settings_revision": 1, "settings": {}},
            "settings_schema": lambda: {"status": "completed", "schema_version": "settings.v2", "defaults": {}},
            "settings_save": lambda payload, expected_revision=None: {"accepted": True, "status": "completed", "settings_revision": 2},
            "settings_reset": lambda section: {"accepted": True, "status": "completed"},
            "settings_reset_all": lambda: {"accepted": True, "status": "completed"},
            "diagnostics_snapshot": lambda: {}, "diagnostics_export": lambda: {"status": "completed"},
            "diagnostics_subsystem": lambda subsystem: {"status": "completed", "subsystem": subsystem},
            "diagnostics_recovery_drafts": lambda: {"status": "completed", "drafts": []},
        })

    def _request(self, method: str, path: str, body: dict | None = None):
        encoded = json.dumps(body or {}).encode("utf-8")
        request = ApiRequest(method, path, {}, {"content-length": str(len(encoded))}, lambda strict: body or {})
        return self.router.dispatch(request, self.context)

    def test_health_bootstrap_and_settings_contracts(self) -> None:
        health = self._request("GET", "/health")
        self.assertEqual(health.status, 200)
        self.assertEqual(health.payload["status"], "healthy")
        self.assertIn("contract_version", health.payload)

        bootstrap = self._request("GET", "/api/bootstrap")
        self.assertEqual(bootstrap.status, 200)
        self.assertEqual(bootstrap.payload["status"], "completed")

        settings = self._request("GET", "/api/settings")
        self.assertEqual(settings.status, 200)
        self.assertIn("settings_revision", settings.payload)

    def test_unknown_route_is_not_claimed_by_modular_router(self) -> None:
        self.assertIsNone(self._request("GET", "/api/not-a-route"))

    def test_component_payload_is_strict_and_path_free(self) -> None:
        value = self._request("POST", "/api/components/install/plan", {"component_id": "../escape", "component_type": "model", "path": "C:\\secret"})
        self.assertEqual(value.status, 400)
        encoded = json.dumps(value.payload, ensure_ascii=True)
        self.assertNotIn("C:\\", encoded)
        self.assertNotIn("escape", encoded)

    def test_backup_routes_reject_browser_paths(self) -> None:
        value = self._request("POST", "/api/backup/inspect", {"backup_path": "C:\\Users\\secret.zip"})
        self.assertEqual(value.status, 400)
        self.assertNotIn("Users", json.dumps(value.payload))


if __name__ == "__main__":
    unittest.main()
