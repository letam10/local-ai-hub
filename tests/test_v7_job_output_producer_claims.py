"""Bounded producer-to-job-output claim contract tests.

These tests exercise only temporary synthetic files and mocked producer seams;
they never start a worker, server, model, runtime, FFmpeg, or ComfyUI.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.services import artifact_store
from src.services.job_manager.manager import JobContext
from src.shared.utils.adapter_common import (
    requires_server_output_namespace,
    safe_output_namespace,
    server_output_namespace,
)


class _ClaimContext:
    def __init__(self, namespace: Path) -> None:
        self.namespace = namespace
        self.labels: list[str] = []

    def claim_output_namespace(self, label: str) -> Path:
        self.labels.append(label)
        return self.namespace


class V7JobOutputProducerClaimsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.output_root = root / "Output"
        self.output_root.mkdir()
        self.index_path = root / "Config" / "artifacts.json"
        self.index_path.parent.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _patch_store(self):
        return (
            patch.object(artifact_store, "OUTPUT_ROOT", self.output_root),
            patch.object(artifact_store, "INDEX_PATH", self.index_path),
        )

    def test_manager_issues_private_namespace_and_children_are_owned(self) -> None:
        job_id = "jobv5_" + "1" * 32
        with self._patch_store()[0], self._patch_store()[1]:
            self.assertIsNotNone(artifact_store.begin_job_output_scope(job_id))
            context = JobContext(job_id)
            namespace = context.claim_output_namespace("whisper")
            self.assertIsNotNone(namespace)
            assert namespace is not None
            self.assertEqual(namespace.parent.name, ".job-output-scopes")
            self.assertEqual(safe_output_namespace(self.output_root.parent, namespace), namespace.resolve())
            self.assertTrue(requires_server_output_namespace(context))
            self.assertEqual(server_output_namespace(context, "whisper"), namespace)

            json_path = namespace / "transcript.json"
            srt_path = namespace / "transcript.srt"
            self.assertEqual(artifact_store.claim_job_output_path(job_id, json_path)["status"], "claimed")
            self.assertEqual(artifact_store.claim_job_output_path(job_id, srt_path)["status"], "claimed")
            json_path.write_text("{}", encoding="utf-8")
            srt_path.write_text("1\n", encoding="utf-8")
            prepared = artifact_store.prepare_job_output_scope(
                job_id,
                {"status": "completed", "files": [str(json_path), str(srt_path)]},
            )
            self.assertEqual(prepared["status"], "owned")
            self.assertEqual(prepared["owned_count"], 2)

    def test_namespace_requires_child_attestation_and_preserves_foreign_child(self) -> None:
        job_id = "jobv5_" + "2" * 32
        with self._patch_store()[0], self._patch_store()[1]:
            self.assertIsNotNone(artifact_store.begin_job_output_scope(job_id))
            namespace = artifact_store.claim_job_output_namespace(job_id, "sam2")
            self.assertIsNotNone(namespace)
            assert namespace is not None
            owned = namespace / "mask.png"
            foreign = namespace / "foreign.png"
            owned.write_bytes(b"attested producer child")
            self.assertEqual(JobContext(job_id).attest_output(owned)["status"], "attested")
            foreign.write_bytes(b"foreign child preserve")
            prepared = artifact_store.prepare_job_output_scope(
                job_id,
                {"status": "completed", "outputs": [str(owned), str(foreign)]},
            )
            self.assertEqual(prepared["status"], "manual_review")
            self.assertEqual(prepared["owned_count"], 1)
            self.assertEqual(prepared["ambiguous_count"], 1)
            finalized = artifact_store.finalize_job_output_scope(job_id, terminal_state="failed")
            self.assertEqual(finalized["status"], "manual_review")
            self.assertFalse(owned.exists())
            self.assertEqual(foreign.read_bytes(), b"foreign child preserve")

    def test_namespace_claim_save_failure_has_no_usable_namespace(self) -> None:
        job_id = "jobv5_" + "3" * 32
        with self._patch_store()[0], self._patch_store()[1]:
            self.assertIsNotNone(artifact_store.begin_job_output_scope(job_id))
            with patch.object(artifact_store, "_save_job_output_scopes", return_value=False):
                self.assertIsNone(artifact_store.claim_job_output_namespace(job_id, "producer"))
            scope = artifact_store.inspect_job_output_scope(job_id)
            self.assertIsNotNone(scope)
            assert scope is not None
            self.assertEqual(scope["namespace_count"], 0)
            self.assertFalse((self.output_root / ".job-output-scopes").exists() and any((self.output_root / ".job-output-scopes").iterdir()))

    def test_context_without_manager_claim_contract_is_not_promoted(self) -> None:
        self.assertIsNone(server_output_namespace(None, "producer"))
        self.assertIsNone(server_output_namespace(object(), "producer"))
        self.assertFalse(requires_server_output_namespace(None))
        self.assertFalse(requires_server_output_namespace(object()))

    def test_sam2_adapter_passes_only_manager_namespace_to_worker(self) -> None:
        from src.modules.sam2.backend import adapter

        source = self.output_root / "input.png"
        source.write_bytes(b"synthetic")
        namespace = self.output_root / ".job-output-scopes" / "synthetic-sam2"
        namespace.mkdir(parents=True)
        context = _ClaimContext(namespace)
        captured: dict[str, object] = {}

        def fake_worker(_command, request, **_kwargs):
            captured.update(request)
            return {"status": "completed", "operation": "segment_image", "outputs": [str(namespace / "mask.png")]}

        with (
            patch.object(adapter, "_runtime_contract", return_value=(Path(__file__), self.output_root, source)),
            patch.object(adapter, "WORKER", Path(__file__)),
            patch.object(adapter, "_source_artifact", return_value=(source, None)),
            patch.object(adapter, "run_json_worker", side_effect=fake_worker),
        ):
            result = adapter.segment_image({"source_artifact_id": "artifact_" + "a" * 32}, context=context)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(captured["output_namespace"], str(namespace))
        self.assertEqual(context.labels, ["sam2"])

    def test_required_producers_reference_the_namespace_seam(self) -> None:
        adapter_paths = (
            "src/modules/animesr/backend/adapter.py",
            "src/modules/practical_rife/backend/adapter.py",
            "src/modules/real_esrgan/backend/adapter.py",
            "src/modules/sam2/backend/adapter.py",
            "src/modules/media_editor/backend/adapter.py",
            "src/modules/whisper/backend/adapter.py",
            "src/modules/vision/backend/omniparser_adapter.py",
            "src/modules/vision/backend/rfdetr_adapter.py",
            "src/modules/vision/backend/groundingdino_adapter.py",
            "src/modules/ocr/backend/adapter.py",
            "src/modules/voice/backend/qwen3_tts_adapter.py",
            "src/modules/voice/backend/seed_vc_adapter.py",
            "src/modules/image_generation/backend/comfyui.py",
        )
        root = Path(__file__).resolve().parents[1]
        for relative in adapter_paths:
            with self.subTest(relative=relative):
                source = (root / relative).read_text(encoding="utf-8")
                self.assertIn("server_output_namespace", source)
        for relative in (
            "src/modules/animesr/backend/worker.py",
            "src/modules/practical_rife/backend/worker.py",
            "src/modules/real_esrgan/backend/worker.py",
            "src/modules/sam2/backend/worker.py",
            "Services/Whisper/whisper_cli.py",
            "Services/Whisper/transcribe_japanese_clip.py",
        ):
            with self.subTest(relative=relative):
                source = (root / relative).read_text(encoding="utf-8")
                self.assertIn("output_namespace", source)

    def test_scope_corruption_is_fixed_and_path_free(self) -> None:
        job_id = "jobv5_" + "4" * 32
        manifest = self.index_path.with_name(".job_output_scopes.json")
        with self._patch_store()[0], self._patch_store()[1]:
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text(json.dumps({
                "schema_version": "job-output-scope.v1",
                "records": {job_id: {
                    "job_id": job_id,
                    "job_fingerprint": "a" * 64,
                    "state": "open",
                    "snapshot_complete": True,
                    "baseline": {},
                    "claims": [],
                    "namespaces": [],
                    "candidates": {},
                    "unknown_field": "must-not-echo",
                }},
            }), encoding="utf-8")
            self.assertIsNone(artifact_store.inspect_job_output_scope(job_id))
            self.assertEqual(artifact_store.reconcile_job_output_scopes(active_job_ids=set()), {"cleaned": 0, "manual_review": 0})


if __name__ == "__main__":
    unittest.main()
