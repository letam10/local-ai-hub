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


if __name__ == "__main__":
    unittest.main()
