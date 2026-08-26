"""Bounded read-only storage summary regressions for the installed UI."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

from src.services.storage_manager import overview


ROOT = Path(__file__).resolve().parents[1]


class StorageSummaryBoundedTests(unittest.TestCase):
    def _roots(self, root: Path) -> tuple[Path, ...]:
        return (root, root / "Models", root / "Environments", root / "Runtime", root / "Cache", root / "Output", root / "Temp", root / "Logs")

    def test_storage_summary_reports_partial_instead_of_walking_forever(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            models = root / "Models"
            models.mkdir(parents=True)
            for index in range(5):
                (models / f"model-{index}.bin").write_bytes(bytes([index]))
            roots = self._roots(root)
            with patch.object(overview, "_managed_roots", return_value=roots), patch.object(overview, "_volume_projection", return_value=[]), patch.object(overview, "_legacy_records", return_value=[]), patch.object(overview, "_DIRECTORY_SCAN_MAX_ENTRIES", 2):
                result = overview.storage_summary(force=True)
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["scan"]["status"], "partial")
            self.assertEqual(result["areas"]["Models"]["status"], "partial")
            self.assertFalse(result["areas"]["Models"]["complete"])
            self.assertLessEqual(result["areas"]["Models"]["entries_scanned"], 2)
            self.assertIn("bounded", result["areas"]["Models"]["reason"])

    def test_missing_or_non_directory_root_is_unavailable_without_path_echo(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            missing = root / "missing"
            report = overview._directory_size_report(missing)
            self.assertEqual(report["status"], "unavailable")
            self.assertFalse(report["complete"])
            self.assertNotIn(str(missing), str(report))

    def test_small_managed_roots_complete_with_metadata_only_projection(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            logs = root / "Logs"
            logs.mkdir(parents=True)
            (logs / "api.log").write_bytes(b"ok")
            report = overview._directory_size_report(logs)
            self.assertEqual(report["status"], "available")
            self.assertTrue(report["complete"])
            self.assertEqual(report["bytes"], 2)
            self.assertEqual(report["entries_scanned"], 1)

    def test_background_scan_reports_progress_and_exact_completion(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            roots = self._roots(root)

            def report(path: Path) -> dict[str, object]:
                return {"bytes": 10, "gb": 0.0, "status": "available", "complete": True, "entries_scanned": 1, "reason": "bounded fixture", "next_action": "none"}

            with patch.object(overview, "_managed_roots", return_value=roots), patch.object(overview, "_directory_size_report", side_effect=report), patch.object(overview, "_volume_projection", return_value=[]), patch.object(overview, "_legacy_records", return_value=[]), patch.object(overview, "_disk_snapshot", return_value={"status": "available"}):
                overview._scan_thread = None
                started = overview.start_storage_scan(force=True)
                self.assertEqual(started["status"], "running")
                thread = overview._scan_thread
                self.assertIsNotNone(thread)
                thread.join(timeout=2)
                self.assertFalse(thread.is_alive())
                snapshot = overview.storage_scan_snapshot()

            self.assertEqual(snapshot["status"], "completed")
            self.assertEqual(snapshot["scan"]["progress"], 100)
            self.assertTrue(snapshot["scan"]["exact"])
            self.assertEqual(len(snapshot["areas"]), 7)

    def test_deep_scan_crosses_fast_entry_budget_and_publishes_live_progress(self) -> None:
        with TemporaryDirectory(dir=ROOT.parent) as temporary:
            models = Path(temporary) / "Models"
            models.mkdir(parents=True)
            total_files = overview._DIRECTORY_SCAN_MAX_ENTRIES + 25
            for index in range(total_files):
                (models / f"file-{index:05d}.bin").write_bytes(b"x")
            updates: list[dict[str, object]] = []
            report = overview._deep_directory_size_report(models, on_progress=updates.append)
            self.assertTrue(report["complete"])
            self.assertEqual(report["status"], "available")
            self.assertEqual(report["entries_scanned"], total_files)
            self.assertEqual(report["files_scanned"], total_files)
            self.assertEqual(report["bytes"], total_files)
            self.assertTrue(any(int(item["entries_scanned"]) > overview._DIRECTORY_SCAN_MAX_ENTRIES for item in updates))

    def test_deep_scan_cancellation_returns_partial_count_without_finishing(self) -> None:
        with TemporaryDirectory(dir=ROOT.parent) as temporary:
            models = Path(temporary) / "Models"
            models.mkdir(parents=True)
            total_files = overview._DEEP_SCAN_YIELD_ENTRIES * 4
            for index in range(total_files):
                (models / f"file-{index:05d}.bin").write_bytes(b"xx")
            cancel = threading.Event()

            def publish(value: dict[str, object]) -> None:
                if int(value["entries_scanned"]) >= overview._DEEP_SCAN_YIELD_ENTRIES:
                    cancel.set()

            report = overview._deep_directory_size_report(models, cancel_event=cancel, on_progress=publish)
            self.assertEqual(report["status"], "cancelled")
            self.assertFalse(report["complete"])
            self.assertLess(report["entries_scanned"], total_files)
            self.assertIn("hủy", str(report["reason"]).lower())

    def test_deep_scan_unreadable_directory_is_partial_with_reason(self) -> None:
        with TemporaryDirectory(dir=ROOT.parent) as temporary:
            models = Path(temporary) / "Models"
            blocked = models / "blocked"
            blocked.mkdir(parents=True)
            (models / "ok.bin").write_bytes(b"ok")
            original_scandir = overview.os.scandir

            def scandir(path: object):
                if Path(path) == blocked:
                    raise PermissionError("fixture unreadable")
                return original_scandir(path)

            with patch.object(overview.os, "scandir", side_effect=scandir):
                report = overview._deep_directory_size_report(models)
            self.assertEqual(report["status"], "partial")
            self.assertFalse(report["complete"])
            self.assertGreaterEqual(report["unreadable_entries"], 1)
            self.assertIn("không đọc được", str(report["reason"]).lower())

    def test_deep_scan_skips_reparse_entry_and_refuses_exact_claim(self) -> None:
        with TemporaryDirectory(dir=ROOT.parent) as temporary:
            models = Path(temporary) / "Models"
            models.mkdir(parents=True)
            (models / "kept.bin").write_bytes(b"123")
            (models / "junction-target.bin").write_bytes(b"should-not-count")
            with patch.object(overview, "_is_reparse_point", side_effect=lambda entry: entry.name == "junction-target.bin"):
                report = overview._deep_directory_size_report(models)
            self.assertEqual(report["status"], "partial")
            self.assertFalse(report["complete"])
            self.assertEqual(report["reparse_entries"], 1)
            self.assertEqual(report["bytes"], 3)
            self.assertIn("reparse", str(report["reason"]).lower())

    def test_deep_background_scan_updates_area_before_completion(self) -> None:
        with TemporaryDirectory(dir=ROOT.parent) as temporary:
            root = Path(temporary)
            roots = self._roots(root)
            updates: list[dict[str, object]] = []

            def deep_report(path: Path, *, cancel_event=None, on_progress=None) -> dict[str, object]:
                value = {"bytes": 1, "gb": 0.0, "status": "available", "complete": True, "entries_scanned": 1, "files_scanned": 1, "directories_scanned": 0, "reparse_entries": 0, "unreadable_entries": 0, "reason": "fixture", "next_action": "none"}
                if callable(on_progress):
                    on_progress(value)
                updates.append(dict(value))
                return value

            with patch.object(overview, "_managed_roots", return_value=roots), patch.object(overview, "_deep_directory_size_report", side_effect=deep_report), patch.object(overview, "_volume_projection", return_value=[]), patch.object(overview, "_legacy_records", return_value=[]), patch.object(overview, "_disk_snapshot", return_value={"status": "available"}):
                overview._scan_thread = None
                started = overview.start_storage_scan(force=True, mode="deep_exact")
                self.assertEqual(started["status"], "running")
                thread = overview._scan_thread
                self.assertIsNotNone(thread)
                thread.join(timeout=2)
                snapshot = overview.storage_scan_snapshot()

            self.assertTrue(updates)
            self.assertEqual(snapshot["status"], "completed")
            self.assertTrue(snapshot["scan"]["exact"])
            self.assertEqual(snapshot["scan"]["mode"], "deep_exact")
            self.assertEqual(snapshot["scan"]["total_bytes_counted"], 7)

    def test_background_deep_scan_can_be_cancelled_without_blocking_request(self) -> None:
        with TemporaryDirectory(dir=ROOT.parent) as temporary:
            root = Path(temporary)
            roots = self._roots(root)

            def blocking_report(path: Path, *, cancel_event=None, on_progress=None) -> dict[str, object]:
                while cancel_event is not None and not cancel_event.is_set():
                    time.sleep(0.005)
                value = {"bytes": 2, "gb": 0.0, "status": "cancelled", "complete": False, "entries_scanned": 2, "files_scanned": 2, "directories_scanned": 0, "reparse_entries": 0, "unreadable_entries": 0, "reason": "cancelled fixture", "next_action": "retry"}
                if callable(on_progress):
                    on_progress(value)
                return value

            with patch.object(overview, "_managed_roots", return_value=roots), patch.object(overview, "_deep_directory_size_report", side_effect=blocking_report), patch.object(overview, "_volume_projection", return_value=[]), patch.object(overview, "_legacy_records", return_value=[]), patch.object(overview, "_disk_snapshot", return_value={"status": "available"}):
                overview._scan_thread = None
                started = overview.start_storage_scan(force=True, mode="deep_exact")
                self.assertEqual(started["scan"]["mode"], "deep_exact")
                response = overview.cancel_storage_scan(started["scan"]["scan_id"])
                self.assertTrue(response["scan"]["cancel_requested"])
                thread = overview._scan_thread
                self.assertIsNotNone(thread)
                thread.join(timeout=2)
                snapshot = overview.storage_scan_snapshot()

            self.assertFalse(thread.is_alive())
            self.assertEqual(snapshot["status"], "cancelled")
            self.assertFalse(snapshot["scan"]["exact"])


if __name__ == "__main__":
    unittest.main()
