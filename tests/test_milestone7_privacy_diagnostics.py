"""Focused Milestone 7C contract, security and static-projection tests."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.services.privacy_diagnostics import (
    build_diagnostic_markdown,
    build_policy_diff_markdown,
    build_support_bundle_manifest,
    build_support_bundle_markdown,
    diagnose_snapshot,
    diff_privacy_policies,
    plan_privacy_policy_migration,
    plan_remediation,
    load_server_owned_policy,
    load_server_owned_snapshot,
)
from src.services.privacy_diagnostics import policy_catalog, snapshot_catalog
from src.services.privacy_diagnostics.io import export_privacy_policy, safe_import_diagnostic_snapshot, safe_import_privacy_policy
from src.shared.schemas.privacy_diagnostics import (
    MAX_DESCRIPTOR_BYTES,
    diagnostic_finding_schema,
    diagnostic_snapshot_schema,
    diagnostic_schemas,
    privacy_policy_schema,
    remediation_plan_schema,
    support_bundle_manifest_schema,
    validate_diagnostic_snapshot,
    validate_privacy_policy,
)


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "privacy_diagnostics" / "policies" / "default.privacy-policy.json"
SNAPSHOT_PATH = ROOT / "privacy_diagnostics" / "samples" / "diagnostic-snapshot.diagnostic-snapshot.json"


class Milestone7PrivacyDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        cls.snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        cls.owned_policy = load_server_owned_policy(cls.policy["id"])["policy"]
        cls.owned_snapshot = load_server_owned_snapshot(cls.snapshot["id"])["snapshot"]
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            Draft202012Validator = None
        cls.Draft202012Validator = Draft202012Validator

    def test_samples_validate_and_are_detached(self) -> None:
        self.assertTrue(validate_privacy_policy(self.policy)["valid"])
        self.assertTrue(validate_diagnostic_snapshot(self.snapshot)["valid"])
        imported = safe_import_privacy_policy(POLICY_PATH.read_bytes())
        self.assertTrue(imported["accepted"])
        imported["policy"]["label"] = "detached"
        self.assertNotEqual(imported["policy"]["label"], self.policy["label"])
        exported = export_privacy_policy(self.policy)
        self.assertTrue(exported["ready"])
        self.assertTrue(safe_import_privacy_policy(exported["content"])["accepted"])

    def test_schema_required_fields_are_structurally_complete(self) -> None:
        for schema in diagnostic_schemas():
            self.assertTrue(set(schema["required"]).issubset(set(schema["properties"])))

    def test_real_draft_schema_accepts_generated_contracts_and_rejects_python_rejects(self) -> None:
        if self.Draft202012Validator is None:
            self.skipTest("jsonschema is provided by the bounded hub test environment")
        policy = copy.deepcopy(self.policy)
        snapshot = copy.deepcopy(self.snapshot)
        diagnosis = diagnose_snapshot(self.owned_policy, self.owned_snapshot)
        bundle = build_support_bundle_manifest(self.owned_policy, self.owned_snapshot, diagnosis["findings"])
        plan = plan_remediation(self.owned_policy, self.owned_snapshot, diagnosis["findings"])
        values = [
            (privacy_policy_schema(), policy),
            (diagnostic_snapshot_schema(), snapshot),
            (diagnostic_finding_schema(), diagnosis["findings"][0]),
            (support_bundle_manifest_schema(), bundle["manifest"]),
            (remediation_plan_schema(), plan["plan"]),
        ]
        for schema, value in values:
            validator = self.Draft202012Validator(schema)
            self.assertFalse(list(validator.iter_errors(value)))
        bad = copy.deepcopy(policy)
        bad["label"] = "C:" + "relative\\secret.txt"
        self.assertFalse(validate_privacy_policy(bad)["valid"])
        self.assertTrue(list(self.Draft202012Validator(privacy_policy_schema()).iter_errors(bad)))
        bad = copy.deepcopy(policy)
        bad["unexpected"] = "field"
        self.assertFalse(validate_privacy_policy(bad)["valid"])
        self.assertTrue(list(self.Draft202012Validator(privacy_policy_schema()).iter_errors(bad)))

    def test_scrubber_rejects_paths_secrets_commands_and_payloads_without_echo(self) -> None:
        values = [
            "C:" + "relative\\secret.txt",
            "\\Windows\\System32",
            "../private",
            "api" + "key=small-secret-42",
            "powershell -NoProfile",
            "data:image/png;base64," + "AAAA",
        ]
        for value in values:
            bad = copy.deepcopy(self.policy)
            bad["label"] = value
            result = validate_privacy_policy(bad)
            self.assertFalse(result["valid"])
            self.assertNotIn(value, json.dumps(result))

    def test_json_import_is_fail_closed_for_duplicate_nonfinite_and_oversize(self) -> None:
        duplicate = safe_import_privacy_policy('{"id":"a","id":"b"}')
        self.assertFalse(duplicate["accepted"])
        self.assertEqual(duplicate["errors"][0]["code"], "duplicate_json_key")
        self.assertFalse(safe_import_privacy_policy('{"value":NaN}')["accepted"])
        self.assertFalse(safe_import_privacy_policy(b"{" + b"a" * (MAX_DESCRIPTOR_BYTES + 1))["accepted"])

    def test_raw_or_imported_snapshot_cannot_cross_trusted_diagnostic_boundary(self) -> None:
        imported = safe_import_diagnostic_snapshot(json.dumps(self.snapshot))
        self.assertTrue(imported["accepted"])
        raw_result = diagnose_snapshot(self.policy, self.snapshot)
        imported_result = diagnose_snapshot(self.owned_policy, imported)
        self.assertFalse(raw_result["valid"])
        self.assertFalse(imported_result["valid"])
        self.assertEqual(raw_result["errors"][0]["code"], "provenance_required")
        self.assertEqual(imported_result["errors"][0]["code"], "provenance_required")
        self.assertNotIn(self.snapshot["label"], json.dumps(imported_result))
        raw_bundle = build_support_bundle_manifest(self.policy, self.snapshot)
        raw_plan = plan_remediation(self.policy, self.snapshot)
        self.assertEqual(raw_bundle["errors"][0]["code"], "provenance_required")
        self.assertEqual(raw_plan["errors"][0]["code"], "provenance_required")

    def test_policy_snapshot_provenance_mismatch_is_rejected_without_echo(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_root = snapshot_catalog.MANAGED_DIAGNOSTIC_SNAPSHOT_ROOT
            snapshot_catalog.MANAGED_DIAGNOSTIC_SNAPSHOT_ROOT = root
            try:
                mismatched = copy.deepcopy(self.snapshot)
                mismatched["policy_id"] = "another-policy"
                path = root / "mismatch.diagnostic-snapshot.json"
                path.write_text(json.dumps(mismatched), encoding="utf-8")
                loaded = load_server_owned_snapshot(mismatched["id"])
                self.assertTrue(loaded["found"])
                result = diagnose_snapshot(self.owned_policy, loaded["snapshot"])
                self.assertFalse(result["valid"])
                self.assertEqual(result["errors"][0]["code"], "policy_snapshot_mismatch")
                self.assertNotIn(mismatched["label"], json.dumps(result))
            finally:
                snapshot_catalog.MANAGED_DIAGNOSTIC_SNAPSHOT_ROOT = old_root

    def test_forged_schema_valid_finding_cannot_be_sealed_or_planned(self) -> None:
        diagnosis = diagnose_snapshot(self.owned_policy, self.owned_snapshot)
        forged = copy.deepcopy(diagnosis["findings"])
        forged[0]["evidence_digest"] = "0" * 64
        bundle = build_support_bundle_manifest(self.owned_policy, self.owned_snapshot, forged)
        plan = plan_remediation(self.owned_policy, self.owned_snapshot, forged)
        self.assertFalse(bundle["valid"])
        self.assertFalse(plan["valid"])
        self.assertEqual(bundle["errors"][0]["code"], "finding_provenance_mismatch")
        self.assertEqual(plan["errors"][0]["code"], "finding_provenance_mismatch")
        self.assertNotIn("0" * 64, json.dumps(bundle))
        self.assertNotIn("0" * 64, json.dumps(plan))

    def test_diagnostics_are_deterministic_and_scrubbed(self) -> None:
        before_policy = copy.deepcopy(self.policy)
        before_snapshot = copy.deepcopy(self.snapshot)
        first = diagnose_snapshot(self.owned_policy, self.owned_snapshot)
        second = diagnose_snapshot(self.owned_policy, self.owned_snapshot)
        self.assertEqual(first, second)
        self.assertEqual(first["execution"], "not_run")
        self.assertTrue(any(item["rule_id"] == "config_drift" for item in first["findings"]))
        self.assertTrue(any(item["rule_id"] == "contract_stale" for item in first["findings"]))
        self.assertTrue(any(item["rule_id"] == "missing_evidence" for item in first["findings"]))
        self.assertTrue(build_diagnostic_markdown(self.owned_policy, self.owned_snapshot)["ready"])
        text = json.dumps(first, sort_keys=True)
        self.assertNotIn(self.policy["label"], text)
        self.assertEqual(self.policy, before_policy)
        self.assertEqual(self.snapshot, before_snapshot)

    def test_support_and_remediation_are_dry_run_and_require_consent(self) -> None:
        diagnosis = diagnose_snapshot(self.owned_policy, self.owned_snapshot)
        bundle = build_support_bundle_manifest(self.owned_policy, self.owned_snapshot, diagnosis["findings"])
        self.assertTrue(bundle["valid"])
        self.assertEqual(bundle["manifest"]["execution"], "not_run")
        rendered = build_support_bundle_markdown(bundle)
        self.assertTrue(rendered["ready"])
        self.assertNotIn(self.policy["label"], rendered["content"])
        plan = plan_remediation(self.owned_policy, self.owned_snapshot, diagnosis["findings"])
        self.assertTrue(plan["valid"])
        self.assertTrue(plan["plan"]["dry_run"])
        self.assertEqual(plan["plan"]["execution"], "not_run")
        self.assertTrue(all("risk" in action and "owner" in action and "rollback_code" in action for action in plan["plan"]["actions"]))
        revoked = copy.deepcopy(self.snapshot)
        revoked["consent"]["state"] = "revoked"
        denied = build_support_bundle_manifest(self.policy, revoked, diagnosis["findings"])
        self.assertFalse(denied["valid"])
        self.assertEqual(denied["errors"][0]["code"], "provenance_required")

    def test_policy_diff_detects_metadata_and_restrictive_changes(self) -> None:
        unchanged = diff_privacy_policies(self.policy, copy.deepcopy(self.policy))
        self.assertEqual(unchanged["status"], "unchanged")
        self.assertFalse(unchanged["changes"])
        reordered = copy.deepcopy(self.policy)
        for key in ("components", "config_keys", "tool_ids", "contract_ids"):
            reordered[key] = list(reversed(reordered[key]))
        self.assertEqual(diff_privacy_policies(self.policy, reordered)["status"], "unchanged")
        changed = copy.deepcopy(self.policy)
        changed["label"] = "A different safe label"
        diff = diff_privacy_policies(self.policy, changed)
        self.assertEqual(diff["status"], "changed")
        self.assertEqual(diff["changes"], [{"kind": "policy_metadata_changed"}])
        self.assertNotIn(changed["label"], json.dumps(diff))
        migration = plan_privacy_policy_migration(self.policy, changed)
        self.assertEqual(migration["status"], "planned")
        self.assertNotEqual(migration["status"], "not_required")
        changed["retention"]["days"] = 1
        restrictive = plan_privacy_policy_migration(self.policy, changed)
        self.assertEqual(restrictive["status"], "manual_review")
        self.assertTrue(restrictive["dry_run"])
        self.assertTrue(build_policy_diff_markdown(diff)["ready"])

    def test_projection_rejects_client_built_report_and_markdown_is_safe(self) -> None:
        diagnosis = diagnose_snapshot(self.owned_policy, self.owned_snapshot)
        rejected = build_support_bundle_manifest(self.owned_policy, self.owned_snapshot, {"findings": diagnosis["findings"]})
        self.assertFalse(rejected["valid"])
        self.assertEqual(rejected["errors"][0]["code"], "finding_provenance_mismatch")
        self.assertFalse(build_support_bundle_markdown({"manifest": {"label": "unsafe"}})["ready"])
        self.assertFalse(build_policy_diff_markdown({"valid": True, "status": "changed", "from": {"id": "evil", "fingerprint": "x"}, "to": {"id": "evil", "fingerprint": "y"}, "changes": [{"kind": "<script>"}]} )["ready"])

    def test_fixed_root_discovery_refuses_duplicate_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_root = policy_catalog.MANAGED_PRIVACY_POLICY_ROOT
            policy_catalog.MANAGED_PRIVACY_POLICY_ROOT = root
            try:
                first_path = root / "one.privacy-policy.json"
                second_path = root / "two.privacy-policy.json"
                first_path.write_text(json.dumps(self.policy), encoding="utf-8")
                duplicate = copy.deepcopy(self.policy)
                duplicate["revision"] = 2
                second_path.write_text(json.dumps(duplicate), encoding="utf-8")
                result = policy_catalog.discover_managed_privacy_policies()
                self.assertEqual(result["status"], "unavailable")
                self.assertIn({"code": "managed_policy_identity_ambiguous"}, result["errors"])
                self.assertNotIn(self.policy["label"], json.dumps(result))
            finally:
                policy_catalog.MANAGED_PRIVACY_POLICY_ROOT = old_root

    def test_reader_refuses_oversize_before_open_and_bounds_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_root = policy_catalog.MANAGED_PRIVACY_POLICY_ROOT
            policy_catalog.MANAGED_PRIVACY_POLICY_ROOT = root
            try:
                path = root / "default.privacy-policy.json"
                path.write_bytes(b"{" + b"a" * MAX_DESCRIPTOR_BYTES + b"}")
                with patch.object(policy_catalog.os, "open", wraps=policy_catalog.os.open) as opened:
                    payload, error = policy_catalog._read_managed_json(path)
                self.assertIsNone(payload)
                self.assertEqual(error, "managed_policy_size")
                opened.assert_not_called()
                path.write_text(json.dumps(self.policy), encoding="utf-8")
                with patch.object(policy_catalog.os, "read", wraps=policy_catalog.os.read) as reader:
                    payload, error = policy_catalog._read_managed_json(path)
                self.assertIsNotNone(payload)
                self.assertIsNone(error)
                self.assertEqual(reader.call_count, 1)
                self.assertEqual(reader.call_args.args[1], MAX_DESCRIPTOR_BYTES + 1)
            finally:
                policy_catalog.MANAGED_PRIVACY_POLICY_ROOT = old_root

    def test_reader_fails_closed_when_descriptor_mutates_after_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "default.privacy-policy.json"
            path.write_text(json.dumps(self.policy), encoding="utf-8")
            old_root = policy_catalog.MANAGED_PRIVACY_POLICY_ROOT
            policy_catalog.MANAGED_PRIVACY_POLICY_ROOT = root
            original_read = policy_catalog.os.read

            def read_then_mutate(fd: int, count: int) -> bytes:
                data = original_read(fd, count)
                path.write_text(json.dumps(self.policy) + " ", encoding="utf-8")
                return data

            try:
                with patch.object(policy_catalog.os, "read", side_effect=read_then_mutate):
                    payload, error = policy_catalog._read_managed_json(path)
                self.assertIsNone(payload)
                self.assertEqual(error, "managed_policy_refused")
            finally:
                policy_catalog.MANAGED_PRIVACY_POLICY_ROOT = old_root

    def test_cli_report_is_static(self) -> None:
        from scripts.validate_privacy_diagnostics import _json_report, _markdown

        report = _json_report()
        self.assertEqual(report["contract"], "privacy-diagnostics-static-validation.v1")
        self.assertEqual(report["execution"], "not_run")
        rendered = _markdown(report)
        self.assertNotIn(self.policy["label"], rendered)


if __name__ == "__main__":
    unittest.main()
