from __future__ import annotations

import json
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class _Context:
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self.cancelled = False
        self.progress_updates: list[tuple[int, str | None]] = []

    def progress(self, value: int, message: str | None = None) -> None:
        self.progress_updates.append((value, message))


class NodeStudioSchemaTests(unittest.TestCase):
    def test_default_workflow_presets_are_trackable_and_valid(self) -> None:
        from src.services.node_studio.schema import validate_graph

        for path in sorted((ROOT / "workflows").glob("*.json")):
            graph = json.loads(path.read_text(encoding="utf-8"))
            validation = validate_graph(graph)
            self.assertTrue(validation["valid"], f"{path.name}: {validation['errors']}")
            self.assertEqual(graph["schema_version"], 1)

    def test_validator_rejects_typed_socket_mismatch_and_cycles(self) -> None:
        from src.services.node_studio.schema import validate_graph

        mismatch = {
            "schema_version": 1,
            "nodes": [
                {"id": "number", "type": "number", "data": {"value": 1}},
                {"id": "flux", "type": "flux_generate", "data": {"width": 512, "height": 512, "steps": 4, "seed": 1, "negative_prompt": ""}},
            ],
            "edges": [{"id": "wrong", "source": {"node": "number", "port": "number"}, "target": {"node": "flux", "port": "prompt"}}],
        }
        self.assertIn("socket_type_mismatch", {item["code"] for item in validate_graph(mismatch)["errors"]})

        cycle = {
            "schema_version": 1,
            "nodes": [{"id": "compare", "type": "image_compare", "data": {}}],
            "edges": [
                {"id": "a", "source": {"node": "compare", "port": "a"}, "target": {"node": "compare", "port": "a"}},
                {"id": "b", "source": {"node": "compare", "port": "b"}, "target": {"node": "compare", "port": "b"}},
            ],
        }
        self.assertIn("cycle_detected", {item["code"] for item in validate_graph(cycle)["errors"]})

    def test_dirty_propagation_marks_only_changed_node_and_downstream(self) -> None:
        from src.services.node_studio.schema import downstream_nodes

        graph = {
            "schema_version": 1,
            "nodes": [
                {"id": "number", "type": "number", "data": {"value": 1}},
                {"id": "flux", "type": "flux_generate", "data": {"width": 512, "height": 512, "steps": 4, "seed": 1, "negative_prompt": ""}},
                {"id": "preview", "type": "preview_image", "data": {}},
            ],
            "edges": [
                {"id": "width", "source": {"node": "number", "port": "number"}, "target": {"node": "flux", "port": "width"}},
                {"id": "image", "source": {"node": "flux", "port": "image"}, "target": {"node": "preview", "port": "image"}},
            ],
        }
        result = downstream_nodes(graph, ["number"])
        self.assertTrue(result["valid"])
        self.assertEqual(result["dirty_nodes"], ["flux", "number", "preview"])

    def test_graph_rejects_raw_path_in_an_asset_property(self) -> None:
        from src.services.node_studio.schema import validate_graph

        graph = {"schema_version": 1, "nodes": [{"id": "input", "type": "load_image", "data": {"asset_id": r"D:\\private\image.png"}}], "edges": []}
        self.assertIn("invalid_asset_id", {item["code"] for item in validate_graph(graph)["errors"]})


