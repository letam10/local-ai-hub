import json
from pathlib import Path
import tempfile
import unittest

from src.app.readiness import evaluate_readiness, event_seen, load_state, record_event, record_frontend_signal


ROOT = Path(__file__).resolve().parents[1]


class V8AppReadinessContractTests(unittest.TestCase):
    def test_http_200_alone_can_never_claim_recovered(self) -> None:
        result = evaluate_readiness({"health_status": 200, "ui_status": 200, "bootstrap_status": 200})
        self.assertEqual(result["status"], "not_ready")
        self.assertFalse(result["user_app_recovered"])
        self.assertIn("desktop_process_alive", result["missing"])
        self.assertTrue(result["http_status_ignored"])

    def test_exact_desktop_frontend_and_routes_are_required(self) -> None:
        evidence = {
            "health_status": 200,
            "ui_status": 200,
            "bootstrap_status": 200,
            "desktop_process_alive": True,
            "window_visible": True,
            "window_responding": True,
            "api_identity_match": True,
            "payload_identity_match": True,
            "bootstrap_payload_valid": True,
            "frontend_ready": True,
            "dashboard_rendered": True,
            "routes_complete": True,
            "stable_window": True,
        }
        result = evaluate_readiness(evidence)
        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["user_app_recovered"])

    def test_missing_route_or_stale_window_is_not_recovered(self) -> None:
        evidence = {name: True for name in (
            "desktop_process_alive", "window_visible", "window_responding", "api_identity_match",
            "payload_identity_match", "bootstrap_payload_valid", "frontend_ready", "dashboard_rendered",
            "routes_complete", "stable_window",
        )}
        evidence["routes_complete"] = False
        result = evaluate_readiness(evidence)
        self.assertEqual(result["status"], "not_ready")
        self.assertIn("routes_complete", result["missing"])

    def test_readiness_events_are_bounded_and_identity_bound(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v8-readiness-") as folder:
            root = Path(folder)
            payload_id = "main-aaaaaaaaaaaa"
            payload = root / "versions" / payload_id
            payload.mkdir(parents=True)
            (root / "current.json").write_text(json.dumps({"version": payload_id}), encoding="utf-8")
            (payload / "build.json").write_text(json.dumps({"source_commit": "a" * 40}), encoding="utf-8")
            self.assertEqual(record_event(root, "desktop_started", status="starting", pid=1234)["status"], "recorded")
            for _ in range(100):
                record_event(root, "route_rendered", route="dashboard", pid=1234)
            state = load_state(root)
            self.assertEqual(state["schema_version"], "v8-desktop-readiness.v1")
            self.assertLessEqual(len(state["events"]), 64)
            self.assertTrue(event_seen(state, "route_rendered", route="dashboard"))
            serialized = json.dumps(state)
            self.assertNotIn("\\", serialized)

    def test_loopback_frontend_signal_requires_identity_and_render_before_ready(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v8-readiness-signal-") as folder:
            root = Path(folder)
            payload_id = "main-aaaaaaaaaaaa"
            payload = root / "versions" / payload_id
            payload.mkdir(parents=True)
            (root / "current.json").write_text(json.dumps({"version": payload_id}), encoding="utf-8")
            (payload / "build.json").write_text(json.dumps({"source_commit": "a" * 40}), encoding="utf-8")
            record_event(root, "desktop_started", status="starting", pid=4321)
            blocked = record_frontend_signal(root, "frontend_ready", source_commit="a" * 40, payload_id=payload_id)
            self.assertEqual(blocked["code"], "FRONTEND_RENDER_REQUIRED")
            self.assertEqual(record_frontend_signal(root, "frontend_bootstrap_completed", source_commit="a" * 40, payload_id=payload_id)["status"], "recorded")
            self.assertEqual(record_frontend_signal(root, "frontend_dom_visible", route="dashboard", source_commit="a" * 40, payload_id=payload_id)["status"], "recorded")
            self.assertEqual(record_frontend_signal(root, "frontend_rendered", route="dashboard", source_commit="a" * 40, payload_id=payload_id)["status"], "recorded")
            ready = record_frontend_signal(root, "frontend_ready", source_commit="a" * 40, payload_id=payload_id)
            self.assertEqual(ready["status"], "recorded")
            mismatch = record_frontend_signal(root, "frontend_ready", source_commit="b" * 40, payload_id=payload_id)
            self.assertEqual(mismatch["code"], "READINESS_SOURCE_MISMATCH")

    def test_frontend_has_failure_guard_and_ready_contract(self) -> None:
        app_js = (ROOT / "src" / "ui" / "app.js").read_text(encoding="utf-8")
        index_html = (ROOT / "src" / "ui" / "index.html").read_text(encoding="utf-8")
        self.assertIn("initialize().catch", app_js)
        self.assertIn("frontend_js_bootstrap_failed", app_js)
        self.assertIn("__localAiHubFrontendReady", app_js)
        self.assertIn("__localAiHubFrontendRendered", app_js)
        self.assertIn("__localAiHubFrontendBootstrapReady", app_js)
        self.assertIn("/api/desktop/readiness", app_js)
        self.assertIn("recordLoopbackFrontendEvent", app_js)
        self.assertIn('recordLoopbackFrontendEvent("frontend_ready"', app_js)
        self.assertIn('recordFrontendEvent("frontend_bootstrap_completed")', app_js)
        self.assertIn('recordFrontendEvent("frontend_dom_visible", routeId())', app_js)
        self.assertIn("requestAnimationFrame", app_js)
        self.assertIn("AbortController", app_js)
        self.assertIn("source_commit", app_js)
        self.assertIn("potentially blocking native", app_js)
        self.assertNotIn('bridge.frontend.record("frontend_ready")', app_js)
        self.assertIn("HTTP 200 của API không đủ", index_html)


if __name__ == "__main__":
    unittest.main()
