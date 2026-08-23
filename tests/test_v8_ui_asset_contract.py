"""Static UI asset references must resolve inside the installed payload."""

from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class UiAssetContractTests(unittest.TestCase):
    def test_index_stylesheet_references_exist(self) -> None:
        html = (ROOT / "src" / "ui" / "index.html").read_text(encoding="utf-8")
        references = re.findall(r'<link[^>]+href="([^"]+)"', html)
        local_paths = [value.removeprefix("/ui/") for value in references if value.startswith("/ui/")]
        self.assertTrue(local_paths)
        for relative in local_paths:
            self.assertTrue((ROOT / "src" / "ui" / relative).is_file(), relative)


if __name__ == "__main__":
    unittest.main()
