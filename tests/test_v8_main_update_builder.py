from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile

from scripts.build_main_update import ARCHIVE_NAME, build


class V8MainUpdateBuilderTests(unittest.TestCase):
    def test_builder_packages_only_tracked_app_content_with_commit_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "update"
            previous = os.environ.get("GITHUB_SHA")
            os.environ["GITHUB_SHA"] = "d" * 40
            try:
                manifest = build(output)
            finally:
                if previous is None:
                    os.environ.pop("GITHUB_SHA", None)
                else:
                    os.environ["GITHUB_SHA"] = previous
            self.assertEqual(manifest["source_commit"], "d" * 40)
            self.assertEqual(manifest["payload_id"], "main-dddddddddddd")
            self.assertEqual(manifest["runtime_strategy"], "reuse-current")
            self.assertTrue((output / "update-manifest.json").is_file())
            self.assertTrue((output / "SHA256SUMS.txt").is_file())
            with zipfile.ZipFile(output / ARCHIVE_NAME, "r") as bundle:
                names = bundle.namelist()
            self.assertIn("app/src/app/launcher.py", names)
            self.assertTrue(all(name.startswith("app/") for name in names))
            self.assertFalse(any("/Models/" in name or "/Environments/" in name or "/Output/" in name for name in names))
            parsed = json.loads((output / "update-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(parsed, manifest)


if __name__ == "__main__":
    unittest.main()
