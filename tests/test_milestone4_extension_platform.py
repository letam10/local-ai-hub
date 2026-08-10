"""Focused contracts for Milestone 4B extension platform services."""

from __future__ import annotations

from copy import deepcopy
import unittest

from src.shared.schemas.extension_manifest import (
    MANIFEST_SCHEMA_VERSION,
    ManifestValidationError,
    manifest_errors,
    validate_extension_manifest,
    version_satisfies,
)
from src.services.capability_planner import CardValidationError, plan_resources, validate_model_card, validate_runtime_card


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


if __name__ == "__main__":
    unittest.main()
