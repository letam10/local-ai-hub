"""Bounded synthetic tests for job-owned ordinary Output cleanup."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from src.services import artifact_store
from src.services.api import jobs as api_jobs
from src.services.job_manager.manager import HubJobManager


class V7JobOutputTransactionTests(unittest.TestCase):
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
        return patch.multiple(
            api_jobs,
            _jobs={},
            _save=lambda **_kwargs: None,
        ), patch.object(artifact_store, "OUTPUT_ROOT", self.output_root), patch.object(artifact_store, "INDEX_PATH", self.index_path)

    def test_cancelled_owned_output_is_cleaned_without_public_artifact(self) -> None:
        ready = threading.Event()
        release = threading.Event()
        holder: dict[str, Path] = {}
        manager = HubJobManager()
        patches = self._patch_store()
        try:
            with patches[0], patches[1], patches[2]:
                def runner(_payload: dict[str, object], context: object) -> dict[str, object]:
                    target = context.output_path("created.mp4")
                    holder["path"] = target
                    target.write_bytes(b"owned output")
                    ready.set()
                    self.assertTrue(release.wait(3))
                    return {"status": "completed", "output": str(target)}

                record = manager.submit("upscale_anime_video", {}, runner, heavy=False)
                self.assertTrue(ready.wait(3))
                cancelled, _message = manager.cancel(record["id"])
                self.assertTrue(cancelled)
                release.set()
                idle, remaining = manager.wait_for_idle(5)
                self.assertTrue(idle, remaining)
                public = api_jobs.get_job(record["id"])
                self.assertIsNotNone(public)
                assert public is not None
                self.assertEqual(public["status"], "cancelled")
                self.assertFalse(holder["path"].exists())
                self.assertFalse(self.index_path.exists())
                self.assertNotIn(str(holder["path"]), json.dumps(public, ensure_ascii=False))
        finally:
            release.set()
            manager.cancel_all_and_wait(3)

    def test_preexisting_same_path_is_preserved_and_marked_manual_review(self) -> None:
        ready = threading.Event()
        release = threading.Event()
        target = self.output_root / "existing.mp4"
        target.write_bytes(b"user bytes")
        manager = HubJobManager()
        patches = self._patch_store()
        try:
            with patches[0], patches[1], patches[2]:
                def runner(_payload: dict[str, object], _context: object) -> dict[str, object]:
                    ready.set()
                    self.assertTrue(release.wait(3))
                    return {"status": "completed", "output": str(target)}

                record = manager.submit("upscale_anime_video", {}, runner, heavy=False)
                self.assertTrue(ready.wait(3))
                cancelled, _message = manager.cancel(record["id"])
                self.assertTrue(cancelled)
                release.set()
                idle, remaining = manager.wait_for_idle(5)
                self.assertTrue(idle, remaining)
                public = api_jobs.get_job(record["id"])
                self.assertIsNotNone(public)
                assert public is not None
                self.assertEqual(public["status"], "cancelled")
                self.assertEqual(target.read_bytes(), b"user bytes")
                self.assertNotIn(str(target), json.dumps(public, ensure_ascii=False))
        finally:
            release.set()
            manager.cancel_all_and_wait(3)

    def test_publication_failure_cleans_owned_candidates_and_never_leaves_partial_index(self) -> None:
        holder: dict[str, Path] = {}
        manager = HubJobManager()
        patches = self._patch_store()
        try:
            with patches[0], patches[1], patches[2], patch.object(artifact_store, "register_worker_outputs", return_value=None):
                def runner(_payload: dict[str, object], context: object) -> dict[str, object]:
                    target = context.output_path("created.mp4")
                    holder["path"] = target
                    target.write_bytes(b"publication failure")
                    return {"status": "completed", "output": str(target)}

                record = manager.submit(
                    "upscale_anime_video",
                    {},
                    runner,
                    heavy=False,
                )
                idle, remaining = manager.wait_for_idle(5)
                self.assertTrue(idle, remaining)
                public = api_jobs.get_job(record["id"])
                self.assertIsNotNone(public)
                assert public is not None
                self.assertEqual(public["status"], "failed")
                self.assertFalse(holder["path"].exists())
                self.assertFalse(self.index_path.exists())
                self.assertNotIn(str(holder["path"]), json.dumps(public, ensure_ascii=False))
        finally:
            manager.cancel_all_and_wait(3)

    def test_invalid_traversal_directory_and_symlink_candidates_are_refused_without_deletion(self) -> None:
        job_id = "jobv5_" + "a" * 32
        with patch.object(artifact_store, "OUTPUT_ROOT", self.output_root), patch.object(artifact_store, "INDEX_PATH", self.index_path):
            self.assertIsNotNone(artifact_store.begin_job_output_scope(job_id))
            outside = Path(self.temp.name) / "outside.bin"
            outside.write_bytes(b"outside")
            directory = self.output_root / "directory"
            directory.mkdir()
            self.assertEqual(artifact_store.prepare_job_output_scope(job_id, {"status": "completed", "files": [str(outside), str(directory), str(self.output_root / ".." / "outside.bin")]})["status"], "invalid")
            self.assertTrue(outside.exists())
            self.assertTrue(directory.exists())
            symlink = self.output_root / "link.bin"
            try:
                symlink.symlink_to(outside)
            except (OSError, NotImplementedError):
                symlink = None
            if symlink is not None:
                self.assertEqual(artifact_store.prepare_job_output_scope(job_id, {"status": "completed", "output": str(symlink)})["status"], "invalid")
                self.assertTrue(outside.exists())

    def test_stale_scope_reconcile_cleans_only_explicit_owned_candidate(self) -> None:
        job_id = "jobv5_" + "b" * 32
        owned = self.output_root / "owned.bin"
        legacy = self.output_root / "legacy.bin"
        legacy.write_bytes(b"legacy")
        with patch.object(artifact_store, "OUTPUT_ROOT", self.output_root), patch.object(artifact_store, "INDEX_PATH", self.index_path):
            self.assertIsNotNone(artifact_store.begin_job_output_scope(job_id))
            owned.write_bytes(b"owned")
            self.assertEqual(artifact_store.prepare_job_output_scope(job_id, {"status": "completed", "output": str(owned)})["status"], "owned")
            result = artifact_store.reconcile_job_output_scopes(active_job_ids=set())
            self.assertEqual(result["manual_review"], 0)
            self.assertFalse(owned.exists())
            self.assertEqual(legacy.read_bytes(), b"legacy")

    def test_multiple_owned_candidates_are_cleaned_as_one_scope(self) -> None:
        job_id = "jobv5_" + "c" * 32
        first = self.output_root / "first.bin"
        second = self.output_root / "nested" / "second.bin"
        with patch.object(artifact_store, "OUTPUT_ROOT", self.output_root), patch.object(artifact_store, "INDEX_PATH", self.index_path):
            self.assertIsNotNone(artifact_store.begin_job_output_scope(job_id))
            second.parent.mkdir(parents=True)
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            prepared = artifact_store.prepare_job_output_scope(job_id, {"status": "completed", "files": [str(first), str(second)]})
            self.assertEqual(prepared["status"], "owned")
            self.assertEqual(prepared["owned_count"], 2)
            cleaned = artifact_store.finalize_job_output_scope(job_id, terminal_state="cancelled")
            self.assertEqual(cleaned["status"], "cleaned")
            self.assertFalse(first.exists())
            self.assertFalse(second.exists())

    def test_corrupt_scope_state_fails_closed_without_path_or_type_echo(self) -> None:
        job_id = "jobv5_" + "d" * 32
        manifest = self.index_path.with_name(".job_output_scopes.json")
        with patch.object(artifact_store, "OUTPUT_ROOT", self.output_root), patch.object(artifact_store, "INDEX_PATH", self.index_path):
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text(json.dumps({
                "schema_version": "job-output-scope.v1",
                "records": {job_id: {
                    "job_id": job_id,
                    "job_fingerprint": "a" * 64,
                    "state": [],
                    "snapshot_complete": True,
                    "baseline": {},
                    "candidates": {},
                }},
            }), encoding="utf-8")
            self.assertIsNone(artifact_store.inspect_job_output_scope(job_id))
            self.assertEqual(artifact_store.reconcile_job_output_scopes(active_job_ids=set()), {"cleaned": 0, "manual_review": 0})


if __name__ == "__main__":
    unittest.main()
