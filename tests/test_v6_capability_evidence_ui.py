from __future__ import annotations

import copy
import json
import re
import subprocess
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

from tests import v6_capability_evidence_ui_fixture as fixture


ROOT = Path(__file__).resolve().parents[1]
UNSAFE_DISPLAY_PATTERN = re.compile(r"(?i)([a-z]:[\\/]|\\\\|(?:file|data|https?):|api[_-]?key|password|secret|token)\s*[:=]")


class V6CapabilityEvidenceUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pages = (ROOT / "src" / "ui" / "pages.js").read_text(encoding="utf-8")
        cls.node = (ROOT / "src" / "ui" / "node_studio.js").read_text(encoding="utf-8")
        cls.app = (ROOT / "src" / "ui" / "app.js").read_text(encoding="utf-8")
        cls.styles = (ROOT / "src" / "ui" / "styles.css").read_text(encoding="utf-8")

    def setUp(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), fixture.CapabilityEvidenceHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.assertFalse(self.thread.is_alive(), "fixture thread did not stop cleanly")

    def _json(self, path: str) -> dict[str, object]:
        with urlopen(Request(self.base_url + path), timeout=5) as response:
            self.assertEqual(response.status, 200)
            return json.loads(response.read())

    def _state(self, state: str = "completed") -> dict[str, object]:
        payload = fixture._bootstrap(state)
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

    def test_fixture_publishes_exact_scope_and_all_fail_closed_states(self) -> None:
        payload = self._json("/api/bootstrap")
        evidence = payload["productization"]["capabilities"]["runtime_evidence"]
        scope = payload["productization"]["capabilities"]["media_operation_scope"]
        self.assertEqual(evidence["operations"], list(fixture.MEDIA_OPERATIONS))
        self.assertEqual(scope["available_operations"], list(fixture.MEDIA_OPERATIONS))
        self.assertTrue(scope["evidence_verified"])
        self.assertNotRegex(json.dumps(payload), UNSAFE_DISPLAY_PATTERN)
        for state in ("error", "blocked", "not_run", "malformed"):
            state_payload = self._json(f"/api/bootstrap?state={state}")
            state_evidence = state_payload["productization"]["capabilities"]["runtime_evidence"]
            if state == "malformed":
                self.assertIsInstance(state_evidence["cleanup"]["processes_remaining"], dict)
            else:
                self.assertEqual(state_evidence["outcome"], "blocked" if state == "blocked" else state)
            self.assertFalse(state_payload["productization"]["capabilities"]["media_operation_scope"]["evidence_verified"])

    def test_dashboard_settings_and_media_share_one_truthful_evidence_model(self) -> None:
        state = self._state()
        dashboard = self._render("dashboard", state)
        settings = self._render("settings", state)
        media = self._render("media", state)
        for html in (dashboard, settings, media):
            self.assertIn('data-media-evidence-status="operational"', html)
            self.assertIn('data-media-evidence-outcome="completed"', html)
            for operation in fixture.MEDIA_OPERATIONS:
                self.assertIn(f'data-media-operation="{operation}"', html)
            self.assertNotIn("[object Object]", html)
            self.assertNotRegex(html, UNSAFE_DISPLAY_PATTERN)
        self.assertIn('data-readiness-route="settings"', dashboard)
        self.assertIn('data-route="media"', settings)
        self.assertIn('data-media-generic-status="partial"', settings)
        self.assertIn('data-media-generic-action="explanatory"', media)
        self.assertIn("Execution unavailable from this snapshot", media)
        self.assertNotIn('data-media-generic-status="operational"', media)

    def test_error_blocked_not_run_and_malformed_states_never_green_promote(self) -> None:
        for state_name in ("error", "blocked", "not_run", "malformed"):
            html = self._render("media", self._state(state_name))
            self.assertIn('data-media-evidence-status="unavailable"', html)
            self.assertNotIn('data-operation-status="operational"', html)
            self.assertNotIn("[object Object]", html)
            self.assertNotRegex(html, UNSAFE_DISPLAY_PATTERN)
            if state_name != "malformed":
                self.assertIn(f'data-media-evidence-outcome="{"blocked" if state_name == "blocked" else state_name}"', html)
            else:
                self.assertIn('data-media-evidence-outcome="not_run"', html)

    def test_runtime_schema_version_is_required_and_hostile_shapes_fail_closed(self) -> None:
        for replacement in ("missing", [], {}, "runtime-evidence-projection.v0"):
            state = self._state()
            evidence = state["productization"]["capabilities"]["runtime_evidence"]
            if replacement == "missing":
                evidence.pop("schema_version", None)
            else:
                evidence["schema_version"] = replacement
            html = self._render("media", state)
            self.assertIn('data-media-evidence-status="unavailable"', html)
            self.assertIn('data-media-evidence-outcome="not_run"', html)
            self.assertIn('data-media-evidence-verified="false"', html)
            self.assertNotIn('data-media-evidence-status="operational"', html)

        model_slice = self.pages[self.pages.index("const MEDIA_EVIDENCE_OPERATIONS"):self.pages.index("const JOB_STATUS_RANK")]
        self.assertIn('value.schema_version !== "runtime-evidence-projection.v1"', model_slice)
        self.assertNotIn("value.schema_version !== undefined", model_slice)

    def test_hostile_unknown_evidence_is_fixed_safe_and_not_echoed(self) -> None:
        state = self._state()
        hostile = copy.deepcopy(state)
        evidence = hostile["productization"]["capabilities"]["runtime_evidence"]
        evidence["reason"] = "C:\\fixture\\private; token=hidden"
        evidence["unknown_nested"] = {"callable": "command --unsafe"}
        html = self._render("settings", hostile)
        self.assertIn('data-media-evidence-status="unavailable"', html)
        self.assertIn("No safe server-owned media evidence", html)
        self.assertNotRegex(html, UNSAFE_DISPLAY_PATTERN)
        self.assertNotIn("command --unsafe", html)
        self.assertNotIn("[object Object]", html)

    def test_source_model_and_fast_refresh_contract_are_bounded(self) -> None:
        model_slice = self.pages[self.pages.index("const MEDIA_EVIDENCE_OPERATIONS"):self.pages.index("const JOB_STATUS_RANK")]
        for marker in ("runtime_evidence", "media_operation_scope", "video_grade", "logo_overlay", "encode", "mediaEvidenceFallback", "Server snapshot only"):
            self.assertIn(marker, model_slice)
        self.assertNotIn("JSON.stringify", model_slice)
        refresh_slice = self.app[self.app.index("const refreshFast"):self.app.index("const refreshCreative")]
        self.assertIn("getCapabilities()", refresh_slice)
        self.assertNotIn("getProductization", refresh_slice)
        self.assertNotIn("getStorage()", refresh_slice)

    def test_node_studio_consumes_registry_scope_without_a_second_editor(self) -> None:
        for marker in ("operation_scope", "normalizeOperationScope", "operationEvidenceFor", "operationAvailabilityFor", "mediaGraphRunEligibility", "data-operation-scope-status", "data-graph-operation-evidence", "video_grade", "logo_overlay", "encode", 'setAttribute("role", "application")'):
            self.assertIn(marker, self.node)
        self.assertEqual(self.node.count("new globalThis.LiteGraph.LGraphCanvas"), 1)
        self.assertEqual(self.node.count("new globalThis.LiteGraph.LGraph()"), 1)
        self.assertNotIn("getCapabilities", self.node)
        self.assertIn("focus-visible", self.styles)

    def test_completed_scope_cannot_run_generic_operational_media_node(self) -> None:
        script = """
import { mediaGraphRunEligibility } from './src/ui/node_studio.js';
const scope = {status: 'operational', execution: 'completed', evidenceVerified: true, availableOperations: ['video_grade', 'logo_overlay', 'encode'], operationStatus: {video_grade: 'operational', logo_overlay: 'operational', encode: 'operational'}};
const exact = {nodes: [{type: 'video_grade'}, {type: 'encode'}]};
const generic = {nodes: [{type: 'generic_media', status: 'operational'}]};
const mixed = {nodes: [{type: 'video_grade'}, {type: 'generic_media', status: 'operational'}]};
const hostileScope = {...scope, operationStatus: {...scope.operationStatus, generic_media: 'operational'}};
process.stdout.write(JSON.stringify({exact: mediaGraphRunEligibility('media', exact, scope), generic: mediaGraphRunEligibility('media', generic, scope), mixed: mediaGraphRunEligibility('media', mixed, scope), unavailable: mediaGraphRunEligibility('media', exact, {...scope, evidenceVerified: false}), hostileScope: mediaGraphRunEligibility('media', exact, hostileScope)}));
"""
        result = subprocess.run(["node", "--input-type=module", "--eval", script], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        outcome = json.loads(result.stdout)
        self.assertTrue(outcome["exact"]["eligible"])
        self.assertFalse(outcome["generic"]["eligible"])
        self.assertFalse(outcome["mixed"]["eligible"])
        self.assertFalse(outcome["unavailable"]["eligible"])
        self.assertFalse(outcome["hostileScope"]["eligible"])
        run_slice = self.node[self.node.index("async run("):self.node.index("async cancel(")]
        self.assertIn("const eligibility = this.runEligibility()", run_slice)
        self.assertIn("if (!eligibility.eligible)", run_slice)
        self.assertLess(run_slice.index("if (!eligibility.eligible)"), run_slice.index("runNodeGraph"))
        handle_slice = self.node[self.node.index("handleAction(action)"):self.node.index("nextPanelWidth(value)")]
        self.assertIn("this.runEligibility()", handle_slice)
        self.assertIn("if (!eligibility.eligible)", handle_slice)

    def test_registry_fixture_publishes_scope_and_unrelated_node_stays_partial(self) -> None:
        payload = self._json("/api/node-studio/registry?scope=media")
        self.assertEqual(payload["operation_scope"]["operations"], list(fixture.MEDIA_OPERATIONS))
        nodes = {item["type"]: item for item in payload["nodes"]}
        for operation in fixture.MEDIA_OPERATIONS:
            self.assertEqual(nodes[operation]["status"], "operational")
        self.assertEqual(nodes["generic_media"]["status"], "partial")
        generic_operational = copy.deepcopy(nodes["generic_media"])
        generic_operational["status"] = "operational"
        generic_operational["availability"]["status"] = "operational"
        generic_operational["availability"]["reason"] = "Hostile completed generic mock must remain non-operational in the UI."
        self.assertEqual(generic_operational["status"], "operational")
        self.assertIn("operationAvailabilityFor", self.node)
        self.assertNotIn("[object Object]", json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
