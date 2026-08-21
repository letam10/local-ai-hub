from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STUDIO = ROOT / "src" / "ui" / "features" / "node_studio" / "studio.js"
STYLES = ROOT / "src" / "ui" / "styles.css"


class NodeWorkflowOutputPreviewUxTests(unittest.TestCase):
    def _run_node(self, body: str) -> None:
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is required for the pure output-preview contract")
        result = subprocess.run(
            [node, "--input-type=module", "-e", body],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_collector_keeps_later_safe_artifacts_and_deduplicates_provenance(self) -> None:
        self._run_node(
            r"""
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const source = readFileSync("src/ui/features/node_studio/studio.js", "utf8");
const start = source.indexOf("const NODE_UI_STATE_VERSION");
const end = source.indexOf("function graphFingerprint", start);
const escapeHtml = (value) => String(value).replace(/[&<>\"']/g, (item) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[item]));
const moduleSource = "const LOCAL_PREFIX = 'test';\nconst escapeHtml = " + escapeHtml.toString() + ";\n" + source.slice(start, end);
const helpers = await import("data:text/javascript;base64," + Buffer.from(moduleSource).toString("base64"));

const artifact = (suffix, type, name, size) => ({
  id: `artifact_${suffix.repeat(32)}`,
  url: `/api/artifacts/artifact_${suffix.repeat(32)}`,
  media_type: type,
  name,
  size_bytes: size,
});
const image = artifact("a", "image/png", "image.png", 10);
const video = artifact("b", "video/mp4", "video.mp4", 20);
const audio = artifact("c", "audio/wav", "audio.wav", 30);
const metadata = artifact("d", "application/json", "result.json", 40);
const mask = { ...artifact("e", "image/mask", "mask.png", 50), mask: true };
const sensitiveWord = ["sec", "ret"].join("");
const privatePrefix = "C:\\private\\";
const invalidFirst = { id: "artifact_bad", url: privatePrefix + sensitiveWord + ".mp4", name: ["Bearer ", sensitiveWord, ": hidden"].join("") };
const malformed = { ...artifact("f", "application/json", "safe.json", 60), name: { hostile: "[object Object]" } };
const result = helpers.collectArtifactProjections([
  { first: invalidFirst, nested: [image, { duplicate: image }, video, audio, metadata, malformed] },
  [{ artifact_id: mask.id, url: mask.url, media_type: mask.media_type, name: mask.name, size_bytes: mask.size_bytes, mask: true }, image],
]);
assert.deepEqual(result.items.map((item) => item.id), [image.id, video.id, audio.id, metadata.id, mask.id]);
assert.equal(result.truncated, false);
assert.equal(new Set(result.items.map((item) => item.id)).size, result.items.length);
const hostilePattern = new RegExp([privatePrefix.replace(/\\/g, "\\\\"), "Bearer " + sensitiveWord + ":", "\\[object Object\\]"].join("|"), "i");
assert.doesNotMatch(JSON.stringify(result), hostilePattern);
assert.equal(helpers.safeArtifactProjection({ ...image, name: { bad: true } }).safe, false);
assert.equal(helpers.safeArtifactProjection({ ...image, media_type: { bad: true } }).safe, false);
const markup = helpers.renderArtifactPreviewMarkup(result, { status: "completed" });
assert.equal((markup.match(/data-preview-artifact=/g) || []).length, 5);
assert.match(markup, /image\.png/);
assert.match(markup, /video\.mp4/);
assert.match(markup, /audio\.wav/);
assert.match(markup, /result\.json/);
assert.match(markup, /Mask · image\/mask/);
assert.doesNotMatch(markup, hostilePattern);
""",
        )

    def test_collector_is_bounded_deterministic_and_invalid_first_does_not_hide_later_output(self) -> None:
        self._run_node(
            r"""
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const source = readFileSync("src/ui/features/node_studio/studio.js", "utf8");
const start = source.indexOf("const NODE_UI_STATE_VERSION");
const end = source.indexOf("function graphFingerprint", start);
const escapeHtml = (value) => String(value).replace(/[&<>\"']/g, (item) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[item]));
const moduleSource = "const LOCAL_PREFIX = 'test';\nconst escapeHtml = " + escapeHtml.toString() + ";\n" + source.slice(start, end);
const helpers = await import("data:text/javascript;base64," + Buffer.from(moduleSource).toString("base64"));
const make = (index) => {
  const id = `artifact_${index.toString(16).padStart(32, "0")}`;
  return { id, url: `/api/artifacts/${id}`, media_type: "application/octet-stream", name: `item-${index}`, size_bytes: index };
};
const values = Array.from({ length: 80 }, (_, index) => make(index));
const one = helpers.collectArtifactProjections([{ invalid: { id: "artifact_bad", url: "/api/artifacts/artifact_bad", name: "bad" }, values }]);
const two = helpers.collectArtifactProjections([{ invalid: { id: "artifact_bad", url: "/api/artifacts/artifact_bad", name: "bad" }, values }]);
assert.equal(one.items.length, 32);
assert.equal(one.truncated, true);
assert.deepEqual(one, two);
assert.equal(one.items[0].id, values[0].id);
assert.equal(one.items.at(-1).id, values[31].id);
""",
        )

    def test_preview_states_are_fixed_and_truthful_without_claiming_artifact_existence(self) -> None:
        self._run_node(
            r"""
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const source = readFileSync("src/ui/features/node_studio/studio.js", "utf8");
const start = source.indexOf("const NODE_UI_STATE_VERSION");
const end = source.indexOf("function graphFingerprint", start);
const escapeHtml = (value) => String(value).replace(/[&<>\"']/g, (item) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[item]));
const moduleSource = "const LOCAL_PREFIX = 'test';\nconst escapeHtml = " + escapeHtml.toString() + ";\n" + source.slice(start, end);
const helpers = await import("data:text/javascript;base64," + Buffer.from(moduleSource).toString("base64"));
for (const [status, expected] of [["completed", "completed"], ["partial", "partial"], ["failed", "failed"], ["unavailable", "unavailable"], ["not_run", "not_run"], ["running", "not_run"]]) {
  const markup = helpers.renderArtifactPreviewMarkup({ items: [], truncated: false }, { status });
  assert.match(markup, new RegExp(`data-artifact-state=\"${expected}\"`));
  assert.match(markup, /no safe artifact was published/i);
  assert.doesNotMatch(markup, /data-preview-artifact=/);
}
const id = "artifact_" + "a".repeat(32);
const markup = helpers.renderArtifactPreviewMarkup({ items: [{ safe: true, id, url: `/api/artifacts/${id}`, name: "data.json", mediaType: "application/json", sizeBytes: null, kind: "metadata", mask: false }], truncated: false }, { status: "completed" });
assert.match(markup, /data-preview-artifact=/);
assert.match(markup, /Size<\/dt><dd>unavailable/);
""",
        )

    def test_source_and_styles_keep_single_editor_safe_transport_and_responsive_preview_rules(self) -> None:
        source = STUDIO.read_text(encoding="utf-8")
        styles = STYLES.read_text(encoding="utf-8")
        self.assertNotIn("firstArtifact", source)
        for marker in (
            "collectArtifactProjections",
            "renderArtifactPreviewMarkup",
            "this.runProvenance.filter",
            "data-preview-artifact",
            'preload="metadata"',
            "MAX_PREVIEW_ARTIFACTS",
        ):
            self.assertIn(marker, source)
        self.assertNotIn("fetch(", source)
        self.assertNotIn("[object Object]", source)
        for marker in (
            ".graph-preview-list",
            ".graph-preview-card",
            ".graph-preview-meta",
            ".graph-preview-truncated",
            "min-width: 0",
            "@media (max-width: 980px)",
            "@media (max-width: 760px)",
        ):
            self.assertIn(marker, styles)


if __name__ == "__main__":
    unittest.main()
