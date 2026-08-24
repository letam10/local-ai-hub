"""Regression tests for the live diagnostics projection method contracts."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src.services import artifact_store
from src.services.diagnostics.center import DiagnosticsCenter


class DiagnosticsProjectionRuntimeTests(unittest.TestCase):
    def test_jobs_projection_uses_api_jobs_list_contract(self) -> None:
        center = DiagnosticsCenter()
        records = [{"id": "job-1", "status": "completed"}, {"id": "job-2", "status": "failed"}]
        with patch("src.services.api.jobs.list_jobs", return_value=records) as list_jobs:
            result = center.jobs_store_state()
        list_jobs.assert_called_once_with(limit=1000)
        self.assertEqual(result["status"], "HEALTHY")
        self.assertEqual(result["counts"], {"completed": 1, "failed": 1})

    def test_artifact_projection_reads_managed_index_not_removed_process_registry(self) -> None:
        with TemporaryDirectory() as temporary:
            index = Path(temporary) / "artifacts.json"
            index.write_text(json.dumps({"artifact_a": {"id": "artifact_a"}}), encoding="utf-8")
            with patch.object(artifact_store, "INDEX_PATH", index):
                result = artifact_store.diagnostics_summary()
        self.assertEqual(result["total_artifacts"], 1)
        self.assertTrue(result["index_path_exists"])
        self.assertTrue(result["index_readable"])


if __name__ == "__main__":
    unittest.main()
