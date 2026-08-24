"""V8 Wave 4 source-side Product UX and Wave 3 re-audit contracts.

These tests do not launch Windows, WebView2, the loopback server, providers,
component downloads, model runtimes, FFmpeg workloads or GPU inference.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.services.api.context import ApiContext
from src.services.api.router import ApiRequest
from src.services.api.routes import component_v8
from src.services.component_enablement_v8 import ComponentEnablementService
from src.platform.paths import HubPaths
from src.services.component_installer.maintenance_executor import MaintenanceExecutor
from src.services.component_installer.receipts import write_component_receipt
from src.services.productization import ComponentLifecycle
from src.services.productization.catalog import ProductionCatalog
from src.services.api import components as component_api


class V8Wave4ProductUxTests(unittest.TestCase):
    def test_observed_local_model_is_not_projected_as_absent(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            paths = HubPaths(app_root=repo, data_root=Path(temporary))
            catalog = ProductionCatalog(
                paths=paths,
                catalog_path=repo / "Config" / "v7_production_catalog.example.json",
                observed_models={"animesr-v2": {"installed": True, "size": {"bytes": 42}}},
            )
            item = catalog.inspect_model("animesr-v2")
            self.assertEqual(item["status"], "INSTALLED_UNVERIFIED")
            self.assertTrue(item["observed_local"])
            self.assertEqual(item["installed_size_bytes"], 42)
            self.assertFalse(item["operational"])

    def test_component_snapshot_reconciles_legacy_observation_without_promoting_runtime(self) -> None:
        base = {"records": [{"component_id": "animesr-v2", "component_type": "model", "status": "NOT_INSTALLED", "model_status": "NOT_INSTALLED", "execution": "not_run"}]}
        fake = SimpleNamespace(snapshot=lambda: base)
        with patch.object(component_api, "component_installer", return_value=fake), patch.object(
            component_api,
            "_observed_model_records",
            return_value={"animesr-v2": {"installed": True, "size": {"bytes": 12}}},
        ):
            value = component_api.snapshot()
        record = value["records"][0]
        self.assertEqual(record["status"], "INSTALLED_UNVERIFIED")
        self.assertEqual(record["model_status"], "INSTALLED_UNVERIFIED")
        self.assertTrue(record["observed_local"])
        self.assertEqual(record["execution"], "not_run")

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
        record = catalog.runtimes["ffmpeg"]
        self.assertEqual(record["required_leaves"], ["v8/ffmpeg/ffmpeg.exe", "v8/ffmpeg/ffprobe.exe"])
        self.assertNotIn("tools/ffmpeg/", json.dumps(record, ensure_ascii=True))

    def test_v2_runtime_maintenance_binds_record_and_preserves_fixed_leaves(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            paths = HubPaths(app_root=repo, data_root=data)
            catalog = ProductionCatalog(paths=paths, catalog_path=repo / "Config" / "v7_production_catalog.example.json")
            lifecycle = ComponentLifecycle(paths=paths, catalog=catalog)
            record = catalog.runtimes["ffmpeg"]
            binding = lifecycle._catalog_binding("runtime", record)
            leaves = []
            for relative in record["required_leaves"]:
                target = data / "runtime" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"fixture-runtime-leaf")
                leaves.append({"relative_path": relative, "observed_size_bytes": target.stat().st_size, "observed_mtime_ns": target.stat().st_mtime_ns, "verification_level": "unverified"})
            write_component_receipt(paths.config_root, "ffmpeg", {"component_id": "ffmpeg", "component_type": "runtime", "root_class": "runtime_root", "location_class": "runtime_root", "source_identity": record["source_identity"], "leaves": leaves, "state": "INSTALLED_UNVERIFIED", "source": "catalog_primary", "operational": False}, catalog_binding=binding)
            executor = MaintenanceExecutor(paths=paths, catalog=catalog)

            repair = lifecycle.plan_maintenance("ffmpeg", "repair")
            repaired = executor.apply(lifecycle._plans[repair["plan_id"]], confirmed=True, catalog_binding=binding, current_record=record)
            self.assertEqual(repaired["status"], "completed")

            update = lifecycle.plan_maintenance("ffmpeg", "update")
            update_result = executor.apply(lifecycle._plans[update["plan_id"]], confirmed=True, catalog_binding=binding, current_record=record)
            self.assertEqual(update_result["code"], "update_candidate_required")

            uninstall = lifecycle.plan_maintenance("ffmpeg", "uninstall")
            removed = executor.apply(lifecycle._plans[uninstall["plan_id"]], confirmed=True, catalog_binding=binding, current_record=record)
            self.assertEqual(removed["status"], "completed")
            self.assertTrue(all(not (data / "runtime" / relative).exists() for relative in record["required_leaves"]))

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

    def test_ui_repairs_search_and_action_gating_at_render_boundary(self) -> None:
        app = (Path(__file__).resolve().parents[1] / "src" / "ui" / "app.js").read_text(encoding="utf-8")
        dashboard = (Path(__file__).resolve().parents[1] / "src" / "ui" / "features" / "dashboard" / "render.js").read_text(encoding="utf-8")
        models = (Path(__file__).resolve().parents[1] / "src" / "ui" / "features" / "models" / "models.js").read_text(encoding="utf-8")
        settings = (Path(__file__).resolve().parents[1] / "src" / "ui" / "features" / "settings" / "render.js").read_text(encoding="utf-8")
        studio = (Path(__file__).resolve().parents[1] / "src" / "ui" / "features" / "node_studio" / "studio.js").read_text(encoding="utf-8")
        self.assertIn("const TOOL_EXECUTION_READY = new Set([\"operational\"])", app)
        self.assertIn("form[data-job-form]", app)
        self.assertIn('document.addEventListener("input"', app)
        self.assertIn("readinessGate", app)
        self.assertIn("data-model-action-status", models)
        self.assertIn("settingsActionStatus", settings)
        self.assertIn("transportReady", dashboard)
        self.assertIn('HubLiteNode.title_text_color = "#ffffff"', studio)
        self.assertIn("this.unsaved = Boolean(this.unsaved)", studio)
        self.assertIn("this.persist({ source: \"template\", saved: quiet })", studio)


if __name__ == "__main__":
    unittest.main()
