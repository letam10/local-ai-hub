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
        self.assertEqual(payload["operation_scope"]["operations"], ["video_grade", "logo_overlay", "encode"])
        self.assertFalse(payload["operation_scope"]["evidence_verified"])
        self.assertTrue(all(
            item["status"] == "partial"
            for item in payload["nodes"]
            if item["type"] in {"video_grade", "logo_overlay", "encode"}
        ))

    def test_registry_promotes_only_exact_scoped_nodes_from_completed_evidence(self) -> None:
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
        scope = {
            "schema_version": "runtime-operation-scope.v1",
            "subject": "media_overlay_cpu_acceptance",
            "status": "operational",
            "execution": "completed",
            "evidence_verified": True,
            "operations": ["video_grade", "logo_overlay", "encode"],
            "available_operations": ["video_grade", "logo_overlay", "encode"],
            "operation_status": {"video_grade": "operational", "logo_overlay": "operational", "encode": "operational"},
        }
        with (
            patch.object(adapter, "cached_encoder_capabilities", return_value=snapshot),
            patch.object(registry, "runtime_evidence_operation_scope", return_value=scope),
        ):
            payload = registry.registry_payload("media")
        nodes = {item["type"]: item for item in payload["nodes"]}
        for node_type in ("video_grade", "logo_overlay", "encode"):
            self.assertEqual(nodes[node_type]["status"], "operational")
        self.assertEqual(nodes["audio_loudness"]["status"], "partial")
        self.assertEqual(payload["operation_scope"]["available_operations"], ["video_grade", "logo_overlay", "encode"])

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

    def test_closed_media_public_boundary_rejects_paths_and_unsafe_fields_before_execution(self) -> None:
        from src.modules.media_editor.backend import adapter

        artifact_id = "artifact_" + "a" * 32
        raw_path = str(Path(__file__).resolve())
        unsafe_fields = ("path", "secondary_path", "input_paths", "callable", "manifest", "command", "filter", "executable", "font", "secret", "unknown_control")
        operations = {
            "video_grade": {"source_artifact_id": artifact_id},
            "logo_overlay": {"source_artifact_id": artifact_id, "overlay_artifact_id": artifact_id},
            "audio_loudness": {"source_artifact_id": artifact_id},
        }
        for operation, base_payload in operations.items():
            for field in unsafe_fields:
                payload = {"operation": operation, **base_payload, field: "client-controlled-secret" if field != "path" else raw_path}
                with (
                    patch.object(adapter, "_paths", side_effect=AssertionError("closed validation must precede _paths")),
                    patch.object(adapter, "run_command", side_effect=AssertionError("closed validation must precede run_command")),
                    patch.object(adapter, "resolve", side_effect=AssertionError("closed validation must precede artifact resolution")),
                    patch.object(adapter, "describe", side_effect=AssertionError("closed validation must precede artifact description")),
                ):
                    result = adapter.run_operation(payload)
                self.assertEqual(result["status"], "error", (operation, field))
                self.assertEqual(result["error"], adapter._CLOSED_MEDIA_PAYLOAD_ERROR, (operation, field))
                self.assertNotIn(raw_path, json.dumps(result, ensure_ascii=False))
                self.assertNotIn("client-controlled-secret", json.dumps(result, ensure_ascii=False))

    def test_closed_media_artifacts_resolve_server_side_and_build_commands_without_execution(self) -> None:
        from src.modules.media_editor.backend import adapter

        with TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "source.mp4"
            audio = root / "source.wav"
            overlay = root / "overlay.png"
            ffmpeg = root / "ffmpeg.exe"
            for path in (video, audio, overlay, ffmpeg):
                path.write_bytes(b"fixture")
            video_id = "artifact_" + "a" * 32
            audio_id = "artifact_" + "b" * 32
            overlay_id = "artifact_" + "c" * 32
            paths = {video_id: video, audio_id: audio, overlay_id: overlay}
            metadata = {
                video_id: {"media_type": "video/mp4"},
                audio_id: {"media_type": "audio/wav"},
                overlay_id: {"media_type": "image/png"},
            }

            def resolve_mock(artifact: str) -> Path | None:
                return paths.get(artifact)

            def describe_mock(artifact: str) -> dict[str, str] | None:
                return metadata.get(artifact)

            cases = (
                ("video_grade", {"operation": "video_grade", "source_artifact_id": video_id, "brightness": 0.2}, video, "eq=brightness=0.2"),
                ("logo_overlay", {"operation": "logo_overlay", "source_artifact_id": video_id, "overlay_artifact_id": overlay_id, "position": "bottom_right", "opacity": 0.5}, video, "colorchannelmixer"),
                ("audio_loudness", {"operation": "audio_loudness", "source_artifact_id": audio_id, "target_lufs": -16}, audio, "loudnorm=I=-16"),
            )
            with (
                patch.object(adapter, "resolve", side_effect=resolve_mock) as resolve_call,
                patch.object(adapter, "describe", side_effect=describe_mock),
                patch.object(adapter, "_paths", return_value=(ffmpeg, None)),
                patch.object(adapter, "run_command") as run_command,
            ):
                for operation, payload, expected_source, expected_command in cases:
                    source, resolved_overlay = adapter._resolve_closed_media_sources(payload, operation)
                    command = adapter._command(payload, source, root / f"{operation}.out", resolved_overlay=resolved_overlay)
                    self.assertEqual(source, expected_source)
                    self.assertIn(expected_command, " ".join(command or []))
                run_command.assert_not_called()

            resolved_ids = [call.args[0] for call in resolve_call.call_args_list]
            self.assertTrue(resolved_ids)
            self.assertTrue(set(resolved_ids).issubset(paths))
            self.assertNotIn(str(video), resolved_ids)
            self.assertNotIn(str(audio), resolved_ids)
            self.assertNotIn(str(overlay), resolved_ids)

    def test_closed_engine_runners_send_artifact_ids_not_filesystem_paths(self) -> None:
        from src.services.node_studio import engine

        video_id = "artifact_" + "a" * 32
        image_id = "artifact_" + "b" * 32
        audio_id = "artifact_" + "c" * 32
        video = engine.ArtifactValue(video_id, Path("C:/private/video.mp4"), {"media_type": "video/mp4"})
        image = engine.ArtifactValue(image_id, Path("C:/private/logo.png"), {"media_type": "image/png"})
        audio = engine.ArtifactValue(audio_id, Path("C:/private/audio.wav"), {"media_type": "audio/wav"})
        payloads: list[dict[str, object]] = []

        def execute_tool(_tool: str, payload: dict[str, object], _context: object) -> dict[str, object]:
            payloads.append(payload)
            return {"status": "completed"}

        with patch.object(engine, "_media_result", return_value={}):
            engine._run_video_grade({}, {"video": video}, None, execute_tool)
            engine._run_logo_overlay({}, {"video": video, "image": image}, None, execute_tool)
            engine._run_audio_loudness({}, {"audio": audio}, None, execute_tool)

        self.assertEqual([payload["operation"] for payload in payloads], ["video_grade", "logo_overlay", "audio_loudness"])
        self.assertEqual(payloads[0]["source_artifact_id"], video_id)
        self.assertEqual(payloads[1]["source_artifact_id"], video_id)
        self.assertEqual(payloads[1]["overlay_artifact_id"], image_id)
        self.assertEqual(payloads[2]["source_artifact_id"], audio_id)
        for payload in payloads:
            self.assertNotIn("path", payload)
            self.assertNotIn("secondary_path", payload)

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
