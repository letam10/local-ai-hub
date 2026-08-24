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

    def test_settings_backup_loader_imports_its_api_dependency(self) -> None:
        app = (ROOT / "src" / "ui" / "app.js").read_text(encoding="utf-8")
        api = (ROOT / "src" / "ui" / "api.js").read_text(encoding="utf-8")
        self.assertIn("  listBackups,", app)
        self.assertRegex(api, r"export const listBackups\s*=")
        self.assertIn("listBackups()", app)

    def test_vietnamese_status_dictionary_does_not_fragment_unavailable(self) -> None:
        i18n = (ROOT / "src" / "ui" / "i18n.js").read_text(encoding="utf-8")
        self.assertIn('"unavailable": "chưa khả dụng"', i18n)
        self.assertIn(
            '"Media operation scope is not applied to this workspace; no execution is claimed.": '
            '"Phạm vi thao tác media không áp dụng cho workspace này; không tuyên bố thực thi."',
            i18n,
        )


if __name__ == "__main__":
    unittest.main()
