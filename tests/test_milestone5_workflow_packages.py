from __future__ import annotations

import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "workflow_packages" / "samples"


def package_sample(name: str = "image-review.workflow-package.json") -> dict[str, object]:
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


def scenario_sample() -> dict[str, object]:
    return json.loads((SAMPLES / "image-review.evaluation-scenario.json").read_text(encoding="utf-8"))


class WorkflowPackageSchemaTests(unittest.TestCase):
    def test_published_schemas_have_required_property_parity_and_accept_samples(self) -> None:
        from src.shared.schemas.workflow_package import (
            evaluation_scenario_schema,
            validate_evaluation_scenario,
            validate_workflow_package,
            workflow_package_schema,
        )

        def assert_parity(value: object) -> None:
            if isinstance(value, dict):
                if value.get("type") == "object":
                    self.assertTrue(set(value.get("required", [])) <= set(value.get("properties", {})))
                for child in value.values():
                    assert_parity(child)
            elif isinstance(value, list):
                for child in value:
                    assert_parity(child)

        assert_parity(workflow_package_schema())
        assert_parity(evaluation_scenario_schema())
        self.assertTrue(validate_workflow_package(package_sample())["valid"])
        self.assertTrue(validate_evaluation_scenario(scenario_sample())["valid"])

    def test_rejects_paths_secrets_commands_and_untrusted_values_without_reflection(self) -> None:
        from src.shared.schemas.workflow_package import validate_workflow_package

        unsafe = package_sample()
        secret_marker = "s" + "k-this-is-not-a-real-secret-value"
        unsafe["apiKey"] = secret_marker
        unsafe["summary"] = r"C:\private\input.png"
        result = validate_workflow_package(unsafe)
        self.assertFalse(result["valid"])
        self.assertIsNone(result["package"])
        codes = {item["code"] for item in result["errors"]}
        self.assertIn("forbidden_field", codes)
        self.assertIn("raw_path", codes)
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn(secret_marker, serialized)
        self.assertNotIn(r"C:\private", serialized)

        relative = package_sample()
        relative["summary"] = "folder/descriptor.json"
        relative_result = validate_workflow_package(relative)
        self.assertFalse(relative_result["valid"])
        self.assertIn("raw_path", {item["code"] for item in relative_result["errors"]})

    def test_rejects_duplicate_ids_type_mismatch_and_graph_cycles(self) -> None:
        from src.shared.schemas.workflow_package import validate_workflow_package

        duplicate = package_sample()
        duplicate["workflow"]["nodes"].append(copy.deepcopy(duplicate["workflow"]["nodes"][0]))
        duplicate_result = validate_workflow_package(duplicate)
        self.assertFalse(duplicate_result["valid"])
        self.assertIn("duplicate_node_id", {item["code"] for item in duplicate_result["errors"]})

        mismatched = package_sample()
        mismatched["workflow"]["nodes"][2]["inputs"][0]["type"] = "IMAGE"
        mismatch_result = validate_workflow_package(mismatched)
        self.assertFalse(mismatch_result["valid"])
        self.assertIn("type_mismatch", {item["code"] for item in mismatch_result["errors"]})

        cyclic = package_sample()
        subgraph = cyclic["subgraphs"][0]
        subgraph["edges"] = [
            subgraph["edges"][1],
            {
                "id": "self-cycle",
                "from": {"node": "annotate", "port": "report"},
                "to": {"node": "annotate", "port": "facts"},
            },
        ]
        cycle_result = validate_workflow_package(cyclic)
        self.assertFalse(cycle_result["valid"])
        self.assertIn("cycle_detected", {item["code"] for item in cycle_result["errors"]})

    def test_rejects_recursive_and_excessively_nested_subgraphs(self) -> None:
        from src.shared.schemas.workflow_package import validate_workflow_package

        recursive = package_sample()
        node = recursive["subgraphs"][0]["nodes"][1]
        node.pop("operation")
        node["kind"] = "subgraph"
        node["ref"] = "metadata-annotation"
        recursive_result = validate_workflow_package(recursive)
        self.assertFalse(recursive_result["valid"])
        self.assertIn("subgraph_cycle", {item["code"] for item in recursive_result["errors"]})

        nested = package_sample()
        template = nested["subgraphs"][0]
        chain = []
        for index in range(4):
            item = copy.deepcopy(template)
            item["id"] = f"nested-{index}"
            if index < 3:
                node = item["nodes"][1]
                node.pop("operation")
                node["kind"] = "subgraph"
                node["ref"] = f"nested-{index + 1}"
            chain.append(item)
        nested["subgraphs"] = chain
        nested["workflow"]["nodes"][2]["ref"] = "nested-0"
        nested_result = validate_workflow_package(nested)
        self.assertFalse(nested_result["valid"])
        self.assertIn("subgraph_nesting", {item["code"] for item in nested_result["errors"]})


