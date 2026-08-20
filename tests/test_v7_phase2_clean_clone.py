"""Source-only clean-clone acceptance for the model-free V7 bootstrap."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from src.platform.paths import get_paths
from src.services.bootstrap_core import plan_core_bootstrap
from src.services.runtime_manager import CoreRuntimeResolver


ROOT = Path(__file__).resolve().parents[1]


class V7CleanCloneAcceptanceTests(unittest.TestCase):
    def test_direct_no_config_bootstrap_is_model_free_and_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "data"
            environment = os.environ.copy()
            environment["LOCALAIHUB_DATA_ROOT"] = str(data_root)
            environment["LOCALAIHUB_APP_ROOT"] = str(ROOT)
            result = subprocess.run(
                [sys.executable, "-B", str(ROOT / "scripts" / "bootstrap_core.py"), "--no-config"],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["dry_run"])
            self.assertEqual(payload["execution"], "not_run")
            self.assertFalse((data_root / "Config").exists())
            self.assertFalse((data_root / "Models").exists())
            self.assertFalse((data_root / "Environments").exists())
            self.assertFalse((data_root / "runtime").exists())
            self.assertNotIn("D:\\LocalAIHub", result.stdout)

    def test_split_and_legacy_data_root_projections_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary) / "app"
            data = Path(temporary) / "data"
            split = get_paths(app_root=app, data_root=data)
            legacy = get_paths(app_root=app, data_root=app)
            self.assertEqual(split.safe_projection()["mode"], "split_app_data")
            self.assertTrue(legacy.legacy_single_root_mode)
            self.assertEqual(legacy.safe_projection()["mode"], "legacy_single_root")

    def test_missing_core_is_blocked_without_system_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = get_paths(app_root=Path(temporary) / "app", data_root=Path(temporary) / "data")
            inspection = CoreRuntimeResolver(paths=paths, allow_system=False).inspect()
            self.assertEqual(inspection["status"], "MISSING")
            self.assertEqual(plan_core_bootstrap(paths=paths, allow_system=False)["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
