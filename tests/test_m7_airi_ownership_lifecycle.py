"""M7 AIRI ownership, close and path-free projection acceptance fixtures."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.services import runtime_registry


class _FakeProcess:
    def __init__(self, pid: int, *, exits_on_terminate: bool = True) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.exits_on_terminate = exits_on_terminate
        self.terminate_calls = 0
        self.kill_calls = 0

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminate_calls += 1
        if self.exits_on_terminate:
            self.returncode = 0

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = 1

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            raise TimeoutError(timeout)
        return self.returncode


class AiriOwnershipLifecycleTests(unittest.TestCase):
    def _registry(self, directory: Path, executable: Path) -> None:
        (directory / "application_registry.local.json").write_text(
            json.dumps({
                "schema_version": 2,
                "applications": [{
                    "id": "airi",
                    "display_name": "AIRI",
                    "classification": "SYSTEM_INSTALLED_APP",
                    "executable": str(executable),
                    "working_directory": str(directory),
                    "arguments": [],
                    "launch": True,
                    "identity": {"product_name": "AIRI", "publisher": "Moeru AI"},
                }],
            }),
            encoding="utf-8",
        )

    def _scopes(self, directory: Path):
        return (
            patch.object(runtime_registry, "CONFIG_ROOT", directory),
            patch.object(runtime_registry, "LOCAL_REGISTRY", directory / "application_registry.local.json"),
            patch.object(runtime_registry, "EXAMPLE_REGISTRY", directory / "application_registry.example.json"),
            patch.object(runtime_registry, "_read_file_identity", return_value={"product_name": "AIRI", "publisher": "Moeru AI"}),
            patch.object(runtime_registry, "_file_fingerprint", return_value="a" * 64),
            patch.object(runtime_registry, "_running_process_snapshot", return_value=(frozenset(), frozenset(), False)),
        )

    def test_launch_attests_and_persists_opaque_owned_instance_then_gracefully_closes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            executable = directory / "airi.exe"
            executable.write_bytes(b"airi-fixture")
            self._registry(directory, executable)
            process = _FakeProcess(48123)
            attestation = runtime_registry._ProcessAttestation(
                process.pid, 123456, runtime_registry._normalize_process_path(str(executable)), 9000, (process.pid, 9000)
            )
            scopes = self._scopes(directory)
            with scopes[0], scopes[1], scopes[2], scopes[3], scopes[4], scopes[5], patch.object(
                runtime_registry, "popen_hidden", return_value=process
            ), patch.object(runtime_registry, "_attest_launched_process", return_value=attestation), patch.object(
                runtime_registry, "_query_process_attestation", return_value=attestation
            ), patch.object(runtime_registry, "_post_native_close", side_effect=lambda _pid: (process.terminate() or True)):
                status, launched = runtime_registry.launch("airi")
                self.assertEqual(status, 202)
                instance_id = launched["launch_instance_id"]
                self.assertRegex(instance_id, r"^airi_[a-f0-9]{32}$")
                self.assertEqual(launched["owned_instance"]["pid"], process.pid)
                self.assertNotIn(str(executable), json.dumps(launched))
                self.assertNotIn("command", json.dumps(launched).lower())

                ledger = json.loads((directory / runtime_registry._AIRI_OWNERSHIP_FILE).read_text(encoding="utf-8"))
                self.assertEqual(ledger["schema_version"], runtime_registry._AIRI_OWNERSHIP_SCHEMA)
                self.assertEqual(ledger["instances"][0]["instance_id"], instance_id)

                listed = runtime_registry.application("airi", force=True)
                self.assertEqual(listed["ownership_state"], "owned")
                self.assertTrue(listed["close_available"])
                self.assertEqual(listed["launch_instance_id"], instance_id)
                self.assertEqual(listed["running_state"], "running")

                close_status, closed = runtime_registry.close("airi", instance_id)
                self.assertEqual(close_status, 200)
                self.assertEqual(closed["status"], "closed")
                self.assertEqual(closed["close_mode"], "graceful")
                self.assertEqual(process.terminate_calls, 1)
                self.assertEqual(process.kill_calls, 0)

                listed_after = runtime_registry.application("airi", force=True)
                self.assertFalse(listed_after["close_available"])
                self.assertIsNone(listed_after["launch_instance_id"])
                self.assertEqual(listed_after["running_state"], "not_running")

    def test_preexisting_exact_candidate_is_external_and_cannot_be_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            executable = directory / "airi.exe"
            executable.write_bytes(b"airi-fixture")
            self._registry(directory, executable)
            target = runtime_registry._normalize_process_path(str(executable))
            scopes = self._scopes(directory)
            with scopes[0], scopes[1], scopes[2], scopes[3], scopes[4], patch.object(
                runtime_registry, "_running_process_snapshot", return_value=(frozenset({target}), frozenset(), False)
            ):
                listed = runtime_registry.application("airi", force=True)
                self.assertEqual(listed["running_state"], "running")
                self.assertEqual(listed["ownership_state"], "external")
                self.assertFalse(listed["close_available"])
                self.assertIsNone(listed["launch_instance_id"])
                status, payload = runtime_registry.close("airi", "airi_" + "a" * 32)
                self.assertEqual(status, 404)
                self.assertEqual(payload["error"], "owned_instance_not_found")

    def test_pid_reuse_executable_replacement_lineage_and_restart_records_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            executable = directory / "airi.exe"
            executable.write_bytes(b"airi-fixture")
            self._registry(directory, executable)
            scopes = self._scopes(directory)
            process = _FakeProcess(48124)
            attestation = runtime_registry._ProcessAttestation(
                process.pid, 123457, runtime_registry._normalize_process_path(str(executable)), 9000, (process.pid, 9000)
            )
            with scopes[0], scopes[1], scopes[2], scopes[3], scopes[4], scopes[5], patch.object(
                runtime_registry, "popen_hidden", return_value=process
            ), patch.object(runtime_registry, "_attest_launched_process", return_value=attestation), patch.object(
                runtime_registry, "_query_process_attestation", return_value=attestation
            ) as attestation_query:
                status, launched = runtime_registry.launch("airi")
                self.assertEqual(status, 202)
                instance_id = launched["launch_instance_id"]
                record = runtime_registry._lookup_owned_airi_instance(instance_id)
                self.assertIsNotNone(record)

                record.creation_time += 1
                close_status, payload = runtime_registry.close("airi", instance_id)
                self.assertEqual(close_status, 409)
                self.assertEqual(payload["reason_code"], "ownership_process_instance_changed")
                self.assertEqual(process.terminate_calls, 0)

                record.creation_time -= 1
                record.parent_pid += 1
                close_status, payload = runtime_registry.close("airi", instance_id)
                self.assertEqual(close_status, 409)
                self.assertEqual(payload["reason_code"], "ownership_lineage_changed")
                self.assertEqual(process.terminate_calls, 0)

                record.parent_pid -= 1
                with patch.object(runtime_registry, "_file_fingerprint", return_value="b" * 64):
                    close_status, payload = runtime_registry.close("airi", instance_id)
                self.assertEqual(close_status, 409)
                self.assertEqual(payload["reason_code"], "ownership_executable_changed")
                self.assertEqual(process.terminate_calls, 0)

                record.executable_fingerprint = "a" * 64
                record.process = None
                attestation_query.return_value = None
                close_status, payload = runtime_registry.close("airi", instance_id)
                self.assertEqual(close_status, 409)
                self.assertEqual(payload["reason_code"], "ownership_process_unattested")
                self.assertEqual(process.terminate_calls, 0)

    def test_graceful_timeout_uses_bounded_exact_fallback_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            executable = directory / "airi.exe"
            executable.write_bytes(b"airi-fixture")
            self._registry(directory, executable)
            process = _FakeProcess(48125, exits_on_terminate=False)
            attestation = runtime_registry._ProcessAttestation(
                process.pid, 123458, runtime_registry._normalize_process_path(str(executable)), 9000, (process.pid, 9000)
            )
            scopes = self._scopes(directory)
            with scopes[0], scopes[1], scopes[2], scopes[3], scopes[4], scopes[5], patch.object(
                runtime_registry, "popen_hidden", return_value=process
            ), patch.object(runtime_registry, "_attest_launched_process", return_value=attestation), patch.object(
                runtime_registry, "_query_process_attestation", return_value=attestation
            ) as attestation_query, patch.object(runtime_registry, "_post_native_close", side_effect=lambda _pid: (process.terminate() or True)), patch.object(
                runtime_registry, "_terminate_exact_process", side_effect=lambda _pid: (process.kill() or True)
            ), patch.object(runtime_registry, "_AIRI_CLOSE_GRACE_SECONDS", 0.01), patch.object(
                runtime_registry, "_AIRI_CLOSE_POLL_SECONDS", 0.001
            ):
                status, launched = runtime_registry.launch("airi")
                self.assertEqual(status, 202)
                close_status, closed = runtime_registry.close("airi", launched["launch_instance_id"])
                self.assertEqual(close_status, 200)
                self.assertEqual(closed["close_mode"], "bounded_terminate")
                self.assertEqual(process.terminate_calls, 1)
                self.assertEqual(process.kill_calls, 1)


if __name__ == "__main__":
    unittest.main()
