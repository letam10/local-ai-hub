from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STUDIO = ROOT / "src" / "ui" / "features" / "node_studio" / "studio.js"
I18N = ROOT / "src" / "ui" / "i18n.js"
STYLES = ROOT / "src" / "ui" / "styles.css"


class M5NodeHubAuthoringTests(unittest.TestCase):
    def _run_node_helpers(self, body: str) -> None:
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is required for M5 pure helper contracts")
        script = r"""
import { readFileSync } from "node:fs";
const source = readFileSync("src/ui/features/node_studio/studio.js", "utf8");
const start = source.indexOf("const NODE_UI_STATE_VERSION");
const end = source.indexOf("function artifactPreviewState", start);
const escapeHtml = (value) => String(value).replace(/[&<>\"']/g, (item) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[item]));
const moduleSource = "const LOCAL_PREFIX = 'test';\nconst escapeHtml = " + escapeHtml.toString() + ";\n" + source.slice(start, end);
const helpers = await import("data:text/javascript;base64," + Buffer.from(moduleSource).toString("base64"));
""" + body
        result = subprocess.run(
            [node, "--input-type=module", "-e", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def _run_extended_node_helpers(self, body: str) -> None:
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is required for M5 state helper contracts")
        script = r"""
import { readFileSync } from "node:fs";
const source = readFileSync("src/ui/features/node_studio/studio.js", "utf8");
const start = source.indexOf("const NODE_UI_STATE_VERSION");
const end = source.indexOf("function createSliderWidget", start);
const escapeHtml = (value) => String(value).replace(/[&<>\"']/g, (item) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[item]));
const moduleSource = "const LOCAL_PREFIX = 'test';\nconst translateText = (value) => String(value);\nconst currentLanguage = () => 'en';\nconst escapeHtml = " + escapeHtml.toString() + ";\n" + source.slice(start, end);
const helpers = await import("data:text/javascript;base64," + Buffer.from(moduleSource).toString("base64"));
""" + body
        result = subprocess.run(
            [node, "--input-type=module", "-e", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_layout_and_insertion_are_deterministic_and_non_overlapping(self) -> None:
        self._run_node_helpers(
            r"""
const assert = await import("node:assert/strict");
const nodes = [
  { id: "sink", position: { x: 0, y: 0 }, size: [260, 120] },
  { id: "source", position: { x: 0, y: 0 }, size: [180, 80] },
  { id: "middle", position: { x: 0, y: 0 }, size: [220, 100] },
];
const edges = [
  { source: { node: "source", port: "image" }, target: { node: "middle", port: "image" } },
  { source: { node: "middle", port: "image" }, target: { node: "sink", port: "image" } },
];
const first = helpers.computeDeterministicLayout(nodes, edges);
const second = helpers.computeDeterministicLayout(nodes, edges);
assert.deepEqual(first, second);
const byId = new Map(first.map((item) => [item.id, item]));
assert.ok(byId.get("source").x < byId.get("middle").x);
assert.ok(byId.get("middle").x < byId.get("sink").x);
const placed = first.map((item) => ({ ...nodes.find((node) => node.id === item.id), pos: [item.x, item.y] }));
for (let left = 0; left < placed.length; left += 1) for (let right = left + 1; right < placed.length; right += 1) assert.equal(helpers.nodesOverlap(placed[left], placed[right]), false);
const occupied = [{ id: "existing", pos: [80, 80], size: [230, 100] }];
const slot = helpers.findFreeGridSlot(occupied, { x: 80, y: 80 }, { width: 230, height: 100 }, { grid: 40, gap: 32 });
assert.equal(helpers.nodesOverlap({ pos: [slot.x, slot.y], size: [230, 100] }, occupied[0], 32), false);
assert.deepEqual(slot, helpers.findFreeGridSlot(occupied, { x: 80, y: 80 }, { width: 230, height: 100 }, { grid: 40, gap: 32 }));
""",
        )

    def test_minimap_fingerprint_and_scheduler_are_bounded(self) -> None:
        self._run_node_helpers(
            r"""
const assert = await import("node:assert/strict");
const nodes = [{ id: "a", pos: [0, 0], size: [100, 60], outputs: [{ type: "IMAGE" }] }, { id: "b", pos: [240, 0], size: [100, 60] }];
const edge = { id: "edge-a-b", origin_id: "a", target_id: "b", origin_slot: 0, target_slot: 0, type: "IMAGE" };
const base = helpers.minimapFingerprint({ nodes, edges: [edge], scale: 1, offset: [0, 0], selectedIds: ["a"], viewport: [800, 500] });
assert.notEqual(base, helpers.minimapFingerprint({ nodes, edges: [edge], scale: 1.2, offset: [0, 0], selectedIds: ["a"], viewport: [800, 500] }));
assert.notEqual(base, helpers.minimapFingerprint({ nodes, edges: [{ ...edge, type: "VIDEO" }], scale: 1, offset: [0, 0], selectedIds: ["a"], viewport: [800, 500] }));
assert.notEqual(base, helpers.minimapFingerprint({ nodes, edges: [edge], scale: 1, offset: [0, 0], selectedIds: ["b"], viewport: [800, 500] }));
const rawLiteNodes = [{ id: 1, hubId: "hub-a", pos: [0, 0], size: [100, 60], outputs: [{ type: "IMAGE" }] }, { id: 2, hubId: "hub-b", pos: [240, 0], size: [100, 60] }];
const rawLiteEdge = { id: 7, origin_id: 1, target_id: 2, origin_slot: 0, target_slot: 0 };
const rawLiteFingerprint = JSON.parse(helpers.minimapFingerprint({ nodes: rawLiteNodes, edges: [rawLiteEdge] }));
assert.equal(rawLiteFingerprint.edges[0].source, "1");
assert.equal(rawLiteFingerprint.edges[0].target, "2");
assert.equal(rawLiteFingerprint.edges[0].type, "IMAGE");
const frames = [];
const queue = [];
const cancelled = [];
let currentFingerprint = "one";
const scheduler = helpers.createMinimapScheduler({ requestFrame: (callback) => { queue.push(callback); return queue.length; }, cancelFrame: (handle) => cancelled.push(handle), fingerprint: () => currentFingerprint, redraw: (value) => frames.push(value) });
scheduler.start();
assert.equal(queue.length, 1);
queue.shift()();
assert.deepEqual(frames, ["one"]);
currentFingerprint = "two";
queue.shift()();
assert.deepEqual(frames, ["one", "two"]);
scheduler.start();
assert.equal(queue.length, 1);
scheduler.stop();
assert.equal(scheduler.active, false);
assert.equal(cancelled.length, 1);
""",
        )

    def test_context_menu_geometry_and_inline_controls_fail_closed(self) -> None:
        self._run_node_helpers(
            r"""
const assert = await import("node:assert/strict");
const position = helpers.contextMenuPosition({ clientX: 995, clientY: 795 }, { left: 100, top: 100, width: 900, height: 700 }, { width: 300, height: 360 });
assert.ok(position.left >= 8 && position.left <= 592);
assert.ok(position.top >= 8 && position.top <= 332);
const slider = helpers.inlineControlMetadata({ name: "opacity", kind: "number", min: 0, max: 1, step: 0.05, ui: { control: "slider", unit: "%" } });
assert.equal(slider.control, "slider");
assert.equal(helpers.normalizeInlineControlValue({ name: "opacity", kind: "number", min: 0, max: 1, ui: { control: "slider" } }, "2").value, 1);
assert.equal(helpers.normalizeInlineControlValue({ name: "choice", kind: "select", options: ["a", "b"], ui: { control: "select" } }, "c").accepted, false);
assert.equal(helpers.inlineControlMetadata({ name: "hostile", kind: "text", ui: { control: "unknown-widget" } }), null);
assert.equal(helpers.outputSocketState({ status: "running" }, "image"), "running");
assert.equal(helpers.outputSocketState({ status: "completed" }, "image"), "none");
assert.equal(helpers.outputSocketState({ outputs: { image: { artifact_id: "artifact_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" } } }, "image"), "completed");
""",
        )

    def test_dirty_state_and_persisted_run_projection_are_bounded_and_reachable(self) -> None:
        self._run_extended_node_helpers(
            r"""
const assert = await import("node:assert/strict");
const before = {
  nodes: [
    { id: "source", type: "number", data: { value: 1 } },
    { id: "middle", type: "number", data: { value: 2 } },
    { id: "sink", type: "number", data: { value: 3 } },
    { id: "other", type: "number", data: { value: 4 } },
  ],
  edges: [
    { id: "e1", source: { node: "source", port: "number" }, target: { node: "middle", port: "value" } },
    { id: "e2", source: { node: "middle", port: "number" }, target: { node: "sink", port: "value" } },
  ],
};
const after = JSON.parse(JSON.stringify(before));
after.nodes[0].data.value = 99;
assert.deepEqual(helpers.changedGraphNodeIds(before, after), ["source"]);
assert.deepEqual(helpers.downstreamDirtyNodeIds(after, ["source"]), ["middle", "sink", "source"]);
assert.ok(!helpers.downstreamDirtyNodeIds(after, ["source"]).includes("other"));

const jobId = "job_20260903_000000_abcdef12";
const artifactId = "artifact_" + "a".repeat(32);
const projection = helpers.buildPersistedRunProjection({
  job_id: jobId,
  status: "completed",
  nodes: [
    { id: "source", status: "completed", progress: 100, output: { path: "C:/private/not-persisted.png" } },
    { id: "ignored", status: "running", progress: 22 },
  ],
  provenance: [{ artifact_id: artifactId, url: "C:/private/not-persisted.png", media_type: "image/png", name: "safe.png" }],
}, ["source"]);
assert.equal(projection.version, 1);
assert.equal(projection.job_id, jobId);
assert.deepEqual(projection.nodes.map((item) => item.id), ["source"]);
assert.deepEqual(projection.artifacts.map((item) => item.id), [artifactId]);
assert.doesNotMatch(JSON.stringify(projection), /C:\\/);
assert.equal(helpers.normalizePersistedRunProjection(projection, ["source"]).job_id, jobId);
assert.equal(helpers.normalizePersistedRunProjection({ ...projection, job_id: "C:/private/job" }, ["source"]), null);
""",
        )

    def test_slider_has_inspector_range_and_editable_number_peer(self) -> None:
        self._run_extended_node_helpers(
            r"""
const assert = await import("node:assert/strict");
const node = { id: 7, properties: { opacity: 0.5 } };
const property = { name: "opacity", label: "Opacity", kind: "number", min: 0, max: 1, step: 0.05, ui: { control: "slider" } };
const markup = helpers.propertyControl(node, property);
assert.match(markup, /type="range"/);
assert.match(markup, /type="number"/);
assert.match(markup, /data-graph-property-role="range"/);
assert.match(markup, /data-graph-property-role="number"/);
assert.match(markup, /min="0"/);
assert.match(markup, /max="1"/);
assert.match(markup, /step="0\.05"/);
""",
        )

    def test_production_minimap_uses_scheduler_and_linear_node_lookup(self) -> None:
        source = STUDIO.read_text(encoding="utf-8")
        start = source.index("  currentMinimapFingerprint()")
        end = source.index("  recenterFromMinimap(", start)
        production = source[start:end]
        self.assertIn("const nodeById = new Map(nodes.map((node) => [node.id, node]));", production)
        self.assertIn("nodeById.get(link.origin_id)", production)
        self.assertIn("nodeById.get(link.target_id)", production)
        self.assertIn("this.minimapScheduler = createMinimapScheduler", production)
        self.assertNotIn("nodes.find((node) => node.id === link.origin_id)", production)
        self.assertNotIn("nodes.find((node) => node.id === link.target_id)", production)
        self.assertIn("this.minimapScheduler?.stop()", production)

    def test_registry_publishes_server_owned_control_metadata(self) -> None:
        from src.services.node_studio.registry import registry_payload

        payload = registry_payload("image")
        properties = [property_value for node in payload["nodes"] for property_value in node["properties"]]
        self.assertTrue(properties)
        for property_value in properties:
            self.assertIn("ui", property_value)
            self.assertIn("control", property_value["ui"])
            self.assertIn("label", property_value["ui"])
            self.assertIn("group", property_value["ui"])
            self.assertIn("order", property_value["ui"])
        prompt = next(item for item in properties if item["name"] == "negative_prompt")
        self.assertEqual(prompt["ui"]["control"], "prompt")
        self.assertTrue(prompt["ui"]["multiline"])
        self.assertIn(next(item for item in properties if item["name"] == "width")["ui"]["control"], {"size", "number"})

    def test_node_studio_registry_has_bounded_acceptance_impact_scope(self) -> None:
        from scripts.v8_acceptance_gate import _path_impact_scope

        self.assertEqual(_path_impact_scope("src/services/node_studio/registry.py"), "product_experience")

    def test_canvas_contract_keeps_focus_ring_and_removes_only_decorative_frame(self) -> None:
        studio = STUDIO.read_text(encoding="utf-8")
        i18n = I18N.read_text(encoding="utf-8")
        styles = STYLES.read_text(encoding="utf-8")
        self.assertIn("render_canvas_border = false", studio)
        self.assertIn("onDrawBackground", studio)
        self.assertIn("requestAnimationFrame", studio)
        self.assertIn("data-graph-context-menu", studio)
        self.assertIn("data-node-context-action", studio)
        self.assertIn('"Execution status"', i18n)
        self.assertIn('"Output states"', i18n)
        self.assertIn('"Preview unavailable in this node snapshot; no safe artifact was published."', i18n)
        self.assertIn("graph-canvas:focus-visible", styles)
        self.assertNotIn("background-image: radial-gradient", styles[styles.index(".graph-canvas-shell .lgraphcanvas"):styles.index(".graph-canvas__actions")])

    def test_picker_copy_is_i18n_bound_and_editor_teardown_is_complete(self) -> None:
        source = STUDIO.read_text(encoding="utf-8")
        for key in (
            'nodeText("Connect")',
            'nodeText("Search compatible nodes")',
            'nodeText("Rejected candidates")',
            'nodeText("Close connection picker")',
            'nodeText("Only explicitly compatible typed ports are shown.")',
        ):
            self.assertIn(key, source)
        for literal in (
            'aria-label="Close connection picker"',
            'aria-label="Search compatible nodes"',
            '>Rejected candidates:',
            '>Only explicitly compatible typed ports are shown.<',
        ):
            self.assertNotIn(literal, source)
        for marker in (
            "this.abort.abort()",
            "this.stopMinimapLoop()",
            "this.stopPoll()",
            "this.resizeObserver?.disconnect()",
            "this.liteCanvas?.stopRendering?.()",
            "this.liteCanvas?.setCanvas?.(null)",
            "this.liteCanvas?.setGraph?.(null)",
            "restorePersistedRun",
            "markRunUnavailable",
        ):
            self.assertIn(marker, source)


if __name__ == "__main__":
    unittest.main()
