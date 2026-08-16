"""Static contracts for the bounded FLUX/Qwen Image ComfyUI hand-off."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.modules.image_generation.backend import comfyui


def _flux_workflow(*, image: bool = False) -> dict[str, object]:
    value: dict[str, object] = {
        "74": {"inputs": {"text": ""}},
        "67": {"inputs": {"text": ""}},
        "62": {"inputs": {"steps": 20, "width": 768, "height": 768}},
        "66": {"inputs": {"width": 768, "height": 768}},
        "73": {"inputs": {"noise_seed": 1}},
        "9": {"inputs": {"filename_prefix": ""}},
    }
    if image:
        value["80"] = {"inputs": {"image": ""}}
    return value


def _qwen_workflow() -> dict[str, object]:
    return {
        "5": {"inputs": {"text": ""}},
        "6": {"inputs": {"text": ""}},
        "7": {"inputs": {"width": 512, "height": 512}},
        "8": {"inputs": {"seed": 1, "steps": 8}},
        "10": {"inputs": {"filename_prefix": ""}},
    }


class ComfyImageGenerationContractTests(unittest.TestCase):
    def _write_workflow(self, root: Path, name: str, value: object) -> None:
        directory = root / "workflows"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text(json.dumps(value), encoding="utf-8")

    def test_each_engine_uses_its_own_configured_workflow_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            flux = parent / "flux"
            qwen = parent / "qwen"
            self._write_workflow(flux, "flux2_klein_t2i_base_api.json", _flux_workflow())
            self._write_workflow(qwen, "qwen_image_2512_t2i_api.json", _qwen_workflow())

            def configured(component_id: str, _field: str, _variable: str):
                return {"flux_klein_studio": flux, "qwen_image": qwen}.get(component_id)

            with patch.object(comfyui, "configured_path", side_effect=configured):
                self.assertEqual(comfyui._workflow_path("flux", False), flux / "workflows" / "flux2_klein_t2i_base_api.json")
                self.assertEqual(comfyui._workflow_path("qwen", False), qwen / "workflows" / "qwen_image_2512_t2i_api.json")

    def test_invalid_flux_workflow_refuses_before_backend_start_or_upload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write_workflow(root, "flux2_klein_t2i_base_api.json", {"74": {"inputs": {"text": ""}}})
            with (
                patch.object(comfyui, "_studio_root", return_value=root),
                patch.object(comfyui, "ensure_running", side_effect=AssertionError("invalid workflow must not start ComfyUI")) as start,
                patch.object(comfyui, "_upload_input_image", side_effect=AssertionError("invalid workflow must not upload")) as upload,
            ):
                result = comfyui.generate("flux", {"prompt": "small image"})
            start.assert_not_called()
            upload.assert_not_called()
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["execution"], "not_run")
            self.assertIn("image_workflow_invalid", result["reason"])
            self.assertNotIn(str(root), json.dumps(result))

    def test_qwen_8gb_profile_clamps_request_without_download_or_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write_workflow(root, "qwen_image_2512_t2i_api.json", _qwen_workflow())
            with patch.object(comfyui, "_studio_root", return_value=root):
                workflow, error = comfyui._workflow("qwen", {"prompt": "small image", "width": 2048, "height": 1024, "steps": 80})
            self.assertIsNone(error)
            assert workflow is not None
            self.assertEqual(workflow["7"]["inputs"], {"width": 512, "height": 512})
            self.assertEqual(workflow["8"]["inputs"]["steps"], 12)
            command = comfyui._runtime_command(Path("python.exe"), Path("main.py"), memory_profile="qwen_8gb_low_vram")
            self.assertIn("--lowvram", command)
            self.assertNotIn("download", " ".join(command).lower())
            self.assertNotIn("install", " ".join(command).lower())

            with (
                patch.object(comfyui, "_studio_root", return_value=root),
                patch.object(comfyui, "ensure_running", return_value=(False, "not started")) as start,
            ):
                result = comfyui.generate("qwen", {"prompt": "small image"})
            start.assert_called_once_with(memory_profile="qwen_8gb_low_vram")
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["execution"], "not_run")

    def test_qwen_image_input_refuses_without_starting_or_uploading(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "input.png"
            image.write_bytes(b"image")
            self._write_workflow(root, "qwen_image_2512_t2i_api.json", _qwen_workflow())
            with (
                patch.object(comfyui, "_studio_root", return_value=root),
                patch.object(comfyui, "ensure_running", side_effect=AssertionError("unsupported input must not start ComfyUI")) as start,
                patch.object(comfyui, "_upload_input_image", side_effect=AssertionError("unsupported input must not upload")) as upload,
            ):
                result = comfyui.generate("qwen", {"prompt": "small image", "input_image": str(image)})
            start.assert_not_called()
            upload.assert_not_called()
            self.assertEqual(result["status"], "unavailable")
            self.assertIn("image_workflow_unavailable", result["reason"])

    def test_reparse_workflow_path_is_refused_without_backend_start(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write_workflow(root, "flux2_klein_t2i_base_api.json", _flux_workflow())
            with (
                patch.object(comfyui, "_studio_root", return_value=root),
                patch.object(comfyui, "_is_reparse", return_value=True),
                patch.object(comfyui, "ensure_running", side_effect=AssertionError("reparse workflow must not start ComfyUI")) as start,
            ):
                result = comfyui.generate("flux", {"prompt": "small image"})
            start.assert_not_called()
            self.assertEqual(result["status"], "unavailable")
            self.assertIn("image_workflow_unavailable", result["reason"])


if __name__ == "__main__":
    unittest.main()
