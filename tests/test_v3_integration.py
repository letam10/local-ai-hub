from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class V3IntegrationContractTests(unittest.TestCase):
    def test_primary_workflows_are_direct_and_allowlisted(self) -> None:
        from src.services.api.core import TOOL_COMPONENTS

        self.assertTrue(
            {
                "segment_image",
                "segment_from_box",
                "segment_from_points",
                "segment_from_text",
                "track_video_object",
                "upscale_anime_video",
                "transcribe_media",
                "text_to_speech",
                "convert_voice",
                "generate_flux",
                "generate_qwen_image",
                "run_media_operation",
                "parse_screen",
                "detect_objects",
                "ground_objects",
                "ocr_document",
            }.issubset(TOOL_COMPONENTS)
        )

    def test_primary_ui_only_launches_airi_externally(self) -> None:
        pages = (ROOT / "src" / "ui" / "pages.js").read_text(encoding="utf-8") + (ROOT / "src" / "ui" / "shared" / "rendering.js").read_text(encoding="utf-8") + "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "src" / "ui" / "features").rglob("*.js"))
        app = (ROOT / "src" / "ui" / "app.js").read_text(encoding="utf-8")
        api = (ROOT / "src" / "ui" / "api.js").read_text(encoding="utf-8")
        runtime_registry = (ROOT / "src" / "services" / "runtime_registry.py").read_text(encoding="utf-8")
        self.assertEqual(set(re.findall(r'data-launch="([^"]+)"', pages)), {"airi"})
        self.assertEqual(pages.count('data-launch="airi"'), 1)
        self.assertIn("Mở AIRI", pages)
        self.assertIn("launchApplication(launchButton.dataset.launch)", app)
        self.assertIn("/api/applications/${encodeURIComponent(id)}/launch", api)
        self.assertIn("def launch(application_id: str)", runtime_registry)
        self.assertNotIn("data-close-airi", pages + app)
        self.assertIn("ComfyUI Advanced", pages)
        self.assertIn("Anime Upscale Studio chỉ là legacy/debug fallback", pages)
        self.assertNotIn("SAM2 Mask Studio không còn là workflow chính", pages)
        self.assertIn('data-m3-workspace="sam2"', pages)
        self.assertIn('data-tool="segment_from_points"', pages)
        self.assertIn("getBootstrap", api)
        self.assertIn("getBootstrap()", app)
        self.assertIn('tool(state, "segment_from_points")', pages)

    def test_media_workspace_covers_multifile_and_image_operations(self) -> None:
        pages = (ROOT / "src" / "ui" / "pages.js").read_text(encoding="utf-8") + (ROOT / "src" / "ui" / "shared" / "rendering.js").read_text(encoding="utf-8") + "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "src" / "ui" / "features").rglob("*.js"))
        adapter = (ROOT / "src" / "modules" / "media_editor" / "backend" / "adapter.py").read_text(encoding="utf-8")
        for operation in (
            "concat",
            "image_sequence_video",
            "image_crop",
            "image_rotate",
            "image_flip",
            "image_convert",
            "extract_frames",
        ):
            self.assertIn(operation, pages)
            self.assertIn(operation, adapter)
        self.assertIn("_concat_manifest", adapter)
        self.assertIn("input_paths", adapter)
        self.assertNotIn("UI hiện không tạo manifest raw path", adapter)

    def test_workers_are_hidden_and_owned(self) -> None:
        managed = (ROOT / "src" / "services" / "process_manager" / "managed.py").read_text(encoding="utf-8")
        main = (ROOT / "src" / "app" / "main.py").read_text(encoding="utf-8")
        helper = (ROOT / "src" / "services" / "process_manager" / "windows.py").read_text(encoding="utf-8")
        self.assertIn("CREATE_NO_WINDOW", helper)
        self.assertIn("CREATE_NEW_PROCESS_GROUP", helper)
        self.assertIn("SW_HIDE", helper)
        self.assertNotIn("CREATE_NEW_CONSOLE", helper)
        self.assertIn("shell", helper)
        self.assertIn("popen_hidden", managed)
        self.assertIn("popen_hidden", main)
        self.assertIn("terminate_process_tree(process.pid)", managed)
        self.assertNotIn('"taskkill"', managed)
        self.assertIn("CreateToolhelp32Snapshot", helper)
        self.assertIn("TerminateProcess", helper)

    def test_artifact_api_scrubs_private_machine_paths(self) -> None:
        from src.services.artifact_store import publicize

        public = publicize({
            "output": r"D:\private-machine\Output\result.mp4",
            "diagnostic": r"failed at D:\private-machine\Logs\worker.log",
        })
        serialized = json.dumps(public, ensure_ascii=False)
        self.assertNotRegex(serialized, r"[A-Za-z]:[\\/]")
        self.assertNotIn("private-machine", serialized)

    def test_public_job_data_omits_input_and_resume_paths(self) -> None:
        from src.services.api.jobs import public_job

        public = public_job({
            "id": "job_example",
            "tool": "segment_image",
            "input": {"path": r"D:\private-machine\input.png"},
            "resume_data": {"path": r"D:\private-machine\input.png"},
            "status": "completed",
            "progress": 100,
            "created_at": "2026-01-01T00:00:00Z",
            "result": {"mask": r"D:\private-machine\Output\mask.png"},
        })
        self.assertNotIn("input", public)
        self.assertNotIn("resume_data", public)
        self.assertNotRegex(json.dumps(public, ensure_ascii=False), r"[A-Za-z]:[\\/]")

    def test_public_api_rejects_raw_workstation_paths(self) -> None:
        from src.services.api.core import _resolve_assets

        _payload, error = _resolve_assets({"path": r"D:\private-machine\input.png"})
        self.assertEqual(error, "Dùng artifact ID do Hub tạo thay vì gửi đường dẫn cục bộ.")

    def test_inventory_distinguishes_reparse_points_and_checks_real_references(self) -> None:
        source = (ROOT / "scripts" / "inventory_legacy_v3.py").read_text(encoding="utf-8")
        self.assertIn("os.path.isjunction", source)
        self.assertIn("needle = str(path)", source)
        self.assertIn("shortcut_references", source)
        self.assertIn("runtime_compatibility_references", source)
        self.assertIn("Start Menu", source)
        self.assertIn("followlinks=False", source)

    def test_junction_cleanup_requires_an_explicit_approval_manifest(self) -> None:
        source = (ROOT / "scripts" / "cleanup_legacy_v3.ps1").read_text(encoding="utf-8")
        self.assertIn("ApprovedJunctionPath", source)
        self.assertIn("requires one or more exact", source)
        self.assertIn("not listed in -ApprovedJunctionPath", source)

    def test_final_local_report_is_read_only_and_limited_to_three_rounds(self) -> None:
        source = (ROOT / "scripts" / "write_v3_final_report.py").read_text(encoding="utf-8")
        self.assertIn("maximum three rounds", source)
        self.assertIn("verified_absent", source)
        self.assertIn("never moves, deletes", source)

    def test_environment_delete_requires_recorded_direct_smoke(self) -> None:
        source = (ROOT / "scripts" / "migrate_python_environment_v3.ps1").read_text(encoding="utf-8")
        self.assertIn("RecordFunctionalSmoke", source)
        self.assertIn("functional_smoke_passed", source)
        self.assertIn("blocked_active_process", source)
        self.assertIn("cu118", source)
        self.assertIn("cu124", source)

    def test_tool_status_requires_local_bounded_smoke_evidence(self) -> None:
        source = (ROOT / "src" / "services" / "api" / "core.py").read_text(encoding="utf-8")
        smoke = (ROOT / "src" / "services" / "tool_smoke.py").read_text(encoding="utf-8")
        self.assertIn("SMOKE_ELIGIBLE_TOOLS", source)
        self.assertIn("smoke_passed(tool)", source)
        self.assertIn("bounded_direct_job", smoke)

    def test_v3_examples_and_shortcut_follow_single_window_contract(self) -> None:
        hub = json.loads((ROOT / "Config" / "hub_config.example.json").read_text(encoding="utf-8"))
        applications = json.loads((ROOT / "Config" / "application_registry.example.json").read_text(encoding="utf-8"))["applications"]
        shortcut = (ROOT / "scripts" / "update_managed_shortcuts.ps1").read_text(encoding="utf-8")
        self.assertEqual(hub["schema_version"], 3)
        self.assertTrue(hub["start_maximized"])
        self.assertEqual((hub["minimum_width"], hub["minimum_height"]), (1280, 720))
        legacy = {"anime-upscale-studio", "sam2-mask-studio", "local-image-studio", "qwen-image-studio"}
        self.assertTrue(all(item.get("advanced_only") is True for item in applications if item["id"] in legacy))
        self.assertIn("LocalAIHub.exe", shortcut)
        self.assertIn("INSTALLED_PRODUCT_MANIFEST_REQUIRED", shortcut)
        self.assertNotIn("pythonw.exe", shortcut)


if __name__ == "__main__":
    unittest.main()
