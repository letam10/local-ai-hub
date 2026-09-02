"""M2 AIRI discovery, identity, projection and ID-only launch contracts."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from src.services import runtime_registry
from src.services.api.context import ApiContext
from src.services.api.router import ApiRequest
from src.services.api.router_registry import build_router


class AiriManagedLaunchTests(unittest.TestCase):
    def _registry(self, directory: Path, *rows: dict) -> None:
        (directory / "application_registry.local.json").write_text(
            json.dumps({"schema_version": 2, "applications": list(rows)}),
            encoding="utf-8",
        )

    def _airi_row(self, executable: Path, **overrides: object) -> dict:
        row = {
            "id": "airi",
            "display_name": "AIRI",
            "category": "assistant",
            "classification": "SYSTEM_INSTALLED_APP",
            "executable": str(executable),
            "working_directory": str(executable.parent),
            "arguments": ["--safe-mode"],
            "launch": True,
            "identity": {"product_name": "AIRI", "publisher": "Moeru AI"},
        }
        row.update(overrides)
        return row

    def _candidate(self, executable: Path, fingerprint: str | None = None):
        with patch.object(runtime_registry, "_read_file_identity", return_value={"product_name": "AIRI", "publisher": "Moeru AI"}):
            with patch.object(runtime_registry, "_file_fingerprint", return_value=fingerprint):
                candidate, code = runtime_registry._candidate_from_path(
                    executable,
                    working_directory=executable.parent,
                    arguments=[],
                    source="windows_registry",
                    expected_product="AIRI",
                    expected_publisher="Moeru AI",
                )
        self.assertIsNotNone(candidate, code)
        return candidate

    def _local_scope(self, directory: Path):
        return (
            patch.object(runtime_registry, "CONFIG_ROOT", directory),
            patch.object(runtime_registry, "LOCAL_REGISTRY", directory / "application_registry.local.json"),
            patch.object(runtime_registry, "EXAMPLE_REGISTRY", directory / "application_registry.example.json"),
        )

    def test_local_registry_precedes_windows_and_public_projection_is_path_free(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            executable = directory / "airi.exe"
            executable.write_bytes(b"verified-airi")
            api_marker = "api" + "_key"
            self._registry(directory, self._airi_row(
                executable,
                display_name=f"C:/private/{api_marker}=must-not-escape",
                notes="C:/private/secret-token",
                path="C:/private/install",
            ))
            scopes = self._local_scope(directory)
            with scopes[0], scopes[1], scopes[2], patch.object(
                runtime_registry,
                "_read_file_identity",
                return_value={"product_name": "AIRI", "publisher": "Moeru AI"},
            ), patch.object(runtime_registry, "_discover_windows_candidates", side_effect=AssertionError("local registry must win")), patch.object(
                runtime_registry, "_running_process_snapshot", return_value=(frozenset({runtime_registry._normalize_process_path(str(executable))}), frozenset(), False)
            ):
                records = runtime_registry.applications(force=True)
            airi = next(item for item in records if item["id"] == "airi")
            self.assertTrue({"id", "display_name", "discovery_state", "launch_state", "launchable", "running", "running_state", "reason_code"}.issubset(airi))
            self.assertEqual(airi["discovery_state"], "verified")
            self.assertEqual(airi["launch_state"], "available")
            self.assertTrue(airi["launchable"])
            self.assertTrue(airi["running"])
            self.assertEqual(airi["running_state"], "running")
            self.assertEqual(airi["reason_code"], "airi_verified")
            encoded = json.dumps(airi, ensure_ascii=True).lower()
            for marker in ("c:/", "private", "api_key", "secret-token", "executable", "command"):
                self.assertNotIn(marker, encoded)

    def test_missing_local_registry_uses_injected_windows_identity_without_exposing_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            executable = directory / "airi.exe"
            executable.write_bytes(b"windows-airi")
            candidate = self._candidate(executable, "a" * 64)
            scopes = self._local_scope(directory)
            with scopes[0], scopes[1], scopes[2], patch.object(
                runtime_registry,
                "_discover_windows_candidates",
                return_value=([candidate], "airi_not_discovered", False),
            ), patch.object(
                runtime_registry,
                "_read_file_identity",
                return_value={"product_name": "AIRI", "publisher": "Moeru AI"},
            ), patch.object(runtime_registry, "_file_fingerprint", return_value=candidate.fingerprint), patch.object(
                runtime_registry,
                "_running_process_snapshot",
                return_value=(frozenset(), frozenset(), False),
            ):
                record = runtime_registry.application("airi")
            self.assertEqual(record["discovery_state"], "verified")
            self.assertEqual(record["discovery_source"], "windows_registry")
            self.assertTrue(record["launchable"])
            self.assertFalse({"path", "executable", "working_directory", "arguments", "command_line"} & set(record))

    def test_two_distinct_verified_candidates_are_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            first = directory / "airi-one.exe"
            second = directory / "airi-two.exe"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            candidates = [self._candidate(first, "1" * 64), self._candidate(second, "2" * 64)]
            scopes = self._local_scope(directory)
            self._registry(directory, self._airi_row(first), self._airi_row(second))
            with scopes[0], scopes[1], scopes[2], patch.object(runtime_registry, "_read_file_identity", return_value={"product_name": "AIRI", "publisher": "Moeru AI"}), patch.object(
                runtime_registry, "_file_fingerprint", side_effect=["1" * 64, "2" * 64, "1" * 64, "2" * 64]
            ):
                record = runtime_registry.application("airi")
                status, payload = runtime_registry.launch("airi")
            self.assertEqual(record["discovery_state"], "ambiguous")
            self.assertEqual(record["launch_state"], "ambiguous")
            self.assertFalse(record["launchable"])
            self.assertEqual(status, 409)
            self.assertEqual(payload["error"], "application_ambiguous")

    def test_identity_mismatch_script_and_reparse_targets_are_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            executable = directory / "airi.exe"
            executable.write_bytes(b"candidate")
            scopes = self._local_scope(directory)
            self._registry(directory, self._airi_row(executable))
            with scopes[0], scopes[1], scopes[2], patch.object(
                runtime_registry, "_read_file_identity", return_value={"product_name": "Other", "publisher": "Other Publisher"}
            ):
                mismatch = runtime_registry.application("airi")
            self.assertEqual(mismatch["discovery_state"], "unavailable")
            self.assertEqual(mismatch["reason_code"], "airi_local_identity_not_verified")

            script = directory / "airi.cmd"
            self._registry(directory, self._airi_row(script))
            with scopes[0], scopes[1], scopes[2]:
                script_projection = runtime_registry.application("airi")
            self.assertEqual(script_projection["reason_code"], "airi_local_script_target")

            self._registry(directory, self._airi_row(executable))
            with scopes[0], scopes[1], scopes[2], patch.object(runtime_registry, "_is_reparse", return_value=True):
                reparse_projection = runtime_registry.application("airi")
            self.assertEqual(reparse_projection["reason_code"], "airi_local_reparse_target")

            self._registry(directory, self._airi_row(executable, launch=False))
            with scopes[0], scopes[1], scopes[2]:
                disabled_projection = runtime_registry.application("airi")
            self.assertEqual(disabled_projection["reason_code"], "airi_local_launch_disabled")

    def test_launch_revalidates_identity_and_fingerprint_before_popen(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            executable = directory / "airi.exe"
            executable.write_bytes(b"candidate")
            self._registry(directory, self._airi_row(executable))
            scopes = self._local_scope(directory)
            with scopes[0], scopes[1], scopes[2], patch.object(
                runtime_registry, "_read_file_identity", return_value={"product_name": "AIRI", "publisher": "Moeru AI"}
            ), patch.object(runtime_registry, "_file_fingerprint", side_effect=["a" * 64, "b" * 64]), patch.object(
                runtime_registry, "popen_hidden", side_effect=AssertionError("stale candidate must not launch")
            ) as popen:
                status, payload = runtime_registry.launch("airi")
            self.assertEqual(status, 409)
            self.assertEqual(payload["error"], "application_candidate_changed")
            popen.assert_not_called()

    def test_launch_failure_is_stable_and_does_not_echo_os_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            executable = directory / "airi.exe"
            executable.write_bytes(b"candidate")
            self._registry(directory, self._airi_row(executable))
            scopes = self._local_scope(directory)
            with scopes[0], scopes[1], scopes[2], patch.object(
                runtime_registry, "_read_file_identity", return_value={"product_name": "AIRI", "publisher": "Moeru AI"}
            ), patch.object(runtime_registry, "_file_fingerprint", return_value="a" * 64), patch.object(
                runtime_registry, "popen_hidden", side_effect=OSError("could not open C:/private/airi.exe")
            ):
                status, payload = runtime_registry.launch("airi")
            self.assertEqual(status, 500)
            self.assertEqual(payload["error"], "application_launch_failed")
            encoded = json.dumps(payload, ensure_ascii=True).lower()
            self.assertNotIn("c:/", encoded)
            self.assertNotIn("private", encoded)

    def test_invalid_local_registry_does_not_fall_through_to_an_unrelated_windows_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "application_registry.local.json").write_text("[]", encoding="utf-8")
            scopes = self._local_scope(directory)
            with scopes[0], scopes[1], scopes[2], patch.object(
                runtime_registry, "_discover_windows_candidates", side_effect=AssertionError("invalid local registry must fail closed")
            ):
                record = runtime_registry.application("airi")
            self.assertEqual(record["discovery_state"], "unavailable")
            self.assertEqual(record["reason_code"], "airi_local_registry_invalid")

    def test_router_launch_is_id_only_and_does_not_accept_browser_executable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            executable = directory / "airi.exe"
            executable.write_bytes(b"candidate")
            self._registry(directory, self._airi_row(executable, arguments=["--from-registry"]))
            scopes = self._local_scope(directory)
            process = SimpleNamespace(pid=48123)
            with scopes[0], scopes[1], scopes[2], patch.object(
                runtime_registry, "_read_file_identity", return_value={"product_name": "AIRI", "publisher": "Moeru AI"}
            ), patch.object(runtime_registry, "_file_fingerprint", return_value="a" * 64), patch.object(
                runtime_registry, "popen_hidden", return_value=process
            ) as popen:
                context = ApiContext({"launch_application": runtime_registry.launch, "applications": runtime_registry.applications})
                request = ApiRequest(
                    method="POST",
                    path="/api/applications/airi/launch",
                    query={},
                    headers={},
                    _body_reader=lambda _strict: {"executable": "C:/attacker.exe", "arguments": ["--unsafe"]},
                )
                response = build_router().dispatch(request, context)
            self.assertEqual(response.status, 202)
            self.assertEqual(response.payload["application"], "airi")
            self.assertEqual(response.payload["pid"], 48123)
            argv = popen.call_args.args[0]
            self.assertEqual(argv[0], str(executable))
            self.assertEqual(argv[1:], ["--from-registry"])
            self.assertNotIn("attacker.exe", json.dumps(response.payload))

    def test_module_adapter_and_ui_use_the_sanitized_contract(self) -> None:
        adapter = (Path(__file__).resolve().parents[1] / "src/modules/airi/backend/adapter.py").read_text(encoding="utf-8")
        renderer = (Path(__file__).resolve().parents[1] / "src/ui/features/airi/render.js").read_text(encoding="utf-8")
        self.assertIn('application("airi")', adapter)
        self.assertNotIn('"executable"', adapter)
        self.assertIn("discovery_state", adapter)
        self.assertIn("launch_state", renderer)
        self.assertIn("data-refresh-applications", renderer)
        self.assertIn("Chưa tìm thấy AIRI", renderer)
        self.assertIn("data-launch=\"airi\"", renderer)
        self.assertEqual(renderer.count("data-launch=\"airi\""), 1)

    def test_acceptance_impact_classifies_airi_launch_separately_from_ai_runtime(self) -> None:
        from scripts.v8_acceptance_gate import _path_impact_scope

        for path in (
            "src/services/runtime_registry.py",
            "src/services/local_registry_recovery.py",
            "src/modules/airi/backend/adapter.py",
        ):
            self.assertEqual(_path_impact_scope(path), "application_launch")


if __name__ == "__main__":
    unittest.main()
