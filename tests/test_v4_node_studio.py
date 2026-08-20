from __future__ import annotations
# FILE NOTE
# - Mục đích: Contract tests cho V4 Node Studio, polling cache, console helper
# - Liên kết trực tiếp: src/services/node_studio/, src/ui/app.js, src/services/runtime_registry.py
# - Vùng ảnh hưởng khi sửa: V6 đã xóa window.setInterval auto-polling; test_console_helper cần update

import json
import importlib.util
import os
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class _Context:
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self.cancelled = False
        self.progress_updates: list[tuple[int, str | None]] = []

    def progress(self, value: int, message: str | None = None) -> None:
        self.progress_updates.append((value, message))


class _MediaOwner:
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self.cancelled = False
        self.processes: list[object] = []

    def attach_process(self, process: object, _label: str) -> None:
        self.processes.append(process)

    def detach_process(self, process: object) -> None:
        if process in self.processes:
            self.processes.remove(process)

    def progress(self, _value: int, _message: str | None = None) -> None:
        return None


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


class ComfyBridgeWorkflowTests(unittest.TestCase):
    def test_local_bridge_save_load_and_private_path_rejection(self) -> None:
        from src.modules.image_generation.backend import comfyui

        workflow = {
            "schema_version": 1,
            "id": "bridge_test",
            "title": "Bridge test",
            "kind": "raw_comfy_api",
            "prompt": {"1": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}}},
            "bindings": {"text": [{"node": "1", "input": "text"}]},
        }
        with TemporaryDirectory() as temporary:
            tracked = Path(temporary) / "tracked"
            local = Path(temporary) / "local"
            with patch.object(comfyui, "BRIDGE_WORKFLOW_ROOT", tracked), patch.object(comfyui, "BRIDGE_LOCAL_ROOT", local):
                status, payload = comfyui.save_bridge_workflow("bridge_test", workflow)
                self.assertEqual(status, 201)
                self.assertEqual(payload["status"], "completed")
                self.assertEqual(comfyui.load_bridge_workflow("bridge_test")["id"], "bridge_test")
                self.assertEqual(comfyui.list_bridge_workflows()[0]["local"], True)
                unsafe = {**workflow, "id": "unsafe_bridge", "description": r"D:\private-machine\secret.json"}
                error_status, error_payload = comfyui.save_bridge_workflow("unsafe_bridge", unsafe)
                self.assertEqual(error_status, 400)
                self.assertIn("đường dẫn", error_payload["error"])

    def test_advanced_frontend_is_loopback_only_and_not_a_browser_popup(self) -> None:
        pages = (ROOT / "src" / "ui" / "pages.js").read_text(encoding="utf-8") + (ROOT / "src" / "ui" / "shared" / "rendering.js").read_text(encoding="utf-8") + (ROOT / "src" / "ui" / "features" / "image_mask" / "render.js").read_text(encoding="utf-8")
        app = (ROOT / "src" / "ui" / "app.js").read_text(encoding="utf-8")
        self.assertIn('data-workspace-tab="image:advanced"', pages)
        self.assertIn('sandbox="allow-scripts allow-same-origin allow-forms allow-downloads"', pages)
        self.assertIn("127\\.0\\.0\\.1", pages)
        self.assertNotIn("window.open", app)

    def test_portable_comfyui_gets_a_safe_startup_window(self) -> None:
        backend = (ROOT / "src" / "modules" / "image_generation" / "backend" / "comfyui.py").read_text(encoding="utf-8")
        example = json.loads((ROOT / "Config" / "hub_config.example.json").read_text(encoding="utf-8"))
        self.assertIn("max(300.0", backend)
        self.assertEqual(example["comfyui_start_timeout_seconds"], 300)


