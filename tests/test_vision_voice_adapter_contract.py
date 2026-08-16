"""Static contracts for bounded, registry-bound vision and voice workers."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.services.api import core
from src.services.api import jobs
from src.modules.vision.backend import omniparser_adapter, rfdetr_adapter
from src.modules.voice.backend import qwen3_tts_adapter, seed_vc_adapter
from src.shared.utils.adapter_common import normalize_worker_result


def _artifact_id(letter: str = "a") -> str:
    return "artifact_" + letter * 32


class VisionVoiceAdapterContractTests(unittest.TestCase):
    def test_public_requests_reject_paths_commands_and_client_model_selection(self) -> None:
        cases = (
            ("parse_screen", {"path": r"C:\private\screen.png"}),
            ("detect_objects", {"asset_id": _artifact_id(), "model_id": "attacker-model"}),
            ("text_to_speech", {"text": "hello", "command": "python secret.py"}),
            ("convert_voice", {"source": r"D:\source.wav", "target_asset_id": _artifact_id("b")}),
        )
        for tool, payload in cases:
            with self.subTest(tool=tool):
                resolved, error = core._resolve_assets(payload, tool=tool)
                self.assertIsNotNone(error)
                self.assertEqual(resolved, payload)

    def test_vision_worker_requires_opaque_artifact_and_never_echoes_raw_path(self) -> None:
        with patch.object(omniparser_adapter, "run_json_worker") as worker:
            result = omniparser_adapter.parse({"path": r"C:\secret\screen.png"})
        worker.assert_not_called()
        self.assertEqual(result["code"], "raw_input_forbidden")
        self.assertNotIn("C:\\secret", json.dumps(result))

    def test_registry_binding_selects_model_and_clamps_worker_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            python = root / "python.exe"
            helper = root / "detect_cli.py"
            model = root / "weights"
            python.write_bytes(b"python")
            helper.write_bytes(b"worker")
            model.write_bytes(b"weights")
            captured: dict[str, object] = {}

            def fake_worker(command, payload, **kwargs):
                captured["command"] = command
                captured["payload"] = payload
                captured["timeout"] = kwargs["timeout_seconds"]
                return {"status": "completed", "detections": [{"label": "person", "path": r"D:\private\box.json"}], "output": r"D:\private\result.json"}

            with (
                patch.object(rfdetr_adapter, "registered_runtime", return_value=(python, helper, root)),
                patch.object(rfdetr_adapter, "registered_model", return_value=("rf-detr-local", model)),
                patch.object(rfdetr_adapter, "resolve_artifact_input", return_value=(root / "input.png", None)),
                patch.object(rfdetr_adapter, "run_json_worker", side_effect=fake_worker),
            ):
                (root / "input.png").write_bytes(b"image")
                result = rfdetr_adapter.detect({"source_artifact_id": _artifact_id(), "timeout_seconds": 99_999})

        self.assertEqual(captured["timeout"], 300.0)
        self.assertEqual(captured["payload"]["model_id"], "rf-detr-local")
        self.assertNotIn("D:\\private", json.dumps(result))
        self.assertEqual(result["status"], "completed")

    def test_voice_worker_is_registry_bound_and_timeout_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            python = root / "python.exe"
            helper = root / "qwen_cli.py"
            model = root / "qwen-model"
            python.write_bytes(b"python")
            helper.write_bytes(b"worker")
            model.write_bytes(b"weights")
            captured: dict[str, object] = {}

            def fake_worker(command, payload, **kwargs):
                captured["payload"] = payload
                captured["timeout"] = kwargs["timeout_seconds"]
                return {"status": "completed", "audio": r"C:\private\speech.wav", "speaker": "Ryan"}

            with (
                patch.object(qwen3_tts_adapter, "registered_runtime", return_value=(python, helper, root)),
                patch.object(qwen3_tts_adapter, "registered_model", return_value=("qwen3-tts-local", model)),
                patch.object(qwen3_tts_adapter, "run_json_worker", side_effect=fake_worker),
            ):
                result = qwen3_tts_adapter.synthesize({"text": "Một câu ngắn", "timeout_seconds": 99_999})

        self.assertEqual(captured["timeout"], 900.0)
        self.assertEqual(captured["payload"]["model_id"], "qwen3-tts-local")
        self.assertNotIn("C:\\private", json.dumps(result))
        self.assertEqual(result["status"], "completed")

    def test_voice_audio_alias_is_job_provenanced_and_published_as_opaque_artifact(self) -> None:
        raw_audio = r"D:\\LocalAIHub\\Output\\Audio\\speech.wav"
        normalized = normalize_worker_result(
            {"status": "completed", "audio": raw_audio},
            component_id="qwen3_tts",
            context=object(),
            output_fields=("output", "audio", "files", "outputs"),
        )
        self.assertEqual(normalized["output"], raw_audio)
        self.assertNotIn("audio", normalized)

        seen: list[Path] = []

        def register(paths, *, provenance):
            seen.extend(paths)
            return [{"id": _artifact_id("c"), "provenance": provenance}]

        record = {"id": "job_20260816_123456_abcd1234", "tool": "text_to_speech"}
        with patch.object(jobs.artifact_store, "register_worker_outputs", side_effect=register):
            safe, error = jobs._publish_result(normalized, record)

        self.assertIsNone(error)
        self.assertEqual(seen, [Path(raw_audio)])
        self.assertEqual(safe["artifacts"][0]["id"], _artifact_id("c"))
        self.assertNotIn(raw_audio, json.dumps(safe))
        self.assertNotIn("audio", safe)

    def test_seed_voice_requires_two_opaque_artifacts_and_never_uses_raw_source(self) -> None:
        with patch.object(seed_vc_adapter, "run_json_worker") as worker:
            result = seed_vc_adapter.convert({"source": r"D:\source.wav", "target_asset_id": _artifact_id("b")})
        worker.assert_not_called()
        self.assertEqual(result["code"], "raw_input_forbidden")
        self.assertNotIn("D:\\source", json.dumps(result))

    def test_capability_projection_stays_partial_even_when_static_leaves_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            python = root / "python.exe"
            helper = root / "omni_cli.py"
            model = root / "weights"
            for path in (python, helper, model):
                path.write_bytes(b"owned")
            with (
                patch.object(omniparser_adapter, "registered_runtime", return_value=(python, helper, root)),
                patch.object(omniparser_adapter, "registered_model", return_value=("omni-local", model)),
            ):
                capability = omniparser_adapter.capability()
        self.assertTrue(capability["runtime_ready"])
        self.assertTrue(capability["model_registry_ready"])
        self.assertEqual(capability["status"], "partial")


if __name__ == "__main__":
    unittest.main()
