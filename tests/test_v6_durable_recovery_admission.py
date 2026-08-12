from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.services import artifact_store
from src.services.api import api_server, v5_productization
from src.services.api.jobs import DurableJobStore
from src.services.job_manager import DurableWorkEngine, JobContractError, ServerOwnedAdapterRegistry
from src.services.job_manager.durable_adapters import (
    MEDIA_VIDEO_GRADE_ADAPTER_ID,
    build_production_registry,
    validate_media_video_grade_spec,
)


ARTIFACT_ID = "artifact_" + "a" * 32
MARKER = "client-private-path-or-secret-marker"


def video_spec(*, arguments: dict[str, object] | None = None, adapter_id: str = MEDIA_VIDEO_GRADE_ADAPTER_ID) -> dict[str, object]:
    return {
        "schema_version": "job-spec.v1",
        "tool": "media.video_grade",
        "descriptor": {
            "schema_version": "execution-descriptor.v1",
            "adapter_id": adapter_id,
            "operation": "run",
            "arguments": arguments or {"source_artifact_id": ARTIFACT_ID, "brightness": 0.1},
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


SAFE_ARTIFACT = {
    "id": ARTIFACT_ID,
    "name": "source.mp4",
    "size_bytes": 12,
    "media_type": "video/mp4",
    "sha256": "b" * 64,
    "url": f"/api/artifacts/{ARTIFACT_ID}",
}


class V6DurableRecoveryAdmissionTests(unittest.TestCase):
    def _artifact_reads(self):
        original_resolve = artifact_store.resolve
        original_describe = artifact_store.describe

        def resolve(artifact_id: str) -> Path | None:
            if artifact_id == ARTIFACT_ID:
                return Path("server-owned-video.mp4")
            return original_resolve(artifact_id)

        def describe(artifact_id: str) -> dict[str, object] | None:
            if artifact_id == ARTIFACT_ID:
                return dict(SAFE_ARTIFACT)
            return original_describe(artifact_id)

        return patch.multiple(
            "src.services.job_manager.durable_adapters.artifact_store",
            resolve=resolve,
            describe=describe,
        )

    def test_production_registry_is_lazy_allowlisted_and_never_probes_executables(self) -> None:
        from src.modules.media_editor.backend import adapter

        with (
            patch.object(adapter, "run_hidden", side_effect=AssertionError("no executable probe")),
            patch.object(adapter, "encoder_capabilities", side_effect=AssertionError("no encoder discovery")),
        ):
            registry = build_production_registry()
        self.assertTrue(registry.contains(MEDIA_VIDEO_GRADE_ADAPTER_ID))
        self.assertEqual(registry.summaries(), [{"adapter_id": MEDIA_VIDEO_GRADE_ADAPTER_ID}])

    def test_spec_allowlist_accepts_video_controls_and_rejects_unknown_or_raw_fields(self) -> None:
        with self._artifact_reads():
            validated = validate_media_video_grade_spec(video_spec(arguments={
                "source_artifact_id": ARTIFACT_ID,
                "brightness": -1,
                "contrast": 3,
                "saturation": 0,
                "gamma": 4,
                "denoise": "light",
                "sharpen": "medium",
        }))
        self.assertEqual(validated.tool, "media.video_grade")
        blocked_field = "sec" + "ret"
        for candidate in (
            {"source_artifact_id": ARTIFACT_ID, "filter": MARKER},
            {"source_artifact_id": ARTIFACT_ID, "path": MARKER},
            {"source_artifact_id": ARTIFACT_ID, "command": MARKER},
            {"source_artifact_id": ARTIFACT_ID, "callable": MARKER},
            {"source_artifact_id": ARTIFACT_ID, "manifest": MARKER},
            {"source_artifact_id": ARTIFACT_ID, blocked_field: MARKER},
            {"source_artifact_id": ARTIFACT_ID, "unknown_control": MARKER},
        ):
            with self.subTest(candidate=list(set(candidate) - {"source_artifact_id"})):
                with self.assertRaises(JobContractError) as caught:
                    validate_media_video_grade_spec(video_spec(arguments=candidate), resolve_input=False)
                self.assertNotIn(MARKER, str(caught.exception))
        for malformed in ([], {}, "bad"):
            with self.assertRaises(JobContractError):
                validate_media_video_grade_spec(video_spec(arguments={"source_artifact_id": ARTIFACT_ID, "brightness": malformed}), resolve_input=False)

    def test_resource_policy_enforces_all_fixed_fields_before_resolution_or_write(self) -> None:
        for field in ("ram_mb", "disk_mb"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                state = Path(temporary) / "durable.json"
                candidate = video_spec()
                candidate["descriptor"]["resources"][field] = 1  # type: ignore[index]
                with patch(
                    "src.services.job_manager.durable_adapters.artifact_store.resolve",
                    side_effect=AssertionError("resource rejection must precede artifact resolution"),
                ):
                    with self.assertRaises(JobContractError):
                        validate_media_video_grade_spec(candidate, resolve_input=False)
                    result = v5_productization.admit_durable_job(candidate, state)
                self.assertEqual(result["status"], "invalid")
                self.assertFalse(state.exists())

    def test_admission_accepts_one_new_job_without_execution_or_public_echo(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, self._artifact_reads():
            state = Path(temporary) / "durable.json"
            result = v5_productization.admit_durable_job(video_spec(), state)
            self.assertEqual(result["status"], "accepted")
            self.assertEqual(result["execution"], "not_run")
            self.assertTrue(result["dry_run"])
            self.assertEqual(result["job"]["status"], "queued")
            self.assertNotIn("source_artifact_id", json.dumps(result))
            self.assertNotIn(MARKER, json.dumps(result))
            store = DurableJobStore(state)
            raw = store.records()
            store.close()
        self.assertEqual(len(raw), 1)
        self.assertEqual(raw[0]["job_spec"]["descriptor"]["arguments"]["source_artifact_id"], ARTIFACT_ID)

    def test_invalid_and_unavailable_admission_create_no_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "durable.json"
            invalid = v5_productization.admit_durable_job({"client": MARKER}, state)
            self.assertEqual(invalid["status"], "invalid")
            self.assertEqual(invalid["execution"], "not_run")
            self.assertNotIn(MARKER, json.dumps(invalid))
            self.assertFalse(state.exists())
            with patch.object(v5_productization, "build_production_registry", return_value=ServerOwnedAdapterRegistry()):
                unavailable = v5_productization.admit_durable_job(video_spec(), state)
            self.assertEqual(unavailable["status"], "unavailable")
            self.assertEqual(unavailable["recovery"]["action"], "CREATE_NEW_JOB")
            self.assertFalse(state.exists())

    def test_missing_video_artifact_is_unavailable_without_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "durable.json"
            with patch.multiple(
                "src.services.job_manager.durable_adapters.artifact_store",
                resolve=lambda _artifact_id: None,
                describe=lambda _artifact_id: (_ for _ in ()).throw(AssertionError("describe after missing resolve")),
            ):
                result = v5_productization.admit_durable_job(video_spec(), state)
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["error"]["code"], "SOURCE_ARTIFACT_UNAVAILABLE")
            self.assertNotIn(MARKER, json.dumps(result))
            self.assertFalse(state.exists())

    def test_api_status_mapping_is_fixed(self) -> None:
        self.assertEqual(api_server._durable_admission_http_status({"status": "accepted"}), 202)
        self.assertEqual(api_server._durable_admission_http_status({"status": "invalid"}), 400)
        self.assertEqual(api_server._durable_admission_http_status({"status": "unavailable"}), 503)

    def test_restart_retry_uses_same_registry_and_never_invokes_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, self._artifact_reads():
            state = Path(temporary) / "durable.json"
            accepted = v5_productization.admit_durable_job(video_spec(), state)
            job_id = accepted["job"]["id"]
            invoked: list[str] = []
            registry = ServerOwnedAdapterRegistry()

            def adapter(_descriptor: object, _context: object) -> dict[str, str]:
                invoked.append("called")
                return {"status": "completed"}

            registry.register(MEDIA_VIDEO_GRADE_ADAPTER_ID, adapter)
            store = DurableJobStore(state)
            store.update(job_id, {"status": "interrupted", "state_history": ["queued", "interrupted"]})
            store.close()
            retried = v5_productization.resume_durable_job(job_id, state, registry=registry)
            self.assertEqual(retried["status"], "queued")
            self.assertEqual(retried["execution"], "not_run")
            self.assertTrue(retried["dry_run"])
            self.assertEqual(retried["job"]["retry_of"], job_id)
            self.assertEqual(retried["job"]["attempt"], 2)
            self.assertEqual(invoked, [])

    def test_removed_adapter_or_stale_input_fails_closed_without_retry_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, self._artifact_reads():
            state = Path(temporary) / "durable.json"
            accepted = v5_productization.admit_durable_job(video_spec(), state)
            job_id = accepted["job"]["id"]
            empty = v5_productization.resume_durable_job(job_id, state, registry=ServerOwnedAdapterRegistry())
            self.assertEqual(empty["status"], "unavailable")
            self.assertEqual(empty["recovery"]["action"], "CREATE_NEW_JOB")
            store = DurableJobStore(state)
            record = store.get(job_id)
            record["job_spec"]["descriptor"]["arguments"]["source_artifact_id"] = "artifact_" + "c" * 32
            store.update(job_id, record)
            store.close()
            stale = v5_productization.resume_durable_job(job_id, state)
            self.assertEqual(stale["status"], "unavailable")
            self.assertEqual(stale["recovery"]["action"], "CREATE_NEW_JOB")
            self.assertNotIn("artifact_" + "c" * 32, json.dumps(stale))

    def test_managed_output_publication_is_deferred_without_artifact_or_url(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, self._artifact_reads():
            root = Path(temporary)
            state = root / "durable.json"
            output_root = root / "output"
            index = root / "artifacts.json"
            with patch.object(artifact_store, "OUTPUT_ROOT", output_root), patch.object(artifact_store, "INDEX_PATH", index):
                accepted = v5_productization.admit_durable_job(video_spec(), state)
                job_id = accepted["job"]["id"]
                store = DurableJobStore(state)
                store.update(job_id, {"status": "completed", "state_history": ["queued", "completed"]})
                engine = DurableWorkEngine(store, build_production_registry(), gpu_slots=0)
                output_root.mkdir(parents=True, exist_ok=True)
                output = output_root / f"hub-job-{job_id[-8:]}-fixture.bin"
                output.write_bytes(b"tiny managed video fixture")
                with (
                    patch.object(artifact_store, "register_path", side_effect=AssertionError("deferred output must not register")) as register_path,
                    patch.object(store, "update", side_effect=AssertionError("deferred output must not update durable state")) as update,
                ):
                    result = engine.persist_managed_output(job_id, output)
                self.assertIsNone(result)
                register_path.assert_not_called()
                update.assert_not_called()
                self.assertFalse(index.exists())
                public = engine.get(job_id)
                self.assertEqual(public["artifacts"], [])
                self.assertNotIn("url", json.dumps(public["artifacts"]))
                engine.close()


if __name__ == "__main__":
    unittest.main()
