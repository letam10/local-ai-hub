"""V8 Wave 5 source preflight tests.

The suite validates only the tracked acceptance contract and synthetic bounded
evidence. It never treats Linux CI as Windows acceptance and never creates a
version, tag, installer, release artifact, runtime or model workload.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts.v8_acceptance_gate import (
    EVIDENCE_SCHEMA_VERSION,
    AcceptanceGateError,
    current_head,
    evaluate,
    load_gate_contract,
    release_provenance_snapshot,
    source_preflight,
)


class V8Wave5AcceptanceGateTests(unittest.TestCase):
    def _evidence(self, repo: Path, *, source_commit: str | None = None, status: str = "PASS") -> dict[str, object]:
        contract = load_gate_contract(repo / "architecture" / "v8_acceptance_gates.json")
        digest = "a" * 64
        return {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "evidence_class": contract["required_evidence_class"],
            "platform": contract["required_platform"],
            "source_commit": source_commit or current_head(repo),
            "gates": {
                item["gate_id"]: {"status": status, "report_sha256": digest if status == "PASS" else None}
                for item in contract["gates"]
                if item["required"] is True
            },
        }

    def test_source_preflight_is_valid_but_release_provenance_remains_v7(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        source = source_preflight(repo)
        provenance = release_provenance_snapshot()
        self.assertTrue(source["valid"])
        self.assertEqual(source["missing_source_files"], [])
        self.assertFalse(provenance["generation_ready"])
        self.assertEqual(provenance["product_version"], "7.1.0")
        self.assertEqual(provenance["release_branch"], "feature/v7-operational-closure")
        self.assertTrue(str(provenance["intended_tag"]).startswith("v7."))

        result = evaluate(repo_root=repo)
        self.assertFalse(result["release_ready"])
        self.assertIn("V8_RELEASE_PROVENANCE_NOT_REVIEWED", result["blockers"])
        self.assertIn("LOCAL_WINDOWS_EVIDENCE_REQUIRED", result["blockers"])

    def test_synthetic_all_pass_evidence_cannot_override_unreviewed_v8_provenance(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            evidence_path = Path(temp) / "evidence.json"
            evidence_path.write_text(json.dumps(self._evidence(repo), sort_keys=True), encoding="utf-8")
            result = evaluate(evidence_path=evidence_path, repo_root=repo)
        self.assertTrue(result["local_evidence"]["valid"])
        self.assertTrue(result["local_evidence"]["source_commit_matches"])
        self.assertEqual(result["local_evidence"]["pending_gates"], [])
        self.assertFalse(result["release_ready"])
        self.assertEqual(result["blockers"], ["V8_RELEASE_PROVENANCE_NOT_REVIEWED"])

    def test_evidence_is_bound_to_exact_source_commit(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        wrong = "0" * 40
        if current_head(repo) == wrong:
            wrong = "1" * 40
        with tempfile.TemporaryDirectory() as temp:
            evidence_path = Path(temp) / "evidence.json"
            evidence_path.write_text(json.dumps(self._evidence(repo, source_commit=wrong), sort_keys=True), encoding="utf-8")
            result = evaluate(evidence_path=evidence_path, repo_root=repo)
        self.assertTrue(result["local_evidence"]["valid"])
        self.assertFalse(result["local_evidence"]["source_commit_matches"])
        self.assertIn("EVIDENCE_SOURCE_COMMIT_MISMATCH", result["blockers"])
        self.assertFalse(result["release_ready"])

    def test_evidence_schema_rejects_unbounded_or_path_like_extra_fields(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        evidence = self._evidence(repo)
        evidence["raw_path"] = r"D:\\LocalAIHub\\Reports\\acceptance.json"
        with tempfile.TemporaryDirectory() as temp:
            evidence_path = Path(temp) / "evidence.json"
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


if __name__ == "__main__":
    unittest.main()
