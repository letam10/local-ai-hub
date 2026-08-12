from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.services import artifact_store
from src.services.api.jobs import DurableJobStore
from src.services.api.v5_productization import (
    durable_jobs_snapshot,
    project_job_recovery,
    project_product_surface,
    resume_durable_job,
)
from src.services.job_manager import ServerOwnedAdapterRegistry
from src.services.job_manager.durable import (
    DurableWorkEngine,
    public_durable_artifacts,
    public_lifecycle,
    public_recovery_decision,
)


JOB_ID = "jobv5_" + "b" * 32
ARTIFACT_ID = "artifact_" + "c" * 32


def spec(*, adapter_id: str = "unit.adapter", reconstructable: bool = True) -> dict[str, object]:
    return {
        "schema_version": "job-spec.v1",
        "tool": "unit.work",
        "descriptor": {
            "schema_version": "execution-descriptor.v1",
            "adapter_id": adapter_id,
            "operation": "run",
            "arguments": {"artifact_id": ARTIFACT_ID, "mode": "unit"},
            "reconstructable": reconstructable,
            "resources": {
                "cpu_slots": 1,
                "gpu_slots": 0,
                "ram_mb": 64,
                "disk_mb": 8,
                "exclusive_group": None,
            },
        },
    }


def record(*, status: str = "failed", artifacts: list[object] | None = None, history: list[str] | None = None) -> dict[str, object]:
    from src.services.job_manager.contracts import validate_job_spec

    value = spec()
    validated = validate_job_spec(value)
    return {
        "contract_version": "durable-job.v1",
        "id": JOB_ID,
        "status": status,
        "created_at": "2026-08-12T00:00:00+00:00",
        "updated_at": "2026-08-12T00:00:01+00:00",
        "started_at": None,
        "finished_at": "2026-08-12T00:00:02+00:00" if status in {"completed", "failed", "unavailable", "interrupted"} else None,
        "progress": 0,
        "execution": "not_run",
        "dry_run": True,
        "job_spec": value,
        "job_spec_fingerprint": validated.fingerprint,
        "descriptor_summary": validated.descriptor.summary(),
        "resource_plan": {
            "dry_run": True,
            "cpu_slots": 1,
            "gpu_slots": 0,
            "ram_mb": 64,
            "disk_mb": 8,
            "exclusive_group": None,
        },
        "attempt": 1,
        "retry_of": None,
        "retry_available": True,
        "reason_code": "RETRY_IF_RECONSTRUCTABLE",
        "action_code": "persisted-client-action-must-not-escape",
        "result_summary": None,
        "artifacts": artifacts or [],
        "state_history": history or ["queued", status],
    }


