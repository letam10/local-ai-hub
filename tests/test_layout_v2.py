from __future__ import annotations

import json
import unittest
from pathlib import Path

from src.shared.paths.registry import ROOT, RUNTIME_PATHS


class LayoutV2Tests(unittest.TestCase):
    def test_registry_stays_under_workspace(self) -> None:
        self.assertTrue((ROOT / "src").is_dir())
        for path in RUNTIME_PATHS.values():
            self.assertTrue(path.is_relative_to(ROOT), path)

    def test_module_manifests_are_valid_and_distinguish_tool_state(self) -> None:
        allowed_tool_statuses = {"operational", "partial", "queue_only", "unavailable", "planned"}
        modules = ROOT / "src" / "modules"
        manifests = sorted(modules.glob("*/manifest.json"))
        self.assertGreaterEqual(len(manifests), 13)
        for path in manifests:
            value = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(value["schema_version"], 1)
            self.assertTrue(value["module_id"])
            self.assertIn(value["tool_status"], allowed_tool_statuses)
            self.assertIn("component_status", value)

    def test_deprecated_import_shims_remain_available(self) -> None:
        from Hub.config import BASE_DIR as shim_root
        from src.services.api.config import BASE_DIR as canonical_root

        self.assertEqual(shim_root, canonical_root)


if __name__ == "__main__":
    unittest.main()
