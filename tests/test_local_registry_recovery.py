from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.services import local_registry_recovery as recovery
from src.services.api import config


ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class LocalRegistryRecoveryTests(unittest.TestCase):
    def _copy_examples(self, directory: Path) -> None:
        for name in (
            "components.example.json",
            "hub_config.example.json",
            "model_registry.example.json",
            "application_registry.example.json",
        ):
            (directory / name).write_bytes((ROOT / "Config" / name).read_bytes())

    def test_missing_and_example_values_keep_explicit_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            missing = config.read_local_config("components.json", {}, config_dir=directory, example_name="components.example.json")
            self.assertEqual(missing["provenance"], "missing")
            self._copy_examples(directory)
            example = config.read_local_config("components.json", {}, config_dir=directory, example_name="components.example.json")
            self.assertEqual(example["provenance"], "example_template")
            snapshot = recovery.inspect_registry(config_dir=directory)
            self.assertEqual(snapshot["registry_provenance"], "example_or_missing")
            self.assertTrue(all(item["provenance"] == "example_template" for item in snapshot["targets"]))

    def test_non_object_and_malformed_local_config_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "components.json").write_text("[]", encoding="utf-8")
            snapshot = recovery.inspect_registry(config_dir=directory)
            self.assertEqual(snapshot["status"], "error")
            self.assertEqual(snapshot["errors"][0]["code"], "schema_mismatch")
            (directory / "components.json").write_text("{", encoding="utf-8")
            snapshot = recovery.inspect_registry(config_dir=directory)
            self.assertEqual(snapshot["errors"][0]["code"], "malformed_local")

    def test_offline_inspect_and_plan_do_not_call_service_or_process_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch("scripts.refresh_managed_registry.urlopen", side_effect=AssertionError("no network")), patch(
            "src.services.runtime_registry._running_executables", side_effect=AssertionError("no tasklist")
        ):
            directory = Path(temporary)
            snapshot = recovery.inspect_registry(config_dir=directory)
            self.assertEqual(snapshot["execution"], "not_run")
            plan = recovery.plan_registry(config_dir=directory)
            self.assertEqual(plan["execution"], "not_run")
            self.assertTrue(plan["dry_run"])

    def test_example_application_registry_is_unavailable_and_not_launchable(self) -> None:
        from src.services import runtime_registry

        with tempfile.TemporaryDirectory() as temporary, patch.object(runtime_registry, "CONFIG_ROOT", Path(temporary)), patch.object(
            runtime_registry, "LOCAL_REGISTRY", Path(temporary) / "application_registry.local.json"
        ), patch.object(
            runtime_registry, "EXAMPLE_REGISTRY", ROOT / "Config" / "application_registry.example.json"
        ), patch.object(runtime_registry, "_running_executables", side_effect=AssertionError("example must not query processes")):
            (Path(temporary) / "application_registry.example.json").write_bytes(
                (ROOT / "Config" / "application_registry.example.json").read_bytes()
            )
            records = runtime_registry.applications()
        self.assertTrue(records)
        self.assertTrue(all(item["registry_provenance"] == "example_template" for item in records))
        self.assertTrue(all(item["component_status"] == "unavailable" for item in records))
        self.assertTrue(all(item["launchable"] is False for item in records))

    def test_known_only_apply_is_atomic_and_stays_not_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            plan = recovery.plan_registry(config_dir=directory)
            result = recovery.apply_plan(plan, config_dir=directory)
            self.assertEqual(result["status"], "applied")
            self.assertEqual(result["execution"], "not_run")
            self.assertFalse(result["dry_run"])
            self.assertFalse((directory / recovery.JOURNAL_NAME).exists())
            self.assertEqual({path.name for path in directory.glob("*.json")}, set(recovery.TARGETS))
            documents = {
                name: json.loads((directory / name).read_text(encoding="utf-8"))
                for name in recovery.TARGETS
            }
            self.assertTrue(all(row.get("execution") == "not_run" for row in documents["components.json"]["components"]))
            self.assertTrue(all(row.get("execution") == "not_run" for row in documents["model_registry.json"]["models"]))
            self.assertTrue(all(row.get("launch") is False for row in documents["application_registry.local.json"]["applications"]))

    def test_intervening_hash_change_rejects_without_echo(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            plan = recovery.plan_registry(config_dir=directory)
            (directory / "components.json").write_text('{"schema_version": 3, "components": []}', encoding="utf-8")
            result = recovery.apply_plan(plan, config_dir=directory)
            self.assertEqual(result["errors"][0]["code"], "stale_input")
            self.assertNotIn(str(directory), json.dumps(result))

    def test_unknown_id_field_duplicate_and_sensitive_value_fail_closed(self) -> None:
        cases = (
            ({"schema_version": 3, "components": [{"id": "unknown-row"}]}, "unknown_existing_id"),
            ({"schema_version": 3, "components": [{"id": "animesr", "unexpected": True}]}, "unknown_existing_field"),
            ({"schema_version": 3, "components": [{"id": "animesr"}, {"id": "animesr"}]}, "duplicate_or_invalid_id"),
            ({"schema_version": 3, "components": [{"id": "animesr", "source": "sec" + "ret-value"}]}, "unsafe_existing_value"),
        )
        for value, code in cases:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                _write(directory / "components.json", value)
                plan = recovery.plan_registry(config_dir=directory)
                self.assertEqual(plan["status"], "error")
                self.assertEqual(plan["errors"][0]["code"], code)
                self.assertNotIn("unknown-row", json.dumps(plan))

    def test_reparse_root_rejected_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(recovery, "_is_reparse", return_value=True):
            snapshot = recovery.inspect_registry(config_dir=Path(temporary))
            self.assertEqual(snapshot["status"], "unavailable")
            self.assertEqual(snapshot["errors"][0]["code"], "reparse_config_root")

    def test_writer_failure_leaves_bounded_journal_and_resume_is_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            plan = recovery.plan_registry(config_dir=directory)
            original = recovery._write_atomic

            def fail_model(path: Path, payload: bytes) -> None:
                if path.name == "model_registry.json":
                    raise recovery.LocalRegistryError("atomic_write_failed")
                original(path, payload)

            with patch.object(recovery, "_write_atomic", side_effect=fail_model):
                failed = recovery.apply_plan(plan, config_dir=directory)
            self.assertEqual(failed["errors"][0]["code"], "atomic_write_failed")
            self.assertTrue((directory / recovery.JOURNAL_NAME).exists())
            resumed = recovery.resume_journal(plan, config_dir=directory)
            self.assertEqual(resumed["status"], "applied")
            self.assertFalse((directory / recovery.JOURNAL_NAME).exists())

    def test_stale_plan_and_schema_mismatch_never_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            plan = recovery.plan_registry(config_dir=directory)
            altered = dict(plan)
            altered["plan_fingerprint"] = "0" * 64
            result = recovery.apply_plan(altered, config_dir=directory)
            self.assertEqual(result["errors"][0]["code"], "stale_or_invalid_plan")
            malformed = dict(plan)
            malformed["targets"] = ["arbitrary.json"]
            result = recovery.apply_plan(malformed, config_dir=directory)
            self.assertEqual(result["errors"][0]["code"], "unknown_target")

    def test_attestation_inputs_are_finite_and_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            one = recovery.plan_registry(config_dir=Path(first))
            two = recovery.plan_registry(config_dir=Path(second))
            self.assertEqual(one["candidate_fingerprints"], two["candidate_fingerprints"])
            self.assertEqual(one["plan_fingerprint"], two["plan_fingerprint"])
            self.assertTrue(all(len(value) == 64 for value in one["candidate_fingerprints"].values()))


if __name__ == "__main__":
    unittest.main()
