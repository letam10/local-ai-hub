from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from src.services.api import gpu
from src.services.api.core import health


ROOT = Path(__file__).resolve().parents[1]


class StartupLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved_gpu_cache = gpu._gpu_cache
        gpu._gpu_cache = None

    def tearDown(self) -> None:
        gpu._gpu_cache = self._saved_gpu_cache

    def test_readiness_snapshot_does_not_spawn_nvidia_smi(self) -> None:
        with patch.object(gpu, "run_hidden") as run_hidden:
            snapshot = gpu.query_gpu(probe=False)
        run_hidden.assert_not_called()
        self.assertEqual(snapshot["reason"], "GPU snapshot pending")

    def test_health_uses_non_probing_gpu_snapshot_by_default(self) -> None:
        with patch("src.services.api.core.query_gpu", return_value={"available": False}) as query:
            payload = health()
        query.assert_called_once_with(probe=False)
        self.assertEqual(payload["status"], "healthy")

    def test_owned_process_termination_uses_native_tree_not_taskkill(self) -> None:
        managed = (ROOT / "src" / "services" / "process_manager" / "managed.py").read_text(encoding="utf-8")
        windows = (ROOT / "src" / "services" / "process_manager" / "windows.py").read_text(encoding="utf-8")
        self.assertIn("terminate_process_tree(process.pid)", managed)
        self.assertNotIn('"taskkill"', managed)
        self.assertIn("CreateToolhelp32Snapshot", windows)
        self.assertIn("TerminateProcess", windows)


if __name__ == "__main__":
    unittest.main()
