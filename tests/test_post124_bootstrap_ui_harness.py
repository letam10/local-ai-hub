from __future__ import annotations

from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class Post124BootstrapUiHarnessTests(unittest.TestCase):
    def test_executable_bootstrap_timing_harness(self) -> None:
        result = subprocess.run(
            ["node", str(ROOT / "tests" / "node_app_update_bootstrap_harness.mjs")],
            cwd=str(ROOT), capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn('"deferred":true', result.stdout)
        self.assertIn('"restartCalls":1', result.stdout)


if __name__ == "__main__":
    unittest.main()