class NodeStudioExecutionTests(unittest.TestCase):
    def test_content_hash_cache_reuses_a_valid_upstream_output(self) -> None:
        from src.services.node_studio.engine import execute_graph, node_cache

        node_cache.clear()
        graph = {
            "schema_version": 1,
            "id": "number-cache",
            "title": "Number cache",
            "scope": "image",
            "nodes": [{"id": "number", "type": "number", "data": {"value": 7}}],
            "edges": [],
        }
        first = execute_graph(graph, _Context("job_graph_one"), lambda *_args: {"status": "error"})
        second = execute_graph(graph, _Context("job_graph_two"), lambda *_args: {"status": "error"})
        self.assertEqual(first["status"], "completed")
        self.assertFalse(first["nodes"][0]["cache_hit"])
        self.assertTrue(second["nodes"][0]["cache_hit"])

    def test_encode_commands_accept_cbr_and_vbr_numeric_properties(self) -> None:
        from src.modules.media_editor.backend import adapter

        encoder = {
            "id": "libx264",
            "rate_controls": ["quality", "vbr", "cbr"],
            "pixel_formats": ["yuv420p"],
            "preset_supported": True,
        }
        capabilities = {"audio_encoders": []}
        base = {"preset": "ultrafast", "pixel_format": "yuv420p", "bitrate_kbps": "300", "buffer_kbps": "600"}
        with patch.object(adapter, "_paths", return_value=(Path(__file__), None)):
            cbr = adapter._encode_base(Path("input.mp4"), Path("output.mp4"), {**base, "rate_control": "cbr"}, encoder, capabilities, include_audio=False)
            vbr = adapter._encode_base(Path("input.mp4"), Path("output.mp4"), {**base, "rate_control": "vbr", "max_bitrate_kbps": "360"}, encoder, capabilities, include_audio=False)
        self.assertIn("300k", cbr)
        self.assertIn("-minrate", cbr)
        self.assertIn("-maxrate", cbr)
        self.assertIn("360k", vbr)

    def test_graph_honors_cancellation_before_running_a_node(self) -> None:
        from src.services.node_studio.engine import execute_graph

        context = _Context("job_graph_cancel")
        context.cancelled = True
        result = execute_graph(
            {
                "schema_version": 1,
                "id": "cancelled-number",
                "nodes": [{"id": "number", "type": "number", "data": {"value": 3}}],
                "edges": [],
            },
            context,
            lambda *_args: {"status": "error"},
        )
        self.assertEqual(result["status"], "cancelled")

    def test_job_manager_keeps_a_completed_graph_job_completed(self) -> None:
        from src.services.job_manager.manager import HubJobManager

        manager = HubJobManager()
        ran = threading.Event()
        updates: list[dict] = []

        def update(_job_id: str, **values: object) -> None:
            updates.append(dict(values))

        with (
            patch("src.services.job_manager.manager.create_job", return_value={"id": "node-graph-complete"}),
            patch("src.services.job_manager.manager.update_job", side_effect=update),
            patch("src.services.job_manager.manager.record_completed") as record_completed,
        ):
            manager.submit("node_graph", {}, lambda _payload, _context: (ran.set() or {"status": "completed"}), heavy=False)
            self.assertTrue(ran.wait(timeout=1))
            deadline = time.monotonic() + 1
            while manager._contexts and time.monotonic() < deadline:
                time.sleep(0.01)
        self.assertFalse(manager._contexts)
        self.assertTrue(any(item.get("status") == "completed" for item in updates))
        record_completed.assert_called_once_with("node_graph")


class NodeStudioContractTests(unittest.TestCase):
    def test_registry_covers_required_port_types_and_key_nodes(self) -> None:
        from src.services.node_studio.registry import PORT_TYPES, NODE_DEFINITIONS

        self.assertEqual(set(PORT_TYPES), {"IMAGE", "MASK", "VIDEO", "AUDIO", "TEXT", "NUMBER", "BOOLEAN", "MODEL", "METADATA"})
        required = {
            "load_image", "flux_generate", "qwen_image", "sam2_segment", "grounding_dino", "rfdetr_detect", "image_compare",
            "load_video", "probe_media", "trim_cut", "concat", "extract_audio", "replace_audio", "subtitle_burn", "frame_interpolate", "encode", "animesr_upscale",
        }
        self.assertTrue(required.issubset(NODE_DEFINITIONS))

    def test_ui_and_api_keep_node_studio_offline_and_bounded(self) -> None:
        ui = (ROOT / "src" / "ui" / "node_studio.js").read_text(encoding="utf-8")
        api = (ROOT / "src" / "services" / "api" / "api_server.py").read_text(encoding="utf-8")
        schema = (ROOT / "src" / "services" / "node_studio" / "schema.py").read_text(encoding="utf-8")
        self.assertIn("Auto Preview", ui)
        self.assertIn("Run Graph", ui)
        self.assertIn("Undo", ui) if "Undo" in ui else self.assertIn("this.undo", ui)
        self.assertIn("/api/node-studio/run", api)
        self.assertIn("cycle_detected", schema)
        self.assertNotIn("cdn", ui.lower())

    def test_console_helper_and_polling_caches_are_centralized(self) -> None:
        helper = (ROOT / "src" / "services" / "process_manager" / "windows.py").read_text(encoding="utf-8")
        runtime = (ROOT / "src" / "services" / "runtime_registry.py").read_text(encoding="utf-8")
        gpu = (ROOT / "src" / "services" / "api" / "gpu.py").read_text(encoding="utf-8")
        app = (ROOT / "src" / "ui" / "app.js").read_text(encoding="utf-8")
        self.assertIn("CREATE_NO_WINDOW", helper)
        self.assertIn("SW_HIDE", helper)
        self.assertIn("_TASKLIST_CACHE_SECONDS = 5.0", runtime)
        self.assertIn("_GPU_CACHE_SECONDS = 2.0", gpu)
        self.assertIn("2500", app)


if __name__ == "__main__":
    unittest.main()
