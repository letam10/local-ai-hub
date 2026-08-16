"""Static contract tests for local component readiness and smoke evidence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

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


class CapabilityEvidenceConsistencyTests(unittest.TestCase):
    def test_normal_local_not_run_rows_project_actual_runtime_presence(self) -> None:
        item = _component()
        self.assertEqual(core._observed_status(item, {
            "port_open": False,
            "executable_present": True,
            "path_present": True,
            "environment_present": True,
            "model_required": False,
            "model_present": False,
        }), "installed")
        self.assertEqual(core._observed_status(item, {
            "port_open": False,
            "executable_present": False,
            "path_present": True,
            "environment_present": False,
            "model_required": False,
            "model_present": False,
        }), "partial")

    def test_model_missing_and_not_installed_never_become_installed_from_runtime_paths(self) -> None:
        available_runtime = {
            "port_open": False,
            "executable_present": True,
            "path_present": True,
            "environment_present": True,
            "model_required": True,
            "model_present": False,
        }
        self.assertEqual(core._observed_status(_component(model="required-model"), available_runtime), "partial")
        self.assertEqual(core._observed_status(_component(status="not_installed"), available_runtime), "not_installed")

    def test_recovered_static_remains_unavailable_even_when_leaves_exist(self) -> None:
        item = _component(recovery_state="recovered_static")
        with patch.object(core, "_path_exists", return_value=True), patch.object(core, "_port_open", return_value=True):
            self.assertEqual(core._observed_status(item), "unavailable")

    def test_component_projection_is_path_free_and_distinguishes_not_run_from_missing(self) -> None:
        item = _component(path=r"C:\private\runtime", environment=r"C:\private\env")
        with patch.object(core, "components", return_value=[item]), patch.object(core, "_path_exists", return_value=True), patch.object(
            core, "_port_open", return_value=False
        ):
            projection = core.component_statuses()[0]
        self.assertEqual(projection["component_status"], "installed")
        self.assertEqual(projection["current_readiness"], "installed")
        self.assertEqual(projection["last_smoke"]["status"], "not_run")
        self.assertRegex(projection["runtime_fingerprint"], r"^[0-9a-f]{64}$")
        self.assertNotIn("C:\\private", str(projection))

    def test_only_fresh_matching_completed_smoke_can_promote_a_partial_tool(self) -> None:
        item = _component()
        now = datetime.now(timezone.utc)
        with patch.object(core, "_path_exists", return_value=True), patch.object(core, "_port_open", return_value=False):
            observation = core._runtime_observation(item)
            fingerprint = core._runtime_fingerprint(item, observation)
        item["last_smoke"] = {
            "schema_version": core.COMPONENT_RUNTIME_EVIDENCE_SCHEMA,
            "tool": "segment_image",
            "outcome": "completed",
            "execution": "completed",
            "recorded_at": now.isoformat(),
            "runtime_fingerprint": fingerprint,
        }
        with (
            patch.object(core, "components", return_value=[item]),
            patch.object(core, "_path_exists", return_value=True),
            patch.object(core, "_port_open", return_value=False),
            patch.object(core, "smoke_passed", return_value=True),
        ):
            status = core.component_statuses()[0]
            readiness = core._tool_readiness("segment_image", {"sam2": status})
        self.assertEqual(status["last_smoke"], {
            "status": "completed",
            "execution": "completed",
            "fresh": True,
            "runtime_fingerprint_match": True,
            "tool": "segment_image",
        })
        self.assertEqual(readiness["tool_status"], "operational")

    def test_stale_or_mismatched_or_legacy_smoke_never_promotes(self) -> None:
        item = _component()
        now = datetime.now(timezone.utc)
        with patch.object(core, "_path_exists", return_value=True), patch.object(core, "_port_open", return_value=False):
            observation = core._runtime_observation(item)
            fingerprint = core._runtime_fingerprint(item, observation)
        for evidence in (
            {
                "schema_version": core.COMPONENT_RUNTIME_EVIDENCE_SCHEMA,
                "tool": "segment_image",
                "outcome": "completed",
                "execution": "completed",
                "recorded_at": (now - core.COMPONENT_RUNTIME_EVIDENCE_MAX_AGE - timedelta(seconds=1)).isoformat(),
                "runtime_fingerprint": fingerprint,
            },
            {
                "schema_version": core.COMPONENT_RUNTIME_EVIDENCE_SCHEMA,
                "tool": "segment_image",
                "outcome": "completed",
                "execution": "completed",
                "recorded_at": now.isoformat(),
                "runtime_fingerprint": "0" * 64,
            },
            {"status": "completed", "execution": "completed"},
        ):
            with self.subTest(evidence=evidence):
                candidate = dict(item, last_smoke=evidence)
                with (
                    patch.object(core, "components", return_value=[candidate]),
                    patch.object(core, "_path_exists", return_value=True),
                    patch.object(core, "_port_open", return_value=False),
                    patch.object(core, "smoke_passed", return_value=True),
                ):
                    status = core.component_statuses()[0]
                    readiness = core._tool_readiness("segment_image", {"sam2": status})
                self.assertEqual(readiness["tool_status"], "partial")
                self.assertFalse(status["last_smoke"]["fresh"] and status["last_smoke"]["runtime_fingerprint_match"])

    def test_previously_static_operational_tool_requires_matching_smoke_too(self) -> None:
        readiness = core._tool_readiness("probe_media", {
            "ffmpeg": {
                "id": "ffmpeg",
                "name": "FFmpeg / FFprobe",
                "component_status": "installed",
                "last_smoke": {"status": "not_run", "execution": "not_run", "fresh": False, "runtime_fingerprint_match": False},
            }
        })
        self.assertEqual(readiness["tool_status"], "partial")
        self.assertIn("runtime fingerprint", readiness["reason"])


if __name__ == "__main__":
    unittest.main()
