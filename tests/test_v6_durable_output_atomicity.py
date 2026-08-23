from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from src.services import artifact_store
from src.services.api.jobs import DurableJobStore, DurableStoreHealthError
from src.services.job_manager import LegacyDurableWorkEngine
from src.services.job_manager.durable import public_durable_artifacts
from src.services.job_manager.durable_adapters import build_production_registry


def video_spec(source_artifact_id: str) -> dict[str, object]:
    return {
        "schema_version": "job-spec.v1",
        "tool": "media.video_grade",
        "descriptor": {
            "schema_version": "execution-descriptor.v1",
            "adapter_id": "media.video_grade.v1",
            "operation": "run",
            "arguments": {"source_artifact_id": source_artifact_id, "brightness": 0.1},
            "reconstructable": True,
            "resources": {
                "cpu_slots": 1,
                "gpu_slots": 0,
                "ram_mb": 0,
                "disk_mb": 0,
                "exclusive_group": None,
            },
        },
    }


class V6DurableOutputAtomicityTests(unittest.TestCase):
    @contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output_root = root / "output"
            upload_root = root / "uploads"
            archive_root = root / "archive"
            index_path = root / "config" / "artifacts.json"
            output_root.mkdir(parents=True)
            source = output_root / "hub-upload-source.mp4"
            source.write_bytes(b"source fixture")
            with (
                patch.object(artifact_store, "OUTPUT_ROOT", output_root),
                patch.object(artifact_store, "UPLOAD_ROOT", upload_root),
                patch.object(artifact_store, "ARCHIVE_ROOT", archive_root),
                patch.object(artifact_store, "INDEX_PATH", index_path),
            ):
                source_public = artifact_store.register_path(source, media_type="video/mp4")
                self.assertIsNotNone(source_public)
                source_id = source_public["id"]
                store = DurableJobStore(root / "durable.json")
                engine = LegacyDurableWorkEngine(store, build_production_registry(), gpu_slots=0)
                job = engine.submit(video_spec(source_id))
                job_id = job["id"]
                store.update(job_id, {"status": "completed", "state_history": ["queued", "completed"]})
                store.flush()
                output = output_root / f"hub-job-{job_id[-8:]}-fixture.bin"
                output.write_bytes(b"managed output fixture")
                try:
                    yield root, output_root, index_path, store, engine, job_id, output
                finally:
                    try:
                        engine.close()
                    except DurableStoreHealthError:
                        pass

    @staticmethod
    def _provenance(store: DurableJobStore, job_id: str) -> dict[str, object]:
        record = store.get(job_id)
        return {
            "job_id": job_id,
            "job_spec_fingerprint": record["job_spec_fingerprint"],
            "adapter_id": "media.video_grade.v1",
            "attempt": record["attempt"],
            "status": "completed",
        }

    def test_stage_is_hidden_until_exact_durable_link_then_publish(self) -> None:
        with self.fixture() as (_root, output_root, _index_path, store, _engine, job_id, output):
            content = output.read_bytes()
            provenance = self._provenance(store, job_id)
            staged = artifact_store.stage_job_artifact(
                output,
                job_id=job_id,
                name="video_grade.mp4",
                media_type="video/mp4",
                size_bytes=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
                provenance=provenance,
            )
            self.assertIsNotNone(staged)
            artifact_id = staged["id"]
            transaction_id = staged["transaction_id"]
            self.assertNotIn("path", staged)
            self.assertIsNone(artifact_store.resolve(artifact_id))
            self.assertIsNone(artifact_store.describe(artifact_id))
            self.assertFalse(artifact_store.open_artifact(artifact_id)[0])
            self.assertNotIn(artifact_id, {item["id"] for item in artifact_store.list_artifacts()})
            self.assertIsNone(artifact_store.publish_staged(artifact_id, transaction_id, provenance))
            self.assertIsNone(artifact_store.resolve(artifact_id))
            link = store.commit_managed_artifact(job_id, artifact_id, transaction_id, provenance)
            self.assertEqual(store.commit_managed_artifact(job_id, artifact_id, transaction_id, provenance), link)
            self.assertIsNone(artifact_store.resolve(artifact_id))
            self.assertIsNotNone(artifact_store._mark_staged_linked(artifact_id, transaction_id, provenance))
            published = artifact_store.publish_staged(artifact_id, transaction_id, provenance)
            self.assertIsNotNone(published)
            self.assertIsNotNone(artifact_store.resolve(artifact_id))
            self.assertEqual(artifact_store.describe(artifact_id)["url"], f"/api/artifacts/{artifact_id}")
            self.assertEqual(store.get_managed_artifact_link(job_id), link)
            self.assertNotIn(str(output_root), json.dumps(published))

    def test_index_replace_failure_preserves_previous_bytes_and_removes_temp(self) -> None:
        with self.fixture() as (_root, _output_root, index_path, _store, _engine, _job_id, _output):
            original = index_path.read_bytes()

            def fail_replace(_source: Path, _target: Path) -> None:
                raise OSError("index replace unavailable")

            with patch.object(Path, "replace", new=fail_replace):
                with self.assertRaises(OSError):
                    artifact_store._save({})
            self.assertEqual(index_path.read_bytes(), original)
            self.assertEqual(list(index_path.parent.glob(f".{index_path.name}.*.tmp")), [])

    def test_engine_managed_output_uses_chunked_private_stage_and_public_link(self) -> None:
        with self.fixture() as (_root, _output_root, _index_path, store, engine, job_id, output):
            original_read_bytes = Path.read_bytes

            def reject_full_output_read(path: Path) -> bytes:
                if path.resolve() == output.resolve():
                    raise AssertionError("managed output must not use read_bytes")
                return original_read_bytes(path)

            with patch.object(Path, "read_bytes", reject_full_output_read):
                result = engine.persist_managed_output(job_id, output)
            self.assertIsNotNone(result)
            artifact_id = result["id"]
            self.assertEqual(result["provenance"]["job_id"], job_id)
            self.assertNotIn("path", result)
            self.assertIsNotNone(artifact_store.resolve(artifact_id))
            record = store.get(job_id)
            self.assertEqual(record["artifacts"], [artifact_id])
            self.assertEqual(public_durable_artifacts(record)[0]["status"], "available")
            self.assertNotIn(str(output.parent), json.dumps(result))

    def test_managed_output_rejects_source_overwrite_candidate(self) -> None:
        with self.fixture() as (_root, _output_root, _index_path, _store, engine, job_id, output):
            with patch.object(artifact_store, "resolve", return_value=output):
                self.assertIsNone(engine.persist_managed_output(job_id, output))
            self.assertEqual(artifact_store.list_managed_artifacts(), [])

    def test_durable_store_failure_aborts_private_stage_without_orphan_or_url(self) -> None:
        with self.fixture() as (_root, _output_root, index_path, store, engine, job_id, output):
            with patch.object(
                store,
                "commit_managed_artifact",
                side_effect=DurableStoreHealthError("DURABLE_STORE_UNAVAILABLE", "Create a new job."),
            ):
                self.assertIsNone(engine.persist_managed_output(job_id, output))
            self.assertEqual(artifact_store.list_managed_artifacts(), [])
            self.assertEqual(len(artifact_store.list_artifacts()), 1)  # only the legacy source artifact
            record = store.get(job_id)
            self.assertEqual(record["artifacts"], [])
            self.assertEqual(public_durable_artifacts(record), [])

    def test_durable_snapshot_failure_after_stage_leaves_no_artifact_index_entry(self) -> None:
        with self.fixture() as (_root, _output_root, _index_path, store, engine, job_id, output):
            with patch.object(
                store,
                "_write_snapshot_locked",
                side_effect=DurableStoreHealthError("DURABLE_STORE_PERSISTENCE_FAILED", "Repair durable state."),
            ):
                self.assertIsNone(engine.persist_managed_output(job_id, output))
            self.assertEqual(artifact_store.list_managed_artifacts(), [])
            self.assertEqual(store._records[job_id]["artifacts"], [])
            self.assertIsNone(artifact_store.resolve("artifact_" + "e" * 32))

    def test_stage_index_failure_cleans_private_file_and_keeps_legacy_index_safe(self) -> None:
        with self.fixture() as (_root, _output_root, _index_path, store, _engine, job_id, output):
            content = output.read_bytes()
            provenance = self._provenance(store, job_id)
            with patch("src.services.artifact_store._save", side_effect=OSError("index unavailable")):
                staged = artifact_store.stage_job_artifact(
                    output,
                    job_id=job_id,
                    name="video_grade.mp4",
                    media_type="video/mp4",
                    size_bytes=len(content),
                    sha256=hashlib.sha256(content).hexdigest(),
                    provenance=provenance,
                )
            self.assertIsNone(staged)
            self.assertEqual(artifact_store.list_managed_artifacts(), [])
            self.assertEqual(len(artifact_store.list_artifacts()), 1)
            self.assertEqual(list((_output_root).glob("hub-job-stage-*")), [])

    def test_publish_failure_leaves_exact_link_hidden_for_reconcile(self) -> None:
        with self.fixture() as (_root, _output_root, _index_path, store, engine, job_id, output):
            with patch.object(artifact_store, "publish_staged", return_value=None):
                self.assertIsNone(engine.persist_managed_output(job_id, output))
            link = store.get_managed_artifact_link(job_id)
            self.assertIsNotNone(link)
            self.assertIsNone(artifact_store.resolve(link["artifact_id"]))
            self.assertEqual(len(artifact_store.list_artifacts()), 1)  # only the legacy source artifact
            engine.reconcile_startup()
            self.assertIsNotNone(artifact_store.resolve(link["artifact_id"]))

    def test_published_record_without_durable_link_stays_hidden(self) -> None:
        with self.fixture() as (_root, _output_root, _index_path, store, _engine, job_id, output):
            content = output.read_bytes()
            staged = artifact_store.stage_job_artifact(
                output,
                job_id=job_id,
                name="video_grade.mp4",
                media_type="video/mp4",
                size_bytes=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
                provenance=self._provenance(store, job_id),
            )
            self.assertIsNotNone(staged)
            artifact_id = staged["id"]
            index = artifact_store._load()
            index[artifact_id]["visibility"] = "published"
            index[artifact_id]["durable_linked"] = False
            artifact_store._save(index)
            self.assertIsNone(artifact_store.resolve(artifact_id))
            self.assertIsNone(artifact_store.describe(artifact_id))
            self.assertNotIn(artifact_id, {item["id"] for item in artifact_store.list_artifacts()})

    def test_stage_without_durable_link_is_aborted_on_startup_reconcile(self) -> None:
        with self.fixture() as (_root, _output_root, _index_path, store, engine, job_id, output):
            provenance = self._provenance(store, job_id)
            content = output.read_bytes()
            staged = artifact_store.stage_job_artifact(
                output,
                job_id=job_id,
                name="video_grade.mp4",
                media_type="video/mp4",
                size_bytes=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
                provenance=provenance,
            )
            self.assertIsNotNone(staged)
            artifact_id = staged["id"]
            engine.reconcile_startup()
            self.assertIsNone(artifact_store.inspect_managed_artifact(artifact_id))
            self.assertIsNone(artifact_store.resolve(artifact_id))

    def test_wrong_attempt_and_conflicting_retry_never_replace_terminal_link(self) -> None:
        with self.fixture() as (_root, _output_root, _index_path, store, engine, job_id, output):
            first = engine.persist_managed_output(job_id, output)
            self.assertIsNotNone(first)
            first_link = store.get_managed_artifact_link(job_id)
            self.assertIsNotNone(first_link)
            provenance = self._provenance(store, job_id)
            provenance["attempt"] = 2
            content = output.read_bytes()
            second = artifact_store.stage_job_artifact(
                output,
                job_id=job_id,
                name="video_grade.mp4",
                media_type="video/mp4",
                size_bytes=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
                provenance=provenance,
            )
            self.assertIsNotNone(second)
            with self.assertRaises(DurableStoreHealthError):
                store.commit_managed_artifact(job_id, second["id"], second["transaction_id"], provenance)
            artifact_store.abort_job_artifact(second["id"], second["transaction_id"])
            self.assertEqual(store.get_managed_artifact_link(job_id), first_link)
            self.assertEqual(len(artifact_store.list_artifacts()), 2)

    def test_tampered_managed_file_is_demoted_without_public_url(self) -> None:
        with self.fixture() as (_root, _output_root, _index_path, store, engine, job_id, output):
            result = engine.persist_managed_output(job_id, output)
            self.assertIsNotNone(result)
            artifact_id = result["id"]
            managed_path = Path(artifact_store._load()[artifact_id]["path"])
            managed_path.write_bytes(b"changed after publication")
            engine.reconcile_startup()
            self.assertIsNone(artifact_store.resolve(artifact_id))
            self.assertNotIn(artifact_id, {item["id"] for item in artifact_store.list_artifacts()})
            self.assertIsNone(store.get_managed_artifact_link(job_id))

    def test_symlinked_managed_record_is_demoted_without_public_url(self) -> None:
        with self.fixture() as (_root, _output_root, _index_path, store, engine, job_id, output):
            result = engine.persist_managed_output(job_id, output)
            self.assertIsNotNone(result)
            artifact_id = result["id"]
            managed_path = Path(artifact_store._load()[artifact_id]["path"])
            with patch.object(Path, "is_symlink", new=lambda candidate: candidate == managed_path):
                engine.reconcile_startup()
            self.assertIsNone(artifact_store.resolve(artifact_id))
            self.assertIsNone(store.get_managed_artifact_link(job_id))

    def test_legacy_artifacts_keep_visibility_and_range_boundary(self) -> None:
        with self.fixture() as (_root, output_root, _index_path, _store, _engine, _job_id, _output):
            legacy = output_root / "legacy-result.bin"
            legacy.write_bytes(b"legacy")
            public = artifact_store.register_path(legacy, media_type="application/octet-stream")
            self.assertIsNotNone(public)
            self.assertIsNotNone(artifact_store.resolve(public["id"]))
            self.assertEqual(artifact_store.describe(public["id"])["url"], f"/api/artifacts/{public['id']}")

    def test_durable_media_is_fenced_from_generic_public_first_output(self) -> None:
        with self.fixture() as (_root, _output_root, index_path, _store, engine, job_id, _output):
            with patch("src.services.job_manager.durable.atomic_write_job_output", side_effect=AssertionError("unsafe generic path")) as writer:
                self.assertIsNone(engine.persist_output(job_id, b"must not publish"))
            writer.assert_not_called()
            self.assertEqual(artifact_store.list_managed_artifacts(), [])

    def test_malformed_managed_visibility_and_unhashable_artifact_id_fail_closed(self) -> None:
        with self.fixture() as (_root, _output_root, index_path, _store, _engine, _job_id, _output):
            index_path.write_text(
                json.dumps({
                    "artifact_" + "c" * 32: {
                        "id": "artifact_" + "c" * 32,
                        "path": "private",
                        "visibility": [],
                        "transaction_id": "artifact_tx_" + "d" * 32,
                    }
                }),
                encoding="utf-8",
            )
            self.assertEqual(artifact_store.list_managed_artifacts(), [])
            self.assertEqual(artifact_store.list_artifacts(), [])
            self.assertIsNone(artifact_store.resolve([]))  # type: ignore[arg-type]
            self.assertIsNone(artifact_store.describe({}))  # type: ignore[arg-type]

    def test_corrupt_or_symlinked_index_is_not_replaced_by_managed_stage(self) -> None:
        with self.fixture() as (_root, _output_root, index_path, store, _engine, job_id, output):
            content = output.read_bytes()
            provenance = self._provenance(store, job_id)
            corrupt = b'{"unterminated":'
            index_path.write_bytes(corrupt)
            self.assertIsNone(
                artifact_store.stage_job_artifact(
                    output,
                    job_id=job_id,
                    name="video_grade.mp4",
                    media_type="video/mp4",
                    size_bytes=len(content),
                    sha256=hashlib.sha256(content).hexdigest(),
                    provenance=provenance,
                )
            )
            self.assertEqual(index_path.read_bytes(), corrupt)
            valid = json.dumps({}).encode("utf-8")
            index_path.write_bytes(valid)
            with patch.object(Path, "is_symlink", new=lambda candidate: candidate == index_path):
                self.assertIsNone(
                    artifact_store.stage_job_artifact(
                        output,
                        job_id=job_id,
                        name="video_grade.mp4",
                        media_type="video/mp4",
                        size_bytes=len(content),
                        sha256=hashlib.sha256(content).hexdigest(),
                        provenance=provenance,
                    )
                )
            self.assertEqual(index_path.read_bytes(), valid)

    def test_unindexed_stage_file_is_reaped_but_indexed_stage_is_retained(self) -> None:
        with self.fixture() as (_root, output_root, _index_path, store, engine, job_id, output):
            orphan = output_root / "hub-job-stage-orphan.bin"
            orphan.write_bytes(b"orphan")
            self.assertEqual(artifact_store.cleanup_managed_orphans(), 1)
            self.assertFalse(orphan.exists())
            content = output.read_bytes()
            staged = artifact_store.stage_job_artifact(
                output,
                job_id=job_id,
                name="video_grade.mp4",
                media_type="video/mp4",
                size_bytes=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
                provenance=self._provenance(store, job_id),
            )
            self.assertIsNotNone(staged)
            self.assertEqual(artifact_store.cleanup_managed_orphans(), 0)
            engine.reconcile_startup()

    def test_managed_stage_rejects_noncompleted_or_wrong_contract_fields(self) -> None:
        with self.fixture() as (_root, _output_root, _index_path, store, _engine, job_id, output):
            content = output.read_bytes()
            provenance = self._provenance(store, job_id)
            for field, value in (("status", "failed"), ("adapter_id", "other.adapter"), ("attempt", 0)):
                candidate = dict(provenance)
                candidate[field] = value
                with self.subTest(field=field):
                    self.assertIsNone(
                        artifact_store.stage_job_artifact(
                            output,
                            job_id=job_id,
                            name="video_grade.mp4",
                            media_type="video/mp4",
                            size_bytes=len(content),
                            sha256=hashlib.sha256(content).hexdigest(),
                            provenance=candidate,
                        )
                    )

    def test_reconcile_corrupt_status_shapes_fail_closed_without_type_error(self) -> None:
        with self.fixture() as (_root, _output_root, _index_path, store, engine, job_id, _output):
            for malformed in ([], {}):
                with self.subTest(status=type(malformed).__name__):
                    store.update(job_id, {"status": malformed, "job_spec": {"tampered": True}})
                    store.flush()
                    result = engine.reconcile_startup()
                    self.assertEqual(result["unavailable"], 1)
                    public = engine.get(job_id)
                    self.assertEqual(public["status"], "unavailable")
                    self.assertEqual(public["recovery"]["action"], "CREATE_NEW_JOB")


if __name__ == "__main__":
    unittest.main()
