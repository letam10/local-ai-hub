"""V8 Wave 5 acceptance preflight tests.

The suite validates tracked contracts and synthetic bounded evidence/report
bundles only. It never treats Linux CI as Windows acceptance and never creates a
version, tag, installer, release artifact, runtime or model workload.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.v8_acceptance_gate import (
    EVIDENCE_SCHEMA_VERSION,
    REPORT_SCHEMA_VERSION,
    WEBVIEW_CAPABILITY_REPORT_SCHEMA_VERSION,
    AcceptanceGateError,
    current_head,
    evaluate,
    load_gate_contract,
    release_provenance_snapshot,
    source_change_scopes,
    source_preflight,
    _path_impact_scope,
)


class V8Wave5AcceptanceGateTests(unittest.TestCase):
    @staticmethod
    def _webview_capabilities(item: dict[str, object]) -> dict[str, object]:
        contract = item["capability_evidence"]
        assert isinstance(contract, dict)
        values = contract["native_dpi_values"]
        scales = contract["layout_scales"]
        assert isinstance(values, list) and isinstance(scales, list)
        return {
            "schema_version": contract["schema_version"],
            "native_host_dpi_current": 125,
            "native_host_dpi": {
                str(value): "PASS" if value == 125 else "NOT_AVAILABLE_ON_TEST_HOST"
                for value in values
            },
            "webview_layout": {
                str(scale): {
                    "status": "PASS",
                    "no_clipping": True,
                    "no_overlap": True,
                    "usable_controls": True,
                }
                for scale in scales
            },
        }

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
                    "schema_version": WEBVIEW_CAPABILITY_REPORT_SCHEMA_VERSION if item.get("capability_evidence") is not None else REPORT_SCHEMA_VERSION,
                    "gate_id": gate_id,
                    "status": "PASS",
                    "platform": contract["required_platform"],
                    "source_commit": source,
                    "checks": {check_id: True for check_id in required_checks},
                }
                if item.get("capability_evidence") is not None:
                    report["capabilities"] = self._webview_capabilities(item)
                raw = json.dumps(report, sort_keys=True).encode("utf-8")
                (reports / f"{gate_id}.json").write_bytes(raw)
                digest = hashlib.sha256(raw).hexdigest()
                if tamper_digest:
                    digest = ("0" if digest[0] != "0" else "1") + digest[1:]
            gates[gate_id] = {
                "status": status,
                "report_sha256": digest,
                "provenance": {
                    "schema_version": "v8-local-gate-provenance.v1",
                    "kind": "RERUN_EXACT_HEAD",
                    "origin_source_commit": source,
                    "reason_code": "rerun_exact_head",
                },
            }
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

    def test_evidence_requires_per_gate_provenance(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            evidence_path = self._write_bundle(repo, root)
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence["gates"]["windows_filesystem"].pop("provenance")
            evidence_path.write_text(json.dumps(evidence, sort_keys=True), encoding="utf-8")
            result = evaluate(evidence_path=evidence_path, repo_root=repo)
        self.assertFalse(result["local_evidence"]["valid"])
        self.assertIn("EVIDENCE_GATE_INVALID", result["blockers"])

    def test_rerun_provenance_rejects_a_report_from_another_commit(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            evidence_path = self._write_bundle(repo, root)
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            report_path = root / "reports" / "windows_filesystem.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["source_commit"] = "f" * 40
            raw = json.dumps(report, sort_keys=True).encode("utf-8")
            report_path.write_bytes(raw)
            evidence["gates"]["windows_filesystem"]["report_sha256"] = hashlib.sha256(raw).hexdigest()
            evidence_path.write_text(json.dumps(evidence, sort_keys=True), encoding="utf-8")
            result = evaluate(evidence_path=evidence_path, repo_root=repo)
        self.assertFalse(result["local_evidence"]["valid"])
        self.assertIn("EVIDENCE_REPORT_BINDING_INVALID", result["blockers"])

    def test_reused_evidence_requires_unaffected_changed_scope(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        origin = "e" * 40
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            evidence_path = self._write_bundle(repo, root)
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            report_path = root / "reports" / "windows_filesystem.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["source_commit"] = origin
            raw = json.dumps(report, sort_keys=True).encode("utf-8")
            report_path.write_bytes(raw)
            gate = evidence["gates"]["windows_filesystem"]
            gate["report_sha256"] = hashlib.sha256(raw).hexdigest()
            gate["provenance"] = {
                "schema_version": "v8-local-gate-provenance.v1",
                "kind": "REUSED_UNAFFECTED_EVIDENCE",
                "origin_source_commit": origin,
                "reason_code": "source_change_scope_unaffected",
                "unaffected_scopes": ["filesystem_transaction"],
            }
            evidence_path.write_text(json.dumps(evidence, sort_keys=True), encoding="utf-8")
            with patch("scripts.v8_acceptance_gate.source_change_scopes", return_value=set()):
                valid = evaluate(evidence_path=evidence_path, repo_root=repo)
            with patch("scripts.v8_acceptance_gate.source_change_scopes", return_value={"filesystem_transaction"}):
                blocked = evaluate(evidence_path=evidence_path, repo_root=repo)
        self.assertTrue(valid["local_evidence"]["valid"])
        self.assertTrue(valid["merge_ready"])
        self.assertFalse(blocked["local_evidence"]["valid"])
        self.assertIn("EVIDENCE_REUSED_GATE_AFFECTED_BY_SOURCE_CHANGE", blocked["blockers"])

    def test_project_manager_atomic_write_is_a_filesystem_transaction_scope(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        self.assertEqual(_path_impact_scope("src/services/project_manager/manager.py"), "filesystem_transaction")
        self.assertEqual(
            source_change_scopes(
                repo,
                origin_source_commit="afc40e6dcdfdcb2fdb4ea822f1dd8da5cb0c8ab1",
                final_source_commit="a5e59b9a7dcc68a0e15694da21dcd369b242a723",
            ),
            {"filesystem_transaction"},
        )

    def test_shared_ai_result_contracts_are_artifact_callsite_scope(self) -> None:
        for path in (
            "src/shared/schemas/vision.py",
            "src/shared/schemas/ocr_whisper.py",
            "src/shared/utils/adapter_common.py",
        ):
            with self.subTest(path=path):
                self.assertEqual(_path_impact_scope(path), "artifact_callsite")

    def test_project_manager_change_rejects_reused_windows_filesystem_evidence(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        origin = "afc40e6dcdfdcb2fdb4ea822f1dd8da5cb0c8ab1"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            evidence_path = self._write_bundle(repo, root)
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            report_path = root / "reports" / "windows_filesystem.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["source_commit"] = origin
            raw = json.dumps(report, sort_keys=True).encode("utf-8")
            report_path.write_bytes(raw)
            evidence["gates"]["windows_filesystem"] = {
                "status": "PASS",
                "report_sha256": hashlib.sha256(raw).hexdigest(),
                "provenance": {
                    "schema_version": "v8-local-gate-provenance.v1",
                    "kind": "REUSED_UNAFFECTED_EVIDENCE",
                    "origin_source_commit": origin,
                    "reason_code": "source_change_scope_unaffected",
                    "unaffected_scopes": ["filesystem_transaction"],
                },
            }
            evidence_path.write_text(json.dumps(evidence, sort_keys=True), encoding="utf-8")
            result = evaluate(evidence_path=evidence_path, repo_root=repo)
        self.assertFalse(result["local_evidence"]["valid"])
        self.assertIn("EVIDENCE_REUSED_GATE_AFFECTED_BY_SOURCE_CHANGE", result["blockers"])

    def test_project_manager_scope_does_not_invalidate_unrelated_reuse(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        origin = "afc40e6dcdfdcb2fdb4ea822f1dd8da5cb0c8ab1"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            evidence_path = self._write_bundle(repo, root)
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence["gates"]["windows_filesystem"]["status"] = "NOT_RUN"
            report_path = root / "reports" / "runtime_smoke.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["source_commit"] = origin
            raw = json.dumps(report, sort_keys=True).encode("utf-8")
            report_path.write_bytes(raw)
            evidence["gates"]["runtime_smoke"] = {
                "status": "PASS",
                "report_sha256": hashlib.sha256(raw).hexdigest(),
                "provenance": {
                    "schema_version": "v8-local-gate-provenance.v1",
                    "kind": "REUSED_UNAFFECTED_EVIDENCE",
                    "origin_source_commit": origin,
                    "reason_code": "source_change_scope_unaffected",
                    "unaffected_scopes": ["desktop_startup", "runtime"],
                },
            }
            evidence_path.write_text(json.dumps(evidence, sort_keys=True), encoding="utf-8")
            result = evaluate(evidence_path=evidence_path, repo_root=repo)
        self.assertTrue(result["local_evidence"]["reports_verified"])
        self.assertIn("LOCAL_WINDOWS_GATES_INCOMPLETE", result["blockers"])
        self.assertNotIn("EVIDENCE_REUSED_GATE_AFFECTED_BY_SOURCE_CHANGE", result["blockers"])

    def test_unknown_high_risk_source_path_is_not_silently_unclassified(self) -> None:
        self.assertEqual(_path_impact_scope("src/services/new_persistence_authority/store.py"), "unclassified_acceptance_relevant")
        self.assertEqual(_path_impact_scope("docs/operations/review-note.md"), None)

    def test_source_change_scopes_propagates_unknown_high_risk_sentinel(self) -> None:
        def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
            if "merge-base" in command:
                return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")
            return subprocess.CompletedProcess(command, 0, stdout=b"src/services/new_persistence_authority/store.py\n", stderr=b"")

        repo = Path(__file__).resolve().parents[1]
        with patch("scripts.v8_acceptance_gate.subprocess.run", side_effect=fake_run):
            scopes = source_change_scopes(repo, origin_source_commit="a" * 40, final_source_commit="b" * 40)
        self.assertEqual(scopes, {"unclassified_acceptance_relevant"})

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
        self.assertTrue({"route_render", "real_navigation", "dark", "light", "native_host_dpi_current", "native_host_dpi_capability_recorded", "webview_layout_100_no_clipping_overlap_or_unusable_controls", "webview_layout_125_no_clipping_overlap_or_unusable_controls", "webview_layout_150_no_clipping_overlap_or_unusable_controls", "degraded_error_recovery", "frontend_ready", "normal_close", "trusted_native_interaction"}.issubset(by_id["webview2_product_ux"]))
        webview = next(item for item in contract["gates"] if item["gate_id"] == "webview2_product_ux")
        self.assertEqual(webview["capability_evidence"], {
            "schema_version": "v8-webview-dpi-evidence.v1",
            "native_dpi_values": [100, 125, 150],
            "native_unavailable_status": "NOT_AVAILABLE_ON_TEST_HOST",
            "layout_scales": [100, 125, 150],
        })
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

    def test_gate_contract_rejects_missing_webview_capability_declaration(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        contract = json.loads((repo / "architecture" / "v8_acceptance_gates.json").read_text(encoding="utf-8"))
        webview = next(item for item in contract["gates"] if item["gate_id"] == "webview2_product_ux")
        webview.pop("capability_evidence")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "gates.json"
            path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaises(AcceptanceGateError) as caught:
                load_gate_contract(path)
        self.assertEqual(caught.exception.code, "GATE_CONTRACT_CAPABILITY_INVALID")

    def test_webview_native_unavailable_is_allowed_when_current_native_and_all_layout_scales_pass(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            result = evaluate(evidence_path=self._write_bundle(repo, Path(temp)), repo_root=repo)
        self.assertTrue(result["local_evidence"]["valid"])
        self.assertTrue(result["merge_ready"])

    def test_webview_current_native_and_layout_results_are_fail_closed(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            evidence_path = self._write_bundle(repo, root)
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            report_path = root / "reports" / "webview2_product_ux.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["capabilities"]["native_host_dpi"]["125"] = "NOT_AVAILABLE_ON_TEST_HOST"
            raw = json.dumps(report, sort_keys=True).encode("utf-8")
            report_path.write_bytes(raw)
            evidence["gates"]["webview2_product_ux"]["report_sha256"] = hashlib.sha256(raw).hexdigest()
            evidence_path.write_text(json.dumps(evidence, sort_keys=True), encoding="utf-8")
            result = evaluate(evidence_path=evidence_path, repo_root=repo)
        self.assertFalse(result["local_evidence"]["valid"])
        self.assertIn("EVIDENCE_WEBVIEW_NATIVE_CURRENT_MISSING", result["blockers"])

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            evidence_path = self._write_bundle(repo, root)
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            report_path = root / "reports" / "webview2_product_ux.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["capabilities"]["webview_layout"]["150"]["no_overlap"] = False
            raw = json.dumps(report, sort_keys=True).encode("utf-8")
            report_path.write_bytes(raw)
            evidence["gates"]["webview2_product_ux"]["report_sha256"] = hashlib.sha256(raw).hexdigest()
            evidence_path.write_text(json.dumps(evidence, sort_keys=True), encoding="utf-8")
            result = evaluate(evidence_path=evidence_path, repo_root=repo)
        self.assertFalse(result["local_evidence"]["valid"])
        self.assertIn("EVIDENCE_WEBVIEW_LAYOUT_FAILED", result["blockers"])

    def test_webview_pass_report_cannot_omit_capability_evidence(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            evidence_path = self._write_bundle(repo, root)
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            report_path = root / "reports" / "webview2_product_ux.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report.pop("capabilities")
            report["schema_version"] = REPORT_SCHEMA_VERSION
            raw = json.dumps(report, sort_keys=True).encode("utf-8")
            report_path.write_bytes(raw)
            evidence["gates"]["webview2_product_ux"]["report_sha256"] = hashlib.sha256(raw).hexdigest()
            evidence_path.write_text(json.dumps(evidence, sort_keys=True), encoding="utf-8")
            result = evaluate(evidence_path=evidence_path, repo_root=repo)
        self.assertFalse(result["local_evidence"]["valid"])
        self.assertIn("EVIDENCE_REPORT_INVALID", result["blockers"])


if __name__ == "__main__":
    unittest.main()
