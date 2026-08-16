"""Focused contracts for the UI continuity, layout and language controls."""

from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "src" / "ui"


class UiPolishTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (UI / "index.html").read_text(encoding="utf-8")
        cls.app = (UI / "app.js").read_text(encoding="utf-8")
        cls.css = (UI / "styles.css").read_text(encoding="utf-8")
        cls.i18n = (UI / "i18n.js").read_text(encoding="utf-8")

    def test_snapshot_refresh_is_explicit_and_main_is_not_a_live_region(self) -> None:
        self.assertIn('id="refresh-snapshot"', self.html)
        self.assertIn('id="snapshot-status"', self.html)
        self.assertNotIn('<main class="main-panel" id="main-content" aria-live=', self.html)
        self.assertNotIn("window.setInterval(() => refreshFast", self.app)
        self.assertIn('refreshFast({ quiet: false, renderView: true })', self.app)

    def test_route_focus_is_intentional_and_not_every_background_render(self) -> None:
        self.assertRegex(self.app, r"const render = \(\{ focus = false \} = \{\}\) =>")
        self.assertIn("if (focus) view.focus({ preventScroll: true });", self.app)
        self.assertIn('render({ focus: true }); await loadRouteData()', self.app)

    def test_full_width_content_and_focus_outline_are_explicit(self) -> None:
        module_rule = re.search(r"#module-view\s*\{([^}]*)\}", self.css, re.S)
        self.assertIsNotNone(module_rule)
        assert module_rule is not None
        self.assertIn("width: 100%;", module_rule.group(1))
        self.assertIn("max-width: var(--content-max);", module_rule.group(1))
        self.assertIn("--content-max: none;", self.css)
        self.assertIn("#main-content:focus, #module-view:focus { outline: none; }", self.css)

    def test_five_language_options_have_persisted_selector(self) -> None:
        for language in ("vi", "en", "zh", "ja", "ko"):
            self.assertIn(f'value="{language}"', self.html)
        self.assertIn('id="language-select"', self.html)
        self.assertIn("local-ai-hub-language", self.i18n)
        self.assertEqual(self.i18n.count("{ id:"), 5)
        self.assertIn("window.location.reload()", self.app)

    def test_translation_skips_code_and_selector_content(self) -> None:
        self.assertIn("parent.closest(\"script,style,code,pre,[data-i18n-skip]\")", self.i18n)
        self.assertIn('data-i18n-skip', self.html)
        self.assertIn("const escaped = source.replace", self.i18n)

    def test_desktop_shortcut_falls_back_to_gui_pythonw(self) -> None:
        shortcut_script = (ROOT / "scripts" / "update_managed_shortcuts.ps1").read_text(encoding="utf-8")
        self.assertIn("Get-Command pythonw.exe", shortcut_script)
        self.assertIn("single GUI window", shortcut_script)
        self.assertNotIn("python.exe'", shortcut_script)


if __name__ == "__main__":
    unittest.main()
