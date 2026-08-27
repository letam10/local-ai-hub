"""V8 Wave 5 acceptance preflight tests.

The suite validates tracked contracts and synthetic bounded evidence/report
bundles only. It never treats Linux CI as Windows acceptance and never creates a
version, tag, installer, release artifact, runtime or model workload.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.v8_acceptance_gate import (
    EVIDENCE_SCHEMA_VERSION,
    REPORT_SCHEMA_VERSION,
    AcceptanceGateError,
    current_head,
    evaluate,
    load_gate_contract,
    release_provenance_snapshot,
    source_preflight,
)


class V8Wave5AcceptanceGateTests(unittest.TestCase):
    def _write_bundle(self, repo: Path, root: Path, *, source_commit: str | None = None, status: str = "PASS", tamper_digest: bool = False) -> Path:
        contract = load_gate_contract(repo / "architecture" / "v8_acceptance_gates.json")
        source = source_commit or current_head(repo)
        reports = root / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        gates: dict[str, dict[str, object]] = {}
        for item in contract["gates"]:
            if item["required"] is not True:
                continue
            gate_id = item["gate_id"]
            digest: str | None = None
            if status == "PASS":
                required_checks = list(item["required_checks"])
                report = {
                    "schema_version": REPORT_SCHEMA_VERSION,
                    "gate_id": gate_id,
                    "status": "PASS",
                    "platform": contract["required_platform"],
                    "source_commit": source,
                    "checks": {check_id: True for check_id in required_checks},
                }
                raw = json.dumps(report, sort_keys=True).encode("utf-8")
                (reports / f"{gate_id}.json").write_bytes(raw)
                digest = hashlib.sha256(raw).hexdigest()
                if tamper_digest:
                    digest = ("0" if digest[0] != "0" else "1") + digest[1:]
            gates[gate_id] = {"status": status, "report_sha256": digest}
        evidence = {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "evidence_class": contract["required_evidence_class"],
            "platform": contract["required_platform"],
            "source_commit": source,
            "gates": gates,
        }
        evidence_path = root / "evidence.json"
        evidence_path.write_text(json.dumps(evidence, sort_keys=True), encoding="utf-8")
        return evidence_path

    def test_source_preflight_reports_prepared_release_identity(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        source = source_preflight(repo)
        provenance = release_provenance_snapshot(repo)
        self.assertTrue(source["valid"])
        self.assertEqual(source["missing_source_files"], [])
        self.assertTrue(provenance["contract_valid"])
        self.assertEqual(provenance["generation"], "V8")
        self.assertEqual(provenance["release_branch"], "feature/local-ai-hub-v8")
        self.assertTrue(provenance["identity_approved"])
        self.assertEqual(provenance["candidate_version"], "8.0.1")
        self.assertIsNone(provenance["candidate_tag"])
        self.assertTrue(provenance["product_version_matches"])
        self.assertTrue(provenance["technical_ready"])
        self.assertTrue(provenance["merge_ready"])
        self.assertIsNone(provenance["tag_exists"])

        result = evaluate(repo_root=repo)
        self.assertFalse(result["release_ready"])
        self.assertTrue(result["technical_ready"])
        self.assertFalse(result["merge_ready"])
        self.assertIn("LOCAL_WINDOWS_EVIDENCE_REQUIRED", result["blockers"])

    def test_all_pass_evidence_reports_are_verified_and_release_ready(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            evidence_path = self._write_bundle(repo, Path(temp))
            result = evaluate(evidence_path=evidence_path, repo_root=repo)
        self.assertTrue(result["local_evidence"]["valid"])
        self.assertTrue(result["local_evidence"]["reports_verified"])
        self.assertTrue(result["local_evidence"]["source_commit_matches"])
        self.assertEqual(result["local_evidence"]["pending_gates"], [])
        self.assertFalse(result["release_ready"])
        self.assertTrue(result["technical_ready"])
        self.assertTrue(result["merge_ready"])
        self.assertEqual(result["blockers"], [])

    def test_declared_pass_without_local_report_is_rejected(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            evidence_path = self._write_bundle(repo, root)
            first = next((root / "reports").iterdir())
            first.unlink()
            result = evaluate(evidence_path=evidence_path, repo_root=repo)
        self.assertFalse(result["local_evidence"]["valid"])
        self.assertIn("EVIDENCE_REPORT_MISSING", result["blockers"])

    def test_declared_report_digest_must_match_actual_file(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            evidence_path = self._write_bundle(repo, Path(temp), tamper_digest=True)
            result = evaluate(evidence_path=evidence_path, repo_root=repo)
        self.assertFalse(result["local_evidence"]["valid"])
        self.assertIn("EVIDENCE_REPORT_DIGEST_MISMATCH", result["blockers"])

    def test_evidence_is_bound_to_exact_source_commit(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        wrong = "0" * 40
        if current_head(repo) == wrong:
            wrong = "1" * 40
        with tempfile.TemporaryDirectory() as temp:
            evidence_path = self._write_bundle(repo, Path(temp), source_commit=wrong)
            result = evaluate(evidence_path=evidence_path, repo_root=repo)
        self.assertTrue(result["local_evidence"]["valid"])
        self.assertTrue(result["local_evidence"]["reports_verified"])
        self.assertFalse(result["local_evidence"]["source_commit_matches"])
        self.assertIn("EVIDENCE_SOURCE_COMMIT_MISMATCH", result["blockers"])

    def test_evidence_schema_rejects_path_like_extra_fields(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            evidence_path = self._write_bundle(repo, root)
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence["raw_path"] = r"D:\\LocalAIHub\\Reports\\acceptance.json"
            evidence_path.write_text(json.dumps(evidence, sort_keys=True), encoding="utf-8")
            result = evaluate(evidence_path=evidence_path, repo_root=repo)
        self.assertFalse(result["local_evidence"]["valid"])
        self.assertIn("EVIDENCE_ROOT_INVALID", result["blockers"])
        self.assertNotIn("raw_path", json.dumps(result, sort_keys=True))

    def test_gate_contract_is_strict_and_user_controlled_for_release_mutations(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        contract = load_gate_contract(repo / "architecture" / "v8_acceptance_gates.json")
        self.assertEqual(contract["generation"], "V8")
        self.assertEqual(contract["required_platform"], "windows-x64")
        self.assertEqual(contract["required_evidence_class"], "local_windows")
        self.assertTrue(all(item["required"] is True for item in contract["gates"]))
        self.assertTrue(all(item["required_checks"] for item in contract["gates"]))
        self.assertTrue(all(len(item["required_checks"]) == len(set(item["required_checks"])) for item in contract["gates"]))
        by_id = {item["gate_id"]: set(item["required_checks"]) for item in contract["gates"]}
        self.assertTrue({"route_render", "real_navigation", "dark", "light", "dpi_100", "dpi_125", "dpi_150", "degraded_error_recovery", "frontend_ready", "normal_close", "trusted_native_interaction"}.issubset(by_id["webview2_product_ux"]))
        self.assertTrue({"api_start_failure_or_frontend_timeout", "watchdog_rollback_previous_relaunch"}.issubset(by_id["crash_recovery"]))
        self.assertIn("real_lightweight_helper_execution", by_id["real_component_lifecycle"])
        self.assertEqual(contract["release_policy"]["main_merge"], "user_approved_only")
        self.assertEqual(contract["release_policy"]["version_change"], "user_approved_only")
        self.assertEqual(contract["release_policy"]["tag_change"], "user_approved_only")
        self.assertFalse(contract["release_policy"]["machine_local_evidence_committed_to_git"])

    def test_gate_loader_rejects_unknown_root_fields(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        contract = load_gate_contract(repo / "architecture" / "v8_acceptance_gates.json")
        contract["unexpected"] = True
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "gates.json"
            path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaises(AcceptanceGateError) as caught:
                load_gate_contract(path)
        self.assertEqual(caught.exception.code, "GATE_CONTRACT_ROOT_INVALID")

    def test_pass_report_missing_declared_check_is_rejected(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            evidence_path = self._write_bundle(repo, root)
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            gate_id = next(iter(evidence["gates"]))
            report_path = root / "reports" / f"{gate_id}.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            removed = next(iter(report["checks"]))
            report["checks"].pop(removed)
            raw = json.dumps(report, sort_keys=True).encode("utf-8")
            report_path.write_bytes(raw)
            evidence["gates"][gate_id]["report_sha256"] = hashlib.sha256(raw).hexdigest()
            evidence_path.write_text(json.dumps(evidence, sort_keys=True), encoding="utf-8")
            result = evaluate(evidence_path=evidence_path, repo_root=repo)
        self.assertFalse(result["local_evidence"]["valid"])
        self.assertIn("EVIDENCE_REPORT_REQUIRED_CHECKS_MISSING", result["blockers"])

    def test_pass_report_with_only_arbitrary_true_key_is_rejected(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            evidence_path = self._write_bundle(repo, root)
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            gate_id = next(iter(evidence["gates"]))
            report_path = root / "reports" / f"{gate_id}.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["checks"] = {"synthetic_contract_check": True}
            raw = json.dumps(report, sort_keys=True).encode("utf-8")
            report_path.write_bytes(raw)
            evidence["gates"][gate_id]["report_sha256"] = hashlib.sha256(raw).hexdigest()
            evidence_path.write_text(json.dumps(evidence, sort_keys=True), encoding="utf-8")
            result = evaluate(evidence_path=evidence_path, repo_root=repo)
        self.assertFalse(result["local_evidence"]["valid"])
        self.assertIn("EVIDENCE_REPORT_REQUIRED_CHECKS_MISSING", result["blockers"])

    def test_gate_contract_rejects_missing_required_check_declaration(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        contract = json.loads((repo / "architecture" / "v8_acceptance_gates.json").read_text(encoding="utf-8"))
        contract["gates"][0].pop("required_checks")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "gates.json"
            path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaises(AcceptanceGateError) as caught:
                load_gate_contract(path)
        self.assertEqual(caught.exception.code, "GATE_CONTRACT_GATE_INVALID")


if __name__ == "__main__":
    unittest.main()
