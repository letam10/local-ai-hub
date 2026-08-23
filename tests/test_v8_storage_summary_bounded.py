"""Bounded read-only storage summary regressions for the installed UI."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src.services.storage_manager import overview


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


if __name__ == "__main__":
    unittest.main()