class DurableRecoveryPublicContractTests(unittest.TestCase):
    def test_recovery_uses_validated_spec_and_current_server_registry_only(self) -> None:
        raw = record()
        empty = public_recovery_decision(raw, ServerOwnedAdapterRegistry())
        self.assertEqual(empty["status"], "unavailable")
        self.assertEqual(empty["action"], "CREATE_NEW_JOB")
        self.assertFalse(empty["action_available"])
        self.assertNotIn("persisted-client", json.dumps(empty))
        public = DurableWorkEngine.public_job(raw)
        self.assertNotIn("persisted-client-action", json.dumps(public))
        raw["contract_version"] = "durable-job.legacy"
        self.assertFalse(public_recovery_decision(raw, ServerOwnedAdapterRegistry())["action_available"])
        raw["contract_version"] = "durable-job.v1"

        registry = ServerOwnedAdapterRegistry()
        invoked: list[str] = []

        def adapter(_descriptor: object, _context: object) -> dict[str, str]:
            invoked.append("called")
            return {"status": "completed"}

        registry.register("unit.adapter", adapter)
        available = public_recovery_decision(raw, registry)
        self.assertEqual(available["status"], "available")
        self.assertEqual(available["action"], "RETRY_IF_RECONSTRUCTABLE")
        self.assertTrue(available["action_available"])

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "durable.json"
            store = DurableJobStore(path)
            store.put(raw)
            store.close()
            resumed = resume_durable_job(JOB_ID, path, registry=registry)
        self.assertEqual(resumed["status"], "queued")
        self.assertEqual(resumed["execution"], "not_run")
        self.assertTrue(resumed["dry_run"])
        self.assertEqual(resumed["job"]["retry_of"], JOB_ID)
        self.assertEqual(resumed["job"]["attempt"], 2)
        self.assertEqual(invoked, [])

    def test_invalid_descriptor_unknown_adapter_and_free_form_action_fail_closed(self) -> None:
        invalid = record()
        invalid["job_spec"] = {"client" + "_" + "sec" + "ret": "no-echo"}
        invalid["job_spec_fingerprint"] = "f" * 64
        invalid["descriptor_summary"] = {"action": "no-echo"}
        invalid_decision = public_recovery_decision(invalid, ServerOwnedAdapterRegistry())
        self.assertEqual(invalid_decision["action"], "CREATE_NEW_JOB")
        self.assertFalse(invalid_decision["action_available"])
        self.assertNotIn("no-echo", json.dumps(invalid_decision))

        unknown = record()
        unknown["job_spec"]["descriptor"]["adapter_id"] = "unknown.adapter"  # type: ignore[index]
        from src.services.job_manager.contracts import validate_job_spec

        unknown_spec = validate_job_spec(unknown["job_spec"])
        unknown["job_spec_fingerprint"] = unknown_spec.fingerprint
        unknown["descriptor_summary"] = unknown_spec.descriptor.summary()
        decision = public_recovery_decision(unknown, ServerOwnedAdapterRegistry())
        self.assertEqual(decision["reason"], "No current server-owned adapter is registered for this job.")
        self.assertFalse(decision["action_available"])

    def test_lifecycle_is_bounded_and_terminal_state_never_regresses(self) -> None:
        lifecycle = public_lifecycle({
            "status": "running",
            "state_history": ["queued", "starting", "completed", "running"] * 20,
            "attempt": 0,
            "retry_of": r"C:\private\job",
            "created_at": "not-a-timestamp",
            "updated_at": "2026-08-12T00:00:01+00:00",
        })
        self.assertEqual(lifecycle["state"], "completed")
        self.assertEqual(lifecycle["history"], ["queued", "starting", "completed"])
        self.assertLessEqual(len(lifecycle["history"]), 32)
        self.assertEqual(lifecycle["attempt"], 1)
        self.assertIsNone(lifecycle["retry_of"])
        self.assertIsNone(lifecycle["timestamps"]["created_at"])

    def test_matching_artifact_is_public_and_mismatch_has_no_preview_link(self) -> None:
        raw = record(status="completed", artifacts=[ARTIFACT_ID, "not-an-artifact"])
        safe = {
            "id": ARTIFACT_ID,
            "name": "result.bin",
            "size_bytes": 12,
            "media_type": "application/octet-stream",
            "url": f"/api/artifacts/{ARTIFACT_ID}",
            "sha256": "d" * 64,
            "provenance": {
                "job_id": JOB_ID,
                "job_spec_fingerprint": raw["job_spec_fingerprint"],
                "adapter_id": "unit.adapter",
                "attempt": 1,
                "status": "completed",
            },
        }
        with patch.object(artifact_store, "resolve", return_value=Path("ignored-in-test")), patch.object(artifact_store, "describe", return_value=safe):
            projected = public_durable_artifacts(raw)
        self.assertEqual(projected[0]["status"], "available")
        self.assertTrue(projected[0]["preview_available"])
        self.assertEqual(projected[0]["url"], f"/api/artifacts/{ARTIFACT_ID}")
        self.assertEqual(projected[1]["status"], "unavailable")
        self.assertFalse(projected[1]["preview_available"])
        self.assertNotIn("ignored-in-test", json.dumps(projected))

        mismatch = dict(safe)
        mismatch["provenance"] = {**safe["provenance"], "attempt": 2}
        with patch.object(artifact_store, "resolve", return_value=Path("ignored-in-test")), patch.object(artifact_store, "describe", return_value=mismatch):
            unavailable = public_durable_artifacts({**raw, "artifacts": [ARTIFACT_ID]})
        self.assertEqual(unavailable[0]["status"], "unavailable")
        self.assertNotIn("url", unavailable[0])

    def test_snapshot_and_product_projection_are_read_only_and_closed(self) -> None:
        raw = record()
        marker = "C:/private/path-or-client-command"
        raw["action_code"] = marker
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "durable.json"
            before_store = DurableJobStore(path)
            before_store.put(raw)
            before_store.close()
            before = path.read_bytes()
            snapshot = durable_jobs_snapshot(path)
            after = path.read_bytes()
        self.assertEqual(before, after)
        durable = snapshot["records"][0]
        self.assertEqual(durable["recovery"]["action"], "CREATE_NEW_JOB")
        self.assertFalse(durable["recovery"]["action_available"])
        self.assertNotIn(marker, json.dumps(snapshot))
        self.assertEqual(snapshot["execution"], "not_run")
        self.assertTrue(snapshot["dry_run"])

        product = project_job_recovery(snapshot["records"])
        self.assertEqual(product["records"][0]["source"], "durable")
        self.assertFalse(product["records"][0]["resumable"])
        surface = project_product_surface(control_plane={}, health={}, jobs=snapshot["records"], workflow_library={})
        self.assertEqual(surface["execution"], "not_run")
        self.assertTrue(surface["dry_run"])

    def test_product_projection_does_not_trust_client_shaped_recovery_mapping(self) -> None:
        forged = project_job_recovery([{
            "id": "jobv5_" + "d" * 32,
            "status": "failed",
            "recovery": {
                "status": "available",
                "action": "RETRY_IF_RECONSTRUCTABLE",
                "action_available": True,
                "reason": "A current server-owned adapter can reconstruct this job.",
                "next_action": "An explicit server-owned retry may be queued without starting runtime work.",
            },
        }])
        self.assertFalse(forged["records"][0]["resumable"])
        self.assertNotIn("recovery", forged["records"][0])


if __name__ == "__main__":
    unittest.main()