class WorkflowPackageServiceTests(unittest.TestCase):
    def test_safe_import_rejects_duplicate_json_keys_and_export_is_canonical_detached(self) -> None:
        from src.services.workflow_packages import export_workflow_package, safe_import_workflow_package
        from src.shared.schemas.workflow_package import validate_workflow_package

        duplicate = '{"schema_version":"workflow-package.v1","schema_version":"workflow-package.v1"}'
        rejected = safe_import_workflow_package(duplicate)
        self.assertFalse(rejected["accepted"])
        self.assertEqual(rejected["errors"][0]["code"], "duplicate_json_key")

        original = package_sample()
        reordered = copy.deepcopy(original)
        reordered["capabilities"].reverse()
        reordered["compatibility"]["required_node_types"].reverse()
        reordered["workflow"]["nodes"].reverse()
        reordered["workflow"]["edges"].reverse()
        imported = safe_import_workflow_package(json.dumps(original, ensure_ascii=False))
        self.assertTrue(imported["accepted"])
        imported["package"]["title"] = "Changed only in detached import"
        self.assertNotEqual(original["title"], imported["package"]["title"])
        original_export = export_workflow_package(original)
        reordered_export = export_workflow_package(reordered)
        self.assertTrue(original_export["ready"])
        self.assertTrue(reordered_export["ready"])
        self.assertEqual(original_export["content"], reordered_export["content"])
        self.assertEqual(validate_workflow_package(original)["fingerprint"], validate_workflow_package(reordered)["fingerprint"])

    def test_static_catalog_lint_and_preflight_keep_runtime_truthful(self) -> None:
        from src.services.workflow_packages import (
            discover_managed_packages,
            lint_workflow_package,
            load_managed_package,
            preflight_workflow_package,
        )

        catalog = discover_managed_packages()
        self.assertEqual(catalog["status"], "partial")
        self.assertGreaterEqual(len(catalog["records"]), 2)
        self.assertNotIn(str(ROOT), json.dumps(catalog, ensure_ascii=False))
        loaded = load_managed_package("local-ai-hub.image-review", "1.1.0")
        self.assertTrue(loaded["found"])
        self.assertEqual(loaded["status"], "partial")
        self.assertEqual(lint_workflow_package(loaded)["status"], "clean")
        matched = preflight_workflow_package(loaded, ["hub.image.inspect", "hub.metadata.annotate"])
        self.assertEqual(matched["status"], "partial")
        missing = preflight_workflow_package(loaded, ["hub.image.inspect"])
        self.assertEqual(missing["status"], "unavailable")
        self.assertEqual(missing["missing_node_types"], ["hub.metadata.annotate"])

    def test_linter_reports_unreferenced_subgraph_without_running_a_graph(self) -> None:
        from src.services.workflow_packages import lint_workflow_package

        package = package_sample()
        unused = copy.deepcopy(package["subgraphs"][0])
        unused["id"] = "unused-annotation"
        package["subgraphs"].append(unused)
        result = lint_workflow_package(package)
        self.assertTrue(result["valid"])
        self.assertEqual(result["execution"], "not_run")
        self.assertIn("unreferenced_subgraph", {item["code"] for item in result["findings"]})
        source = "\n".join((ROOT / "src" / "services" / "workflow_packages" / name).read_text(encoding="utf-8") for name in ("io.py", "linting.py", "diffing.py", "catalog.py"))
        self.assertNotIn("subprocess", source)
        self.assertNotIn("node_studio.engine", source)

    def test_diff_and_migration_are_deterministic_dry_run_and_non_mutating(self) -> None:
        from src.services.workflow_packages import diff_workflow_packages, plan_workflow_migration

        before = package_sample()
        after = package_sample("image-review-v1_1.workflow-package.json")
        before_snapshot = json.dumps(before, ensure_ascii=False, sort_keys=True)
        after_snapshot = json.dumps(after, ensure_ascii=False, sort_keys=True)
        first_diff = diff_workflow_packages(before, after)
        second_diff = diff_workflow_packages(before, after)
        self.assertEqual(first_diff, second_diff)
        self.assertEqual(first_diff["status"], "changed")
        first_plan = plan_workflow_migration(before, after)
        second_plan = plan_workflow_migration(before, after)
        self.assertEqual(first_plan, second_plan)
        self.assertTrue(first_plan["dry_run"])
        self.assertEqual(first_plan["execution"], "not_run")
        self.assertEqual(before_snapshot, json.dumps(before, ensure_ascii=False, sort_keys=True))
        self.assertEqual(after_snapshot, json.dumps(after, ensure_ascii=False, sort_keys=True))

        renamed = copy.deepcopy(after)
        renamed["id"] = "local-ai-hub.different-package"
        blocked = plan_workflow_migration(before, renamed)
        self.assertEqual(blocked["status"], "unavailable")
        self.assertTrue(blocked["dry_run"])

    def test_static_cli_reads_only_managed_catalog_and_writes_not_run_reports(self) -> None:
        command = [sys.executable, "-B", "scripts/validate_workflow_packages.py"]
        json_result = subprocess.run([*command, "--format", "json"], cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(json_result.returncode, 0, json_result.stderr)
        payload = json.loads(json_result.stdout)
        self.assertEqual(payload["execution"], "not_run")
        self.assertEqual(payload["status"], "partial")
        markdown_result = subprocess.run([*command, "--format", "markdown", "--package-id", "local-ai-hub.image-review"], cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(markdown_result.returncode, 0, markdown_result.stderr)
        self.assertIn("Execution: `not_run`", markdown_result.stdout)


class EvaluationAndAuditTests(unittest.TestCase):
    def test_human_ab_scenario_stays_static_and_rejects_runner_fields(self) -> None:
        from src.services.evaluation_lab import build_human_ab_plan, safe_import_evaluation_scenario

        scenario = scenario_sample()
        package = package_sample("image-review-v1_1.workflow-package.json")
        imported = safe_import_evaluation_scenario(json.dumps(scenario, ensure_ascii=False))
        self.assertTrue(imported["accepted"])
        plan = build_human_ab_plan(imported, package)
        self.assertTrue(plan["valid"])
        self.assertEqual(plan["status"], "planned")
        self.assertTrue(plan["human_review_required"])
        self.assertEqual(plan["execution"], "not_run")

        unsafe = scenario_sample()
        unsafe["runner"] = "cmd.exe"
        rejected = safe_import_evaluation_scenario(json.dumps(unsafe, ensure_ascii=False))
        self.assertFalse(rejected["accepted"])
        self.assertIsNone(rejected["scenario"])
        duplicate = safe_import_evaluation_scenario('{"schema_version":"evaluation-scenario.v1","schema_version":"evaluation-scenario.v1"}')
        self.assertFalse(duplicate["accepted"])
        self.assertEqual(duplicate["errors"][0]["code"], "duplicate_json_key")

    def test_scrubbed_audit_json_and_markdown_exclude_free_text_paths_and_secrets(self) -> None:
        from src.services.evaluation_lab import build_package_audit, build_package_audit_markdown

        package = package_sample("image-review-v1_1.workflow-package.json")
        package["title"] = "<script>untrusted title</script> | [link]"
        audit = build_package_audit(package, scenario_value=scenario_sample(), previous_package_value=package_sample())
        self.assertTrue(audit["valid"])
        serialized = json.dumps(audit, ensure_ascii=False)
        self.assertNotIn("untrusted title", serialized)
        self.assertNotIn("<script>", serialized)
        markdown = build_package_audit_markdown(package, scenario_value=scenario_sample())
        self.assertNotIn("untrusted title", markdown)
        self.assertNotIn("<script>", markdown)
        self.assertIn("Execution: `not_run`", markdown)

        unsafe = package_sample()
        unsafe["summary"] = r"C:\private\secret.png"
        blocked = build_package_audit(unsafe)
        self.assertFalse(blocked["valid"])
        self.assertNotIn(r"C:\private", json.dumps(blocked, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