class DistributionContractTests(unittest.TestCase):
    def test_core_manifest_excludes_models_and_installer_full_needs_confirmation(self) -> None:
        core = json.loads((ROOT / "distribution" / "core.manifest.json").read_text(encoding="utf-8"))
        modules = json.loads((ROOT / "distribution" / "modules.manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(core["asset_name"], "LocalAIHub-Core-Win64.zip")
        self.assertEqual(core["target_size_mib"], {"minimum": 50, "maximum": 200})
        self.assertIn("Models", core["exclude_roots"])
        self.assertTrue(modules["download_policy"]["never_download_models_during_full_install_without_confirmation"])

        spec = importlib.util.spec_from_file_location("bootstrap_installer_test", ROOT / "scripts" / "bootstrap_installer.py")
        assert spec and spec.loader
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        plan = installer.plan_install(modules, "full", [], False)
        self.assertEqual(len(plan), 5)
        self.assertTrue(all(item["module_archive"] == "unavailable" for item in plan))
        self.assertTrue(all(item["model_download"] == "blocked_without_explicit_confirmation" for item in plan))

    def test_source_audit_and_core_builder_are_read_only_by_default(self) -> None:
        source_audit = (ROOT / "scripts" / "audit_source_v5.py").read_text(encoding="utf-8")
        builder = (ROOT / "scripts" / "build_core_release.py").read_text(encoding="utf-8")
        self.assertIn("read-only", source_audit.lower())
        self.assertIn("never copies, moves, deletes", source_audit.lower())
        self.assertIn("--build", builder)
        self.assertIn("--runtime-dir", builder)
        self.assertIn("Do not pad it", builder)


class NodeStudioContractTests(unittest.TestCase):
    def test_registry_covers_required_port_types_and_key_nodes(self) -> None:
        from src.services.node_studio.registry import PORT_TYPES, NODE_DEFINITIONS

        self.assertEqual(set(PORT_TYPES), {"IMAGE", "MASK", "VIDEO", "AUDIO", "TEXT", "NUMBER", "BOOLEAN", "MODEL", "METADATA"})
        required = {
            "load_image", "flux_generate", "qwen_image", "image_edit", "image_upscale", "comfyui_workflow", "sam2_segment", "grounding_dino", "rfdetr_detect", "image_compare",
            "load_video", "video_generate", "video_transform", "video_upscale", "probe_media", "trim_cut", "concat", "extract_audio", "replace_audio", "subtitle_burn", "frame_interpolate", "encode", "animesr_upscale",
        }
        self.assertTrue(required.issubset(NODE_DEFINITIONS))

    def test_image_nodes_publish_typed_availability_contract(self) -> None:
        from src.services.node_studio.registry import NODE_DEFINITIONS, registry_payload

        for node_type in ("flux_generate", "qwen_image", "image_edit", "image_upscale"):
            definition = NODE_DEFINITIONS[node_type].public()
            self.assertEqual(definition["availability"]["status"], definition["status"])
            self.assertTrue(definition["availability"]["reason"])
            self.assertTrue(definition["availability"]["action"])

        payload = registry_payload("image")
        self.assertEqual(payload["contract_version"], "node-studio.v2")
        self.assertEqual(set(payload["availability"]["counts"]), {"operational", "partial", "unavailable"})
        self.assertIn("image_upscale", {item["type"] for item in payload["nodes"]})

    def test_image_templates_cover_generate_edit_upscale_and_export(self) -> None:
        from src.services.node_studio.schema import validate_graph

        create = json.loads((ROOT / "workflows" / "image_create_upscale.json").read_text(encoding="utf-8"))
        edit = json.loads((ROOT / "workflows" / "image_edit_upscale.json").read_text(encoding="utf-8"))
        self.assertTrue(validate_graph(create, require_runnable=True)["valid"])
        edit_validation = validate_graph(edit)
        self.assertTrue(edit_validation["valid"], edit_validation["errors"])
        self.assertIn("image_edit", {item["type"] for item in edit["nodes"]})
        self.assertIn("image_upscale", {item["type"] for item in create["nodes"]})
        self.assertEqual(edit["nodes"][0]["data"]["asset_id"], "")
        edge_targets = {(item["target"]["node"], item["target"]["port"]) for item in edit["edges"]}
        self.assertIn(("edit", "image"), edge_targets)

    def test_video_templates_cover_transform_generation_and_export_contracts(self) -> None:
        from src.services.node_studio.registry import NODE_DEFINITIONS
        from src.services.node_studio.schema import validate_graph

        transform = json.loads((ROOT / "workflows" / "video_creative_pipeline.json").read_text(encoding="utf-8"))
        generation = json.loads((ROOT / "workflows" / "video_generation_unavailable.json").read_text(encoding="utf-8"))
        self.assertTrue(validate_graph(transform)["valid"])
        self.assertTrue(validate_graph(generation, require_runnable=True)["valid"])
        self.assertEqual(NODE_DEFINITIONS["video_generate"].status, "unavailable")
        self.assertEqual(NODE_DEFINITIONS["video_upscale"].status, "partial")
        transform_types = {item["type"] for item in transform["nodes"]}
        self.assertTrue({"video_transform", "video_upscale", "frame_interpolate", "encode", "preview_video", "save_video", "export_video"}.issubset(transform_types))

    def test_video_generation_returns_honest_unavailable_action(self) -> None:
        from src.services.node_studio.engine import execute_graph

        result = execute_graph(
            {
                "schema_version": 1,
                "id": "video-generation-status",
                "nodes": [
                    {"id": "prompt", "type": "prompt_text", "data": {"text": "Một cảnh ngắn"}},
                    {"id": "generate", "type": "video_generate", "data": {}},
                ],
                "edges": [{"id": "prompt_generate", "source": {"node": "prompt", "port": "text"}, "target": {"node": "generate", "port": "prompt"}}],
            },
            _Context("job_video_generation_status"),
            lambda *_args: self.fail("video_generate must not call an unavailable backend"),
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertTrue(result["next_action"])

    def test_video_upscale_command_is_allowlisted(self) -> None:
        from src.modules.media_editor.backend import adapter

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            ffmpeg = root / "ffmpeg.exe"
            source = root / "source.mp4"
            target = root / "target.mp4"
            ffmpeg.write_bytes(b"")
            source.write_bytes(b"input")
            with patch.object(adapter, "_paths", return_value=(ffmpeg, None)):
                command = adapter._command({"operation": "video_upscale", "scale": 2}, source, target)
        assert command is not None
        command_text = " ".join(command)
        self.assertIn("trunc(iw*2.0/2)*2", command_text)
        self.assertIn("-c:a", command)

    def test_resize_commands_preserve_ffmpeg_aspect_sentinel(self) -> None:
        from src.modules.media_editor.backend import adapter

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            ffmpeg = root / "ffmpeg.exe"
            source = root / "source.mp4"
            target = root / "target.mp4"
            ffmpeg.write_bytes(b"")
            source.write_bytes(b"input")
            with patch.object(adapter, "_paths", return_value=(ffmpeg, None)):
                video_command = adapter._command({"operation": "resize", "width": 16, "height": -2}, source, target)
                image_command = adapter._command({"operation": "image_resize", "width": 16, "height": -2}, source, target)
        assert video_command is not None
        assert image_command is not None
        self.assertIn("scale=16:-2", video_command)
        self.assertIn("scale=16:-2", image_command)

    def test_default_video_resize_sentinel_preserves_aspect_through_upscale(self) -> None:
        if os.environ.get("LOCALAIHUB_RUN_VIDEO_SMOKE") != "1":
            self.skipTest("Video smoke is opt-in: set LOCALAIHUB_RUN_VIDEO_SMOKE=1 when resources are available")

        from src.modules.media_editor.backend import adapter
        from src.services import artifact_store
        from src.services.node_studio import engine as node_engine
        from src.services.process_manager import managed

        ffmpeg, ffprobe = adapter._paths()
        if ffmpeg is None or not ffmpeg.is_file() or ffprobe is None or not ffprobe.is_file():
            self.skipTest("Canonical FFmpeg/ffprobe is not installed")

        job_id = "test_video_resize_sentinel"
        template = json.loads((ROOT / "workflows" / "video_creative_pipeline.json").read_text(encoding="utf-8"))
        transform_data = next(item["data"] for item in template["nodes"] if item["type"] == "video_transform")
        self.assertEqual(transform_data.get("height"), -2)
        try:
            with TemporaryDirectory() as temporary:
                root = Path(temporary)
                source = root / "input.mp4"
                seed = adapter.run_hidden(
                    [
                        str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "color=c=blue:s=16x16:r=4", "-t", "1",
                        "-pix_fmt", "yuv420p", "-c:v", "libx264", str(source),
                    ],
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
                self.assertEqual(seed.returncode, 0, seed.stderr.decode("utf-8", errors="replace"))

                output_root = root / "output"
                owner = _MediaOwner(job_id)
                upload_root = root / "uploads"
                index_path = root / "artifacts.json"
                log_root = root / "logs"
                with (
                    patch.object(artifact_store, "UPLOAD_ROOT", upload_root),
                    patch.object(artifact_store, "OUTPUT_ROOT", output_root),
                    patch.object(artifact_store, "INDEX_PATH", index_path),
                    patch.object(adapter, "OUTPUT_ROOT", output_root),
                    patch.object(node_engine, "OUTPUT_ROOT", output_root),
                    patch.object(managed, "LOG_ROOT", log_root),
                ):
                    uploaded = artifact_store.stage_upload("input.mp4", source.read_bytes(), "video/mp4")
                    graph = json.loads(json.dumps(template))
                    for node in graph["nodes"]:
                        if node["type"] == "load_video":
                            node["data"]["asset_id"] = uploaded["id"]
                        elif node["type"] == "video_transform":
                            node["data"]["width"] = 16
                        elif node["type"] == "frame_interpolate":
                            node["data"]["mode"] = "off"
                        elif node["type"] == "encode":
                            node["data"].update({"codec": "libx264", "prefer_gpu": False, "preset": "ultrafast"})

                    def execute_tool(tool: str, payload: dict, context: _MediaOwner) -> dict:
                        if tool == "run_media_operation":
                            return adapter.run_operation(payload, context)
                        raise AssertionError(f"Unexpected tool in bounded graph: {tool}")

                    result = node_engine.execute_graph(graph, owner, execute_tool)
                    self.assertEqual(result.get("status"), "completed", result)
                    self.assertEqual(len(result.get("nodes", [])), 9)
                    upscale_node = next(item for item in result["nodes"] if item["id"] == "upscale")
                    upscale_artifact = upscale_node["output"]["video"]["id"]
                    upscale_path = artifact_store.resolve(upscale_artifact)
                    self.assertIsNotNone(upscale_path)
                    details = adapter.probe(str(upscale_path))

                self.assertEqual(details.get("status"), "completed", details)
                video_stream = next(stream for stream in details["streams"] if stream.get("codec_type") == "video")
                self.assertEqual((video_stream["width"], video_stream["height"]), (32, 32))

            self.assertFalse(owner.processes, "FFmpeg child processes must be detached after the bounded smoke")
        finally:
            node_engine.node_cache.clear()

    def test_graph_run_provenance_is_public_and_deduplicated(self) -> None:
        from src.services.node_studio.state import GraphRunRegistry

        registry = GraphRunRegistry()
        artifact_id = "artifact_" + "a" * 32
        registry.begin("job_provenance", {"id": "image-test", "nodes": [{"id": "upscale", "type": "image_upscale"}]})
        output = {"image": {"id": artifact_id, "name": "result.png", "media_type": "image/png", "path": r"D:\private\result.png"}, "copy": {"id": artifact_id}}
        registry.update_node("job_provenance", "upscale", status="completed", progress=100, output=output, next_action="Kiểm tra preview.")
        registry.finish("job_provenance", status="completed")
        snapshot = registry.snapshot("job_provenance")
        assert snapshot is not None
        self.assertEqual(snapshot["contract_version"], "node-run.v2")
        self.assertEqual(len(snapshot["provenance"]), 1)
        self.assertNotIn("D:\\private", str(snapshot))
        self.assertNotIn("D:\\private", str(snapshot["nodes"][0]["output"]))
        self.assertEqual(snapshot["next_action"], "Kiểm tra preview.")

    def test_ui_and_api_keep_node_studio_offline_and_bounded(self) -> None:
        ui = (ROOT / "src" / "ui" / "node_studio.js").read_text(encoding="utf-8")
        api = (ROOT / "src" / "services" / "api" / "api_server.py").read_text(encoding="utf-8")
        schema = (ROOT / "src" / "services" / "node_studio" / "schema.py").read_text(encoding="utf-8")
        self.assertIn("Auto Preview", ui)
        self.assertIn("Run Graph", ui)
        self.assertIn("Undo", ui) if "Undo" in ui else self.assertIn("this.undo", ui)
        self.assertIn("LiteGraph.LGraphCanvas", ui)
        self.assertIn("allow_reconnect_links", ui)
        self.assertNotIn("chooseOutput", ui)
        self.assertNotIn("connectInput", ui)
        self.assertIn("/api/node-studio/run", api)
        self.assertIn("/api/node-studio/availability", api)
        self.assertIn("contract_version", api)
        self.assertIn("cycle_detected", schema)
        self.assertNotIn("cdn", ui.lower())

    def test_console_helper_and_polling_caches_are_centralized(self) -> None:
        helper = (ROOT / "src" / "services" / "process_manager" / "windows.py").read_text(encoding="utf-8")
        runtime = (ROOT / "src" / "services" / "runtime_registry.py").read_text(encoding="utf-8")
        gpu = (ROOT / "src" / "services" / "api" / "gpu.py").read_text(encoding="utf-8")
        app = (ROOT / "src" / "ui" / "app.js").read_text(encoding="utf-8")
        launcher_audit = (ROOT / "scripts" / "audit_local_ai_hub_entrypoints.ps1").read_text(encoding="utf-8")
        self.assertIn("CREATE_NO_WINDOW", helper)
        self.assertIn("SW_HIDE", helper)
        self.assertIn("SW_MINIMIZE", helper)
        self.assertIn("_TASKLIST_CACHE_SECONDS = 5.0", runtime)
        self.assertIn("_GPU_CACHE_SECONDS = 2.0", gpu)
        # V6 removed window.setInterval auto-polling in favour of explicit refreshFast() calls.
        # Verify the explicit refresh mechanism is present and no unbounded auto-poll is registered.
        self.assertIn("refreshFast", app)
        self.assertNotIn("window.setInterval(() => refreshFast", app)
        self.assertIn("pythonw.exe", launcher_audit)
        retired_launcher = "launch_local_ai_hub" + ".cmd"
        self.assertFalse((ROOT / "scripts" / retired_launcher).exists())


if __name__ == "__main__":
    unittest.main()
