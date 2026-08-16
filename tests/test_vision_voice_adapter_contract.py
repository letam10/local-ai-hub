"""Static contracts for bounded, registry-bound vision and voice workers."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.services.api import core
from src.services.api import jobs
from src.modules.ocr.backend import adapter as paddleocr_adapter
from src.modules.vision.backend import omniparser_adapter, rfdetr_adapter
from src.modules.voice.backend import qwen3_tts_adapter, seed_vc_adapter
from src.shared.utils import adapter_common
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

    def test_omniparser_timeout_is_bounded_and_has_a_finite_root_cause(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            python = root / "python.exe"
            helper = root / "omni_cli.py"
            model = root / "weights"
            source = root / "input.png"
            for path in (python, helper, model, source):
                path.write_bytes(b"fixture")
            captured: dict[str, object] = {}

            def fake_worker(_command, _payload, **kwargs):
                captured["timeout"] = kwargs["timeout_seconds"]
                return {"status": "error", "error": "omniparser vượt quá thời gian cho phép của Hub."}

            with (
                patch.object(omniparser_adapter, "registered_runtime", return_value=(python, helper, root)),
                patch.object(omniparser_adapter, "registered_model", return_value=("omni-local", model)),
                patch.object(omniparser_adapter, "resolve_artifact_input", return_value=(source, None)),
                patch.object(omniparser_adapter, "run_json_worker", side_effect=fake_worker),
            ):
                result = omniparser_adapter.parse({"source_artifact_id": _artifact_id(), "timeout_seconds": 999_999})

        self.assertEqual(captured["timeout"], 300.0)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "worker_timeout")
        self.assertEqual(result["execution"], "timed_out")
        self.assertNotIn(str(root), json.dumps(result))

    def test_paddleocr_missing_tool_model_is_unavailable_without_worker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            python = root / "python.exe"
            helper = root / "paddle_cli.py"
            source = root / "input.png"
            for path in (python, helper, source):
                path.write_bytes(b"fixture")
            with (
                patch.object(paddleocr_adapter, "registered_runtime", return_value=(python, helper, root)),
                patch.object(paddleocr_adapter, "registered_model", return_value=None),
                patch.object(paddleocr_adapter, "resolve_artifact_input", return_value=(source, None)),
                patch.object(paddleocr_adapter, "run_json_worker") as worker,
            ):
                result = paddleocr_adapter.parse({"source_artifact_id": _artifact_id()})

        worker.assert_not_called()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["code"], "tool_model_missing")
        self.assertEqual(result["execution"], "not_run")
        self.assertNotIn(str(root), json.dumps(result))

    def test_registry_rejects_empty_or_external_model_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model_root = root / "Models"
            model_root.mkdir()
            empty = model_root / "Vision" / "RF-DETR"
            empty.mkdir(parents=True)
            external = root / "external-model"
            external.write_bytes(b"weights")
            with (
                patch.object(adapter_common, "MODEL_ROOT", model_root),
                patch.object(adapter_common, "component", return_value={"id": "rfdetr"}),
                patch.object(adapter_common, "models", return_value=[{"id": "rf", "engine": "RF-DETR", "local_path": str(empty)}]),
            ):
                self.assertIsNone(adapter_common.registered_model("rfdetr", "RF-DETR"))
            with patch.object(adapter_common, "models", return_value=[{"id": "rf", "engine": "RF-DETR", "local_path": str(external)}]):
                self.assertIsNone(adapter_common.registered_model("rfdetr", "RF-DETR"))

    def test_seed_runtime_binding_requires_canonical_root_and_non_reparse_leaves(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = root / "runtime" / "engines" / "voice" / "Seed-VC"
            environment = root / "Environments" / "seed-vc" / "Scripts"
            service.mkdir(parents=True)
            environment.mkdir(parents=True)
            helper = service / "seed_cli.py"
            python = environment / "python.exe"
            helper.write_bytes(b"worker")
            python.write_bytes(b"python")
            with (
                patch.object(adapter_common, "ROOT", root),
                patch.object(adapter_common, "component", return_value={"id": "seed_vc", "path": str(service), "executable": str(python)}),
            ):
                binding = adapter_common.registered_runtime("seed_vc", "seed_cli.py")
            self.assertEqual(binding, (python.resolve(), helper.resolve(), service.resolve()))

            outside = root.parent / "seed-vc-outside"
            outside.mkdir(exist_ok=True)
            (outside / "seed_cli.py").write_bytes(b"worker")
            with patch.object(adapter_common, "component", return_value={"id": "seed_vc", "path": str(outside), "executable": str(python)}):
                self.assertIsNone(adapter_common.registered_runtime("seed_vc", "seed_cli.py"))

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

    def test_rfdetr_capability_exposes_unverified_installed_api_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            python = root / "python.exe"
            helper = root / "detect_cli.py"
            model = root / "weights"
            for path in (python, helper, model):
                path.write_bytes(b"owned")
            with (
                patch.object(rfdetr_adapter, "registered_runtime", return_value=(python, helper, root)),
                patch.object(rfdetr_adapter, "registered_model", return_value=("rf-detr-local", model)),
            ):
                capability = rfdetr_adapter.capability()
        self.assertEqual(capability["worker_contract"], "detect_cli.py.v1")
        self.assertEqual(capability["worker_contract_status"], "unverified_until_smoke")
        self.assertEqual(capability["installed_api"], "not_imported")
        self.assertEqual(capability["status"], "partial")


if __name__ == "__main__":
    unittest.main()
