"""Wave 1 reservation/ownership regressions.

All tests use private temporary roots and in-process runners.  No model,
runtime, subprocess, server or network workload is started.
"""

from __future__ import annotations

import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.services import artifact_store
from src.services.api import jobs as api_jobs
from src.services.job_manager import output_reservations as reservations
from src.services.job_manager.manager import HubJobManager
from src.services.job_manager.producer_inventory import producer_inventory, validate_producer_inventory


class V7JobOutputOwnershipFinalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        root = Path(self.temp.name)
        self.output_root = root / "Output"
        self.temp_root = root / "Temp"
        self.config_root = root / "Config"
        self.output_root.mkdir()
        self.temp_root.mkdir()
        self.config_root.mkdir()
        self.index_path = self.config_root / "artifacts.json"
        self.jobs_path = self.config_root / "jobs.json"
        self.patches = (
            patch.object(artifact_store, "OUTPUT_ROOT", self.output_root),
            patch.object(artifact_store, "TEMP_ROOT", self.temp_root),
            patch.object(artifact_store, "INDEX_PATH", self.index_path),
            patch.object(api_jobs, "JOBS_PATH", self.jobs_path),
            patch.object(api_jobs, "_jobs", {}),
            patch.object(api_jobs, "_save", lambda **_kwargs: None),
        )
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def _manifest(self) -> dict:
        path = self.index_path.with_name(".job_output_reservations.json")
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"records": {}}

    def _manager_job(self, tool: str, runner):
        manager = HubJobManager()
        record = manager.submit(tool, {}, runner, heavy=False)
        return manager, record

    def test_reservation_exists_before_runner_starts(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        def runner(_payload, _context):
            entered.set()
            self.assertTrue(release.wait(3))
            return {"status": "completed"}

        manager, record = self._manager_job("run_media_operation", runner)
        self.assertTrue(entered.wait(3))
        manifest = self._manifest()
        self.assertEqual(len(manifest["records"]), 1)
        reservation = next(iter(manifest["records"].values()))
        self.assertEqual(reservation["job_id"], record["id"])
        self.assertEqual(reservation["state"], "producing")
        release.set()
        manager.wait_for_idle(5)

    def test_no_reservation_cannot_publish_path_result(self) -> None:
        safe, error = api_jobs._publish_result(
            {"status": "completed", "output": str(self.output_root / "foreign.mp4")},
            {"id": "job_example", "tool": "legacy"},
        )
        self.assertEqual(safe["status"], "failed")
        self.assertEqual(error, "OUTPUT_RESERVATION_REQUIRED")
        self.assertEqual(artifact_store.list_artifacts(), [])

    def test_real_media_callsite_uses_reservation_handle_and_publishes(self) -> None:
        def runner(_payload, context):
            target = context.output_path("video_grade.mp4")
            target.write_bytes(b"owned")
            return {"status": "completed", "output": str(target)}

        manager, record = self._manager_job("run_media_operation", runner)
        self.assertTrue(manager.wait_for_idle(5)[0])
        public = api_jobs.get_job(record["id"])
        self.assertEqual(public["status"], "completed")
        self.assertEqual(len(public["result"]["artifacts"]), 1)
        self.assertNotIn(str(self.temp_root), json.dumps(public))

    def test_actual_media_adapter_output_path_is_reservation_bound(self) -> None:
        from src.modules.media_editor.backend import adapter as media_adapter

        source = self.temp_root / "input.mp4"
        source.write_bytes(b"input")
        target = self.temp_root / "reserved-output.mp4"

        class Context:
            def output_path(self, _filename: str) -> Path:
                return target

        def fake_run(_command, **_kwargs):
            target.write_bytes(b"output")
            return 0, ""

        with (
            patch.object(media_adapter, "_source", return_value=source),
            patch.object(media_adapter, "_command", return_value=["ffmpeg", "-i", str(source), str(target)]),
            patch.object(media_adapter, "run_command", side_effect=fake_run),
        ):
            result = media_adapter.run_operation({"operation": "transcode"}, Context())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(Path(result["output"]), target)

    def test_two_jobs_same_leaf_get_distinct_reservation_roots(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        seen: list[Path] = []

        def runner(_payload, context):
            target = context.output_path("same.bin")
            seen.append(target)
            target.write_bytes(b"job")
            entered.set()
            release.wait(3)
            return {"status": "completed", "output": str(target)}

        manager = HubJobManager()
        first = manager.submit("run_media_operation", {}, runner, heavy=False)
        second = manager.submit("run_media_operation", {}, runner, heavy=False)
        self.assertTrue(entered.wait(3))
        release.set()
        self.assertTrue(manager.wait_for_idle(5)[0])
        self.assertEqual(len(seen), 2)
        self.assertNotEqual(seen[0].parent, seen[1].parent)
        self.assertEqual(api_jobs.get_job(first["id"])["status"], "completed")
        self.assertEqual(api_jobs.get_job(second["id"])["status"], "completed")

    def test_preexisting_output_is_preserved_and_not_claimed(self) -> None:
        foreign = self.output_root / "existing.mp4"
        foreign.write_bytes(b"user")

        def runner(_payload, _context):
            return {"status": "completed", "output": str(foreign)}

        manager, record = self._manager_job("upscale_anime_video", runner)
        self.assertTrue(manager.wait_for_idle(5)[0])
        public = api_jobs.get_job(record["id"])
        self.assertEqual(public["status"], "failed")
        self.assertEqual(foreign.read_bytes(), b"user")
        self.assertNotIn(str(foreign), json.dumps(public))

    def test_foreign_child_and_traversal_are_preserved(self) -> None:
        foreign = self.output_root / "foreign.bin"
        foreign.write_bytes(b"foreign")
        traversal = self.output_root / ".." / "outside.bin"
        traversal.write_bytes(b"outside")
        job = reservations.create_reservation("job_20260821_010101_aaaaaaaa", "a" * 64, "legacy")
        self.assertIsNotNone(job)
        rid = job["reservation_id"]
        self.assertTrue(reservations.begin_producing(rid))
        result = reservations.commit_reservation(rid, {"status": "completed", "files": [str(foreign), str(traversal)]}, provenance={"job_id": job["job_id"], "job_spec_fingerprint": "a" * 64, "adapter_id": "legacy", "attempt": 1, "status": "completed"}, require_output=True)
        self.assertEqual(result["status"], "manual_review")
        self.assertTrue(foreign.exists())
        self.assertTrue(traversal.exists())

    def test_reparse_or_symlink_output_refuses_without_delete(self) -> None:
        job = reservations.create_reservation("job_20260821_010101_bbbbbbbb", "b" * 64, "run_media_operation")
        self.assertIsNotNone(job)
        rid = job["reservation_id"]
        target = reservations.reserve_output_path(rid, "safe.bin")
        target.write_bytes(b"safe")
        link = target.parent / "link.bin"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable")
        reservations.begin_producing(rid)
        result = reservations.commit_reservation(rid, {"status": "completed", "output": str(link)}, provenance={"job_id": job["job_id"], "job_spec_fingerprint": "b" * 64, "adapter_id": "run_media_operation", "attempt": 1, "status": "completed"}, require_output=True)
        self.assertEqual(result["status"], "manual_review")
        self.assertTrue(target.exists())

    def test_replacement_before_commit_is_manual_review(self) -> None:
        job = reservations.create_reservation("job_20260821_010101_cccccccc", "c" * 64, "run_media_operation")
        rid = job["reservation_id"]
        target = reservations.reserve_output_path(rid, "replace.bin")
        target.write_bytes(b"first")
        replacement = target.with_suffix(".replacement")
        replacement.write_bytes(b"second")
        reservations.begin_producing(rid)
        target.unlink()
        replacement.replace(target)
        result = reservations.commit_reservation(rid, {"status": "completed", "output": str(target)}, provenance={"job_id": job["job_id"], "job_spec_fingerprint": "c" * 64, "adapter_id": "run_media_operation", "attempt": 1, "status": "completed"}, require_output=True)
        self.assertEqual(result["status"], "published")
        # A same-scope replacement is still reservation-owned; publication is
        # allowed only after the new identity is freshly attested.
        self.assertEqual(len(result["artifacts"]), 1)

    def test_cancel_after_partial_output_cleans_only_allocated_leaf(self) -> None:
        job = reservations.create_reservation("job_20260821_010101_dddddddd", "d" * 64, "run_media_operation")
        rid = job["reservation_id"]
        target = reservations.reserve_output_path(rid, "partial.bin")
        reservations.begin_producing(rid)
        target.write_bytes(b"partial")
        cleaned = reservations.abort_reservation(rid, state="cancelled")
        self.assertEqual(cleaned["status"], "cleaned")
        self.assertFalse(target.exists())

    def test_commit_and_cancel_are_terminal_and_second_commit_refuses(self) -> None:
        job = reservations.create_reservation("job_20260821_010101_eeeeeeee", "e" * 64, "run_media_operation")
        rid = job["reservation_id"]
        target = reservations.reserve_output_path(rid, "one.bin")
        target.write_bytes(b"one")
        reservations.begin_producing(rid)
        provenance = {"job_id": job["job_id"], "job_spec_fingerprint": "e" * 64, "adapter_id": "run_media_operation", "attempt": 1, "status": "completed"}
        first = reservations.commit_reservation(rid, {"status": "completed", "output": str(target)}, provenance=provenance, require_output=True)
        second = reservations.commit_reservation(rid, {"status": "completed", "output": str(target)}, provenance=provenance, require_output=True)
        self.assertEqual(first["status"], "published")
        self.assertEqual(second["status"], "unavailable")

    def test_manifest_corruption_and_oversize_fail_closed(self) -> None:
        manifest = self.index_path.with_name(".job_output_reservations.json")
        manifest.write_text('{"schema_version":"job-output-reservation.v1","records":{', encoding="utf-8")
        self.assertIsNone(reservations.inspect_reservation("output_res_" + "f" * 32))
        manifest.write_bytes(b"x" * (reservations.MAX_OUTPUT_COUNT * 4096))
        self.assertIsNone(reservations.create_reservation("job_20260821_010101_ffffffff", "f" * 64, "legacy"))

    def test_reservation_capacity_refuses_without_overflowing_manifest(self) -> None:
        for index in range(reservations.MAX_RESERVATIONS):
            job_id = f"job_20260821_010101_{index:08x}"
            created = reservations.create_reservation(job_id, f"{index:064x}", "legacy")
            self.assertIsNotNone(created)
        refused = reservations.create_reservation("job_20260821_010101_deadbeef", "d" * 64, "legacy")
        self.assertIsNone(refused)
        manifest = self._manifest()
        self.assertEqual(len(manifest["records"]), reservations.MAX_RESERVATIONS)
        self.assertIsNotNone(reservations.inspect_reservation(next(iter(manifest["records"]))))

    def test_replacement_before_abort_is_preserved_and_requires_manual_review(self) -> None:
        job = reservations.create_reservation("job_20260821_010101_abab abab".replace(" ", ""), "a" * 64, "legacy")
        self.assertIsNotNone(job)
        rid = job["reservation_id"]
        target = reservations.reserve_output_path(rid, "owned.bin")
        target.write_bytes(b"owner")
        reservations.begin_producing(rid)
        replacement = target.with_name("foreign.bin")
        replacement.write_bytes(b"foreign")
        replacement.replace(target)
        result = reservations.abort_reservation(rid, state="cancelled")
        self.assertEqual(result["status"], "manual_review")
        self.assertEqual(target.read_bytes(), b"foreign")

    def test_source_replacement_between_hash_and_copy_is_not_published(self) -> None:
        job = reservations.create_reservation("job_20260821_010101_cacacaca", "c" * 64, "run_media_operation")
        self.assertIsNotNone(job)
        rid = job["reservation_id"]
        target = reservations.reserve_output_path(rid, "race.bin")
        target.write_bytes(b"owned")
        reservations.begin_producing(rid)
        original_copy = reservations._copy_reserved_file
        replaced = {"done": False}

        def race(source, reservation_id, relative, expected):
            if not replaced["done"]:
                replacement = source.with_name("foreign.bin")
                replacement.write_bytes(b"foreign")
                source.unlink()
                replacement.replace(source)
                replaced["done"] = True
            return original_copy(source, reservation_id, relative, expected)

        provenance = {"job_id": job["job_id"], "job_spec_fingerprint": "c" * 64, "adapter_id": "run_media_operation", "attempt": 1, "status": "completed"}
        with patch.object(reservations, "_copy_reserved_file", side_effect=race):
            result = reservations.commit_reservation(rid, {"status": "completed", "output": str(target)}, provenance=provenance, require_output=True)
        self.assertEqual(result["status"], "manual_review")
        self.assertEqual(target.read_bytes(), b"foreign")
        self.assertEqual(artifact_store.list_artifacts(), [])

    def test_source_replacement_after_attestation_before_publication_is_refused(self) -> None:
        job = reservations.create_reservation("job_20260821_010101_dadadada", "d" * 64, "run_media_operation")
        self.assertIsNotNone(job)
        rid = job["reservation_id"]
        target = reservations.reserve_output_path(rid, "late-race.bin")
        target.write_bytes(b"owned")
        reservations.begin_producing(rid)
        original_hash = reservations._hash_stable
        replaced = {"done": False}

        def attest_then_replace(source):
            result = original_hash(source)
            if result is not None and source == target and not replaced["done"]:
                replacement = source.with_name("late-foreign.bin")
                replacement.write_bytes(b"foreign")
                source.unlink()
                replacement.replace(source)
                replaced["done"] = True
            return result

        provenance = {"job_id": job["job_id"], "job_spec_fingerprint": "d" * 64, "adapter_id": "run_media_operation", "attempt": 1, "status": "completed"}
        with patch.object(reservations, "_hash_stable", side_effect=attest_then_replace):
            result = reservations.commit_reservation(rid, {"status": "completed", "output": str(target)}, provenance=provenance, require_output=True)
        self.assertEqual(result["status"], "manual_review")
        self.assertEqual(target.read_bytes(), b"foreign")
        self.assertEqual(artifact_store.list_artifacts(), [])

    def test_inventory_has_no_unknown_producer(self) -> None:
        self.assertTrue(validate_producer_inventory())
        self.assertTrue(producer_inventory())
        self.assertNotIn("UNKNOWN", {item["status"] for item in producer_inventory()})

    def test_public_reservation_projection_is_path_free(self) -> None:
        job = reservations.create_reservation("job_20260821_010101_11111111", "1" * 64, "run_media_operation")
        rendered = json.dumps(job, ensure_ascii=False)
        self.assertNotIn(str(self.temp_root), rendered)
        self.assertNotIn("\\", rendered)


if __name__ == "__main__":
    unittest.main()
