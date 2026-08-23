"""Direct V8 DurableWorkEngine publication proof on a controlled local root.

This suite exercises the production V8 bridge using only a synthetic source
artifact and a local output candidate.  No adapter process, provider, model,
GPU or network service is started.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.platform.paths import HubPaths
from src.services import artifact_store
from src.services.api import jobs as api_jobs
from src.services.api.jobs import DurableJobStore
from src.services.artifact_access_v8 import (
    install_v8_artifact_compatibility,
    uninstall_v8_artifact_compatibility_for_tests,
)
from src.services.job_manager.manager import HubJobManager
from src.services.job_manager.durable_adapters import build_production_registry
from src.services.job_manager.v8_engine import V8DurableWorkEngine
from src.services.output_authority_v8 import ProductionOutputAuthority
from src.services.v8_output_bridge import (
    V8OutputBridge,
    reset_default_bridge_for_tests,
    set_default_bridge_for_tests,
)
from src.services.v8_transaction_extensions import V8ProductionTransactionStore


def _video_spec(source_artifact_id: str) -> dict[str, object]:
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


class V8DurableEngineOutputBridgeTests(unittest.TestCase):
    def tearDown(self) -> None:
        uninstall_v8_artifact_compatibility_for_tests()
        reset_default_bridge_for_tests()

    @contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = HubPaths(app_root=root / "app", data_root=root / "data")
            paths.app_root.mkdir()
            paths.data_root.mkdir()
            paths.output_root.mkdir(parents=True)
            index = paths.config_root / "artifacts.json"
            index.parent.mkdir(parents=True)
            control = V8ProductionTransactionStore(paths.config_root / "v8_control.sqlite3")
            bridge = V8OutputBridge(ProductionOutputAuthority(paths=paths, store=control))
            source = paths.output_root / "hub-upload-source.mp4"
            source.write_bytes(b"source fixture")
            with (
                patch.object(artifact_store, "OUTPUT_ROOT", paths.output_root),
                patch.object(artifact_store, "INDEX_PATH", index),
            ):
                source_public = artifact_store.register_path(source, media_type="video/mp4")
                self.assertIsNotNone(source_public)
                assert source_public is not None
                durable = DurableJobStore(root / "durable.json")
                engine = V8DurableWorkEngine(
                    durable,
                    build_production_registry(),
                    gpu_slots=0,
                    output_bridge=bridge,
                )
                job = engine.submit(_video_spec(str(source_public["id"])))
                job_id = str(job["id"])
                durable.update(job_id, {"status": "completed", "state_history": ["queued", "completed"]})
                durable.flush()
                try:
                    yield paths, bridge, durable, engine, job_id, source_public
                finally:
                    engine.close()

    def test_reservation_precedes_local_candidate_and_publication_is_v8_owned(self) -> None:
        with self.fixture() as (paths, bridge, durable, engine, job_id, source_public):
            self.assertIsNone(engine.persist_managed_output(job_id, paths.output_root / f"hub-job-{job_id[-8:]}-no-reservation.bin"))
            self.assertEqual(bridge.list_public(), [])

            reservation = bridge.reserve(job_id)
            self.assertIsInstance(reservation, str)
            candidate = paths.output_root / f"hub-job-{job_id[-8:]}-local.bin"
            candidate.write_bytes(b"managed V8 output")
            published = engine.persist_managed_output(job_id, candidate)

            self.assertIsNotNone(published)
            assert published is not None
            artifact_id = str(published["id"])
            self.assertNotIn("path", published)
            self.assertEqual(published["provenance"]["job_id"], job_id)
            resolved = bridge.resolve(artifact_id)
            self.assertIsNotNone(resolved)
            assert resolved is not None
            self.assertNotEqual(resolved, candidate)
            self.assertEqual(resolved.read_bytes(), b"managed V8 output")
            self.assertEqual(durable.get(job_id)["artifacts"], [artifact_id])

            # Historical V7 source reads remain available in the legacy store;
            # the V8 output object is independently owned by the V8 bridge.
            source_id = str(source_public["id"])
            self.assertIsNotNone(artifact_store.resolve(source_id))
            self.assertIsNotNone(artifact_store.describe(source_id))
            self.assertEqual(len(bridge.list_public()), 1)

    def test_direct_hub_job_manager_reserves_before_local_runner_publication(self) -> None:
        with self.fixture() as (paths, bridge, _durable, _engine, _job_id, _source_public):
            set_default_bridge_for_tests(bridge)
            install_v8_artifact_compatibility()
            manager = HubJobManager()
            index = paths.config_root / "artifacts.json"
            try:
                with (
                    patch.multiple(api_jobs, _jobs={}, _save=lambda **_kwargs: None),
                    patch.object(artifact_store, "OUTPUT_ROOT", paths.output_root),
                    patch.object(artifact_store, "INDEX_PATH", index),
                ):
                    def runner(_payload: dict[str, object], context: object) -> dict[str, object]:
                        job_id = str(getattr(context, "job_id"))
                        candidate = paths.output_root / f"hub-job-{job_id[-8:]}-manager.bin"
                        candidate.write_bytes(b"manager-owned V8 output")
                        return {"status": "completed", "output": str(candidate)}

                    record = manager.submit("run_media_operation", {}, runner, heavy=False)
                    idle, remaining = manager.wait_for_idle(5)
                    self.assertTrue(idle, remaining)
                    public = api_jobs.get_job(str(record["id"]))
                    self.assertIsNotNone(public)
                    assert public is not None
                    self.assertEqual(public["status"], "completed")
                    self.assertNotIn(str(paths.output_root), str(public))
                    artifacts = public.get("result", {}).get("artifacts", [])
                    self.assertIsInstance(artifacts, list)
                    self.assertEqual(len(artifacts), 1)
                    self.assertEqual(len(bridge.list_public()), 1)
            finally:
                manager.cancel_all_and_wait(3)


if __name__ == "__main__":
    unittest.main()
