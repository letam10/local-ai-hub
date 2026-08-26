"""Final V8 correction contracts for long scans and truthful durable retry UX."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.services.api.v5_productization import project_job_recovery, retry_durable_job


ROOT = Path(__file__).resolve().parents[1]


def _run_node(script: str) -> subprocess.CompletedProcess[str]:
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("node is required for frontend contract tests")
    return subprocess.run(
        [node, "--input-type=module", "--eval", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )


class FinalCorrectionTests(unittest.TestCase):
    def test_storage_poller_runs_past_sixty_seconds_and_handles_cancel(self) -> None:
        script = r'''
import assert from "node:assert/strict";
import { createStorageScanPoller } from "./src/ui/storage_scan_polling.js";

const makeHarness = (statuses, options = {}) => {
  let clock = 0;
  let routeActive = true;
  let calls = 0;
  const queue = [];
  const seen = [];
  const ceilings = [];
  const schedule = (callback, delay) => queue.push({ callback, at: clock + delay });
  const poller = createStorageScanPoller({
    getSnapshot: async () => {
      calls += 1;
      const status = statuses.shift() || "running";
      return { scan: { scan_id: "scan-fixture", status, progress: Math.min(99, calls) } };
    },
    isRouteActive: () => routeActive,
    onSnapshot: (value) => seen.push(value.scan.status),
    onCeiling: (value) => ceilings.push(value.scan.polling_message),
    schedule,
    now: () => clock,
    initialDelayMs: options.initialDelayMs ?? 0,
    intervalMs: options.intervalMs ?? 500,
    ceilingMs: options.ceilingMs ?? 600000,
  });
  const pump = async () => {
    queue.sort((left, right) => left.at - right.at);
    const next = queue.shift();
    if (!next) return false;
    clock = next.at;
    await next.callback();
    return true;
  };
  return { poller, pump, queue, seen, ceilings, setRoute: (value) => { routeActive = value; }, get calls() { return calls; }, get clock() { return clock; } };
};

const long = makeHarness([...Array(130).fill("running"), "completed"]);
long.poller.start("scan-fixture");
while (long.queue.length) await long.pump();
assert.equal(long.calls, 131);
assert.ok(long.clock >= 60000, `fixture only advanced ${long.clock}ms`);
assert.equal(long.seen.at(-1), "completed");
assert.equal(long.ceilings.length, 0);

const cancelling = makeHarness(["cancelling", "cancelling", "cancelled"]);
cancelling.poller.start("scan-fixture");
while (cancelling.queue.length) await cancelling.pump();
assert.deepEqual(cancelling.seen, ["cancelling", "cancelling", "cancelled"]);

const route = makeHarness(["running", "completed"]);
route.poller.start("scan-fixture");
await route.pump();
assert.equal(route.calls, 1);
route.setRoute(false);
route.poller.stop();
while (route.queue.length) await route.pump();
assert.equal(route.calls, 1);

const ceiling = makeHarness(["running", "running", "running"], { ceilingMs: 1000, intervalMs: 500 });
ceiling.poller.start("scan-fixture");
while (ceiling.queue.length) await ceiling.pump();
assert.equal(ceiling.calls, 2);
assert.equal(ceiling.ceilings.length, 1);
assert.match(ceiling.ceilings[0], /Quét vẫn đang chạy nền/);
'''
        result = _run_node(script)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_retry_api_marks_reconstruct_only_without_execution(self) -> None:
        with TemporaryDirectory() as temporary:
            response = {
                "status": "queued",
                "execution": "not_run",
                "dry_run": True,
                "job": {"id": "jobv5_" + "a" * 32, "status": "queued"},
            }
            with patch("src.services.api.v5_productization.resume_durable_job", return_value=response):
                result = retry_durable_job("jobv5_" + "b" * 32, Path(temporary) / "durable.json")
        self.assertEqual(result["retry_contract"], "new_job")
        self.assertEqual(result["retry_mode"], "reconstruct_only")
        self.assertFalse(result["actual_retry_execution"])
        self.assertEqual(result["execution"], "not_run")
        self.assertTrue(result["dry_run"])
        self.assertTrue(result["historical_record_preserved"])

    def test_public_projection_excludes_reconstruct_only_pending_from_active_counts(self) -> None:
        old_id = "jobv5_" + "a" * 32
        new_id = "jobv5_" + "b" * 32
        projected = project_job_recovery([
            {
                "id": old_id,
                "source": "durable",
                "status": "failed",
                "retry_mode": "reconstruct_only",
                "execution": "not_run",
                "dry_run": True,
            },
            {
                "id": new_id,
                "source": "durable",
                "status": "queued",
                "retry_of": old_id,
                "execution": "not_run",
            },
        ])
        new_record = next(item for item in projected["records"] if item["id"] == new_id)
        self.assertEqual(projected["counts"]["active"], 0)
        self.assertFalse(new_record["active"])
        self.assertTrue(new_record["reconstruct_only_pending"])
        self.assertEqual(new_record["status"], "queued")
        self.assertEqual(new_record["execution"], "not_run")
        self.assertTrue(new_record["dry_run"])
        self.assertFalse(new_record["actual_retry_execution"])

    def test_live_hot_queued_job_remains_active(self) -> None:
        job_id = "job_hot_queued"
        projected = project_job_recovery([{"id": job_id, "source": "hot", "status": "queued"}])
        record = projected["records"][0]
        self.assertEqual(projected["counts"]["active"], 1)
        self.assertTrue(record["active"])
        self.assertFalse(record["reconstruct_only_pending"])

    def test_jobs_ui_excludes_reconstruct_only_pending_but_keeps_hot_queue_active(self) -> None:
        durable_id = "jobv5_" + "c" * 32
        hot_id = "job_hot_queued"
        state = {
            "productization": {
                "jobs": {
                    "status": "partial",
                    "execution": "not_run",
                    "dry_run": True,
                    "records": [{
                        "id": durable_id,
                        "tool": "unit_retryable",
                        "source": "durable",
                        "status": "queued",
                        "progress": 0,
                        "retry_mode": "reconstruct_only",
                        "execution": "not_run",
                        "dry_run": True,
                        "artifacts": [],
                    }],
                    # Deliberately stale to prove the frontend derives active
                    # from the public record semantics instead of this count.
                    "counts": {"active": 1, "attention": 0, "interrupted": 0, "recoverable": 0, "total": 1},
                },
            },
        }
        hot_state = {
            "productization": {
                "jobs": {
                    "status": "partial",
                    "execution": "not_run",
                    "dry_run": True,
                    "records": [{
                        "id": hot_id,
                        "tool": "unit_live",
                        "source": "hot",
                        "status": "queued",
                        "progress": 0,
                        "artifacts": [],
                    }],
                    "counts": {"active": 0, "attention": 0, "interrupted": 0, "recoverable": 0, "total": 1},
                },
            },
        }
        script = f'''
import assert from "node:assert/strict";
import {{ jobRecoverySnapshot }} from "./src/ui/pages.js";
import {{ renderPage }} from "./src/ui/pages.js";
const state = {json.dumps(state, ensure_ascii=True)};
const recovery = jobRecoverySnapshot(state);
assert.equal(recovery.counts.active, 0);
assert.equal(recovery.records[0].active, false);
assert.equal(recovery.records[0].reconstructOnlyPending, true);
assert.equal(recovery.records[0].actualRetryExecution, false);
const allHtml = renderPage("jobs", state);
assert.match(allHtml, /Đã tạo · chưa thực thi/);
assert.doesNotMatch(allHtml, /Đang xử lý/);
const activeHtml = renderPage("jobs", {{ ...state, jobFilter: "active" }});
assert.doesNotMatch(activeHtml, /{durable_id}/);
const hotState = {json.dumps(hot_state, ensure_ascii=True)};
const hotRecovery = jobRecoverySnapshot(hotState);
assert.equal(hotRecovery.counts.active, 1);
assert.equal(hotRecovery.records[0].active, true);
const hotActiveHtml = renderPage("jobs", {{ ...hotState, jobFilter: "active" }});
assert.match(hotActiveHtml, /{hot_id}/);
'''
        result = _run_node(script)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_frontend_exposes_long_polling_and_truthful_durable_retry_labels(self) -> None:
        app = (ROOT / "src" / "ui" / "app.js").read_text(encoding="utf-8")
        poller = (ROOT / "src" / "ui" / "storage_scan_polling.js").read_text(encoding="utf-8")
        models = (ROOT / "src" / "ui" / "features" / "models" / "models.js").read_text(encoding="utf-8")
        jobs = (ROOT / "src" / "ui" / "features" / "jobs" / "render.js").read_text(encoding="utf-8")
        shared = (ROOT / "src" / "ui" / "shared" / "rendering.js").read_text(encoding="utf-8")
        adapter = (ROOT / "src" / "services" / "api" / "v5_productization.py").read_text(encoding="utf-8")
        self.assertNotIn("attempts >= 120", app)
        for marker in ("STORAGE_SCAN_POLL_CEILING_MS", "STORAGE_SCAN_ACTIVE_STATES", "cancelling", "polling_limited", "Theo dõi tiếp"):
            self.assertIn(marker, poller + app + models)
        self.assertIn("storageScanPoller.stop()", app)
        self.assertIn("Quét vẫn đang chạy nền", app + poller + models)
        for marker in ("Tạo lại tác vụ", "chưa thực thi", "retryMode", "reconstruct_only"):
            self.assertIn(marker, jobs + shared + adapter + app)

    def test_durable_render_does_not_call_reconstruct_only_a_retry(self) -> None:
        job_id = "jobv5_" + "c" * 32
        state = {
            "productization": {
                "jobs": {
                    "status": "partial",
                    "execution": "not_run",
                    "dry_run": True,
                    "records": [{
                        "id": job_id,
                        "tool": "unit_retryable",
                        "source": "durable",
                        "status": "failed",
                        "progress": 0,
                        "resumable": True,
                        "retry_mode": "reconstruct_only",
                        "execution": "not_run",
                        "dry_run": True,
                        "next_action": "Create a new task from the retained request.",
                        "artifacts": [],
                    }],
                    "counts": {"active": 0, "attention": 1, "interrupted": 0, "recoverable": 1, "total": 1},
                },
            },
        }
        script = f'''
import assert from "node:assert/strict";
import {{ renderPage }} from "./src/ui/pages.js";
const html = renderPage("jobs", {json.dumps(state, ensure_ascii=True)});
assert.match(html, /Tạo lại tác vụ/);
assert.match(html, /chưa thực thi/);
assert.doesNotMatch(html, /data-resume-durable-job="{job_id}"[^>]*>Thử lại/);
'''
        result = _run_node(script)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
