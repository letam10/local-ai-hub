"""Static contracts for opaque SAM2 input and output-only publication."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.modules.sam2.backend import adapter, worker
from src.modules.vision.backend import groundingdino_adapter
from src.services.api import core, jobs


def _artifact_id(letter: str = "a") -> str:
    return "artifact_" + letter * 32


class Sam2ArtifactContractTests(unittest.TestCase):
    def test_adapter_resolves_typed_artifact_and_job_manager_publicizes_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime" / "engines" / "vision" / "SAM2"
            python = root / "Environments" / "sam2" / "Scripts" / "python.exe"
            checkpoint = runtime / "checkpoints" / "sam2.1_hiera_small.pt"
            source = root / "Temp" / "uploads" / "hub-upload-source.png"
            mask = root / "Output" / "SAM2" / "sam2_test" / "mask.png"
            preview = mask.with_name("overlay.png")
            for path in (python, checkpoint, source, mask, preview):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture")
            captured: dict[str, object] = {}

            def fake_worker(command, request, **kwargs):
                captured["command"] = command
                captured["request"] = request
                captured["timeout"] = kwargs["timeout_seconds"]
                return {"status": "completed", "operation": "segment_from_points", "outputs": [str(mask), str(preview)]}

            with (
                patch.object(adapter, "_runtime", return_value=(python, runtime)),
                patch.object(adapter, "local_root", return_value=root),
                patch.object(adapter, "resolve_artifact_input", return_value=(source, None)),
                patch.object(adapter, "describe", return_value={"id": _artifact_id(), "media_type": "image/png"}),
                patch.object(adapter, "run_json_worker", side_effect=fake_worker),
            ):
                normalized = adapter.segment_from_points({"source_artifact_id": _artifact_id(), "timeout_seconds": 99_999}, context=object())

        self.assertEqual(captured["request"].get("path"), str(source))
        self.assertNotIn("output_root", captured["request"])
        self.assertEqual(captured["timeout"], 1200.0)
        self.assertEqual(normalized["outputs"], [str(mask), str(preview)])
        self.assertNotIn("mask", normalized)
        self.assertNotIn("preview", normalized)

        seen: list[Path] = []

        def register(paths, *, provenance):
            seen.extend(paths)
            return [
                {"id": _artifact_id("b"), "provenance": provenance},
                {"id": _artifact_id("c"), "provenance": provenance},
            ]

        record = {"id": "job_20260816_000000_deadbeef", "tool": "segment_from_points"}
        with patch.object(jobs.artifact_store, "register_worker_outputs", side_effect=register):
            safe, error = jobs._publish_result(normalized, record)

        self.assertIsNone(error)
        self.assertEqual(seen, [mask, preview])
        self.assertEqual([item["id"] for item in safe["artifacts"]], [_artifact_id("b"), _artifact_id("c")])
        self.assertEqual(safe["artifacts"][0]["provenance"]["job_id"], record["id"])
        self.assertNotIn(str(root), json.dumps(safe))

    def test_adapter_rejects_raw_path_or_wrong_media_before_worker_launch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            python = root / "Environments" / "sam2" / "Scripts" / "python.exe"
            checkpoint = runtime / "checkpoints" / "sam2.1_hiera_small.pt"
            for path in (python, checkpoint):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture")
            with (
                patch.object(adapter, "_runtime", return_value=(python, runtime)),
                patch.object(adapter, "local_root", return_value=root),
                patch.object(adapter, "run_json_worker") as launch,
            ):
                rejected = adapter.segment_image({"source_artifact_id": _artifact_id(), "path": r"C:\\private\\input.png"})
            launch.assert_not_called()
            self.assertEqual(rejected["code"], "raw_input_forbidden")
            self.assertNotIn("C:\\private", json.dumps(rejected))

            source = root / "Temp" / "uploads" / "source.mp4"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"fixture")
            with (
                patch.object(adapter, "_runtime", return_value=(python, runtime)),
                patch.object(adapter, "local_root", return_value=root),
                patch.object(adapter, "resolve_artifact_input", return_value=(source, None)),
                patch.object(adapter, "describe", return_value={"id": _artifact_id(), "media_type": "video/mp4"}),
                patch.object(adapter, "run_json_worker") as launch,
            ):
                wrong_media = adapter.segment_image({"source_artifact_id": _artifact_id()})
            launch.assert_not_called()
            self.assertEqual(wrong_media["code"], "artifact_media_type_invalid")

    def test_composed_text_flow_keeps_source_as_opaque_artifact_id(self) -> None:
        captured: dict[str, object] = {}

        def segment(payload, context):
            captured["payload"] = payload
            captured["context"] = context
            return {"status": "completed"}

        with (
            patch.object(
                groundingdino_adapter,
                "ground",
                return_value={"status": "completed", "grounded": [{"box_normalized_cxcywh": [0.5, 0.5, 0.2, 0.2]}]},
            ),
            patch.object(adapter, "segment_from_box", side_effect=segment),
        ):
            result = core._run_operation("segment_from_text", {"source_artifact_id": _artifact_id(), "prompt": "cat"}, object())

        self.assertEqual(result["status"], "completed")
        self.assertEqual(captured["payload"]["source_artifact_id"], _artifact_id())
        self.assertNotIn("path", captured["payload"])

    def test_worker_rejects_reparse_input_before_model_load_or_output_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime" / "engines" / "vision" / "SAM2"
            checkpoint = runtime / "checkpoints" / "sam2.1_hiera_small.pt"
            uploads = root / "Temp" / "uploads"
            outside = root / "outside.png"
            for path in (checkpoint, outside):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture")
            uploads.mkdir(parents=True)
            source = uploads / "linked.png"
            try:
                source.symlink_to(outside)
            except OSError:
                self.skipTest("Symlink fixture is unavailable on this Windows account.")
            (root / "Output").mkdir()
            request = {"runtime": str(runtime), "checkpoint": str(checkpoint), "path": str(source), "operation": "segment_image"}
            with (
                patch.dict(os.environ, {"LOCALAIHUB_ROOT": str(root)}, clear=False),
                patch.object(worker, "_build_predictor", side_effect=AssertionError("model load must not start")),
            ):
                result = worker._segment(request)
            output_created = (root / "Output" / "SAM2").exists()

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "sam2_path_contract_invalid")
        self.assertFalse(output_created)

    def test_worker_rejects_reparse_output_root_before_model_load(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime" / "engines" / "vision" / "SAM2"
            checkpoint = runtime / "checkpoints" / "sam2.1_hiera_small.pt"
            source = root / "Temp" / "uploads" / "source.png"
            for path in (checkpoint, source):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture")
            output = root / "Output"
            output.mkdir()
            outside = root / "outside-output"
            outside.mkdir()
            try:
                (output / "SAM2").symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("Symlink fixture is unavailable on this Windows account.")
            request = {"runtime": str(runtime), "checkpoint": str(checkpoint), "path": str(source), "operation": "segment_image"}
            with (
                patch.dict(os.environ, {"LOCALAIHUB_ROOT": str(root)}, clear=False),
                patch.object(worker, "_build_predictor", side_effect=AssertionError("model load must not start")),
            ):
                result = worker._segment(request)

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "sam2_path_contract_invalid")

    def test_worker_metadata_aliases_cannot_bypass_artifact_publication(self) -> None:
        self.assertEqual(jobs._output_candidates({"mask": "mask.png", "preview": "overlay.png"}), [])
        self.assertEqual(jobs._output_candidates({"outputs": ["mask.png", "overlay.png"]}), [Path("mask.png"), Path("overlay.png")])
        capability = adapter.capability()
        self.assertNotEqual(capability.get("status"), "operational")

    def test_missing_sam2_output_fails_publication_and_cannot_be_evidence(self) -> None:
        record = {"id": "job_20260816_000000_deadbeef", "tool": "segment_image"}
        with patch.object(jobs.artifact_store, "register_worker_outputs", return_value=[]):
            safe, error = jobs._publish_result(
                {"status": "completed", "operation": "segment_image", "outputs": ["missing-mask.png"]},
                record,
            )

        self.assertEqual(error, "output_publish")
        self.assertEqual(safe["status"], "failed")
        self.assertNotIn("artifacts", safe)


if __name__ == "__main__":
    unittest.main()
