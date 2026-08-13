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

    def test_example_components_cannot_become_running_but_local_components_remain_observable(self) -> None:
        from src.services.api import core

        with tempfile.TemporaryDirectory() as temporary, patch.object(config, "CONFIG_DIR", Path(temporary)), patch.object(
            core, "_port_open", return_value=True
        ):
            directory = Path(temporary)
            (directory / "components.example.json").write_bytes(
                (ROOT / "Config" / "components.example.json").read_bytes()
            )
            self.assertEqual(core.component_statuses(), [])

            _write(directory / "components.json", {
                "schema_version": 3,
                "components": [{"id": "animesr", "name": "AnimeSR", "port": 43123}],
            })
            local_statuses = core.component_statuses()
            self.assertEqual(len(local_statuses), 1)
            self.assertEqual(local_statuses[0]["component_status"], "running")

    def test_known_only_apply_is_atomic_and_stays_not_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            plan = recovery.plan_registry(config_dir=directory)
            self.assertEqual(plan["status"], "partial")
            self.assertTrue(plan["apply_allowed"])
            self.assertEqual(plan["auto_targets"], sorted(recovery.AUTO_TARGETS))
            self.assertEqual(plan["source_consumer"]["source"]["relative_path"], "scripts/refresh_managed_registry.py")
            self.assertEqual(plan["source_consumer"]["consumer"]["relative_path"], "src/services/api/core.py")
            self.assertTrue(plan["relative_leaf_attestation"]["fresh"])
            result = recovery.apply_plan(plan, config_dir=directory)
            self.assertEqual(result["status"], "applied")
            self.assertEqual(result["execution"], "not_run")
            self.assertFalse(result["dry_run"])
            self.assertFalse((directory / recovery.JOURNAL_NAME).exists())
            self.assertEqual({path.name for path in directory.glob("*.json")}, set(recovery.AUTO_TARGETS))
            documents = {
                name: json.loads((directory / name).read_text(encoding="utf-8"))
                for name in recovery.AUTO_TARGETS
            }
            self.assertTrue(all(row.get("execution") == "not_run" for row in documents["components.json"]["components"]))
            self.assertTrue(all(row.get("execution") == "not_run" for row in documents["model_registry.json"]["models"]))
            self.assertTrue(all(row.get("recovery_state") == "recovered_static" for row in documents["components.json"]["components"]))

    def test_recovered_static_component_never_promotes_through_core(self) -> None:
        from src.services.api import core

        with tempfile.TemporaryDirectory() as temporary, patch.object(config, "CONFIG_DIR", Path(temporary)), patch.object(
            core, "_port_open", return_value=True
        ):
            plan = recovery.plan_registry(config_dir=Path(temporary))
            recovery.apply_plan(plan, config_dir=Path(temporary))
            statuses = core.component_statuses()
            self.assertTrue(statuses)
            self.assertTrue(all(item["component_status"] == "unavailable" for item in statuses))
            self.assertTrue(all(item["configured_component_status"] == "configured" for item in statuses))

    def test_intervening_hash_change_rejects_without_echo(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            plan = recovery.plan_registry(config_dir=directory)
            (directory / "components.json").write_text('{"schema_version": 3, "components": []}', encoding="utf-8")
            result = recovery.apply_plan(plan, config_dir=directory)
            self.assertEqual(result["errors"][0]["code"], "target_conflict")
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
                self.assertEqual(plan["status"], "partial")
                self.assertTrue(any(code in item["code"] for item in plan["errors"]))
                component_row = next(item for item in plan["targets"] if item["target"] == "components.json")
                self.assertEqual(component_row["decision"], "manual_review")
                self.assertNotIn("unknown-row", json.dumps(plan))

    def test_reparse_root_rejected_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(config, "_is_reparse", return_value=True):
            snapshot = recovery.inspect_registry(config_dir=Path(temporary))
            self.assertEqual(snapshot["status"], "unavailable")
            self.assertEqual(snapshot["errors"][0]["code"], "reparse_config_root")

    def test_reparse_target_is_rejected_before_read_or_journal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            clean_plan = recovery.plan_registry(config_dir=directory)
            sentinel = b"preserve-this-target"
            target = directory / "components.json"
            target.write_bytes(sentinel)

            def target_reparse(path: Path) -> bool:
                return path.name == "components.json"

            with patch.object(config, "_is_reparse", side_effect=target_reparse):
                snapshot = recovery.inspect_registry(config_dir=directory)
                self.assertEqual(snapshot["status"], "unavailable")
                self.assertEqual(snapshot["errors"][0]["code"], "reparse_target")
                plan = recovery.plan_registry(config_dir=directory)
                self.assertEqual(plan["status"], "error")
                result = recovery.apply_plan(clean_plan, config_dir=directory)
                self.assertEqual(result["errors"][0]["code"], "reparse_target")
                self.assertEqual(target.read_bytes(), sentinel)
                self.assertFalse((directory / recovery.JOURNAL_NAME).exists())

    def test_default_script_entrypoint_is_inspect_plan_only(self) -> None:
        from scripts import refresh_managed_registry

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "Config"
            directory.mkdir()
            inspect = recovery.inspect_registry
            plan = recovery.plan_registry

            with patch.object(
                recovery,
                "inspect_registry",
                side_effect=lambda: inspect(config_dir=directory),
            ), patch.object(
                recovery,
                "plan_registry",
                side_effect=lambda: plan(config_dir=directory),
            ), patch.object(recovery, "apply_plan", side_effect=AssertionError("default entrypoint must not apply")):
                self.assertEqual(refresh_managed_registry.main(), 0)

            self.assertEqual(list(directory.iterdir()), [])

    def test_writer_failure_leaves_bounded_journal_and_resume_is_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            plan = recovery.plan_registry(config_dir=directory)
            original = recovery._write_new_atomic

            def fail_model(path: Path, payload: bytes) -> None:
                if path.name == "model_registry.json":
                    raise recovery.LocalRegistryError("atomic_write_failed")
                original(path, payload)

            with patch.object(recovery, "_write_new_atomic", side_effect=fail_model):
                failed = recovery.apply_plan(plan, config_dir=directory)
            self.assertEqual(failed["errors"][0]["code"], "atomic_write_failed")
            self.assertTrue((directory / recovery.JOURNAL_NAME).exists())
            resumed = recovery.resume_journal(plan, config_dir=directory)
            self.assertEqual(resumed["status"], "applied")
            self.assertFalse((directory / recovery.JOURNAL_NAME).exists())

    def test_plan_marks_present_hub_and_application_manual_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "hub_config.json").write_text('{"schema_version": 3}', encoding="utf-8")
            plan = recovery.plan_registry(config_dir=directory)
            self.assertEqual(next(item for item in plan["targets"] if item["target"] == "hub_config.json")["decision"], "manual_review")
            self.assertEqual(next(item for item in plan["targets"] if item["target"] == "hub_config.json")["reason"], "target_present_manual_review")
            self.assertFalse((directory / recovery.JOURNAL_NAME).exists())

    def test_plan_is_redacted_and_special_leaves_are_manual_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            plan = recovery.plan_registry(config_dir=directory)
            serialized = json.dumps(plan, ensure_ascii=True)
            self.assertNotIn(str(directory), serialized)
            self.assertNotIn("http://", serialized)
            self.assertNotIn("https://", serialized)
            self.assertEqual(len(plan["fixed_ids"]["components.json"]), 10)
            self.assertEqual(len(plan["fixed_ids"]["model_registry.json"]), 8)
            self.assertEqual(len(plan["fixed_ids"]["application_registry.local.json"]), 6)
            for row in plan["relative_leaf_attestation"]["rows"]:
                if row["id"] in recovery.MANUAL_LEAF_IDS:
                    self.assertEqual(row["state"], "manual_review")

    def test_source_consumer_mismatch_and_process_preflight_write_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            plan = recovery.plan_registry(config_dir=directory)
            mismatched = dict(plan)
            mismatched["source_consumer"] = dict(plan["source_consumer"])
            mismatched["source_consumer"]["source"] = dict(plan["source_consumer"]["source"])
            mismatched["source_consumer"]["source"]["head"] = "0" * 40
            result = recovery.apply_plan(mismatched, config_dir=directory)
            self.assertEqual(result["errors"][0]["code"], "source_consumer_mismatch")
            self.assertFalse((directory / recovery.JOURNAL_NAME).exists())

            with patch.object(recovery, "_owned_process_count", return_value=1):
                result = recovery.apply_plan(plan, config_dir=directory)
            self.assertEqual(result["errors"][0]["code"], "owned_process_present")
            self.assertFalse((directory / recovery.JOURNAL_NAME).exists())

    def test_journal_contains_only_fixed_hash_status_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            plan = recovery.plan_registry(config_dir=directory)
            original = recovery._write_new_atomic

            def fail_model(path: Path, payload: bytes) -> None:
                if path.name == "model_registry.json":
                    raise recovery.LocalRegistryError("atomic_write_failed")
                original(path, payload)

            with patch.object(recovery, "_write_new_atomic", side_effect=fail_model):
                result = recovery.apply_plan(plan, config_dir=directory)
            self.assertEqual(result["errors"][0]["code"], "atomic_write_failed")
            journal = json.loads((directory / recovery.JOURNAL_NAME).read_text(encoding="utf-8"))
            self.assertEqual(set(journal), {
                "schema_version", "plan_fingerprint", "targets", "expected_input_hashes",
                "desired_hashes", "status", "controller_identity", "completed",
            })
            self.assertNotIn(str(directory), json.dumps(journal))

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
            self.assertEqual(result["errors"][0]["code"], "target_shape_invalid")

    def test_attestation_inputs_are_finite_and_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            one = recovery.plan_registry(config_dir=Path(first))
            two = recovery.plan_registry(config_dir=Path(second))
            self.assertEqual(one["candidate_fingerprints"], two["candidate_fingerprints"])
            self.assertEqual(one["plan_fingerprint"], two["plan_fingerprint"])
            self.assertTrue(all(len(value) == 64 for value in one["candidate_fingerprints"].values()))


if __name__ == "__main__":
    unittest.main()
