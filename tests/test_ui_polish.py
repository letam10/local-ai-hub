"""Focused contracts for the UI continuity, layout and language controls."""
# FILE NOTE
# - Mục đích: Contract tests cho UI polish: layout, focus, i18n, snapshot continuity
# - Liên kết trực tiếp: src/ui/app.js, src/ui/pages.js, src/ui/index.html, src/ui/styles.css
# - Vùng ảnh hưởng khi sửa: PR44 thay đổi render() signature và closeArtifactPreview() interface

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
        cls.pages = (UI / "pages.js").read_text(encoding="utf-8")

    def test_snapshot_refresh_is_explicit_and_main_is_not_a_live_region(self) -> None:
        self.assertIn('id="refresh-snapshot"', self.html)
        self.assertIn('id="snapshot-status"', self.html)
        self.assertNotIn('<main class="main-panel" id="main-content" aria-live=', self.html)
        self.assertNotIn("window.setInterval(() => refreshFast", self.app)
        self.assertIn('refreshFast({ quiet: false, renderView: true })', self.app)

    def test_route_focus_is_intentional_and_not_every_background_render(self) -> None:
        # PR44: render() uses string focus key; background renders preserve focus via continuity
        self.assertRegex(self.app, r'const render = \(\{ background = false, focus = "" \} = \{\}\) =>')
        self.assertIn("restoreFocusContinuity(continuity, focus);", self.app)
        self.assertIn('render({ focus: "main" }); await loadRouteData()', self.app)
        self.assertIn("captureFocusContinuity", self.app)

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

    def test_translation_is_limited_to_explicit_static_markup(self) -> None:
        self.assertNotIn("document.createTreeWalker", self.i18n)
        self.assertIn('nodes("[data-i18n]")', self.i18n)
        self.assertIn('["data-i18n-aria-label", "aria-label"]', self.i18n)
        self.assertIn('["data-i18n-title", "title"]', self.i18n)
        self.assertIn('data-i18n="Trung tâm AI cục bộ"', self.html)
        self.assertIn('data-i18n-aria-label="Mở điều hướng"', self.html)
        self.assertIn("const escaped = source.replace", self.i18n)

    def test_rendered_page_templates_mark_fixed_copy_without_touching_snapshots(self) -> None:
        self.assertIn('import { translateText } from "./i18n.js";', self.pages)
        self.assertIn("const uiText = (value) => translateText", self.pages)
        for phrase in ("Reason & next action", "Next action", "Recovery reason", "Module preflight", "Readiness & Module Plan", "Jobs recovery"):
            self.assertIn(f'data-i18n="{phrase}"', self.pages)
        self.assertIn("escapeHtml(jobRecovery.reason)", self.pages)
        self.assertIn("escapeHtml(recovery.reason)", self.pages)

    def test_dynamic_snapshot_and_creative_values_never_enter_translation_markers(self) -> None:
        self.assertIn("const metricSnapshot =", self.pages)
        self.assertIn("<small>${escapeHtml(detail)}</small>", self.pages)
        self.assertIn("const cardDynamic =", self.pages)
        self.assertIn("const fieldDynamic =", self.pages)
        self.assertIn("cardDynamic(`Asset của ${selected.title}`", self.pages)
        self.assertIn("fieldDynamic(variable.label || variable.name", self.pages)
        self.assertIn('data-i18n-container="Attention"', self.pages)
        self.assertIn("const normalized = readinessStatus(status);", self.pages)
        self.assertIn("READINESS_STATUS_LABELS.unknown", self.pages)
        self.assertNotIn('nodes("[data-recovery-count]").forEach', self.i18n)

    def test_artifact_preview_close_restores_opener_or_main_landmark(self) -> None:
        # PR44: uses opener?.isConnected + focusMainContent() instead of querySelector + focusTarget
        self.assertIn("artifactPreviewOpener = button;", self.app)
        self.assertIn("const opener = artifactPreviewOpener;", self.app)
        self.assertIn("opener?.isConnected", self.app)
        self.assertIn("focusMainContent();", self.app)
        self.assertIn("if (event.key === \"Escape\"", self.app)
        self.assertIn("closeArtifactPreview();", self.app)

    def test_desktop_shortcut_fallback_requires_eligible_gui_python(self) -> None:
        shortcut_script = (ROOT / "scripts" / "update_managed_shortcuts.ps1").read_text(encoding="utf-8")
        self.assertIn("Get-Command pythonw.exe", shortcut_script)
        self.assertIn("function Test-HubPythonwFallback", shortcut_script)
        self.assertIn('"src.app.main", "uvicorn", "webview"', shortcut_script)
        self.assertIn("MISSING_RUNTIME", shortcut_script)
        self.assertIn("if ($missingRuntime)", shortcut_script)
        self.assertIn("if (Test-Path -LiteralPath $pythonw -PathType Leaf)", shortcut_script)
        self.assertIn("Test-HubPythonwFallback -PythonwPath $pythonw", shortcut_script)


if __name__ == "__main__":
    unittest.main()
