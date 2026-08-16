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


if __name__ == "__main__":
    unittest.main()
