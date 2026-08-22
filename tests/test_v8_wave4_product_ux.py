"""V8 Wave 4 source-side Product UX and Wave 3 re-audit contracts.

These tests do not launch Windows, WebView2, the loopback server, providers,
component downloads, model runtimes, FFmpeg workloads or GPU inference.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest

from src.services.api.context import ApiContext
from src.services.api.router import ApiRequest
from src.services.api.routes import component_v8
from src.services.component_enablement_v8 import ComponentEnablementService
from src.services.productization.catalog import ProductionCatalog


class V8Wave4ProductUxTests(unittest.TestCase):
    def test_reference_existing_is_not_misclassified_as_download_gate(self) -> None:
        catalog = SimpleNamespace(
            models={},
            runtimes={
                "comfyui": {
                    "disposition": "REFERENCE_EXISTING",
                    "install_strategy": "reference_existing",
                    "primary_source": None,
                    "source_verification": "unknown",
                    "authentication": {"required": False, "state": "not_required"},
                    "license": {"state": "unknown", "spdx_id": None, "url": None},
                    "integrity": {"verification": "unverified"},
                    "estimated_download_size": 0,
                    "estimated_disk_size": 0,
                    "source_identity": None,
                }
            },
        )
        value = ComponentEnablementService(catalog=catalog).assess("comfyui")
        self.assertEqual(value["acceptance_state"], "REFERENCE_EXISTING")
        self.assertFalse(value["auto_install_eligible"])
        self.assertIn("existing managed runtime", value["next_action"])

    def test_reviewed_gpl_runtime_can_be_source_accepted_without_promoting_execution(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        catalog = ProductionCatalog(catalog_path=repo / "Config" / "v7_production_catalog.example.json")
        value = ComponentEnablementService(catalog=catalog).assess("ffmpeg")
        self.assertEqual(value["acceptance_state"], "AUTO_INSTALL_READY")
        self.assertTrue(value["auto_install_eligible"])
        self.assertEqual(value["execution"], "not_run")
        self.assertTrue(value["dry_run"])

    def test_operation_route_uses_injected_context_with_bounded_limit(self) -> None:
        observed: list[int] = []

        def operations(*, limit: int) -> dict[str, object]:
            observed.append(limit)
            return {"status": "completed", "operations": []}

        context = ApiContext({"component_operations": operations})
        request = ApiRequest(method="GET", path="/api/components/operations", query={"limit": ["12"]}, headers={})
        response = component_v8.operations(request, context, {})
        self.assertEqual(response.status, 200)
        self.assertEqual(observed, [12])
        self.assertEqual(response.payload["operations"], [])

        invalid = ApiRequest(method="GET", path="/api/components/operations", query={"limit": ["0"]}, headers={})
        refused = component_v8.operations(invalid, context, {})
        self.assertEqual(refused.status, 400)
        self.assertEqual(refused.payload["error"], "component_operation_limit_invalid")
        self.assertEqual(observed, [12])

    def test_operation_confirm_maps_lost_plan_to_conflict(self) -> None:
        context = ApiContext({
            "component_confirm_operation": lambda operation_id, confirmed=False: {
                "status": "unavailable",
                "code": "plan_session_lost",
                "operation": {"operation_id": operation_id, "state": "blocked"},
                "execution": "not_run",
            }
        })
        request = ApiRequest(
            method="POST",
            path="/api/components/operations/compop_" + "a" * 32 + "/confirm",
            query={},
            headers={"content-type": "application/json"},
            _body_reader=lambda strict: {"confirmed": True},
        )
        response = component_v8.operation_confirm(request, context, {"operation_id": "compop_" + "a" * 32})
        self.assertEqual(response.status, 409)
        self.assertEqual(response.payload["code"], "plan_session_lost")

    def test_components_product_ux_is_mounted_non_polling_and_fail_closed(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        index = (repo / "src" / "ui" / "index.html").read_text(encoding="utf-8")
        renderer = (repo / "src" / "ui" / "features" / "components" / "render.js").read_text(encoding="utf-8")
        feature = (repo / "src" / "ui" / "features" / "components" / "index.js").read_text(encoding="utf-8")
        control = (repo / "src" / "ui" / "features" / "components" / "v8_control_plane.js").read_text(encoding="utf-8")

        self.assertIn('/ui/features/components/v8_control_plane.js', index)
        self.assertEqual(index.count('id="snapshot-status"'), 1)
        self.assertIn("data-v8-component-control-plane", renderer)
        self.assertIn("data-v8-source-state", renderer)
        self.assertIn('/api/components/operations?limit=12', control)
        self.assertIn('/api/components/source-acceptance', control)
        self.assertIn('data-v8-operation-confirm', control)
        self.assertIn('data-v8-operation-cancel', control)
        self.assertIn('FINITE_COMPONENT_TYPES', control)
        self.assertIn('FINITE_OPERATION_ACTIONS', control)
        self.assertNotIn('value.component_type === "runtime" ? "runtime" : "model"', control)
        self.assertIn('if (mounted) return;', control)
        self.assertNotIn("setInterval(", control)
        self.assertNotIn("filesystem_path", control)
        self.assertNotIn("source_path", control)
        self.assertNotIn("selected_path", control)
        self.assertIn('REFERENCE_EXISTING: "Use Existing"', feature)
        self.assertIn('MANUAL_INSTALL: "Manual Install"', feature)


if __name__ == "__main__":
    unittest.main()
