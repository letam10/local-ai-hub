from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class UnifiedUiTests(unittest.TestCase):
    def test_frontend_contains_every_required_workspace(self) -> None:
        index = (ROOT / "src" / "ui" / "index.html").read_text(encoding="utf-8")
        pages = (ROOT / "src" / "ui" / "pages.js").read_text(encoding="utf-8")
        frontend = index + pages
        app = (ROOT / "src" / "ui" / "app.js").read_text(encoding="utf-8")
        api_client = (ROOT / "src" / "ui" / "api.js").read_text(encoding="utf-8")
        for label in (
            "Dashboard",
            "AIRI",
            "Vision Studio",
            "SAM2",
            "OCR",
            "Whisper",
            "Voice",
            "Image AI",
            "Media",
            "Video Creative",
            "AnimeSR",
            "Jobs",
            "Models & Storage",
            "Settings",
        ):
            self.assertIn(label, frontend)
        self.assertIn("localStorage", app)
        self.assertIn("/api/dashboard", api_client)

    def test_desktop_and_api_share_the_ui_route(self) -> None:
        desktop = (ROOT / "src" / "app" / "main.py").read_text(encoding="utf-8")
        api = (ROOT / "src" / "services" / "api" / "api_server.py").read_text(encoding="utf-8")
        self.assertIn('UI_URL = f"http://{HOST}:{PORT}/ui/"', desktop)
        self.assertIn("webview", desktop)
        self.assertIn("min_size=(1280, 720)", desktop)
        self.assertIn("window.maximize()", desktop)
        self.assertIn("confirm_close=False", desktop)
        self.assertIn("startup_mutex", desktop)
        self.assertIn("close_owned_api()", desktop)
        self.assertNotIn("tkinter", desktop.casefold())
        self.assertIn('path.startswith("/ui/")', api)
        self.assertIn('normalized == "/api/applications"', api)
        self.assertIn('path == "/api/storage/scan"', api)
        self.assertIn("HubHTTPServer", api)
        self.assertIn("health(probe_gpu=False)", api)
        self.assertIn("health(probe_gpu=True)", api)

    def test_example_application_registry_is_allowlisted(self) -> None:
        registry = json.loads((ROOT / "Config" / "application_registry.example.json").read_text(encoding="utf-8"))
        applications = registry["applications"]
        identifiers = {item["id"] for item in applications}
        self.assertEqual(
            identifiers,
            {
                "anime-upscale-studio",
                "sam2-mask-studio",
                "local-image-studio",
                "qwen-image-studio",
                "airi",
                "ollama",
            },
        )
        self.assertTrue(all(item["launch"] is True for item in applications))
        self.assertTrue(all("${" in item["executable"] for item in applications))

        environment_example = (ROOT / "Config" / "local.env.example.cmd").read_text(encoding="utf-8")
        declared = set(re.findall(r'^set "([A-Z0-9_]+)=', environment_example, flags=re.MULTILINE))
        required = set(re.findall(r"\$\{([A-Z0-9_]+)\}", json.dumps(applications)))
        self.assertTrue(required.issubset(declared), required - declared)

    def test_model_example_lists_the_full_shared_image_inventory(self) -> None:
        registry = json.loads((ROOT / "Config" / "model_registry.example.json").read_text(encoding="utf-8"))
        model_ids = {item["id"] for item in registry["models"]}
        self.assertTrue(
            {
                "flux-2-klein-base-4b-fp8",
                "flux2-vae",
                "qwen-image-2512-fp8",
                "qwen-3-4b",
                "qwen-2.5-vl-7b-fp8",
                "qwen-image-vae",
            }.issubset(model_ids)
        )

    def test_registry_generator_does_not_embed_a_personal_profile_path(self) -> None:
        for name in ("refresh_managed_registry.py", "refresh_final_inventory.py"):
            generator = (ROOT / "scripts" / name).read_text(encoding="utf-8")
            self.assertNotIn("C:\\Users\\", generator)
            self.assertIn("LOCALAPPDATA", generator)

    def test_application_launcher_rejects_an_untrusted_identifier(self) -> None:
        from src.services.runtime_registry import launch

        status, payload = launch("../not-an-application")
        self.assertEqual(status, 404)
        self.assertEqual(payload["status"], "error")

    def test_storage_scan_skips_junction_aliases(self) -> None:
        source = (ROOT / "src" / "services" / "storage_manager" / "overview.py").read_text(encoding="utf-8")
        self.assertIn("FILE_ATTRIBUTE_REPARSE_POINT", source)
        self.assertIn("_is_reparse_point(entry)", source)

    def test_control_plane_summaries_do_not_expose_machine_paths(self) -> None:
        from src.services.api.core import component_statuses
        from src.services.storage_manager.overview import model_summary

        for record in [*component_statuses(), *model_summary()]:
            self.assertFalse({"path", "executable", "environment", "model", "local_path"} & set(record))
            self.assertFalse(any(isinstance(value, str) and re.match(r"^[A-Za-z]:[\\/]", value) for value in record.values()))


if __name__ == "__main__":
    unittest.main()
