"""Focused V8.0.1 UX contracts for truthful status explanations and job history."""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class JobHistoryActionTests(unittest.TestCase):
    def setUp(self) -> None:
        from src.services.api import jobs

        self.jobs = jobs
        self.temp = TemporaryDirectory()
        self.path_patch = patch.object(jobs, "JOBS_PATH", Path(self.temp.name) / "Config" / "jobs.json")
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        with jobs._lock:
            self.snapshot = dict(jobs._jobs)
            jobs._jobs.clear()
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        with self.jobs._lock:
            self.jobs._jobs.clear()
            self.jobs._jobs.update(self.snapshot)
        self.temp.cleanup()

    def _record(self, job_id: str, status: str) -> dict[str, object]:
        return {
            "id": job_id,
            "contract_version": "job.v2",
            "tool": "probe_media",
            "status": status,
            "created_at": "2026-08-24T00:00:00+00:00",
            "finished_at": "2026-08-24T00:01:00+00:00" if status in self.jobs.TERMINAL_STATUSES else None,
            "result": {"artifacts": [{"id": "artifact_" + "a" * 32}]},
        }

    def test_delete_terminal_history_preserves_artifact_contract(self) -> None:
        record = self._record("job_terminal", "completed")
        with self.jobs._lock:
            self.jobs._jobs[record["id"]] = record
        with patch.object(self.jobs, "_save") as save:
            result = self.jobs.delete_job("job_terminal")
        self.assertEqual(result["status"], "deleted")
        self.assertTrue(result["artifacts_preserved"])
        self.assertNotIn("job_terminal", self.jobs._jobs)
        save.assert_called_once_with(immediate=True)

    def test_delete_active_or_invalid_history_is_refused(self) -> None:
        record = self._record("job_active", "running")
        with self.jobs._lock:
            self.jobs._jobs[record["id"]] = record
        self.assertEqual(self.jobs.delete_job("job_active")["status"], "rejected")
        self.assertIn("job_active", self.jobs._jobs)
        self.assertEqual(self.jobs.delete_job("../outside")["status"], "invalid")

    def test_clear_terminal_history_never_removes_active_records_or_artifacts(self) -> None:
        with self.jobs._lock:
            self.jobs._jobs.update({
                "job_done": self._record("job_done", "failed"),
                "job_live": self._record("job_live", "running"),
            })
        with patch.object(self.jobs, "_save"):
            result = self.jobs.clear_terminal_history()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["removed_count"], 1)
        self.assertTrue(result["artifacts_preserved"])
        self.assertNotIn("job_done", self.jobs._jobs)
        self.assertIn("job_live", self.jobs._jobs)


class UserVisibleStatusContractTests(unittest.TestCase):
    def _render(self, route: str, state: dict[str, object]) -> str:
        payload = json.dumps(state, ensure_ascii=True)
        script = f"""
import {{ renderPage }} from './src/ui/pages.js';
globalThis.window = {{ localStorage: {{ getItem: () => 'vi' }} }};
process.stdout.write(renderPage({json.dumps(route)}, {payload}));
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

    def test_component_status_explains_purpose_reason_impact_and_action(self) -> None:
        html = self._render("components", {"componentManager": {"records": [{
            "component_id": "airi",
            "component_type": "runtime",
            "display_name": "AIRI",
            "status": "external_managed",
            "reason": "Ứng dụng ngoài do installer quản lý.",
            "next_action": "Mở AIRI Settings.",
            "execution": "not_run",
        }]}})
        self.assertIn('data-status-explanation', html)
        self.assertIn("Dùng để làm gì", html)
        self.assertIn("Tại sao", html)
        self.assertIn("Ảnh hưởng", html)
        self.assertIn("Bước tiếp theo", html)
        self.assertIn('data-severity="muted"', html)
        self.assertIn("AIRI", html)

    def test_jobs_renderer_has_bounded_history_controls_and_safe_explanations(self) -> None:
        html = self._render("jobs", {"jobs": [{
            "id": "jobv5_" + "b" * 32,
            "tool": "run_media_operation",
            "status": "failed",
            "created_at": "2026-08-24T00:00:00+00:00",
            "finished_at": "2026-08-24T00:01:00+00:00",
            "error": "Backend unavailable",
            "result": {},
        }]})
        for marker in ("data-job-search", "data-job-type-filter", "data-job-sort", "data-job-page", "data-delete-job", "data-clear-terminal-history", "Dùng để làm gì", "Ảnh hưởng"):
            self.assertIn(marker, html)
        self.assertIn("artifact", html.lower())

    def test_static_node_title_wrap_and_preview_contracts_exist(self) -> None:
        vendor = (ROOT / "src/ui/vendor/litegraph.js").read_text(encoding="utf-8")
        studio = (ROOT / "src/ui/features/node_studio/studio.js").read_text(encoding="utf-8")
        projects = (ROOT / "src/ui/features/projects/render.js").read_text(encoding="utf-8")
        css = (ROOT / "src/ui/styles.css").read_text(encoding="utf-8")
        for marker in ("hub_title_layout", "hub_title_height", "_hub_title_lines", "title_layout.lines.forEach"):
            self.assertIn(marker, vendor)
        for marker in ("Execution status", "Capability", "data-graph-property", "role=\"switch\""):
            self.assertIn(marker, studio)
        for marker in ("workflow-mini-graph", "data-asset-preview", "preview_url", "data-preview-fallback"):
            self.assertIn(marker, projects)
        for marker in ("status-pill[data-severity=\"error\"]", ".workflow-mini-graph", ".asset-preview-skeleton", ".graph-inspector"):
            self.assertIn(marker, css)

    def test_job_routes_are_in_generated_inventory(self) -> None:
        inventory = json.loads((ROOT / "architecture/api_routes.yaml").read_text(encoding="utf-8"))
        route_ids = {item["route_id"] for item in inventory["routes"]}
        self.assertIn("jobs.history_delete", route_ids)
        self.assertIn("jobs.history_clear", route_ids)


if __name__ == "__main__":
    unittest.main()
