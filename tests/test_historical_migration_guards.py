from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(f"historical_guard_{name.replace('.', '_')}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class HistoricalMigrationGuardTests(unittest.TestCase):
    def test_v3_inventory_preserves_a_completed_historical_snapshot(self) -> None:
        module = load_script("inventory_legacy_v3.py")
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "legacy_cleanup_v3.local.json"
            expected = {"historical_snapshot": True, "not_runtime_configuration": True}
            state.write_text(json.dumps(expected), encoding="utf-8")
            module.STATE = state
            with patch.object(sys, "argv", ["inventory_legacy_v3.py"]):
                self.assertEqual(module.main(), 0)
            self.assertEqual(json.loads(state.read_text(encoding="utf-8")), expected)

    def test_v2_inventory_preserves_a_completed_historical_snapshot(self) -> None:
        module = load_script("refresh_final_inventory.py")
        with tempfile.TemporaryDirectory() as temp:
            manifest = Path(temp) / "layout_migration.local.json"
            expected = {"historical_snapshot": True, "not_runtime_configuration": True}
            manifest.write_text(json.dumps(expected), encoding="utf-8")
            module.MANIFEST_PATH = manifest
            with patch.object(sys, "argv", ["refresh_final_inventory.py"]):
                self.assertEqual(module.main(), 0)
            self.assertEqual(json.loads(manifest.read_text(encoding="utf-8")), expected)

    def test_historical_powershell_helpers_guard_completed_state(self) -> None:
        cleanup = (ROOT / "scripts" / "cleanup_legacy_v3.ps1").read_text(encoding="utf-8")
        layout = (ROOT / "scripts" / "migrate_layout_v2.ps1").read_text(encoding="utf-8")
        environment = (ROOT / "scripts" / "migrate_python_environment_v3.ps1").read_text(encoding="utf-8")
        self.assertIn("historical on this completed host", cleanup)
        self.assertIn("historical on this completed host", layout)
        self.assertIn("legacy_removed", environment)


if __name__ == "__main__":
    unittest.main()
