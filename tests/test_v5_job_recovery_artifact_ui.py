from __future__ import annotations

import inspect
import json
import re
import subprocess
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tests import v5_acceptance_ui_fixture as fixture


ROOT = Path(__file__).resolve().parents[1]
UNSAFE_DISPLAY_PATTERN = re.compile(r"(?i)([a-z]:[\\/]|\\\\|(?:file|data):|api[_-]?key|password|secret|token)\s*[:=]")


class V5JobRecoveryArtifactUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pages = (ROOT / "src" / "ui" / "pages.js").read_text(encoding="utf-8")
        cls.app = (ROOT / "src" / "ui" / "app.js").read_text(encoding="utf-8")
        cls.styles = (ROOT / "src" / "ui" / "styles.css").read_text(encoding="utf-8")

    def setUp(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), fixture.AcceptanceHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.assertFalse(self.thread.is_alive(), "fixture thread did not stop cleanly")

    def _request(self, path: str, *, method: str = "GET") -> tuple[int, dict[str, str], bytes]:
        request = Request(self.base_url + path, method=method)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, dict(response.headers.items()), response.read()
        except HTTPError as error:
            return error.code, dict(error.headers.items()), error.read()

    def _state(self) -> dict[str, object]:
        return {
            "health": fixture.BOOTSTRAP["health"],
            "capabilities": fixture.BOOTSTRAP["capabilities"],
            "productization": fixture.BOOTSTRAP["productization"],
            "storage": fixture.BOOTSTRAP["storage"],
            "jobs": fixture.BOOTSTRAP["jobs"],
            "durableJobs": fixture.BOOTSTRAP["durable_jobs"]["records"],
            "components": [],
            "tools": [],
            "applications": [],
            "settings": fixture.BOOTSTRAP["settings"],
            "workflowLibrary": fixture.BOOTSTRAP["workflow_library"],
        }

    def _render(self, route: str, state: dict[str, object]) -> str:
        serialized = json.dumps(state, ensure_ascii=True)
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

    def test_fixture_publishes_canonical_recovery_counts_and_safe_action_branches(self) -> None:
        status, _headers, body = self._request("/api/bootstrap")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        recovery = payload["productization"]["jobs"]
        self.assertEqual(recovery["counts"], {"active": 1, "attention": 4, "interrupted": 2, "recoverable": 1, "total": 6})
        self.assertEqual({item["status"] for item in recovery["records"]}, {"running", "failed", "interrupted", "unavailable", "completed"})
        self.assertEqual(sum(item["source"] == "durable" for item in recovery["records"]), 1)
        self.assertFalse(next(item for item in recovery["records"] if item["source"] == "durable")["resumable"])
        self.assertNotRegex(json.dumps(payload), UNSAFE_DISPLAY_PATTERN)

        status, _headers, _body = self._request(f"/jobs/{fixture.HOT_FAILED_ID}/resume", method="POST")
        self.assertEqual(status, 200)
        status, _headers, _body = self._request(f"/api/durable-jobs/{fixture.DURABLE_ID}/resume", method="POST")
        self.assertEqual(status, 200)
        durable_response = json.loads(_body)
        self.assertEqual(durable_response["status"], "unavailable")
        self.assertEqual(durable_response["execution"], "not_run")

    def test_productization_jobs_are_primary_and_normalized_without_unknown_stringification(self) -> None:
        recovery_slice = self.pages[self.pages.index("export const jobRecoverySnapshot"):self.pages.index("const readinessModuleDetails")]
        for marker in ("productJobs.records", "productJobs.counts", "hasCanonical", "productization.jobs", "safeHotJobDetail"):
            self.assertIn(marker, recovery_slice)
        for marker in ("const safeJobArtifacts", "const safeJobProvenance"):
            self.assertIn(marker, self.pages)
        self.assertIn("source: jobSource", recovery_slice)
        self.assertNotIn("JSON.stringify", recovery_slice)
        self.assertNotIn("Object.values", recovery_slice)

    def test_dashboard_recovery_card_routes_to_existing_focused_jobs(self) -> None:
        html = self._render("dashboard", self._state())
        self.assertIn('class="job-recovery-card card"', html)
        self.assertIn('data-recovery-source="productization.jobs"', html)
        self.assertIn('data-recovery-count="attention"><span>Attention</span><strong>4</strong>', html)
        self.assertIn('data-recovery-count="recoverable"><span>Recoverable</span><strong>1</strong>', html)
        self.assertIn('data-route="jobs" data-recovery-focus="attention"', html)
        self.assertIn('aria-controls="jobs-page"', html)
        self.assertNotIn("[object Object]", html)
        self.assertNotRegex(html, UNSAFE_DISPLAY_PATTERN)

    def test_jobs_render_source_lifecycle_artifacts_and_exact_resume_gating(self) -> None:
        html = self._render("jobs", self._state())
        self.assertIn('data-job-recovery-source="productization.jobs"', html)
        self.assertIn(f'data-job-id="{fixture.HOT_FAILED_ID}"', html)
        self.assertIn(f'data-job-source-label="hot">Hot', html)
        self.assertIn(f'data-job-source-label="durable">Durable', html)
        self.assertIn(f'data-resume-job="{fixture.HOT_FAILED_ID}"', html)
        self.assertNotIn(f'data-resume-durable-job="{fixture.DURABLE_ID}"', html)
        self.assertIn("5 available", html)
        self.assertIn("Preview unavailable in this snapshot.", html)
        self.assertIn("Recovery reason", html)
        self.assertIn("Lifecycle", html)
        self.assertIn('data-job-action-status role="status"', html)
        self.assertNotIn("[object Object]", html)
        self.assertNotRegex(html, UNSAFE_DISPLAY_PATTERN)

    def test_duplicate_canonical_records_are_deterministically_deduped(self) -> None:
        state = self._state()
        productization = json.loads(json.dumps(state["productization"]))
        records = productization["jobs"]["records"]
        duplicate = dict(records[1])
        duplicate["reason"] = {"unsafe": "[object Object]"}
        productization["jobs"]["records"] = [duplicate, *records, duplicate]
        state["productization"] = productization
        html = self._render("jobs", state)
        self.assertEqual(html.count(f'data-job-id="{fixture.HOT_FAILED_ID}"'), 1)
        self.assertNotIn("[object Object]", html)

    def test_action_feedback_and_responsive_contracts_stay_in_owned_paths(self) -> None:
        action_slice = self.app[self.app.index("const setJobActionStatus"):self.app.index("const renderNavigation")]
        for marker in ("data-job-action-status", "refreshFast", "resumeDurableJob", "resumeJob", "cancelJob"):
            self.assertIn(marker, self.app)
        self.assertIn("state.jobFilter = \"attention\"", self.app)
        self.assertIn("safeDisplayMessage", action_slice)
        self.assertNotIn("JSON.stringify(result", action_slice)
        for marker in ("job-recovery-counts", "job-recovery-detail", "job-action-status", "focus-visible", "overflow-wrap", "@media (max-width: 980px)"):
            self.assertIn(marker, self.styles)
        pages_actions = self.pages[self.pages.index("function renderJobs"):self.pages.index("function renderModels")]
        self.assertIn('const durable = job.source === "durable";', pages_actions)
        self.assertIn('job.resumable === true', pages_actions)
        self.assertNotIn("job.lifecycle || job.status", pages_actions)

    def test_artifact_preview_and_fixture_transport_remain_bounded(self) -> None:
        app = (ROOT / "src" / "ui" / "app.js").read_text(encoding="utf-8")
        start = app.index("const showArtifactPreview")
        end = app.index("const renderNavigation", start)
        preview = app[start:end]
        for forbidden in ("fetch(", "arrayBuffer()", "FileReader", "Blob", "URL.createObjectURL"):
            self.assertNotIn(forbidden, preview)
        source = inspect.getsource(fixture.AcceptanceHandler._serve_artifact)
        self.assertNotIn("read_bytes(", source)


if __name__ == "__main__":
    unittest.main()
