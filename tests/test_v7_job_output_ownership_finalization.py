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
        self.assertEqual(result["status"], "manual_review")
        self.assertEqual(target.read_bytes(), b"second")
        self.assertEqual(artifact_store.list_artifacts(), [])

    def test_cancel_after_partial_output_cleans_only_allocated_leaf(self) -> None:
        job = reservations.create_reservation("job_20260821_010101_dddddddd", "d" * 64, "run_media_operation")
        rid = job["reservation_id"]
        target = reservations.reserve_output_path(rid, "partial.bin")
        reservations.begin_producing(rid)
        target.write_bytes(b"partial")
        cleaned = reservations.abort_reservation(rid, state="cancelled")
        self.assertEqual(cleaned["status"], "cleaned")
        self.assertFalse(target.exists())

    def test_commit_is_idempotently_recoverable_after_public_commit(self) -> None:
        job = reservations.create_reservation("job_20260821_010101_eeeeeeee", "e" * 64, "run_media_operation")
        rid = job["reservation_id"]
        target = reservations.reserve_output_path(rid, "one.bin")
        target.write_bytes(b"one")
        reservations.begin_producing(rid)
        provenance = {"job_id": job["job_id"], "job_spec_fingerprint": "e" * 64, "adapter_id": "run_media_operation", "attempt": 1, "status": "completed"}
        first = reservations.commit_reservation(rid, {"status": "completed", "output": str(target)}, provenance=provenance, require_output=True)
        second = reservations.commit_reservation(rid, {"status": "completed", "output": str(target)}, provenance=provenance, require_output=True)
        self.assertEqual(first["status"], "published")
        self.assertEqual(second["status"], "published")
        self.assertEqual(second["artifacts"][0]["id"], first["artifacts"][0]["id"])

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
        original_prepare = artifact_store.prepare_worker_artifact
        replaced = {"done": False}

        def race(source, **kwargs):
            if not replaced["done"]:
                replacement = source.with_name("foreign.bin")
                replacement.write_bytes(b"foreign")
                source.unlink()
                replacement.replace(source)
                replaced["done"] = True
            return original_prepare(source, **kwargs)

        provenance = {"job_id": job["job_id"], "job_spec_fingerprint": "c" * 64, "adapter_id": "run_media_operation", "attempt": 1, "status": "completed"}
        with patch.object(artifact_store, "prepare_worker_artifact", side_effect=race):
            result = reservations.commit_reservation(rid, {"status": "completed", "output": str(target)}, provenance=provenance, require_output=True)
        self.assertEqual(result["status"], "manual_review")
        self.assertEqual(target.read_bytes(), b"owned")
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

    def test_staged_replacement_before_registration_is_not_published(self) -> None:
        job = reservations.create_reservation("job_20260821_010101_efefefef", "e" * 64, "run_media_operation")
        self.assertIsNotNone(job)
        rid = job["reservation_id"]
        target = reservations.reserve_output_path(rid, "stage-race.bin")
        target.write_bytes(b"owned-stage")
        reservations.begin_producing(rid)
        original_publish = artifact_store.publish_prepared_transaction
        attempted = {"done": False}

        def replace_then_publish(transaction_id, *, reservation_id, provenance, artifact_proofs):
            artifact_id = artifact_proofs[0]["artifact_id"]
            stage = Path(artifact_store._load()[artifact_id]["path"])
            replacement = stage.with_name("foreign-stage.bin")
            replacement.write_bytes(b"foreign-stage")
            stage.unlink()
            replacement.replace(stage)
            attempted["done"] = True
            return original_publish(transaction_id, reservation_id=reservation_id, provenance=provenance, artifact_proofs=artifact_proofs)

        provenance = {"job_id": job["job_id"], "job_spec_fingerprint": "e" * 64, "adapter_id": "run_media_operation", "attempt": 1, "status": "completed"}
        with patch.object(artifact_store, "publish_prepared_transaction", side_effect=replace_then_publish):
            result = reservations.commit_reservation(rid, {"status": "completed", "output": str(target)}, provenance=provenance, require_output=True)
        self.assertTrue(attempted["done"])
        self.assertEqual(result["status"], "manual_review")
        staged_foreign = list(self.output_root.rglob("hub-job-stage-*"))
        self.assertEqual(len(staged_foreign), 1)
        self.assertEqual(staged_foreign[0].read_bytes(), b"foreign-stage")
        self.assertEqual(artifact_store.list_artifacts(), [])

    def test_manifest_replacement_after_atomic_install_refuses_and_cleans_scope(self) -> None:
        manifest = self.index_path.with_name(".job_output_reservations.json")
        original_replace = reservations.os.replace
        replaced = {"done": False}

        def replace_then_foreign(source, destination):
            original_replace(source, destination)
            if destination == manifest and not replaced["done"]:
                foreign = manifest.with_name(".foreign-manifest.json")
                foreign.write_text('{"schema_version":"job-output-reservation.v1","records":{}}\n', encoding="utf-8")
                original_replace(foreign, manifest)
                replaced["done"] = True

        with patch.object(reservations.os, "replace", side_effect=replace_then_foreign):
            created = reservations.create_reservation("job_20260821_010101_fefefefe", "f" * 64, "legacy")
        self.assertIsNone(created)
        self.assertTrue(replaced["done"])
        self.assertEqual(reservations._load_manifest()["records"], {})
        self.assertFalse(any((self.temp_root / "job-reservations").glob("output_res_*")))

    def test_authorization_persistence_failure_aborts_prepared_transaction_without_public_artifact(self) -> None:
        job = reservations.create_reservation("job_20260821_010101_12121212", "1" * 64, "run_media_operation")
        rid = job["reservation_id"]
        target = reservations.reserve_output_path(rid, "authorize-fail.bin")
        target.write_bytes(b"prepared")
        reservations.begin_producing(rid)
        original_save = reservations._save_manifest
        calls = {"count": 0}

        def fail_authorization(manifest):
            calls["count"] += 1
            if calls["count"] == 1:
                return False
            return original_save(manifest)

        provenance = {"job_id": job["job_id"], "job_spec_fingerprint": "1" * 64, "adapter_id": "run_media_operation", "attempt": 1, "status": "completed"}
        with patch.object(reservations, "_save_manifest", side_effect=fail_authorization):
            result = reservations.commit_reservation(rid, {"status": "completed", "output": str(target)}, provenance=provenance, require_output=True)
        self.assertEqual(result["status"], "manual_review")
        self.assertEqual(artifact_store.list_artifacts(), [])
        self.assertEqual(artifact_store.list_managed_artifacts(), [])

    def test_public_commit_has_no_required_reservation_write_after_visibility(self) -> None:
        job = reservations.create_reservation("job_20260821_010101_23232323", "2" * 64, "run_media_operation")
        rid = job["reservation_id"]
        target = reservations.reserve_output_path(rid, "commit-authorized.bin")
        target.write_bytes(b"published")
        reservations.begin_producing(rid)
        original_save = reservations._save_manifest
        calls = {"count": 0}

        def fail_if_after_authorization(manifest):
            calls["count"] += 1
            if calls["count"] == 1:
                return original_save(manifest)
            return False

        provenance = {"job_id": job["job_id"], "job_spec_fingerprint": "2" * 64, "adapter_id": "run_media_operation", "attempt": 1, "status": "completed"}
        with patch.object(reservations, "_save_manifest", side_effect=fail_if_after_authorization):
            result = reservations.commit_reservation(rid, {"status": "completed", "output": str(target)}, provenance=provenance, require_output=True)
        self.assertEqual(result["status"], "published")
        self.assertEqual(calls["count"], 1)
        self.assertEqual(len(artifact_store.list_artifacts()), 1)

    def test_published_object_is_detached_from_mutable_reservation_path(self) -> None:
        job = reservations.create_reservation("job_20260821_010101_34343434", "3" * 64, "run_media_operation")
        rid = job["reservation_id"]
        target = reservations.reserve_output_path(rid, "immutable.bin")
        target.write_bytes(b"original")
        reservations.begin_producing(rid)
        provenance = {"job_id": job["job_id"], "job_spec_fingerprint": "3" * 64, "adapter_id": "run_media_operation", "attempt": 1, "status": "completed"}
        result = reservations.commit_reservation(rid, {"status": "completed", "output": str(target)}, provenance=provenance, require_output=True)
        self.assertEqual(result["status"], "published")
        artifact_id = result["artifacts"][0]["id"]
        managed = artifact_store.resolve(artifact_id)
        self.assertIsNotNone(managed)
        assert managed is not None
        self.assertNotIn(".hub-reserved", str(managed))
        self.assertNotEqual(managed.parent, target.parent)
        replacement = target.with_name("foreign-release.bin")
        replacement.write_bytes(b"foreign-release")
        target.unlink()
        replacement.replace(target)
        self.assertEqual(managed.read_bytes(), b"original")
        self.assertEqual(artifact_store.resolve(artifact_id), managed)

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
