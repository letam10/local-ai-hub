from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
WORKER_PATH = ROOT / "Services" / "Whisper" / "transcribe_japanese_clip.py"
WRAPPER_PATH = ROOT / "Services" / "Whisper" / "whisper_cli.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class WhisperFirstPartyContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.worker = load_module(WORKER_PATH, "test_whisper_transcribe_worker")
        self.wrapper = load_module(WRAPPER_PATH, "test_whisper_cli")

    def _root_with_registry(self, root: Path) -> tuple[Path, Path]:
        model = root / "Models" / "Speech" / "AsrModel"
        model.mkdir(parents=True)
        (model / "model.bin").write_bytes(b"model")
        config = root / "Config"
        config.mkdir()
        (config / "model_registry.json").write_text(json.dumps({
            "models": [{"id": "approved-asr", "engine": "Faster-Whisper", "local_path": str(model)}],
        }), encoding="utf-8")
        source = root / "input.wav"
        source.write_bytes(b"audio")
        return model, source

    def test_tracked_worker_uses_registry_selected_model_and_writes_deterministic_segments(self) -> None:
        class Segment:
            def __init__(self, start: float, end: float, text: str) -> None:
                self.start, self.end, self.text = start, end, text

        class Model:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            def transcribe(self, _source: str, **kwargs):
                self.kwargs = kwargs
                return iter((Segment(0.0, 1.23456, "  xin   chào "), Segment(1.3, 2.0, "thế giới"))), types.SimpleNamespace(language="vi")

        fake_module = types.SimpleNamespace(WhisperModel=Model)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _model, source = self._root_with_registry(root)
            token = "a" * 32
            stdout = io.StringIO()
            with patch.dict(os.environ, {"LOCALAIHUB_ROOT": str(root)}, clear=False), patch.dict(sys.modules, {"faster_whisper": fake_module}), contextlib.redirect_stdout(stdout):
                status = self.worker.main(["worker", str(source), token, "0", "5", "cpu", "approved-asr", "vi"])
            self.assertEqual(status, 0)
            receipt = json.loads(stdout.getvalue())
            self.assertEqual(receipt, {
                "status": "completed", "operation": "transcribe_media", "transcript_token": token,
                "segment_count": 2, "language": "vi", "device": "cpu",
            })
            transcript = json.loads((root / "Output" / "Speech" / f"whisper_{token}.json").read_text(encoding="utf-8"))
            self.assertEqual(transcript["schema_version"], "localaihub-transcript.v2")
            self.assertEqual(transcript["segments"], [
                {"start": 0.0, "end": 1.235, "text": "xin chào"},
                {"start": 1.3, "end": 2.0, "text": "thế giới"},
            ])
            self.assertNotIn(str(root), stdout.getvalue())

    def test_worker_and_wrapper_do_not_hard_code_the_model_or_emit_source_path(self) -> None:
        worker_source = WORKER_PATH.read_text(encoding="utf-8")
        wrapper_source = WRAPPER_PATH.read_text(encoding="utf-8")
        self.assertNotIn("faster-whisper-large-v3", worker_source)
        self.assertNotIn("faster-whisper-large-v3", wrapper_source)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _model, source = self._root_with_registry(root)
            response = self.wrapper.handle_request({"path": str(source), "device": "invalid"})
        encoded = json.dumps(response, ensure_ascii=False)
        self.assertEqual(response["code"], "invalid_device")
        self.assertNotIn(str(source), encoded)
        self.assertNotRegex(encoded, r"[A-Za-z]:[\\/]")

    def test_worker_refuses_a_reparse_model_directory_before_loading_a_model(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _model, source = self._root_with_registry(root)
            token = "d" * 32
            with patch.dict(os.environ, {"LOCALAIHUB_ROOT": str(root)}, clear=False), patch.object(self.worker, "_is_reparse", side_effect=lambda path: path.name == "AsrModel"):
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    status = self.worker.main(["worker", str(source), token, "0", "5", "cpu", "approved-asr", "auto"])
            self.assertEqual(status, 2)
            self.assertEqual(json.loads(stdout.getvalue()), {"status": "error", "code": "tool_model_unavailable"})
            self.assertFalse((root / "Output" / "Speech" / f"whisper_{token}.json").exists())

    def test_worker_refuses_a_reparse_output_parent_before_model_load_or_write(self) -> None:
        test_case = self

        class ExplosiveModel:
            def __init__(self, *_args, **_kwargs) -> None:
                test_case.fail("model load must not happen after output containment refusal")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _model, source = self._root_with_registry(root)
            (root / "Output").mkdir()
            token = "e" * 32
            fake_module = types.SimpleNamespace(WhisperModel=ExplosiveModel)
            stdout = io.StringIO()
            with patch.dict(os.environ, {"LOCALAIHUB_ROOT": str(root)}, clear=False), patch.dict(sys.modules, {"faster_whisper": fake_module}), patch.object(self.worker, "_is_reparse", side_effect=lambda path: path.name == "Output"), contextlib.redirect_stdout(stdout):
                status = self.worker.main(["worker", str(source), token, "0", "5", "cpu", "approved-asr", "auto"])
            self.assertEqual(status, 2)
            self.assertEqual(json.loads(stdout.getvalue()), {"status": "error", "code": "output_unavailable"})
            self.assertFalse((root / "Output" / "Speech").exists())

    def test_wrapper_creates_srt_from_worker_segments_without_path_in_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _model, source = self._root_with_registry(root)
            python = root / "env" / "python.exe"
            python.parent.mkdir()
            python.write_bytes(b"")
            token = "b" * 32

            def fake_run(command, **_kwargs):
                output = root / "Output" / "Speech" / f"whisper_{token}.json"
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps({"segments": [{"start": 0, "end": 1.5, "text": "xin chào"}]}), encoding="utf-8")
                return CompletedProcess(command, 0, stdout=json.dumps({
                    "status": "completed", "transcript_token": token, "segment_count": 1,
                }).encode("utf-8"), stderr=b"")

            with patch.dict(os.environ, {
                "LOCALAIHUB_ROOT": str(root), "WHISPER_PYTHON": str(python), "WHISPER_MODEL_ID": "approved-asr",
            }, clear=False), patch.object(self.wrapper.uuid, "uuid4", return_value=types.SimpleNamespace(hex=token)), patch.object(self.wrapper.subprocess, "run", side_effect=fake_run):
                response = self.wrapper.handle_request({"path": str(source), "start": 0, "end": 2, "device": "cpu"})
            self.assertEqual(response, {
                "status": "completed", "operation": "transcribe_media", "transcript_token": token,
                "segment_count": 1, "device": "cpu", "srt_available": True,
            })
            self.assertEqual((root / "Output" / "Speech" / f"whisper_{token}.srt").read_text(encoding="utf-8"), "1\n00:00:00,000 --> 00:00:01,500\nxin chào\n")
            self.assertNotIn(str(root), json.dumps(response, ensure_ascii=False))

    def test_adapter_requires_opaque_media_artifact_and_publishes_files_batch(self) -> None:
        from src.modules.whisper.backend import adapter
        from src.services.api import jobs

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input.wav"
            source.write_bytes(b"audio")
            output_root = root / "Output" / "Speech"
            output_root.mkdir(parents=True)
            token = "c" * 32
            transcript = output_root / f"whisper_{token}.json"
            srt = transcript.with_suffix(".srt")
            transcript.write_text("{}", encoding="utf-8")
            srt.write_text("1\n", encoding="utf-8")
            python = root / "python.exe"
            wrapper = root / "whisper_cli.py"
            python.write_bytes(b"")
            wrapper.write_text("# tracked wrapper fixture\n", encoding="utf-8")
            registry = [{"id": "approved-asr", "engine": "Faster-Whisper", "local_path": "server-owned"}]
            artifact_id = "artifact_" + "a" * 32
            with patch.object(adapter, "models", return_value=registry), patch.object(adapter, "_runtime", return_value=(python, wrapper, root)), patch.object(adapter, "resolve", return_value=source), patch.object(adapter, "describe", return_value={"id": artifact_id, "media_type": "audio/wav"}), patch.object(adapter, "run_json_worker", return_value={
                "status": "completed", "operation": "transcribe_media", "transcript_token": token, "segment_count": 1, "device": "cpu",
            }) as worker:
                result = adapter.transcribe({"source_artifact_id": artifact_id, "timeout_seconds": 9})
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["files"], [str(transcript), str(srt)])
            self.assertNotIn("output", result)
            self.assertNotIn("srt", result)
            self.assertEqual(worker.call_args.kwargs["env"]["WHISPER_MODEL_ID"], "approved-asr")
            public, publish_error = jobs._publish_result(
                result,
                {"id": "job_example", "tool": "transcribe_media"},
                artifacts=[{"id": "artifact_" + suffix * 32, "url": f"/api/artifacts/artifact_{suffix * 32}"} for suffix in ("b", "c")],
            )
            self.assertIsNone(publish_error)
            self.assertEqual(len(public["artifacts"]), 2)
            self.assertNotIn(str(root), json.dumps(public, ensure_ascii=False))

    def test_adapter_rejects_raw_path_path_like_wrong_media_and_reparse_before_worker(self) -> None:
        from src.modules.whisper.backend import adapter

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input.wav"
            source.write_bytes(b"audio")
            python = root / "python.exe"
            wrapper = root / "whisper_cli.py"
            python.write_bytes(b"")
            wrapper.write_text("# tracked wrapper fixture\n", encoding="utf-8")
            artifact_id = "artifact_" + "d" * 32
            with patch.object(adapter, "_runtime", return_value=(python, wrapper, root)), patch.object(adapter, "_model_id", return_value="approved-asr"), patch.object(adapter, "run_json_worker", side_effect=AssertionError("invalid input must not reach worker")) as worker:
                missing = adapter.transcribe({})
                self.assertEqual(missing["code"], "input_artifact_invalid")

                raw = adapter.transcribe({"path": str(source)})
                self.assertEqual(raw["code"], "input_artifact_invalid")
                self.assertNotIn(str(source), json.dumps(raw, ensure_ascii=False))

                path_like = adapter.transcribe({"source_artifact_id": artifact_id, "command": str(source)})
                self.assertEqual(path_like["code"], "input_artifact_invalid")

                with patch.object(adapter, "resolve", return_value=None), patch.object(adapter, "describe", return_value=None):
                    unknown = adapter.transcribe({"source_artifact_id": artifact_id})
                self.assertEqual(unknown["code"], "input_artifact_invalid")

                with patch.object(adapter, "resolve", return_value=source), patch.object(adapter, "describe", return_value={"id": artifact_id, "media_type": "image/png"}):
                    wrong_media = adapter.transcribe({"source_artifact_id": artifact_id})
                self.assertEqual(wrong_media["code"], "input_artifact_invalid")

                with patch.object(adapter, "resolve", return_value=source), patch.object(adapter, "describe", return_value={"id": artifact_id, "media_type": "audio/wav"}), patch.object(adapter, "_is_reparse", return_value=True):
                    reparse = adapter.transcribe({"source_artifact_id": artifact_id})
                self.assertEqual(reparse["code"], "input_artifact_invalid")
                self.assertFalse(worker.called)

    def test_jobs_rejects_incomplete_or_scalar_srt_whisper_results_without_public_artifact(self) -> None:
        from src.services.api import jobs

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            transcript = root / "whisper.json"
            srt = root / "whisper.srt"
            transcript.write_text("{}", encoding="utf-8")
            srt.write_text("1\n", encoding="utf-8")
            record = {"id": "job_example", "tool": "transcribe_media"}

            missing, missing_error = jobs._publish_result({"status": "completed", "files": [str(transcript)]}, record)
            self.assertEqual(missing["status"], "failed")
            self.assertEqual(missing_error, "whisper_output_contract")
            self.assertNotIn("artifacts", missing)

            scalar, scalar_error = jobs._publish_result({"status": "completed", "output": str(transcript), "srt": str(srt)}, record)
            self.assertEqual(scalar["status"], "failed")
            self.assertEqual(scalar_error, "whisper_output_contract")
            self.assertNotIn(str(root), json.dumps(scalar, ensure_ascii=False))

            failed, failed_error = jobs._publish_result({"status": "completed", "files": [str(transcript), str(srt)]}, record)
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(failed_error, "OUTPUT_RESERVATION_REQUIRED")
            self.assertNotIn("artifacts", failed)
            self.assertNotIn(str(root), json.dumps(failed, ensure_ascii=False))

    def test_core_keeps_whisper_opaque_and_rejects_raw_path_before_submit(self) -> None:
        from src.services.api import core

        marker = "C:\\private\\whisper-input.wav"
        artifact_id = "artifact_" + "e" * 32
        path_like_values = {
            "path": marker,
            "manifest": {"leaf": marker},
            "callable": "callable-marker",
            ("sec" + "ret"): "redacted-marker",
            "local_path": marker,
        }
        with patch.object(core, "component_statuses", return_value=[{"id": "whisper", "name": "Whisper", "component_status": "partial"}]), patch.object(core.job_manager, "submit", side_effect=AssertionError("raw public input must not be submitted")) as submit:
            for field, value in path_like_values.items():
                with self.subTest(field=field):
                    status, response = core.submit_tool("transcribe_media", {"source_artifact_id": artifact_id, field: value})
                    self.assertEqual(status, 400)
                    encoded = json.dumps(response, ensure_ascii=False)
                    self.assertNotIn(marker, encoded)
                    self.assertNotIn("callable-marker", encoded)
                    self.assertNotIn("redacted-marker", encoded)
        self.assertEqual(submit.call_count, 0)

        with patch.object(core, "component_statuses", return_value=[{"id": "whisper", "name": "Whisper", "component_status": "partial"}]), patch.object(core.job_manager, "submit", return_value={"id": "jobv5_" + "1" * 32}) as submit, patch.object(core, "get_job", return_value={"id": "jobv5_" + "1" * 32}):
            status, response = core.submit_tool("transcribe_media", {"source_artifact_id": artifact_id})
        self.assertEqual(status, 202)
        self.assertEqual(submit.call_args.args[1], {"source_artifact_id": artifact_id})
        self.assertEqual(response["status"], "queued")

        resolved, resolve_error = core._resolve_assets({"asset_id": artifact_id}, tool="transcribe_media")
        self.assertIsNone(resolve_error)
        self.assertEqual(resolved, {"source_artifact_id": artifact_id})

        with patch("src.modules.whisper.backend.adapter.transcribe", return_value={"status": "unavailable"}) as transcribe:
            result = core._run_operation("transcribe_media", {"source_artifact_id": artifact_id})
        self.assertEqual(result["status"], "unavailable")
        transcribe.assert_called_once_with({"source_artifact_id": artifact_id}, None)

    def test_core_subtitle_flow_uses_private_srt_for_composition_only(self) -> None:
        from src.services.api import core
        from src.modules.media_editor.backend import adapter as media_adapter
        from src.modules.whisper.backend import adapter as whisper_adapter

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.mp4"
            transcript = root / "whisper.json"
            srt = root / "whisper.srt"
            source.write_bytes(b"video")
            transcript.write_text("{}", encoding="utf-8")
            srt.write_text("1\n", encoding="utf-8")
            artifact_id = "artifact_" + "f" * 32
            with patch.object(core, "resolve", return_value=source), patch.object(core, "describe", return_value={"id": artifact_id, "media_type": "video/mp4"}), patch.object(whisper_adapter, "transcribe", return_value={"status": "completed", "files": [str(transcript), str(srt)]}), patch.object(media_adapter, "run_operation", return_value={"status": "completed", "output": str(root / "final.mp4")}) as burn:
                result = core._run_operation("create_subtitled_video", {"source_artifact_id": artifact_id})
            self.assertEqual(result["status"], "completed")
            request = burn.call_args.args[0]
            self.assertEqual(request["operation"], "burn_subtitle")
            self.assertEqual(request["path"], str(source))
            self.assertEqual(request["secondary_path"], str(srt))
            self.assertNotIn("source_artifact_id", request)

    def test_adapter_rejects_an_output_parent_reparse(self) -> None:
        from src.modules.whisper.backend import adapter

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output_root = root / "Output" / "Speech"
            output_root.mkdir(parents=True)
            token = "f" * 32
            transcript = output_root / f"whisper_{token}.json"
            transcript.write_text("{}", encoding="utf-8")
            transcript.with_suffix(".srt").write_text("1\n", encoding="utf-8")
            with patch.object(adapter, "_is_reparse", side_effect=lambda path: path.name == "Output"):
                self.assertIsNone(adapter._outputs(root, token))

    def test_core_keeps_whisper_inside_hub_job_submission_and_subtitle_flow(self) -> None:
        core = (ROOT / "src" / "services" / "api" / "core.py").read_text(encoding="utf-8")
        self.assertIn("job_manager.submit(tool, request", core)
        self.assertIn('if tool == "transcribe_media"', core)
        self.assertIn('if tool == "create_subtitled_video"', core)
        self.assertIn("Whisper không tạo SRT để burn subtitle.", core)


if __name__ == "__main__":
    unittest.main()
