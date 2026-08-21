"""Synthetic tests for the opaque V7 job-output reservation protocol."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.services import artifact_store


class V7JobOutputReservationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.output_root = root / "Output"
        self.output_root.mkdir()
        self.index_path = root / "Config" / "artifacts.json"
        self.index_path.parent.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _patch_store(self):
        return patch.object(artifact_store, "OUTPUT_ROOT", self.output_root), patch.object(artifact_store, "INDEX_PATH", self.index_path)

    def test_token_attestation_owns_only_attested_child_and_preserves_foreign_child(self) -> None:
        job_id = "jobv5_" + "1" * 32
        patches = self._patch_store()
        with patches[0], patches[1]:
            self.assertIsNotNone(artifact_store.begin_job_output_scope(job_id))
            reservation = artifact_store.reserve_job_output_namespace(
                job_id,
                "sam2",
                expected_patterns=["*.png"],
                max_children=2,
            )
            self.assertIsNotNone(reservation)
            assert reservation is not None
            namespace = Path(str(reservation["path"]))
            owned = namespace / "mask.png"
            foreign = namespace / "foreign.png"
            owned.write_bytes(b"reserved producer child")
            foreign.write_bytes(b"foreign child")
            self.assertEqual(artifact_store.attest_job_output_reservation(job_id, reservation["token"], owned)["status"], "attested")
            resolved = artifact_store.resolve_job_output_reservations(
                job_id,
                {"status": "completed", "output_reservations": [{"token": reservation["token"], "field": "files"}]},
            )
            self.assertIsNotNone(resolved)
            assert resolved is not None
            prepared = artifact_store.prepare_job_output_scope(job_id, resolved)
            self.assertEqual(prepared["status"], "owned")
            finalized = artifact_store.finalize_job_output_scope(job_id, resolved, terminal_state="failed")
            self.assertEqual(finalized["status"], "manual_review")
            self.assertFalse(owned.exists())
            self.assertEqual(foreign.read_bytes(), b"foreign child")

    def test_bare_path_without_token_is_manual_review_and_never_deleted(self) -> None:
        job_id = "jobv5_" + "2" * 32
        target = self.output_root / "foreign.bin"
        target.write_bytes(b"unclaimed")
        patches = self._patch_store()
        with patches[0], patches[1]:
            self.assertIsNotNone(artifact_store.begin_job_output_scope(job_id))
            prepared = artifact_store.prepare_job_output_scope(job_id, {"status": "completed", "output": str(target)})
            self.assertEqual(prepared["status"], "manual_review")
            finalized = artifact_store.finalize_job_output_scope(job_id, {"status": "completed", "output": str(target)}, terminal_state="cancelled")
            self.assertEqual(finalized["status"], "manual_review")
            self.assertEqual(target.read_bytes(), b"unclaimed")

    def test_token_cannot_be_reused_across_jobs_or_wrong_producer(self) -> None:
        first = "jobv5_" + "3" * 32
        second = "jobv5_" + "4" * 32
        patches = self._patch_store()
        with patches[0], patches[1]:
            self.assertIsNotNone(artifact_store.begin_job_output_scope(first))
            self.assertIsNotNone(artifact_store.begin_job_output_scope(second))
            reservation = artifact_store.reserve_job_output(first, "video", suffix=".mp4")
            self.assertIsNotNone(reservation)
            assert reservation is not None
            target = Path(str(reservation["path"]))
            target.write_bytes(b"one")
            self.assertEqual(artifact_store.attest_job_output_reservation(second, reservation["token"], target)["status"], "unavailable")
            self.assertEqual(artifact_store.attest_job_output_reservation(first, reservation["token"], target)["status"], "attested")

    def test_incomplete_snapshot_refuses_reservation(self) -> None:
        job_id = "jobv5_" + "5" * 32
        patches = self._patch_store()
        with patches[0], patches[1], patch.object(artifact_store, "_scope_snapshot", return_value=({}, False)):
            self.assertIsNotNone(artifact_store.begin_job_output_scope(job_id))
            self.assertIsNone(artifact_store.reserve_job_output(job_id, "whisper", suffix=".json"))

    def test_sealed_scope_rejects_late_reservation_but_accepts_existing_attestation(self) -> None:
        job_id = "jobv5_" + "9" * 32
        patches = self._patch_store()
        with patches[0], patches[1]:
            self.assertIsNotNone(artifact_store.begin_job_output_scope(job_id))
            reservation = artifact_store.reserve_job_output(job_id, "whisper", suffix=".json")
            self.assertIsNotNone(reservation)
            assert reservation is not None
            self.assertTrue(artifact_store.seal_job_output_reservations(job_id))
            self.assertIsNone(artifact_store.reserve_job_output(job_id, "late", suffix=".json"))
            target = Path(str(reservation["path"]))
            target.write_bytes(b"transcript")
            self.assertEqual(artifact_store.attest_job_output_reservation(job_id, reservation["token"], target, "other_producer")["status"], "manual_review")
            self.assertEqual(artifact_store.attest_job_output_reservation(job_id, reservation["token"], target, "whisper")["status"], "attested")

    def test_reservation_save_failure_rolls_back_without_authority(self) -> None:
        job_id = "jobv5_" + "6" * 32
        patches = self._patch_store()
        with patches[0], patches[1]:
            self.assertIsNotNone(artifact_store.begin_job_output_scope(job_id))
            with patch.object(artifact_store, "_save_job_output_scopes", return_value=False):
                self.assertIsNone(artifact_store.reserve_job_output_namespace(job_id, "comfyui", expected_patterns=["*.png"], max_children=2))
            state = artifact_store.inspect_job_output_scope(job_id)
            self.assertIsNotNone(state)
            assert state is not None
            self.assertEqual(state["reservation_count"], 0)
            self.assertFalse(list((self.output_root / ".job-output-scopes").glob("*")) if (self.output_root / ".job-output-scopes").exists() else [])

    def test_attested_child_replacement_is_manual_review_and_byte_preserved(self) -> None:
        job_id = "jobv5_" + "b" * 32
        patches = self._patch_store()
        with patches[0], patches[1]:
            self.assertIsNotNone(artifact_store.begin_job_output_scope(job_id))
            reservation = artifact_store.reserve_job_output(job_id, "media", suffix=".mp4")
            self.assertIsNotNone(reservation)
            assert reservation is not None
            target = Path(str(reservation["path"]))
            target.write_bytes(b"original")
            self.assertEqual(artifact_store.attest_job_output_reservation(job_id, reservation["token"], target)["status"], "attested")
            target.write_bytes(b"replacement")
            resolved = artifact_store.resolve_job_output_reservations(job_id, {"status": "completed", "output_reservations": [{"token": reservation["token"], "field": "output"}]})
            self.assertIsNotNone(resolved)
            assert resolved is not None
            prepared = artifact_store.prepare_job_output_scope(job_id, resolved)
            self.assertEqual(prepared["status"], "manual_review")
            finalized = artifact_store.finalize_job_output_scope(job_id, resolved, terminal_state="failed")
            self.assertEqual(finalized["status"], "manual_review")
            self.assertEqual(target.read_bytes(), b"replacement")

    def test_corrupt_or_unknown_reservation_ledger_fails_closed_without_echo(self) -> None:
        job_id = "jobv5_" + "7" * 32
        manifest = self.index_path.with_name(".job_output_scopes.json")
        patches = self._patch_store()
        with patches[0], patches[1]:
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text(json.dumps({
                "schema_version": "job-output-scope.v2",
                "records": {job_id: {
                    "job_id": job_id,
                    "job_fingerprint": "a" * 64,
                    "state": "open",
                    "snapshot_complete": True,
                    "baseline": {},
                    "reservations": {"resv_" + "8" * 32: {"token": "resv_" + "8" * 32, "job_id": job_id, "producer": "x", "namespace": "n", "expected_relative": "n/a", "expected_patterns": [], "max_children": 1, "max_size_bytes": 1, "state": [], "before": None, "attested": {}}},
                    "candidates": {},
                }},
            }), encoding="utf-8")
            self.assertIsNone(artifact_store.inspect_job_output_scope(job_id))
            self.assertEqual(artifact_store.reconcile_job_output_scopes(active_job_ids=set()), {"cleaned": 0, "manual_review": 0})

    def test_manager_blocks_publication_when_ledger_corrupts_after_submit(self) -> None:
        from src.services.api import jobs as api_jobs
        from src.services.job_manager.manager import HubJobManager

        job_id = "jobv5_" + "a" * 32
        manager = HubJobManager()
        patches = self._patch_store()
        with patches[0], patches[1], patch.object(api_jobs, "_jobs", {}), patch.object(api_jobs, "_save"), patch.object(api_jobs, "_publish_result", side_effect=AssertionError("publication must remain unreachable")):
            def runner(_payload, _context):
                artifact_store._job_output_scope_path().write_text("[]", encoding="utf-8")
                return {"status": "completed", "output": str(self.output_root / "foreign.bin")}

            record = manager.submit("run_media_operation", {}, runner, heavy=False)
            idle, remaining = manager.wait_for_idle(5)
            self.assertTrue(idle, remaining)
            public = api_jobs.get_job(record["id"])
            self.assertIsNotNone(public)
            assert public is not None
            self.assertEqual(public["status"], "failed")
            self.assertEqual(public["result"]["status"], "unavailable")
            self.assertNotIn(str(self.output_root), json.dumps(public, ensure_ascii=False))
            self.assertNotIn("resv_", json.dumps(public, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
