from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

import scripts.build_main_update as build_main_update
from scripts.build_main_update import ARCHIVE_NAME, build


class V8MainUpdateBuilderTests(unittest.TestCase):
    def test_builder_packages_only_tracked_app_content_with_commit_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "update"
            with patch.object(build_main_update, "resolve_source_commit", return_value="d" * 40):
                manifest = build(output, expected_source_sha="d" * 40)
            self.assertEqual(manifest["source_commit"], "d" * 40)
            self.assertEqual(manifest["payload_id"], "main-dddddddddddd")
            self.assertEqual(manifest["runtime_strategy"], "reuse-current")
            self.assertTrue((output / "update-manifest.json").is_file())
            contract = json.loads((output / "update-contract.json").read_text(encoding="utf-8"))
            self.assertEqual(contract["update_kind"], "APP_ONLY")
            self.assertEqual(contract["source_commit"], "d" * 40)
            self.assertTrue((output / "SHA256SUMS.txt").is_file())
            with zipfile.ZipFile(output / ARCHIVE_NAME, "r") as bundle:
                names = bundle.namelist()
            self.assertIn("app/src/app/launcher.py", names)
            self.assertTrue(all(name.startswith("app/") for name in names))
            self.assertFalse(any("/Models/" in name or "/Environments/" in name or "/Output/" in name for name in names))
            parsed = json.loads((output / "update-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(parsed, manifest)
            sums = {
                parts[1]: parts[0]
                for parts in (line.split() for line in (output / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines())
                if len(parts) == 2
            }
            self.assertEqual(sums[ARCHIVE_NAME], hashlib.sha256((output / ARCHIVE_NAME).read_bytes()).hexdigest())
            self.assertEqual(sums["update-contract.json"], hashlib.sha256((output / "update-contract.json").read_bytes()).hexdigest())
            self.assertNotIn(b"\r\n", (output / "update-contract.json").read_bytes())

    def test_full_builder_requires_and_binds_a_bounded_runtime_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            runtime.mkdir()
            (runtime / "pythonw.exe").write_bytes(b"python")
            (runtime / "runtime.dll").write_bytes(b"dll")
            output = root / "update"
            previous_runtime = os.environ.get("LOCALAIHUB_RUNTIME_VERSION")
            os.environ["LOCALAIHUB_RUNTIME_VERSION"] = "3.12.10"
            try:
                with patch.object(build_main_update, "resolve_source_commit", return_value="e" * 40):
                    manifest = build(
                        output,
                        update_kind="FULL",
                        runtime_root=runtime,
                        expected_source_sha="e" * 40,
                    )
            finally:
                if previous_runtime is None:
                    os.environ.pop("LOCALAIHUB_RUNTIME_VERSION", None)
                else:
                    os.environ["LOCALAIHUB_RUNTIME_VERSION"] = previous_runtime
            self.assertEqual(manifest["runtime_strategy"], "bundled")
            contract = json.loads((output / "update-contract.json").read_text(encoding="utf-8"))
            self.assertEqual(contract["update_kind"], "FULL")
            self.assertEqual(contract["runtime_version"], "3.12.10")
            with zipfile.ZipFile(output / ARCHIVE_NAME, "r") as bundle:
                self.assertIn("runtime/pythonw.exe", bundle.namelist())


if __name__ == "__main__":
    unittest.main()
