"""Focused contracts for Milestone 4B extension platform services."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unittest
from typing import Any, Mapping

from src.shared.schemas.extension_manifest import (
    MANIFEST_SCHEMA_VERSION,
    ManifestValidationError,
    extension_manifest_schema,
    manifest_errors,
    validate_extension_manifest,
    version_satisfies,
)
from src.services.capability_planner import CardValidationError, plan_resources, validate_model_card, validate_runtime_card
from src.services.extension_platform import (
    build_compatibility_report,
    compatibility_report_json,
    discover_extensions,
    generate_extension_scaffold,
    load_extension_config,
    preflight_extension,
    preflight_extensions,
    render_compatibility_markdown,
)


ROOT = Path(__file__).resolve().parents[1]


def valid_manifest() -> dict:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "id": "metadata-catalog",
        "version": "1.0.0",
        "display_name": "Metadata Catalog",
        "description": "A static catalog capability pack.",
        "author": {"name": "Local AI Hub", "url": "https://example.invalid/local-ai-hub"},
        "license": "MIT",
        "source": "https://example.invalid/local-ai-hub/extensions/metadata-catalog",
        "capabilities": ["capability_pack", "metadata_catalog", "resource_planning"],
        "compatibility": {"hub": {"min_version": "4.0.0", "max_version": "4.99.99"}, "platforms": ["windows"]},
        "required_components": [],
        "required_models": [],
        "permissions": ["read_extension_metadata", "plan_resources", "render_compatibility_report"],
        "entrypoints": [
            {"kind": "capability_pack", "path": "capability-pack.json"},
            {"kind": "documentation", "path": "README.md"},
        ],
        "resource_profile": {
            "cpu": {"class": "light", "threads": 1},
            "gpu": {"required": False, "vendor": "none", "device_class": "none"},
            "vram_gb": 0,
            "ram_gb": 0.25,
            "disk_gb": 0.01,
            "exclusive_resource_groups": [],
        },
        "availability": {
            "status": "operational",
            "reason": "This capability pack is static metadata only.",
            "action": "Use the compatibility report or resource planner.",
        },
    }


def _draft_schema_case_matches(schema: Mapping[str, Any], value: Any, root: Mapping[str, Any] | None = None) -> bool:
    """Evaluate the published schema keywords exercised by v1 parity cases.

    The production code deliberately has no JSON Schema dependency. This small,
    test-only evaluator verifies the published Draft 2020-12 document itself for
    accepted and rejected representative manifests without adding one.
    """

    root = root or schema
    if "$ref" in schema:
        reference = schema["$ref"]
        if not isinstance(reference, str) or not reference.startswith("#/"):
            return False
        target: Any = root
        for part in reference[2:].split("/"):
            if not isinstance(target, Mapping) or part not in target:
                return False
            target = target[part]
        return _draft_schema_case_matches(target, value, root)
    if "const" in schema and value != schema["const"]:
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    if "not" in schema and _draft_schema_case_matches(schema["not"], value, root):
        return False
    if "allOf" in schema and not all(_draft_schema_case_matches(part, value, root) for part in schema["allOf"]):
        return False
    if "oneOf" in schema and sum(_draft_schema_case_matches(part, value, root) for part in schema["oneOf"]) != 1:
        return False
    if "if" in schema and _draft_schema_case_matches(schema["if"], value, root):
        if "then" in schema and not _draft_schema_case_matches(schema["then"], value, root):
            return False

    expected_type = schema.get("type")
    if expected_type == "object" and not isinstance(value, Mapping):
        return False
    if expected_type == "array" and not isinstance(value, list):
        return False
    if expected_type == "string" and not isinstance(value, str):
        return False
    if expected_type == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        return False
    if expected_type == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
        return False
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            return False
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            return False
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            return False
        if "maximum" in schema and value > schema["maximum"]:
            return False
    if isinstance(value, Mapping):
        required = schema.get("required", [])
        if any(key not in value for key in required):
            return False
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False and any(key not in properties for key in value):
            return False
        if any(key in value and not _draft_schema_case_matches(property_schema, value[key], root) for key, property_schema in properties.items()):
            return False
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            return False
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            return False
        if schema.get("uniqueItems") and len({json.dumps(item, sort_keys=True) for item in value}) != len(value):
            return False
        if "items" in schema and any(not _draft_schema_case_matches(schema["items"], item, root) for item in value):
            return False
    return True


class ExtensionManifestSchemaTests(unittest.TestCase):
    def test_valid_manifest_is_detached_and_has_declarative_contract(self) -> None:
        source = valid_manifest()
        result = validate_extension_manifest(source)
        self.assertEqual(result["id"], "metadata-catalog")
        self.assertIsNot(result, source)
        source["availability"]["status"] = "planned"
        self.assertEqual(result["availability"]["status"], "operational")

    def test_shell_permissions_and_unsafe_descriptor_paths_are_rejected(self) -> None:
        value = valid_manifest()
        value["permissions"].append("execute_shell")
        value["entrypoints"][0]["path"] = "../payload.py"
        errors = manifest_errors(value)
        self.assertIn("unsupported_permission", {item["code"] for item in errors})
        self.assertIn("unsafe_descriptor_path", {item["code"] for item in errors})
        with self.assertRaises(ManifestValidationError):
            validate_extension_manifest(value)

    def test_gpu_and_status_claims_require_complete_honest_metadata(self) -> None:
        value = deepcopy(valid_manifest())
        value["resource_profile"]["gpu"] = {"required": True, "vendor": "none", "device_class": "discrete"}
        value["availability"] = {"status": "operational", "reason": "", "action": ""}
        errors = manifest_errors(value)
        self.assertIn("inconsistent_gpu_requirement", {item["code"] for item in errors})
        self.assertIn("invalid_value", {item["code"] for item in errors})

    def test_version_constraints_are_dependency_free_and_bounded(self) -> None:
        self.assertTrue(version_satisfies("4.2.1", ">=4.0.0, <5.0.0"))
        self.assertFalse(version_satisfies("5.0.0", ">=4.0.0, <5.0.0"))
        self.assertFalse(version_satisfies("4.2.1", "latest"))

    def test_published_json_schema_has_parity_for_representative_manifest_cases(self) -> None:
        schema = extension_manifest_schema()
        self.assertTrue(set(schema["required"]).issubset(schema["properties"]))
        accepted = valid_manifest()
        self.assertTrue(_draft_schema_case_matches(schema, accepted))
        self.assertEqual(validate_extension_manifest(accepted)["id"], accepted["id"])

        rejected_cases = []
        unknown_field = valid_manifest()
        unknown_field["command"] = "not-allowlisted"
        rejected_cases.append(unknown_field)
        unsafe_permission = valid_manifest()
        unsafe_permission["permissions"].append("execute_shell")
        rejected_cases.append(unsafe_permission)
        unsafe_entrypoint = valid_manifest()
        unsafe_entrypoint["entrypoints"][0]["path"] = "payload.py"
        rejected_cases.append(unsafe_entrypoint)
        inconsistent_gpu = valid_manifest()
        inconsistent_gpu["resource_profile"]["vram_gb"] = 1
        rejected_cases.append(inconsistent_gpu)
        for candidate in rejected_cases:
            self.assertFalse(_draft_schema_case_matches(schema, candidate))
            with self.assertRaises(ManifestValidationError):
                validate_extension_manifest(candidate)


def valid_model_card() -> dict:
    return {
        "schema_version": "model-card.v1",
        "id": "metadata-embedder",
        "display_name": "Metadata Embedder",
        "version": "1.0.0",
        "lineage": ["Local AI Hub sample metadata descriptor"],
        "license": "MIT",
        "source": "https://example.invalid/local-ai-hub/models/metadata-embedder",
        "intended_use": ["Illustrate static capability-pack model metadata."],
        "limitations": ["This is documentation only and is not an installed model."],
        "compatibility": {"platforms": ["windows"], "required_components": [], "required_models": []},
    }


class CapabilityPlannerTests(unittest.TestCase):
    @staticmethod
    def _gpu_manifest(identifier: str, vram_gb: float) -> dict:
        value = valid_manifest()
        value["id"] = identifier
        value["resource_profile"] = {
            "cpu": {"class": "moderate", "threads": 2},
            "gpu": {"required": True, "vendor": "nvidia", "device_class": "discrete"},
            "vram_gb": vram_gb,
            "ram_gb": 2,
            "disk_gb": 1,
            "exclusive_resource_groups": [],
        }
        return value

    def test_model_and_runtime_cards_reject_machine_paths_and_keep_lineage(self) -> None:
        card = validate_model_card(valid_model_card())
        self.assertEqual(card["lineage"], ["Local AI Hub sample metadata descriptor"])
        unsafe = valid_model_card()
        unsafe["local_path"] = r"D:\\private\\model.bin"
        with self.assertRaises(CardValidationError):
            validate_model_card(unsafe)

        runtime = {
            **valid_model_card(),
            "schema_version": "runtime-card.v1",
            "id": "metadata-runtime",
            "runtime_kind": "adapter",
            "capabilities": ["metadata_catalog"],
        }
        self.assertEqual(validate_runtime_card(runtime)["runtime_kind"], "adapter")

    def test_resource_plan_is_dry_run_and_detects_mutual_exclusion(self) -> None:
        first = valid_manifest()
        first["id"] = "gpu-catalog-a"
        first["resource_profile"] = {
            "cpu": {"class": "moderate", "threads": 2},
            "gpu": {"required": True, "vendor": "nvidia", "device_class": "discrete"},
            "vram_gb": 4,
            "ram_gb": 2,
            "disk_gb": 1,
            "exclusive_resource_groups": ["gpu:primary"],
        }
        second = deepcopy(first)
        second["id"] = "gpu-catalog-b"
        result = plan_resources(
            [first, second],
            hardware={
                "cpu_threads": 8,
                "ram_gb": 16,
                "disk_gb": 100,
                "gpus": [{"id": "rtx4060", "vendor": "nvidia", "device_class": "discrete", "vram_gb": 8}],
            },
        )
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["conflicts"][0]["group"], "gpu:primary")
        self.assertNotIn("path", str(result).lower())

    def test_unknown_hardware_never_claims_gpu_readiness(self) -> None:
        value = valid_manifest()
        value["id"] = "gpu-catalog-unknown"
        value["resource_profile"] = {
            "cpu": {"class": "moderate", "threads": 2},
            "gpu": {"required": True, "vendor": "nvidia", "device_class": "discrete"},
            "vram_gb": 4,
            "ram_gb": 2,
            "disk_gb": 1,
            "exclusive_resource_groups": [],
        }
        result = plan_resources([value])
        self.assertEqual(result["status"], "partial")
        self.assertIn("hardware inventory", result["requests"][0]["additional_actions"][0])

    def test_serial_gpu_plan_reuses_a_physically_fitting_device(self) -> None:
        result = plan_resources(
            [self._gpu_manifest("gpu-serial-a", 6), self._gpu_manifest("gpu-serial-b", 6)],
            hardware={"gpus": [{"id": "rtx4060", "vendor": "nvidia", "device_class": "discrete", "vram_gb": 8}]},
            mode="serial",
        )
        self.assertEqual(result["status"], "operational")
        self.assertEqual([item["gpu_assignment"] for item in result["requests"]], ["rtx4060", "rtx4060"])
        self.assertTrue(all(not item["additional_actions"] for item in result["requests"]))

    def test_parallel_gpu_overcommit_is_partial_but_individually_fitting(self) -> None:
        result = plan_resources(
            [self._gpu_manifest("gpu-parallel-a", 6), self._gpu_manifest("gpu-parallel-b", 6)],
            hardware={"gpus": [{"id": "rtx4060", "vendor": "nvidia", "device_class": "discrete", "vram_gb": 8}]},
            mode="parallel",
        )
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["requests"][0]["gpu_assignment"], "rtx4060")
        self.assertIsNone(result["requests"][1]["gpu_assignment"])
        self.assertIn("Run requests serially", result["requests"][1]["additional_actions"][0])

    def test_single_gpu_request_larger_than_known_device_is_unavailable(self) -> None:
        result = plan_resources(
            [self._gpu_manifest("gpu-oversize", 10)],
            hardware={"gpus": [{"id": "rtx4060", "vendor": "nvidia", "device_class": "discrete", "vram_gb": 8}]},
            mode="serial",
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["requests"][0]["gpu_assignment"])
        self.assertIn("at least 10 GB VRAM", result["requests"][0]["additional_actions"][0])


class ExtensionPlatformTests(unittest.TestCase):
    @staticmethod
    def _record(manifest: dict) -> dict:
        return {
            "manifest": manifest,
            "extension_id": manifest["id"],
            "display_name": manifest["display_name"],
            "status": "operational",
            "reason": manifest["availability"]["reason"],
            "action": manifest["availability"]["action"],
            "capabilities": manifest["capabilities"],
            "required_components": manifest["required_components"],
            "required_models": manifest["required_models"],
            "resource_profile": manifest["resource_profile"],
            "descriptors": {"model_cards": [], "runtime_cards": []},
        }

    @staticmethod
    def _gpu_heavy_manifest() -> dict:
        value = valid_manifest()
        value["id"] = "gpu-heavy-catalog"
        value["resource_profile"] = {
            "cpu": {"class": "moderate", "threads": 2},
            "gpu": {"required": True, "vendor": "nvidia", "device_class": "discrete"},
            "vram_gb": 10,
            "ram_gb": 2,
            "disk_gb": 1,
            "exclusive_resource_groups": [],
        }
        return value

    def test_static_discovery_reads_only_managed_descriptors_and_keeps_status_honest(self) -> None:
        discovery = discover_extensions(ROOT)
        records = {item["extension_id"]: item for item in discovery["extensions"] if item["extension_id"]}
        self.assertEqual(discovery["managed_root"], "extensions")
        self.assertEqual(records["metadata-catalog"]["status"], "operational")
        self.assertEqual(records["image-resource-advisor"]["status"], "partial")
        self.assertEqual(records["metadata-catalog"]["entrypoints"][0], {"kind": "capability_pack"})
        self.assertNotIn("D:\\", json.dumps(discovery))
        self.assertNotIn("execute_shell", json.dumps(discovery))

    def test_preflight_and_reports_are_dry_run_and_export_both_formats(self) -> None:
        discovery = discover_extensions(ROOT)
        configuration = load_extension_config(ROOT)
        preflight = preflight_extensions(discovery, configuration=configuration)
        results = {item["extension_id"]: item for item in preflight["extensions"]}
        self.assertTrue(preflight["dry_run"])
        self.assertEqual(results["metadata-catalog"]["status"], "operational")
        self.assertEqual(results["image-resource-advisor"]["status"], "partial")
        self.assertTrue(preflight["resource_plan"]["dry_run"])
        report = build_compatibility_report(discovery, preflight=preflight)
        self.assertEqual(json.loads(compatibility_report_json(report))["report_version"], "extension-compatibility-report.v1")
        markdown = render_compatibility_markdown(report)
        self.assertIn("Image Resource Advisor", markdown)
        self.assertNotIn("D:\\", markdown)

    def test_generator_is_scoped_to_extensions_and_produces_planned_static_descriptor(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = generate_extension_scaffold("generated-catalog", root=root, display_name="Generated Catalog")
            self.assertEqual(result["status"], "created")
            self.assertEqual(set(result["files"]), {"README.md", "capability-pack.json", "extension.json"})
            (root / "extensions" / "generated-catalog" / "untrusted.py").write_text("raise RuntimeError('must not run')", encoding="utf-8")
            discovery = discover_extensions(root)
        self.assertEqual(discovery["extensions"][0]["status"], "planned")
        self.assertEqual(discovery["extensions"][0]["extension_id"], "generated-catalog")

    def test_generator_keeps_companion_descriptor_id_within_manifest_bounds(self) -> None:
        extension_id = "catalog-" + "a" * 56
        self.assertEqual(len(extension_id), 64)
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            generate_extension_scaffold(extension_id, root=root)
            discovery = discover_extensions(root)
        pack = discovery["extensions"][0]["descriptors"]["capability_packs"][0]
        self.assertLessEqual(len(pack["id"]), 64)

    def test_enabled_subset_excludes_disabled_gpu_extension_from_resource_plan_and_status(self) -> None:
        static = valid_manifest()
        static["id"] = "static-catalog"
        heavy = self._gpu_heavy_manifest()
        preflight = preflight_extensions(
            {"extensions": [self._record(static), self._record(heavy)]},
            configuration={
                "hub_version": "4.0.0",
                "platform": "windows",
                "enabled_extensions": ["static-catalog"],
                "components": [],
                "models": [],
                "hardware": {"gpus": [{"id": "rtx4060", "vendor": "nvidia", "device_class": "discrete", "vram_gb": 8}]},
            },
        )
        results = {item["extension_id"]: item for item in preflight["extensions"]}
        self.assertEqual(preflight["planning_scope"], "enabled_extensions")
        self.assertEqual(preflight["status"], "operational")
        self.assertEqual(results["gpu-heavy-catalog"]["status"], "planned")
        self.assertFalse(results["gpu-heavy-catalog"]["planning_eligible"])
        self.assertEqual([item["extension_id"] for item in preflight["resource_plan"]["requests"]], ["static-catalog"])
        report = build_compatibility_report({"extensions": [self._record(static), self._record(heavy)]}, preflight=preflight)
        self.assertEqual(report["status"], "operational")
        self.assertFalse(next(item for item in report["extensions"] if item["extension_id"] == "gpu-heavy-catalog")["planning_eligible"])

    def test_explicit_empty_enabled_list_plans_no_extensions(self) -> None:
        static = valid_manifest()
        static["id"] = "empty-list-static"
        heavy = self._gpu_heavy_manifest()
        preflight = preflight_extensions(
            {"extensions": [self._record(static), self._record(heavy)]},
            configuration={"hub_version": "4.0.0", "platform": "windows", "enabled_extensions": [], "components": [], "models": []},
        )
        self.assertEqual(preflight["planning_scope"], "enabled_extensions")
        self.assertEqual(preflight["status"], "planned")
        self.assertEqual(preflight["resource_plan"]["requests"], [])
        self.assertTrue(all(item["status"] == "planned" and not item["planning_eligible"] for item in preflight["extensions"]))

    def test_workflow_templates_remain_partial_without_a_static_descriptor_contract(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            generate_extension_scaffold("workflow-catalog", root=root)
            manifest_path = root / "extensions" / "workflow-catalog" / "extension.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["capabilities"] = ["workflow_templates"]
            manifest["entrypoints"] = [{"kind": "documentation", "path": "README.md"}]
            manifest["availability"] = {
                "status": "operational",
                "reason": "Workflow metadata is declared.",
                "action": "Validate the descriptor.",
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            record = discover_extensions(root)["extensions"][0]
        self.assertEqual(record["status"], "partial")
        self.assertIn("does not execute or import", record["reason"])
        self.assertEqual(preflight_extension(manifest, hub_version="4.0.0", platform="windows")["status"], "partial")


if __name__ == "__main__":
    unittest.main()
