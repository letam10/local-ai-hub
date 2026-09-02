"""Bounded M3 contracts for the Vision/SAM2 workspace and opaque results."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.services.api import jobs
from src.shared.schemas.vision import (
    SAM2_SELECTION_SCHEMA,
    VISION_ANNOTATION_SCHEMA,
    VisionContractError,
    build_sam2_selection,
    build_vision_annotation,
    normalize_tool_payload,
    validate_public_result_contract,
)


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = "artifact_" + "a" * 32


class M3VisionSam2WorkspaceTests(unittest.TestCase):
    def test_sam2_mode_transitions_edit_helpers_and_frame_truth(self) -> None:
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is required for M3 interaction contracts")
        script = r'''
import assert from "node:assert/strict";
import {
  boxHandleAt,
  createSam2SelectionState,
  moveNormalizedBox,
  normalizeBox,
  resizeNormalizedBox,
  selectionToPayload,
  transitionSam2Mode,
  commitSam2Selection,
} from "./src/ui/features/vision/interactive.js";

const source = "artifact_" + "a".repeat(32);
const model = createSam2SelectionState({ mode: "box", selection: { points: [{ x: .1, y: .1, label: 1 }], box: [.2, .2, .8, .8] } });
assert.deepEqual(model.selection, { points: [], box: [.2, .2, .8, .8] });
assert.equal(transitionSam2Mode(model, "points"), true);
assert.deepEqual(model.selection, { points: [], box: null });

const mixedTrack = createSam2SelectionState({ mode: "track", selection: { points: [{ x: .2, y: .2, label: 1 }], box: [.1, .1, .9, .9] } });
commitSam2Selection(mixedTrack, { points: [{ x: .3, y: .3, label: 1 }], box: [.1, .1, .9, .9] });
assert.deepEqual(mixedTrack.selection, { points: [], box: [.1, .1, .9, .9] });
const trackPayload = selectionToPayload({ ...mixedTrack, sourceArtifact: { id: source }, framePrecisionUnavailable: true, duration: 4, frameTimeSeconds: 12 });
assert.deepEqual(trackPayload.box, [.1, .1, .9, .9]);
assert.equal("points" in trackPayload, false);
assert.equal(trackPayload.frame_time_seconds, 4);
assert.equal("frame_index" in trackPayload, false);

const pointPayload = selectionToPayload({ mode: "points", selection: { points: [{ x: .2, y: .4, label: 1 }], box: [.1, .1, .9, .9] } });
assert.equal("box" in pointPayload, false);
assert.equal(pointPayload.points.length, 1);
const verifiedPayload = selectionToPayload({ mode: "track", selection: { points: [{ x: .2, y: .4, label: 1 }], box: null }, sourceArtifact: { media_metadata: { fps: 30, frame_count: 300, verified: true } }, framePrecisionUnavailable: false, frameIndex: 999, frameTimeSeconds: 9, duration: 10 });
assert.equal(verifiedPayload.frame_index, 299);
assert.equal("frame_time_seconds" in verifiedPayload, false);
const unknownMetadataPayload = selectionToPayload({ mode: "track", selection: { points: [{ x: .2, y: .4, label: 1 }], box: null }, sourceArtifact: { media_metadata: { fps: 30, frame_count: 300, verified: false, vfr: true } }, framePrecisionUnavailable: false, frameIndex: 17, frameTimeSeconds: 6, duration: 5 });
assert.equal(unknownMetadataPayload.frame_time_seconds, 5);
assert.equal("frame_index" in unknownMetadataPayload, false);

const box = [.2, .2, .6, .7];
assert.equal(boxHandleAt(box, { x: .2, y: .2 }), "nw");
assert.deepEqual(moveNormalizedBox(box, .6, -.5), [.6, 0, 1, .5]);
assert.deepEqual(resizeNormalizedBox(box, "se", { x: .9, y: .9 }), [.2, .2, .9, .9]);
assert.deepEqual(normalizeBox([.2, .2, .2, .8]), null);
'''
        result = subprocess.run([node, "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_m3_and_m4_workspace_polling_reattach_is_generation_guarded(self) -> None:
        app = (ROOT / "src" / "ui" / "app.js").read_text(encoding="utf-8")
        for workspace in ("vision", "sam2", "ocr", "whisper"):
            self.assertIn(f"{workspace}: new Set", app)
        self.assertIn("restoreWorkspaceJobReferences();", app)
        self.assertIn("resumeVisibleWorkspaceJobPollers();", app)
        self.assertIn("stopAllM3JobPollers();", app)
        self.assertIn("current.generation !== entry.generation", app)
        self.assertIn("afterRead !== entry", app)
        self.assertIn('if (M3_TERMINAL_JOB_STATUSES.has(String(job.status || "")))', app)
        self.assertIn("render({ focus: \"main\" });", app)
        self.assertIn("window.addEventListener(\"hashchange\", async () => {\n  storageScanPoller.stop();\n  stopAllM3JobPollers();", app)

    def test_public_geometry_is_normalized_and_bounded(self) -> None:
        normalized, error = normalize_tool_payload(
            "segment_from_points",
            {
                "source_artifact_id": ARTIFACT,
                "points": [{"x": 0.25, "y": 0.75, "label": 1}],
            },
        )
        self.assertIsNone(error)
        self.assertEqual(normalized["points"][0], {"x": 0.25, "y": 0.75, "label": 1, "normalized": True})

        boxed, error = normalize_tool_payload(
            "segment_from_box",
            {"source_artifact_id": ARTIFACT, "box": [0.1, 0.2, 0.8, 0.9]},
        )
        self.assertIsNone(error)
        self.assertEqual(boxed["box"], [0.1, 0.2, 0.8, 0.9])
        self.assertTrue(boxed["normalized_box"])

        for payload in (
            {"source_artifact_id": ARTIFACT, "points": [{"x": 1.1, "y": 0.5}]},
            {"source_artifact_id": ARTIFACT, "box": [0.1, 0.2, 0.8, 1.2]},
            {"source_artifact_id": ARTIFACT, "box": [0.8, 0.2, 0.1, 0.9]},
            {"source_artifact_id": ARTIFACT, "frame_index": -1, "points": [{"x": 0.5, "y": 0.5}]},
        ):
            with self.subTest(payload=payload):
                _value, error = normalize_tool_payload(
                    "track_video_object" if "frame_index" in payload else "segment_from_points" if "points" in payload else "segment_from_box",
                    payload,
                )
                self.assertIsNotNone(error)

    def test_result_contracts_are_opaque_and_have_required_shapes(self) -> None:
        annotation = build_vision_annotation(
            ARTIFACT,
            {
                "width": 512,
                "height": 256,
                "detections": [
                    {"box_normalized_cxcywh": [0.5, 0.5, 0.25, 0.5], "label": "person", "confidence": 0.91, "path": r"C:\secret\box.json"},
                    {"box_normalized_cxcywh": [2, 0.5, 0.2, 0.2], "label": "bad", "confidence": 0.5},
                ],
            },
        )
        self.assertEqual(annotation["schema_version"], VISION_ANNOTATION_SCHEMA)
        self.assertEqual(annotation["detections"], [{"normalized_box": [0.375, 0.25, 0.625, 0.75], "label": "person", "confidence": 0.91}])
        self.assertNotIn("path", json.dumps(annotation))

        selection = build_sam2_selection(
            ARTIFACT,
            frame_index=7,
            points=[{"x": 0.1, "y": 0.2, "label": 1}, {"x": 0.8, "y": 0.9, "label": 0}],
            box=[0.05, 0.1, 0.95, 0.9],
        )
        self.assertEqual(selection["schema_version"], SAM2_SELECTION_SCHEMA)
        self.assertEqual(selection["positive_points"], [{"x": 0.1, "y": 0.2}])
        self.assertEqual(selection["negative_points"], [{"x": 0.8, "y": 0.9}])
        self.assertEqual(selection["frame_index"], 7)
        self.assertNotIn("path", json.dumps(selection))

        with self.assertRaises(VisionContractError):
            validate_public_result_contract(
                {"schema_version": SAM2_SELECTION_SCHEMA, "source_artifact_id": ARTIFACT, "positive_points": [{"x": 2, "y": 0}], "negative_points": [], "normalized_box": None},
                key="selection",
            )

    def test_job_publication_attaches_only_published_opaque_artifact_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "mask.png"
            overlay = Path(temporary) / "overlay.png"
            output.write_bytes(b"mask")
            overlay.write_bytes(b"overlay")
            result = {
                "status": "completed",
                "operation": "segment_from_points",
                "outputs": [str(output), str(overlay)],
                "selection": build_sam2_selection(
                    ARTIFACT,
                    frame_index=0,
                    points=[{"x": 0.5, "y": 0.5, "label": 1}],
                ),
            }
            published = [
                {"id": "artifact_" + "b" * 32, "media_type": "image/png", "name": "mask.png", "provenance": {}},
                {"id": "artifact_" + "c" * 32, "media_type": "image/png", "name": "overlay.png", "provenance": {}},
            ]
            def register(_paths, *, provenance):
                return [{**item, "provenance": provenance} for item in published]

            with patch.object(jobs.artifact_store, "register_worker_outputs", side_effect=register):
                safe, error = jobs._publish_result(result, {"id": "job_contract", "tool": "segment_from_points"})
        self.assertIsNone(error)
        self.assertEqual(safe["selection"]["mask_artifact_ids"], ["artifact_" + "b" * 32])
        self.assertEqual(safe["selection"]["overlay_artifact_ids"], ["artifact_" + "c" * 32])
        self.assertNotIn(str(output), json.dumps(safe))
        self.assertNotIn("path", json.dumps(safe))

    def test_rendered_workspaces_use_button_only_file_control_and_inline_contracts(self) -> None:
        script = r"""
        import { renderPage } from './src/ui/pages.js';
        const base = {components:[{id:'sam2',component_status:'partial'},{id:'omniparser',component_status:'partial'},{id:'rfdetr',component_status:'partial'},{id:'groundingdino',component_status:'partial'}],tools:[]};
        base.tools = ['parse_screen','detect_objects','ground_objects','segment_from_points','segment_from_box','segment_from_text','track_video_object'].map(name => ({name, tool_status:'partial', reason:'No smoke', action:'Configure'}));
        for (const [route, required] of [['vision',['OmniParser','RF-DETR','Grounding DINO','vision.annotation.v1']], ['sam2',['Chọn điểm','Chọn hộp','Chọn bằng mô tả','Theo dõi video','Thêm vùng','Loại vùng','sam2.selection.v1']]]) {
          const html = renderPage(route, {...base, m3:{vision:{activeTool:'omniparser',thresholds:{}},sam2:{mode:'points',intent:'positive',selection:{points:[],box:null},resultTab:'original',maskOpacity:.68}}});
          for (const item of required) if (!html.includes(item)) throw new Error(`${route} missing ${item}`);
          if (html.includes('Choose File') || html.includes('No file chosen')) throw new Error('native picker copy leaked');
          if (!html.includes('data-file-picker-button') || !html.includes('file-picker__input')) throw new Error('button-only picker missing');
          if (!html.includes('m3-workspace__inputs') || !html.includes('m3-workspace__canvas') || !html.includes('m3-workspace__results')) throw new Error('three-zone workspace missing');
          const forms = [...html.matchAll(/<form[^>]*data-m3-job-form[\s\S]*?<\/form>/g)].map(match => match[0]);
          if (forms.some(form => form.includes('type="file"'))) throw new Error('file input is nested in job form');
        }
        """
        result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_sam2_result_tabs_project_published_media_and_escape_names(self) -> None:
        script = r"""
        import { renderPage } from './src/ui/pages.js';
        const maskId = 'artifact_' + 'a'.repeat(32);
        const overlayId = 'artifact_' + 'b'.repeat(32);
        const result = {
          artifacts: [
            { id: maskId, url: `/api/artifacts/${maskId}`, media_type: 'image/png', name: 'mask" onerror="bad' },
            { id: overlayId, url: `/api/artifacts/${overlayId}`, media_type: 'image/png', name: 'overlay.png' },
          ],
          selection: { mask_artifact_ids: [maskId], overlay_artifact_ids: [overlayId] },
        };
        const base = {
          components: [{ id: 'sam2', component_status: 'partial' }],
          tools: ['segment_from_points','segment_from_box','segment_from_text','track_video_object'].map(name => ({ name, tool_status: 'partial', reason: 'No smoke', action: 'Configure' })),
          m3: {
            vision: { activeTool: 'omniparser', thresholds: {} },
            sam2: { mode: 'points', intent: 'positive', selection: { points: [], box: null }, resultTab: 'mask', maskOpacity: .68, job: { result } },
          },
        };
        const html = renderPage('sam2', base);
        if (!html.includes(`/api/artifacts/${maskId}`) || !html.includes(`/api/artifacts/${overlayId}`)) throw new Error('published result artifact media missing');
        if (html.includes('alt="mask" onerror="bad"')) throw new Error('artifact name injection was not escaped');
        console.log('ok');
        """
        result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        css = (ROOT / "src/ui/styles.css").read_text(encoding="utf-8")
        self.assertIn(".m3-media-layer > img[hidden]", css)
        self.assertIn(".m3-media-layer > video[hidden]", css)


if __name__ == "__main__":
    unittest.main()
