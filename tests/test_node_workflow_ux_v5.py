from __future__ import annotations

import json
import math
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class NodeWorkflowUxV5Tests(unittest.TestCase):
    def test_litegraph_adapter_has_one_editor_and_v5_focus_contract(self) -> None:
        source = (ROOT / "src" / "ui" / "node_studio.js").read_text(encoding="utf-8")
        self.assertEqual(source.count("new globalThis.LiteGraph.LGraphCanvas"), 1)
        self.assertEqual(source.count("new globalThis.LiteGraph.LGraph()"), 1)
        for marker in (
            "multi_select = false",
            "onMouse =",
            "bindConnectionPickerHook",
            "handleSelectionShortcut",
            "isTextControl",
            "isCanvasActive",
            "ArrowUp",
            "data-node-palette",
            "data-node-inspector",
            "NODE_UI_STATE_PREFIX",
            "data-graph-guide",
            'preload="metadata"',
        ):
            self.assertIn(marker, source)
        self.assertNotIn("LiteGraph.js", source.split("const LOCAL_PREFIX", 1)[-1])

    def test_pure_picker_helpers_reject_mismatch_and_choose_only_one_candidate(self) -> None:
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is required for the pure adapter helper check")
        script = r"""
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const source = readFileSync("src/ui/node_studio.js", "utf8");
const start = source.indexOf("const NODE_UI_STATE_VERSION");
const end = source.indexOf("function readWorkflowIndex", start);
const moduleSource = "const LOCAL_PREFIX = 'test';" + String.fromCharCode(10) + source.slice(start, end);
const moduleUrl = "data:text/javascript;base64," + Buffer.from(moduleSource).toString("base64");
const helpers = await import(moduleUrl);

const sink = {
  type: "sink",
  title: "Video Sink",
  description: "sink",
  inputs: [{ name: "video", label: "Video", type: "VIDEO", multi: false }],
  outputs: [],
};
const other = {
  type: "other",
  title: "Other Sink",
  description: "other",
  inputs: [{ name: "video", label: "Video", type: "VIDEO", multi: false }],
  outputs: [],
};
const sourceDefinition = {
  type: "source",
  title: "Image Source",
  description: "source",
  inputs: [],
  outputs: [{ name: "image", label: "Image", type: "IMAGE", multi: false }],
};

const one = helpers.getConnectionPortCandidates(new Map([["sink", sink]]), { direction: "input", type: "VIDEO" });
assert.equal(one.compatible.length, 1);
assert.equal(helpers.chooseConnectionCandidate(one).definition.type, "sink");
const many = helpers.getConnectionPortCandidates(new Map([["sink", sink], ["other", other]]), { direction: "input", type: "VIDEO" });
assert.equal(many.compatible.length, 2);
assert.equal(helpers.chooseConnectionCandidate(many), null);
const occupied = helpers.getConnectionPortCandidates(new Map([["sink", sink]]), { direction: "input", type: "VIDEO", occupied: ["sink:video"] });
assert.equal(occupied.compatible.length, 0);
assert.match(occupied.rejected[0].reason, /already connected/i);
const mismatch = helpers.connectionPortDecision({ type: "IMAGE" }, { type: "VIDEO" });
assert.equal(mismatch.compatible, false);
assert.match(mismatch.reason, /mismatch/i);
const outputCandidates = helpers.getConnectionPortCandidates(new Map([["source", sourceDefinition]]), { direction: "output", type: "VIDEO" });
assert.equal(outputCandidates.compatible.length, 0);
assert.match(outputCandidates.rejected[0].reason, /mismatch/i);
const normalized = helpers.normalizeNodePanelState({ version: 1, palette: "collapsed", pickerSearch: "typed" });
assert.equal(normalized.palette, "collapsed");
assert.equal(normalized.pickerSearch, "typed");
"""
        result = subprocess.run([node, "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_registry_open_uses_truthful_cached_encoder_snapshot_without_probe(self) -> None:
        from src.modules.media_editor.backend import adapter
        from src.services.node_studio import registry

        snapshot = {
            "status": "not_run",
            "execution": "not_run",
            "available": False,
            "reason": "explicit probe not authorized",
            "encoders": [],
            "containers": ["mp4"],
            "audio_encoders": [],
        }
        with (
            patch.object(adapter, "run_hidden", side_effect=AssertionError("registry must not run executables")),
            patch.object(adapter, "encoder_capabilities", side_effect=AssertionError("registry must not probe encoders")),
            patch.object(adapter, "cached_encoder_capabilities", return_value=snapshot),
        ):
            payload = registry.registry_payload("media")
        self.assertEqual(payload["encoder_capabilities"]["status"], "not_run")
        self.assertEqual(payload["encoder_capabilities"]["execution"], "not_run")
        self.assertFalse(payload["encoder_capabilities"]["available"])
        self.assertEqual(payload["encoder_capabilities"]["encoders"], [])

    def test_new_video_contracts_are_closed_and_status_truthful(self) -> None:
        from src.services.node_studio.registry import get_definition, validate_node_data

        expected = {
            "video_grade": ("partial", {"brightness", "contrast", "saturation", "gamma", "denoise", "sharpen"}),
            "logo_overlay": ("partial", {"position", "opacity"}),
            "audio_loudness": ("partial", {"target_lufs", "true_peak", "gain_db"}),
        }
        for node_type, (status, properties) in expected.items():
            definition = get_definition(node_type)
            self.assertIsNotNone(definition)
            self.assertEqual(definition.status, status)
            self.assertEqual({item["name"] for item in definition.properties}, properties)
        self.assertEqual(get_definition("text_overlay").status, "unavailable")
        self.assertEqual(get_definition("probe_media").inputs[0].type, "VIDEO")
        self.assertEqual(get_definition("probe_audio").inputs[0].type, "AUDIO")
        logo_names = {item["name"] for item in get_definition("logo_overlay").properties}
        self.assertNotIn("path", logo_names)
        self.assertNotIn("font", logo_names)

        unsafe_value = "".join(("C:", "/", "private", "/", "opaque", "-", "id"))
        errors = validate_node_data(
            {
                "nodes": [
                    {"id": "grade", "type": "video_grade", "data": {"brightness": math.nan, "filter": unsafe_value}},
                    {"id": "logo", "type": "logo_overlay", "data": {"opacity": 4, "font_path": unsafe_value}},
                    {"id": "image", "type": "load_image", "data": {"asset_id": unsafe_value}},
                ]
            }
        )
        encoded = json.dumps(errors, ensure_ascii=False)
        self.assertNotIn(unsafe_value, encoded)
        self.assertIn("invalid_number", {item["code"] for item in errors})
        self.assertIn("unsafe_node_property", {item["code"] for item in errors})
        self.assertIn("number_above_maximum", {item["code"] for item in errors})
        self.assertIn("invalid_asset_id", {item["code"] for item in errors})

    def test_safe_media_commands_are_constructed_without_executing_ffmpeg(self) -> None:
        from src.modules.media_editor.backend import adapter

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            overlay = root / "overlay.png"
            source.write_bytes(b"source")
            overlay.write_bytes(b"image")
            target = root / "output.mp4"
            with patch.object(adapter, "_paths", return_value=(source, None)), patch.object(adapter, "resolve", return_value=overlay):
                grade = adapter._command(
                    {
                        "operation": "video_grade",
                        "brightness": 0.2,
                        "contrast": 1.2,
                        "saturation": 0.8,
                        "gamma": 1.1,
                        "denoise": "light",
                        "sharpen": "off",
                    },
                    source,
                    target,
                )
                overlay_command = adapter._command(
                    {"operation": "logo_overlay", "overlay_artifact_id": "artifact_" + "a" * 32, "position": "bottom_right", "opacity": 0.5},
                    source,
                    target,
                )
                audio = adapter._command(
                    {"operation": "audio_loudness", "target_lufs": -16, "true_peak": -1.5, "gain_db": 2},
                    source,
                    root / "output.m4a",
                )
                with self.assertRaises(ValueError):
                    adapter._command({"operation": "video_grade", "filter": "drawtext=text=secret"}, source, target)
                with self.assertRaises(ValueError):
                    adapter._command({"operation": "logo_overlay", "overlay_path": str(overlay)}, source, target)
            self.assertIn("hqdn3d", " ".join(grade))
            self.assertIn("eq=brightness=0.2", " ".join(grade))
            self.assertIn("overlay=x=W-w-20:y=H-h-20", " ".join(overlay_command))
            self.assertIn("colorchannelmixer", " ".join(overlay_command))
            self.assertIn("loudnorm=I=-16", " ".join(audio))
            self.assertIn("volume=2dB", " ".join(audio))

    def test_video_templates_have_metadata_and_preview_save_export_order(self) -> None:
        from src.services.node_studio.registry import get_definition
        from src.services.node_studio.schema import validate_graph

        paths = [
            ROOT / "workflows" / "media_encode.json",
            ROOT / "workflows" / "video_creative_pipeline.json",
            ROOT / "workflows" / "video_generation_unavailable.json",
            ROOT / "workflows" / "animesr_pipeline.json",
        ]
        for path in paths:
            graph = json.loads(path.read_text(encoding="utf-8"))
            metadata = graph["metadata"]
            for key in ("title", "purpose", "category", "node_count", "capability", "status", "output_intent"):
                self.assertIn(key, metadata, path.name)
            self.assertEqual(metadata["category"], "video")
            self.assertEqual(metadata["node_count"], len(graph["nodes"]))
            self.assertIn(metadata["status"], {"partial", "unavailable"})
            self.assertTrue(validate_graph(graph)["valid"], path.name)
            nodes = {node["id"]: node for node in graph["nodes"]}
            self.assertIn("preview", nodes)
            self.assertIn("save", nodes)
            self.assertIn("export", nodes)
            edges = {(edge["source"]["node"], edge["target"]["node"]) for edge in graph["edges"]}
            self.assertIn(("preview", "save"), edges)
            self.assertIn(("save", "export"), edges)

        generated = json.loads((ROOT / "workflows" / "video_generation_unavailable.json").read_text(encoding="utf-8"))
        self.assertEqual(next(node for node in generated["nodes"] if node["type"] == "video_generate")["type"], "video_generate")
        self.assertEqual(get_definition("video_generate").status, "unavailable")

    def test_ui_static_contract_has_no_raw_artifact_preview_or_second_engine(self) -> None:
        source = (ROOT / "src" / "ui" / "node_studio.js").read_text(encoding="utf-8")
        registry = (ROOT / "src" / "services" / "node_studio" / "registry.py").read_text(encoding="utf-8")
        self.assertIn("safeArtifactProjection", source)
        self.assertIn("SAFE_ARTIFACT_URL", source)
        self.assertIn("data-preview-artifact", source)
        self.assertNotIn("artifact.path", source)
        self.assertNotIn("run_hidden", registry)
        self.assertNotIn("import encoder_capabilities", registry)
        self.assertEqual(source.count("new globalThis.LiteGraph.LGraphCanvas"), 1)


if __name__ == "__main__":
    unittest.main()
