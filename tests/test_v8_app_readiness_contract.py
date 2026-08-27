import json
import hashlib
from pathlib import Path
import tempfile
import unittest

from src.app.readiness import (
    LEGACY_PAYLOAD_ALLOWLIST,
    _legacy_payload_is_safe,
    classify_state,
    evaluate_readiness,
    event_seen,
    load_state,
    record_event,
    record_frontend_signal,
)


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

    def test_normal_close_records_terminal_user_exit_instead_of_running(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v8-readiness-close-") as folder:
            root = Path(folder)
            payload_id = "main-aaaaaaaaaaaa"
            payload = root / "versions" / payload_id
            payload.mkdir(parents=True)
            (root / "current.json").write_text(json.dumps({"version": payload_id}), encoding="utf-8")
            (payload / "build.json").write_text(json.dumps({"source_commit": "a" * 40}), encoding="utf-8")
            record_event(root, "desktop_started", status="starting", pid=4321)
            result = record_event(root, "desktop_closed_by_user", pid=4321)
            self.assertEqual(result["status"], "recorded")
            state = load_state(root)
            self.assertEqual(state["status"], "normal_exit")
            self.assertTrue(event_seen(state, "desktop_closed_by_user"))
            self.assertFalse(classify_state(state, desktop_process_alive=False, current_pid=4321)["active_session"])

    def test_unexpected_exit_is_distinct_from_normal_close(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v8-readiness-crash-") as folder:
            root = Path(folder)
            payload_id = "main-aaaaaaaaaaaa"
            payload = root / "versions" / payload_id
            payload.mkdir(parents=True)
            (root / "current.json").write_text(json.dumps({"version": payload_id}), encoding="utf-8")
            (payload / "build.json").write_text(json.dumps({"source_commit": "a" * 40}), encoding="utf-8")
            record_event(root, "desktop_started", status="starting", pid=4321)
            record_event(root, "desktop_unexpected_exit", pid=4321)
            state = load_state(root)
            self.assertEqual(state["status"], "unexpected_exit")
            self.assertTrue(event_seen(state, "desktop_unexpected_exit"))
            self.assertFalse(classify_state(state, desktop_process_alive=False, current_pid=4321)["active_session"])

    def test_stale_active_session_is_not_reported_as_running(self) -> None:
        state = {
            "schema_version": "v8-desktop-readiness.v1",
            "status": "running",
            "pid": 9876,
            "payload_id": "main-aaaaaaaaaaaa",
            "events": [{"event": "frontend_ready", "route": "dashboard"}],
        }
        stale = classify_state(state, desktop_process_alive=False, current_pid=None)
        self.assertEqual(stale["status"], "stale_session")
        self.assertTrue(stale["stale"])
        self.assertFalse(stale["active_session"])
        self.assertEqual(stale["exit_reason"], "unknown")

    def test_live_session_requires_the_recorded_pid(self) -> None:
        state = {"status": "running", "pid": 9876}
        self.assertTrue(classify_state(state, desktop_process_alive=True, current_pid=9876)["active_session"])
        mismatch = classify_state(state, desktop_process_alive=True, current_pid=1234)
        self.assertEqual(mismatch["status"], "stale_session")
        self.assertFalse(mismatch["active_session"])

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

    def test_legacy_frontend_signal_is_allowed_without_pending_update(self) -> None:
        """The original 8.0.1 payload must not be stranded by V8 identity gates."""

        with tempfile.TemporaryDirectory(prefix="v8-readiness-legacy-signal-") as folder:
            root = Path(folder)
            payload = root / "versions" / "8.0.1"
            payload.mkdir(parents=True)
            (root / "current.json").write_text(json.dumps({"version": "8.0.1"}), encoding="utf-8")
            record_event(root, "desktop_started", status="starting", pid=4321)
            self.assertEqual(record_frontend_signal(root, "frontend_bootstrap_completed")["status"], "recorded")
            self.assertEqual(record_frontend_signal(root, "frontend_dom_visible", route="dashboard")["status"], "recorded")
            self.assertEqual(record_frontend_signal(root, "frontend_rendered", route="dashboard")["status"], "recorded")
            ready = record_frontend_signal(root, "frontend_ready", route="dashboard")
            self.assertEqual(ready["status"], "recorded")

            pending = root / "update-state" / "pending-health.json"
            pending.parent.mkdir(parents=True, exist_ok=True)
            pending.write_text("{}", encoding="utf-8")
            blocked = record_frontend_signal(root, "frontend_ready", route="dashboard")
            self.assertEqual(blocked["code"], "READINESS_IDENTITY_UNAVAILABLE")

    def test_legacy_compatibility_is_an_explicit_allowlist(self) -> None:
        self.assertEqual(LEGACY_PAYLOAD_ALLOWLIST, {"8.0.1"})
        with tempfile.TemporaryDirectory(prefix="v8-readiness-legacy-allowlist-") as folder:
            root = Path(folder)
            for version in ("foo", "8.0.0-test", "8.0.1-hotfix-unknown", "main-aaaaaaaaaaaa"):
                payload = root / "versions" / version
                payload.mkdir(parents=True)
                (root / "current.json").write_text(json.dumps({"version": version}), encoding="utf-8")
                self.assertFalse(_legacy_payload_is_safe(root), version)
                payload.rmdir()
            payload = root / "versions" / "8.0.1"
            payload.mkdir(parents=True)
            (root / "current.json").write_text(json.dumps({"version": "8.0.1"}), encoding="utf-8")
            self.assertTrue(_legacy_payload_is_safe(root))

    def test_legacy_identity_metadata_is_verified_when_present(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v8-readiness-legacy-identity-") as folder:
            root = Path(folder)
            payload = root / "versions" / "8.0.1"
            payload.mkdir(parents=True)
            manifest = {
                "schema_version": "v8.0.1-version-manifest.v1",
                "product_id": "LocalAIHub",
                "version": "8.0.1",
                "app_relative": "app",
                "runtime_relative": "runtime/Python312/pythonw.exe",
                "entrypoint": "src.app.launcher",
            }
            manifest_path = payload / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
            digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            (root / "current.json").write_text(json.dumps({
                "schema_version": "v8.0.1-pointer.v1",
                "version": "8.0.1",
                "payload_relative": "versions/8.0.1",
                "manifest_sha256": digest,
            }, sort_keys=True), encoding="utf-8")
            (root / "product.json").write_text(json.dumps({
                "schema_version": "v8.0.1-product.v1",
                "product_id": "LocalAIHub",
                "version": "8.0.1",
                "launcher": "LocalAIHub.exe",
                "icon": "local-ai-hub.ico",
                "current_pointer": "current.json",
            }, sort_keys=True), encoding="utf-8")
            (root / "installation.json").write_text(json.dumps({
                "schema_version": "v8.0.1-installation.v1",
                "product_id": "LocalAIHub",
                "app_root": str(root),
                "data_root": str(root / "data"),
                "app_user_model_id": "LocalAIHub.Desktop",
                "launcher": "LocalAIHub.exe",
            }, sort_keys=True), encoding="utf-8")
            self.assertTrue(_legacy_payload_is_safe(root))

            malformed_pointer = json.loads((root / "current.json").read_text(encoding="utf-8"))
            malformed_pointer["payload_relative"] = "../../outside"
            (root / "current.json").write_text(json.dumps(malformed_pointer), encoding="utf-8")
            self.assertFalse(_legacy_payload_is_safe(root))

            (root / "current.json").write_text(json.dumps({"version": "8.0.1"}), encoding="utf-8")
            (root / "product.json").write_text(json.dumps({"version": "8.0.1", "product_id": "not-local-ai-hub"}), encoding="utf-8")
            self.assertFalse(_legacy_payload_is_safe(root))

    def test_legacy_pending_health_remains_strict(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v8-readiness-legacy-pending-") as folder:
            root = Path(folder)
            (root / "versions" / "8.0.1").mkdir(parents=True)
            (root / "current.json").write_text(json.dumps({"version": "8.0.1"}), encoding="utf-8")
            pending = root / "update-state" / "pending-health.json"
            pending.parent.mkdir(parents=True)
            pending.write_text("{}", encoding="utf-8")
            self.assertFalse(_legacy_payload_is_safe(root))

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
        self.assertIn('recordFrontendEvent("frontend_nav_visible", routeId())', app_js)
        self.assertIn('recordFrontendEvent("frontend_view_visible", routeId())', app_js)
        self.assertIn("requestAnimationFrame", app_js)
        self.assertIn("setTimeout(finish, 500)", app_js)
        self.assertIn("AbortController", app_js)
        self.assertIn("source_commit", app_js)
        self.assertIn("potentially blocking native", app_js)
        self.assertNotIn('bridge.frontend.record("frontend_ready")', app_js)
        self.assertIn("HTTP 200 của API không đủ", index_html)


if __name__ == "__main__":
    unittest.main()
