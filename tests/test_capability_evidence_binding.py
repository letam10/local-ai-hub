"""Contract tests for component-bound direct-job completion evidence."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.services import tool_smoke
from src.services.api import core


def _component(**extra: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": "sam2",
        "name": "SAM 2",
        "status": "partial",
        "path": "runtime-root",
        "environment": "runtime-env",
        "runtime_status": "not_run",
        "execution": "not_run",
    }
    value.update(extra)
    return value


class CapabilityEvidenceBindingTests(unittest.TestCase):
    def test_completed_job_receipt_binds_component_and_promotes_matching_tool(self) -> None:
        component = _component()
        with tempfile.TemporaryDirectory() as temporary:
            state_path = Path(temporary) / "tool_smoke.json"
            with (
                patch.object(tool_smoke, "STATE_PATH", state_path),
                patch.object(core, "components", return_value=[component]),
                patch.object(core, "_path_exists", return_value=True),
                patch.object(core, "_port_open", return_value=False),
            ):
                tool_smoke.record_completed("segment_image")
                receipt = json.loads(state_path.read_text(encoding="utf-8"))["segment_image"]
                projected = core.component_statuses()[0]
                readiness = core._tool_readiness("segment_image", {"sam2": projected})

        self.assertEqual(receipt["schema_version"], core.COMPONENT_RUNTIME_EVIDENCE_SCHEMA)
        self.assertEqual(receipt["component"], "sam2")
        self.assertRegex(receipt["runtime_fingerprint"], r"^[0-9a-f]{64}$")
        self.assertEqual(projected["last_smoke"]["status"], "completed")
        self.assertTrue(projected["last_smoke"]["runtime_fingerprint_match"])
        self.assertEqual(readiness["tool_status"], "operational")

    def test_receipt_for_a_different_component_never_promotes_the_tool(self) -> None:
        component = _component()
        with tempfile.TemporaryDirectory() as temporary:
            state_path = Path(temporary) / "tool_smoke.json"
            state_path.write_text(json.dumps({
                "segment_image": {
                    "schema_version": core.COMPONENT_RUNTIME_EVIDENCE_SCHEMA,
                    "tool": "segment_image",
                    "component": "rfdetr",
                    "status": "completed",
                    "outcome": "completed",
                    "execution": "completed",
                    "recorded_at": "2026-08-16T00:00:00+00:00",
                    "runtime_fingerprint": "a" * 64,
                    "evidence": "bounded_direct_job",
                },
            }), encoding="utf-8")
            with (
                patch.object(tool_smoke, "STATE_PATH", state_path),
                patch.object(core, "components", return_value=[component]),
                patch.object(core, "_path_exists", return_value=True),
                patch.object(core, "_port_open", return_value=False),
            ):
                projected = core.component_statuses()[0]
                readiness = core._tool_readiness("segment_image", {"sam2": projected})

        self.assertEqual(projected["last_smoke"]["status"], "not_run")
        self.assertEqual(readiness["tool_status"], "partial")

    def test_runtime_fingerprint_drift_keeps_receipt_non_operational(self) -> None:
        component = _component()
        with tempfile.TemporaryDirectory() as temporary:
            state_path = Path(temporary) / "tool_smoke.json"
            with (
                patch.object(tool_smoke, "STATE_PATH", state_path),
                patch.object(core, "components", return_value=[component]),
                patch.object(core, "_path_exists", return_value=True),
                patch.object(core, "_port_open", return_value=False),
            ):
                tool_smoke.record_completed("segment_image")
                with patch.object(core, "_path_exists", return_value=False):
                    projected = core.component_statuses()[0]
                    readiness = core._tool_readiness("segment_image", {"sam2": projected})

        self.assertEqual(projected["last_smoke"]["status"], "completed")
        self.assertFalse(projected["last_smoke"]["runtime_fingerprint_match"])
        self.assertNotEqual(readiness["tool_status"], "operational")

    def test_unbound_completion_receipt_stays_not_run(self) -> None:
        component = _component()
        with tempfile.TemporaryDirectory() as temporary:
            state_path = Path(temporary) / "tool_smoke.json"
            with (
                patch.object(tool_smoke, "STATE_PATH", state_path),
                patch.object(tool_smoke, "_current_component_binding", return_value=None),
                patch.object(core, "components", return_value=[component]),
                patch.object(core, "_path_exists", return_value=True),
                patch.object(core, "_port_open", return_value=False),
            ):
                tool_smoke.record_completed("segment_image")
                projected = core.component_statuses()[0]

        self.assertEqual(projected["last_smoke"]["status"], "not_run")

    def test_failed_and_unavailable_attempts_supersede_old_completion(self) -> None:
        component = _component()
        with tempfile.TemporaryDirectory() as temporary:
            state_path = Path(temporary) / "tool_smoke.json"
            with (
                patch.object(tool_smoke, "STATE_PATH", state_path),
                patch.object(core, "components", return_value=[component]),
                patch.object(core, "_path_exists", return_value=True),
                patch.object(core, "_port_open", return_value=False),
            ):
                tool_smoke.record_completed("segment_image")
                tool_smoke.record_failed("segment_image", failure_code="OUTPUT_MISSING")
                failed_projection = core.component_statuses()[0]
                failed_readiness = core._tool_readiness("segment_image", {"sam2": failed_projection})
                tool_smoke.record_unavailable("segment_image", failure_code="PREFLIGHT_UNAVAILABLE", execution="not_run")
                unavailable_projection = core.component_statuses()[0]
                unavailable_readiness = core._tool_readiness("segment_image", {"sam2": unavailable_projection})

        self.assertEqual(failed_projection["last_smoke"]["status"], "failed")
        self.assertEqual(failed_readiness["tool_status"], "partial")
        self.assertIn("vô hiệu hóa", failed_readiness["reason"])
        self.assertNotIn("C:\\", str(failed_projection))
        self.assertEqual(unavailable_projection["last_smoke"]["status"], "unavailable")
        self.assertEqual(unavailable_projection["last_smoke"]["execution"], "not_run")
        self.assertEqual(unavailable_readiness["tool_status"], "partial")
        self.assertIn("không khả dụng", unavailable_readiness["reason"])

    def test_preflight_unavailable_replaces_prior_evidence(self) -> None:
        with patch.object(core, "component_statuses", return_value=[]), patch.object(
            core,
            "_tool_readiness",
            return_value={
                "component": "sam2",
                "component_status": "missing",
                "tool_status": "unavailable",
                "reason": "Backend unavailable.",
                "action": "Run a bounded smoke later.",
            },
        ), patch.object(core, "record_unavailable") as record:
            status, payload = core.submit_tool("segment_image", {})

        self.assertEqual(status, 503)
        self.assertEqual(payload["status"], "unavailable")
        record.assert_called_once_with("segment_image", failure_code="PREFLIGHT_UNAVAILABLE", execution="not_run")

    def test_output_missing_completion_cannot_record_operational_evidence(self) -> None:
        from src.services.job_manager import manager as manager_module

        updates: list[dict[str, object]] = []

        class InlineThread:
            def __init__(self, *, target: object, args: tuple[object, ...], **_kwargs: object) -> None:
                self._target = target
                self._args = args

            def start(self) -> None:
                self._target(*self._args)  # type: ignore[operator]

        manager = manager_module.HubJobManager()
        record = {"id": "job_missing_artifact", "tool": "segment_from_points", "status": "queued"}
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(tool_smoke, "STATE_PATH", Path(temporary) / "tool_smoke.json"),
            patch.object(manager_module, "create_job", return_value=dict(record)),
            patch.object(manager_module, "get_job_internal", return_value=dict(record)),
            patch.object(manager_module, "update_job", side_effect=lambda _job_id, **values: updates.append(values)),
            patch.object(manager_module.threading, "Thread", InlineThread),
        ):
            manager.submit(
                "segment_from_points",
                {},
                lambda _payload, _context: {"status": "completed", "mask": "synthetic", "preview": "synthetic"},
                heavy=False,
            )
            self.assertFalse(tool_smoke.passed("segment_from_points"))

        self.assertTrue(any(item.get("status") == "failed" for item in updates))
        self.assertFalse(any(item.get("status") == "completed" for item in updates))
        failed = next(item for item in updates if item.get("status") == "failed")
        self.assertEqual(failed["result"]["failure_code"], "OUTPUT_MISSING")

    def test_probe_media_is_the_explicit_metadata_only_completion(self) -> None:
        self.assertFalse(tool_smoke.requires_published_artifact("probe_media"))
        self.assertTrue(tool_smoke.requires_published_artifact("segment_from_points"))
        self.assertTrue(tool_smoke.has_published_artifact({"artifacts": [{"id": "artifact_" + "a" * 32}]}))
        self.assertFalse(tool_smoke.has_published_artifact({"mask": "synthetic", "preview": "synthetic"}))


if __name__ == "__main__":
    unittest.main()
