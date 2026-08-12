from __future__ import annotations

import json
import re
import subprocess
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

from tests import v6_snapshot_continuity_fixture as fixture


ROOT = Path(__file__).resolve().parents[1]
UNSAFE_DISPLAY_PATTERN = re.compile(r"(?i)([a-z]:[\\/]|\\\\|(?:file|data|https?):|api[_-]?key|password|secret|token)\s*[:=]")


class V6SnapshotContinuityA11yUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (ROOT / "src" / "ui" / "index.html").read_text(encoding="utf-8")
        cls.app = (ROOT / "src" / "ui" / "app.js").read_text(encoding="utf-8")
        cls.pages = (ROOT / "src" / "ui" / "pages.js").read_text(encoding="utf-8")
        cls.styles = (ROOT / "src" / "ui" / "styles.css").read_text(encoding="utf-8")

    def setUp(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), fixture.SnapshotContinuityHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.assertFalse(self.thread.is_alive(), "fixture thread did not stop cleanly")

    def _json(self, path: str) -> object:
        with urlopen(Request(self.base_url + path), timeout=5) as response:
            self.assertEqual(response.status, 200)
            return json.loads(response.read())

    def _state(self) -> dict[str, object]:
        payload = fixture.capability_fixture._bootstrap("completed")
        return {
            "health": payload["health"],
            "capabilities": payload["capabilities"],
            "productization": payload["productization"],
            "storage": payload["storage"],
            "jobs": payload["jobs"],
            "durableJobs": payload["durable_jobs"]["records"],
            "components": [],
            "tools": [],
            "applications": [],
            "settings": payload["settings"],
            "workflowLibrary": payload["workflow_library"],
            "jobFilter": "all",
        }

    def _render(self, route: str) -> str:
        serialized = json.dumps(self._state(), ensure_ascii=True)
        script = f"""
import {{ renderPage }} from './src/ui/pages.js';
const state = {serialized};
const before = JSON.stringify(state);
const html = renderPage({json.dumps(route)}, state);
if (JSON.stringify(state) !== before) throw new Error('render mutated state');
process.stdout.write(html);
"""
        result = subprocess.run(
            ["node", "--input-type=module", "--eval", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_fixture_is_loopback_and_snapshot_transport_is_read_only(self) -> None:
        bootstrap = self._json("/api/bootstrap")
        jobs = self._json("/api/jobs")
        self.assertEqual(bootstrap["status"], "completed")
        self.assertEqual(jobs["status"], "completed")
        self.assertNotRegex(json.dumps(bootstrap), UNSAFE_DISPLAY_PATTERN)
        self.assertNotIn("[object Object]", json.dumps(bootstrap))

    def test_skip_link_main_landmark_and_bounded_status(self) -> None:
        self.assertIn('<a class="skip-link" href="#main-content">', self.html)
        self.assertIn('<main class="main-panel" id="main-content" tabindex="-1">', self.html)
        self.assertIn('id="snapshot-status" class="sr-only" role="status" aria-live="polite" aria-atomic="true"', self.html)
        self.assertNotRegex(self.html, r'<main[^>]*aria-live=')
        self.assertIn("Snapshot received.", self.html)
        self.assertIn(".skip-link:focus-visible", self.styles)
        self.assertIn(".sr-only", self.styles)

    def test_refresh_preserves_safe_focus_and_scroll_without_unconditional_focus(self) -> None:
        for marker in (
            "const FOCUS_TOKEN_SELECTORS",
            "const SAFE_FOCUS_TOKENS",
            "const captureFocusContinuity",
            "const restoreFocusContinuity",
            "const restoreScrollContinuity",
            'if (background && continuity.token)',
            'setSnapshotStatus("deferred")',
            'render({ background: true })',
            'render({ focus: "main" })',
        ):
            self.assertIn(marker, self.app)
        self.assertNotIn('view.focus({ preventScroll: true })', self.app)
        self.assertIn('window.addEventListener("hashchange", async () => { render({ focus: "main" });', self.app)
        refresh_slice = self.app[self.app.index("const refreshFast"):self.app.index("const refreshCreative")]
        self.assertIn("getHealth()", refresh_slice)
        self.assertIn("getJobs()", refresh_slice)
        self.assertIn("getCapabilities()", refresh_slice)
        self.assertIn("getDurableJobs()", refresh_slice)
        self.assertNotIn("getProductization", refresh_slice)
        self.assertNotIn("getStorage()", refresh_slice)

    def test_snapshot_status_uses_only_fixed_local_messages(self) -> None:
        status_slice = self.app[self.app.index("const SNAPSHOT_STATUS_TEXT"):self.app.index("const safeStorageGet")]
        self.assertIn("snapshotStatus.textContent = SNAPSHOT_STATUS_TEXT[key]", status_slice)
        self.assertIn('Object.prototype.hasOwnProperty.call(SNAPSHOT_STATUS_TEXT, stateKey)', status_slice)
        self.assertNotIn("snapshotStatus.textContent = state", status_slice)
        self.assertNotIn("snapshotStatus.textContent = message", status_slice)

    def test_focus_selectors_are_fixed_and_job_preview_tokens_are_rendered(self) -> None:
        selector_slice = self.app[self.app.index("const FOCUS_TOKEN_SELECTORS"):self.app.index("const safeStorageGet")]
        for token in ("job-filter-all", "job-filter-active", "job-filter-attention", "job-filter-completed", "artifact-preview-opener", "artifact-preview-close"):
            self.assertIn(token, selector_slice)
        self.assertIn("focusTokenSelector = (token) => SAFE_FOCUS_TOKENS.has(token)", selector_slice)
        self.assertNotIn("querySelector(`[", selector_slice)
        jobs = self._render("jobs")
        self.assertIn('data-focus-key="job-filter-all"', jobs)
        self.assertIn('data-focus-key="job-filter-attention"', jobs)
        self.assertIn('data-focus-key="job-filter-completed"', jobs)
        self.assertIn('data-focus-key="job-action-resume-durable"', self.pages)
        self.assertIn('data-focus-key="artifact-preview-opener"', self.pages)

    def test_preview_close_restores_opener_or_main_fallback(self) -> None:
        self.assertIn("let artifactPreviewOpener = null", self.app)
        self.assertIn("artifactPreviewOpener = button", self.app)
        self.assertIn("opener?.isConnected", self.app)
        self.assertIn("else focusMainContent()", self.app)
        self.assertIn('close.dataset.focusKey = "artifact-preview-close"', self.app)
        self.assertIn("closeArtifactPreview(); return;", self.app)
        self.assertNotIn("artifactPreviewOpener = active", self.app)

    def test_rendered_snapshot_state_does_not_echo_hostile_values(self) -> None:
        state = self._state()
        state["health"] = {"status": "partial", "disk": {"free_bytes": 0}, "hostile": "C:\\private\\secret; token=hidden"}
        serialized = json.dumps(state, ensure_ascii=True)
        script = f"""
import {{ renderPage }} from './src/ui/pages.js';
const html = renderPage('dashboard', {serialized});
process.stdout.write(html);
"""
        result = subprocess.run(
            ["node", "--input-type=module", "--eval", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotRegex(result.stdout, UNSAFE_DISPLAY_PATTERN)
        self.assertNotIn("[object Object]", result.stdout)


if __name__ == "__main__":
    unittest.main()
