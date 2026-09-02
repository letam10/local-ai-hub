"""Bounded M4 OCR/Whisper contracts and inline workspace projections."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.modules.ocr.backend import adapter as ocr_adapter
from src.modules.whisper.backend import adapter as whisper_adapter
from src.services.api import core, jobs
from src.shared.schemas.ocr_whisper import (
    OCR_RESULT_SCHEMA,
    WHISPER_TRANSCRIPT_SCHEMA,
    OcrWhisperContractError,
    build_ocr_result,
    build_whisper_transcript,
    normalize_ocr_payload,
    normalize_whisper_payload,
    validate_ocr_result_contract,
    validate_whisper_transcript_contract,
)


ROOT = Path(__file__).resolve().parents[1]


def artifact_id(letter: str = "a") -> str:
    return "artifact_" + letter * 32


class M4OcrWhisperWorkspaceTests(unittest.TestCase):
    def test_ocr_adapter_captures_language_region_page_at_helper_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input.png"
            output = root / "ocr.json"
            source.write_bytes(b"image")
            output.write_bytes(b"json")
            runtime_python = root / "python.exe"
            runtime_helper = root / "paddle_cli.py"
            model = root / "model"
            for path in (runtime_python, runtime_helper, model):
                path.write_bytes(b"fixture")
            source_id = artifact_id()
            worker_result = {
                "status": "completed",
                "files": [str(output)],
                "pages": [{"page_number": 2, "width": 100, "height": 100, "blocks": [{"normalized_box": [0.1, 0.2, 0.8, 0.9], "text": "hello"}]}],
            }
            captured: dict[str, object] = {}

            def run_helper(_command, request, **_kwargs):
                captured.update(request)
                return worker_result

            with (
                patch.object(ocr_adapter, "registered_runtime", return_value=(runtime_python, runtime_helper, root)),
                patch.object(ocr_adapter, "registered_model", return_value=("ocr-local", model)),
                patch.object(ocr_adapter, "resolve_artifact_input", return_value=(source, None)),
                patch.object(ocr_adapter, "describe", return_value={"media_type": "image/png"}),
                patch.object(ocr_adapter, "run_json_worker", side_effect=run_helper),
            ):
                result = ocr_adapter.parse({
                    "source_artifact_id": source_id,
                    "language": "VI",
                    "region": [0.1, 0.2, 0.8, 0.9],
                    "page_number": 2,
                    "output_format": "JSON",
                }, context=object())
            self.assertEqual(result["status"], "completed")
            self.assertEqual(captured["language"], "vi")
            self.assertEqual(captured["normalized_box"], [0.1, 0.2, 0.8, 0.9])
            self.assertEqual(captured["page_number"], 2)
            self.assertEqual(captured["output_format"], "json")
            self.assertNotIn("region", captured)

    def test_payloads_and_result_contracts_are_bounded_and_opaque(self) -> None:
        ocr_payload, error = normalize_ocr_payload({
            "source_artifact_id": artifact_id(),
            "output_format": "MARKDOWN",
            "region": [0.1, 0.2, 0.8, 0.9],
        })
        self.assertIsNone(error)
        self.assertEqual(ocr_payload["output_format"], "markdown")
        self.assertEqual(ocr_payload["normalized_box"], [0.1, 0.2, 0.8, 0.9])

        whisper_payload, error = normalize_whisper_payload({
            "source_artifact_id": artifact_id(),
            "start": "1.5",
            "end": "4.25",
            "device": "CUDA",
            "language": "VI",
        })
        self.assertIsNone(error)
        self.assertEqual(whisper_payload["device"], "cuda")
        self.assertEqual(whisper_payload["language"], "vi")
        self.assertEqual(whisper_payload["start"], 1.5)

        ocr = build_ocr_result(artifact_id(), {
            "pages": [{
                "page_number": 1,
                "width": 1000,
                "height": 500,
                "blocks": [{"bbox": [100, 50, 900, 450], "text": "Xin chào", "confidence": 0.91}],
            }],
        })
        self.assertEqual(ocr["schema_version"], OCR_RESULT_SCHEMA)
        self.assertEqual(ocr["pages"][0]["blocks"][0]["normalized_box"], [0.1, 0.1, 0.9, 0.9])
        self.assertEqual(validate_ocr_result_contract(ocr), ocr)

        transcript = build_whisper_transcript(artifact_id(), {
            "language": "vi",
            "segments": [{"start": 0.0, "end": 1.25, "text": "xin chào"}],
        })
        self.assertEqual(transcript["schema_version"], WHISPER_TRANSCRIPT_SCHEMA)
        self.assertEqual(transcript["duration_ms"], 1250)
        self.assertEqual(transcript["segments"][0]["start_ms"], 0)
        self.assertEqual(validate_whisper_transcript_contract(transcript), transcript)

        invalid_payloads = [
            (normalize_ocr_payload, {"source_artifact_id": r"C:\private\doc.pdf"}),
            (normalize_ocr_payload, {"source_artifact_id": artifact_id(), "region": [0.8, 0.1, 0.2, 0.9]}),
            (normalize_whisper_payload, {"source_artifact_id": artifact_id(), "start": 5, "end": 2}),
            (normalize_whisper_payload, {"source_artifact_id": artifact_id(), "device": "igpu"}),
        ]
        for normalizer, payload in invalid_payloads:
            with self.subTest(payload=payload):
                _normalized, error = normalizer(payload)
                self.assertIsNotNone(error)

        with self.assertRaises(OcrWhisperContractError):
            validate_whisper_transcript_contract({
                "schema_version": WHISPER_TRANSCRIPT_SCHEMA,
                "source_artifact_id": artifact_id(),
                "language": "vi",
                "duration_ms": 1000,
                "segments": [{"start_ms": 0, "end_ms": 2000, "text": "bad"}],
            })

    def test_job_publication_attaches_ocr_and_whisper_artifact_roles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ocr_files = [root / "ocr.txt", root / "ocr.md", root / "ocr.json", root / "ocr.csv", root / "ocr_annotated.png"]
            for path in ocr_files:
                path.write_bytes(b"output")
            ocr_result = {
                "status": "completed",
                "files": [str(path) for path in ocr_files],
                "ocr_result": build_ocr_result(artifact_id(), {"pages": []}),
            }
            ocr_published = [
                {"id": artifact_id("b"), "name": "ocr.txt", "media_type": "text/plain", "provenance": {}},
                {"id": artifact_id("c"), "name": "ocr.md", "media_type": "text/markdown", "provenance": {}},
                {"id": artifact_id("d"), "name": "ocr.json", "media_type": "application/json", "provenance": {}},
                {"id": artifact_id("e"), "name": "ocr.csv", "media_type": "text/csv", "provenance": {}},
                {"id": artifact_id("f"), "name": "ocr_annotated.png", "media_type": "image/png", "provenance": {}},
            ]

            def register_ocr(_paths, *, provenance):
                return [{**item, "provenance": provenance} for item in ocr_published]

            with patch.object(jobs.artifact_store, "register_worker_outputs", side_effect=register_ocr):
                safe_ocr, error = jobs._publish_result(ocr_result, {"id": "job_ocr", "tool": "ocr_document"})
            self.assertIsNone(error)
            self.assertEqual(safe_ocr["ocr_result"]["text_artifact_id"], artifact_id("b"))
            self.assertEqual(safe_ocr["ocr_result"]["markdown_artifact_id"], artifact_id("c"))
            self.assertEqual(safe_ocr["ocr_result"]["json_artifact_id"], artifact_id("d"))
            self.assertEqual(safe_ocr["ocr_result"]["table_artifact_ids"], [artifact_id("e")])
            self.assertEqual(safe_ocr["ocr_result"]["annotated_artifact_ids"], [artifact_id("f")])
            self.assertNotIn(str(root), json.dumps(safe_ocr))

            transcript_file = root / "whisper.json"
            srt_file = root / "whisper.srt"
            transcript_file.write_text("{}", encoding="utf-8")
            srt_file.write_text("1\n", encoding="utf-8")
            whisper_result = {
                "status": "completed",
                "files": [str(transcript_file), str(srt_file)],
                "transcript": build_whisper_transcript(artifact_id(), {"segments": []}),
            }
            whisper_published = [
                {"id": artifact_id("1"), "name": "whisper.json", "media_type": "application/json", "provenance": {}},
                {"id": artifact_id("2"), "name": "whisper.srt", "media_type": "application/x-subrip", "provenance": {}},
            ]

            def register_whisper(_paths, *, provenance):
                return [{**item, "provenance": provenance} for item in whisper_published]

            with patch.object(jobs.artifact_store, "register_worker_outputs", side_effect=register_whisper):
                safe_whisper, error = jobs._publish_result(whisper_result, {"id": "job_whisper", "tool": "transcribe_media"})
            self.assertIsNone(error)
            self.assertEqual(safe_whisper["transcript"]["json_artifact_id"], artifact_id("1"))
            self.assertEqual(safe_whisper["transcript"]["transcript_artifact_id"], artifact_id("1"))
            self.assertEqual(safe_whisper["transcript"]["srt_artifact_id"], artifact_id("2"))
            self.assertNotIn(str(root), json.dumps(safe_whisper))

    def test_adapters_create_inline_contracts_without_exposing_private_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input.png"
            output = root / "ocr.json"
            source.write_bytes(b"image")
            output.write_bytes(b"json")
            source_id = artifact_id()
            runtime_python = root / "python.exe"
            runtime_helper = root / "paddle_cli.py"
            model = root / "model"
            for path in (runtime_python, runtime_helper, model):
                path.write_bytes(b"fixture")
            worker_result = {
                "status": "completed",
                "files": [str(output)],
                "pages": [{"page_number": 1, "width": 100, "height": 100, "blocks": [{"normalized_box": [0, 0, 1, 1], "text": "hello"}]}],
            }
            with (
                patch.object(ocr_adapter, "registered_runtime", return_value=(runtime_python, runtime_helper, root)),
                patch.object(ocr_adapter, "registered_model", return_value=("ocr-local", model)),
                patch.object(ocr_adapter, "resolve_artifact_input", return_value=(source, None)),
                patch.object(ocr_adapter, "describe", return_value={"media_type": "image/png"}),
                patch.object(ocr_adapter, "run_json_worker", return_value=worker_result),
            ):
                result = ocr_adapter.parse({"source_artifact_id": source_id}, context=object())
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["ocr_result"]["schema_version"], OCR_RESULT_SCHEMA)
            self.assertNotIn(str(root), json.dumps(result))

            with patch.object(ocr_adapter, "resolve_artifact_input", return_value=(source, None)), patch.object(ocr_adapter, "describe", return_value={"media_type": "audio/wav"}), patch.object(ocr_adapter, "run_json_worker") as worker:
                invalid = ocr_adapter.parse({"source_artifact_id": source_id})
            worker.assert_not_called()
            self.assertEqual(invalid["code"], "input_media_type_invalid")

            whisper_source = root / "input.wav"
            whisper_source.write_bytes(b"audio")
            whisper_output_root = root / "Output" / "Speech"
            whisper_output_root.mkdir(parents=True)
            token = "a" * 32
            whisper_json = whisper_output_root / f"whisper_{token}.json"
            whisper_srt = whisper_json.with_suffix(".srt")
            whisper_json.write_text(json.dumps({
                "language": "vi",
                "segments": [{"start": 0.0, "end": 1.25, "text": "xin chào"}],
            }), encoding="utf-8")
            whisper_srt.write_text("1\n", encoding="utf-8")
            whisper_python = root / "whisper-python.exe"
            whisper_wrapper = root / "whisper_cli.py"
            whisper_python.write_bytes(b"fixture")
            whisper_wrapper.write_bytes(b"fixture")
            with (
                patch.object(whisper_adapter, "_runtime", return_value=(whisper_python, whisper_wrapper, root)),
                patch.object(whisper_adapter, "_model_id", return_value="whisper-local"),
                patch.object(whisper_adapter, "resolve", return_value=whisper_source),
                patch.object(whisper_adapter, "describe", return_value={"media_type": "audio/wav"}),
                patch.object(whisper_adapter, "run_json_worker", return_value={
                    "status": "completed", "operation": "transcribe_media", "transcript_token": token,
                    "segment_count": 1, "language": "vi", "device": "cpu",
                }),
            ):
                transcript_result = whisper_adapter.transcribe({"source_artifact_id": source_id}, context=object())
            self.assertEqual(transcript_result["status"], "completed")
            self.assertEqual(transcript_result["transcript"]["schema_version"], WHISPER_TRANSCRIPT_SCHEMA)
            self.assertEqual(transcript_result["transcript"]["segments"][0]["end_ms"], 1250)

    def test_core_normalizes_m4_requests_before_queue(self) -> None:
        source_id = artifact_id()
        with (
            patch.object(core, "component_statuses", return_value=[{"id": "paddleocr_vl", "component_status": "partial"}, {"id": "whisper", "component_status": "partial"}]),
            patch.object(core.job_manager, "submit", return_value={"id": "jobv5_" + "a" * 32}) as submit,
            patch.object(core, "get_job", return_value={"id": "jobv5_" + "a" * 32}),
        ):
            status, _response = core.submit_tool("ocr_document", {"source_artifact_id": source_id, "output_format": "JSON"})
            self.assertEqual(status, 202)
            status, _response = core.submit_tool("transcribe_media", {"source_artifact_id": source_id, "device": "CPU", "start": "0", "end": "2"})
            self.assertEqual(status, 400)
            status, _response = core.submit_tool("transcribe_media", {"source_artifact_id": source_id, "start": "0", "end": "2"})
            self.assertEqual(status, 202)
        self.assertEqual(submit.call_count, 2)
        self.assertEqual(submit.call_args_list[0].args[1]["output_format"], "json")
        self.assertEqual(submit.call_args_list[1].args[1]["device"], "cuda")

    def test_rendered_m4_workspaces_are_inline_and_button_only(self) -> None:
        script = r"""
        import { renderPage } from './src/ui/pages.js';
        const base = {
          components: [{id:'paddleocr_vl', component_status:'partial'}, {id:'whisper', component_status:'partial'}],
          tools: [
            {name:'ocr_document', tool_status:'partial', reason:'blocked', action:'review'},
            {name:'transcribe_media', tool_status:'partial', reason:'blocked', action:'review', supported_options:{devices:['cuda'], hardware:'nvidia_rtx4060'}},
            {name:'create_subtitled_video', tool_status:'partial', reason:'blocked', action:'review'},
          ],
          m4: {
            ocr: {resultTab:'text', pdfPage:1, pdfPageCount:0, region:null},
            whisper: {resultTab:'transcript', currentTime:0, duration:0, start:0, end:10},
          },
        };
        for (const [route, required] of [
          ['ocr', ['m4-tool-workspace','data-m4-ocr-stage','data-ocr-page-action','Văn bản','Markdown','Bảng','JSON','Hộp','ocr.result.v1']],
          ['whisper', ['m4-tool-workspace','data-m4-whisper-stage','data-whisper-current','data-whisper-start-range','data-whisper-end-range','Transcript','SRT','JSON','whisper.transcript.v1']]
        ]) {
          const html = renderPage(route, base);
          for (const item of required) if (!html.includes(item)) throw new Error(`${route} missing ${item}`);
          if (html.includes('Choose File') || html.includes('No file chosen')) throw new Error('native picker copy leaked');
          if (!html.includes('data-file-picker-button') || !html.includes('file-picker__input')) throw new Error('button-only picker missing');
          if (!html.includes('m4-workspace__inputs') || !html.includes('m4-workspace__canvas') || !html.includes('m4-workspace__results')) throw new Error('three-zone workspace missing');
          if ([...html.matchAll(/<form[^>]*data-m4-job-form[\s\S]*?<\/form>/g)].some(match => match[0].includes('type="file"'))) throw new Error('file input nested in M4 job form');
          if (route === 'whisper' && (html.includes('<option value="cpu">') || !html.includes('RTX 4060 preflight'))) throw new Error('Whisper device allowlist is not server-owned');
        }
        console.log('ok');
        """
        result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_m4_media_listener_and_route_reopen_contract_is_complete(self) -> None:
        interactive = (ROOT / "src" / "ui" / "features" / "ocr_whisper" / "interactive.js").read_text(encoding="utf-8")
        app = (ROOT / "src" / "ui" / "app.js").read_text(encoding="utf-8")
        self.assertIn('querySelectorAll("[data-m4-whisper-audio], [data-m4-whisper-video]")', interactive)
        self.assertIn('["loadedmetadata", "durationchange"]', interactive)
        self.assertIn('media.addEventListener("timeupdate"', interactive)
        self.assertIn("activeWhisperMedia(workspace)", interactive)
        self.assertIn("abort.abort();", interactive)
        self.assertIn("M3_JOB_POLL_MAX_MS", app)
        self.assertIn("Không thể lấy snapshot job trong thời hạn theo dõi", app)


if __name__ == "__main__":
    unittest.main()
