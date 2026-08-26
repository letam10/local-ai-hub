"""Regression contracts for the V8 Windows UX/storage closure.

These tests stay source-side and read-only.  They do not launch WebView2,
providers, model runtimes or GPU work; real Windows evidence belongs to the
separate acceptance packet.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from src.services.diagnostics.center import DIAGNOSTIC_SUBSYSTEMS, DiagnosticsCenter
from src.services.productization.catalog import ProductionCatalog
from src.services.storage_manager.overview import _storage_summary_from_reports


ROOT = Path(__file__).resolve().parents[1]


class V8WindowsUxClosureTests(unittest.TestCase):
    def test_diagnostics_publish_all_twelve_bounded_evidence_fields(self) -> None:
        center = DiagnosticsCenter()
        methods = {
            "git_integrity_state": {"status": "HEALTHY", "root_verified": True, "inside_work_tree": True},
            "config_registry_state": {"status": "HEALTHY", "schema_versions": {}},
            "jobs_store_state": {"status": "HEALTHY", "counts": {}},
            "artifact_store_state": {"status": "HEALTHY", "artifact_count": 0},
            "workflow_store_state": {"status": "HEALTHY", "workflow_count": 0, "library_revision": 0},
            "models_inventory": {"status": "HEALTHY", "models": {}, "present_count": 0, "total_count": 0},
            "environments_inventory": {"status": "HEALTHY", "environments": [], "present_count": 0, "total_count": 0},
            "runtime_inventory": {"status": "HEALTHY", "runtimes": {}, "present_count": 0, "total_count": 0},
            "storage_state": {"status": "HEALTHY", "drives": {}},
            "gpu_detection": {"status": "HEALTHY", "gpus": []},
            "latest_app_errors": {"status": "HEALTHY", "lines": []},
            "recovery_forensic_state": {"status": "HEALTHY", "files": []},
        }
        with patch.multiple(center, **{name: (lambda value=value: value) for name, value in methods.items()}):
            snapshot = center.snapshot()
        self.assertEqual(set(snapshot), set(DIAGNOSTIC_SUBSYSTEMS))
        for name in DIAGNOSTIC_SUBSYSTEMS:
            self.assertIn("inspected", snapshot[name], name)
            self.assertIn("evidence_summary", snapshot[name], name)
            self.assertIn("impact", snapshot[name], name)
            self.assertRegex(snapshot[name]["checked_at"], r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}T")

    def test_storage_diagnostic_reuses_scan_snapshot_without_recursive_scan(self) -> None:
        center = DiagnosticsCenter()
        scan = {"status": "partial", "mode": "deep_exact", "exact": False, "progress": 100, "files_scanned": 123456, "total_bytes_counted": 987654321, "reason": "Một vùng có reparse point.", "next_action": "Quét lại sau khi xử lý."}
        with patch("src.services.storage_manager.overview.storage_scan_snapshot", return_value={"scan": scan}):
            result = center.storage_state()
        self.assertEqual(result["scan"]["status"], "partial")
        self.assertEqual(result["scan"]["files_scanned"], 123456)

    def test_catalog_inventory_separates_model_and_runtime_health(self) -> None:
        catalog = ProductionCatalog(catalog_path=ROOT / "Config" / "v7_production_catalog.example.json")
        snapshot = catalog.snapshot()
        inventory = snapshot["inventory"]
        self.assertEqual(inventory["status"], "healthy")
        self.assertIn("registry_records", inventory)
        self.assertIn("runtimes", inventory)
        self.assertIn("readiness_note", inventory["runtimes"])
        self.assertIn("runtime", inventory["runtimes"]["readiness_note"].casefold())

    def test_deep_exact_requires_every_allowlisted_area(self) -> None:
        reports = {
            name: {"bytes": 1, "gb": 0.0, "status": "available", "complete": True, "entries_scanned": 1, "files_scanned": 1}
            for name in ("Models", "Environments", "Runtime", "Cache", "Output", "Temp", "Logs")
        }
        with patch("src.services.storage_manager.overview._volume_projection", return_value=[]), patch("src.services.storage_manager.overview._legacy_records", return_value=[]), patch("src.services.storage_manager.overview._disk_snapshot", return_value={"status": "available"}):
            complete = _storage_summary_from_reports(Path("."), reports, scan_status="completed", scan_mode="deep_exact")
            incomplete = _storage_summary_from_reports(Path("."), {key: value for key, value in reports.items() if key != "Logs"}, scan_status="partial", scan_mode="deep_exact")
        self.assertTrue(complete["scan"]["exact"])
        self.assertFalse(incomplete["scan"]["exact"])
        self.assertIn("vùng chưa hoàn tất", incomplete["reason"])

    def test_ui_closure_contracts_are_explicit(self) -> None:
        css = (ROOT / "src/ui/styles.css").read_text(encoding="utf-8")
        diagnostics = (ROOT / "src/ui/features/diagnostics/render.js").read_text(encoding="utf-8")
        settings = (ROOT / "src/ui/features/settings/render.js").read_text(encoding="utf-8")
        app = (ROOT / "src/ui/app.js").read_text(encoding="utf-8")
        resource = (ROOT / "src/ui/shared/rendering.js").read_text(encoding="utf-8")
        components = (ROOT / "src/ui/features/components/render.js").read_text(encoding="utf-8")
        self.assertIn("diagnostics-column", diagnostics)
        self.assertIn("align-content: start", css)
        self.assertIn("status-explanation__body", css)
        self.assertIn("settingsDirty", app)
        self.assertIn("Áp dụng & lưu", settings)
        self.assertIn("Hủy thay đổi", settings)
        self.assertIn("Chưa cần gán tài nguyên riêng", resource)
        self.assertIn("Đây là đánh giá lập kế hoạch", resource)
        self.assertIn("componentStatusCopy", components)
        self.assertIn("INSTALLED_UNVERIFIED", components)

    def test_edited_javascript_parses(self) -> None:
        for relative in (
            "src/ui/app.js",
            "src/ui/features/components/render.js",
            "src/ui/features/components/v8_control_plane.js",
            "src/ui/features/settings/render.js",
            "src/ui/features/models/models.js",
            "src/ui/features/diagnostics/render.js",
            "src/ui/shared/rendering.js",
            "src/ui/storage_scan_polling.js",
        ):
            result = subprocess.run(["node", "--check", str(ROOT / relative)], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, f"{relative}: {result.stderr}")


if __name__ == "__main__":
    unittest.main()
